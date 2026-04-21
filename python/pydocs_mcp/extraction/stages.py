"""Six ingestion stages for IngestionPipeline (spec §7.2).

All registered via ``@stage_registry.register()``; all async; all return new
``IngestionState`` via ``dataclasses.replace``. Stages that vary by target
(``FileDiscoveryStage``, ``PackageBuildStage``) branch on
``state.target_kind`` internally — the YAML stays one-dimensional per
spec decision #18 / §7.3.

Per-file error isolation: :class:`ChunkingStage` catches per-file failures
with ``# noqa: BLE001`` because spec AC #27 requires a bad file not to abort
the whole pipeline. All other stages let exceptions propagate — a missing
distribution in :class:`PackageBuildStage` is a ``LookupError`` the service
layer is expected to translate into a non-fatal skip.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydocs_mcp.extraction import (
    chunkers as _chunkers,  # noqa: F401 — side-effect registration of Chunkers in chunker_registry
)
from pydocs_mcp.extraction.document_node import DocumentNode
from pydocs_mcp.extraction.pipeline import IngestionState, TargetKind
from pydocs_mcp.extraction.serialization import chunker_registry, stage_registry
from pydocs_mcp.extraction.tree_flatten import flatten_to_chunks
from pydocs_mcp.models import Chunk, Package, PackageOrigin

if TYPE_CHECKING:
    from pydocs_mcp.extraction.config import ChunkingConfig
    from pydocs_mcp.extraction.discovery import (
        DependencyFileDiscoverer,
        ProjectFileDiscoverer,
    )

log = logging.getLogger("pydocs-mcp")


@stage_registry.register("file_discovery")
@dataclass(frozen=True, slots=True)
class FileDiscoveryStage:
    """Fills ``state.paths`` + ``state.root`` — target-kind branch lives here.

    Holds BOTH discoverers and picks at runtime on ``state.target_kind``. The
    alternative (two pipelines, one per kind) would duplicate the shared
    middle four stages and force callers to pick — the branch is small,
    local, and typed (spec decision #19).
    """

    project_discoverer: "ProjectFileDiscoverer"
    dep_discoverer: "DependencyFileDiscoverer"
    name: str = "file_discovery"

    async def run(self, state: IngestionState) -> IngestionState:
        paths, root = await asyncio.to_thread(self._discover, state)
        return replace(state, paths=tuple(paths), root=root)

    def _discover(self, state: IngestionState) -> tuple[list[str], Path]:
        if state.target_kind is TargetKind.PROJECT:
            return self.project_discoverer.discover(Path(str(state.target)))
        return self.dep_discoverer.discover(str(state.target))

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "FileDiscoveryStage":
        # Deferred import avoids importing concrete discoverers at registry
        # construction time — keeps the registry decode path free of
        # side-effect-heavy filesystem-aware modules.
        from pydocs_mcp.extraction.discovery import (
            DependencyFileDiscoverer,
            ProjectFileDiscoverer,
        )
        disc = context.app_config.extraction.discovery
        return cls(
            project_discoverer=ProjectFileDiscoverer(scope=disc.project),
            dep_discoverer=DependencyFileDiscoverer(scope=disc.dependency),
        )

    def to_dict(self) -> dict:
        return {"type": "file_discovery"}


@stage_registry.register("file_read")
@dataclass(frozen=True, slots=True)
class FileReadStage:
    """Fills ``state.file_contents`` via parallel Rust-accelerated read.

    Wraps ``_fast.read_files_parallel`` under ``asyncio.to_thread`` — the
    underlying Rayon iterator is CPU-bound on large projects, so offloading
    keeps the event loop responsive.
    """

    name: str = "file_read"

    async def run(self, state: IngestionState) -> IngestionState:
        contents = await asyncio.to_thread(self._read, list(state.paths))
        return replace(state, file_contents=tuple(contents))

    def _read(self, paths: list[str]) -> list[tuple[str, str]]:
        # Deferred so _fast's native/fallback choice is resolved lazily.
        from pydocs_mcp._fast import read_files_parallel
        return list(read_files_parallel(paths))

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "FileReadStage":
        return cls()

    def to_dict(self) -> dict:
        return {"type": "file_read"}


@stage_registry.register("chunking")
@dataclass(frozen=True, slots=True)
class ChunkingStage:
    """Fills ``state.trees`` by dispatching each file to a chunker by extension.

    Per-file failures are isolated (spec AC #27): one broken file must not
    abort ingestion of the whole project. Unknown extensions are dropped
    silently — the dispatch policy is ``chunker_registry[ext]`` and missing
    registrations are a wiring concern, not a per-run error.
    """

    chunking_config: "ChunkingConfig"
    name: str = "chunking"

    async def run(self, state: IngestionState) -> IngestionState:
        trees = await asyncio.to_thread(self._chunk_all, state)
        return replace(state, trees=tuple(trees))

    def _chunk_all(self, state: IngestionState) -> list[DocumentNode]:
        trees: list[DocumentNode] = []
        for path, source in state.file_contents:
            tree = self._chunk_one(path, source, state)
            if tree is not None:
                trees.append(tree)
        return trees

    def _chunk_one(
        self, path: str, source: str, state: IngestionState,
    ) -> DocumentNode | None:
        if not source:
            return None
        ext = Path(path).suffix.lower()
        chunker_cls = chunker_registry.get(ext)
        if chunker_cls is None:
            return None  # unknown extension — skip silently (policy, not error)
        chunker = chunker_cls.from_config(self.chunking_config)
        try:
            return chunker.build_tree(path, source, state.package_name, state.root)
        except Exception as exc:  # noqa: BLE001 -- AC #27: per-file failure must not abort pipeline
            log.warning("chunker %s failed on %s: %s", ext, path, exc)
            return None

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "ChunkingStage":
        return cls(chunking_config=context.app_config.extraction.chunking)

    def to_dict(self) -> dict:
        return {"type": "chunking"}


@stage_registry.register("flatten")
@dataclass(frozen=True, slots=True)
class FlattenStage:
    """Fills ``state.chunks`` by walking each tree via ``flatten_to_chunks``.

    Thin wrapper — the walking / direct-text rule lives in
    :mod:`pydocs_mcp.extraction.tree_flatten`; this stage just concatenates
    per-tree results in pipeline order.
    """

    name: str = "flatten"

    async def run(self, state: IngestionState) -> IngestionState:
        chunks = await asyncio.to_thread(self._flatten_all, state)
        return replace(state, chunks=tuple(chunks))

    def _flatten_all(self, state: IngestionState) -> list[Chunk]:
        out: list[Chunk] = []
        for tree in state.trees:
            out.extend(flatten_to_chunks(tree, package=state.package_name))
        return out

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "FlattenStage":
        return cls()

    def to_dict(self) -> dict:
        return {"type": "flatten"}


@stage_registry.register("content_hash")
@dataclass(frozen=True, slots=True)
class ContentHashStage:
    """Fills ``state.content_hash`` — the package-level hash used for whole-
    package cache invalidation (spec §7.2 note on per-node hashes).

    Per-node ``DocumentNode.content_hash`` values are computed inside each
    chunker and don't flow through state — they ride on the trees instead.
    """

    name: str = "content_hash"

    async def run(self, state: IngestionState) -> IngestionState:
        h = await asyncio.to_thread(self._hash, list(state.paths))
        return replace(state, content_hash=h)

    def _hash(self, paths: list[str]) -> str:
        # Deferred so _fast's native/fallback choice is resolved lazily.
        from pydocs_mcp._fast import hash_files
        result = hash_files(paths)
        # hash_files may return str (fallback) or bytes (some native builds).
        # Normalize so downstream consumers see a stable str regardless.
        return result if isinstance(result, str) else result.hex()

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "ContentHashStage":
        return cls()

    def to_dict(self) -> dict:
        return {"type": "content_hash"}


@stage_registry.register("package_build")
@dataclass(frozen=True, slots=True)
class PackageBuildStage:
    """Fills ``state.package`` — branches on ``state.target_kind``.

    PROJECT path produces the canonical ``Package(name="__project__", ...)``
    that today's ``indexer.py`` builds. DEPENDENCY path walks
    ``importlib.metadata.Distribution`` metadata — a missing distribution
    raises :class:`LookupError` so the service layer can translate into a
    non-fatal skip one level up (declared-but-not-installed deps are common
    during local development; the stage keeps its contract honest by raising).
    """

    name: str = "package_build"

    async def run(self, state: IngestionState) -> IngestionState:
        pkg = await asyncio.to_thread(self._build, state)
        return replace(state, package=pkg)

    def _build(self, state: IngestionState) -> Package:
        if state.target_kind is TargetKind.PROJECT:
            return self._project_package(state)
        return self._dep_package(state)

    def _project_package(self, state: IngestionState) -> Package:
        target = Path(str(state.target))
        return Package(
            name="__project__",
            version="local",
            summary=f"Project: {target.name}",
            homepage="",
            dependencies=(),
            content_hash=state.content_hash,
            origin=PackageOrigin.PROJECT,
        )

    def _dep_package(self, state: IngestionState) -> Package:
        # Deferred imports keep the heavy importlib.metadata machinery out of
        # module-load-time for callers that never hit the dep branch.
        from pydocs_mcp.deps import normalize_package_name
        from pydocs_mcp.extraction._dep_helpers import (
            find_installed_distribution,
        )
        dep_name = str(state.target)
        dist = find_installed_distribution(dep_name)
        if dist is None:
            raise LookupError(f"dependency {dep_name!r} is not installed")
        raw_name = dist.metadata["Name"] or dep_name
        name = normalize_package_name(raw_name)
        version = dist.metadata["Version"] or "?"
        summary = dist.metadata["Summary"] or ""
        homepage = dist.metadata["Home-page"] or ""
        deps = tuple(
            r.split(";")[0].strip() for r in (dist.requires or [])[:50]
        )
        return Package(
            name=name,
            version=version,
            summary=summary,
            homepage=homepage,
            dependencies=deps,
            content_hash=state.content_hash,
            origin=PackageOrigin.DEPENDENCY,
        )

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "PackageBuildStage":
        return cls()

    def to_dict(self) -> dict:
        return {"type": "package_build"}


__all__ = (
    "ChunkingStage",
    "ContentHashStage",
    "FileDiscoveryStage",
    "FileReadStage",
    "FlattenStage",
    "PackageBuildStage",
)
