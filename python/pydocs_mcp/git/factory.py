"""Creator function for the git port (spec §6.14 item 1: creation separated from use)."""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.null_repository import NullGitRepository
from pydocs_mcp.git.refs import locate_gitdir
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from pydocs_mcp.retrieval.config.git_models import GitConfig, GitEnablement

log = logging.getLogger("pydocs-mcp")


def git_repository_factory(config: GitConfig) -> Callable[[Path], GitRepository]:
    """Bind the config once; the returned callable picks the adapter per project root."""

    def _build(project_root: Path) -> GitRepository:
        if config.enabled is GitEnablement.OFF:
            return NullGitRepository()
        missing = _unavailable_reason(config, project_root)
        if missing is not None:
            _log_unavailable(config, project_root, missing)
            return NullGitRepository()
        return SubprocessGitRepository(
            project_root=project_root,
            binary=config.binary,
            timeout_seconds=config.timeout_seconds,
        )

    return _build


def _unavailable_reason(config: GitConfig, project_root: Path) -> str | None:
    if shutil.which(config.binary) is None:
        return f"binary {config.binary!r} not on PATH"
    if locate_gitdir(project_root) is None:
        return "not a git repository"
    return None


def _log_unavailable(config: GitConfig, project_root: Path, reason: str) -> None:
    """``on`` asked for git explicitly, so its absence warns; ``auto`` only informs."""
    level = logging.WARNING if config.enabled is GitEnablement.ON else logging.INFO
    # json.dumps, not hand-formatting: a root containing a quote or a backslash
    # (every Windows path) would otherwise emit unparseable JSON.
    payload = {"event": "git_unavailable", "reason": reason, "root": str(project_root)}
    log.log(level, json.dumps(payload))
