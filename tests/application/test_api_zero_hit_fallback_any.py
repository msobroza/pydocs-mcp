"""The ``api_zero_hit_fallback_any`` rule at the public ``ToolRouter`` seam (ADR 0027).

A ``kind="api"`` request whose member lookup (the API pass) returns no row is
answered in the same response by the same request run at ``kind="any"`` (the
fallback pass), under a fixed label. Outcomes of a ``kind="api"`` request:

- **H** — the API pass returned rows: today's bytes, whatever the flags;
- **S** — zero API rows, the fallback served rows: label + the ``any`` body;
- **E** — both passes empty: today's zero-hit body with the retry line's text
  replaced by the fixed "both empty" line;
- **X** — the fallback pass raised: a typed ``ServiceUnavailableError``.

Every outcome test keeps three assertions apart: the requested kind (the API
pass ran first), the raw API zero hit (the fired rule), and the served kind
(the label and each row's own ``kind``). Expected bytes are literals taken
from the spec and from the behaviour at ``9b9de5a3``; the flags are always
set explicitly (``F`` = this rule, ``Z`` = ``search_zero_hit``, ``R`` =
``api_zero_hit_retry_any``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from dataclasses import dataclass
from typing import Any

import pytest

from pydocs_mcp.application.branch_directory import BranchSnapshot
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
)
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.models import BranchIndexSource, Chunk, ModuleMember
from pydocs_mcp.observability.rendered_rows import begin_rendered_rows_capture
from pydocs_mcp.retrieval.config import SuggestionsConfig
from pydocs_mcp.storage.branch_records import BranchRecord

from ._router_fakes import (
    FakeBranchDirectory,
    RaisingDocsSearch,
    RecordingApiSearch,
    RecordingDocsSearch,
    make_envelope,
    make_service,
)

# ── Pinned bytes (spec D4 / D5; #438 texts at 9b9de5a3) ───────────────────
F_SERVED = (
    '[suggestion: kind="api" matched nothing — showing kind="any" results '
    "for the same query and filters]"
)
F_EMPTY = (
    '[suggestion: kind="api" matched nothing, and kind="any" found nothing '
    "for the same query and filters either]"
)
F_RULE, Z_RULE, R_RULE = "api_zero_hit_fallback_any", "search_zero_hit", "api_zero_hit_retry_any"
QUERY = "nested serialization"
HEADER = "[index: 8e2110e · 0d old · 1 packages]"
POINTER = "Together: → get_overview()"
Z_TEXT = "[suggestion: zero hits — orient with get_overview()]"
RETRY = (
    '[suggestion: kind="api" matched nothing — retry with search_codebase('
    'query="nested serialization", kind="any") for a descriptive query, or give a name fragment]'
)
ZR = [(True, True), (True, False), (False, True), (False, False)]
ZR_IDS = ["Z-on-R-on", "Z-on-R-off", "Z-off-R-on", "Z-off-R-off"]

# Today's kind="api" zero-hit body per (Z, R) — the #438 flag-independence table.
TODAY_ZERO_HIT_BODY = {
    (True, True): f"No symbols found.\n{POINTER}\n\n{RETRY}",
    (True, False): f"No symbols found.\n{POINTER}",
    (False, True): f"No symbols found.\n{RETRY}",
    (False, False): "No symbols found.",
}
TODAY_SUGGESTION = {(True, True): RETRY, (True, False): Z_TEXT, (False, True): RETRY}
TODAY_RULES = {
    (True, True): [Z_RULE, R_RULE],
    (True, False): [Z_RULE],
    (False, True): [R_RULE],
    (False, False): [],
}

CHUNK_ROW = Chunk(
    text="Nested serialization walks every child node.",
    id=11,
    relevance=0.9,
    metadata={
        "title": "Tree.walk",
        "qualified_name": "pkg.mod.Tree.walk",
        "package": "__project__",
        "source_path": "pkg/mod.py",
        "start_line": 3,
        "end_line": 9,
    },
)
MEMBER_ROW = ModuleMember(
    id=7,
    relevance=0.5,
    metadata={
        "package": "__project__",
        "module": "pkg.mod",
        "name": "serialize",
        "kind": "function",
        "signature": "(tree)",
        "docstring": "Serialize a tree.",
    },
)


def flags(f: bool, z: bool = True, r: bool = True) -> SuggestionsConfig:
    return SuggestionsConfig(
        api_zero_hit_fallback_any=f, search_zero_hit=z, api_zero_hit_retry_any=r
    )


@dataclass
class Served:
    """One served response plus what the tool logged and published."""

    response: Any
    rules: list[str]
    records: list[dict[str, str]]
    rendered_rows: int | None


def solo(docs: Any, api: Any, **replace: Any) -> ProjectServices:
    return dataclasses.replace(make_service("solo"), docs=docs, api=api, **replace)


def router(*services: ProjectServices, suggestions: SuggestionsConfig, budget=None) -> ToolRouter:
    search = MultiProjectSearch(services=services)
    if budget is not None:
        search = MultiProjectSearch(services=services, budget_tokens=budget)
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=search,
        lookup_router=MultiProjectLookup(services=services),
        suggestions=suggestions,
    )


async def serve(
    tool: ToolRouter, payload: SearchInput, caplog: pytest.LogCaptureFixture, **kw: Any
) -> Served:
    caplog.clear()
    capture = begin_rendered_rows_capture()
    with caplog.at_level("INFO", logger="pydocs_mcp.application.suggestions"):
        response = await tool.search_codebase(payload, **kw)
    records = [
        json.loads(r.message)
        for r in caplog.records
        if r.name == "pydocs_mcp.application.suggestions"
    ]
    served = Served(response, [r["rule"] for r in records], records, capture.count)
    # Contract §2.3 / D8: some rule is logged ⇔ meta.suggestion is non-null.
    assert bool(served.rules) is bool(response.meta.get("suggestion"))
    return served


def api_request(**kw: Any) -> SearchInput:
    return SearchInput(query=QUERY, kind="api", **kw)


def direct_any(payload: SearchInput) -> SearchInput:
    return payload.model_copy(update={"kind": "any"})


def without_label(text: str) -> str:
    """The response text with exactly the label line and its newline removed."""
    assert text.count(F_SERVED + "\n") == 1
    return text.replace(F_SERVED + "\n", "", 1)


# ── F off: today's bytes, and no kind="any" query ─────────────────────────


@pytest.mark.parametrize(("z", "r"), ZR, ids=ZR_IDS)
async def test_flag_off_zero_hit_is_today_and_issues_no_any_query(
    z: bool, r: bool, caplog: pytest.LogCaptureFixture
) -> None:
    docs, api = RecordingDocsSearch((CHUNK_ROW,)), RecordingApiSearch()
    out = await serve(
        router(solo(docs, api), suggestions=flags(False, z, r)), api_request(), caplog
    )
    assert out.response.text == f"{HEADER}\n\n{TODAY_ZERO_HIT_BODY[(z, r)]}\n"
    assert out.response.items == ()
    assert out.response.meta.get("suggestion") == TODAY_SUGGESTION.get((z, r))
    assert out.rules == TODAY_RULES[(z, r)]
    assert docs.queries == [] and len(api.queries) == 1
    assert out.response.meta["truncated"] is False and out.rendered_rows == 0


# ── F on, S: the fallback served rows ─────────────────────────────────────


@pytest.mark.parametrize(("z", "r"), ZR, ids=ZR_IDS)
async def test_served_fallback_labels_the_any_results(
    z: bool, r: bool, caplog: pytest.LogCaptureFixture
) -> None:
    docs, api = RecordingDocsSearch((CHUNK_ROW,)), RecordingApiSearch()
    tool = router(solo(docs, api), suggestions=flags(True, z, r))
    out = await serve(tool, api_request(), caplog)
    direct = await serve(tool, direct_any(api_request()), caplog)
    lines = out.response.text.split("\n")
    # Requested kind: the API pass (member lookup) ran first, alone.
    assert api.queries[0].terms == QUERY and len(api.queries) == 3 and len(docs.queries) == 2
    # Raw API zero hit: the fallback rule fired, once, and nothing else.
    assert out.rules == [F_RULE]
    assert out.records == [{"event": "suggestion_fired", "tool": "search_codebase", "rule": F_RULE}]
    # Served kind: the label is the first body line, after the freshness header…
    assert lines[:3] == [HEADER, "", F_SERVED]
    assert out.response.meta["suggestion"] == F_SERVED
    # …and the rows are the direct kind="any" rows, each with its own kind.
    assert out.response.items == direct.response.items
    assert [item["kind"] for item in out.response.items] == ["chunk"]
    assert without_label(out.response.text) == direct.response.text
    assert "No symbols found." not in out.response.text and RETRY not in out.response.text
    assert out.rendered_rows == direct.rendered_rows == 1
    assert out.response.meta["truncated"] is direct.response.meta["truncated"] is False


# ── F on, E: both passes empty ────────────────────────────────────────────


@pytest.mark.parametrize(("z", "r"), ZR, ids=ZR_IDS)
async def test_both_passes_empty_replaces_the_retry_line_with_the_empty_label(
    z: bool, r: bool, caplog: pytest.LogCaptureFixture
) -> None:
    docs, api = RecordingDocsSearch(), RecordingApiSearch()
    out = await serve(router(solo(docs, api), suggestions=flags(True, z, r)), api_request(), caplog)
    # Today's (Z, R on) body, with the retry text replaced by F_EMPTY.
    expected_body = TODAY_ZERO_HIT_BODY[(z, True)].replace(RETRY, F_EMPTY)
    assert out.response.text == f"{HEADER}\n\n{expected_body}\n"
    # Requested kind / raw API zero hit / served kind (nothing served).
    assert api.queries[0].terms == QUERY and len(docs.queries) == 1
    assert out.rules == ([Z_RULE, F_RULE] if z else [F_RULE])
    assert out.response.items == ()
    assert out.response.meta["suggestion"] == F_EMPTY
    # Footer, meta.truncated and rendered rows are the API pass's.
    assert out.response.meta["truncated"] is False and out.rendered_rows == 0


async def test_both_passes_empty_on_a_union_keeps_its_own_empty_literal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    alpha = dataclasses.replace(
        solo(RecordingDocsSearch(), RecordingApiSearch()), project=make_service("alpha").project
    )
    beta = dataclasses.replace(
        solo(RecordingDocsSearch(), RecordingApiSearch()), project=make_service("beta").project
    )
    for f in (False, True):
        out = await serve(router(alpha, beta, suggestions=flags(f)), api_request(), caplog)
        tail = F_EMPTY if f else RETRY
        assert out.response.text == f"{HEADER}\n\nNo matches found.\n{POINTER}\n\n{tail}\n"
        # The union renders no row listing: the count stays undefined either way.
        assert out.rendered_rows is None and out.response.items == ()


# ── F on, X: the fallback pass raised ─────────────────────────────────────


async def test_a_failed_fallback_raises_the_typed_error_and_logs_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from pydocs_mcp.application.mcp_errors import ServiceUnavailableError

    original = RuntimeError("embedder endpoint down")
    docs, api = RaisingDocsSearch(original), RecordingApiSearch()
    tool = router(solo(docs, api), suggestions=flags(True))
    caplog.clear()
    with caplog.at_level("INFO", logger="pydocs_mcp.application.suggestions"):
        with pytest.raises(ServiceUnavailableError) as caught:
            await tool.search_codebase(api_request())
    assert str(caught.value) == (
        'search_codebase(kind="api") found no symbols; its kind="any" fallback failed: '
        "RuntimeError: embedder endpoint down"
    )
    assert caught.value.__cause__ is original
    assert api.queries[0].terms == QUERY and len(docs.queries) == 1
    assert [r for r in caplog.records if r.name == "pydocs_mcp.application.suggestions"] == []


async def test_a_cancelled_fallback_propagates_unwrapped() -> None:
    docs = RaisingDocsSearch(asyncio.CancelledError())
    tool = router(solo(docs, RecordingApiSearch()), suggestions=flags(True))
    with pytest.raises(asyncio.CancelledError):
        await tool.search_codebase(api_request())
    assert len(docs.queries) == 1


# ── F on, H: the API pass returned rows — byte-identical to F off ─────────


async def _f_on_and_off(make_tool, payload, caplog, **kw) -> tuple[Served, Served]:
    return (
        await serve(make_tool(flags_on=True), payload, caplog, **kw),
        await serve(make_tool(flags_on=False), payload, caplog, **kw),
    )


def _same_response(on: Served, off: Served) -> None:
    assert on.response.text == off.response.text
    assert on.response.items == off.response.items
    assert on.response.meta == off.response.meta
    assert (on.rules, on.rendered_rows) == (off.rules, off.rendered_rows)


@pytest.mark.parametrize("budget", [None, 1], ids=["rendered", "budget-corner"])
@pytest.mark.parametrize(("z", "r"), ZR, ids=ZR_IDS)
@pytest.mark.parametrize("limit", [10, 5000], ids=["limit", "limit-clamped"])
async def test_an_api_hit_is_byte_identical_with_the_fallback_on(
    z: bool, r: bool, budget: int | None, limit: int, caplog: pytest.LogCaptureFixture
) -> None:
    docs = RecordingDocsSearch((CHUNK_ROW,))

    def make_tool(flags_on: bool) -> ToolRouter:
        services = solo(docs, RecordingApiSearch((MEMBER_ROW,)))
        return router(services, suggestions=flags(flags_on, z, r), budget=budget)

    on, off = await _f_on_and_off(make_tool, api_request(limit=limit), caplog)
    _same_response(on, off)
    assert docs.queries == [] and F_RULE not in on.rules
    assert [item["kind"] for item in on.response.items] == ["member"]
    footer = on.response.text.split("[truncated: ", 1)[-1]
    assert (limit == 5000) is (footer.count("limit=5000 above the configured maximum") == 1)
    if budget == 1:
        # SC1: rows elided from the text — today's empty literal and Z/R lines show.
        assert "No symbols found." in on.response.text and "elided by the token budget" in footer
        assert (RETRY in on.response.text) is r and on.rendered_rows == 0
        assert on.response.meta["truncated"] is True


# ── Ledger: only the served pass's ledger reaches the response (P1c) ──────


async def test_a_clamped_limit_on_a_served_fallback_prints_one_clamp_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tool = router(
        solo(RecordingDocsSearch((CHUNK_ROW,)), RecordingApiSearch()), suggestions=flags(True)
    )
    out = await serve(tool, api_request(limit=5000), caplog)
    direct = await serve(tool, direct_any(api_request(limit=5000)), caplog)
    assert out.response.text.count("limit=5000 above the configured maximum") == 1
    assert "[truncated: 1 section — recovery pointers inline]" in out.response.text
    assert out.response.meta["truncated"] is True
    assert out.rendered_rows == direct.rendered_rows == 1
    assert without_label(out.response.text) == direct.response.text


async def test_a_clamped_limit_on_an_empty_fallback_keeps_the_api_pass_ledger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def make_tool(flags_on: bool) -> ToolRouter:
        return router(
            solo(RecordingDocsSearch(), RecordingApiSearch()), suggestions=flags(flags_on)
        )

    on, off = await _f_on_and_off(make_tool, api_request(limit=5000), caplog)
    footer_on = on.response.text.split("\n\n[truncated: ", 1)[1]
    assert footer_on == off.response.text.split("\n\n[truncated: ", 1)[1]
    assert footer_on.count("limit=5000 above the configured maximum") == 1
    assert on.response.meta["truncated"] is off.response.meta["truncated"] is True
    assert on.rendered_rows == off.rendered_rows == 0


# ── Other kinds: F never fires, no extra query ────────────────────────────


class FixedDecisionSearch:
    """A decision layer answering every search with one fixed empty body."""

    async def search_with_items(self, query: str, **scope: object):
        return "No matches found.", (), {}


@pytest.mark.parametrize("rows", [(), (CHUNK_ROW,)], ids=["zero-hit", "hit"])
@pytest.mark.parametrize("kind", ["any", "docs", "decision"])
async def test_other_kinds_are_untouched_by_the_fallback_flag(
    kind: str, rows: tuple, caplog: pytest.LogCaptureFixture
) -> None:
    docs, api = RecordingDocsSearch(rows), RecordingApiSearch()

    def make_tool(flags_on: bool) -> ToolRouter:
        services = solo(docs, api, decisions=FixedDecisionSearch())
        return router(services, suggestions=flags(flags_on))

    on, off = await _f_on_and_off(make_tool, SearchInput(query=QUERY, kind=kind), caplog)
    _same_response(on, off)
    assert F_RULE not in on.rules
    per_call = {"any": (1, 1), "docs": (1, 0), "decision": (0, 0)}[kind]
    assert (len(docs.queries), len(api.queries)) == tuple(2 * n for n in per_call)


# ── Branch suggestion interplay and the every-flag-off invariant ──────────


def _checkout_not_indexed_directory() -> FakeBranchDirectory:
    row = BranchRecord("main", "a" * 40, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0,
                       is_default=True)  # fmt: skip
    return FakeBranchDirectory(BranchSnapshot((row,), "main", "feature/y", {}))


@pytest.mark.parametrize(
    ("docs_rows", "api_rows", "expected"),
    [((CHUNK_ROW,), (), F_SERVED), ((), (), F_EMPTY), ((), (MEMBER_ROW,), None)],
    ids=["S", "E", "H"],
)
async def test_the_fallback_label_beats_the_checkout_suggestion(
    docs_rows: tuple, api_rows: tuple, expected: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    def make_tool(flags_on: bool) -> ToolRouter:
        services = solo(
            RecordingDocsSearch(docs_rows),
            RecordingApiSearch(api_rows),
            branch_directory=_checkout_not_indexed_directory(),
        )
        return router(services, suggestions=flags(flags_on))

    on, off = await _f_on_and_off(make_tool, api_request(), caplog)
    if expected is None:  # H: today's behaviour, the checkout suggestion included
        _same_response(on, off)
        assert on.rules == ["checkout_not_indexed"]
        assert "is not indexed" in on.response.meta["suggestion"]
        return
    assert on.response.meta["suggestion"] == expected
    assert "checkout_not_indexed" not in on.rules and on.rules[-1] == F_RULE


@pytest.mark.parametrize(
    ("docs_rows", "api_rows"), [((CHUNK_ROW,), ()), ((), ()), ((), (MEMBER_ROW,))]
)
async def test_every_flag_off_leaves_meta_suggestion_null(
    docs_rows: tuple, api_rows: tuple, caplog: pytest.LogCaptureFixture
) -> None:
    services = solo(
        RecordingDocsSearch(docs_rows),
        RecordingApiSearch(api_rows),
        branch_directory=_checkout_not_indexed_directory(),
    )
    every_off = SuggestionsConfig(**dict.fromkeys(SuggestionsConfig.model_fields, False))
    out = await serve(router(services, suggestions=every_off), api_request(), caplog)
    assert out.response.meta.get("suggestion") is None and out.rules == []
    assert "[suggestion:" not in out.response.text
