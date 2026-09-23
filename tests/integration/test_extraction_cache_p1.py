"""#309 end to end, through the composition root the CLI uses: the working-tree
pass fills the blob-keyed extraction cache under the current per-file extraction
key; a second branch's manifest splits into hits for the blobs it shares and a
miss for the one it changed; a grammar flip or a chunker change supersedes every
row, while a setting that shapes no file (decision capture) keeps them (#261)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from pydocs_mcp.application.extraction_cache import (
    CacheSplit,
    cached_file,
    file_extraction_cache_key,
    split_cache_hits,
)
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
)
from pydocs_mcp.git.factory import git_repository_factory
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.storage.branch_records import BranchFile
from pydocs_mcp.storage.sqlite import SqliteUnitOfWork
from tests._git_sandbox import isolate_git_config, requires_git, run_git
from tests.integration.test_multi_branch_p0 import _count, _index, _project, _rows

pytestmark = requires_git


@pytest.fixture(autouse=True)
def _sandboxed_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests switch branches and commit: an inherited ``GIT_DIR`` (a run
    launched from a git hook) would aim them at the invoking repository. The
    scrubbed environment also reaches ``_project``'s own git calls."""
    isolate_git_config(monkeypatch, tmp_path / "home")


def _current_key(config: AppConfig) -> str:
    return file_extraction_cache_key(
        config.compute_ingestion_pipeline_hash(),
        chunking=config.extraction.chunking,
        reference_capture=config.reference_graph.capture,
    )


def _keys(db: Path) -> set[str]:
    return {r[0] for r in _rows(db, "SELECT DISTINCT pipeline_hash FROM file_extractions")}


def _config(tmp_path: Path, name: str, overlay: str) -> AppConfig:
    path = tmp_path / name
    path.write_text(overlay, encoding="utf-8")
    return AppConfig.load(explicit_path=path)


def _uow(db: Path) -> SqliteUnitOfWork:
    return SqliteUnitOfWork(provider=PerCallConnectionProvider(cache_path=db))


async def _split(db: Path, files: tuple[BranchFile, ...], key: str) -> CacheSplit:
    async with _uow(db) as uow:
        return await split_cache_hits(uow, files, key)


def test_a_second_branch_reuses_every_unchanged_blob_and_misses_the_changed_one(
    tmp_path: Path,
) -> None:
    root, db = _project(tmp_path), tmp_path / "p.db"
    config = AppConfig.load()
    _index(root, db, config)
    assert _keys(db) == {_current_key(config)}
    assert _count(db, "file_extractions WHERE tree_json IS NULL") == 0
    run_git(root, "checkout", "-q", "-b", "feature/x")
    (root / "pkg" / "b.py").write_text("def gamma():\n    return 3\n", encoding="utf-8")
    run_git(root, "commit", "-q", "-am", "feature")
    run_git(root, "checkout", "-q", "main")

    git = git_repository_factory(config.git)(root)
    files = tuple(BranchFile("feature/x", p, blob) for p, blob, _ in git.ls_tree("feature/x"))
    split = asyncio.run(_split(db, files, _current_key(config)))

    assert [f.path for f, _ in split.hits] == ["pkg/a.py"]
    # b.py changed. The empty __init__.py chunks to no tree at all, so it has
    # nothing to cache and stays a (free) miss: an empty source is never parsed.
    assert sorted(f.path for f in split.misses) == ["pkg/__init__.py", "pkg/b.py"]
    asyncio.run(_assert_the_hit_is_the_stored_extraction(db, dict(split.hits)))


async def _assert_the_hit_is_the_stored_extraction(db: Path, hits: dict) -> None:
    (row,) = (r for f, r in hits.items() if f.path == "pkg/a.py")
    cached = cached_file(row, branch="feature/x")
    async with _uow(db) as uow:
        tree = await uow.trees.load(PROJECT_PACKAGE_NAME, "pkg.a", branch="main")
        members = await uow.module_members.list(
            filter={"package": PROJECT_PACKAGE_NAME, "module": "pkg.a", "branch": "main"}
        )
        membership = await uow.branch_chunks.list_membership("main")
    assert cached.artifacts.tree == tree
    assert [m.metadata["name"] for m in cached.artifacts.members] == [
        m.metadata["name"] for m in members
    ]
    assert {m.chunk_id for m in cached.memberships} == {
        m.chunk_id for m in membership if m.source_path == "pkg/a.py"
    }


@pytest.fixture
def _fresh_multilang_caches() -> Iterator[None]:
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


@pytest.mark.usefixtures("_fresh_multilang_caches")
def test_a_grammar_appearing_supersedes_every_row_and_re_embeds_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0022's stranded state through the cache: rows extracted while the
    Rust grammar could not load (text windows, no edges) must never hit once it
    loads; the Python chunks, which the grammar does not shape, keep their ids."""
    pytest.importorskip("tree_sitter_rust")
    root, db = _project(tmp_path), tmp_path / "p.db"
    (root / "pkg" / "lib.rs").write_text(
        "fn run() { helper(); }\nfn helper() {}\n", encoding="utf-8"
    )
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "rust")
    config = AppConfig.load()
    rust_tree = "SELECT tree_json FROM file_extractions WHERE path='pkg/lib.rs'"
    python_chunks = "SELECT id, content_hash FROM chunks WHERE source_path LIKE 'pkg/%.py'"
    with monkeypatch.context() as blocked:
        blocked.setitem(sys.modules, "tree_sitter", None)
        _reset_multilang_caches()
        _index(root, db, config)
        blocked_key = _current_key(config)
    _reset_multilang_caches()
    keys_blocked, tree_blocked, chunks_blocked = (
        _keys(db),
        _rows(db, rust_tree),
        _rows(db, python_chunks),
    )

    _index(root, db, config)

    assert keys_blocked == {blocked_key}
    assert _keys(db) == {_current_key(config)} != keys_blocked  # the old rows are gone
    assert _rows(db, rust_tree) != tree_blocked  # structural symbols, not text windows
    assert chunks_blocked and _rows(db, python_chunks) == chunks_blocked


def test_a_chunker_setting_supersedes_the_rows_and_a_decision_setting_keeps_them(
    tmp_path: Path,
) -> None:
    root, db = _project(tmp_path), tmp_path / "p.db"
    stock = AppConfig.load()
    _index(root, db, stock)
    created = "SELECT MAX(created_at) FROM file_extractions"
    first_write = _rows(db, created)

    decisions = _config(tmp_path, "d.yaml", "decision_capture:\n  merge_jaccard: 0.5\n")
    _index(root, db, decisions)  # the decision fold moves the package hash: a full pass
    assert _keys(db) == {_current_key(stock)} == {_current_key(decisions)}
    assert _rows(db, created) > first_write  # rewritten under the same key, not skipped

    windows = "extraction:\n  chunking:\n    text_section:\n      window_lines: 7\n"
    chunking = _config(tmp_path, "c.yaml", windows)
    _index(root, db, chunking)
    assert _keys(db) == {_current_key(chunking)} != {_current_key(stock)}
