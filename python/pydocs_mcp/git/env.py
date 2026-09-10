"""The child-environment guard every ``git`` subprocess in this codebase uses.

``git -C <root>`` changes only the working directory: ``GIT_DIR`` and its
siblings still override repository discovery, so a child that inherits them
answers about a DIFFERENT repository while every path-derived answer still
describes this tree — silently wrong rather than loudly broken. An index pass
launched from a ``post-commit`` / ``post-checkout`` hook (the natural way to
keep an index fresh before the ref watcher ships) inherits exactly those
variables from the invoking repository.

Both git callers route through :func:`git_child_env`: the ``GitRepository``
subprocess adapter and the decision layer's bounded ``git log`` reader.
"""

from __future__ import annotations

import os

# Variables that redirect git away from ``-C <root>``; stripped from every
# child environment (see the module docstring for the hook scenario).
REPOSITORY_OVERRIDE_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
)

# ``GIT_OPTIONAL_LOCKS=0``: never take ``index.lock`` for a read-only query, so
# a concurrent user command is never blocked. ``GIT_TERMINAL_PROMPT=0``: fail
# fast instead of blocking forever on a credential prompt.
_SAFETY_KNOBS = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}


def git_child_env() -> dict[str, str]:
    """The parent environment minus repository redirects, plus the safety knobs."""
    inherited = {k: v for k, v in os.environ.items() if k not in REPOSITORY_OVERRIDE_VARS}
    return inherited | _SAFETY_KNOBS


__all__ = ("REPOSITORY_OVERRIDE_VARS", "git_child_env")
