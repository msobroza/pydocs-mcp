"""The chat page's opt-in trace (``ask_your_docs.trace``): where one serve child records, and
what one answered question keeps.

The traced child's recorder is per PROCESS — one ``server_events.jsonl`` per child, every
question of the session in it — while the eval readers open one trajectory directory
holding the raw capture and its sidecars (``model_turns.json``, ``model_usage.json``). So
each answered question is materialised as its own trajectory directory under the child's:
``<trace_dir>/questions/<n>/`` holds the header plus exactly the lines recorded while that
question ran, the two sidecars joined from its messages by the eval binding's own join, and
a small ``question.json``. The result blobs its calls name are copied to
``<trace_dir>/questions/blobs/``, where readers look (beside the trajectory directory).
Local-only, and nothing prunes it: whoever turned the knob on clears the folder.

Core deps only (no langchain, no streamlit); the messages are duck-typed like
``model_turns`` / ``model_usage``.

Example:
    trace = TraceLocation.minted_under(Path("/traces"))  # one per child, never reused
    sink = trace.question_sink("typed question", "standalone rewrite")  # the question starts
    answer = await ask(agent, history, "standalone rewrite", trace_sink=sink)
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.ask_your_docs.chat_trace_protocols import ChatTraceSink
from pydocs_mcp.harness.ask_your_docs.first_turn import is_finalized_reply
from pydocs_mcp.harness.ask_your_docs.model_turns import stamp_model_turns
from pydocs_mcp.harness.ask_your_docs.model_usage import stamp_model_usage
from pydocs_mcp.observability.trace_env import trace_subprocess_env
from pydocs_mcp.observability.trace_reader import read_result_blob_digests, read_tool_call_records
from pydocs_mcp.observability.trace_writer import RESULT_BLOBS_DIRNAME, SERVER_EVENTS_FILENAME

QUESTIONS_DIRNAME = "questions"
QUESTION_RECORD_FILENAME = "question.json"
QUESTION_RECORD_SCHEMA_VERSION = 1

# A blob is named by the sha256 hex of its bytes (trace_writer.write_result_blob), so no
# other name can be in the store; and only such a name is safe to join onto a path.
_BLOB_DIGEST = re.compile(r"[0-9a-f]{64}")


class ChatTraceMissingError(PydocsMCPError, RuntimeError):
    """A traced child recorded nothing: the trace overlay never reached it (ADR 0009)."""

    def __init__(self, *, events_path: Path) -> None:
        self.events_path = events_path
        super().__init__(
            f"no server trace at {events_path} for a traced chat-page serve child — the "
            "PYDOCS_TRACE__* overlay never reached it, so this question cannot be kept; "
            "check how the page launched its serve child (page_trace.traced_serve_opener)"
        )


@dataclass(frozen=True, slots=True)
class TraceLocation:
    """ONE traced child's trajectory, ``<trace_root>/<trajectory_id>/``."""

    trace_root: Path
    trajectory_id: str

    @classmethod
    def minted_under(cls, trace_root: Path) -> TraceLocation:
        """A fresh id under ``trace_root`` — one per child: the recorder refuses a reused id."""
        return cls(trace_root, uuid.uuid4().hex)

    @property
    def trace_dir(self) -> Path:
        return self.trace_root / self.trajectory_id

    @property
    def events_path(self) -> Path:
        return self.trace_dir / SERVER_EVENTS_FILENAME

    @property
    def questions_dir(self) -> Path:
        return self.trace_dir / QUESTIONS_DIRNAME

    @property
    def blobs_dir(self) -> Path:
        """The recorder's store: shared by every child under the same root."""
        return self.trace_root / RESULT_BLOBS_DIRNAME

    def child_env(self) -> dict[str, str]:
        """The three ``PYDOCS_TRACE__*`` names this child must be launched with."""
        return trace_subprocess_env(self.trace_root, self.trajectory_id)

    def question_sink(self, question: str, standalone: str) -> ChatTraceSink:
        """Start keeping one question: typed as ``question``, asked as ``standalone``."""
        return ChatTraceWriter(self, question, standalone)


