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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.models import Chunk, FileChangeKind, LandingStep, ModuleMember, Package

if TYPE_CHECKING:
    # Imported only for typing — keeps the application layer from taking
    # a runtime dependency on storage value objects (``NodeReference``)
    # and avoids a runtime import cycle with the sibling service modules
    # that themselves import these Protocols' consumers.
    from pydocs_mcp.application.reference_service import ContextNode, ImpactNode
    from pydocs_mcp.application.similar_linker import SimilarPairOutcome
    from pydocs_mcp.application.target_resolution import ResolutionEntry, TargetResolution
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
    # The exact absolute paths the discovery stage walked (spec §6.14 item 5):
    # the branch manifest is built from these, so it equals what was
    # extracted by construction — no second walk, no drift.
    discovered_paths: tuple[str, ...] = field(default=())
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

    async def extract_from_paths(
        self,
        project_root: Path,
        paths: Sequence[str],
    ) -> ExtractionResult:
        """Project-mode extraction of exactly ``paths`` (relative POSIX), no walk (#309).

        Mines no decisions: decision mining per branch is P2 (O10).
        """
        ...


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
class TargetResolver(Protocol):
    """Miss-path target resolution consumed by the symbol-shaped tools (spec §2.2).

    Conformers: ``ProjectTargetResolver`` (real, rules gated per YAML flag)
    and ``NullTargetResolver`` (every flag off, or direct/test construction —
    returns the empty ``TargetResolution()``), so consumers never hold
    ``Resolver | None``. Called only after the exact target raised
    ``NotFoundError``; ``entry`` names the calling surface
    (``lookup`` / ``context`` / ``source``).
    """

    async def resolve(self, target: str, /, *, entry: ResolutionEntry) -> TargetResolution: ...


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


