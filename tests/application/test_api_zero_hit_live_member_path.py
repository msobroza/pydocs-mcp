"""KAF-02 secondary seam — the minimised two-word witness through the live member path.

The primary tests in :mod:`tests.application.test_error_empty_contract` pin the
behaviour on a fakes-based router (``_empty_search_router``). This file wires a
real :class:`ApiSearch` over a populated SQLite member repository and serves
``search_codebase(query="nested serialization", kind="api")`` through the real
:class:`ToolRouter` — proving the two-word witness is still a zero-row result
*and* that the kind-aware retry hint fires with the exact query interpolated
(spec §Testing Decisions "Secondary seam"). The LIKE semantics are explicitly
unchanged by KAF-02 — this test is the witness, not a recall rescue.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.application.api_search import ApiSearch
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
)
from pydocs_mcp.application.suggestions import API_ZERO_HIT_RETRY_ANY_RULE
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import MemberKind, ModuleMember, ModuleMemberFilterField
from pydocs_mcp.retrieval.config import (
    AppConfig,
    SuggestionsConfig,
    build_member_pipeline_from_config,
)
from pydocs_mcp.retrieval.factories import build_retrieval_context
from pydocs_mcp.storage.factories import build_connection_provider
from pydocs_mcp.storage.sqlite import SqliteModuleMemberRepository

from tests._fakes import MockEmbedder
from tests.application._router_fakes import (
    FakeLookup,
    FakeOverview,
    FakeSymbolSource,
    make_envelope,
    make_project,
)
from pydocs_mcp.application.docs_search import DocsSearch
from pydocs_mcp.application.null_services import NullDecisionService
from pydocs_mcp.retrieval.config import build_chunk_pipeline_from_config

_WITNESS_QUERY = "nested serialization"


def _member(name: str, docstring: str) -> ModuleMember:
    return ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: "demo",
            ModuleMemberFilterField.MODULE.value: "demo.m",
            ModuleMemberFilterField.NAME.value: name,
            ModuleMemberFilterField.KIND.value: MemberKind.FUNCTION.value,
            "signature": "()",
            "return_annotation": "",
            "parameters": (),
            "docstring": docstring,
        },
    )


@pytest.fixture
async def witness_db(tmp_path: Path) -> Path:
    """Populate a SQLite member table where neither 'nested serialization' row
    contains both words — the minimised fixture of the trap (spec §3)."""
    db_path = tmp_path / "witness.db"
    open_index_database(db_path).close()
    provider = build_connection_provider(db_path)
    repo = SqliteModuleMemberRepository(provider=provider)
    await repo.upsert_many(
        [
            _member("process_rows", "Nested docs recovery."),
            _member("serialize_rows", "Schema-driven serialization."),
            _member("unrelated", "Nothing relevant."),
        ]
    )
    return db_path


def _live_services(db: Path) -> tuple[ProjectServices, ...]:
    """One project whose ``api`` is the production :class:`ApiSearch` wired to
    the live member pipeline — everything else is a fake because this seam only
    exercises the member-fetcher / API-search path."""
    config = AppConfig.load()
    context = build_retrieval_context(db, config, embedder=MockEmbedder())
    api = ApiSearch(member_pipeline=build_member_pipeline_from_config(config, context))
    docs = DocsSearch(chunk_pipeline=build_chunk_pipeline_from_config(config, context))
    services = (
        ProjectServices(
            project=make_project("demo"),
            docs=docs,
            api=api,
            lookup=FakeLookup(),
            symbol_source=FakeSymbolSource(),
            overview=FakeOverview(),
            decisions=NullDecisionService(),
        ),
    )
    return services


def _live_router(db: Path, suggestions: SuggestionsConfig | None = None) -> ToolRouter:
    services = _live_services(db)
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
        suggestions=suggestions or SuggestionsConfig(),
    )


# ADR 0027: the #438 witness below is the flag-off proof — fallback OFF, named.
_FALLBACK_OFF = SuggestionsConfig(api_zero_hit_fallback_any=False)


@pytest.mark.asyncio
async def test_two_word_witness_returns_zero_rows_and_fires_retry_hint(
    witness_db: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The live member path returns zero rows for the two-word witness, AND
    the tool router emits the kind-aware retry hint with the preserved query.

    This is the end-to-end witness of the trap and the fix:
    - LIKE '%nested serialization%' matches no row (engine unchanged, AC-14);
    - the response carries "No symbols found." + the overview pointer
      (unchanged) + one new [suggestion: line carrying the retry at
      kind="any" with the two-word query verbatim (AC-2).
    """
    router = _live_router(witness_db, _FALLBACK_OFF)
    with caplog.at_level("INFO", logger="pydocs_mcp.application.suggestions"):
        resp = await router.search_codebase(SearchInput(query=_WITNESS_QUERY, kind="api"))
    # LIKE semantics unchanged — zero rows, "No symbols found." body literal.
    assert len(resp.items) == 0
    assert "No symbols found." in resp.text
    # The kind-aware retry hint fires with the exact two-word query.
    suggestion_lines = [line for line in resp.text.splitlines() if line.startswith("[suggestion:")]
    assert len(suggestion_lines) == 1
    assert 'kind="any"' in suggestion_lines[0]
    assert f'query="{_WITNESS_QUERY}"' in suggestion_lines[0]
    # Structured log records the new fire (R7 attribution). Searchable at the
    # rule name — no raw query text in event name / level / indexing keys.
    import json as _json

    events = [_json.loads(r.message) for r in caplog.records]
    assert any(
        event
        == {
            "event": "suggestion_fired",
            "tool": "search_codebase",
            "rule": API_ZERO_HIT_RETRY_ANY_RULE,
        }
        for event in events
    )


