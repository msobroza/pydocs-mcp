"""run_index_pass — the write-side index-pass sequence (application layer)."""

from __future__ import annotations

import logging
from pathlib import Path

from pydocs_mcp.application.index_project import run_index_pass
from pydocs_mcp.application.indexing_service import IndexingStats
from pydocs_mcp.storage.index_metadata import IndexMetadata


class FakeIndexOrchestrator:
    """Named fake for ProjectIndexer — records index_project kwargs."""

    def __init__(self, calls: list[str], stats: IndexingStats) -> None:
        self._calls = calls
        self._stats = stats
        self.kwargs: dict[str, object] | None = None

    async def index_project(self, project: Path, **kwargs: object) -> IndexingStats:
        self._calls.append("index_project")
        self.kwargs = {"project": project, **kwargs}
        return self._stats


class FakeInvalidatingService:
    """Named fake for IndexingService — records the invalidation call."""

    def __init__(self, calls: list[str], stale: list[str]) -> None:
        self._calls = calls
        self._stale = stale
        self.current_model: str | None = None

    async def invalidate_stale_embeddings(self, *, current_model: str) -> list[str]:
        self._calls.append("invalidate")
        self.current_model = current_model
        return self._stale


def _harness(*, repaired: list[str] | None = None, stale: list[str] | None = None):
    calls: list[str] = []
    stamped: list[IndexMetadata] = []

    async def check_integrity() -> list[str]:
        calls.append("check_integrity")
        return list(repaired or [])

    async def rebuild_fts() -> None:
        calls.append("rebuild_fts")

    def stamp_metadata(meta: IndexMetadata) -> None:
        calls.append("stamp_metadata")
        stamped.append(meta)

    async def write_aggregates(_project: Path) -> None:
        calls.append("write_aggregates")

    orchestrator = FakeIndexOrchestrator(calls, IndexingStats(indexed=2, cached=1))
    service = FakeInvalidatingService(calls, list(stale or []))
    return (
        calls,
        stamped,
        orchestrator,
        service,
        check_integrity,
        rebuild_fts,
        stamp_metadata,
        write_aggregates,
    )


async def _run(
    orchestrator,
    service,
    check_integrity,
    rebuild_fts,
    stamp_metadata,
    write_aggregates,
    *,
    force=False,
    project=Path("/tmp/proj"),
):
    return await run_index_pass(
        orchestrator=orchestrator,
        indexing_service=service,
        pipeline_hash="hash-1",
        project=project,
        embedding_provider="fastembed",
        embedding_model="model-b",
        embedding_dim=384,
        force=force,
        include_project_source=True,
        include_dependencies=False,
        workers=3,
        check_integrity=check_integrity,
        rebuild_fts=rebuild_fts,
        stamp_metadata=stamp_metadata,
        write_aggregates=write_aggregates,
    )


async def _run_index_pass_with_fakes(*, project: Path, stamp_metadata) -> None:
    """Drive ``run_index_pass`` with this file's fakes, overriding only ``project``.

    Used by the git-head stamp tests, which care solely about ``project`` (whether
    it is a git repo) and the ``stamp_metadata`` callback that captures the result.
    """
    _calls, _stamped, orch, svc, ci, rf, _sm, wa = _harness()
    await _run(orch, svc, ci, rf, stamp_metadata, wa, project=project)


async def test_sequence_and_forwarding() -> None:
    calls, _stamped, orch, svc, ci, rf, sm, wa = _harness()
    stats = await _run(orch, svc, ci, rf, sm, wa)

    assert calls == [
        "check_integrity",
        "invalidate",
        "index_project",
        "rebuild_fts",
        "stamp_metadata",
        "write_aggregates",
    ]
    assert stats.indexed == 2
    assert stats.cached == 1
    assert svc.current_model == "model-b"
    assert orch.kwargs == {
        "project": Path("/tmp/proj"),
        "force": False,
        "include_project_source": True,
        "include_dependencies": False,
        "workers": 3,
    }


async def test_force_skips_stale_invalidation() -> None:
    calls, _stamped, orch, svc, ci, rf, sm, wa = _harness()
    await _run(orch, svc, ci, rf, sm, wa, force=True)

    assert "invalidate" not in calls
    assert orch.kwargs is not None
    assert orch.kwargs["force"] is True


async def test_metadata_stamp_carries_identity_and_recency() -> None:
    _calls, stamped, orch, svc, ci, rf, sm, wa = _harness()
    await _run(orch, svc, ci, rf, sm, wa)

    (meta,) = stamped
    assert meta.project_name == "proj"
    assert meta.project_root == "/tmp/proj"
    assert meta.embedding_provider == "fastembed"
    assert meta.embedding_model == "model-b"
    assert meta.embedding_dim == 384
    assert meta.pipeline_hash == "hash-1"
    assert meta.indexed_at > 0.0


async def test_stamp_includes_git_head_when_project_is_a_repo(tmp_path) -> None:
    # Arrange a minimal git layout so resolve_git_head returns a sha.
    sha = "d" * 40
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text(f"{sha}\n")

    stamped: list = []
    await _run_index_pass_with_fakes(project=tmp_path, stamp_metadata=stamped.append)

    assert stamped and stamped[0].git_head == sha


async def test_stamp_git_head_empty_for_non_git_tree(tmp_path) -> None:
    stamped: list = []
    await _run_index_pass_with_fakes(project=tmp_path, stamp_metadata=stamped.append)
    assert stamped and stamped[0].git_head == ""


async def test_repair_and_stale_warnings_logged(caplog) -> None:
    calls, _stamped, orch, svc, ci, rf, sm, wa = _harness(
        repaired=["numpy"], stale=["fastapi", "attrs"]
    )
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await _run(orch, svc, ci, rf, sm, wa)

    assert "Cache integrity" in caplog.text
    assert "Embedding model changed; re-embedding 2 package(s): fastapi, attrs" in caplog.text


async def test_force_logs_cache_cleared(caplog) -> None:
    _calls, _stamped, orch, svc, ci, rf, sm, wa = _harness()
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await _run(orch, svc, ci, rf, sm, wa, force=True)

    assert "Cache cleared" in caplog.text


def test_run_index_pass_is_package_exported() -> None:
    import pydocs_mcp.application as application

    assert application.run_index_pass is run_index_pass
