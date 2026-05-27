"""Runtime config — pydantic-settings + YAML source layering (spec §5.9)."""
from __future__ import annotations

import hashlib
import importlib.resources
import os
from collections.abc import Mapping
from contextvars import ContextVar
from functools import cache, cached_property
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from pydocs_mcp.extraction.config import ExtractionConfig

# Side-effect imports: populate stage/formatter registries via decorators.
from pydocs_mcp.retrieval import formatters as _formatters  # noqa: F401
from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
from pydocs_mcp.retrieval.serialization import BuildContext

# WHY: ``RouteCase`` / ``RouteStep`` are imported lazily inside
# :func:`_build_handler_pipeline` rather than top-level. After the
# Task-9 corpse removal removed the eager ``retrievers`` side-effect
# import from :mod:`pydocs_mcp.retrieval.__init__`, the import chain
# ``retrieval.steps`` → ``token_budget`` → ``application`` →
# ``storage`` → ``extraction.reference_capture`` → ``retrieval.config``
# hits this module before ``retrieval.steps.__init__`` has finished
# binding ``RouteCase`` / ``RouteStep``. Deferring the import to
# function scope breaks the cycle without changing call-site shape.

# ── Tunable user-config path override ───────────────────────────────────
#
# ``AppConfig.load(explicit_path=...)`` sets this ambient override so the
# class-method ``settings_customise_sources`` can pick it up when pydantic
# instantiates the settings. Scoped to a ContextVar so concurrent async
# callers (tests in particular) don't clobber each other.
_USER_CONFIG_PATH_OVERRIDE: ContextVar[Path | None] = ContextVar(
    "_USER_CONFIG_PATH_OVERRIDE", default=None,
)

# Cached resolution of the user-config path for the current ``AppConfig.load``
# call. ``settings_customise_sources`` and ``load`` both used to invoke
# :func:`_resolved_user_config_path` (which touches env + cwd + home); the
# ContextVar lets us resolve once in ``load`` and reuse inside the pydantic
# source hook without re-running the lookup chain.
#
# The default is the ``_UNSET`` sentinel (not ``None``) because ``None`` is a
# legitimate resolved value ("no user config found"). Without the sentinel we
# couldn't tell "not cached yet" from "resolved to None".
_UNSET: object = object()
_RESOLVED_USER_CONFIG_PATH: ContextVar[Path | None | object] = ContextVar(
    "_RESOLVED_USER_CONFIG_PATH", default=_UNSET,
)


class PipelineRouteEntry(BaseModel):
    """One entry in a handler's route list.

    Exactly one of ``predicate`` / ``default`` must be set (spec §5.9, AC #32).
    """

    model_config = ConfigDict(extra="forbid")

    predicate: str | None = None
    default: bool = False
    pipeline_path: Path

    @model_validator(mode="after")
    def _exactly_one_of_predicate_default(self) -> "PipelineRouteEntry":
        has_predicate = self.predicate is not None
        if has_predicate and self.default:
            raise ValueError(
                "route entry must set exactly one of predicate or default; both set"
            )
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
    def _default_le_max(self) -> "ReferenceOutputConfig":
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
    def _default_le_max(self) -> "SearchOutputConfig":
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
    def _validate_debounce_bound(self) -> "WatchConfig":
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


