"""The one-server MCP config that boots ``pydocs_mcp serve`` for a CLI arm.

The composed harness's counterpart of the in-process harness's serve
connection: same interpreter, same module launch, same top-level ``--config``
placement.

Two things live here, and the split is the point. :func:`serve_args` is the
PLATFORM fact — how this product's server is launched — and every engine
renders the same one. :func:`render_serve_mcp_config` is Claude Code's
``mcpServers`` SCHEMA and is called only from that engine's adapter; a second
engine with a different config reader writes its own document from the same
:func:`serve_args` (see ``engines/base.py``'s ``render_mcp_config``).

``env`` is the ADR 0009 channel: the documented ``.mcp.json`` pass-through
carries the ``PYDOCS_TRACE__*`` correlation identity into the serve child, so
the server recorder writes into this trajectory's trace. Omitting it is
byte-identical to a config without an ``env`` block.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

# The MCP server name the arm's tool grant matches (``mcp__pydocs-mcp__*``).
MCP_SERVER_NAME = "pydocs-mcp"

# A serve overlay rides the product's TOP-LEVEL ``--config`` flag, which must
# precede the ``serve`` subcommand (``pydocs-mcp --config x.yaml serve .``) —
# hence the module-args / subcommand split.
_MODULE_ARGS = ("-m", "pydocs_mcp")
_SERVE_SUBCOMMAND = "serve"
_CONFIG_FLAG = "--config"


def render_serve_mcp_config(
    *,
    corpus_dir: Path,
    python: Path,
    env: Mapping[str, str] | None = None,
    overlay: Path | None = None,
) -> str:
    """Render the ``.mcp.json`` document for one traced (or untraced) arm.

    Example:
        >>> render_serve_mcp_config(  # doctest: +SKIP
        ...     corpus_dir=Path("/corpus"), python=Path("/venv/bin/python")
        ... )
        '{"mcpServers": {"pydocs-mcp": {"command": "/venv/bin/python", ...}}}'
    """
    server: dict[str, object] = {
        "command": str(python),
        "args": serve_args(corpus_dir, overlay),
    }
    if env:
        server["env"] = dict(env)
    return json.dumps({"mcpServers": {MCP_SERVER_NAME: server}})


def serve_args(corpus_dir: Path, overlay: Path | None) -> list[str]:
    """The serve argv AFTER the interpreter, ``--config <overlay>`` before ``serve``.

    Public because it is the platform half of every engine's config document:
    the schema around it differs per engine, the launch never does.

    Example:
        >>> serve_args(Path("/corpus"), None)
        ['-m', 'pydocs_mcp', 'serve', '/corpus']
    """
    config_args = [_CONFIG_FLAG, str(overlay)] if overlay is not None else []
    return [*_MODULE_ARGS, *config_args, _SERVE_SUBCOMMAND, str(corpus_dir)]
