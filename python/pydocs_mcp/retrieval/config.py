"""Runtime config — pydantic-settings + YAML source + load precedence."""
from __future__ import annotations

import importlib.resources
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

# Side-effect imports: populate stage/retriever/formatter registries via decorators.
from pydocs_mcp.retrieval import formatters as _formatters  # noqa: F401
from pydocs_mcp.retrieval import retrievers as _retrievers  # noqa: F401
from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.stages import RouteCase, RouteStage, SubPipelineStage


class PipelineRouteEntry(BaseModel):
    """One entry in a handler's route list.

    - If `predicate` is set, run the pipeline at `pipeline_path` when the predicate matches.
    - If `default` is True, use the pipeline at `pipeline_path` as fallback.
    Exactly one of `predicate` or `default` must be set.
    """
    predicate: str | None = None
    default: bool = False
    pipeline_path: Path


class HandlerConfig(BaseModel):
    routes: tuple[PipelineRouteEntry, ...]


class AppConfig(BaseSettings):
    cache_dir: Path = Path.home() / ".pydocs-mcp"
    log_level: str = "info"
    chunk: HandlerConfig | None = None
    member: HandlerConfig | None = None

    model_config = SettingsConfigDict(env_prefix="PYDOCS_", yaml_file=None)

    @classmethod
    def load(cls, explicit_path: Path | None = None) -> "AppConfig":
        """Resolve + load config per precedence: explicit > env > cwd > home > defaults."""
        path = cls._resolve_path(explicit_path)
        if path is None or not path.exists():
            return cls()  # defaults
        with path.open("r", encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
        return cls(**data)

    @staticmethod
    def _resolve_path(explicit_path: Path | None) -> Path | None:
        if explicit_path is not None:
            return explicit_path
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


def build_chunk_pipeline_from_config(
    config: AppConfig, context: BuildContext
) -> CodeRetrieverPipeline:
    if config.chunk is None:
        return _load_preset_yaml(
            _preset_path("chunk_fts.yaml"),
            context,
        )
    return _build_handler_pipeline("chunk", config.chunk, context)


def build_member_pipeline_from_config(
    config: AppConfig, context: BuildContext
) -> CodeRetrieverPipeline:
    if config.member is None:
        return _load_preset_yaml(
            _preset_path("member_like.yaml"),
            context,
        )
    return _build_handler_pipeline("member", config.member, context)


def _preset_path(name: str) -> Path:
    return Path(str(importlib.resources.files("pydocs_mcp.presets").joinpath(name)))


def _load_preset_yaml(path: Path, context: BuildContext) -> CodeRetrieverPipeline:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return CodeRetrieverPipeline.from_dict(data, context)


def _build_handler_pipeline(
    handler_name: str, handler_config: HandlerConfig, context: BuildContext
) -> CodeRetrieverPipeline:
    routes: list[RouteCase] = []
    default = None
    for entry in handler_config.routes:
        sub_pipeline = _load_preset_yaml(entry.pipeline_path, context)
        stage = SubPipelineStage(pipeline=sub_pipeline)
        if entry.default:
            if default is not None:
                raise ValueError(f"{handler_name}: multiple default routes declared")
            default = stage
        elif entry.predicate:
            routes.append(RouteCase(predicate_name=entry.predicate, stage=stage))
        else:
            raise ValueError(
                f"{handler_name}: route entry must set either predicate or default"
            )
    if not routes and default is not None:
        return CodeRetrieverPipeline(name=f"{handler_name}_from_config", stages=(default,))
    return CodeRetrieverPipeline(
        name=f"{handler_name}_from_config",
        stages=(RouteStage(routes=tuple(routes), default=default),),
    )
