"""AC5 (spec 2026-07-11-cli-mcp-docs-audit): `ask_your_docs.cli._build_parser`
is callable core-only — no [ask-your-docs] extra needed for help-level parsing;
`main()` still gates execution on `_require_extra`."""

from __future__ import annotations

import sys


def test_build_parser_core_only() -> None:
    from pydocs_mcp.ask_your_docs.cli import _DEFAULT_PORT, _build_parser

    args = _build_parser().parse_args(["--workspace", "w", "--config", "c"])
    assert args.workspace == "w"
    assert args.config == "c"
    assert args.port == _DEFAULT_PORT
    assert args.streamlit_args == []


def test_module_import_stays_lazy() -> None:
    """Importing the cli module must not pull the agent stack (the
    subpackage's lazy-import contract, CLAUDE.md §Key Technical Details)."""
    import pydocs_mcp.ask_your_docs.cli

    assert "streamlit" not in sys.modules
    assert "langgraph" not in sys.modules
