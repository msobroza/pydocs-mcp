"""AppConfig — pydantic-settings layering: shipped baseline → user YAML → env → init.

Also owns the user-config path resolution chain (explicit path → env var →
cwd → XDG home) and the ingestion pipeline hash.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import os
from collections.abc import Mapping
from contextvars import ContextVar
from functools import cache, cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from pydocs_mcp.extraction.config import ExtractionConfig
from pydocs_mcp.retrieval.config.embedder_models import (
    _DEFAULT_DEVICE,
    EmbeddingConfig,
    LateInteractionConfig,
    LlmConfig,
)
from pydocs_mcp.retrieval.config.models import (
    HandlerConfig,
    OutputConfig,
    ReferenceGraphConfig,
    SearchBackendConfig,
    SearchConfig,
    ServeConfig,
    SymbolSourceConfig,
)

# ── Tunable user-config path override ───────────────────────────────────
#
# ``AppConfig.load(explicit_path=...)`` sets this ambient override so the
# class-method ``settings_customise_sources`` can pick it up when pydantic
# instantiates the settings. Scoped to a ContextVar so concurrent async
# callers (tests in particular) don't clobber each other.
_USER_CONFIG_PATH_OVERRIDE: ContextVar[Path | None] = ContextVar(
    "_USER_CONFIG_PATH_OVERRIDE",
    default=None,
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
    "_RESOLVED_USER_CONFIG_PATH",
    default=_UNSET,
)


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
    # get_symbol(depth="source") line cap (spec §D7). Bounds the verbatim
    # per-symbol source view; wired config→service in a later task. Per
    # CLAUDE.md §"MCP API surface vs YAML configuration": a deployment-time
    # rendering bound, NOT an MCP tool param — the surface stays fixed.
    symbol_source: SymbolSourceConfig = Field(default_factory=SymbolSourceConfig)
    # Response conventions (spec §D4/§D5): freshness-envelope + per-hit
    # next-step pointer toggles shared by every search/lookup response.
    # Per CLAUDE.md §"MCP API surface vs YAML configuration": these are
    # deployment-time rendering knobs, NOT MCP tool params. The MCP surface
    # (search, lookup) stays fixed.
    output: OutputConfig = Field(default_factory=OutputConfig)
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
    # Unified SearchBackend seam (spec §8.1): which storage backend serves
    # retrieval capabilities. Defaults to ``sqlite_composite``. ``dim`` /
    # ``bit_width`` are NOT duplicated here — they stay sourced from
    # ``embedding`` (single source of truth). Per CLAUDE.md §"MCP API
    # surface vs YAML configuration": backend selection is a deployment-time
    # knob, NOT an MCP tool param — the MCP surface stays fixed.
    search_backend: SearchBackendConfig = Field(default_factory=SearchBackendConfig)
    # LLM chat-completion client config (Task 3 / AC-2). Architectural twin
    # of ``embedding`` — provider/model_name/tuning knobs consumed by
    # ``build_llm_client(cfg)`` to construct the right concrete client. Per
    # CLAUDE.md §"MCP API surface vs YAML configuration": LLM model choice
    # is a pipeline-tuning knob, NOT an MCP tool param — the MCP surface
    # stays fixed.
    llm: LlmConfig = Field(default_factory=LlmConfig)
    # Late-interaction (ColBERT / PyLate) embedder config. Sibling of
    # ``embedding`` / ``llm``; consumed by ``build_multi_vector_embedder(cfg)``
    # and folded into ``ingestion_pipeline_hash`` when the active ingestion
    # pipeline references ``embed_chunks_multi_vector``. Defaults to
    # ``enabled=False`` — opt-in only (spec Decision G).
    late_interaction: LateInteractionConfig = Field(
        default_factory=LateInteractionConfig,
    )
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
        env_prefix="PYDOCS_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    def with_full_index_dependencies(self, names: tuple[str, ...]) -> AppConfig:
        """Return a copy with ``names`` merged into ``embedding.full_index_dependencies``.

        CLI ``--full-dep`` convenience — flags ADD to (never replace) the
        YAML-declared list, deduplicated order-preserving. Pure function
        (pydantic ``model_copy``); no-op when ``names`` is empty.
        """
        if not names:
            return self
        merged = list(dict.fromkeys([*self.embedding.full_index_dependencies, *names]))
        return self.model_copy(
            update={
                "embedding": self.embedding.model_copy(
                    update={"full_index_dependencies": merged},
                ),
            },
        )

    def with_device(self, *, gpu: bool) -> AppConfig:
        """Return a copy with the embedder execution device set.

        ``--gpu`` maps to ``"cuda"``, absent to ``"cpu"``. Device is a
        runtime latency knob excluded from every pipeline hash (see
        _DEFAULT_DEVICE), so this never invalidates an index cache. Pure
        function — the receiver is unmutated (pydantic ``model_copy``).
        """
        device = "cuda" if gpu else _DEFAULT_DEVICE
        return self.model_copy(
            update={
                "embedding": self.embedding.model_copy(update={"device": device}),
                "late_interaction": self.late_interaction.model_copy(
                    update={"device": device},
                ),
            },
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
    def load(cls, explicit_path: Path | None = None) -> AppConfig:
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
        from pydocs_mcp.extraction.factories import (
            _default_ingestion_pipeline_path,
            _resolve_ingestion_pipeline_path,
        )

        override = self.extraction.ingestion.pipeline_path
        # Resolve the override through the SAME allowlist resolver
        # build_ingestion_pipeline uses, so a config-relative path like
        # ``pipelines/ingestion_late_interaction.yaml`` is read identically
        # here and at build time. Reading the raw (CWD-relative) override
        # made the hash crash on any non-CWD-relative path.
        if override is not None:
            ingestion_path = _resolve_ingestion_pipeline_path(override, self)
        else:
            ingestion_path = _default_ingestion_pipeline_path()
        yaml_bytes = ingestion_path.read_bytes()
        identity = self.embedding.compute_pipeline_hash().encode("utf-8")
        # Backend identity must invalidate cached sidecars when the storage
        # backend changes: a TurboQuant ``.tq`` / fast-plaid ``.plaid`` written
        # by one backend kind is meaningless to another (e.g. a future Qdrant).
        # Folding it unconditionally rebuilds the index once on backend switch.
        identity += b"|" + self.search_backend.compute_identity().encode("utf-8")
        # Late-interaction fold (Task 13 / Decision G): only mix the
        # LateInteractionConfig identity in when the active YAML actually
        # references the ``embed_chunks_multi_vector`` stage. Gating on the
        # YAML bytes preserves the "default install hash is stable"
        # invariant — a deployment that ships single-vector ingestion sees
        # byte-identical hashes regardless of LateInteractionConfig defaults.
        if b"embed_chunks_multi_vector" in yaml_bytes:
            identity += b"|" + self.late_interaction.compute_pipeline_hash().encode("utf-8")
        return hashlib.sha256(identity + b"|" + yaml_bytes).hexdigest()

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
    return Path(
        str(importlib.resources.files("pydocs_mcp.defaults").joinpath("default_config.yaml"))
    )


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
