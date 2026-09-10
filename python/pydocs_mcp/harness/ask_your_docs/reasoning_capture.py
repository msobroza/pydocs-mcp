"""Recover the reasoning text langchain-openai drops (activity panel, PROPOSAL §4).

langchain-openai 1.1.9 keeps only content, tool calls and audio from a Chat Completions
message or delta, so OpenRouter's ``reasoning`` and vLLM / DeepSeek / llama.cpp's
``reasoning_content`` never reach the page — although the endpoint already sent, and
billed, them. :func:`reasoning_chat_model_class` derives a subclass that copies that text
into ``additional_kwargs["reasoning_content"]``, the key langchain-core already concatenates
across stream chunks. Nothing on the wire changes: no request field is added.

Both overrides are PRIVATE upstream methods: pyproject caps langchain-openai below 2 and
``test_reasoning_capture`` fails CI if either disappears. Should one vanish anyway, capture
degrades to the stock class plus one ``reasoning_capture_unavailable`` JSON log line.
Imports no langchain itself — it only subclasses the base it is handed.

Example:
    >>> reasoning_text_from_payload({"reasoning": "plan", "reasoning_details": []})
    'plan'
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

REASONING_KWARG = "reasoning_content"  # where the captured text lands on the message
REDACTED_KWARG = "reasoning_redacted"  # True when the provider sent only an encrypted blob
OVERRIDDEN_METHODS = ("_create_chat_result", "_convert_chunk_to_generation_chunk")
# Order matters: OpenRouter sends ``reasoning`` AND ``reasoning_details`` carrying the same
# text in one delta — the first hit wins; the two are never concatenated.
_REASONING_STRING_FIELDS = ("reasoning_content", "reasoning")
_DETAIL_TEXT_FIELDS = {"reasoning.text": "text", "reasoning.summary": "summary"}
_ENCRYPTED_DETAIL = "reasoning.encrypted"
_OPEN_TAG, _CLOSE_TAG = "<think>", "</think>"


def reasoning_text_from_payload(payload: Mapping[str, Any]) -> str | None:
    """Reasoning text from one Chat Completions message or delta, in any known dialect."""
    for field in _REASONING_STRING_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and value:
            return value
    return "".join(_detail_text(detail) for detail in _details(payload)) or None


def reasoning_is_redacted(payload: Mapping[str, Any]) -> bool:
    """True when a ``reasoning.encrypted`` detail is present (the model reasoned, text hidden)."""
    return any(detail.get("type") == _ENCRYPTED_DETAIL for detail in _details(payload))


def _details(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    details = payload.get("reasoning_details")
    if not isinstance(details, list):
        return []
    return [detail for detail in details if isinstance(detail, Mapping)]


def _detail_text(detail: Mapping[str, Any]) -> str:
    field = _DETAIL_TEXT_FIELDS.get(str(detail.get("type")))
    value = detail.get(field) if field else None
    return value if isinstance(value, str) else ""


def annotate_reasoning(message: Any, payload: Mapping[str, Any]) -> None:
    """Copy ``payload``'s reasoning text and encrypted-only flag onto ``message``."""
    if text := reasoning_text_from_payload(payload):
        message.additional_kwargs[REASONING_KWARG] = text
    if reasoning_is_redacted(payload):
        message.additional_kwargs[REDACTED_KWARG] = True


def _response_choices(response: Any) -> list[Mapping[str, Any]]:
    # The openai SDK model keeps unknown fields, so model_dump() still carries ``reasoning``.
    body = response if isinstance(response, Mapping) else response.model_dump()
    return list(body.get("choices") or [])


def _chunk_delta(chunk: Mapping[str, Any]) -> Mapping[str, Any]:
    choices = chunk.get("choices") or (chunk.get("chunk") or {}).get("choices") or []
    delta = choices[0].get("delta") if choices else None
    return delta if isinstance(delta, Mapping) else {}


@functools.cache
def reasoning_chat_model_class(base: Any) -> Any:
    """``base`` (``ChatOpenAI``) subclassed to keep reasoning, or ``base`` itself if it can't be.

    Cached per base, so one subclass serves every model the page builds. A non-class base (a
    test spy) or one missing a hook comes back unchanged — AC-19's kwargs spy still sees the
    identical constructor call.
    """
    if isinstance(base, type) and all(callable(getattr(base, n, None)) for n in OVERRIDDEN_METHODS):
        return _derive_reasoning_class(base)
    logger.warning(json.dumps({"event": "reasoning_capture_unavailable", "base": _name(base)}))
    return base