# Known-model dim lookup. Add entries as the model-selection follow-up
# PR locks in benchmarked models. Models not in this table skip the
# check (caller is on the hook).
_KNOWN_MODEL_DIMS: dict[str, int] = {
    # FastEmbed
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5":  768,
    "BAAI/bge-large-en-v1.5": 1024,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    # OpenAI (text-embedding-3-* default dims; can be reduced via .dimensions)
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class EmbeddingConfig(BaseModel):
    """Embedding + vector-quantization config (spec §5.10).

    YAML-tunable; no MCP tool params (per CLAUDE.md §"MCP API surface
    vs YAML configuration").
    """

    model_config = ConfigDict(extra="forbid")

    provider:   Literal["fastembed", "openai"] = "fastembed"
    model_name: str = "BAAI/bge-small-en-v1.5"
    dim:        int = Field(default=384, ge=1)
    batch_size: int = Field(default=32, ge=1)
    # TurboQuant scalar-quantization bit width. 4 is the sweet spot per
    # turbovec README — ~16x compression with minimal recall loss on
    # 384-1536 dim embeddings. Tune up to 8 for higher quality, down to
    # 2 for max compression.
    bit_width:  int = Field(default=4, ge=1, le=8)

    @field_validator("dim")
    @classmethod
    def _validate_dim_multiple_of_8(cls, v: int) -> int:
        # WHY: turbovec's ``IdMapIndex`` asserts ``dim % 8 == 0`` (see
        # turbovec/src/lib.rs). Without this validator, a YAML setting
        # like ``embedding.dim: 100`` would load fine and only blow up
        # at first write — far from the misconfiguration. Failing at
        # config-load surfaces it next to the offending line.
        if v % 8 != 0:
            raise ValueError(
                f"embedding.dim={v} must be a multiple of 8 (TurboQuant "
                "IdMapIndex constraint). Common values: 384, 512, 768, "
                "1024, 1536, 3072."
            )
        return v

    @model_validator(mode="after")
    def _validate_dim_matches_known_model(self) -> "EmbeddingConfig":
        # WHY: without this check, setting ``model_name: BAAI/bge-base-en-v1.5``
        # (768-dim) while leaving ``dim=384`` (the shipped default) silently
        # produces a corrupt vector store at query time — every embedded
        # vector lands in a column dimensioned for the wrong model. Failing
        # at config-load time surfaces the misconfiguration immediately.
        # Unknown models skip the check so custom / locally-finetuned models
        # remain usable (caller is on the hook for matching dim).
        expected = _KNOWN_MODEL_DIMS.get(self.model_name)
        if expected is not None and self.dim != expected:
            raise ValueError(
                f"embedding.dim={self.dim} does not match the known "
                f"dimension of {self.model_name!r} (expected {expected}). "
                "Either set dim to the model's native dimension or "
                "remove the model from the known-dims lookup if you "
                "intend a custom configuration."
            )
        return self

    def compute_pipeline_hash(self) -> str:
        """SHA-256 of embedder fields that affect vector identity.

        ``batch_size`` is deliberately excluded — it affects throughput,
        not vector contents. Future preprocessing flags
        (``normalize_whitespace``, etc.) get added here as they're
        introduced. Pipe-separated to keep the hash input human-readable
        in a debugger; the field set is small enough that no escaping
        is required (``provider`` / ``bit_width`` are bounded enums /
        ints, ``model_name`` cannot legally contain a pipe).
        """
        identity = "|".join([
            self.provider,
            self.model_name,
            str(self.dim),
            str(self.bit_width),
        ])
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class LlmConfig(BaseModel):
    """LLM chat-completion client configuration.

    Architectural twin of ``EmbeddingConfig`` — same shape (provider,
    model_name, tuning params), used by ``build_llm_client(cfg)`` to
    construct the right concrete client. Defaults selected for cost
    efficiency: gpt-4o-mini is OpenAI's cheap-but-capable model and the
    right baseline for a retrieval re-ranking step where calls are
    frequent but small.
    """

    provider: Literal["openai"] = "openai"
    model_name: str = "gpt-4o-mini"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    api_key: str | None = None  # None -> SDK reads OPENAI_API_KEY env var


class AppConfig(BaseSettings):
    """Runtime configuration.

    All defaults ship via ``defaults/default_config.yaml`` — there are no
    Python-level defaults on YAML-backed fields (spec §5.9, AC #14). The
    source layering (shipped baseline → user YAML → env → init) is wired
    in ``settings_customise_sources``.
    """

    cache_dir: Path
    log_level: str
    metadata_schemas: Mapping[str, tuple[str, ...]]
    pipelines: Mapping[str, HandlerConfig]
    # Sub-PR #5: extraction-pipeline settings — chunker registry, discovery
    # scope, member caps, ingestion pipeline override. Defaults are shipped
    # in ``defaults/default_config.yaml`` so user YAMLs need only override
    # the keys they care about.
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    # Sub-PR #5c: reference-graph capture toggles + output bounds. Read by
    # ``ReferenceCaptureStage`` (enabled/kinds) and ``configure_from_app_config``
    # (default_limit/max_limit → LookupInput.limit). Per CLAUDE.md §"MCP API
    # surface vs YAML configuration": these are pipeline-tuning knobs, NOT
    # MCP tool params. The MCP surface (search, lookup) stays fixed.
    reference_graph: ReferenceGraphConfig = Field(default_factory=ReferenceGraphConfig)
    # Post-trilogy polish: parallel YAML knobs for the ``search`` MCP tool.
    # Same wiring pattern as ``reference_graph.output`` — pushed into
    # ``SearchInput.limit`` via ``configure_from_app_config``. The MCP
    # surface stays fixed; only deployment-time bounds are configurable.
    search: SearchConfig = Field(default_factory=SearchConfig)
    # Serve-command tunables (file watcher today; future HTTP transport
    # options tomorrow). Per CLAUDE.md §"MCP API surface vs YAML
    # configuration": CLI ``--watch`` overrides ``serve.watch.enabled``;
    # no MCP tool param. The MCP surface (search, lookup) stays fixed.
    serve: ServeConfig = Field(default_factory=ServeConfig)
    # Hybrid-search foundation (spec §5.10): embedding provider /
    # model / dim / batch / TurboQuant bit-width. Consumed by
    # ``build_embedder()`` and ``EmbedChunksStage`` later in the
    # hybrid-search PR. Per CLAUDE.md §"MCP API surface vs YAML
    # configuration": embedding model choice is a pipeline-tuning knob,
    # NOT an MCP tool param — the MCP surface stays fixed.
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    # LLM chat-completion client config (Task 3 / AC-2). Architectural twin
    # of ``embedding`` — provider/model_name/tuning knobs consumed by
    # ``build_llm_client(cfg)`` to construct the right concrete client. Per
    # CLAUDE.md §"MCP API surface vs YAML configuration": LLM model choice
    # is a pipeline-tuning knob, NOT an MCP tool param — the MCP surface
    # stays fixed.
    llm: LlmConfig = Field(default_factory=LlmConfig)
    # Resolved user-config path captured at load time — powers the
    # pipeline_path allowlist so that a user-supplied ``./my_pipeline.yaml``
    # next to an explicit ``--config`` file resolves, while paths outside
    # the shipped pipelines + user-config directory are rejected. Populated
    # by ``AppConfig.load`` via ``object.__setattr__`` (pydantic doesn't
    # let us declare this as a normal field without round-tripping it
    # through YAML).
    #
    # Read-only from the outside — treat it as private state.

    # WHY env_nested_delimiter='__': without it, nested env-var overrides
    # like ``PYDOCS_EMBEDDING__MODEL_NAME=...`` would silently no-op —
    # pydantic-settings only routes env vars into sub-models when a
    # delimiter is configured. The ``__`` (double underscore) separator
    # is the pydantic-settings convention.
    model_config = SettingsConfigDict(
        env_prefix="PYDOCS_", env_nested_delimiter="__", extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        shipped_path = _shipped_default_config_path()
        shipped_source = YamlConfigSettingsSource(settings_cls, yaml_file=shipped_path)
        # Reuse the path ``load`` already resolved when available — avoids
        # re-running the env/cwd/home lookup chain on every ``AppConfig.load``.
        cached = _RESOLVED_USER_CONFIG_PATH.get()
        user_path = cached if cached is not _UNSET else _resolved_user_config_path()
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        if user_path is not None and user_path.exists():
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=user_path))
        sources.append(shipped_source)
        return tuple(sources)

    @classmethod
    def load(cls, explicit_path: Path | None = None) -> "AppConfig":
        """Resolve the user's config path and construct the layered AppConfig.

        ``explicit_path`` wins over env / cwd / XDG home for the user layer;
        the shipped baseline always applies underneath.
        """
        token = _USER_CONFIG_PATH_OVERRIDE.set(explicit_path)
        resolved: Path | None = _resolved_user_config_path()
        resolved_token = _RESOLVED_USER_CONFIG_PATH.set(resolved)
        try:
            instance = cls()
        finally:
            _RESOLVED_USER_CONFIG_PATH.reset(resolved_token)
            _USER_CONFIG_PATH_OVERRIDE.reset(token)
        # Stash the resolved user-config path so downstream pipeline
        # assembly can derive the security allowlist without re-reading
        # the ContextVar (which gets reset above).
        object.__setattr__(instance, "_effective_user_config_path", resolved)
        return instance

    def _user_config_path(self) -> Path | None:
        """Return the user-config path captured at ``load`` time, if any."""
        return getattr(self, "_effective_user_config_path", None)

    @cached_property
    def ingestion_pipeline_hash(self) -> str:
        """SHA-256 of embedder identity + ingestion YAML bytes.

        Used as the ``pipeline_hash`` slot in
        :func:`~pydocs_mcp.models.compute_chunk_content_hash`. Any edit
        to ingestion.yaml (added stage, changed batch_size, reordered
        steps, even comment-only changes — we hash raw bytes) OR any
        change to embedder config invalidates every chunk's hash. The
        diff-merge sees all chunks as 'added' and re-embeds via the
        existing path. No separate 'force re-embed' code path needed.

        Hashing raw bytes (vs parsed YAML) is intentionally conservative:
        even comment-only or whitespace edits trigger re-embed. Trade:
        occasionally over-invalidates, but eliminates the risk of two
        semantically-different YAMLs hashing equal due to parser quirks.
        Pipeline edits are rare; over-invalidation cost is bounded.

        When ``extraction.ingestion.pipeline_path`` is unset (the default),
        we fall back to the shipped ``pydocs_mcp/pipelines/ingestion.yaml``
        — mirroring the resolution in
        :func:`pydocs_mcp.extraction.factories.build_ingestion_pipeline`.

        Cached per-AppConfig instance via :func:`functools.cached_property`:
        the YAML path + content are fixed for the life of the config, so
        re-opening the file on every chunk's content-hash assignment is
        wasted I/O. ``__main__.py`` reads this once at startup, but the
        cache is the contract — future callers (background indexers, tests,
        any code path that touches the hash repeatedly) get the same
        free read-once behavior.
        """
        # Deferred import: ``extraction.factories`` pulls in
        # ``extraction.pipeline.stages.reference_capture`` which imports
        # back from ``retrieval.config`` (for ``ReferenceCaptureConfig``).
        # Importing inside the method breaks the module-level cycle and
        # keeps the single source of truth for the bundled-YAML lookup.
        from pydocs_mcp.extraction.factories import _default_ingestion_pipeline_path

        override = self.extraction.ingestion.pipeline_path
        ingestion_path = override if override is not None else _default_ingestion_pipeline_path()
        return hashlib.sha256(
            self.embedding.compute_pipeline_hash().encode("utf-8")
            + b"|"
            + ingestion_path.read_bytes()
        ).hexdigest()

    def compute_ingestion_pipeline_hash(self) -> str:
        """Method-form shim over :attr:`ingestion_pipeline_hash`.

        Kept for call sites that predate the property form
        (``__main__.py``'s composition root, the
        ``test_config_pipeline_hash`` suite). New code should read the
        property directly so the cache contract is obvious at the call
        site.
        """
        return self.ingestion_pipeline_hash


