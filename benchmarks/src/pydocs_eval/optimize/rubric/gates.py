"""Deterministic gate predicates — free per-sample boolean checks (spec §3.4.2).

Each gate is a stateless frozen dataclass registered in ``gate_registry``
(the shared ``_Registry`` mechanic) and called with ``(task, transcript,
params)``. Gates are free by contract: no LLM, no I/O beyond the transcript
already in memory. New kinds are one ``@gate_registry.register("…")`` away.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.optimize.rubric.model import GateCheck
from pydocs_eval.serialization import (
    _Registry,  # WHY: same in-repo registry mechanic as the optimize axes; a second copy would drift
)

# Gate default params — single sources; GateCheck.params overrides per config.
_DEFAULT_MIN_ANSWER_CHARS = 40
_DEFAULT_USED_TOOLS = 1
_DEFAULT_MAX_TURNS = 12
_DEFAULT_MAX_WALL_SECONDS = 300.0

# The six task-shaped MCP tools an "indexed" call counts against. Hard-coded
# HERE (not derived from product TOOL_DOCS) so the rubric core stays free of
# the [retrieval] extra; the ask_prompt artifact's TOOL_DOCS-derived check is
# the drift alarm — a surface change breaks it loudly, and this tuple follows.
_INDEXED_TOOL_NAMES = frozenset(
    {"get_overview", "search_codebase", "get_symbol", "get_context", "get_references", "get_why"}
)


@runtime_checkable
class TranscriptLike(Protocol):
    """The transcript view gates read — structurally satisfied by AskTranscript."""

    answer: str
    tool_calls: tuple[ToolCallLike, ...]
    turns: int
    wall_seconds: float


@runtime_checkable
class ToolCallLike(Protocol):
    """One tool invocation as gates see it."""

    tool_name: str


class GatePredicate(Protocol):
    """A pure per-sample boolean check (spec §3.4.2)."""

    def __call__(
        self, task: EvalTask, transcript: TranscriptLike, params: Mapping[str, object]
    ) -> bool: ...


gate_registry: _Registry[GatePredicate] = _Registry()


def evaluate_gate(check: GateCheck, task: EvalTask, transcript: TranscriptLike) -> bool:
    """Resolve ``check.kind`` in the registry and run it with ``check.params``.

    Raises:
        KeyError: unknown ``kind``, naming the registered kinds (AC-7 shape).

    Example:
        >>> from pydocs_eval.optimize.rubric.model import GateCheck  # doctest: +SKIP
        >>> evaluate_gate(GateCheck("g", "max_turns", {"n": 8}), task, transcript)  # doctest: +SKIP
        True
    """
    predicate = gate_registry.build(check.kind)
    return predicate(task, transcript, check.params)


@gate_registry.register("min_answer_chars")
@dataclass(frozen=True, slots=True)
class MinAnswerChars:
    """Answer length ≥ ``n`` — screens empty / refused answers for free."""

    def __call__(
        self, task: EvalTask, transcript: TranscriptLike, params: Mapping[str, object]
    ) -> bool:
        threshold = int(params.get("n", _DEFAULT_MIN_ANSWER_CHARS))  # type: ignore[call-overload]
        return len(transcript.answer) >= threshold


@gate_registry.register("answer_regex")
@dataclass(frozen=True, slots=True)
class AnswerRegex:
    """``params["pattern"]`` matches somewhere in the answer."""

    def __call__(
        self, task: EvalTask, transcript: TranscriptLike, params: Mapping[str, object]
    ) -> bool:
        pattern = str(params["pattern"])
        return re.search(pattern, transcript.answer) is not None


@gate_registry.register("gold_substring")
@dataclass(frozen=True, slots=True)
class GoldSubstring:
    """Any gold path/symbol from the task appears verbatim in the answer.

    Evidence comes from ``gold.file_set`` plus every string value in
    ``gold.extra`` (symbol names, anchors). A task carrying no gold
    substrings passes vacuously — no evidence to check is not a failure.
    """

    def __call__(
        self, task: EvalTask, transcript: TranscriptLike, params: Mapping[str, object]
    ) -> bool:
        _ = params
        candidates = list(task.gold.file_set)
        candidates += [v for v in task.gold.extra.values() if isinstance(v, str)]
        if not candidates:
            return True
        return any(candidate in transcript.answer for candidate in candidates)


@gate_registry.register("used_indexed_tools")
@dataclass(frozen=True, slots=True)
class UsedIndexedTools:
    """≥ ``n`` calls to the six task-shaped tools — deterministic groundedness."""

    def __call__(
        self, task: EvalTask, transcript: TranscriptLike, params: Mapping[str, object]
    ) -> bool:
        threshold = int(params.get("n", _DEFAULT_USED_TOOLS))  # type: ignore[call-overload]
        indexed = sum(1 for call in transcript.tool_calls if call.tool_name in _INDEXED_TOOL_NAMES)
        return indexed >= threshold


@gate_registry.register("max_turns")
@dataclass(frozen=True, slots=True)
class MaxTurns:
    """Transcript turns ≤ ``n`` — bounds runaway agent loops."""

    def __call__(
        self, task: EvalTask, transcript: TranscriptLike, params: Mapping[str, object]
    ) -> bool:
        threshold = int(params.get("n", _DEFAULT_MAX_TURNS))  # type: ignore[call-overload]
        return transcript.turns <= threshold


@gate_registry.register("max_wall_seconds")
@dataclass(frozen=True, slots=True)
class MaxWallSeconds:
    """Wall time ≤ ``s`` — an efficiency floor in the objective."""

    def __call__(
        self, task: EvalTask, transcript: TranscriptLike, params: Mapping[str, object]
    ) -> bool:
        threshold = float(params.get("s", _DEFAULT_MAX_WALL_SECONDS))  # type: ignore[arg-type]
        return transcript.wall_seconds <= threshold
