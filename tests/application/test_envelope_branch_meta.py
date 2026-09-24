"""The resolved branch reaches ``meta`` (spec §6.4, §6.5c, §6.7; #311, O19).

``meta.branch`` names the resolution and is the only envelope addition; the
default selector keeps today's per-project ``index_stale`` reading while an
explicit selection reads the branch's own pair; the resolution's suggestion
reaches only the three tools that declare the field; and the freshness facts
come from the probe the caller hands in (the answering project's own).
"""

from __future__ import annotations

import json
import logging

import pytest
from pydantic import BaseModel

from pydocs_mcp.application.branch_resolution import (
    NULL_RESOLUTION,
    BranchSelectorKind,
    ResolvedBranch,
)
from pydocs_mcp.application.envelope import ResponseEnvelope, _assemble_meta
from pydocs_mcp.application.freshness import EnvelopeInfo
from pydocs_mcp.application.suggestions import CHECKOUT_NOT_INDEXED_RULE
from pydocs_mcp.application.tool_response import (
    ENVELOPE_MODELS,
    SUGGESTION_TOOLS,
    MetaModel,
    SuggestionMetaModel,
    _meta_declares_suggestion,
)
from pydocs_mcp.models import NON_GIT_BRANCH_NAME, BranchIndexSource, LandingKind
from pydocs_mcp.storage.branch_records import BranchRecord

from ._router_fakes import CountingProbe

A, B = "a" * 40, "b" * 40
_SUGGESTION = (
    "[suggestion: checked-out branch 'y' is not indexed; run: pydocs-mcp index . --branch y]"
)
_META_KEYS = {
    "tool",
    "project",
    "indexed_git_head",
    "live_git_head",
    "index_stale",
    "branch",
    "truncated",
}


def _info(*, stale: bool, branch: str | None = "main") -> EnvelopeInfo:
    return EnvelopeInfo(A, B if stale else A, 0, 1, stale, branch)


def _resolved(
    name: str,
    kind: BranchSelectorKind,
    *,
    head: str = A,
    live: str | None = None,
    suggestion: str | None = None,
    landing: bool = False,
) -> ResolvedBranch:
    record = BranchRecord(
        name,
        head,
        BranchIndexSource.WORKING_TREE,
        "p",
        1.0,
        1.0,
        landing_kind=LandingKind.SINGLE_COMMIT if landing else None,
    )
    return ResolvedBranch(name, record, kind, live, suggestion)


def _meta(tool: str = "get_symbol", info: EnvelopeInfo | None = None, **kw: object) -> dict:
    return _assemble_meta(
        tool=tool, project="p", info=info, truncated=False, extras=kw.pop("extras", {}), **kw
    )


def test_without_a_resolution_meta_is_exactly_todays() -> None:
    info = _info(stale=True, branch="feature/x")
    assert _meta(info=info) == _meta(info=info, branch=None)
    assert _meta(info=info)["branch"] == "feature/x" and _meta(info=info)["index_stale"] is True


def test_default_selector_names_the_resolved_branch_and_keeps_the_probe_staleness() -> None:
    # The per-branch pair says "fresh"; the probe (working tree vs the last pass)
    # says "stale": the default selector keeps today's reading (#311 identity).
    resolved = _resolved("main", BranchSelectorKind.DEFAULT, head=A, live=A)
    meta = _meta(info=_info(stale=True), branch=resolved)
    assert meta["branch"] == "main" and meta["index_stale"] is True
    fresh = _meta(
        info=_info(stale=False), branch=_resolved("main", BranchSelectorKind.DEFAULT, live=B)
    )
    assert fresh["index_stale"] is False
    assert set(meta) == _META_KEYS  # meta.branch is the only addition — no new key


def test_default_selector_without_probe_facts_keeps_branch_null() -> None:
    # Contract §2.4 case 4: no freshness facts, no branch — for the default selector.
    meta = _meta(info=None, branch=_resolved("main", BranchSelectorKind.DEFAULT))
    assert meta["branch"] is None and meta["index_stale"] is False


def test_the_null_resolution_and_the_non_git_row_render_null() -> None:
    assert _meta(info=_info(stale=False, branch=None), branch=NULL_RESOLUTION)["branch"] is None
    non_git = _resolved(NON_GIT_BRANCH_NAME, BranchSelectorKind.DEFAULT)
    assert _meta(info=_info(stale=False, branch=None), branch=non_git)["branch"] is None


def _pair(meta: dict) -> tuple[object, object, object]:
    return meta["indexed_git_head"], meta["live_git_head"], meta["index_stale"]


def test_an_explicit_selection_reads_the_branch_pair_for_all_three_fields() -> None:
    # Contract §2.1: index_stale is "true only when both heads resolve and
    # differ" — so the heads it sits beside are the selected branch's own
    # pair too (spec §7 item 4), never the checkout's.
    stale = _resolved("feature/x", BranchSelectorKind.NAME, head=A, live=B)
    meta = _meta(info=_info(stale=False), branch=stale)
    assert meta["branch"] == "feature/x" and _pair(meta) == (A, B, True)
    fresh = _resolved("feature/x", BranchSelectorKind.NAME, head=B, live=B)
    assert _pair(_meta(info=_info(stale=True), branch=fresh)) == (B, B, False)
    unread = _resolved("feature/x", BranchSelectorKind.NAME, head=A, live=None)
    assert _pair(_meta(info=_info(stale=True), branch=unread)) == (A, None, False)