@cache
def _shipped_default_config_path() -> Path:
    """Path to the package-shipped baseline YAML (spec §5.9).

    Cached: ``importlib.resources.files`` + ``joinpath`` + ``Path(str(...))``
    runs on every ``AppConfig.load`` call otherwise. The shipped defaults
    directory never changes at runtime, so the lookup is safely memoisable.
    """
    return Path(str(importlib.resources.files("pydocs_mcp.defaults").joinpath("default_config.yaml")))


@cache
def _shipped_pipelines_dir() -> Path:
    """Resolved path to the ``pydocs_mcp/pipelines/`` directory (spec §5.9).

    Cached for the same reason as :func:`_shipped_default_config_path` —
    the pipeline-path allowlist recomputes this on every YAML load.
    """
    return Path(str(importlib.resources.files("pydocs_mcp.pipelines"))).resolve()


def _resolved_user_config_path() -> Path | None:
    """Resolve the user-layer config path.

    Order:
      1. explicit path set via :meth:`AppConfig.load`
      2. ``PYDOCS_CONFIG_PATH`` env var
      3. ``./pydocs-mcp.yaml``
      4. ``~/.config/pydocs-mcp/config.yaml``
      5. ``None`` — shipped baseline is sufficient.
    """
    explicit = _USER_CONFIG_PATH_OVERRIDE.get()
    if explicit is not None:
        return explicit
    env = os.environ.get("PYDOCS_CONFIG_PATH")
    if env:
        return Path(env)
    cwd_candidate = Path.cwd() / "pydocs-mcp.yaml"
    if cwd_candidate.exists():
        return cwd_candidate
    home_candidate = Path.home() / ".config" / "pydocs-mcp" / "config.yaml"
    if home_candidate.exists():
        return home_candidate
    return None


