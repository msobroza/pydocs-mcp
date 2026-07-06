"""Pydantic config sub-models — routing, reference graph, search, serve, backend.

Depends only on pydantic — no retrieval-side imports — so extraction-side
consumers (``ReferenceCaptureStage``, ``synthesize_similar_edges``,
``stdlib_qnames``) can be retargeted here later without pulling
settings-layering or pipeline-assembly machinery.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PipelineRouteEntry(BaseModel):
    """One entry in a handler's route list.

    Exactly one of ``predicate`` / ``default`` must be set (spec §5.9, AC #32).
    """

    model_config = ConfigDict(extra="forbid")

    predicate: str | None = None
    default: bool = False
    pipeline_path: Path

    @model_validator(mode="after")
    def _exactly_one_of_predicate_default(self) -> PipelineRouteEntry:
        has_predicate = self.predicate is not None
        if has_predicate and self.default:
            raise ValueError("route entry must set exactly one of predicate or default; both set")
        if not has_predicate and not self.default:
            raise ValueError(
                "route entry must set exactly one of predicate or default; neither set"
            )
        return self


class HandlerConfig(BaseModel):
    """Pipeline handler config — tuple of route entries. Accepts a plain list
    of entries at load time (the shipped YAML writes the list directly)."""

    model_config = ConfigDict(extra="forbid")

    routes: tuple[PipelineRouteEntry, ...]

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_list(cls, data: Any) -> Any:
        if isinstance(data, list):
            return {"routes": data}
        return data


class ReferenceCaptureConfig(BaseModel):
    """Reference-graph capture toggles (sub-PR #5c, §5.3).

    ``kinds`` is typed as ``Literal`` so an unknown value fails at YAML load
    rather than silently producing zero edges. MENTIONS is opt-in: the
    shipped default omits it because regex-over-markdown is lower-precision
    than AST capture and would noise up the graph by default.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    kinds: list[Literal["calls", "imports", "inherits", "mentions"]] = Field(
        default_factory=lambda: ["calls", "imports", "inherits"],
    )


class ReferenceOutputConfig(BaseModel):
    """Per-deployment bounds for the ``lookup(show=callers|callees|inherits)``
    output (sub-PR #5c, §5.3).

    Both keys are read by ``configure_from_app_config`` at server/CLI
    startup and wired into ``LookupInput.limit`` — ``default_limit``
    becomes the field default, ``max_limit`` the validator ceiling. The
    cross-field validator below ensures the default can never exceed the
    ceiling (which would make the LookupInput default unusable).
    """

    model_config = ConfigDict(extra="forbid")

    default_limit: int = Field(50, ge=1)
    max_limit: int = Field(1000, ge=1)

    @model_validator(mode="after")
    def _default_le_max(self) -> ReferenceOutputConfig:
        if self.default_limit > self.max_limit:
            raise ValueError(
                f"reference_graph.output.default_limit={self.default_limit} "
                f"> max_limit={self.max_limit}; the LookupInput default "
                f"would always fail the max validator. Adjust YAML.",
            )
        return self


class ReferenceResolverConfig(BaseModel):
    """Resolver-side knobs for the cross-node reference graph (sub-PR follow-up to #5c).

    YAML keys map 1:1; defaults shipped in defaults/default_config.yaml.

    ``include_stdlib`` toggles whether the resolver loads the bundled
    ``stdlib_qnames.json`` and merges its qnames into the resolver's
    qname universe. When True (default), CALLS edges to stdlib / builtins
    targets (``os.path.join``, ``len``, ``asyncio.to_thread``, ...) resolve
    instead of staying ``to_node_id=None``. AC #15 / sub-PR follow-up to
    #5c — projected +5-10pp on the self-index resolution rate.
    """

    model_config = ConfigDict(extra="forbid")

    include_stdlib: bool = True
    # WHY: when False, the resolver only fires Rule B (exact qname match)
    # — Rule C (strict-suffix-within-package) is skipped. Ablation knob
    # for measuring Rule C's contribution to AC #15 resolution rate.
    strict_suffix: bool = True


class NodeScoresConfig(BaseModel):
    """Index-time node-score precompute toggle (PageRank / community / in-degree).

    Off by default: computing PageRank + Louvain at index time needs the
    ``[graph]`` extra (networkx) and adds a one-shot post-index pass, so it's
    opt-in. When enabled, ``IndexingService.recompute_node_scores`` populates the
    ``node_scores`` table the centrality-prior / community-diversity rerank steps
    read. When disabled the table stays empty and those steps no-op.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class SimilarEdgesConfig(BaseModel):
    """Synthetic embedding-kNN ``similar`` edge generation (index-time).

    Off by default. When enabled, the ``synthesize_similar_edges`` ingestion
    stage adds, per indexed node, ``top_m`` ``kind='similar'`` reference-graph
    edges to its nearest-neighbour symbols by embedding cosine — densifying the
    sparse AST graph so ``graph_expand`` (with ``similar`` in its ``kinds``) can
    reach semantically-related code with no call/inherit edge.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    top_m: int = Field(5, ge=1, le=25)


# Single source of truth for the blast-radius traversal depth default —
# referenced by both ``ImpactConfig.max_depth`` (the YAML-tunable canonical
# source) and ``LookupService.impact_max_depth`` (the direct-construction
# fallback), so the literal lives in exactly one place.
_DEFAULT_IMPACT_MAX_DEPTH = 3


class ImpactConfig(BaseModel):
    """Bounded reverse-traversal depth for ``lookup(show="impact")``.

    ``impact`` answers "what transitively calls X / what breaks if I change X"
    by walking the reference graph BACKWARD from the target. ``max_depth``
    bounds that walk (and is termination-critical for cyclic graphs). It is a
    server-side tunable, NOT an MCP parameter — the client only sends the fixed
    ``lookup(target, show)`` surface.
    """

    model_config = ConfigDict(extra="forbid")

    max_depth: int = Field(_DEFAULT_IMPACT_MAX_DEPTH, ge=1, le=6)


# Single source of truth for the smart-context defaults — referenced by both
# ``ContextConfig`` (the YAML canonical source) and the ``LookupService``
# fallback fields, so the literals live in exactly one place.
_DEFAULT_CONTEXT_MAX_DEPTH = 2
_DEFAULT_CONTEXT_TOKEN_BUDGET = 2048


class ContextConfig(BaseModel):
    """Bounds for ``lookup(show="context")`` (smart-context packing).

    ``context`` walks the reference graph FORWARD from the target (its
    dependency closure — what it calls) and packs the closure under one token
    budget at graded fidelity (focus = full source, ring = signatures, rest =
    outline). ``max_depth`` bounds the walk; ``token_budget`` caps the packed
    output. Both are server-side tunables, NOT MCP parameters.
    """

    model_config = ConfigDict(extra="forbid")

    max_depth: int = Field(_DEFAULT_CONTEXT_MAX_DEPTH, ge=1, le=6)
    token_budget: int = Field(_DEFAULT_CONTEXT_TOKEN_BUDGET, ge=128, le=100_000)


class ReferenceGraphConfig(BaseModel):
    """Composite — capture toggles + output bounds (sub-PR #5c, §5.3).

    Lives under ``AppConfig.reference_graph``. Two reasons it's a typed
    sub-model rather than two flat keys: (1) namespaces the YAML so future
    reference-graph tunables (resolver thresholds, etc.) get an obvious
    home, and (2) the ``output`` cross-field validator needs an enclosing
    model to run on.
    """

    model_config = ConfigDict(extra="forbid")

    capture: ReferenceCaptureConfig = Field(default_factory=ReferenceCaptureConfig)
    output: ReferenceOutputConfig = Field(default_factory=ReferenceOutputConfig)
    resolver: ReferenceResolverConfig = Field(default_factory=ReferenceResolverConfig)
    node_scores: NodeScoresConfig = Field(default_factory=NodeScoresConfig)
    similar_edges: SimilarEdgesConfig = Field(default_factory=SimilarEdgesConfig)
    impact: ImpactConfig = Field(default_factory=ImpactConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)


class SearchOutputConfig(BaseModel):
    """Per-deployment bounds for the ``search`` MCP tool's ``limit``.

    Parity with :class:`ReferenceOutputConfig` — two YAML knobs (default
    and ceiling) pushed into ``SearchInput.limit`` via
    ``configure_from_app_config``. Kept as a separate sub-model (rather
    than reusing ``reference_graph.output``) because the two surfaces are
    conceptually distinct: ``search`` returns chunks, ``lookup`` returns
    references, and their historical defaults differ (10 vs 50). Keeping
    independent YAML keys lets deployments tune one without the other.
    """

    model_config = ConfigDict(extra="forbid")

    default_limit: int = Field(10, ge=1)
    max_limit: int = Field(1000, ge=1)

    @model_validator(mode="after")
    def _default_le_max(self) -> SearchOutputConfig:
        if self.default_limit > self.max_limit:
            raise ValueError(
                f"search.output.default_limit={self.default_limit} "
                f"> max_limit={self.max_limit}; the SearchInput default "
                f"would always fail the max validator. Adjust YAML.",
            )
        return self


class SearchConfig(BaseModel):
    """Namespace for ``search``-tool tunables (parity with ``reference_graph``)."""

    model_config = ConfigDict(extra="forbid")

    output: SearchOutputConfig = Field(default_factory=SearchOutputConfig)


# Single source of truth for the get_symbol(depth="source") line cap — the
# YAML-canonical default lives here; ``SymbolSourceService`` carries its own
# construction-time fallback constant (wired config→service in a later task).
_DEFAULT_MAX_LINES_SYMBOL_SOURCE = 400


class SymbolSourceConfig(BaseModel):
    """Line cap for ``get_symbol(depth="source")`` verbatim output (spec §D7).

    Bounds how many source lines the per-symbol source view renders before it
    truncates with a recovery note pointing at the on-disk file (the terminal
    §D7 recovery step). Server-side tunable, NOT an MCP parameter — the fixed
    surface stays at ``search`` + ``lookup``/``get_symbol``.
    """

    model_config = ConfigDict(extra="forbid")

    max_lines: int = Field(_DEFAULT_MAX_LINES_SYMBOL_SOURCE, ge=20, le=5000)


class EnvelopeConfig(BaseModel):
    """Freshness envelope on every MCP/CLI response (spec §D4).

    ``head_check_ttl_seconds`` bounds how often the probe re-reads
    ``.git/HEAD`` + ``index_metadata`` — 5s keeps a chatty agent session at
    ~1 stat-burst per turn without ever serving minutes-stale warnings.
    """

    enabled: bool = True
    head_check_ttl_seconds: float = Field(5.0, ge=0.0)


class NextPointersConfig(BaseModel):
    """Per-hit next-step pointer rendering toggle (spec §D5)."""

    enabled: bool = True


class OutputConfig(BaseModel):
    """Response-convention toggles shared by every tool output."""

    envelope: EnvelopeConfig = EnvelopeConfig()
    next_pointers: NextPointersConfig = NextPointersConfig()


class OverviewConfig(BaseModel):
    """get_overview card caps (spec §D17) — list caps keep the card inside token budgets."""

    model_config = ConfigDict(extra="forbid")

    max_modules: int = Field(20, ge=1, le=200)
    max_communities: int = Field(10, ge=1, le=50)


# Single source of truth for the debounce bounds (CLAUDE.md §"Default
# values: single source of truth"). Used both for the pydantic Field
# default AND the cross-field validator's ceiling check below.
_DEFAULT_WATCH_DEBOUNCE_MS = 500
_MAX_WATCH_DEBOUNCE_MS = 60_000


class WatchConfig(BaseModel):
    """File-watcher tunables for ``pydocs-mcp serve --watch``.

    Per CLAUDE.md §"MCP API surface vs YAML configuration": these are
    deployment-time knobs, NOT MCP tool params. The MCP surface stays at
    the fixed 2 tools (``search``, ``lookup``); ``--watch`` is the only
    CLI flag and it overrides ``enabled``.

    ``debounce_ms`` is bounded: zero/negative would fire on every byte
    of a slow-write editor (atomic-save sequences fire 2-3 events per
    save — debounce naturally collapses them); >60_000 ms means the
    user is better off re-running ``pydocs-mcp index .`` manually
    (spec §6 R7).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    debounce_ms: int = Field(default=_DEFAULT_WATCH_DEBOUNCE_MS, ge=1)
    # tuple so dataclass-style sharing across threads stays immutable
    extensions: tuple[str, ...] = (".py", ".md", ".ipynb")
    ignore_globs: tuple[str, ...] = (
        "**/__pycache__/**",
        "**/.git/**",
        "**/.venv/**",
        "**/node_modules/**",
        "**/.pytest_cache/**",
        "**/*.pyc",
    )

    @model_validator(mode="after")
    def _validate_debounce_bound(self) -> WatchConfig:
        if self.debounce_ms >= _MAX_WATCH_DEBOUNCE_MS:
            raise ValueError(
                f"serve.watch.debounce_ms={self.debounce_ms} must be "
                f"< {_MAX_WATCH_DEBOUNCE_MS} ms. Larger values defeat "
                "the purpose of a live watcher; re-run "
                "`pydocs-mcp index .` manually instead."
            )
        return self


class ServeConfig(BaseModel):
    """Namespace for ``serve``-command tunables (parity with ``search`` /
    ``reference_graph``). Only ``watch`` lives here today; future serve-
    side knobs (HTTP transport options, etc.) get an obvious home."""

    model_config = ConfigDict(extra="forbid")

    watch: WatchConfig = Field(default_factory=WatchConfig)


class SearchBackendConfig(BaseModel):
    """Which storage backend serves retrieval capabilities (spec §8.1).

    ``dim`` / ``bit_width`` are NOT duplicated here — they stay sourced from
    :class:`EmbeddingConfig` (single source of truth). Remote-backend blocks
    (qdrant/elasticsearch) are documented extension points, not parsed yet.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = "sqlite_composite"

    def compute_identity(self) -> str:
        """Identity string folded into the pipeline hash (spec §10)."""
        return f"search_backend={self.kind}"
