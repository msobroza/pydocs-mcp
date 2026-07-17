"""Application-layer Protocols — extraction, dependency resolution + navigation.

ChunkExtractor returns an :class:`ExtractionResult` so the extraction
pipeline can surface chunks, the ``DocumentNode`` forest, and the
:class:`Package` together as named fields (spec §5, AC #19).
Strategy-based implementations live in ``extraction/strategies/`` and
``extraction/pipeline/`` and depend only on these Protocols, keeping
``ProjectIndexer`` backend-agnostic. A dataclass is used (instead of a
``tuple[..., ..., ...]``) so adding future fields (e.g. extraction
stats) doesn't break every destructuring call site.

:class:`TreeNavigator` / :class:`ReferenceNavigator` capture exactly the
surface :class:`~pydocs_mcp.application.lookup_service.LookupService`
consumes from its two collaborators. Positional-only markers (PEP 570
``/``) keep parameter-NAME differences between the real impls
(``TreeService`` / ``ReferenceService``) and the Null impls out of the
structural-conformance check; keyword-only names (``kind`` /
``max_depth`` / ``limit``) match the concrete impls exactly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.models import Chunk, ModuleMember, Package

if TYPE_CHECKING:
    # Imported only for typing — keeps the application layer from taking
    # a runtime dependency on storage value objects (``NodeReference``)
    # and avoids a runtime import cycle with the sibling service modules
    # that themselves import these Protocols' consumers.
    from pydocs_mcp.application.reference_service import ContextNode, ImpactNode
    from pydocs_mcp.application.similar_linker import SimilarPairOutcome
    from pydocs_mcp.application.workspace_linker import BundleHandle
    from pydocs_mcp.extraction.decisions._types import RawDecision
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.storage.node_reference import NodeReference


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Output of one :class:`ChunkExtractor` invocation.

    Carries flat chunks (FTS-bound), the document-tree forest (persisted
    to ``document_trees`` for lookup), and the package metadata in one
    immutable value so adding a future field (e.g. ``stats``) doesn't
    force every destructuring call site to change.

    ``references`` defaults to an empty tuple so existing extractors that
    don't yet emit cross-node edges (spec §4.2, AC #21) keep working
    without modification — Open/Closed compliance for the extension.

    ``reference_aliases`` is the per-module alias table captured during
    ingestion (spec §7.2 Rule A). Carried alongside ``references`` so the
    resolver running inside ``IndexingService.reindex_package`` has both
    inputs — references for the unresolved edges, aliases for ``from X
    import Y as Z``-style rewrites.
    """

    chunks: tuple[Chunk, ...]
    trees: tuple[DocumentNode, ...]
    package: Package
    references: tuple[NodeReference, ...] = field(default=())
    reference_aliases: dict[str, dict[str, str]] = field(default_factory=dict)
    # Sub-PR #5d — per-class ``self.X`` attribute-type table built by
    # ``capture_self_attribute_types``. Drives the resolver's Rule 0
    # (self.X.Y inference); carried alongside ``reference_aliases``
    # because both feed the same resolver pass.
    class_attribute_types: dict[str, dict[str, str]] = field(default_factory=dict)
    # Merged mined decisions (spec §D8) — populated by the capture_decisions
    # sub-pipeline on project targets only; dependency extractions leave it
    # empty. Threaded into ``IndexingService.reindex_package`` for reconcile +
    # persistence.
    decisions: tuple[RawDecision, ...] = field(default=())
    # Optional §D12 LLM-structured overlay: ``decision_key(title) -> (grounded
    # structured fields, verification tier)``. Populated ONLY when the default-off
    # structuring gate is enabled; empty otherwise. Threaded into
    # ``reindex_package`` so ``DecisionRecord.structured`` / ``verification`` are
    # stamped from it before persistence (the deliverable, not a discarded value).
    decision_structured: dict[str, tuple[dict[str, object], str]] = field(default_factory=dict)


@runtime_checkable
class DependencyResolver(Protocol):
    async def resolve(self, project_dir: Path) -> tuple[str, ...]: ...