# ── Pipeline assembly ───────────────────────────────────────────────────


def build_chunk_pipeline_from_config(
    config: AppConfig, context: BuildContext,
) -> CodeRetrieverPipeline:
    return _build_handler_pipeline(
        "chunk", config.pipelines["chunk"], context, config._user_config_path(),
    )


def build_member_pipeline_from_config(
    config: AppConfig, context: BuildContext,
) -> CodeRetrieverPipeline:
    return _build_handler_pipeline(
        "member", config.pipelines["member"], context, config._user_config_path(),
    )


def _pipeline_path_allowed_roots(user_config_path: Path | None) -> tuple[Path, ...]:
    """Return the directories a ``pipeline_path`` may resolve inside.

    A YAML config is user-controlled input — unrestricted ``pipeline_path``
    would happily load ``/etc/shadow`` (and surface the contents in the
    subsequent YAML parse error). Keep the allowlist to:

    1. The shipped ``pydocs_mcp/pipelines/`` directory (the baseline YAMLs).
    2. The directory that contains the user's config file, if they supplied
       one — so ``./my_pipeline.yaml`` next to ``pydocs-mcp.yaml`` works.
    """
    roots = [_shipped_pipelines_dir()]
    if user_config_path is not None:
        roots.append(user_config_path.resolve().parent)
    return tuple(roots)


