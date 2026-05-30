"""Storage Protocols — 12 @runtime_checkable contracts (spec §5.2, AC #3)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, runtime_checkable

from pydocs_mcp.models import Chunk, Embedding, ModuleMember, Package
from pydocs_mcp.storage.filters import Filter

if TYPE_CHECKING:
    import numpy as np

    from pydocs_mcp.extraction.model import DocumentNode
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.storage.node_reference import NodeReference


@runtime_checkable
class PackageStore(Protocol):
    async def upsert(self, package: Package) -> None: ...
    async def get(self, name: str) -> Package | None: ...
    async def list(
        self,
        filter: Filter | Mapping | None = None,
        limit: int | None = None,
    ) -> list[Package]: ...
    async def delete(self, filter: Filter | Mapping) -> int: ...
    async def count(self, filter: Filter | Mapping | None = None) -> int: ...

    async def delete_all(self) -> None:
        """Delete every package row. Atomic within the surrounding UoW.

        Symmetric with :class:`DocumentTreeStore.delete_all` and
        :class:`ReferenceStore.delete_all` — closes the Protocol gap so
        :meth:`IndexingService.clear_all` does not need to build an
        ``All(clauses=())`` filter to express "wipe everything". The
        explicit method also documents the destructive sweep as part of
        the Protocol surface; alternate backends (Postgres, DuckDB) can
        implement it without reverse-engineering the filter intent.
        """
        ...


@runtime_checkable
class ChunkStore(Protocol):
    async def upsert(self, chunks: Iterable[Chunk]) -> None: ...
    async def list(
        self,
        filter: Filter | Mapping | None = None,
        limit: int | None = None,
    ) -> list[Chunk]: ...
    async def delete(self, filter: Filter | Mapping) -> int: ...
    async def count(self, filter: Filter | Mapping | None = None) -> int: ...
    async def rebuild_index(self) -> None: ...

    async def delete_all(self) -> None:
        """Delete every chunk row. Atomic within the surrounding UoW.

        Symmetric with :class:`DocumentTreeStore.delete_all` and
        :class:`ReferenceStore.delete_all`. See :class:`PackageStore.delete_all`
        for rationale.
        """
        ...

    async def list_id_hash_pairs(
        self,
        *,
        filter: Filter | Mapping | None = None,
    ) -> tuple[tuple[int, str | None], ...]:
        """Return (id, content_hash) for chunks matching filter.

        Cheap variant of list() that avoids loading full text/metadata.
        Used by the diff-merge in IndexingService.reindex_package and
        by LoadExistingChunkHashesStage in ingestion.

        Rows whose content_hash is NULL (pre-existing legacy rows) return
        None for the hash slot — the diff-merge treats those as 'removed'
        so they self-heal on the first reindex per package (spec AC-8).
        """
        ...

    async def delete_by_ids(self, ids: Sequence[int]) -> None:
        """Delete chunks by their SQLite primary-key IDs.

        Used by the diff-merge to remove only the rows that no longer
        exist in the incoming chunk set (instead of wiping the whole
        package's chunks like ``delete(filter={"package": X})`` does).
        Empty ids → no-op.
        """
        ...

    async def insert(self, chunks: tuple[Chunk, ...]) -> None:
        """Insert chunks; assigns rowids.

        Distinct from ``upsert`` (which silently updates on duplicate keys
        — undesirable for the diff-merge which only inserts the
        added/changed subset). Persists Chunk.content_hash.
        """
        ...


@runtime_checkable
class ModuleMemberStore(Protocol):
    async def upsert_many(self, members: Iterable[ModuleMember]) -> None: ...
    async def list(
        self,
        filter: Filter | Mapping | None = None,
        limit: int | None = None,
    ) -> list[ModuleMember]: ...
    async def delete(self, filter: Filter | Mapping) -> int: ...
    async def count(self, filter: Filter | Mapping | None = None) -> int: ...

    async def delete_all(self) -> None:
        """Delete every module-member row. Atomic within the surrounding UoW.

        Symmetric with :class:`DocumentTreeStore.delete_all` and
        :class:`ReferenceStore.delete_all`. See :class:`PackageStore.delete_all`
        for rationale.
        """
        ...


# Each SearchMatch is a Chunk or ModuleMember with relevance/retriever_name set.
# Returning tuple[Chunk | ModuleMember, ...] — the "SearchMatch" type alias
# carried forward from sub-PR #1 is implemented via the Chunk/ModuleMember
# instances themselves (which carry relevance + retriever_name fields).


@runtime_checkable
class TextSearchable(Protocol):
    async def text_search(
        self,
        query_terms: str,
        limit: int,
        filter: Filter | Mapping | None = None,
    ) -> tuple[Chunk, ...]: ...


@runtime_checkable
class VectorSearchable(Protocol):
    async def vector_search(
        self,
        query_vector: Sequence[float],
        limit: int,
        filter: Filter | Mapping | None = None,
    ) -> tuple[Chunk, ...]: ...


@runtime_checkable
class HybridSearchable(Protocol):
    async def hybrid_search(
        self,
        query_terms: str,
        query_vector: Sequence[float],
        limit: int,
        filter: Filter | None = None,
    ) -> tuple[Chunk, ...]: ...


@runtime_checkable
class FilterAdapter(Protocol):
    """Translate a backend-neutral Filter tree to a backend-specific query fragment.

    Concrete impls live in the storage layer; the composition root wires
    them into :class:`~pydocs_mcp.retrieval.serialization.BuildContext` so
    retrieval steps (``PreFilterStep``, ``ChunkFetcherStep``,
    ``MemberFetcherStep``) call the typed Protocol instead of importing
    the SQLite-specific adapter at runtime. For SQL backends ``adapt``
    returns ``(where_clause, positional_params)``; for Cypher / Mongo /
    other backends the shape varies — the fetcher that consumes the
    output knows the backend's expected query-string format.

    ``target_field`` distinguishes the table-specific column whitelist +
    prefix the adapter should use: ``"chunk"`` for ``chunks_fts JOIN
    chunks`` style queries (prefixed columns), ``"member"`` for
    ``module_members`` (bare column names). Concrete adapters store both
    whitelists internally and dispatch at ``adapt`` time so the
    composition root wires a SINGLE adapter into BuildContext.
    """

    def adapt(
        self,
        tree: Filter,
        *,
        target_field: Literal["chunk", "member"],
    ) -> tuple[str, tuple[Any, ...]]: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """Atomic transaction scope + per-transaction repository accessor (spec §14.2).

    Inside ``async with uow:`` the FIVE repository attributes are valid
    and share one SQLite connection. Outside the context they raise
    :class:`~pydocs_mcp.storage.errors.UnitOfWorkNotEnteredError`.
    Explicit ``commit()`` persists; safety-net ``rollback`` on exception
    or no-commit. ``references`` is the 5th attribute (the cross-node
    reference-graph store).

    Spec S15: ``vectors`` is ALWAYS present — the SQLite-only deployment
    exposes a :class:`~pydocs_mcp.storage.null_vector_store.NullVectorStore`,
    the composite SQLite + TurboQuant deployment exposes the real
    backend. Callers no longer need ``getattr(uow, "vectors", None)``
    guards.

    Late-interaction: ``multi_vectors`` is ALWAYS present too — the
    default deployment exposes a
    :class:`~pydocs_mcp.storage.null_multi_vector_store.NullMultiVectorStore`,
    deployments that enable ``late_interaction.enabled=true`` in YAML
    swap in a real fast-plaid backend via the composition root.
    """

    # Read-only @property (not bare settable annotations): both
    # SqliteUnitOfWork and CompositeUnitOfWork expose these repos via
    # read-only properties, so the Protocol must require only a
    # *readable* attribute. A settable impl still satisfies a read-only
    # requirement (covariant), so this is the honest minimal contract.
    @property
    def packages(self) -> PackageStore: ...
    @property
    def chunks(self) -> ChunkStore: ...
    @property
    def module_members(self) -> ModuleMemberStore: ...
    @property
    def trees(self) -> DocumentTreeStore: ...
    @property
    def references(self) -> ReferenceStore: ...
    # Untyped here to avoid a hard import of NullVectorStore at the
    # Protocol level (NullVectorStore is a concrete dataclass with no
    # @runtime_checkable Protocol behind it yet). The structural
    # contract is "clear_all() / add_vectors() / remove_vectors() —
    # all async no-op or real" and is enforced by the impls below.
    @property
    def vectors(self) -> object: ...
    # The MultiVectorStore Protocol IS @runtime_checkable so the typed
    # attribute can name it directly — unlike ``vectors`` which still
    # uses ``object`` for the NullVectorStore precedent.
    @property
    def multi_vectors(self) -> MultiVectorStore: ...

    async def __aenter__(self) -> UnitOfWork: ...
    async def __aexit__(self, exc_type, exc, tb) -> bool: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...

    async def delete_all(self) -> None:
        """Wipe every row across every store on this UoW (spec I3).

        Atomic within the surrounding UoW transaction. Lets
        :meth:`IndexingService.clear_all` express its intent in one
        line and removes the per-store ``delete(filter=All(...))``
        gymnastics. Concrete impls order children-before-parents to
        respect future FK constraints.
        """
        ...


@runtime_checkable
class DocumentTreeStore(Protocol):
    """Storage boundary for DocumentNode trees (spec §12.2).

    Persists per-module ``DocumentNode`` trees emitted by extraction so
    ``get_document_tree`` / ``get_package_tree`` queries can serve them
    directly. All methods are async to stay consistent with the rest of
    the storage surface (sub-PR #3 convention); SQLite I/O inside
    implementations wraps ``asyncio.to_thread``.

    ``save_many`` takes a ``package`` kwarg explicitly rather than
    introspecting each tree — the store does not own identity derivation;
    the caller (``IndexingService.reindex_package``) already knows which
    package is being written and must be the single source of truth for
    that mapping.
    """

    async def save_many(
        self,
        trees: Sequence[DocumentNode],
        *,
        package: str,
        uow: UnitOfWork | None = None,
    ) -> None: ...

    async def load(self, package: str, module: str) -> DocumentNode | None: ...

    async def load_all_in_package(self, package: str) -> dict[str, DocumentNode]: ...

    async def exists(self, package: str, module: str) -> bool: ...

    async def delete_for_package(
        self,
        package: str,
        *,
        uow: UnitOfWork | None = None,
    ) -> None: ...

    async def delete_all(self, *, uow: UnitOfWork | None = None) -> None:
        """Drop every row across all packages.

        Used by :meth:`IndexingService.clear_all` so the trees table tracks
        the destructive sweep of the other entity stores — otherwise stale
        trees survive ``clear_all`` and ``LookupService.get_tree`` serves
        cached payloads for re-indexed packages.
        """
        ...


@runtime_checkable
class GraphSearchable(Protocol):
    """Read-only reference-graph view (callers / callees / by-name).

    The read capability a SearchBackend exposes via ``.graph()``. Consumed
    by the ``lookup`` MCP path (ReferenceService), not the retrieval
    pipeline. The write surface lives on :class:`ReferenceStore`.

    ``find_callers`` and ``find_callees`` are CROSS-PACKAGE (no package
    filter) — user intent on ``lookup(target="requests.get",
    show="callers")`` is "who calls this anywhere", not "who calls this
    inside requests". Each returned row carries ``from_package`` so the
    caller can group/render by source package downstream.
    """

    async def find_callers(self, *, target_node_id: str) -> list[NodeReference]: ...

    async def find_callees(self, *, from_node_id: str) -> list[NodeReference]: ...

    async def find_by_name(
        self,
        to_name: str,
        kind: ReferenceKind | None = None,
    ) -> list[NodeReference]: ...


@runtime_checkable
class ReferenceStore(GraphSearchable, Protocol):
    """Storage boundary for the cross-node reference graph (spec §6.2).

    Persists ``NodeReference`` rows captured during extraction so the
    ``callers`` / ``callees`` lookup modes (sub-PR #6 dispatch surface)
    can serve them. All methods are async to stay consistent with the
    rest of the storage surface; SQLite I/O wraps ``asyncio.to_thread``.

    ``find_callers`` and ``find_callees`` are CROSS-PACKAGE (no package
    filter) — user intent on ``lookup(target="requests.get",
    show="callers")`` is "who calls this anywhere", not "who calls this
    inside requests". Each returned row carries ``from_package`` so the
    caller can group/render by source package downstream.

    ``save_many`` resolves PK collisions via ``INSERT ... ON CONFLICT
    (from_package, from_node_id, to_name, kind) DO UPDATE SET
    to_node_id = excluded.to_node_id``. Idempotent re-extraction of the
    same source updates resolution; concurrent re-index across packages
    that share a target name (``requests.get``) won't crash.
    """

    async def save_many(
        self,
        refs: Iterable[NodeReference],
        *,
        package: str,
        uow: UnitOfWork | None = None,
    ) -> None: ...

    async def delete_for_package(
        self,
        package: str,
        *,
        uow: UnitOfWork | None = None,
    ) -> None: ...

    async def delete_all(self, *, uow: UnitOfWork | None = None) -> None: ...

    async def resolve_unresolved(self, qnames: Iterable[str]) -> int:
        """Resolve previously-unresolved refs whose ``to_name`` matches a qname.

        Sets ``to_node_id = to_name`` for every row where
        ``to_node_id IS NULL`` AND ``to_name`` is in ``qnames``. Returns
        the number of rows updated. Idempotent (already-resolved rows
        and unmatched ``to_name`` values are skipped).

        Replaces :class:`IndexingService`'s historical reach-through into
        :attr:`SqliteUnitOfWork._held_conn` (spec C1) so the service stays
        backend-agnostic: any future Postgres / DuckDB adapter satisfies
        this method and the cross-package re-resolution pass keeps working.
        """
        ...


@runtime_checkable
class Embedder(Protocol):
    """One embedder serves both query-time and ingestion-time work.

    Spec §5.2 — concrete classes return their natural shape:
    single-vector embedders (FastEmbed, OpenAI, BGE) return ``np.ndarray``
    (1D, float32); future ColBERT-style embedders return ``MultiVector``
    (list of 1D ``np.ndarray``\\ s). Use
    `pydocs_mcp.models.is_multi_vector(emb)` to disambiguate.
    """

    # Defaults make the attributes discoverable via hasattr(Embedder, ...)
    # for structural / introspection tests. Real implementations override.
    dim: int = 0
    # Identifier string the embedder embedded with — written to
    # ``Package.embedding_model`` by ``EmbedChunksStage`` so a YAML
    # ``embedding.model_name`` swap triggers the re-embed sweep in
    # :func:`find_packages_with_stale_embeddings`.
    model_name: str = ""

    async def embed_query(self, text: str) -> Embedding: ...

    async def embed_chunks(
        self,
        texts: Sequence[str],
    ) -> tuple[Embedding, ...]: ...


@runtime_checkable
class MultiVectorEmbedder(Protocol):
    """Late-interaction (ColBERT-style) embedder: one vector PER TOKEN.

    Distinct from :class:`Embedder` (single pooled vector per text).
    ``embed_query`` / ``embed_chunks`` each return a
    ``MultiVector = list[np.ndarray]`` of length ``n_tokens`` — every
    element is a 1-D float32 ``np.ndarray`` of length ``dim``. The outer
    container is a Python ``list`` (NOT a stacked 2-D array) because
    :func:`pydocs_mcp.models.is_multi_vector` disambiguates the
    ``Embedding`` union via ``isinstance(emb, list)``.

    Implementations MUST L2-normalize each token-vector before returning
    so MaxSim's downstream dot-product IS the cosine — no per-query
    renormalization in ``_maxsim`` (spec Decision C).
    """

    dim: int
    model_name: str

    async def embed_query(self, text: str) -> list[np.ndarray]: ...

    async def embed_chunks(
        self,
        texts: Sequence[str],
    ) -> tuple[list[np.ndarray], ...]: ...


@runtime_checkable
class MultiVectorSearchable(Protocol):
    """Read-only late-interaction view: MaxSim score over a candidate subset.

    The read capability a SearchBackend exposes via ``.multi()``. The
    write surface lives on :class:`MultiVectorStore`, which extends this.
    """

    async def score(
        self,
        query_embedding: list[np.ndarray],
        *,
        subset_chunk_ids: Sequence[int],
        top_k: int,
    ) -> tuple[tuple[int, float], ...]: ...


@runtime_checkable
class MultiVectorStore(MultiVectorSearchable, Protocol):
    """Typed contract for the multi-vector (token-matrix) backend.

    Backend-neutral surface: callers identify chunks by ``chunk_id`` —
    NOT by the backend-internal ``plaid_doc_id``. The concrete UoW
    handles the id translation through the
    ``chunk_multi_vector_ids`` SQLite mapping table inside the same
    transaction as the ``chunks`` writes, so retrieval steps never see
    the backend id space.
    """

    async def add_vectors(
        self,
        ids: Sequence[int],
        embeddings: Sequence[list[np.ndarray]],
    ) -> None: ...

    async def remove_vectors(self, ids: Sequence[int]) -> None: ...

    async def clear_all(self) -> None: ...


class ChatMessage(TypedDict):
    """One message in an LLM chat-completion conversation.

    Mirrors the OpenAI / Anthropic / common LLM API shape: role +
    content. Used by LlmClient.chat() / chat_sync() as input.
    """

    role: Literal["system", "user", "assistant"]
    content: str


@runtime_checkable
class LlmClient(Protocol):
    """LLM chat-completion client.

    Exposes BOTH async ``chat()`` and sync ``chat_sync()`` — LLM calls
    surface in more contexts than embedding calls (the MCP server is
    async, but the CLI debug path, test helpers, and notebooks need a
    sync surface).

    Implementations live under
    ``python/pydocs_mcp/retrieval/llm_clients/``. The factory
    ``build_llm_client(cfg)`` dispatches on ``cfg.provider`` to the
    right concrete (OpenAiLlmClient for v1; SOLID open/closed for
    future providers). The retrieval-owned location reflects current
    usage (only LlmTreeReasoningStep consumes it); if extraction-time
    LLM use lands, the package can be lifted to a neutral location.
    """

    model_name: str

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        """Async chat completion. Returns the assistant's response text."""
        ...

    def chat_sync(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        """Sync chat completion. Same contract as ``chat()``."""
        ...


@runtime_checkable
class ResultFuser(Protocol):
    """Combines N ranked Chunk lists into one fused ranking.

    Spec §5.2. Implementations: RRFResultFuser (reciprocal-rank fusion).
    Future: WeightedSumResultFuser, DistributionBasedResultFuser.
    """

    async def fuse(
        self,
        ranked_lists: Sequence[tuple[Chunk, ...]],
        *,
        limit: int,
    ) -> tuple[Chunk, ...]: ...
