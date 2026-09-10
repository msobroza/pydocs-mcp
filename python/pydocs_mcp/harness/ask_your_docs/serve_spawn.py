"""The stdio connection for one pydocs-mcp serve subprocess — langchain-free.

Moved out of ``agent.py`` (0.6.1) so the serve argv shape and its environment rule are
importable, and tested against a real stdio child, with core deps only.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any

from pydocs_mcp.harness.core.serve_child_env import NO_ENV_OVERLAY, serve_child_env


def serve_connection(
    workspace: str,
    pydocs_config: str | None = None,
    pydocs_cmd: list[str] | None = None,
    subprocess_env: Mapping[str, str] = NO_ENV_OVERLAY,
    *,
    seal_config_tier: bool = False,
) -> dict[str, Any]:
    """The stdio connection dict for one pydocs-mcp serve subprocess.

    The single source of the serve argv shape — ``build_agent`` and the
    harness binding (which holds a session open for a whole run) both build
    their connection here, so the argv and env rules cannot drift.

    Env rules: the child inherits this process's environment through
    :func:`~pydocs_mcp.harness.core.serve_child_env.serve_child_env`, with
    ``subprocess_env`` (the ADR 0009 trace overlay) on top;
    ``seal_config_tier`` (the eval binding) withholds the shell's ``PYDOCS_*``
    configuration and ``OPENAI_BASE_URL`` / ``LLM_MODEL``.

    Example:
        >>> serve_connection("/ws", "/cfg.yaml")["args"][-5:]
        ['--config', '/cfg.yaml', 'serve', '--workspace', '/ws']
    """
    # WHY this default: the serve child then runs under the SAME interpreter as
    # this app — no reliance on ``pydocs-mcp`` being on the child's PATH.
    command, *prefix = pydocs_cmd or [sys.executable, "-m", "pydocs_mcp"]
    # --config is a root flag: it must come BEFORE the serve subcommand.
    config_args = ["--config", pydocs_config] if pydocs_config else []
    args = [*prefix, *config_args, "serve", "--workspace", workspace]
    # WHY always an env map: with none, the SDK starts the child from six
    # variables and the child cannot build its embedder (serve_child_env). The
    # ADR 0009 trace channel still rides this map as the overlay, never as a
    # parent os.environ mutation, which would race concurrent runs.
    env = serve_child_env(subprocess_env, seal_config_tier=seal_config_tier)
    return {"transport": "stdio", "command": command, "args": args, "env": env}
