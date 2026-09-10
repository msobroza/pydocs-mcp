"""The launcher's --base-url / --model reach the app under PRIVATE names (0.6.1).

Core deps only: ``cli._require_extra`` (its documented seam) is a no-op and
``subprocess.run`` is a recording fake, so nothing starts. The serve child inherits the
launcher's environment (``harness.core.serve_child_env``), so writing the chat endpoint
into ``OPENAI_BASE_URL`` would re-point the child's OpenAI embedder at the chat host.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from pydocs_mcp.harness.ask_your_docs import cli
from pydocs_mcp.harness.ask_your_docs.cli import LAUNCH_BASE_URL_ENV_VAR, LAUNCH_MODEL_ENV_VAR
from pydocs_mcp.harness.core.serve_child_env import serve_child_env

from ._launcher_fakes import FakeStreamlitRun

_CHAT_URL = "http://chat/v1"
_SHELL_EMBED_URL = "http://embed-gw/v1"


def _extra_installed() -> None:
    """The launcher's extra guard, satisfied (no streamlit needed to record the spawn)."""


def _present(env: Mapping[str, str], *names: str) -> set[str]:
    """Which of ``names`` are set — NAMES only, so a failure prints no inherited value."""
    return set(names) & env.keys()


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> FakeStreamlitRun:
    fake = FakeStreamlitRun()
    monkeypatch.setattr(cli, "_require_extra", _extra_installed)
    monkeypatch.setattr(cli.subprocess, "run", fake)
    return fake


def test_launcher_forwards_connection_flags_under_private_names(
    launched: FakeStreamlitRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", _SHELL_EMBED_URL)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert cli.main(["--workspace", "w", "--base-url", _CHAT_URL, "--model", "m"]) == 0
    env = launched.env
    assert env[LAUNCH_BASE_URL_ENV_VAR] == _CHAT_URL and env[LAUNCH_MODEL_ENV_VAR] == "m"
    assert env["OPENAI_BASE_URL"] == _SHELL_EMBED_URL
    assert _present(env, "LLM_MODEL") == set()


def test_launcher_clears_stale_private_names_when_flags_absent(
    launched: FakeStreamlitRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale shell export must not pose as a launcher flag."""
    monkeypatch.setenv(LAUNCH_BASE_URL_ENV_VAR, "http://stale/v1")
    monkeypatch.setenv(LAUNCH_MODEL_ENV_VAR, "stale-model")
    assert cli.main(["--workspace", "w"]) == 0
    assert _present(launched.env, LAUNCH_BASE_URL_ENV_VAR, LAUNCH_MODEL_ENV_VAR) == set()


@pytest.mark.parametrize("shell_value", [_SHELL_EMBED_URL, None])
def test_serve_child_never_sees_the_launcher_chat_endpoint(
    launched: FakeStreamlitRun, monkeypatch: pytest.MonkeyPatch, shell_value: str | None
) -> None:
    if shell_value is None:
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("OPENAI_BASE_URL", shell_value)
    cli.main(["--workspace", "w", "--base-url", _CHAT_URL, "--model", "m"])
    assert serve_child_env(environ=launched.env).get("OPENAI_BASE_URL") == shell_value