def _path_is_inside(candidate: Path, roots: tuple[Path, ...]) -> bool:
    """Return True iff ``candidate`` (already resolved) sits inside any root."""
    for root in roots:
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _resolve_pipeline_path(
    pipeline_path: Path, user_config_path: Path | None = None,
) -> Path:
    """Resolve a YAML ``pipeline_path`` against the user/shipped roots.

    Relative paths are first tried under the user's config directory, then
    under the shipped ``pipelines/`` dir. Absolute paths are accepted only if
    they land inside the allowlist. Symlinks are resolved before the check
    so a symlink planted inside ``pipelines/`` pointing at ``/etc/shadow`` is
    rejected.
    """
    allowed_roots = _pipeline_path_allowed_roots(user_config_path)
    pipelines_dir = _shipped_pipelines_dir()

    if pipeline_path.is_absolute():
        resolved = pipeline_path.resolve()
    else:
        parts = pipeline_path.parts
        # ``presets/...`` is the pre-refactor convention; give a clear
        # migration error rather than a confusing FileNotFoundError.
        if parts and parts[0] == "presets":
            raise ValueError(
                f"pipeline_path={pipeline_path!s}: the 'presets/' prefix was "
                f"renamed to 'pipelines/' (chunk_fts.yaml → chunk_search.yaml, "
                f"member_like.yaml → member_search.yaml). Update your "
                f"pydocs-mcp.yaml accordingly."
            )
        # ``pipelines/foo.yaml`` uses search-path semantics: user-dir wins
        # when the file is present locally (so a user can override the shipped
        # pipeline by dropping their own ``pipelines/chunk_search.yaml`` next
        # to their config), otherwise falls back to the shipped dir. This
        # lets ``default_config.yaml`` reference bundled YAMLs without
        # knowing the install path AND lets users override them.
        if parts and parts[0] == "pipelines":
            user_local = None
            if user_config_path is not None:
                user_local = (user_config_path.resolve().parent / pipeline_path).resolve()
            if user_local is not None and user_local.exists():
                candidate = user_local
            else:
                candidate = Path(str(importlib.resources.files("pydocs_mcp").joinpath(
                    str(pipeline_path)
                ))).resolve()
        else:
            base = (
                user_config_path.resolve().parent
                if user_config_path is not None
                else pipelines_dir
            )
            candidate = (base / pipeline_path).resolve()
        resolved = candidate

    if not _path_is_inside(resolved, allowed_roots):
        raise ValueError(
            f"pipeline_path must be inside one of {sorted(str(r) for r in allowed_roots)}; "
            f"got {pipeline_path!s} (resolved to {resolved!s})"
        )
    return resolved


