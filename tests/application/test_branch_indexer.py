"""BranchIndexer (spec §6.3 for a ref that is not checked out, #310): the manifest
is ``ls_tree`` ∩ the discovery scope, hits are reused, misses are materialized
into a scratch tree outside the project and extracted, and the scratch tree
never outlives the pass — not even a cancelled one."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_mcp.application import node_score_compute
from pydocs_mcp.application.branch_indexer import BranchIndexer
from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.extraction_cache import (
    EMPTY_SWEEP,
    FileArtifacts,
    artifacts_json,
)
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.protocols import ExtractionResult, GitRepository
from pydocs_mcp.extraction import AstMemberExtractor
from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.strategies.python_module_id import package_rooted_module_id
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    Chunk,
    ModuleMember,
    Package,
    PackageOrigin,
)
from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES
from pydocs_mcp.storage.branch_records import FileExtraction
from pydocs_mcp.storage.node_score import NodeScore
from tests._fakes import FakeGitRepository, InMemoryNodeScoreStore, make_fake_uow_factory

A, B, MB = "a" * 40, "b" * 40, "c" * 40
_KEY = "p|x:k"
_BRANCH = "feature/x"
_PROJECT = Package(
    name=PROJECT_PACKAGE_NAME,
    version="",
    summary="",
    homepage="",
    dependencies=(),
    content_hash="",
    origin=PackageOrigin.PROJECT,
)


def _module(root: Path, relative: str) -> str:
    return package_rooted_module_id(str(root / relative), root)


def _node(root: Path, relative: str) -> DocumentNode:
    module = _module(root, relative)
    return DocumentNode(module, module, module, NodeKind.MODULE, relative, 1, 1, "t", "h")


@dataclass
class RecordingExtractor:
    """``extract_from_paths`` over the scratch tree; records what it saw on disk."""

    calls: list[tuple[str, ...]] = field(default_factory=list)
    roots: list[Path] = field(default_factory=list)
    on_disk: list[frozenset[str]] = field(default_factory=list)

    async def extract_from_project(self, project_dir: Path) -> ExtractionResult:
        raise AssertionError("a branch pass never walks a tree")

    async def extract_from_dependency(self, dep_name: str) -> ExtractionResult:
        raise AssertionError("a branch pass never extracts a dependency")

    async def extract_from_paths(self, project_root: Path, paths: Sequence[str]):
        self.calls.append(tuple(paths))
        self.roots.append(project_root)
        files = (p for p in project_root.rglob("*") if p.is_file())
        self.on_disk.append(frozenset(p.relative_to(project_root).as_posix() for p in files))
        chunks = tuple(self._chunk(project_root, p) for p in paths)
        trees = tuple(_node(project_root, p) for p in paths)
        return ExtractionResult(chunks=chunks, trees=trees, package=_PROJECT)

    @staticmethod
    def _chunk(root: Path, relative: str) -> Chunk:
        return Chunk.from_test_inputs(
            package=PROJECT_PACKAGE_NAME,
            module=_module(root, relative),
            title=relative,
            text=(root / relative).read_text(encoding="utf-8"),
            metadata={"source_path": relative, "start_line": 1, "end_line": 1},
        )


def _base(git: GitRepository) -> BaseBranch | None:
    return BaseBranch("main", A, None)


def _indexer(
    tmp_path: Path, git: FakeGitRepository, factory, extractor: RecordingExtractor, **overrides
) -> BranchIndexer:
    fields = dict(
        git=git,
        chunk_extractor=extractor,
        member_extractor=AstMemberExtractor(),
        indexing_service=IndexingService(uow_factory=factory),
        uow_factory=factory,
        scope=DiscoveryScopeConfig(max_file_size_bytes=1000),
        excludes_loader=lambda root: EMPTY_PROJECT_EXCLUDES,
        pipeline_hash="p",
        current_extraction_cache_key=lambda: _KEY,
        project_root=tmp_path / "proj",
        base_resolver=_base,
        now=lambda: 7.0,
    )
    return BranchIndexer(**{**fields, **overrides})


def _git(*rows: tuple[str, str, int], blobs: dict[str, str]) -> FakeGitRepository:
    return FakeGitRepository(trees={B: rows}, blobs=blobs, merge_bases={frozenset((A, B)): MB})


async def test_the_manifest_is_the_ref_listing_intersected_with_the_scope(tmp_path) -> None:
    git = _git(
        ("pkg/a.py", "s1", 6),
        ("pkg/a.pyc", "s2", 6),
        ("node_modules/x.py", "s3", 6),
        ("big.py", "s4", 10**6),
        blobs={"s1": "a = 1\n"},
    )
    factory, extractor = make_fake_uow_factory(), RecordingExtractor()
    outcome = await _indexer(tmp_path, git, factory, extractor).index_ref(_BRANCH, B)
    assert (outcome.files_total, outcome.files_extracted) == (1, 1)
    assert extractor.calls == [("pkg/a.py",)]
    (root,) = extractor.roots
    assert not root.exists() and not root.is_relative_to(tmp_path / "proj")
    assert root.name == "proj"  # the stand-in keeps the project's name (module ids)
    async with factory() as uow:
        record = await uow.branches.get_branch(_BRANCH)
        files = await uow.branches.list_files(_BRANCH)
    assert (record.head_sha, record.base_name, record.merge_base_sha) == (B, "main", MB)
    assert [f.path for f in files] == ["pkg/a.py"]


async def test_a_second_pass_over_an_unchanged_ref_extracts_and_embeds_nothing(tmp_path) -> None:
    git = _git(("pkg/a.py", "s1", 6), blobs={"s1": "a = 1\n"})
    factory, extractor = make_fake_uow_factory(), RecordingExtractor()
    indexer = _indexer(tmp_path, git, factory, extractor)
    await indexer.index_ref(_BRANCH, B)
    outcome = await indexer.index_ref(_BRANCH, B)
    assert len(extractor.calls) == 1
    assert (outcome.files_reused, outcome.files_extracted, outcome.chunks_embedded) == (1, 0, 0)


async def test_an_empty_file_is_never_extracted(tmp_path) -> None:
    git = _git(
        ("pkg/__init__.py", "e0", 0), ("pkg/a.py", "s1", 6), blobs={"e0": "", "s1": "a = 1\n"}
    )
    factory, extractor = make_fake_uow_factory(), RecordingExtractor()
    indexer = _indexer(tmp_path, git, factory, extractor)
    await indexer.index_ref(_BRANCH, B)
    await indexer.index_ref(_BRANCH, B)
    assert extractor.calls == [("pkg/a.py",)]
    # Still in the manifest, and on disk while the miss was extracted.
    assert "pkg/__init__.py" in extractor.on_disk[0]


async def test_spans_only_cache_rows_are_misses_and_get_refilled(tmp_path) -> None:
    git = _git(("pkg/a.py", "s1", 6), blobs={"s1": "a = 1\n"})
    factory, extractor = make_fake_uow_factory(), RecordingExtractor()
    async with factory() as uow:
        await uow.file_extractions.upsert_many([FileExtraction("s1", "pkg/a.py", _KEY, "[]", 1.0)])
        await uow.commit()
    await _indexer(tmp_path, git, factory, extractor).index_ref(_BRANCH, B)
    assert extractor.calls == [("pkg/a.py",)]
    async with factory() as uow:
        assert (await uow.file_extractions.get("s1", "pkg/a.py", _KEY)).tree_json is not None


async def test_init_files_and_the_root_pyproject_ride_the_one_blob_read(tmp_path) -> None:
    git = _git(
        ("pyproject.toml", "t0", 9),
        ("pkg/__init__.py", "i0", 6),
        ("pkg/a.py", "s1", 6),
        blobs={"t0": "[project]\n", "i0": "i = 1\n", "s1": "a = 1\n"},
    )
    factory, extractor = make_fake_uow_factory(), RecordingExtractor()
    indexer = _indexer(tmp_path, git, factory, extractor)
    await indexer.index_ref(_BRANCH, B)
    assert len(git.blob_reads) == 1
    assert {"pyproject.toml", "pkg/__init__.py", "pkg/a.py"} <= extractor.on_disk[0]
    # Every file is now cached: the re-run still lays the packages out (module
    # ids depend on them) but extracts nothing, so it needs no pyproject.toml.
    await indexer.index_ref(_BRANCH, B)
    assert len(extractor.calls) == 1
    assert [p for _sha, p in git.blob_reads[1]] == ["pkg/__init__.py"]


async def _seed_hit(factory, path: str, module: str, members: tuple[ModuleMember, ...] = ()):
    tree = DocumentNode(module, module, module, NodeKind.MODULE, path, 1, 1, "t", "h")
    columns = artifacts_json(FileArtifacts(tree, members, EMPTY_SWEEP))
    row = FileExtraction("s1", path, _KEY, "[]", 1.0, *columns)
    async with factory() as uow:
        await uow.file_extractions.upsert_many([row])
        await uow.commit()


async def test_a_hit_whose_module_id_moved_on_this_branch_is_extracted(tmp_path) -> None:
    """The cached row was extracted where ``src/pkg`` was a package (``pkg.a``);
    on this branch it is not, so the id is ``src.pkg.a`` and the row is a miss."""
    git = _git(("src/pkg/a.py", "s1", 6), blobs={"s1": "a = 1\n"})
    factory, extractor = make_fake_uow_factory(), RecordingExtractor()
    await _seed_hit(factory, "src/pkg/a.py", "pkg.a")
    await _indexer(tmp_path, git, factory, extractor).index_ref(_BRANCH, B)
    assert extractor.calls == [("src/pkg/a.py",)]
    async with factory() as uow:
        assert await uow.trees.load(PROJECT_PACKAGE_NAME, "src.pkg.a", branch=_BRANCH)


async def test_a_hit_whose_package_layout_holds_is_reused(tmp_path) -> None:
    git = _git(
        ("src/pkg/__init__.py", "e0", 0),
        ("src/pkg/a.py", "s1", 6),
        blobs={"e0": "", "s1": "a = 1\n"},
    )
    factory, extractor = make_fake_uow_factory(), RecordingExtractor()
    await _seed_hit(factory, "src/pkg/a.py", "pkg.a")
    outcome = await _indexer(tmp_path, git, factory, extractor).index_ref(_BRANCH, B)
    assert extractor.calls == [] and outcome.files_reused == 1


async def test_a_hit_whose_module_id_another_file_shares_is_extracted(tmp_path) -> None:
    """``a/app/main.py`` and ``b/app/main.py`` are both ``app.main`` here: the
    cached tree of one cannot be told apart from the other's, so both go
    through extraction, as the working-tree pass leaves them uncached."""
    git = _git(
        ("a/app/__init__.py", "e0", 0),
        ("a/app/main.py", "s1", 6),
        ("b/app/__init__.py", "e0", 0),
        ("b/app/main.py", "s2", 6),
        blobs={"e0": "", "s1": "a = 1\n", "s2": "b = 2\n"},
    )
    factory, extractor = make_fake_uow_factory(), RecordingExtractor()
    await _seed_hit(factory, "a/app/main.py", "app.main")
    outcome = await _indexer(tmp_path, git, factory, extractor).index_ref(_BRANCH, B)
    assert [set(call) for call in extractor.calls] == [{"a/app/main.py", "b/app/main.py"}]
    assert (outcome.files_reused, outcome.files_extracted) == (0, 2)


async def test_members_come_from_the_cache_and_the_misses_never_twice(tmp_path) -> None:
    cached = ModuleMember(
        metadata={
            "package": PROJECT_PACKAGE_NAME,
            "module": "pkg",
            "name": "from_init",
            "kind": "function",
        }
    )
    git = _git(
        ("pkg/__init__.py", "s1", 21),
        ("pkg/b.py", "s2", 22),
        blobs={"s1": "def from_init(): ...\n", "s2": "def from_miss(): ...\n"},
    )
    factory, extractor = make_fake_uow_factory(), RecordingExtractor()
    await _seed_hit(factory, "pkg/__init__.py", "pkg", (cached,))
    await _indexer(tmp_path, git, factory, extractor).index_ref(_BRANCH, B)
    assert extractor.calls == [("pkg/b.py",)]
    async with factory() as uow:
        rows = await uow.module_members.list(
            filter={"package": PROJECT_PACKAGE_NAME, "branch": _BRANCH}
        )
    assert sorted(m.metadata["name"] for m in rows) == ["from_init", "from_miss"]


async def test_no_base_leaves_the_base_fields_empty(tmp_path) -> None:
    git = _git(("pkg/a.py", "s1", 6), blobs={"s1": "a = 1\n"})
    factory = make_fake_uow_factory()
    indexer = _indexer(tmp_path, git, factory, RecordingExtractor(), base_resolver=lambda g: None)
    await indexer.index_ref(_BRANCH, B)
    async with factory() as uow:
        record = await uow.branches.get_branch(_BRANCH)
    assert (record.base_name, record.merge_base_sha) == (None, None)


async def test_an_unrelated_history_stamps_an_empty_merge_base(tmp_path) -> None:
    git = FakeGitRepository(trees={B: (("pkg/a.py", "s1", 6),)}, blobs={"s1": "a = 1\n"})
    factory = make_fake_uow_factory()
    await _indexer(tmp_path, git, factory, RecordingExtractor()).index_ref(_BRANCH, B)
    async with factory() as uow:
        record = await uow.branches.get_branch(_BRANCH)
    assert (record.base_name, record.merge_base_sha) == ("main", "")


async def test_a_failed_base_read_costs_the_base_only(tmp_path) -> None:
    def _failing(git: GitRepository) -> BaseBranch | None:
        raise GitCommandError(("git", "rev-parse"), "timeout after 30s")

    git = _git(("pkg/a.py", "s1", 6), blobs={"s1": "a = 1\n"})
    factory = make_fake_uow_factory()
    indexer = _indexer(tmp_path, git, factory, RecordingExtractor(), base_resolver=_failing)
    outcome = await indexer.index_ref(_BRANCH, B)
    assert outcome.files_extracted == 1
    async with factory() as uow:
        assert (await uow.branches.get_branch(_BRANCH)).base_name is None


async def test_a_branch_rescore_leaves_the_served_dependency_scores_alone(
    tmp_path, monkeypatch
) -> None:
    """``replace_dependency_tier=False`` is all that keeps a branch pass from
    re-ranking the served branch's dependencies by this branch's graph (#310)."""
    scores = InMemoryNodeScoreStore()
    factory = make_fake_uow_factory(node_scores=scores)
    served_dependency = NodeScore("requests", "requests.get", pagerank=0.5)
    await scores.upsert((served_dependency,), branch="")
    computed = (
        NodeScore(PROJECT_PACKAGE_NAME, "pkg.a", pagerank=0.9),
        NodeScore("requests", "requests.get", pagerank=0.1),
    )
    monkeypatch.setattr(node_score_compute, "compute_scores", lambda edges, packages: computed)
    service = IndexingService(uow_factory=factory, node_scores_enabled=True)
    git = _git(("pkg/a.py", "s1", 6), blobs={"s1": "a = 1\n"})
    indexer = _indexer(tmp_path, git, factory, RecordingExtractor(), indexing_service=service)
    await indexer.index_ref(_BRANCH, B)
    assert scores.by_key == {("requests", "requests.get"): served_dependency}
    assert scores.by_branch[_BRANCH] == {(PROJECT_PACKAGE_NAME, "pkg.a"): computed[0]}


async def test_the_scratch_tree_is_made_and_removed_off_the_event_loop(
    tmp_path, monkeypatch
) -> None:
    """The removal walks every materialized blob; on the loop it would stall a
    live ``serve`` once ref-driven refresh runs these passes there (#310)."""
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}
    real_mkdtemp, real_rmtree = tempfile.mkdtemp, shutil.rmtree

    def _mkdtemp(*args, **kwargs):
        seen["mkdtemp"] = threading.get_ident()
        return real_mkdtemp(*args, **kwargs)

    def _rmtree(path, *args, **kwargs):
        seen["rmtree"] = threading.get_ident()
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(tempfile, "mkdtemp", _mkdtemp)
    monkeypatch.setattr(shutil, "rmtree", _rmtree)
    git = _git(("pkg/a.py", "s1", 6), blobs={"s1": "a = 1\n"})
    extractor = RecordingExtractor()
    await _indexer(tmp_path, git, make_fake_uow_factory(), extractor).index_ref(_BRANCH, B)
    assert set(seen) == {"mkdtemp", "rmtree"} and loop_thread not in seen.values()
    assert not extractor.roots[0].exists()


@dataclass
class _BlockingBlobs(FakeGitRepository):
    """``read_blobs`` parks until released: a pass cancelled mid-materialization."""

    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)

    def read_blobs(self, entries):
        self.entered.set()
        self.release.wait(timeout=10)
        return super().read_blobs(entries)


async def test_a_cancelled_pass_removes_the_scratch_tree_after_the_writer_stops(
    tmp_path, monkeypatch
) -> None:
    temp_root = tmp_path / "system-temp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    git = _BlockingBlobs(trees={B: (("pkg/a.py", "s1", 6),)}, blobs={"s1": "a = 1\n"})
    indexer = _indexer(tmp_path, git, make_fake_uow_factory(), RecordingExtractor())
    task = asyncio.create_task(indexer.index_ref(_BRANCH, B))
    assert await asyncio.to_thread(git.entered.wait, 10)
    task.cancel()
    await asyncio.sleep(0.05)
    # The writer thread still runs: its tree must still be there for it.
    assert list(temp_root.iterdir())
    git.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(temp_root.iterdir()) == []
