"""The MCP tool-name vocabulary the eval package reads off agent transcripts.

An MCP client stamps every server-provided tool as ``mcp__<server>__<tool>``,
while the client's own tools (``Read``, ``Grep``, ``Glob``, ``Bash``) stay bare.
Three readers depend on that one prefix — the Q&A-track stream parser
(``agent_track/_parse.py``), the loop-events distiller
(``trajectory/stream_reader.py``), and the pointer-line parser
(``trajectory/pointer_lines.py``) — and each used to declare its own copy, two
of them describing that copy as the single source of truth. This module is the
one that actually is: a CLI rename is now a one-line fix here.

The product's ``harness/cli_agents/claude_code.py`` spells the same prefix for
its own use and is deliberately NOT imported — the eval package keeps a
zero-``pydocs_mcp``-import floor (ADR 0009 placement), so the constant is
mirrored here exactly as ``blob_store`` and ``attribution`` mirror their
contracts. A pin test (``benchmarks/tests/core/test_mcp_tool_prefix_one_home.py``)
holds the three readers to this name.

Leaf module by construction: stdlib-only, no ``pydocs_eval`` imports, so any
subpackage can depend on it without a cycle.
"""

from __future__ import annotations

MCP_TOOL_PREFIX = "mcp__"
