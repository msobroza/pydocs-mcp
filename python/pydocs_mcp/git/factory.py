"""Creator function for the git port (spec §6.14 item 1: creation separated from use)."""

from __future__ import annotations

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
        if missing is None:
            return SubprocessGitRepository(
                project_root=project_root,
                binary=config.binary,
                timeout_seconds=config.timeout_seconds,
            )
        level = logging.WARNING if config.enabled is GitEnablement.ON else logging.INFO
        log.log(
            level,
            '{"event": "git_unavailable", "reason": "%s", "root": "%s"}',
            missing,
            project_root,
        )
        return NullGitRepository()

    return _build


def _unavailable_reason(config: GitConfig, project_root: Path) -> str | None:
    if shutil.which(config.binary) is None:
        return f"binary {config.binary!r} not on PATH"
    if locate_gitdir(project_root) is None:
        return "not a git repository"
    return None
