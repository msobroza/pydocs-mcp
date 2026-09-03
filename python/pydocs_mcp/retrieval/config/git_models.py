"""``git:`` configuration (spec §6.9, P0 subset: enablement, binary, timeout).

Deployment knobs, never MCP tool params (CLAUDE.md §"MCP API surface vs YAML
configuration"). P1 adds ``branches`` / ``ref_watch`` / ``remote`` here.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

_DEFAULT_GIT_BINARY = "git"
_DEFAULT_GIT_TIMEOUT_SECONDS = 30.0


class GitEnablement(StrEnum):
    """``auto``: on when a git binary and a repository are found; ``on`` / ``off``."""

    AUTO = "auto"
    ON = "on"
    OFF = "off"


class GitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: GitEnablement = GitEnablement.AUTO
    binary: str = Field(default=_DEFAULT_GIT_BINARY, min_length=1)
    timeout_seconds: float = Field(default=_DEFAULT_GIT_TIMEOUT_SECONDS, gt=0)
