"""Errors and empty results stay enveloped and carry a next step (spec §D1)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.mcp_inputs import LookupInput, SearchInput, SymbolInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
)
from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.null_services import NullDecisionService
from pydocs_mcp.application.suggestions import (
    API_ZERO_HIT_RETRY_ANY_RULE,
    SEARCH_ZERO_HIT_SUGGESTION,
    api_zero_hit_retry_any_suggestion,
)
from pydocs_mcp.application.target_resolution import NullTargetResolver
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.retrieval.config import SuggestionsConfig
from pydocs_mcp.multirepo import LoadedProject
from pydocs_mcp.storage.index_metadata import IndexMetadata

from ._router_fakes import (
    FakeApi,
    FakeDocs,
    FakeOverview,
    FakeSymbolSource,
    make_envelope,
    make_services,
)


class _RaisingLookup:
    """A lookup that always reports its target missing — drives the
    multi-project ``_lookup_body`` final NotFound raise (the one that must
    carry a search pointer)."""

    async def lookup(self, payload: LookupInput) -> str:
        raise NotFoundError(f"'{payload.target}' not indexed here")

    async def lookup_with_items(self, payload: LookupInput) -> str:
        return await self.lookup(payload)  # always raises before returning

    # Multi-project pass 1 / pass 2 seams (spec 2026-09-10 §2.5): the Null
    # resolver never rewrites, so the final raise keeps its exact message.
    target_resolver = NullTargetResolver()

    async def lookup_exact(self, payload: LookupInput) -> str:
        return await self.lookup(payload)

    async def lookup_rewritten(self, payload: LookupInput, rewrite: object) -> str:
        return await self.lookup(payload)


def _project(name: str, indexed_at: float) -> LoadedProject:
    meta = IndexMetadata(
        project_name=name,
        project_root="",
        embedding_provider="fastembed",
        embedding_model="bge",
        embedding_dim=384,
        pipeline_hash="h",
        indexed_at=indexed_at,
    )
    return LoadedProject(name=name, db_path=Path(f"/x/{name}.db"), metadata=meta)


def _raising_services() -> tuple[ProjectServices, ...]:
    # Two loaded projects, both missing the target -> _lookup_body exhausts the
    # recency-ordered loop and hits its final raise.
    return tuple(
        ProjectServices(
            project=_project(name, indexed_at),
            docs=FakeDocs(),
            api=FakeApi(),
            lookup=_RaisingLookup(),
            symbol_source=FakeSymbolSource(),
            overview=FakeOverview(),
            decisions=NullDecisionService(),
        )
        for name, indexed_at in (("a", 1.0), ("b", 2.0))
    )


class _EmptyDocs:
    async def search(self, query):
        from pydocs_mcp.models import ChunkList, SearchResponse

        return SearchResponse(result=ChunkList(items=()), query=query, duration_ms=0.0)

    async def ranked(self, query):
        from pydocs_mcp.models import ChunkList

        return ChunkList(items=())


class _EmptyApi:
    async def search(self, query):
        from pydocs_mcp.models import ModuleMemberList, SearchResponse

        return SearchResponse(result=ModuleMemberList(items=()), query=query, duration_ms=0.0)

    async def ranked(self, query):
        from pydocs_mcp.models import ModuleMemberList

        return ModuleMemberList(items=())


def _empty_search_router(suggestions: SuggestionsConfig | None = None) -> ToolRouter:
    """A single-project ToolRouter whose docs/api services return no hits, so
    ``search_codebase`` renders the empty-result body."""
    base = make_services()[0]
    services = (
        ProjectServices(
            project=base.project,
            docs=_EmptyDocs(),
            api=_EmptyApi(),
            lookup=base.lookup,
            symbol_source=base.symbol_source,
            overview=base.overview,
            decisions=base.decisions,
        ),
    )
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
        suggestions=suggestions or SuggestionsConfig(),
    )


def _empty_search_router_fallback_off(**flags: bool) -> ToolRouter:
    """:func:`_empty_search_router` with the ADR 0027 zero-hit fallback OFF —
    the #438 ``kind="api"`` zero-hit pins below are the flag-off proof (every
    response byte-identical to 9b9de5a3), so they name the flag explicitly."""
    return _empty_search_router(SuggestionsConfig(api_zero_hit_fallback_any=False, **flags))


def _missing_target_router(envelope: ResponseEnvelope) -> ToolRouter:
    """A router over projects that all miss every target, on ``envelope``."""
    services = _raising_services()
    return ToolRouter(
        services=services,
        envelope=envelope,
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


def _symbol_miss(envelope: ResponseEnvelope, target: str = "pkg.mod.Missing") -> NotFoundError:
    """Drive get_symbol at a target no project holds; return the raised error."""
    with pytest.raises(NotFoundError) as excinfo:
        asyncio.run(_missing_target_router(envelope).get_symbol(SymbolInput(target=target)))
    return excinfo.value


def test_unknown_target_error_renders_the_mcp_call_form() -> None:
    # An error message's pointer must be a call the agent can issue verbatim,
    # exactly like a returned body's — the raise unwinds through the envelope,
    # which resolves it on the way out instead of leaking the raw token.
    assert str(_symbol_miss(make_envelope("mcp"))) == (
        "'pkg.mod.Missing' not found in any loaded project. → search_codebase(query=\"Missing\")"
    )


def test_unknown_target_error_renders_the_cli_call_form() -> None:
    assert str(_symbol_miss(make_envelope("cli"))) == (
        "'pkg.mod.Missing' not found in any loaded project. → pydocs-mcp search \"Missing\""
    )


def test_unknown_target_error_strips_its_pointer_when_pointers_are_disabled() -> None:
    # Same body-side contract as ResponseEnvelope.wrap: a deployment with
    # pointers off must not see "-> search_codebase(...)" syntax anywhere.
    message = str(_symbol_miss(make_envelope("mcp", pointers_enabled=False)))
    assert message == "'pkg.mod.Missing' not found in any loaded project."


def test_resolved_error_keeps_the_error_envelope_shape() -> None:
    # The wire shape is the exception CLASS (server.py maps MCPToolError
    # subclasses to JSON-RPC errors) — resolution rewrites the message only.
    error = _symbol_miss(make_envelope("mcp"))
    assert type(error) is NotFoundError
    assert "[[next:" not in str(error)


def test_error_without_a_pointer_is_left_byte_identical() -> None:
    # Single project -> the per-project miss, which carries no pointer token.
    # Nothing in that message may move when the resolution pass runs over it.
    services = _raising_services()[:1]
    router = ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )
    with pytest.raises(NotFoundError) as excinfo:
        asyncio.run(router.get_symbol(SymbolInput(target="pkg.mod.Missing")))
    assert str(excinfo.value) == "'pkg.mod.Missing' not indexed here"


def test_zero_hit_search_points_at_overview() -> None:
    # Fake docs/api services returning empty; assert the enveloped output
    # contains the resolved get_overview() pointer (surface="mcp").
    out = asyncio.run(
        _empty_search_router().search_codebase(SearchInput(query="nothing here"))
    ).text
    assert "→ get_overview()" in out
    # And the raw token must not leak through the envelope.
    assert "[[next:" not in out


def test_zero_hit_search_meta_carries_suggestion() -> None:
    # ADR 0007: the fired rule is machine-readable as meta.suggestion so
    # transcript analysis can attribute the nudge to machinery (R7).
    resp = asyncio.run(_empty_search_router().search_codebase(SearchInput(query="nothing here")))
    assert resp.meta["suggestion"] == SEARCH_ZERO_HIT_SUGGESTION


def test_zero_hit_search_flag_off_strips_pointer_and_suggestion() -> None:
    # search_zero_hit off ⇒ the bare empty-result body (the pre-pointer
    # bytes) and no suggestion key anywhere in meta.
    on = asyncio.run(_empty_search_router().search_codebase(SearchInput(query="nothing here")))
    off = asyncio.run(
        _empty_search_router(SuggestionsConfig(search_zero_hit=False)).search_codebase(
            SearchInput(query="nothing here")
        )
    )
    assert "get_overview" not in off.text
    assert "suggestion" not in off.meta
    # The default-on response differs from flag-off ONLY by the pointer line
    # (search_zero_hit is behavior-preserving by construction, ADR 0007).
    assert on.text.replace("\nTogether: → get_overview()\n", "\n") == off.text


def test_zero_hit_search_fired_rule_emits_structured_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO", logger="pydocs_mcp.application.suggestions"):
        asyncio.run(_empty_search_router().search_codebase(SearchInput(query="nothing here")))
    events = [json.loads(r.message) for r in caplog.records]
    assert {
        "event": "suggestion_fired",
        "tool": "search_codebase",
        "rule": "search_zero_hit",
    } in events


# ── KAF-02: kind="api" zero-hit retry hint ────────────────────────────────
# A kind="api" zero hit leaves the agent stranded with "No symbols found."
# Those four peer tests mirror the kind="any" set above (AC-5: originals
# untouched) and pin the actionable retry hint the new rule emits (AC-2,
# AC-3, AC-4, AC-6). The next two blocks (AC-7, AC-8) extend the suite
# with the external-boundary contract on selector preservation and safe
# query rendering. The witness query is the spec's minimised two-word
# fixture — ``"nested serialization"``.
_WITNESS_QUERY = "nested serialization"


def test_api_zero_hit_offers_retry_kind_any() -> None:
    # Defaults (both flags on). Empty API returns "No symbols found."; the
    # router carries the existing overview pointer line AND the new retry
    # line at kind="any" with the preserved query VALUE (AC-2).
    resp = asyncio.run(
        _empty_search_router_fallback_off().search_codebase(
            SearchInput(query=_WITNESS_QUERY, kind="api")
        )
    )
    assert "No symbols found." in resp.text
    assert "→ get_overview()" in resp.text
    assert len(resp.items) == 0
    # One suggestion line names the retry at kind="any" and interpolates the
    # verbatim query. Asserted as a substring of the retry body — pinning the
    # whole line would couple to the renderer's exact copy.
    suggestion_lines = [line for line in resp.text.splitlines() if line.startswith("[suggestion:")]
    assert len(suggestion_lines) == 1
    assert 'kind="any"' in suggestion_lines[0]
    assert f'query="{_WITNESS_QUERY}"' in suggestion_lines[0]


def test_api_zero_hit_meta_carries_api_retry_suggestion() -> None:
    # AC-2: meta.suggestion carries the new kind-aware rule's text (single-
    # string precedence: the new rule replaces the generic search_zero_hit
    # string in meta.suggestion, while the overview pointer line in text stays).
    resp = asyncio.run(
        _empty_search_router_fallback_off().search_codebase(
            SearchInput(query=_WITNESS_QUERY, kind="api")
        )
    )
    suggestion = resp.meta["suggestion"]
    assert suggestion.startswith("[suggestion:")
    assert 'kind="any"' in suggestion
    assert f'query="{_WITNESS_QUERY}"' in suggestion
    assert suggestion != SEARCH_ZERO_HIT_SUGGESTION


def test_api_zero_hit_flag_off_strips_retry_hint() -> None:
    # AC-3: new flag off AND search_zero_hit on ⇒ byte-identical to today's
    # zero-hit body (overview pointer + generic suggestion). The default-on
    # response differs from flag-off ONLY by the new retry line and the meta
    # suggestion text (behaviour-preserving by construction, ADR 0007). OFF
    # carries the legacy response bytes; ON = OFF + "\n" + retry_line + "\n".
    on = asyncio.run(
        _empty_search_router_fallback_off().search_codebase(
            SearchInput(query=_WITNESS_QUERY, kind="api")
        )
    )
    off = asyncio.run(
        _empty_search_router_fallback_off(api_zero_hit_retry_any=False).search_codebase(
            SearchInput(query=_WITNESS_QUERY, kind="api")
        )
    )
    assert off.meta["suggestion"] == SEARCH_ZERO_HIT_SUGGESTION
    assert "No symbols found." in off.text
    assert "→ get_overview()" in off.text
    # No retry line reaches the OFF body.
    assert not any(line.startswith("[suggestion:") for line in off.text.splitlines())
    # The ON body is the OFF body plus one retry line (blank-line separated).
    retry_line = next(line for line in on.text.splitlines() if line.startswith("[suggestion:"))
    assert on.text == f"{off.text}\n{retry_line}\n"


def test_api_zero_hit_both_flags_off_bare_body() -> None:
    # AC-4: both flags off ⇒ bare empty-result body + no suggestion key
    # (byte-identical to today's double-off zero-hit).
    resp = asyncio.run(
        _empty_search_router_fallback_off(
            search_zero_hit=False, api_zero_hit_retry_any=False
        ).search_codebase(SearchInput(query=_WITNESS_QUERY, kind="api"))
    )
    assert "get_overview" not in resp.text
    assert "[suggestion:" not in resp.text
    assert "suggestion" not in resp.meta


def test_api_zero_hit_fired_rule_emits_structured_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # AC-6: the new rule logs one structured event per fire; when the generic
    # rule also fires its own event coexists (meta carries exactly one rule's
    # text — AC-2 above pins the precedence). No raw query reaches the event.
    with caplog.at_level("INFO", logger="pydocs_mcp.application.suggestions"):
        asyncio.run(
            _empty_search_router_fallback_off().search_codebase(
                SearchInput(query=_WITNESS_QUERY, kind="api")
            )
        )
    events = [json.loads(r.message) for r in caplog.records]
    assert {
        "event": "suggestion_fired",
        "tool": "search_codebase",
        "rule": API_ZERO_HIT_RETRY_ANY_RULE,
    } in events
    assert {
        "event": "suggestion_fired",
        "tool": "search_codebase",
        "rule": "search_zero_hit",
    } in events
    # No raw query text appears in event name / level / indexing keys.
    for event in events:
        assert _WITNESS_QUERY not in event["event"]
        assert _WITNESS_QUERY not in event["rule"]
        assert _WITNESS_QUERY not in event["tool"]


# ── AC-7: selector preservation ─────────────────────────────────────────
# Non-default scope / package / project selectors must appear in the retry
# call verbatim; defaults are omitted (concise rendering). (branch rides on
# payload via BranchSelectedInput — the #315 field is not on SearchInput yet.)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"scope": "deps"}, 'scope="deps"'),
        ({"scope": "project"}, 'scope="project"'),
        ({"package": "fastapi"}, 'package="fastapi"'),
        # The _empty_search_router loads one project named "solo"; passing it
        # as the project selector is non-default (SearchInput.project default
        # is "") and pins verbatim preservation through the live project seam.
        ({"project": "solo"}, 'project="solo"'),
    ],
)
def test_api_zero_hit_preserves_non_default_selectors(
    overrides: dict[str, str], expected: str
) -> None:
    payload = SearchInput(query=_WITNESS_QUERY, kind="api", **overrides)
    resp = asyncio.run(_empty_search_router_fallback_off().search_codebase(payload))
    assert expected in resp.meta["suggestion"]
    # And the body carries the same selector in the rendered retry line.
    retry_line = next(line for line in resp.text.splitlines() if line.startswith("[suggestion:"))
    assert expected in retry_line


def test_api_zero_hit_omits_default_selectors() -> None:
    # Default selectors (scope="all", package="", project="") are omitted so
    # the retry rendering stays concise (spec §"Safe rendering"): omitting a
    # NON-default would silently widen, but omitting defaults never widens.
    resp = asyncio.run(
        _empty_search_router_fallback_off().search_codebase(
            SearchInput(query=_WITNESS_QUERY, kind="api")
        )
    )
    suggestion = resp.meta["suggestion"]
    assert "scope=" not in suggestion
    assert "package=" not in suggestion
    assert "project=" not in suggestion
    assert "branch=" not in suggestion


def test_api_zero_hit_renderer_preserves_non_default_branch() -> None:
    # The branch selector flows into the renderer via _branch_selector(payload)
    # on the router (#315 will declare the field on SearchInput; until then the
    # router reads it through getattr). The renderer pins verbatim preservation
    # here — the router integration for branch is a trivial pass-through, and
    # exercising it through the fakes router needs a stamped branch directory
    # that the Null default does not supply; the contract check belongs to the
    # renderer, which is what AC-7 pins ("appear verbatim in the retry call").
    from pydocs_mcp.application.suggestions import api_zero_hit_retry_any_suggestion

    rendered = api_zero_hit_retry_any_suggestion(_WITNESS_QUERY, branch="feature-x")
    assert 'branch="feature-x"' in rendered
    # Default branch "" stays omitted (concise rendering).
    rendered_default = api_zero_hit_retry_any_suggestion(_WITNESS_QUERY)
    assert "branch=" not in rendered_default


# ── AC-8: safe rendering of the retry call (external boundary) ───────────
# Representative escaping cases pin the external-boundary contract:
# embedded double quotes, backslashes, newlines / tabs and non-ASCII
# characters round-trip to the same string VALUE when the embedded call is
# parsed. The test pins the round-trip property, not the chosen convention
# (JSON-escaped RFC 8259 string literal is acceptable per spec §"Safe
# rendering of the retry call").
def _parse_rendered_query(suggestion: str) -> str:
    """Extract the ``query=<JSON string>`` value with the canonical scanner.

    Uses ``json.decoder.py_scanstring`` instead of a regex so backslash-
    containing values (``has a \\ backslash``) parse correctly — a plain
    double-quote-delimited regex would over-consume the escape sequences.
    """
    from json.decoder import py_scanstring

    needle = 'query="'
    start = suggestion.index(needle) + len(needle)
    value, _end = py_scanstring(suggestion, start)
    return value


@pytest.mark.parametrize(
    "query",
    [
        'has a " quote',
        "has a \\ backslash",
        "has a \n newline",
        "has a \t tab",
        "has a \u0000 null",
        "combining mark é̀",
        "non-Latin 日本語",
        "RTL العربية",
        "zero-width‍joiner",
    ],
    ids=[
        "double_quote",
        "backslash",
        "newline",
        "tab",
        "null_byte",
        "combining_mark",
        "non_latin",
        "rtl",
        "zero_width_joiner",
    ],
)
def test_api_zero_hit_query_value_round_trips(query: str) -> None:
    resp = asyncio.run(
        _empty_search_router_fallback_off().search_codebase(SearchInput(query=query, kind="api"))
    )
    # The embedded call parses back to the same query string VALUE. Any
    # substitution / escape convention is allowed (spec); the pin is the
    # round-trip.
    assert _parse_rendered_query(resp.meta["suggestion"]) == query
    # Same value reaches the body, same round-trip.
    retry_line = next(line for line in resp.text.splitlines() if line.startswith("[suggestion:"))
    assert _parse_rendered_query(retry_line) == query


def _parse_named_arg(rendered: str, field_name: str) -> str:
    """Parse ``field_name="<escaped>"`` out of ``rendered`` via one JSON scan.

    The suggestion line reads ``name=<JSON string>, name=<JSON string>, …``.
    ``json.decoder.py_scanstring`` walks a valid JSON string from an opening
    double-quote and returns both the parsed value and the end offset — the
    canonical RFC 8259 parser, so matching backslash escapes stays correct
    even when the value itself contains backslashes / quotes.
    """
    from json.decoder import py_scanstring

    needle = f'{field_name}="'
    start = rendered.index(needle) + len(needle)
    value, _end = py_scanstring(rendered, start)
    return value


def test_api_zero_hit_renderer_round_trips_through_json() -> None:
    # Direct unit on the renderer — pins the escaping convention is parseable
    # standalone, not only through the router.
    rendered = api_zero_hit_retry_any_suggestion(
        'name with "quotes" and \\ and \n newline',
        scope="deps",
        package="pkg\\sub",
        project="p",
        branch='b"1',
    )
    assert _parse_named_arg(rendered, "query") == 'name with "quotes" and \\ and \n newline'
    assert _parse_named_arg(rendered, "scope") == "deps"
    assert _parse_named_arg(rendered, "package") == "pkg\\sub"
    assert _parse_named_arg(rendered, "project") == "p"
    assert _parse_named_arg(rendered, "branch") == 'b"1'


# ── Non-api zero-hits must NOT fire the new rule ────────────────────────


@pytest.mark.parametrize("kind", ["any", "docs"])
def test_non_api_zero_hits_do_not_fire_the_new_rule(
    caplog: pytest.LogCaptureFixture, kind: str
) -> None:
    # The new rule is kind="api"-only. kind="any" and kind="docs" zero hits
    # must stay on the generic pointer only — their meta carries the generic
    # suggestion and no new rule event fires.
    with caplog.at_level("INFO", logger="pydocs_mcp.application.suggestions"):
        resp = asyncio.run(
            _empty_search_router().search_codebase(SearchInput(query="nothing here", kind=kind))
        )
    assert resp.meta["suggestion"] == SEARCH_ZERO_HIT_SUGGESTION
    events = [json.loads(r.message) for r in caplog.records]
    for event in events:
        assert event["rule"] != API_ZERO_HIT_RETRY_ANY_RULE


# ── KAF-04 (R3): the renderer's scope omit-when-default guard is pinned to
# ``SearchInput.scope``'s canonical default via a single-source constant, so
# a future accidental literal (or default drift) regresses loudly.


def test_api_zero_hit_renderer_scope_default_is_search_input_default() -> None:
    # The renderer's omit-when-default guard reads ``_SEARCH_SCOPE_DEFAULT``,
    # which derives from ``SearchInput.model_fields["scope"].default`` (direct
    # pydantic-v2 lookup — the precedent in ``tests/test_cli_mcp_parity.py``
    # and ``application/tool_response.py``). Pin the derivation so a future
    # edit either moves both halves together or fails this test.
    from pydocs_mcp.application.suggestions import (
        _SEARCH_SCOPE_DEFAULT,
        api_zero_hit_retry_any_suggestion,
    )

    assert SearchInput.model_fields["scope"].default == _SEARCH_SCOPE_DEFAULT
    # Renderer omits scope when it matches the canonical default.
    omitted = api_zero_hit_retry_any_suggestion("q", scope=_SEARCH_SCOPE_DEFAULT)
    assert "scope=" not in omitted
    # Renderer emits scope when it does not.
    emitted = api_zero_hit_retry_any_suggestion("q", scope="deps")
    assert 'scope="deps"' in emitted


# ── ADR 0027 twins: the same kind="api" zero hits with the fallback ON ─────
# The router above serves no row on either kind, so every twin is outcome E:
# today's zero-hit body with the retry line's text replaced by the fixed
# "both empty" label — byte-constant, so no query or selector reaches it.
_BOTH_EMPTY = (
    '[suggestion: kind="api" matched nothing, and kind="any" found nothing '
    "for the same query and filters either]"
)


@pytest.mark.parametrize(
    ("generic", "retry"), [(True, True), (True, False), (False, True), (False, False)]
)
def test_api_zero_hit_with_fallback_on_ends_with_the_both_empty_label(
    caplog: pytest.LogCaptureFixture, generic: bool, retry: bool
) -> None:
    flags = SuggestionsConfig(search_zero_hit=generic, api_zero_hit_retry_any=retry)
    with caplog.at_level("INFO", logger="pydocs_mcp.application.suggestions"):
        resp = asyncio.run(
            _empty_search_router(flags).search_codebase(
                SearchInput(query=_WITNESS_QUERY, kind="api")
            )
        )
    pointer = "Together: → get_overview()\n\n" if generic else ""
    assert resp.text == f"No symbols found.\n{pointer}{_BOTH_EMPTY}\n"
    assert resp.meta["suggestion"] == _BOTH_EMPTY and len(resp.items) == 0
    rules = [json.loads(r.message)["rule"] for r in caplog.records]
    assert rules == ["search_zero_hit", "api_zero_hit_fallback_any"][(0 if generic else 1) :]


@pytest.mark.parametrize(
    "overrides",
    [{"scope": "deps"}, {"package": "fastapi"}, {"project": "solo"}, {"query": 'q " \\ \n ]'}],
)
def test_api_zero_hit_with_fallback_on_label_never_carries_request_text(
    overrides: dict[str, str],
) -> None:
    payload = SearchInput(**{"query": _WITNESS_QUERY, "kind": "api", **overrides})
    resp = asyncio.run(_empty_search_router().search_codebase(payload))
    assert resp.meta["suggestion"] == _BOTH_EMPTY
    assert resp.text.splitlines()[-1] == _BOTH_EMPTY
    assert "retry with" not in resp.text