@runtime_checkable
class ChunkExtractor(Protocol):
    async def extract_from_project(
        self,
        project_dir: Path,
    ) -> ExtractionResult: ...

    async def extract_from_dependency(
        self,
        dep_name: str,
    ) -> ExtractionResult: ...


@runtime_checkable
class MemberExtractor(Protocol):
    async def extract_from_project(
        self,
        project_dir: Path,
    ) -> tuple[ModuleMember, ...]: ...

    async def extract_from_dependency(
        self,
        dep_name: str,
    ) -> tuple[ModuleMember, ...]: ...


@runtime_checkable
class TreeNavigator(Protocol):
    """Read-side tree navigation consumed by ``LookupService``.

    Conformers: ``TreeService`` (real) and ``NullTreeService`` (raises /
    returns-False stand-in for deployments without a tree index).
    """

    async def get_tree(self, package: str, module: str, /) -> DocumentNode | None: ...

    async def exists(self, package: str, module: str, /) -> bool: ...


@runtime_checkable
class DecisionNavigator(Protocol):
    """The get_why backing contract — Null and real services share it (spec §D9/§D11)."""

    async def search(self, query: str) -> str: ...

    async def search_with_items(
        self, query: str
    ) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]: ...

    async def for_targets(self, targets: list[str], *, query: str = "") -> str: ...

    async def dashboard(self) -> str: ...

    # ``get_why`` body-producer triples (contract §3.6 items[], Task 8) — the
    # text methods above are façades over these three.
    async def why_search(
        self, query: str
    ) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]: ...

    async def why_targets(
        self, targets: list[str], *, query: str = ""
    ) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]: ...

    async def why_dashboard(
        self,
    ) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]: ...


@runtime_checkable
class CrossNavigator(Protocol):
    """Workspace impact federation + decision hydration (spec §3.4b, §A1.2).

    Conformers: ``CrossRepoNavigator`` (real) and ``NullCrossRepoNavigator``
    (single-project / disabled — returns the local walk unchanged and hydrates
    nothing), so consumers never hold ``Navigator | None``.
    """

    async def impact(
        self,
        service: ReferenceNavigator,
        package: str,
        qname: str,
        /,
        *,
        max_depth: int,
        limit: int,
    ) -> tuple[ImpactNode, ...]: ...

    async def decision_titles(
        self, wanted: tuple[tuple[str, str], ...]
    ) -> Mapping[tuple[str, str], str]: ...


@runtime_checkable
class ReferenceNavigator(Protocol):
    """Read-side reference-graph navigation consumed by ``LookupService``.

    Conformers: ``ReferenceService`` (real) and ``NullReferenceService``
    (raises ``ServiceUnavailableError`` when the reference graph is not
    captured). ``package`` is informational on every method — storage is
    cross-package by design; it stays in the signature for rendering
    context and call-site symmetry.
    """

    async def callers(self, package: str, node_qname: str, /) -> tuple[NodeReference, ...]: ...

    async def callees(self, package: str, node_qname: str, /) -> tuple[NodeReference, ...]: ...

    async def find_by_name(
        self, name: str, /, *, kind: ReferenceKind | None = None
    ) -> tuple[NodeReference, ...]: ...

    async def inherits(self, package: str, node_qname: str, /) -> tuple[NodeReference, ...]: ...

    async def governed_by(self, package: str, node_qname: str, /) -> tuple[NodeReference, ...]: ...

    async def impact(
        self, package: str, qname: str, /, *, max_depth: int, limit: int
    ) -> tuple[ImpactNode, ...]: ...

    async def context(
        self, package: str, qname: str, /, *, max_depth: int, limit: int
    ) -> tuple[ContextNode, ...]: ...


@runtime_checkable
class SimilarGenerator(Protocol):
    """One ordered bundle pair -> generated SIMILAR cross-edges (spec SA1.2).

    Conformers: ``SimilarLinkGenerator`` (real, embedder-gated query-driven
    search) and ``NullSimilarLinkGenerator`` (``similar`` not opted in / no
    embedder -- returns an inactive outcome), so ``WorkspaceLinker`` never
    holds ``Generator | None``.
    """

    async def generate_pair(
        self, source: BundleHandle, target: BundleHandle
    ) -> SimilarPairOutcome: ...
