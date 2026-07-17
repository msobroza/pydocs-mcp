"""Pydantic config sub-models — routing, reference graph, search, serve, backend.

Depends only on pydantic — no retrieval-side imports — so extraction-side
consumers (``ReferenceCaptureStage``, ``synthesize_similar_edges``,
``stdlib_qnames``) can be retargeted here later without pulling
settings-layering or pipeline-assembly machinery.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


# Single source of truth for the shipped capture-kind defaults. MENTIONS is
# opt-in (regex-over-markdown, lower-precision than AST capture); SIMILAR and
# GOVERNS are produced by their own index-time stages, not by capture.
_DEFAULT_CAPTURE_KINDS = ("calls", "imports", "inherits")


def _validated_reference_kinds(value: tuple[str, ...]) -> tuple[str, ...]:
    """Shared membership rule for every ``kinds`` config field.

    Validates dynamically against :class:`ReferenceKind` so a typo'd kind
    fails at ``AppConfig.load`` while a new enum member is accepted with no
    config edit (Open/Closed). Deferred import keeps this module
    pydantic-only at import time (see module docstring).
    """
    from pydocs_mcp.extraction.reference_kind import ReferenceKind

    allowed = {str(k) for k in ReferenceKind}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"unknown reference kind(s) {unknown}; have {sorted(allowed)}")
    return value


class ReferenceCaptureConfig(BaseModel):
    """Reference-graph capture toggles (sub-PR #5c, §5.3).

    ``kinds`` is validated against :class:`ReferenceKind` membership so an
    unknown value fails at YAML load rather than silently producing zero
    edges — and a new enum member is accepted with no config edit (same
    rule as ``CrossRepoConfig.kinds``). MENTIONS is opt-in: the shipped
    default omits it because regex-over-markdown is lower-precision than
    AST capture and would noise up the graph by default.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    kinds: tuple[str, ...] = _DEFAULT_CAPTURE_KINDS

    @field_validator("kinds")
    @classmethod
    def _kinds_are_reference_kinds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """A typo'd kind fails at AppConfig.load, never mid-capture."""
        return _validated_reference_kinds(value)


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
# Skeleton body-budget default (spec §D6). Canonical for ``ContextConfig`` +
# the ``LookupService`` fallback field; ``application.formatting`` imports THIS
# constant so the ``format_context(body_ratio=...)`` default never drifts.
_DEFAULT_CONTEXT_RENDER: Literal["skeleton", "full"] = "skeleton"
_DEFAULT_SKELETON_BODY_RATIO = 0.35


class ContextConfig(BaseModel):
    """Bounds for ``lookup(show="context")`` (smart-context packing).

    ``context`` walks the reference graph FORWARD from the target (its
    dependency closure — what it calls) and packs the closure under one token
    budget. ``max_depth`` bounds the walk; ``token_budget`` caps the packed
    output. ``render`` selects the packing strategy: ``"skeleton"`` (the
    default per §D6) renders every node's signature and spends only
    ``skeleton_body_ratio`` of the budget on FULL bodies of the most-central
    nodes; ``"full"`` uses the legacy hop-graded fidelity (focus = full source,
    ring = signature, rest = outline). All server-side tunables, NOT MCP params.
    """

    model_config = ConfigDict(extra="forbid")

    max_depth: int = Field(_DEFAULT_CONTEXT_MAX_DEPTH, ge=1, le=6)
    token_budget: int = Field(_DEFAULT_CONTEXT_TOKEN_BUDGET, ge=128, le=100_000)
    render: Literal["skeleton", "full"] = _DEFAULT_CONTEXT_RENDER
    skeleton_body_ratio: float = Field(_DEFAULT_SKELETON_BODY_RATIO, gt=0.0, le=1.0)


# Single source of truth for cross-repo linking defaults (spec 2026-07-11 +
# Amendment A1). The shipped YAML restates them for user-facing clarity.
_DEFAULT_CROSS_REPO_ENABLED = True  # A1.8: default-on (inert for single-bundle serving)
_DEFAULT_CROSS_REPO_LINK_ON_SERVE = True
_DEFAULT_CROSS_REPO_MATCH_SCOPE: Literal["project_only", "all_packages"] = "project_only"
_DEFAULT_CROSS_REPO_KINDS = ("calls", "imports", "inherits", "governs")  # A1.2
_DEFAULT_CROSS_REPO_MAX_PROJECTS_PER_WALK = 8
_DEFAULT_CROSS_REPO_WORKSPACE_SCORES = True  # A1.1 (in_degree always; pagerank [graph]-gated)
_DEFAULT_CROSS_REPO_ALIAS_RESOLUTION: Literal["imports_graph", "off"] = "imports_graph"  # A1.3
_DEFAULT_CROSS_REPO_SIMILAR_TOP_K = 5
_DEFAULT_CROSS_REPO_SIMILAR_MIN_SCORE = 0.6


class CrossRepoSimilarConfig(BaseModel):
    """Bounds for opt-in cross-repo SIMILAR linking (spec §A1.2)."""

    model_config = ConfigDict(extra="forbid")

    top_k: int = Field(_DEFAULT_CROSS_REPO_SIMILAR_TOP_K, ge=1, le=50)
    min_score: float = Field(_DEFAULT_CROSS_REPO_SIMILAR_MIN_SCORE, ge=0.0, le=1.0)


class CrossRepoConfig(BaseModel):
    """Workspace-level cross-repo reference linking (spec 2026-07-11 + A1).

    Server-side deployment tunables, NOT MCP parameters — ``get_references``
    keeps its pinned six-tool-surface signature; enabling/tuning linking is a
    YAML-only concern (CLAUDE.md §"MCP API surface vs YAML configuration").
    Inert with a single loaded bundle regardless of ``enabled`` (N7).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = _DEFAULT_CROSS_REPO_ENABLED
    link_on_serve: bool = _DEFAULT_CROSS_REPO_LINK_ON_SERVE
    match_scope: Literal["project_only", "all_packages"] = _DEFAULT_CROSS_REPO_MATCH_SCOPE
    kinds: tuple[str, ...] = _DEFAULT_CROSS_REPO_KINDS
    max_projects_per_walk: int = Field(_DEFAULT_CROSS_REPO_MAX_PROJECTS_PER_WALK, ge=1, le=32)
    overlay_dir: Path | None = None  # explicit overlay placement override (§3.1)
    workspace_scores: bool = _DEFAULT_CROSS_REPO_WORKSPACE_SCORES
    alias_resolution: Literal["imports_graph", "off"] = _DEFAULT_CROSS_REPO_ALIAS_RESOLUTION
    similar: CrossRepoSimilarConfig = Field(default_factory=CrossRepoSimilarConfig)

    @field_validator("kinds")
    @classmethod
    def _kinds_are_reference_kinds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """A typo'd kind fails at AppConfig.load, never mid-link."""
        return _validated_reference_kinds(value)


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
    cross_repo: CrossRepoConfig = Field(default_factory=CrossRepoConfig)


class SearchOutputConfig(BaseModel):
    """Per-deployment bounds for the ``search_codebase`` MCP tool's ``limit``.

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


class FilesConfig(BaseModel):
    """Per-deployment bounds for the filesystem tools (``grep``/``glob``/``read_file``).

    Field defaults are the single source of truth; ``default_config.yaml``
    restates them for user-facing clarity (tool-contracts.md §3.7-3.9:
    ``head_limit``/``limit`` are "YAML-wired" — omitted by the client means
    these defaults). ``max_head_limit`` is the ceiling applied to
    client-supplied caps so one request can't demand an unbounded response.
    """

    model_config = ConfigDict(extra="forbid")

    grep_head_limit: int = Field(default=100, ge=1)
    glob_head_limit: int = Field(default=100, ge=1)
    read_limit: int = Field(default=2000, ge=1)
    max_head_limit: int = Field(default=10000, ge=1)

    @model_validator(mode="after")
    def _defaults_le_max(self) -> FilesConfig:
        # Same guard as SearchOutputConfig._default_le_max: a YAML default
        # above the ceiling would make every defaulted call exceed the cap.
        for name in ("grep_head_limit", "glob_head_limit", "read_limit"):
            value: int = getattr(self, name)
            if value > self.max_head_limit:
                raise ValueError(
                    f"files.{name}={value} > max_head_limit="
                    f"{self.max_head_limit}; the YAML default would always "
                    f"exceed the client-cap ceiling. Adjust YAML.",
                )
        return self


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

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    head_check_ttl_seconds: float = Field(5.0, ge=0.0)


class NextPointersConfig(BaseModel):
    """Per-hit next-step pointer rendering toggle (spec §D5)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class OutputConfig(BaseModel):
    """Response-convention toggles shared by every tool output."""

    model_config = ConfigDict(extra="forbid")

    envelope: EnvelopeConfig = Field(default_factory=EnvelopeConfig)
    next_pointers: NextPointersConfig = Field(default_factory=NextPointersConfig)


class GitActivityConfig(BaseModel):
    """§D17 block 9 (Recent activity) index-time aggregation knobs.

    ``enabled`` gates the extra index-end ``git log`` spawn + the aggregate
    write; ``window_days`` bounds how far back commits count toward the block.
    Deployment-time tuning, NOT an MCP param (CLAUDE.md §"MCP API surface").
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    window_days: int = Field(90, ge=1, le=3650)


class LlmSummaryConfig(BaseModel):
    """§D17 block 2 (Architecture) opt-in LLM summary knob.

    ``enabled`` gates the index-time LLM call that generates the architecture
    orientation paragraph; default OFF because it costs an LLM round-trip per
    index whose module set changed (fingerprint-cached: unchanged module set →
    no call). Deployment-time tuning, NOT an MCP param (CLAUDE.md §"MCP API
    surface"). The LLM provider / model come from the top-level ``llm:`` section.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class OverviewConfig(BaseModel):
    """get_overview card caps (spec §D17) — list caps keep the card inside token budgets."""

    model_config = ConfigDict(extra="forbid")

    max_modules: int = Field(20, ge=1, le=200)
    max_communities: int = Field(10, ge=1, le=50)
    git_activity: GitActivityConfig = Field(default_factory=GitActivityConfig)
    llm_summary: LlmSummaryConfig = Field(default_factory=LlmSummaryConfig)


class DecisionsOutputConfig(BaseModel):
    """Per-deployment bounds for the ``get_why`` decision-read output (spec §D9/§D11).

    Parity with :class:`ReferenceOutputConfig` / :class:`SearchOutputConfig` —
    two YAML knobs (``default_limit`` / ``max_limit``) bounding how many decision
    records the real ``DecisionService`` renders. Kept as its own sub-model (not
    reusing ``search.output``) because decisions are a distinct surface with its
    own historical default (10 records vs the search default). The cross-field
    validator below ensures the default can never exceed the ceiling.
    """

    model_config = ConfigDict(extra="forbid")

    default_limit: int = Field(10, ge=1)
    max_limit: int = Field(100, ge=1)

    @model_validator(mode="after")
    def _default_le_max(self) -> DecisionsOutputConfig:
        if self.default_limit > self.max_limit:
            raise ValueError(
                f"decisions.output.default_limit={self.default_limit} "
                f"> max_limit={self.max_limit}; the decision-read default "
                f"would always exceed the ceiling. Adjust YAML.",
            )
        return self


class DecisionsConfig(BaseModel):
    """Namespace for ``get_why`` decision-read tunables (parity with ``search``).

    Distinct from ``decision_capture`` (index-time mining): this owns the
    *read-side* output bounds. Only ``output`` lives here today; future
    read-side knobs (rendering caps, dashboard limits) get an obvious home.
    """

    model_config = ConfigDict(extra="forbid")

    output: DecisionsOutputConfig = Field(default_factory=DecisionsOutputConfig)


# Single source of truth for the default decision-mining source order (spec
# §D8). Referenced by ``DecisionCaptureConfig.sources`` default_factory so the
# literal tuple lives in exactly one place; YAML restates it for user clarity.
_DEFAULT_DECISION_SOURCES = (
    "adr_files",
    "inline_markers",
    "commit_messages",
    "changelog",
    "docs_prose",
)


class LlmStructuringConfig(BaseModel):
    """Default-OFF LLM structuring gate for mined decisions (spec §D12).

    When enabled, the ``LlmClient`` structures decision fields; the grounding
    gate drops any field not traceable to verbatim evidence at or above
    ``grounding_threshold``. Off by default — deterministic mining ships the
    verbatim record with no LLM in the index path.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    grounding_threshold: float = Field(0.60, gt=0.0, le=1.0)
    batch_size: int = Field(5, ge=1, le=20)


class CommitMessagesConfig(BaseModel):
    """Bounds for the ``commit_messages`` mining source (spec §D8).

    ``max_commits`` caps the git-log window; ``timeout_seconds`` bounds the
    subprocess so a slow/hung git never stalls indexing.
    """

    model_config = ConfigDict(extra="forbid")

    max_commits: int = Field(2000, ge=1)
    timeout_seconds: float = Field(30.0, gt=0.0)


class DocsProseConfig(BaseModel):
    """Bounds for the ``docs_prose`` mining source (spec §D8)."""

    model_config = ConfigDict(extra="forbid")

    max_files: int = Field(10, ge=1, le=100)
    max_kb_per_file: int = Field(50, ge=1)


class InlineMarkersConfig(BaseModel):
    """Context window for the ``inline_markers`` mining source (spec §D8).

    ``context_lines`` bounds how many lines around a ``# WHY:`` / ``# DECISION:``
    marker are captured as verbatim evidence.
    """

    model_config = ConfigDict(extra="forbid")

    context_lines: int = Field(20, ge=0, le=200)


class DecisionCaptureConfig(BaseModel):
    """Index-time decision-mining toggles (spec §D8).

    Drives the ``capture_decisions`` ingestion stage: which deterministic
    sources run, the merge/dedupe Jaccard threshold, per-source bounds, and the
    default-off LLM structuring gate. Per CLAUDE.md §"MCP API surface vs YAML
    configuration": all deployment-time knobs, NOT MCP tool params — the
    six task-shaped tools stay fixed.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # WHY: closed source set — each source IS an implemented miner, so the
    # Literal enumerating them is essential, not drift-prone. A misspelled
    # overlay value (e.g. ``adr_file`` missing the trailing ``s``) fails at
    # YAML load rather than silently mining nothing from that source.
    # ``extra="forbid"`` only validates keys, not list values, so the
    # Literal is what closes the hazard here.
    sources: list[
        Literal[
            "adr_files",
            "inline_markers",
            "commit_messages",
            "changelog",
            "docs_prose",
        ]
    ] = Field(default_factory=lambda: list(_DEFAULT_DECISION_SOURCES))
    merge_jaccard: float = Field(0.85, gt=0.0, le=1.0)
    inline_markers: InlineMarkersConfig = Field(default_factory=InlineMarkersConfig)
    commit_messages: CommitMessagesConfig = Field(default_factory=CommitMessagesConfig)
    docs_prose: DocsProseConfig = Field(default_factory=DocsProseConfig)
    include_deps: bool = False
    llm_structuring: LlmStructuringConfig = Field(default_factory=LlmStructuringConfig)


# Single source of truth for the debounce bounds (CLAUDE.md §"Default
# values: single source of truth"). Used both for the pydantic Field
# default AND the cross-field validator's ceiling check below.
_DEFAULT_WATCH_DEBOUNCE_MS = 500
_MAX_WATCH_DEBOUNCE_MS = 60_000


class WatchConfig(BaseModel):
    """File-watcher tunables for ``pydocs-mcp serve --watch``.

    Per CLAUDE.md §"MCP API surface vs YAML configuration": these are
    deployment-time knobs, NOT MCP tool params. The MCP surface stays
    fixed at the six task-shaped tools; watching is enabled by either
    switch — the CLI ``--watch`` flag or ``enabled: true`` here.

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
