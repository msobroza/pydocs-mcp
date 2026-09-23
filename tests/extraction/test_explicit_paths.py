"""Explicit-path discovery and ``extract_from_paths`` (spec §6.3 step 3, #309).

A branch that is not checked out is indexed from a scratch tree its blobs were
materialized into; discovery then yields exactly the cache misses instead of
walking. With no explicit paths the walk is today's, byte for byte.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from pydocs_mcp.application.protocols import ChunkExtractor, ExtractionResult
from pydocs_mcp.extraction import PipelineChunkExtractor, build_ingestion_pipeline
from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages.file_discovery import FileDiscoveryStage
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.discovery import (
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)
from pydocs_mcp.git.blob_scratch import materialize_blobs, scratch_tree
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, ChunkOrigin, Package, PackageOrigin
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import MockEmbedder, make_fake_uow_factory
from tests._git_sandbox import isolate_git_config, requires_git, run_git


def _stage() -> FileDiscoveryStage:
    scope = DiscoveryScopeConfig()
    return FileDiscoveryStage(
        project_discoverer=ProjectFileDiscoverer(scope=scope),
        dep_discoverer=DependencyFileDiscoverer(scope=scope),
    )


def _project_state(root: Path, explicit_paths: tuple[str, ...] = ()) -> IngestionState:
    return IngestionState(
        files=FileBundle(
            target=root,
            target_kind=TargetKind.PROJECT,
            package_name=PROJECT_PACKAGE_NAME,
            explicit_paths=explicit_paths,
        )
    )


def _write(root: Path, relative: str, text: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


async def test_explicit_paths_skip_the_walk_and_keep_the_effective_excludes(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "pkg/a.py", "a = 1\n")
    _write(tmp_path, "pkg/b.py", "b = 1\n")
    _write(tmp_path, "pkg/notes.xyz", "x\n")
    out = await _stage().run(_project_state(tmp_path, ("pkg/notes.xyz", "pkg/b.py")))
    # Exactly the given paths, sorted, absolute — no extension filter re-applied
    # (the caller already intersected the manifest with the discovery scope).
    assert out.files.paths == (str(tmp_path / "pkg" / "b.py"), str(tmp_path / "pkg" / "notes.xyz"))
    assert out.files.root == tmp_path
    assert ".git" in out.files.effective_excludes.names


async def test_explicit_paths_read_the_pyproject_excludes_of_the_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pydocs-mcp]\nexclude_dirs = ["generated"]\n', encoding="utf-8"
    )
    _write(tmp_path, "pkg/a.py", "a = 1\n")
    out = await _stage().run(_project_state(tmp_path, ("pkg/a.py",)))
    walked = await _stage().run(_project_state(tmp_path))
    assert out.files.effective_excludes == walked.files.effective_excludes
    assert "generated" in out.files.effective_excludes.names


async def test_a_path_listed_twice_is_discovered_once(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/a.py", "a = 1\n")
    out = await _stage().run(_project_state(tmp_path, ("pkg/a.py", "pkg/a.py")))
    assert out.files.paths == (str(tmp_path / "pkg" / "a.py"),)


async def test_without_explicit_paths_the_walk_is_unchanged(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/a.py", "a = 1\n")
    _write(tmp_path, "pkg/notes.xyz", "x\n")
    _write(tmp_path, ".git/config", "[core]\n")
    out = await _stage().run(_project_state(tmp_path))
    paths, root, effective = ProjectFileDiscoverer(scope=DiscoveryScopeConfig()).discover(tmp_path)
    assert out.files.paths == tuple(paths) == (str(tmp_path / "pkg" / "a.py"),)
    assert (out.files.root, out.files.effective_excludes) == (root, effective)


def test_effective_excludes_is_the_set_the_walk_prunes_against(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pydocs-mcp]\nexclude_dirs = ["vendor/gen"]\n', encoding="utf-8"
    )
    discoverer = ProjectFileDiscoverer(scope=DiscoveryScopeConfig(exclude_dirs=["build2"]))
    effective = discoverer.effective_excludes(tmp_path)
    assert discoverer.discover(tmp_path)[2] == effective
    assert {"build2", ".git"} <= effective.names
    assert effective.anchored == frozenset({"vendor/gen"})


# ── PipelineChunkExtractor.extract_from_paths ─────────────────────────────


@dataclass(frozen=True)
class _CapturingPipeline:
    """Records the state it received and returns it with a package attached."""

    seen: list[IngestionState]

    async def run(self, state: IngestionState) -> IngestionState:
        self.seen.append(state)
        package = Package(
            name=PROJECT_PACKAGE_NAME,
            version="",
            summary="",
            homepage="",
            dependencies=(),
            content_hash="h",
            origin=PackageOrigin.PROJECT,
        )
        return replace(state, package=package)


async def test_extract_from_paths_runs_project_mode_over_exactly_those_paths(
    tmp_path: Path,
) -> None:
    seen: list[IngestionState] = []
    extractor = PipelineChunkExtractor(pipeline=_CapturingPipeline(seen))  # type: ignore[arg-type]
    result = await extractor.extract_from_paths(tmp_path, ["pkg/b.py", "docs/x.md"])
    (state,) = seen
    assert state.files.target == tmp_path
    assert state.files.target_kind is TargetKind.PROJECT
    assert state.files.package_name == PROJECT_PACKAGE_NAME
    assert state.files.explicit_paths == ("pkg/b.py", "docs/x.md")
    assert result.package.name == PROJECT_PACKAGE_NAME


async def test_extract_from_paths_refuses_an_empty_list_instead_of_walking(
    tmp_path: Path,
) -> None:
    # An empty tuple means "walk the root" to the discovery stage; handing it
    # through would silently extract every file of the scratch tree.
    extractor = PipelineChunkExtractor(pipeline=_CapturingPipeline([]))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one path"):
        await extractor.extract_from_paths(tmp_path, [])


_ADR_TEXT = "# 1. Use SQLite\n\nStatus: accepted\n\nThe index lives in pkg.core.\n"


def _decision_chunks(result: ExtractionResult) -> list[str]:
    origin = ChunkOrigin.DECISION_RECORD.value
    return [c.text for c in result.chunks if c.metadata.get("origin") == origin]


def _governs_edges(result: ExtractionResult) -> list[str]:
    return [r.to_name for r in result.references if r.kind is ReferenceKind.GOVERNS]


async def test_extract_from_paths_mines_no_decisions_from_the_scratch_tree(
    tmp_path: Path,
) -> None:
    # P1 (O10 is P2): a non-working-tree branch carries no decisions. The ADR,
    # CHANGELOG and docs sources glob the root themselves, so without the guard
    # they would mine whichever of those files happened to be cache misses.
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/core.py", "class Engine:\n    pass\n")
    _write(tmp_path, "docs/adr/0001-use-sqlite.md", _ADR_TEXT)
    walked = await _extractor().extract_from_project(tmp_path)
    assert _decision_chunks(walked)
    assert _governs_edges(walked) == ["pkg.core"]
    explicit = await _extractor().extract_from_paths(
        tmp_path, ["pkg/core.py", "docs/adr/0001-use-sqlite.md"]
    )
    assert explicit.chunks, "the explicit paths themselves are still chunked"
    assert explicit.decisions == ()
    assert _decision_chunks(explicit) == []
    assert _governs_edges(explicit) == []


def test_the_chunk_extractor_protocol_carries_extract_from_paths() -> None:
    assert "extract_from_paths" in dir(ChunkExtractor)
    pipeline = _CapturingPipeline([])
    assert isinstance(PipelineChunkExtractor(pipeline=pipeline), ChunkExtractor)  # type: ignore[arg-type]


# ── Parity: blobs materialized from git extract like the working tree ─────

# A root-level ``__init__.py`` makes the root directory's own name the top
# package segment of every module id, so this project also proves the scratch
# stand-in keeps the root's name.
_PROJECT_FILES = {
    "__init__.py": "",
    "pkg/__init__.py": '"""The package."""\n',
    "pkg/core.py": 'class Engine:\n    """Runs."""\n\n    def start(self):\n        return 1\n',
    "pkg/util.py": "from pkg.core import Engine\n\n\ndef make():\n    return Engine()\n",
    "docs/guide.md": "# Guide\n\nIntro.\n\n## Usage\n\nCall make().\r\n",
    "config/settings.toml": '[tool]\nname = "x"\n',
}


@pytest.fixture
def committed_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    isolate_git_config(monkeypatch, tmp_path / "home")
    root = tmp_path / "proj"
    root.mkdir()
    for relative, text in _PROJECT_FILES.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(text.encode("utf-8"))
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "add", "--", ".")
    run_git(root, "commit", "-q", "-m", "one")
    return root


def _chunk_rows(result: ExtractionResult) -> list[tuple[str, str, dict[str, object]]]:
    # ``Chunk.embedding`` is a numpy array, which ``==`` cannot fold to a bool;
    # the embedder is a function of the text, which the rows already compare.
    return [(c.text, c.content_hash, dict(c.metadata)) for c in result.chunks]


def _extractor() -> PipelineChunkExtractor:
    pipeline = build_ingestion_pipeline(
        AppConfig.load(), embedder=MockEmbedder(), uow_factory=make_fake_uow_factory()
    )
    return PipelineChunkExtractor(pipeline=pipeline)


@requires_git
async def test_blobs_materialized_from_git_extract_exactly_like_the_working_tree(
    committed_project: Path,
) -> None:
    git = SubprocessGitRepository(project_root=committed_project)
    entries = [(sha, path) for path, sha, _ in git.ls_tree("HEAD")]
    working = await _extractor().extract_from_project(committed_project)
    with scratch_tree(stand_in_for=committed_project) as root:
        written = materialize_blobs(git, entries, root)
        scratch = await _extractor().extract_from_paths(root, written)
    assert sorted(written) == sorted(_PROJECT_FILES)
    assert _chunk_rows(scratch) == _chunk_rows(working)
    assert scratch.trees == working.trees
    assert scratch.references == working.references
    assert scratch.reference_aliases == working.reference_aliases
    modules = {str(c.metadata["module"]) for c in scratch.chunks}
    assert {"proj.pkg.core", "proj.pkg.util"} <= modules
