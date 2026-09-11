"""AC5 (spec 2026-07-11-cli-mcp-docs-audit): `harness.ask_your_docs.cli._build_parser`
is callable core-only — no [harness-ask-your-docs] extra needed for help-level parsing;
`main()` still gates execution on `_require_extra`."""

from __future__ import annotations

import sys

import pytest


def test_build_parser_core_only() -> None:
    from pydocs_mcp.harness.ask_your_docs.cli import _DEFAULT_PORT, _build_parser

    args = _build_parser().parse_args(["--workspace", "w", "--config", "c"])
    assert args.workspace == "w"
    assert args.config == "c"
    assert args.port == _DEFAULT_PORT
    assert args.streamlit_args == []


def test_module_import_stays_lazy() -> None:
    """Importing the cli module must not pull the agent stack (the
    subpackage's lazy-import contract, CLAUDE.md §Key Technical Details).
    Checked in a fresh subprocess: in a venv that HAS the extra installed,
    sibling tests legitimately import streamlit, which would false-fail an
    in-process sys.modules check."""
    import subprocess

    code = (
        "import sys\n"
        "import pydocs_mcp.harness.ask_your_docs.cli\n"
        "assert 'streamlit' not in sys.modules\n"
        "assert 'langgraph' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_parser_rejects_an_api_key_flag() -> None:
    """AC-24 / D2: secrets never enter argv."""
    from pydocs_mcp.harness.ask_your_docs.cli import _build_parser

    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--api-key", "sk-nope"])


def test_module_import_leaves_httpx_out() -> None:
    """AC-24: the launcher never pulls the connection stack."""
    import subprocess

    code = (
        "import sys\n"
        "import pydocs_mcp.harness.ask_your_docs.cli\n"
        "assert 'httpx' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_launcher_never_imports_the_page_session_modules() -> None:
    """The per-page serve session is page machinery: the launcher must not load it."""
    import subprocess

    code = (
        "import sys\n"
        "import pydocs_mcp.harness.ask_your_docs.cli\n"
        "assert 'pydocs_mcp.harness.ask_your_docs.serve_session' not in sys.modules\n"
        "assert 'pydocs_mcp.harness.ask_your_docs.page_agent' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_launcher_never_imports_the_activity_panel() -> None:
    """The activity panel is page machinery too: the launcher loads none of it."""
    import subprocess

    code = (
        "import sys\n"
        "import pydocs_mcp.harness.ask_your_docs.cli\n"
        "panel = ('activity_view', 'page_turn', 'activity_stream', 'activity_trace_builder')\n"
        "loaded = [m for m in panel if f'pydocs_mcp.harness.ask_your_docs.{m}' in sys.modules]\n"
        "assert not loaded, loaded\n"
        "assert 'streamlit' not in sys.modules and 'langgraph' not in sys.modules\n"
        "assert 'httpx' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_activity_stream_imports_without_streamlit_or_langchain() -> None:
    """The stream and trace modules stay pure: the graph arrives as an argument."""
    import subprocess

    code = (
        "import sys\n"
        "import pydocs_mcp.harness.ask_your_docs.activity_stream\n"
        "import pydocs_mcp.harness.ask_your_docs.activity_trace_builder\n"
        "assert 'streamlit' not in sys.modules\n"
        "assert not any(m.startswith(('langchain', 'langgraph')) for m in sys.modules)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_page_session_modules_import_without_streamlit_or_langchain() -> None:
    """serve_session / page_agent keep their adapter imports function-local."""
    import subprocess

    code = (
        "import sys\n"
        "import pydocs_mcp.harness.ask_your_docs.page_agent\n"
        "assert 'streamlit' not in sys.modules\n"
        "assert not any(m.startswith('langchain') for m in sys.modules)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
