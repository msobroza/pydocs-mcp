"""The ``git:`` AppConfig section (spec §6.9) and the creator function (§6.14 item 1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pydocs_mcp.git.factory import git_repository_factory
from pydocs_mcp.git.null_repository import NullGitRepository
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.config.git_models import GitConfig, GitEnablement
from tests._git_sandbox import requires_git


def test_defaults_are_auto_git_and_thirty_seconds() -> None:
    cfg = AppConfig.load().git
    assert cfg.enabled is GitEnablement.AUTO
    assert cfg.binary == "git"
    assert cfg.timeout_seconds == 30.0


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GitConfig(enabled="auto", binry="git")  # type: ignore[call-arg]


def test_factory_returns_null_when_disabled(tmp_path: Path) -> None:
    build = git_repository_factory(GitConfig(enabled=GitEnablement.OFF))
    assert isinstance(build(tmp_path), NullGitRepository)


def test_factory_returns_null_when_not_a_repository(tmp_path: Path) -> None:
    build = git_repository_factory(GitConfig())
    assert isinstance(build(tmp_path), NullGitRepository)


def _mark_as_git_repository(root: Path) -> None:
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")


@requires_git
def test_factory_returns_subprocess_adapter_for_a_repository(tmp_path: Path) -> None:
    _mark_as_git_repository(tmp_path)
    build = git_repository_factory(GitConfig())
    assert isinstance(build(tmp_path), SubprocessGitRepository)


@requires_git
def test_factory_binds_both_timeouts_from_the_config(tmp_path: Path) -> None:
    # #308: the remote-probe bound comes from git.remote.auto_fetch, so the
    # ls-remote the remote lane runs honors the YAML key (#306 left it unwired).
    _mark_as_git_repository(tmp_path)
    config = GitConfig.model_validate(
        {"timeout_seconds": 12.0, "remote": {"auto_fetch": {"ls_remote_timeout_seconds": 2.5}}}
    )
    repo = git_repository_factory(config)(tmp_path)
    assert isinstance(repo, SubprocessGitRepository)
    assert repo.timeout_seconds == 12.0
    assert repo.network_timeout_seconds == 2.5
