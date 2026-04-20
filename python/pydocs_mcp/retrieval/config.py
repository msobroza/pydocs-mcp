"""Runtime config — pydantic-settings + YAML source layering (spec §5.9)."""
from __future__ import annotations

import importlib.resources
import os
from collections.abc import Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# Side-effect imports: populate stage/retriever/formatter registries via decorators.
from pydocs_mcp.retrieval import formatters as _formatters  # noqa: F401
from pydocs_mcp.retrieval import retrievers as _retrievers  # noqa: F401
from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.stages import RouteCase, RouteStage, SubPipelineStage


# ── Tunable user-config path override ───────────────────────────────────
#
# ``AppConfig.load(explicit_path=...)`` sets this ambient override so the
# class-method ``settings_customise_sources`` can pick it up when pydantic
# instantiates the settings. Scoped to a ContextVar so concurrent async
# callers (tests in particular) don't clobber each other.
_USER_CONFIG_PATH_OVERRIDE: ContextVar[Path | None] = ContextVar(
    "_USER_CONFIG_PATH_OVERRIDE", default=None,
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


class AppConfig(BaseSettings):
    """Runtime configuration.

    All defaults ship via ``presets/default_config.yaml`` — there are no
    Python-level defaults on YAML-backed fields (spec §5.9, AC #14). The
    source layering (shipped baseline → user YAML → env → init) is wired
    in ``settings_customise_sources``.
    """

    cache_dir: Path
    log_level: str
    metadata_schemas: Mapping[str, tuple[str, ...]]
    pipelines: Mapping[str, HandlerConfig]

    model_config = SettingsConfigDict(env_prefix="PYDOCS_", extra="ignore")

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
        user_path = _resolved_user_config_path()
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
        try:
            return cls()
        finally:
            _USER_CONFIG_PATH_OVERRIDE.reset(token)


def _shipped_default_config_path() -> Path:
    """Path to the package-shipped baseline YAML (spec §5.9)."""
    return Path(str(importlib.resources.files("pydocs_mcp.presets").joinpath("default_config.yaml")))


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
    return _build_handler_pipeline("chunk", config.pipelines["chunk"], context)


def build_member_pipeline_from_config(
    config: AppConfig, context: BuildContext,
) -> CodeRetrieverPipeline:
    return _build_handler_pipeline("member", config.pipelines["member"], context)


def _resolve_pipeline_path(pipeline_path: Path) -> Path:
    """Resolve a YAML ``pipeline_path`` against the shipped ``presets/`` dir
    when it's relative. Absolute user paths are used as-is."""
    if pipeline_path.is_absolute():
        return pipeline_path
    parts = pipeline_path.parts
    if parts and parts[0] == "presets":
        return Path(str(importlib.resources.files("pydocs_mcp").joinpath(str(pipeline_path))))
    return pipeline_path


def _load_preset_yaml(path: Path, context: BuildContext) -> CodeRetrieverPipeline:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return CodeRetrieverPipeline.from_dict(data, context)


def _build_handler_pipeline(
    handler_name: str, handler_config: HandlerConfig, context: BuildContext,
) -> CodeRetrieverPipeline:
    routes: list[RouteCase] = []
    default = None
    for entry in handler_config.routes:
        resolved = _resolve_pipeline_path(entry.pipeline_path)
        sub_pipeline = _load_preset_yaml(resolved, context)
        stage = SubPipelineStage(pipeline=sub_pipeline)
        # PipelineRouteEntry guarantees exactly-one-of, so we needn't re-validate
        if entry.default:
            if default is not None:
                raise ValueError(f"{handler_name}: multiple default routes declared")
            default = stage
        else:
            # predicate must be set — guaranteed by PipelineRouteEntry validator
            routes.append(RouteCase(predicate_name=entry.predicate, stage=stage))
    if not routes and default is not None:
        # Single-default route collapses to the inner pipeline directly so
        # callers inspecting pipeline.stages see the preset's stage list,
        # not a RouteStage wrapper (preserves sub-PR #2's golden parity).
        inner = default.pipeline
        return CodeRetrieverPipeline(name=inner.name, stages=inner.stages)
    return CodeRetrieverPipeline(
        name=f"{handler_name}_from_config",
        stages=(RouteStage(routes=tuple(routes), default=default),),
    )
