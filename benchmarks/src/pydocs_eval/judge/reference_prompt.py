"""The reference writer's prompt: what a source becomes as a request, and its hash (judge 9c).

The writer answers the question as a good agent answer would — path, symbol and
line span first, then 2–4 sentences on what the code does — from the shown code
alone. It returns one JSON string. ``prompt_hash`` is the sha256 of the
request's canonical JSON (messages and output schema), the value a stored
reference names its prompt by; a regenerated row's prompt adds one message
naming what the rejected draft got wrong, so its hash differs.

Example:
    >>> reference_prompt(source).request.custom_id  # doctest: +SKIP
    'repoqa-qa/repo_qa/…::factorial'
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_eval.judge.chat_wire import (
    ChatCompletion,
    ChatMessage,
    ChatRequest,
    MessageRole,
    StructuredOutput,
)
from pydocs_eval.judge.reference_sources import ReferenceSource, ShownCode
from pydocs_eval.trajectory.blob_store import canonical_json

_ANSWER_KEY = "answer"

#: The one structured field the writer returns: the whole reference as a string.
REFERENCE_OUTPUT = StructuredOutput(
    name="reference_answer",
    schema={
        "type": "object",
        "properties": {_ANSWER_KEY: {"type": "string"}},
        "required": [_ANSWER_KEY],
        "additionalProperties": False,
    },
)

_WRITER_INSTRUCTIONS = """\
You write the reference answer to a question about a code repository. Other \
answers to the question are judged against yours, so yours must be right, and \
it must rest only on the code you are shown.

You are shown the question and the code that answers it: for each place, its \
file path, its symbol, its line span, and its lines, numbered.

Write the answer the way a good answer to the question reads:
- Start with one line per place, in the order shown: the path, the symbol and \
the line span, the path and the symbol each in backticks exactly as given, for \
example `path/to/file.py` — `symbol` (lines 12–20).
- Then write 2 to 4 sentences on what that code does, taken only from the lines \
shown. Do not guess about code you were not shown.
- Name no file other than the ones shown, and write every function, class or \
method name in backticks.

Return JSON with one key, "answer": the whole answer as one string."""

_REJECTED_HEAD = "A previous draft of this answer failed a code check:"
_REJECTED_TAIL = "Write the answer again, fixing each of these."
# A fence is at least three backticks and longer than any run inside the code.
_BACKTICK_RUN = re.compile(r"`+")
_MIN_FENCE = 3


@dataclass(frozen=True, slots=True)
class ReferencePrompt:
    """One writer request and the hash a reference written from it carries."""

    request: ChatRequest
    prompt_hash: str


def reference_prompt(source: ReferenceSource, *, rejected: Sequence[str] = ()) -> ReferencePrompt:
    """The request that asks for ``source``'s reference; ``rejected`` names a failed draft's problems.

    Example:
        >>> reference_prompt(source, rejected=("names no path 'a.py'",)).prompt_hash  # doctest: +SKIP
        '5c1f…'
    """
    messages = [
        ChatMessage(MessageRole.SYSTEM, _WRITER_INSTRUCTIONS),
        ChatMessage(MessageRole.USER, _shown_source(source)),
    ]
    if rejected:
        messages.append(ChatMessage(MessageRole.USER, _rejected_draft(rejected)))
    request = ChatRequest(source.task_id, tuple(messages), REFERENCE_OUTPUT)
    return ReferencePrompt(request, _prompt_hash(request))


def reference_text_of(completion: ChatCompletion) -> str | None:
    """The reference ``completion`` wrote, or ``None`` when it wrote no non-empty string.

    Example:
        >>> reference_text_of(ChatCompletion("t", "m", {"answer": " `a.py` "}))
        '`a.py`'
    """
    text = completion.content.get(_ANSWER_KEY)
    if not isinstance(text, str) or not text.strip():
        return None
    return text.strip()


def _shown_source(source: ReferenceSource) -> str:
    places = "\n\n".join(_shown_code(code) for code in source.code)
    return f"Question:\n{source.question}\n\nThe code that answers it:\n\n{places}"


def _shown_code(code: ShownCode) -> str:
    head = (
        f"`{code.site.path}` — `{code.site.symbol}`, lines {code.start}–{code.end} "
        f"(shown: lines {code.first_line}–{code.last_line})"
    )
    width = len(str(code.last_line))
    numbered = [
        f"{number:>{width}} | {line}".rstrip()
        for number, line in enumerate(code.lines, start=code.first_line)
    ]
    fence = "`" * _fence_length(code.lines)
    return "\n".join([head, fence, *numbered, fence])


def _fence_length(lines: Sequence[str]) -> int:
    runs = [len(run) for line in lines for run in _BACKTICK_RUN.findall(line)]
    return max(_MIN_FENCE, max(runs, default=0) + 1)


def _rejected_draft(problems: Sequence[str]) -> str:
    listed = "\n".join(f"- {problem}" for problem in problems)
    return f"{_REJECTED_HEAD}\n{listed}\n{_REJECTED_TAIL}"


def _prompt_hash(request: ChatRequest) -> str:
    messages = [{"role": m.role.value, "content": m.content} for m in request.messages]
    output = {"name": request.output.name, "schema": request.output.schema}
    payload = canonical_json({"messages": messages, "output": output})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ("REFERENCE_OUTPUT", "ReferencePrompt", "reference_prompt", "reference_text_of")
