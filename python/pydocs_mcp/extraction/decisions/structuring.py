"""Default-OFF LLM structuring of mined decisions, behind a grounding gate (§D12).

Deterministic mining (§D8) ships a verbatim record with NO LLM in the index
path. When ``decision_capture.llm_structuring.enabled`` is set, this module asks
the configured :class:`LlmClient` to STRUCTURE the merged decisions into named
fields (title / context / decision / rationale / alternatives / consequences),
then runs every returned field through the grounding gate: a field survives only
if some sentence of it overlaps a verbatim evidence span at or above the
configured token-overlap threshold. Anything the LLM invented — a rationale not
present in the evidence — is dropped before it can reach the index. Dropping any
field marks the record ``"unverified"``; a fully-grounded field-set is
``"verified"``.

The gate is PURE (:func:`ground_structured_fields`); the batching driver
(:func:`structure_decisions`) is the only place that touches the client, and it
is a hard no-op when disabled (no client construction, no calls).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydocs_mcp.extraction.decisions._types import RawDecision

# _STOPWORDS is the decision-mining stopword set (single source of truth in the
# engine); the gate reuses it so a low-signal function word ("the", "with", "in")
# can't inflate the overlap ratio and let an ungrounded field slip through.
from pydocs_mcp.extraction.decisions.engine import _STOPWORDS, decision_key
from pydocs_mcp.retrieval.config import LlmStructuringConfig
from pydocs_mcp.retrieval.protocols import ChatMessage, LlmClient

log = logging.getLogger("pydocs-mcp")

# A "sentence" splits on terminal punctuation; a "content token" is a casefolded
# run of word chars WITH internal hyphens kept atomic (so "in-db" is one token,
# not "in" + "db" — splitting it would let a rejected alternative like "keep
# blobs in db" score spurious overlap against evidence that says "in-db blobs").
# Coarse by design — the gate measures overlap, not linguistic correctness.
_SENTENCE_RE = re.compile(r"[.!?]+")
_TOKEN_RE = re.compile(r"[\w-]*\w")

# The structured schema the prompt requests and the gate iterates. Single source
# of truth so the prompt text and the field-walk can never drift out of sync.
_STRUCTURED_FIELDS = (
    "title",
    "context",
    "decision",
    "rationale",
    "alternatives",
    "consequences",
)

# Verification tiers (spec §D12). "verbatim" is the deterministic-mining default
# stamped by the engine; the gate only ever returns the latter two.
_VERIFIED = "verified"
_UNVERIFIED = "unverified"


def _content_tokens(text: str) -> frozenset[str]:
    """Casefolded content-token SET of ``text`` (stopwords + blanks dropped)."""
    return frozenset(
        tok
        for m in _TOKEN_RE.finditer(text or "")
        if (tok := m.group(0).casefold()) not in _STOPWORDS
    )


def _sentences(text: str) -> tuple[str, ...]:
    """Split ``text`` into non-empty sentence spans on terminal punctuation."""
    return tuple(s for s in (part.strip() for part in _SENTENCE_RE.split(text or "")) if s)


def _overlap_ratio(candidate: frozenset[str], evidence: frozenset[str]) -> float:
    """Fraction of ``candidate`` tokens also present in ``evidence`` (0.0 if empty)."""
    if not candidate:
        return 0.0
    return len(candidate & evidence) / len(candidate)


def _is_grounded(
    value: str, evidence_token_sets: Sequence[frozenset[str]], *, threshold: float
) -> bool:
    """True when SOME sentence of ``value`` clears ``threshold`` against ANY span.

    A field is grounded if any one of its sentences has content-token overlap
    ≥ ``threshold`` with any single evidence span. Requiring per-span (not
    pooled) overlap keeps a field from being "grounded" by coincidental tokens
    scattered across unrelated evidence.
    """
    for sentence in _sentences(value):
        tokens = _content_tokens(sentence)
        if any(_overlap_ratio(tokens, ev) >= threshold for ev in evidence_token_sets):
            return True
    return False


def _ground_list_field(
    items: Sequence[object],
    evidence_token_sets: Sequence[frozenset[str]],
    *,
    threshold: float,
) -> tuple[list[str], bool]:
    """Filter a list field to its grounded items; flag whether any were dropped."""
    kept = [
        str(item)
        for item in items
        if _is_grounded(str(item), evidence_token_sets, threshold=threshold)
    ]
    dropped = len(kept) != len(items)
    return kept, dropped


def ground_structured_fields(
    structured: Mapping[str, object],
    evidence_texts: Sequence[str],
    *,
    threshold: float,
) -> tuple[dict[str, object], str]:
    """Drop any structured field not traceable to verbatim evidence (spec §D12).

    Per field, per sentence, the content-token overlap ratio against any evidence
    span must reach ``threshold`` or the field is dropped. List fields keep only
    their grounded items (and a partially-filtered list counts as a drop).
    Returns ``(surviving fields, "verified" | "unverified")`` — ``"unverified"``
    the instant any field or list item is dropped, ``"verified"`` when every
    field survived intact.

    >>> gated, tier = ground_structured_fields(
    ...     {"decision": "Use a sidecar file"}, ("Use a sidecar file",), threshold=0.6
    ... )
    >>> gated, tier
    ({'decision': 'Use a sidecar file'}, 'verified')
    """
    evidence_token_sets = [_content_tokens(text) for text in evidence_texts]
    surviving: dict[str, object] = {}
    dropped_any = False
    for field, value in structured.items():
        if isinstance(value, (list, tuple)):
            kept, dropped = _ground_list_field(value, evidence_token_sets, threshold=threshold)
            dropped_any = dropped_any or dropped
            if kept:
                surviving[field] = kept
            continue
        if _is_grounded(str(value), evidence_token_sets, threshold=threshold):
            surviving[field] = value
        else:
            dropped_any = True
    return surviving, (_UNVERIFIED if dropped_any else _VERIFIED)


def _batches(records: tuple[RawDecision, ...], size: int) -> tuple[tuple[RawDecision, ...], ...]:
    """Split ``records`` into contiguous chunks of at most ``size``."""
    return tuple(records[i : i + size] for i in range(0, len(records), size))


def _build_prompt(batch: tuple[RawDecision, ...]) -> list[ChatMessage]:
    """One strict-JSON structuring request for a batch of merged decisions.

    The user turn lists each decision's title + verbatim evidence; the system
    turn pins the exact JSON envelope so the tolerant parser has a stable shape
    to look for.
    """
    lines: list[str] = []
    for idx, record in enumerate(batch):
        evidence = "\n".join(f"    - {ev.text}" for ev in record.evidence)
        lines.append(f"[{idx}] {record.title}\n{evidence}")
    body = "\n\n".join(lines)
    fields = ", ".join(_STRUCTURED_FIELDS)
    return [
        {
            "role": "system",
            "content": (
                "You structure mined software-architecture decisions. Reply with "
                'STRICT JSON of the form {"decisions": [{...}]} where each object has '
                f"the fields: {fields}. Use ONLY facts present in the supplied "
                "evidence — never invent a rationale or alternative."
            ),
        },
        {"role": "user", "content": body},
    ]


def _parse_batch_reply(reply: str) -> list[dict[str, Any]] | None:
    """Tolerantly parse a batch reply into a list of decision objects.

    Returns ``None`` (caller skips + logs the whole batch) on any malformed
    shape: non-JSON, missing ``decisions`` key, or a non-list value. Never
    raises — a broken LLM reply must not fail the index.
    """
    try:
        parsed = json.loads(reply)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    decisions = parsed.get("decisions")
    if not isinstance(decisions, list):
        return None
    return [obj for obj in decisions if isinstance(obj, dict)]


def _structured_from_object(obj: Mapping[str, Any]) -> dict[str, object]:
    """Keep only the known structured fields, dropping the title (it is the key)."""
    return {field: obj[field] for field in _STRUCTURED_FIELDS if field != "title" and field in obj}


def _gate_object(
    obj: Mapping[str, Any],
    by_key: Mapping[str, RawDecision],
    *,
    threshold: float,
) -> tuple[str, tuple[dict[str, object], str]] | None:
    """Match one LLM object to its source decision, then gate its fields.

    Matches on ``decision_key(title)`` so a structured object binds to exactly
    the merged decision it was derived from. An object whose title doesn't map to
    any input decision is dropped (the LLM hallucinated a decision).
    """
    title = obj.get("title")
    if not isinstance(title, str):
        return None
    key = decision_key(title)
    source = by_key.get(key)
    if source is None:
        return None
    evidence_texts = tuple(ev.text for ev in source.evidence)
    gated, verification = ground_structured_fields(
        _structured_from_object(obj), evidence_texts, threshold=threshold
    )
    return key, (gated, verification)


async def structure_decisions(
    records: tuple[RawDecision, ...],
    llm_client: LlmClient,
    config: LlmStructuringConfig,
) -> dict[str, tuple[dict[str, object], str]]:
    """Structure ``records`` via the LLM, gated on verbatim evidence (spec §D12).

    Batches ``records`` into groups of ``config.batch_size``, one strict-JSON
    chat call per batch. Each batch reply is tolerantly parsed (malformed → the
    whole batch is skipped and logged); every surviving object is grounded
    against its source decision's evidence. Returns a mapping
    ``decision_key -> (surviving structured fields, verification tier)`` for the
    decisions the LLM structured — decisions the LLM left untouched simply don't
    appear (they keep the engine's ``"verbatim"`` default).

    Disabled config is a hard no-op: the client is never touched and an empty
    mapping is returned.
    """
    if not config.enabled or not records:
        return {}

    by_key = {decision_key(record.title): record for record in records}
    out: dict[str, tuple[dict[str, object], str]] = {}
    for batch in _batches(records, config.batch_size):
        reply = await llm_client.chat(_build_prompt(batch), response_format="json_object")
        objects = _parse_batch_reply(reply)
        if objects is None:
            log.warning(
                "decision structuring: malformed batch reply, skipping %d records", len(batch)
            )
            continue
        for obj in objects:
            gated = _gate_object(obj, by_key, threshold=config.grounding_threshold)
            if gated is not None:
                out[gated[0]] = gated[1]
    return out


__all__ = ("ground_structured_fields", "structure_decisions")