class ChatTraceWriter:
    """Persists ONE answered question as ``<trace_dir>/questions/<n>/``.

    Built when the question starts: it notes how much the child has recorded, so the
    question keeps only what is recorded after — a failed turn's calls never leak into the
    next question. Raises :class:`ChatTraceMissingError` at once when the child recorded
    nothing, before the model spends a token.
    """

    def __init__(self, trace: TraceLocation, question: str, standalone: str) -> None:
        self._trace = trace
        self._question = question
        self._standalone = standalone
        self._recorded_before = _recorded_bytes(trace.events_path)

    async def stamp_turn(self, messages: Sequence[Any]) -> None:
        """Write the question's directory from its messages (question, seeded pair, turn)."""
        # Off the event loop (CLAUDE.md §Async Patterns): every tab's turns share it.
        await asyncio.to_thread(self._keep_question, list(messages))

    def _keep_question(self, messages: list[Any]) -> None:
        question_dir = _new_question_dir(self._trace.questions_dir)
        question_events_path = question_dir / SERVER_EVENTS_FILENAME
        _copy_lines_after(self._trace.events_path, self._recorded_before, question_events_path)
        run_blobs = self._trace.questions_dir / RESULT_BLOBS_DIRNAME
        _copy_blobs(read_result_blob_digests(question_dir), self._trace.blobs_dir, run_blobs)
        server_tool_names = tuple(r.tool_name for r in read_tool_call_records(question_dir))
        stamp_model_turns(question_dir, messages, server_tool_names)
        stamp_model_usage(question_dir, messages)
        _write_question_record(question_dir, self._question, self._standalone, messages)


def _recorded_bytes(events_path: Path) -> int:
    """How much the child has recorded so far — the header, then every earlier question."""
    try:
        return events_path.stat().st_size
    except FileNotFoundError as exc:
        raise ChatTraceMissingError(events_path=events_path) from exc


def _copy_lines_after(source: Path, offset: int, target: Path) -> None:
    """The header, then every complete line recorded after ``offset`` — byte for byte."""
    with source.open("rb") as recorded:
        header = recorded.readline()
        recorded.seek(max(offset, len(header)))
        tail = recorded.read()
    # A line still being written (a late call from a stopped turn) belongs to no question.
    target.write_bytes(header + tail[: tail.rfind(b"\n") + 1])


def _new_question_dir(questions_dir: Path) -> Path:
    """``questions/<n>/`` after the highest number kept, so a deleted folder is never reused."""
    entries: Iterable[Path] = questions_dir.iterdir() if questions_dir.is_dir() else ()
    kept = [int(p.name) for p in entries if p.name.isdigit()]
    question_dir = questions_dir / str(max(kept, default=0) + 1)
    question_dir.mkdir(parents=True)
    return question_dir


def _copy_blobs(digests: Iterable[str], store: Path, run_blobs: Path) -> None:
    """Copy the named blobs to ``run_blobs``; one the store no longer holds reads as absent,
    as the eval readers take a pruned blob."""
    for digest in digests:
        target = run_blobs / digest
        if not _BLOB_DIGEST.fullmatch(digest) or target.exists():
            continue
        if (store / digest).is_file():
            run_blobs.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(store / digest, target)


def _write_question_record(
    question_dir: Path, question: str, standalone: str, messages: Sequence[Any]
) -> None:
    payload = {
        "schema_version": QUESTION_RECORD_SCHEMA_VERSION,
        "question": question,
        "standalone_question": standalone,
        # The binding's reading of the answer: the last message's content, as text.
        "answer": str(messages[-1].content) if messages else "",
        # True when the turn ran out of steps and the finalize call answered (#375).
        "finalized": bool(messages) and is_finalized_reply(messages[-1]),
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    (question_dir / QUESTION_RECORD_FILENAME).write_text(text, encoding="utf-8")


__all__ = (
    "QUESTIONS_DIRNAME",
    "QUESTION_RECORD_FILENAME",
    "QUESTION_RECORD_SCHEMA_VERSION",
    "ChatTraceMissingError",
    "ChatTraceWriter",
    "TraceLocation",
)
