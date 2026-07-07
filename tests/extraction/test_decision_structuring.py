"""Tests for the default-OFF LLM structuring gate (spec §D12).

Two units under test, both offline (a canned :class:`FakeLlmClient`, no
network):

* :func:`ground_structured_fields` — the PURE grounding gate. Per structured
  field, per sentence, it measures content-token overlap against every verbatim
  evidence span; a field with no sentence clearing ``threshold`` is dropped, and
  dropping ANY field marks the record ``"unverified"`` (all fields grounded →
  ``"verified"``).
* :func:`structure_decisions` — batches merged decisions (``batch_size`` per
  chat call), asks for strict JSON, tolerantly parses (malformed reply → skip
  the whole batch, logged), and runs each returned field-set through the gate.
  Disabled config is a hard no-op (no client calls at all).
"""

from __future__ import annotations

from pydocs_mcp.extraction.decisions._types import RawDecision
from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.extraction.decisions.structuring import (
    ground_structured_fields,
    structure_decisions,
)
from pydocs_mcp.retrieval.config import LlmStructuringConfig
from pydocs_mcp.storage.decision_record import DecisionEvidence
from tests._fakes import FakeLlmClient


def _raw(title: str) -> RawDecision:
    """Minimal merged decision with one verbatim evidence span."""
    return RawDecision(
        title=title,
        status="active",
        source="inline_markers",
        confidence=0.9,
        evidence=(
            DecisionEvidence(
                source="inline_markers",
                locator="pkg/mod.py:10-30",
                text=title,
            ),
        ),
        affected_files=("pkg/mod.py",),
        affected_qnames=("pkg.mod",),
    )


# ── ground_structured_fields — the pure gate ──


def test_grounded_fields_survive_and_mark_verified() -> None:
    evidence = ("We replace the in-db blobs with a sidecar file. Rationale: row size.",)
    structured = {
        "decision": "Replace in-db blobs with a sidecar file",
        "rationale": "Row size",
        "alternatives": ["keep blobs in db"],
    }
    gated, verification = ground_structured_fields(structured, evidence, threshold=0.60)
    assert gated["decision"] and gated["rationale"]
    assert "alternatives" not in gated  # no evidence token overlap → dropped
    assert verification == "unverified"  # a field was dropped


def test_all_fields_grounded_gives_verified() -> None:
    evidence = ("We replace the in-db blobs with a sidecar file. Rationale: row size.",)
    structured = {
        "decision": "Replace the in-db blobs with a sidecar file",
        "rationale": "row size",
    }
    gated, verification = ground_structured_fields(structured, evidence, threshold=0.60)
    assert gated["decision"] and gated["rationale"]
    assert verification == "verified"  # every field survived the gate


def test_list_field_keeps_only_grounded_items() -> None:
    evidence = ("Keep blobs in the database was rejected. Sidecar file chosen.",)
    structured = {"alternatives": ["keep blobs in the database", "use a remote object store"]}
    gated, verification = ground_structured_fields(structured, evidence, threshold=0.60)
    assert gated["alternatives"] == ["keep blobs in the database"]
    # the ungrounded second item was dropped → not fully verified
    assert verification == "unverified"


def test_empty_structured_is_verified_with_no_fields() -> None:
    gated, verification = ground_structured_fields({}, ("some evidence",), threshold=0.60)
    assert gated == {}
    assert verification == "verified"  # nothing dropped


# ── structure_decisions — batching + tolerant parse + gate ──


async def test_structuring_disabled_is_a_noop() -> None:
    client = FakeLlmClient(responses={})
    config = LlmStructuringConfig(enabled=False)
    result = await structure_decisions((_raw("Use a sidecar file"),), client, config)
    assert result == {}
    assert client._calls == []  # disabled → the client is never touched


async def test_batching_five_records_per_call() -> None:
    # 12 records at batch_size=5 → ceil(12/5) == 3 chat calls. Every reply is
    # malformed JSON → each batch is skipped and logged, yielding no structured
    # output, but the call COUNT still proves the batching arithmetic.
    records = tuple(_raw(f"Decision number {i}") for i in range(12))
    client = FakeLlmClient(responses={"": "not valid json at all"})
    config = LlmStructuringConfig(enabled=True, batch_size=5)
    result = await structure_decisions(records, client, config)
    assert len(client._calls) == 3  # ceil(12 / 5)
    assert result == {}  # every batch's malformed reply was skipped


async def test_valid_batch_reply_is_gated_and_keyed_by_decision() -> None:
    record = _raw("Replace the in-db blobs with a sidecar file")
    reply = (
        '{"decisions": [{"title": "Replace the in-db blobs with a sidecar file", '
        '"decision": "Replace the in-db blobs with a sidecar file", '
        '"rationale": "row size", '
        '"alternatives": ["keep unrelated cache warming"]}]}'
    )
    client = FakeLlmClient(responses={"": reply})
    config = LlmStructuringConfig(enabled=True, batch_size=5, grounding_threshold=0.60)
    result = await structure_decisions((record,), client, config)
    key = decision_key(record.title)
    assert key in result
    structured, verification = result[key]
    assert structured["decision"]  # grounded → survives
    assert "alternatives" not in structured  # ungrounded → dropped by the gate
    assert verification == "unverified"
