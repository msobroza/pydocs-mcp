"""meta.branch (spec §6.7, contract §2.4): declared, sourced from the probe, null-safe."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydocs_mcp.application.envelope import ResponseEnvelope, _assemble_meta
from pydocs_mcp.application.freshness import EnvelopeInfo, IndexFreshnessProbe
from pydocs_mcp.application.tool_response import MetaModel, ReferencesMetaModel, SuggestionMetaModel
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import NON_GIT_BRANCH_NAME
from pydocs_mcp.storage.factories import build_freshness_probe, build_sqlite_uow_factory
from pydocs_mcp.storage.index_metadata import IndexMetadata


def _info(branch: str | None) -> EnvelopeInfo:
    return EnvelopeInfo(
        indexed_commit="a" * 40,
        live_commit="a" * 40,
        age_days=0,
        package_count=1,
        stale=False,
        branch=branch,
    )


def test_meta_model_declares_branch_on_every_variant() -> None:
    for model in (MetaModel, ReferencesMetaModel, SuggestionMetaModel):
        assert "branch" in model.model_fields
        assert model.model_fields["branch"].default is None


def test_assemble_meta_carries_branch_and_degrades_to_null() -> None:
    with_info = _assemble_meta(
        tool="get_overview", project="p", info=_info("feature/x"), truncated=False, extras={}
    )
    assert with_info["branch"] == "feature/x"
    no_info = _assemble_meta(
        tool="get_overview", project="p", info=None, truncated=False, extras={}
    )
    assert no_info["branch"] is None
    assert set(no_info) == {
        "tool",
        "project",
        "indexed_git_head",
        "live_git_head",
        "index_stale",
        "truncated",
        "branch",
    }


def test_probe_maps_the_non_git_sentinel_to_none() -> None:
    meta = IndexMetadata("p", "/p", "prov", "m", 3, "h", indexed_at=0.0, git_head="")
    probe = IndexFreshnessProbe(
        enabled=True,
        ttl_seconds=0.0,
        read_metadata=lambda: meta,
        resolve_live_head=lambda: None,
        count_packages=lambda: 1,
        read_default_branch=lambda: NON_GIT_BRANCH_NAME,
    )
    info = asyncio.run(probe.envelope_info())
    assert info is not None and info.branch is None


def test_probe_default_closure_is_null_safe() -> None:
    meta = IndexMetadata("p", "/p", "prov", "m", 3, "h", indexed_at=0.0)
    probe = IndexFreshnessProbe(
        enabled=True,
        ttl_seconds=0.0,
        read_metadata=lambda: meta,
        resolve_live_head=lambda: None,
        count_packages=lambda: 1,
    )
    assert asyncio.run(probe.envelope_info()).branch is None


def test_factory_probe_reads_the_default_branch_from_the_bundle(tmp_path: Path) -> None:
    from pydocs_mcp.models import BranchIndexSource
    from pydocs_mcp.storage.branch_records import BranchRecord
    from pydocs_mcp.storage.index_metadata import write_index_metadata

    db = tmp_path / "b.db"
    conn = open_index_database(db)
    write_index_metadata(conn, IndexMetadata("p", str(tmp_path), "prov", "m", 3, "h", 1.0))
    conn.close()

    async def _seed() -> None:
        async with build_sqlite_uow_factory(db)() as uow:
            await uow.branches.upsert_branch(
                BranchRecord(
                    "feature/x",
                    "a" * 40,
                    BranchIndexSource.WORKING_TREE,
                    "h",
                    1.0,
                    1.0,
                    is_default=True,
                )
            )
            await uow.commit()

    # Contract §2.4 case 3: a v16 bundle not yet reindexed has an EMPTY
    # ``branches`` table — the state every pre-existing bundle is in right after
    # upgrading. The table exists, so no OperationalError fires; the null comes
    # from the empty fetchone(). A fresh probe per read (the TTL is 0.0 but the
    # cache is per-probe instance).
    before = build_freshness_probe(db_path=db, project_root=tmp_path, enabled=True, ttl_seconds=0.0)
    assert asyncio.run(before.envelope_info()).branch is None

    asyncio.run(_seed())
    probe = build_freshness_probe(db_path=db, project_root=tmp_path, enabled=True, ttl_seconds=0.0)
    assert asyncio.run(probe.envelope_info()).branch == "feature/x"


def test_factory_probe_is_none_on_a_pre_v16_bundle(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "project_name TEXT, project_root TEXT, embedding_provider TEXT, "
        "embedding_model TEXT, embedding_dim INTEGER, pipeline_hash TEXT, "
        "indexed_at REAL, git_head TEXT)"
    )
    conn.execute("CREATE TABLE packages (name TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO index_metadata (id, project_name, indexed_at) VALUES (1, 'p', 1.0)")
    conn.commit()
    conn.close()
    probe = build_freshness_probe(db_path=db, project_root=tmp_path, enabled=True, ttl_seconds=0.0)
    assert asyncio.run(probe.envelope_info()).branch is None


class _Probe:
    """Static probe double — the branch is the only thing that varies."""

    def __init__(self, branch: str | None) -> None:
        self._info = _info(branch)

    async def envelope_info(self) -> EnvelopeInfo:
        return self._info


async def _body() -> str:
    return "body"


async def test_envelope_text_is_unchanged_by_branch() -> None:
    envelope = ResponseEnvelope(probe=_Probe("feature/x"), surface="cli", pointers_enabled=False)

    response = await envelope.wrap("get_overview", "p", _body)
    assert "feature/x" not in response.text  # P0 rendering rule: meta only
    assert response.meta["branch"] == "feature/x"

    # Byte-identity, not just absence of the name: the SAME body wrapped with a
    # branch-less probe must render the SAME text (spec §6.7 — header line and
    # every card are invariant in P0; only ``meta`` moves).
    branchless = ResponseEnvelope(probe=_Probe(None), surface="cli", pointers_enabled=False)
    assert (await branchless.wrap("get_overview", "p", _body)).text == response.text