@runtime_checkable
class GitRepository(Protocol):
    """The git port (spec §6.2: P0 plus P1). Adapters live in ``pydocs_mcp.git``.

    Every path is project-relative POSIX (``pkg/a.py``) except worktree paths,
    which are absolute. Read-only except the two sanctioned writes of §6.8b,
    ``fetch`` and ``update_ref_if_unchanged``, which only callers behind a YAML
    switch invoke. The tree, blob and grep reads go through git objects and
    never read the working tree. Adapters raise
    :class:`~pydocs_mcp.git.errors.GitCommandError` on failure; the Null
    adapter answers empty / ``None`` / ``False`` and never raises.
    """

    def current_branch(self) -> str | None: ...

    def head_sha(self, ref: str | None = None) -> str | None:
        """Commit sha of ``ref`` (``None`` means HEAD); ``None`` when it does not resolve."""
        ...

    def index_manifest(self) -> tuple[tuple[str, str], ...]:
        """``(path, blob_sha)`` for every tracked file, from git's own index."""
        ...

    def hash_objects(self, paths: Sequence[str]) -> tuple[tuple[str, str], ...]:
        """``(path, blob_sha)`` computed from the working-tree bytes of ``paths``."""
        ...

    def working_tree_changes(self) -> tuple[tuple[str, FileChangeKind], ...]:
        """Modified / added(untracked) / deleted paths versus the index."""
        ...

    def list_worktrees(self) -> tuple[tuple[str, str | None], ...]:
        """``(absolute_path, branch_or_None)`` for every worktree of the repository."""
        ...

    # ── P1 part one (spec §6.2): branches, trees, blobs, remotes ──
    def symbolic_ref(self, name: str) -> str | None:
        """Target ref of a symref (``refs/remotes/origin/HEAD`` → ``refs/remotes/origin/main``).

        ``None`` when ``name`` is unset or not a symref. A dangling symref
        still names its target; ``head_sha(target)`` is then ``None``.
        """
        ...

    def list_local_branches(self) -> tuple[tuple[str, str], ...]:
        """``(short_name, sha)`` for every ``refs/heads/*`` ref."""
        ...

    def ls_tree(self, ref: str) -> tuple[tuple[str, str, int], ...]:
        """``(path, blob_sha, size)`` for every regular file of the tree at ``ref``.

        No file bytes are read. Symlinks and submodules are skipped: a symlink
        blob holds its target path, never file content to index.
        """
        ...

    def merge_base(self, a: str, b: str) -> str | None:
        """Best common ancestor, or ``None`` when the histories are unrelated."""
        ...

    def is_ancestor(self, a: str, b: str) -> bool:
        """``True`` when commit ``a`` is reachable from ``b``."""
        ...

    def upstream_of(self, branch: str) -> str | None:
        """``origin/main``-style upstream of local ``branch``, or ``None``."""
        ...

    def ahead_behind(self, branch: str, upstream: str) -> tuple[int, int]:
        """``(commits only on branch, commits only on upstream)``."""
        ...

    def ls_remote_heads(self, remote: str) -> tuple[tuple[str, str], ...]:
        """``(short_name, sha)`` from ``ls-remote --heads`` — the only network read."""
        ...

    def fetch(self, remote: str, *, prune: bool = False) -> None:
        """``git fetch`` — a sanctioned repository write (§6.8b layer 3).

        ``--atomic`` where git supports it (>= 2.31), so a partial failure
        updates no ref; no hook, auto-maintenance or submodule fetch runs.
        """
        ...

    def update_ref_if_unchanged(self, ref: str, new_sha: str, old_sha: str, message: str) -> bool:
        """Compare-and-swap ``ref`` from ``old_sha`` to ``new_sha`` (§6.8b layer 4).

        ``False`` when the ref no longer points at ``old_sha`` (a lost race).
        Any other refusal (a held lock, a missing object) raises
        ``GitCommandError``: a caller looping over branches catches it per
        branch, it is not a remote failure.
        """
        ...

    def grep(self, ref: str, pattern: str, flags: Sequence[str], paths: Sequence[str]) -> str:
        """Raw ``git grep -n -I`` output over ``ref``; ``""`` when nothing matched.

        The output shape does not depend on git config: project-relative
        unquoted paths, no column, no color, basic regex unless ``-E`` / ``-F``
        / ``-P`` is passed. ``flags`` are short matching / context flags
        (``-i``, ``-w``, ``-F``, ``-E``, ``-P``, ``-v``, ``-l``, ``-L``, ``-c``,
        ``-o``, ``-A<n>``, ``-B<n>``, ``-C<n>``, ``--max-depth=<n>``); any other
        flag is refused because it could read the working tree or run a program.
        """
        ...

    def show(self, ref: str, path: str) -> str:
        """The committed text of ``path`` at ``ref``; undecodable bytes are replaced."""
        ...

    def read_blobs(self, entries: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
        """``(path, text)`` for ``(blob_sha, path)`` pairs — ONE ``cat-file --batch`` process."""
        ...

    # ── P1 part two (spec §6.2, amended 2026-09-04): landings and patch ids ──
    # Every patch id is ``git patch-id --stable`` over a diff rendered with
    # ``--no-renames -U3`` and the text-shaping config pinned, so an id cached
    # today compares with one computed later under another user config.
    def patch_id(self, base_sha: str, ref: str) -> str:
        """Patch id of ``diff base_sha ref`` (the whole-range squash id); ``""`` when empty."""
        ...

    def patch_ids_per_commit(self, base_sha: str, ref: str) -> tuple[tuple[str, str], ...]:
        """``(sha, patch_id)`` per commit of ``base_sha..ref``, oldest first.

        The rebase-merge detector's input (§6.8a). Merge commits and commits
        with an empty diff have no row.
        """
        ...

    def first_parent_landings(
        self, base_tip: str, *, max_count: int, stop_at: str | None = None
    ) -> tuple[LandingStep, ...]:
        """First-parent steps of ``base_tip``, newest first, each with its ``c^1..c`` patch id.

        The range is ``stop_at..base_tip``: ``stop_at`` and everything older is
        excluded. ``max_count`` is the hard ceiling either way; the subprocess
        adapter refuses a negative count (``git log -n -1`` means no limit),
        while the Null answers ``()`` for every input. A step with an empty
        diff carries ``patch_id == ""``.
        """
        ...

    def upstream_gone(self, branch: str) -> bool:
        """``True`` when local ``branch`` has an upstream configured whose ref no longer exists.

        ``False`` for no upstream at all: only a prune fetch makes an upstream "gone".
        """
        ...

    def tags_on_first_parent(
        self, base_tip: str, pattern: str, max_count: int
    ) -> tuple[tuple[str, str], ...]:
        """``(tag, commit_sha)`` newest first, for tags on the first-parent line.

        Only the newest ``max_count`` first-parent steps are walked; the
        subprocess adapter refuses a negative count, the Null answers ``()``.
        ``pattern`` is a case-sensitive ``fnmatch`` pattern (``v*``); annotated
        tags are peeled to their commit.
        """
        ...
