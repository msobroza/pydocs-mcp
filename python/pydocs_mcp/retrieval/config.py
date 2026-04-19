"""Runtime config — pydantic-settings + YAML source + load precedence."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


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
