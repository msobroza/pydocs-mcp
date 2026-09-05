"""The shared git child-environment guard (``pydocs_mcp.git.env``).

``git -C <root>`` changes only the working directory, so an inherited
``GIT_DIR`` still redirects a child to another repository. Both git callers —
the ``GitRepository`` subprocess adapter and the decision layer's ``git log``
reader — build their child environment here so neither can regress alone.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.git.env import REPOSITORY_OVERRIDE_VARS, git_child_env


def test_every_repository_redirect_is_named() -> None:
    assert set(REPOSITORY_OVERRIDE_VARS) == {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
    }


@pytest.mark.parametrize("name", REPOSITORY_OVERRIDE_VARS)
def test_each_redirect_is_stripped(name: str, monkeypatch) -> None:
    monkeypatch.setenv(name, "/somewhere/else/.git")
    assert name not in git_child_env()


def test_safety_knobs_are_set_even_when_the_parent_unsets_them(monkeypatch) -> None:
    monkeypatch.delenv("GIT_OPTIONAL_LOCKS", raising=False)
    monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)
    env = git_child_env()
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_safety_knobs_win_over_an_inherited_value(monkeypatch) -> None:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    assert git_child_env()["GIT_TERMINAL_PROMPT"] == "0"


def test_unrelated_variables_pass_through(monkeypatch) -> None:
    # PATH must survive: the child resolves ``git`` through it.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("PYDOCS_UNRELATED", "keep me")
    env = git_child_env()
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["PYDOCS_UNRELATED"] == "keep me"