def test_an_explicit_selection_without_probe_facts_names_the_branch_and_no_pair() -> None:
    # No freshness facts (envelope disabled, unstamped bundle): both heads null,
    # so never stale — but the selection still names its branch.
    stale = _resolved("feature/x", BranchSelectorKind.NAME, head=A, live=B)
    meta = _meta(info=None, branch=stale)
    assert meta["branch"] == "feature/x" and _pair(meta) == (None, None, False)


def test_a_landing_unit_is_never_stale() -> None:
    unit = _resolved(A, BranchSelectorKind.LANDING_SHA, head=A, live=B, landing=True)
    meta = _meta(info=_info(stale=True), branch=unit)
    assert meta["branch"] == A and _pair(meta) == (A, None, False)


def test_suggestion_tools_are_the_ones_whose_meta_declares_the_field() -> None:
    assert frozenset({"search_codebase", "get_why", "grep"}) == SUGGESTION_TOOLS
    assert frozenset(ENVELOPE_MODELS) > SUGGESTION_TOOLS


def test_a_meta_model_extending_the_suggestion_one_still_declares_the_field() -> None:
    class _ReferencesWithSuggestion(SuggestionMetaModel):
        resolution: str | None = None

    class _Envelope(BaseModel):
        meta: _ReferencesWithSuggestion

    class _Plain(BaseModel):
        meta: MetaModel

    assert _meta_declares_suggestion(_Envelope) and not _meta_declares_suggestion(_Plain)


def _fired_rules(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        json.loads(r.getMessage())["rule"]
        for r in caplog.records
        if '"suggestion_fired"' in r.getMessage()
    ]


@pytest.mark.parametrize("tool", sorted(SUGGESTION_TOOLS))
def test_the_resolution_suggestion_reaches_the_suggestion_tools(
    tool: str, caplog: pytest.LogCaptureFixture
) -> None:
    resolved = _resolved("main", BranchSelectorKind.DEFAULT, suggestion=_SUGGESTION)
    with caplog.at_level(logging.DEBUG):
        meta = _meta(tool, info=_info(stale=False), branch=resolved)
    assert meta["suggestion"] == _SUGGESTION
    # The eval trace merge requires a fired-rule record exactly when
    # meta.suggestion is present (presence must agree).
    assert _fired_rules(caplog) == [CHECKOUT_NOT_INDEXED_RULE]


def test_the_tools_own_suggestion_wins_over_the_resolution_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    resolved = _resolved("main", BranchSelectorKind.DEFAULT, suggestion=_SUGGESTION)
    own = {"suggestion": "[suggestion: zero hits — orient with get_overview()]"}
    with caplog.at_level(logging.DEBUG):
        meta = _meta("search_codebase", info=_info(stale=False), branch=resolved, extras=own)
    assert meta["suggestion"] == own["suggestion"]
    assert _fired_rules(caplog) == []  # the tool logged its own rule; nothing added


def test_tools_without_the_field_drop_the_suggestion_and_log_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    resolved = _resolved("main", BranchSelectorKind.DEFAULT, suggestion=_SUGGESTION)
    with caplog.at_level(logging.DEBUG):
        meta = _meta("get_symbol", info=_info(stale=False), branch=resolved)
    assert set(meta) == _META_KEYS
    assert _fired_rules(caplog) == []
    assert any("branch_suggestion_dropped" in r.getMessage() for r in caplog.records)


async def _body() -> str:
    return "body"


async def test_wrap_reads_the_probe_it_is_handed_over_its_own() -> None:
    own = CountingProbe(_info(stale=False))
    answering = CountingProbe(EnvelopeInfo(B, A, 3, 9, True, "dev"))
    envelope = ResponseEnvelope(probe=own, surface="cli", pointers_enabled=False)
    response = await envelope.wrap("glob", "p", _body, probe=answering)
    assert (own.calls, answering.calls) == (0, 1)
    assert response.meta["indexed_git_head"] == B and response.meta["index_stale"] is True
    assert response.text.startswith(f"[index: {B[:7]} · 3d old · 9 packages]")


async def test_wrap_with_a_resolution_changes_meta_only() -> None:
    envelope = ResponseEnvelope(
        probe=CountingProbe(_info(stale=False)), surface="cli", pointers_enabled=False
    )
    plain = await envelope.wrap("grep", "p", _body)
    resolved = _resolved("main", BranchSelectorKind.DEFAULT, suggestion=_SUGGESTION)
    branched = await envelope.wrap("grep", "p", _body, branch=resolved)
    assert branched.text == plain.text and branched.items == plain.items
    assert branched.meta == {**plain.meta, "suggestion": _SUGGESTION}