@pytest.mark.asyncio
async def test_two_word_witness_individual_words_match(witness_db: Path) -> None:
    """Positive controls on the populated fixture (spec §3): ``nested`` and
    ``serialization`` each match ≥1 row by LIKE, proving the fixture is
    populated and the LIKE engine works — only the joined phrase is the trap.
    The retry hint fires for THIS test too (both single-word queries have
    hits? no — they match members, so kind="api" returns rows; NO zero hit
    and NO retry hint). The purpose here is to prove the fixture populates
    separately from the witness-is-zero claim."""
    router = _live_router(witness_db)
    nested = await router.search_codebase(SearchInput(query="nested", kind="api"))
    serialization = await router.search_codebase(SearchInput(query="serialization", kind="api"))
    assert len(nested.items) >= 1
    assert len(serialization.items) >= 1
    # Positive-control: at least one member row carries the single word, so
    # the LIKE engine is functioning — the two-word zero on the witness is a
    # phrase-boundary trap, not a dead engine.


_BOTH_EMPTY = (
    '[suggestion: kind="api" matched nothing, and kind="any" found nothing '
    "for the same query and filters either]"
)


@pytest.mark.asyncio
async def test_two_word_witness_with_fallback_on_reports_both_kinds_empty(
    witness_db: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """ADR 0027 twin, on the shipped config (fallback ON). The fixture holds
    member rows only — no chunk — so the live ``kind="any"`` fallback finds
    nothing either (outcome E, observed by running it): the zero-hit body
    keeps its literal and overview pointer and ends with the fixed
    both-empty label instead of the retry line."""
    import json as _json

    with caplog.at_level("INFO", logger="pydocs_mcp.application.suggestions"):
        resp = await _live_router(witness_db).search_codebase(
            SearchInput(query=_WITNESS_QUERY, kind="api")
        )
    assert len(resp.items) == 0
    assert resp.text == f"No symbols found.\nTogether: → get_overview()\n\n{_BOTH_EMPTY}\n"
    assert resp.meta["suggestion"] == _BOTH_EMPTY
    rules = [_json.loads(r.message)["rule"] for r in caplog.records]
    assert rules == ["search_zero_hit", "api_zero_hit_fallback_any"]