def _name(base: Any) -> str:
    return str(getattr(base, "__name__", type(base).__name__))


def _derive_reasoning_class(base: type) -> type:
    class ReasoningChatOpenAI(base):  # type: ignore[valid-type,misc]
        """``base`` plus reasoning capture: same constructor, same request."""

        def _create_chat_result(self, response: Any, *args: Any, **kwargs: Any) -> Any:
            result = super()._create_chat_result(response, *args, **kwargs)
            choices = _response_choices(response)
            for generation, choice in zip(result.generations, choices, strict=False):
                annotate_reasoning(generation.message, choice.get("message") or {})
            return result

        def _convert_chunk_to_generation_chunk(self, chunk: Any, *args: Any, **kwargs: Any) -> Any:
            generation = super()._convert_chunk_to_generation_chunk(chunk, *args, **kwargs)
            if generation is not None:
                annotate_reasoning(generation.message, _chunk_delta(chunk))
            return generation

    return ReasoningChatOpenAI


class ThinkTagSplitter:
    """Split inline ``<think>…</think>`` reasoning out of streamed content (opt-in).

    For servers without a reasoning parser (llama.cpp ``--reasoning-format none``). A
    possible partial tag at a chunk boundary is held back until the next delta decides it.
    A ``</think>`` before any other tag means the chat template pre-filled ``<think>`` in the
    PROMPT (Qwen3 / R1), so everything before it was reasoning. Opt-in because a literal
    ``<think>`` in an ordinary answer would otherwise be mangled.

    NOT wired yet: no turn path runs it and ``ask_your_docs.ui.reasoning`` has no
    ``think_tags`` setting until one does (a setting that changes nothing is worse than none).

    Example:
        >>> splitter = ThinkTagSplitter()
        >>> splitter.feed("<think>plan</thi")
        >>> splitter.feed("nk>Answer")
        >>> splitter.finish()
        ('plan', 'Answer')
    """

    def __init__(self) -> None:
        self.reasoning = ""
        self.answer = ""
        self._pending = ""
        self._inside = False
        self._seen_tag = False

    def feed(self, delta: str) -> None:
        """Consume one content delta; complete tags are resolved, a partial one is held."""
        self._pending += delta
        while self._consume_next_tag():
            pass
        cut = len(self._pending) - _partial_tag_len(self._pending)
        self._emit(self._pending[:cut])
        self._pending = self._pending[cut:]

    def finish(self) -> tuple[str, str]:
        """Flush what is held back; returns ``(reasoning, answer)``."""
        self._emit(self._pending)
        self._pending = ""
        return self.reasoning, self.answer

    def _consume_next_tag(self) -> bool:
        if not self._seen_tag and self._consume_orphan_close():
            return True
        tag = _CLOSE_TAG if self._inside else _OPEN_TAG
        index = self._pending.find(tag)
        if index == -1:
            return False
        self._emit(self._pending[:index])
        self._pending = self._pending[index + len(tag) :]
        self._inside, self._seen_tag = not self._inside, True
        return True

    def _consume_orphan_close(self) -> bool:
        close, opener = self._pending.find(_CLOSE_TAG), self._pending.find(_OPEN_TAG)
        if close == -1 or -1 < opener < close:
            return False
        self.reasoning += self.answer + self._pending[:close]
        self.answer = ""
        self._pending = self._pending[close + len(_CLOSE_TAG) :]
        self._seen_tag = True
        return True

    def _emit(self, text: str) -> None:
        if self._inside:
            self.reasoning += text
        else:
            self.answer += text


def _partial_tag_len(text: str) -> int:
    """Length of a trailing prefix of either tag — held back until the next delta decides."""
    prefixes = (
        k for k in range(1, len(_CLOSE_TAG)) if text.endswith((_OPEN_TAG[:k], _CLOSE_TAG[:k]))
    )
    return max(prefixes, default=0)


__all__ = (
    "OVERRIDDEN_METHODS",
    "REASONING_KWARG",
    "REDACTED_KWARG",
    "ThinkTagSplitter",
    "annotate_reasoning",
    "reasoning_chat_model_class",
    "reasoning_is_redacted",
    "reasoning_text_from_payload",
)
