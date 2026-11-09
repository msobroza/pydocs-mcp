"""Pinned bytes for two ``kind="api"`` responses the fallback must leave alone or label (ADR 0027).

1. **A hit (the member lookup returned rows) equals the 9b9de5a3 bytes, with the
   fallback on AND off.** The matrix module compares the two flag states with
   each other, which a change made to both alike would pass. Here each is
   pinned to literals recorded from 9b9de5a3 on the same fixture: the rendered
   hit, and the budget corner, where the token budget renders no row and
   today's zero-hit lines still show over real rows.
2. **The served budget corner.** When the budget renders none of the fallback
   rows, the response is still the served one: the label, then the fallback
   pass's own text (its empty literal), then its elision footer. There is no
   overview pointer and no retry line, although a direct ``kind="any"`` gains
   the pointer there.
"""

from __future__ import annotations

import pytest

from .test_api_zero_hit_fallback_any import (
    CHUNK_ROW,
    F_RULE,
    F_SERVED,
    HEADER,
    MEMBER_ROW,
    POINTER,
    RETRY,
    TODAY_RULES,
    TODAY_SUGGESTION,
    TODAY_ZERO_HIT_BODY,
    ZR,
    ZR_IDS,
    api_request,
    direct_any,
    fallback_flags,
    serve_search,
    solo_service,
    zero_hit_router,
)
from ._router_fakes import RecordingApiSearch, RecordingDocsSearch

# ── Literals recorded from 9b9de5a3 on this fixture (one member row) ──────
MEMBER_HIT_BODY = (
    "**[__project__] pkg.mod.serialize(tree)** (function)\n"
    "Serialize a tree.\n"
    'Together: → get_symbol(target="pkg.mod.serialize") → '
    'get_references(target="pkg.mod.serialize", direction="callers")\n'
    'Then: → get_symbol(target="pkg.mod.serialize", depth="source")'
)
MEMBER_ELIDED_FOOTER = (
    "[truncated: 1 section — recovery pointers inline]\n"
    '- 1 result(s) elided by the token budget → get_symbol(target="pkg.mod.serialize")'
)
CHUNK_ELIDED_FOOTER = (
    "[truncated: 1 section — recovery pointers inline]\n"
    '- 1 result(s) elided by the token budget → get_symbol(target="pkg.mod.Tree.walk")'
)
MEMBER_ITEM = {
    "kind": "member",
    "id": "7",
    "qualified_name": "pkg.mod.serialize",
    "package": "__project__",
    "path": None,
    "start_line": None,
    "end_line": None,
    "score": 0.5,
}
FRESH_META = {
    "tool": "search_codebase",
    "project": "solo",
    "indexed_git_head": "8e2110e000000000000000000000000000000000",
    "live_git_head": "8e2110e000000000000000000000000000000000",
    "index_stale": False,
    "branch": None,
}


def _api_hit_router(fallback_on: bool, z: bool, r: bool, budget: int | None):
    services = solo_service(RecordingDocsSearch((CHUNK_ROW,)), RecordingApiSearch((MEMBER_ROW,)))
    return zero_hit_router(services, suggestions=fallback_flags(fallback_on, z, r), budget=budget)


@pytest.mark.parametrize("fallback_on", [False, True], ids=["fallback-off", "fallback-on"])
@pytest.mark.parametrize(("z", "r"), ZR, ids=ZR_IDS)
async def test_a_rendered_api_hit_is_the_9b9de5a3_bytes(
    fallback_on: bool, z: bool, r: bool, caplog: pytest.LogCaptureFixture
) -> None:
    tool = _api_hit_router(fallback_on, z, r, budget=None)
    out = await serve_search(tool, api_request(), caplog)
    assert out.response.text == f"{HEADER}\n\n{MEMBER_HIT_BODY}\n"
    assert out.response.items == (MEMBER_ITEM,)
    assert out.response.meta == {**FRESH_META, "truncated": False}
    assert (out.rules, out.rendered_rows) == ([], 1)


@pytest.mark.parametrize("fallback_on", [False, True], ids=["fallback-off", "fallback-on"])
@pytest.mark.parametrize(("z", "r"), ZR, ids=ZR_IDS)
async def test_an_api_hit_the_budget_renders_nothing_of_is_the_9b9de5a3_bytes(
    fallback_on: bool, z: bool, r: bool, caplog: pytest.LogCaptureFixture
) -> None:
    tool = _api_hit_router(fallback_on, z, r, budget=1)
    out = await serve_search(tool, api_request(), caplog)
    body = TODAY_ZERO_HIT_BODY[(z, r)]
    assert out.response.text == f"{HEADER}\n\n{body}\n\n{MEMBER_ELIDED_FOOTER}\n"
    assert out.response.items == (MEMBER_ITEM,)
    suggestion = TODAY_SUGGESTION.get((z, r))
    expected_meta = {**FRESH_META, "truncated": True}
    if suggestion is not None:
        expected_meta["suggestion"] = suggestion
    assert out.response.meta == expected_meta
    assert (out.rules, out.rendered_rows) == (TODAY_RULES[(z, r)], 0)


@pytest.mark.parametrize(("z", "r"), ZR, ids=ZR_IDS)
async def test_a_served_fallback_the_budget_renders_nothing_of_stays_labelled(
    z: bool, r: bool, caplog: pytest.LogCaptureFixture
) -> None:
    api = RecordingApiSearch()
    services = solo_service(RecordingDocsSearch((CHUNK_ROW,)), api)
    tool = zero_hit_router(services, suggestions=fallback_flags(True, z, r), budget=1)
    out = await serve_search(tool, api_request(), caplog)
    direct = await serve_search(tool, direct_any(api_request()), caplog)
    # Requested kind: the API pass ran first, on the request's own query.
    assert api.queries[0].terms == api_request().query
    # Raw API zero hit: the fallback rule fired, alone.
    assert out.rules == [F_RULE]
    # Served kind: the label, the fallback pass's empty literal, its elision footer.
    assert out.response.text == (
        f"{HEADER}\n\n{F_SERVED}\nNo matches found.\n\n{CHUNK_ELIDED_FOOTER}\n"
    )
    assert out.response.items == direct.response.items
    assert [item["kind"] for item in out.response.items] == ["chunk"]
    assert out.response.meta == {**FRESH_META, "truncated": True, "suggestion": F_SERVED}
    assert out.rendered_rows == 0
    # Not a zero hit: no pointer and no retry line, though a direct any shows the pointer.
    assert POINTER not in out.response.text and RETRY not in out.response.text
    assert (POINTER in direct.response.text) is z