def _load_preset_yaml(path: Path, context: BuildContext) -> CodeRetrieverPipeline:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return CodeRetrieverPipeline.from_dict(data, context)


def _build_handler_pipeline(
    handler_name: str,
    handler_config: HandlerConfig,
    context: BuildContext,
    user_config_path: Path | None = None,
) -> CodeRetrieverPipeline:
    # Lazy import — see top-level WHY note. Breaks the
    # ``retrieval.steps`` ⇄ ``retrieval.config`` cycle that runs through
    # the extraction-side reference-capture stage at import time.
    from pydocs_mcp.retrieval.steps import RouteCase, RouteStep

    routes: list[RouteCase] = []
    default: CodeRetrieverPipeline | None = None
    for entry in handler_config.routes:
        resolved = _resolve_pipeline_path(entry.pipeline_path, user_config_path)
        # WHY: a CodeRetrieverPipeline subclasses ``RetrieverStep``, so we
        # slot it directly into ``RouteCase.stage`` — no adapter needed.
        sub_pipeline = _load_preset_yaml(resolved, context)
        # PipelineRouteEntry guarantees exactly-one-of, so we needn't re-validate
        if entry.default:
            if default is not None:
                raise ValueError(f"{handler_name}: multiple default routes declared")
            default = sub_pipeline
        else:
            # predicate must be set — guaranteed by PipelineRouteEntry validator
            routes.append(RouteCase(predicate_name=entry.predicate, stage=sub_pipeline))
    if not routes and default is not None:
        # Single-default route collapses to the inner pipeline directly so
        # callers inspecting pipeline.stages see the preset's stage list,
        # not a RouteStep wrapper (preserves sub-PR #2's golden parity).
        return CodeRetrieverPipeline(name=default.name, stages=default.stages)
    return CodeRetrieverPipeline(
        name=f"{handler_name}_from_config",
        stages=(RouteStep(routes=tuple(routes), default=default),),
    )
