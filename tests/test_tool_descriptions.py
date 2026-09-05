"""Pins for MCP tool descriptions + CLI help text (issue #12).

Substring assertions (case-insensitive) catch capability claims and
forbidden internal jargon. Resilient to whitespace + minor wording
changes — future tightening of the prose is unblocked as long as the
required signals survive.
"""

from __future__ import annotations

import re


# ── MCP side ─────────────────────────────────────────────────────────────
#
# The MCP tool descriptions and the server-level orientation now come from the
# single ``TOOL_DOCS`` / ``SERVER_INSTRUCTIONS`` source in
# :mod:`pydocs_mcp.application.tool_docs` (spec §D13). The per-tool CONTENT
# contract (six required sections, size budgets, no old-surface references) is
# pinned by ``tests/application/test_tool_docs_lint.py``; the six-tool source
# REGISTRATION contract is pinned by
# ``tests/application/test_server_surface.py``. These tests pin only what is
# specific to ``server.py`` wiring: that it consumes those single sources and
# stamps the read-only advisory annotations onto every tool.


def test_server_instructions_sourced_from_tool_docs() -> None:
    """AC-1: the FastMCP session-level scope frame is ``SERVER_INSTRUCTIONS``.

    ``server.run`` passes ``instructions=SERVER_INSTRUCTIONS`` (no inline
    string), so the orientation prose lives in exactly one place.
    """
    import inspect

    from pydocs_mcp import server as srv
    from pydocs_mcp.application.tool_docs import SERVER_INSTRUCTIONS

    src = inspect.getsource(srv)
    assert "instructions=SERVER_INSTRUCTIONS" in src
    # The single-source orientation is itself substantive scope-framing prose.
    inst_low = SERVER_INSTRUCTIONS.lower()
    assert len(SERVER_INSTRUCTIONS) > 100
    assert "__project__" in inst_low or "project" in inst_low
    assert "do not use" in inst_low or "do not" in inst_low


def test_tool_descriptions_sourced_from_tool_docs() -> None:
    """AC-2: every tool's LLM-visible description is ``TOOL_DOCS[name]``.

    server.py registers via ``description=TOOL_DOCS[name]`` rather than inline
    docstrings, so the six-tool description content is the single source in
    ``application.tool_docs`` (content itself pinned by test_tool_docs_lint)."""
    import inspect

    from pydocs_mcp import server as srv

    src = inspect.getsource(srv)
    assert "description=TOOL_DOCS[name]" in src


def test_mcp_tools_have_readonly_idempotent_annotations() -> None:
    """AC-6: every MCP tool ships readOnlyHint, idempotentHint, openWorldHint.

    Pinned via source-level substring (the ``@mcp.tool()`` decorator call
    text). server.py applies the annotations once in the shared ``_register``
    helper that decorates all six tools, so a single occurrence covers the
    whole surface. FastMCP stores these on the registered tool object but the
    public registry surface varies by version; source-text assertion is the
    lowest-friction pin.
    """
    import inspect

    from pydocs_mcp import server as srv

    src = inspect.getsource(srv)
    assert src.count("readOnlyHint=True") >= 1
    assert src.count("idempotentHint=True") >= 1
    assert src.count("openWorldHint=True") >= 1


# ── CLI side ─────────────────────────────────────────────────────────────


def _cli_help(subcommand: str) -> str:
    """Format the argparse help output for `pydocs-mcp <subcommand>`."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    # argparse subparsers are stored on the parser._subparsers._group_actions[0].choices dict
    subparsers_action = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    sub = subparsers_action.choices[subcommand]
    return sub.format_help()


def test_cli_search_help_announces_capability() -> None:
    """AC-7 (amended by contract §6 note 4): search help is TOOL_DOCS-sourced.

    The hand-written capability prose was replaced by the ``TOOL_DOCS``
    single source — the help must carry that text's opening line and its
    Examples section (content itself linted by test_tool_docs_lint)."""
    from pydocs_mcp.application.tool_docs import TOOL_DOCS

    help_text = _cli_help("search")
    assert TOOL_DOCS["search_codebase"].splitlines()[0] in help_text
    assert "Examples" in help_text


def test_cli_lookup_help_announces_reference_graph() -> None:
    """AC-8: pydocs-mcp lookup --help mentions reference graph, who uses X, Examples."""
    help_text = _cli_help("lookup")
    low = help_text.lower()
    assert "reference graph" in low or "reference-graph" in low
    assert "who uses" in low
    assert "Examples" in help_text


def test_cli_search_flags_all_have_help_text() -> None:
    """AC-7 supplement: every flag on search subparser has non-empty help=."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    subparsers_action = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    sub = subparsers_action.choices["search"]
    for action in sub._actions:
        # Skip the auto-generated `--help` action and the positional.
        if action.dest in ("help",):
            continue
        assert action.help and action.help.strip(), (
            f"search --{action.dest} has empty help= (option: {action.option_strings or action.dest})"
        )


def test_cli_lookup_flags_all_have_help_text() -> None:
    """AC-8 supplement: every flag on lookup subparser has non-empty help=."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    subparsers_action = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    sub = subparsers_action.choices["lookup"]
    for action in sub._actions:
        if action.dest in ("help",):
            continue
        assert action.help and action.help.strip(), (
            f"lookup --{action.dest} has empty help= (option: {action.option_strings or action.dest})"
        )


def test_no_internal_jargon_in_any_description() -> None:
    """AC-9: forbidden internal jargon scan across MCP + CLI prose.

    MCP prose is the single ``TOOL_DOCS`` / ``SERVER_INSTRUCTIONS`` source;
    CLI prose is the argparse help for the currently-registered subcommands."""
    from pydocs_mcp.application.tool_docs import SERVER_INSTRUCTIONS, TOOL_DOCS

    mcp_prose = "\n".join([SERVER_INSTRUCTIONS, *TOOL_DOCS.values()])
    cli_search = _cli_help("search")
    cli_lookup = _cli_help("lookup")
    blob = "\n".join([mcp_prose, cli_search, cli_lookup])

    forbidden = [
        r"PR #\d",
        r"sub-PR",
        r"#5[a-c]",
        r"trilogy",
        r"Task \d+ of",
        r"PR-[A-Z]\d",
        r"\bRRF\b",
        r"\bFTS5\b",
        r"TurboQuant",
    ]
    for pattern in forbidden:
        m = re.search(pattern, blob)
        assert m is None, (
            f"forbidden jargon '{m.group()}' (pattern {pattern!r}) found in user-facing prose"
        )
