"""The ``link`` verb + end-to-end serve wiring over real bundles
(AC15, AC21, AC34; the workspace-overview status line of §3.8).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Package, PackageOrigin
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from pydocs_mcp.storage.index_metadata import IndexMetadata, write_index_metadata
from pydocs_mcp.storage.node_reference import NodeReference


def _node(qname: str, *children: DocumentNode) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=NodeKind.MODULE,
        source_path="m.py",
        start_line=1,
        end_line=5,
        text="x",
        content_hash=f"h-{qname}",
        children=tuple(children),
    )


def _make_bundle(
    path: Path,
    *,
    name: str,
    tree: DocumentNode,
    refs: tuple[NodeReference, ...] = (),
    indexed_at: float = 1.0,
) -> Path:
    conn = open_index_database(path)
    write_index_metadata(
        conn,
        IndexMetadata(
            project_name=name,
            project_root=f"/src/{name}",
            embedding_provider="fastembed",
            embedding_model="BAAI/bge-small-en-v1.5",
            embedding_dim=384,
            pipeline_hash="h",
            indexed_at=indexed_at,
        ),
    )
    conn.close()

    async def _seed() -> None:
        factory = build_sqlite_uow_factory(path)
        async with factory() as uow:
            await uow.packages.upsert(
                Package(
                    name="__project__",
                    version="1",
                    summary="",
                    homepage="",
                    dependencies=(),
                    content_hash=f"c-{name}",
                    origin=PackageOrigin.PROJECT,
                )
            )
            # Trees are saved under __project__ (what the linker's universe
            # reads, matching the indexer) AND under the top package name
            # (what the lookup module probe resolves for a dotted target).
            await uow.trees.save_many([tree], package="__project__")
            await uow.trees.save_many([tree], package=tree.qualified_name.split(".")[0])
            if refs:
                await uow.references.save_many(refs, package="__project__")
            await uow.commit()

    asyncio.run(_seed())
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    _make_bundle(
        ws / "backend_aaaaaaaaaa.db",
        name="backend",
        tree=_node("backend", _node("backend.api.handler")),
        refs=(
            NodeReference(
                from_package="__project__",
                from_node_id="backend.api.handler",
                to_name="mylib.core.parse",
                to_node_id=None,
                kind=ReferenceKind.CALLS,
            ),
        ),
    )
    _make_bundle(
        ws / "mylib_bbbbbbbbbb.db",
        name="mylib",
        tree=_node("mylib", _node("mylib.core", _node("mylib.core.parse"))),
    )
    return ws


def _run_cli(argv: list[str], monkeypatch) -> int:
    monkeypatch.setattr("sys.argv", ["pydocs-mcp", *argv])
    return _cli_main()


def test_link_verb_creates_overlay_and_reports(workspace: Path, capsys, monkeypatch) -> None:
    # AC21: the verb creates the overlay and prints the LinkReport.
    code = _run_cli(["link", "--workspace", str(workspace)], monkeypatch)
    out = capsys.readouterr().out
    assert code in (0, None)
    assert (workspace / "pydocs-links.sqlite3").exists()
    assert "backend: scanned 1 unresolved, created 1 edge(s)" in out
    # AC21: --check is clean right after linking, exit 0, no extra writes.
    assert _run_cli(["link", "--workspace", str(workspace), "--check"], monkeypatch) in (0, None)
    assert "cross-repo links: fresh" in capsys.readouterr().out


def test_link_check_exits_one_on_stale(workspace: Path, capsys, monkeypatch) -> None:
    _run_cli(["link", "--workspace", str(workspace)], monkeypatch)
    capsys.readouterr()
    # "Reindex" backend: rewrite its metadata with a newer indexed_at.
    conn = open_index_database(workspace / "backend_aaaaaaaaaa.db")
    write_index_metadata(
        conn,
        IndexMetadata(
            project_name="backend",
            project_root="/src/backend",
            embedding_provider="fastembed",
            embedding_model="BAAI/bge-small-en-v1.5",
            embedding_dim=384,
            pipeline_hash="h",
            indexed_at=99.0,
        ),
    )
    conn.close()
    code = _run_cli(["link", "--workspace", str(workspace), "--check"], monkeypatch)
    assert code == 1
    assert "stale" in capsys.readouterr().out


def test_overlay_never_loads_as_a_bundle(workspace: Path, monkeypatch) -> None:
    # AC21 second half: the overlay file must never be discovered as a project.
    from pydocs_mcp.multirepo import discover_workspace

    _run_cli(["link", "--workspace", str(workspace)], monkeypatch)
    projects = discover_workspace(workspace)
    assert sorted(p.name for p in projects) == ["backend", "mylib"]


def test_end_to_end_callers_cross_repo_through_build_routers(workspace: Path) -> None:
    # AC15 + AC34: default config (enabled: true), two bundles, serve-path
    # linking — get_references(direction="callers") on the mylib symbol
    # includes the backend caller, project-qualified.
    from pydocs_mcp.application.mcp_inputs import ReferencesInput

    config = AppConfig.load()
    tools, services = build_routers(config, workspace=workspace, run_link_pass=True)

    async def _ask() -> str:
        return await tools.get_references(
            ReferencesInput(target="mylib.core.parse", direction="callers", project="mylib")
        )

    out = asyncio.run(_ask())
    assert "backend.api.handler" in out
    assert "(project: backend)" in out
    assert "cross-repo" in out


def test_single_bundle_is_inert_with_default_config(tmp_path: Path) -> None:
    # AC34/N7: one bundle + enabled:true → no overlay, byte-identical serve.
    solo = tmp_path / "solo"
    solo.mkdir()
    _make_bundle(
        solo / "backend_aaaaaaaaaa.db",
        name="backend",
        tree=_node("backend", _node("backend.api.handler")),
    )
    config = AppConfig.load()
    build_routers(config, workspace=solo, run_link_pass=True)
    assert not (solo / "pydocs-links.sqlite3").exists()


def test_workspace_overview_reports_link_freshness(workspace: Path) -> None:
    # §3.8 freshness surfacing: the workspace card carries the status line.
    from pydocs_mcp.application.mcp_inputs import OverviewInput

    config = AppConfig.load()
    tools, _ = build_routers(config, workspace=workspace, run_link_pass=True)

    async def _card() -> str:
        return await tools.get_overview(OverviewInput())

    out = asyncio.run(_card())
    assert "cross-repo links: fresh" in out
