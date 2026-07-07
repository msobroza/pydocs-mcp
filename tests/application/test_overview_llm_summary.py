"""Opt-in LLM architecture summary (§D17 block 2) — fingerprint cache + render.

Six concerns:
1. ``summary_fingerprint`` — sha256 of the SORTED module qnames (order-invariant).
2. ``generate_overview_summary`` regenerates ONLY when the fingerprint changes
   (fake LlmClient call-count assertions): same fingerprint → cache hit, zero
   LLM calls; changed fingerprint → one LLM call, fresh summary.
3. Malformed (blank) LLM reply → summary skipped, old cache kept (never raises).
4. JSON round-trip of :class:`OverviewSummary`.
5. Renderer golden — ``## Architecture`` block with the ``*generated*`` marker.
6. ``OverviewService`` renders block 2 from the injected ``aggregates_reader``.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.application.formatting import format_overview_card
from pydocs_mcp.application.overview_aggregates import (
    OverviewAggregates,
    OverviewSummary,
    generate_overview_summary,
    summary_fingerprint,
    summary_from_json,
    summary_to_json,
)
from pydocs_mcp.application.overview_service import OverviewCard

_NOW = 1_000_000_000.0
_MODULES = ("proj.api", "proj.core", "proj.storage")
_CENTRAL = ("proj.core.Engine", "proj.api.Router")
_SUMMARY_TEXT = "Proj is a layered app: api over core over storage."


# ── 1. Fingerprint ───────────────────────────────────────────────────────


def test_fingerprint_is_sha256_of_sorted_qnames() -> None:
    import hashlib

    expected = hashlib.sha256("\n".join(sorted(_MODULES)).encode("utf-8")).hexdigest()
    assert summary_fingerprint(_MODULES) == expected


def test_fingerprint_order_invariant() -> None:
    # Sorting before hashing means iteration order of the module map is irrelevant.
    assert summary_fingerprint(_MODULES) == summary_fingerprint(tuple(reversed(_MODULES)))


def test_fingerprint_changes_with_module_set() -> None:
    assert summary_fingerprint(_MODULES) != summary_fingerprint((*_MODULES, "proj.extra"))


# ── 2. Regeneration only on fingerprint change ───────────────────────────


async def test_generate_calls_llm_when_no_cache() -> None:
    from tests._fakes import FakeLlmClient

    client = FakeLlmClient(responses={"proj.core": _SUMMARY_TEXT})
    summary = await generate_overview_summary(
        module_qnames=_MODULES,
        central_symbols=_CENTRAL,
        llm_client=client,
        cached=None,
        now=_NOW,
    )
    assert summary is not None
    assert summary.text == _SUMMARY_TEXT
    assert summary.fingerprint == summary_fingerprint(_MODULES)
    assert summary.generated_at == _NOW
    assert len(client._calls) == 1


async def test_generate_skips_llm_when_fingerprint_matches() -> None:
    from tests._fakes import FakeLlmClient

    cached = OverviewSummary(
        text="old cached text",
        fingerprint=summary_fingerprint(_MODULES),
        generated_at=1.0,
    )
    client = FakeLlmClient(responses={"proj.core": _SUMMARY_TEXT})
    summary = await generate_overview_summary(
        module_qnames=_MODULES,
        central_symbols=_CENTRAL,
        llm_client=client,
        cached=cached,
        now=_NOW,
    )
    # Cache hit: the exact cached record is returned and NO LLM call is made.
    assert summary == cached
    assert client._calls == []


async def test_generate_regenerates_when_fingerprint_changes() -> None:
    from tests._fakes import FakeLlmClient

    stale = OverviewSummary(
        text="stale text",
        fingerprint=summary_fingerprint(("proj.only_old",)),
        generated_at=1.0,
    )
    client = FakeLlmClient(responses={"proj.core": _SUMMARY_TEXT})
    summary = await generate_overview_summary(
        module_qnames=_MODULES,
        central_symbols=_CENTRAL,
        llm_client=client,
        cached=stale,
        now=_NOW,
    )
    assert summary is not None
    assert summary.text == _SUMMARY_TEXT
    assert summary.fingerprint == summary_fingerprint(_MODULES)
    assert len(client._calls) == 1


# ── 3. Malformed reply → skip, keep old cache ────────────────────────────


async def test_blank_reply_keeps_old_cache(caplog: pytest.LogCaptureFixture) -> None:
    from tests._fakes import FakeLlmClient

    stale = OverviewSummary(
        text="previous good summary",
        fingerprint=summary_fingerprint(("proj.only_old",)),
        generated_at=1.0,
    )
    # A blank reply is malformed — the generator must not overwrite the cache.
    client = FakeLlmClient(responses={"proj.core": "   \n  "})
    with caplog.at_level("WARNING"):
        summary = await generate_overview_summary(
            module_qnames=_MODULES,
            central_symbols=_CENTRAL,
            llm_client=client,
            cached=stale,
            now=_NOW,
        )
    assert summary == stale  # old cache kept verbatim
    assert len(client._calls) == 1  # it did call, the reply was just unusable
    assert any("summary" in r.message.lower() for r in caplog.records)


async def test_blank_reply_no_cache_returns_none() -> None:
    from tests._fakes import FakeLlmClient

    client = FakeLlmClient(responses={"proj.core": ""})
    summary = await generate_overview_summary(
        module_qnames=_MODULES,
        central_symbols=_CENTRAL,
        llm_client=client,
        cached=None,
        now=_NOW,
    )
    assert summary is None


# ── 4. JSON round-trip ───────────────────────────────────────────────────


def test_summary_json_round_trip() -> None:
    summary = OverviewSummary(text=_SUMMARY_TEXT, fingerprint="deadbeef", generated_at=1.5)
    restored = summary_from_json(summary_to_json(summary))
    assert restored == summary


def test_summary_from_json_none_on_garbage() -> None:
    assert summary_from_json("not json") is None
    assert summary_from_json("") is None


# ── 5. Renderer golden ───────────────────────────────────────────────────


def _card_with_summary(summary: OverviewSummary | None) -> OverviewCard:
    return OverviewCard(
        package="__project__",
        package_count=1,
        module_count=3,
        symbol_count=5,
        doc_coverage=1.0,
        modules=(),
        entry_points=(),
        communities=(),
        dependency_profile=(),
        node_scores_available=True,
        overview_summary=summary,
    )


def test_architecture_block_renders_with_generated_marker() -> None:
    summary = OverviewSummary(text=_SUMMARY_TEXT, fingerprint="abc", generated_at=_NOW)
    out = format_overview_card(_card_with_summary(summary))
    assert "## Architecture" in out
    assert "*generated*" in out
    assert _SUMMARY_TEXT in out
    # Block 2 sits directly after the stats line, before the module map.
    assert out.index("## Architecture") < out.index("## Module map")


def test_architecture_block_omitted_when_absent() -> None:
    out = format_overview_card(_card_with_summary(None))
    assert "## Architecture" not in out


# ── 6. OverviewService renders block 2 from the injected reader ───────────


async def test_overview_service_renders_summary_from_reader() -> None:
    from tests._fakes import make_fake_uow_factory

    from pydocs_mcp.application.overview_service import OverviewService

    summary = OverviewSummary(text=_SUMMARY_TEXT, fingerprint="abc", generated_at=_NOW)

    def _reader() -> OverviewAggregates:
        return OverviewAggregates(summary=summary)

    svc = OverviewService(
        uow_factory=make_fake_uow_factory(),
        scripts={},
        aggregates_reader=_reader,
    )
    card = await svc.build()
    assert card.overview_summary == summary
    assert "## Architecture" in format_overview_card(card)


async def test_overview_service_summary_none_without_reader() -> None:
    from tests._fakes import make_fake_uow_factory

    from pydocs_mcp.application.overview_service import OverviewService

    svc = OverviewService(uow_factory=make_fake_uow_factory(), scripts={})
    card = await svc.build()
    assert card.overview_summary is None


# ── 7. Wiring: build_overview_aggregates_writer through a real DB ─────────
#
# git_activity is DISABLED in these configs so the writer exercises ONLY the
# block-2 LLM path — no ``read_git_log`` subprocess spawns in tests (plan
# convention: fakes only, no live LLM, no subprocess).

_PROJECT = "__project__"


def _writer_config(*, summary_enabled: bool) -> object:
    from pydocs_mcp.retrieval.config import AppConfig, OverviewConfig
    from pydocs_mcp.retrieval.config.models import GitActivityConfig, LlmSummaryConfig

    return AppConfig(
        overview=OverviewConfig(
            git_activity=GitActivityConfig(enabled=False),
            llm_summary=LlmSummaryConfig(enabled=summary_enabled),
        )
    )


def _module_node(qname: str):
    from pydocs_mcp.extraction.model import DocumentNode, NodeKind

    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=NodeKind.MODULE,
        source_path=qname.replace(".", "/") + ".py",
        start_line=1,
        end_line=10,
        text=f"{qname} module.",
        content_hash="h",
    )


def _score(qname: str, *, pagerank: float):
    from pydocs_mcp.storage.node_score import NodeScore

    return NodeScore(package=_PROJECT, qualified_name=qname, pagerank=pagerank)


async def _seed_module_stores(qnames: tuple[str, ...], *, central: tuple[str, ...] = ()):
    from tests._fakes import (
        InMemoryDocumentTreeStore,
        InMemoryNodeScoreStore,
        make_fake_uow_factory,
    )

    trees = InMemoryDocumentTreeStore()
    await trees.save_many([_module_node(q) for q in qnames], package=_PROJECT)
    scores = InMemoryNodeScoreStore()
    # Higher pagerank first → central[0] is the most-central symbol in the prompt.
    for rank, q in enumerate(central):
        await scores.upsert([_score(q, pagerank=float(len(central) - rank))])
    return make_fake_uow_factory(trees=trees, node_scores=scores)


def _read_column(db_path):
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.storage.index_metadata import read_overview_aggregates

    conn = open_index_database(db_path)
    try:
        return read_overview_aggregates(conn)
    finally:
        conn.close()


async def test_writer_enabled_writes_summary_and_caches(tmp_path) -> None:
    from tests._fakes import FakeLlmClient

    from pydocs_mcp.storage.factories import build_overview_aggregates_writer

    db_path = tmp_path / "proj.db"
    _read_column(db_path)  # create schema (open_index_database migrates)
    uow_factory = await _seed_module_stores(_MODULES, central=_CENTRAL)
    client = FakeLlmClient(responses={"proj.core": _SUMMARY_TEXT})
    writer = build_overview_aggregates_writer(
        _writer_config(summary_enabled=True),
        db_path,
        uow_factory=uow_factory,
        llm_client=client,
    )

    await writer(tmp_path)
    _activity_json, overview_json = _read_column(db_path)
    stored = summary_from_json(overview_json or "")
    assert stored is not None
    assert stored.text == _SUMMARY_TEXT
    assert stored.fingerprint == summary_fingerprint(_MODULES)
    assert len(client._calls) == 1

    # Second run, SAME module set → fingerprint cache hit: no new LLM call, the
    # stored column is unchanged (writer skips the re-write on a cache hit).
    await writer(tmp_path)
    assert len(client._calls) == 1
    assert _read_column(db_path)[1] == overview_json


async def test_writer_regenerates_when_modules_change(tmp_path) -> None:
    from tests._fakes import FakeLlmClient

    from pydocs_mcp.storage.factories import build_overview_aggregates_writer

    db_path = tmp_path / "proj.db"
    _read_column(db_path)
    client = FakeLlmClient(responses={"proj.core": _SUMMARY_TEXT})
    config = _writer_config(summary_enabled=True)

    writer1 = build_overview_aggregates_writer(
        config, db_path, uow_factory=await _seed_module_stores(_MODULES), llm_client=client
    )
    await writer1(tmp_path)
    assert len(client._calls) == 1

    # A new module appears → different fingerprint → one more LLM call, column updated.
    grown = (*_MODULES, "proj.extra")
    writer2 = build_overview_aggregates_writer(
        config, db_path, uow_factory=await _seed_module_stores(grown), llm_client=client
    )
    await writer2(tmp_path)
    assert len(client._calls) == 2
    stored = summary_from_json(_read_column(db_path)[1] or "")
    assert stored is not None
    assert stored.fingerprint == summary_fingerprint(grown)


async def test_writer_disabled_makes_no_call_and_no_write(tmp_path) -> None:
    from tests._fakes import FakeLlmClient

    from pydocs_mcp.storage.factories import build_overview_aggregates_writer

    db_path = tmp_path / "proj.db"
    _read_column(db_path)
    uow_factory = await _seed_module_stores(_MODULES, central=_CENTRAL)
    # A client that raises on ANY use — proves the disabled path never calls it.
    client = FakeLlmClient(responses={})
    writer = build_overview_aggregates_writer(
        _writer_config(summary_enabled=False),
        db_path,
        uow_factory=uow_factory,
        llm_client=client,
    )

    await writer(tmp_path)
    assert client._calls == []
    # Both features disabled → nothing written; the summary column stays NULL.
    assert _read_column(db_path) == (None, None)
