"""Pins for MCP tool descriptions + CLI help text (issue #12).

Substring assertions (case-insensitive) catch capability claims and
forbidden internal jargon. Resilient to whitespace + minor wording
changes — future tightening of the prose is unblocked as long as the
required signals survive.
"""

from __future__ import annotations

import re


# ── MCP side ─────────────────────────────────────────────────────────────


def _mcp_tool_docstrings():
    """Spin up a FastMCP instance the same way ``server.run`` does, but
    without starting the stdio loop. Returns (server_instructions,
    search_docstring, lookup_docstring) so individual tests can assert
    against each surface independently."""
    from pathlib import Path

    from mcp.server.fastmcp import FastMCP

    # We deliberately DON'T call ``server.run``; instead we mirror its
    # construction shape. The `@mcp.tool()` decorators are applied at
    # function-definition time, so we can read the bound docstrings from
    # the decorated functions via the FastMCP registry.
    #
    # Implementation note: if server.run uses `mcp.run(transport=stdio)`
    # at the bottom, we cannot import server module top-level without
    # also triggering the entry point. Verify the import is safe.
    from pydocs_mcp import server as srv

    # The test needs an MCP instance; build one fresh per call.
    mcp = FastMCP("pydocs-mcp")
    # Mirror the decorations server.py applies; we just need the docs.
    # Easiest path: introspect server.py source via inspect.getsource and
    # extract the docstrings as plain strings. This avoids re-invoking
    # the full ``run()`` factory.
    import inspect

    src = inspect.getsource(srv)

    # Extract @mcp.tool() decorated function docstrings via regex.
    def _extract_docstring(src_text: str, fn_name: str) -> str:
        # Match `async def fn_name(...):` then capture the next triple-
        # quoted block.
        pattern = rf"async def {fn_name}\([^)]*\)[^:]*:\s*\"\"\"(.*?)\"\"\""
        m = re.search(pattern, src_text, re.DOTALL)
        return m.group(1) if m else ""

    search_doc = _extract_docstring(src, "search")
    lookup_doc = _extract_docstring(src, "lookup")

    # Server-level instructions: pull from the FastMCP() call.
    inst_match = re.search(
        r"FastMCP\([^)]*instructions\s*=\s*([\"']{1,3})(.*?)\1",
        src,
        re.DOTALL,
    )
    server_instructions = inst_match.group(2) if inst_match else ""

    return server_instructions, search_doc, lookup_doc


def test_server_instructions_set() -> None:
    """AC-1: FastMCP(instructions=...) provides a session-level scope frame."""
    inst, _, _ = _mcp_tool_docstrings()
    assert len(inst) > 100, f"server-level instructions too short: {len(inst)} chars"
    inst_low = inst.lower()
    assert "__project__" in inst_low or "project" in inst_low
    assert "two tools" in inst_low or "search" in inst_low
    assert "do not use" in inst_low or "do not" in inst_low


def test_search_docstring_announces_hybrid_project_dependency() -> None:
    """AC-2: search description leads with the hybrid+project+deps differentiator."""
    _, search, _ = _mcp_tool_docstrings()
    s = search.lower()
    assert "hybrid" in s, "search docstring must announce hybrid retrieval"
    assert "project" in s, "search docstring must mention project-code indexing"
    assert "dependenc" in s, "search docstring must mention dependencies"


def test_lookup_docstring_announces_reference_graph() -> None:
    """AC-3: lookup description leads with the reference-graph capability."""
    _, _, lookup = _mcp_tool_docstrings()
    low = lookup.lower()
    assert "callers" in low
    assert "callees" in low
    assert "inherits" in low
    assert "reference graph" in low or "reference-graph" in low


def test_search_documents_project_sentinel() -> None:
    """AC-4: __project__ priority signal in search params."""
    _, search, _ = _mcp_tool_docstrings()
    assert "__project__" in search


def test_lookup_show_modes_carry_workflow_framing() -> None:
    """AC-5: lookup.show modes carry "use to answer" framing."""
    _, _, lookup = _mcp_tool_docstrings()
    assert "who uses" in lookup.lower() or "use to answer" in lookup.lower()


def test_mcp_tools_have_readonly_idempotent_annotations() -> None:
    """AC-6: both MCP tools ship readOnlyHint, idempotentHint, openWorldHint.

    Pinned via source-level substring (the @mcp.tool() decorator call
    text). FastMCP stores these on the registered tool object but the
    public registry surface varies by version; source-text assertion is
    the lowest-friction pin.
    """
    import inspect

    from pydocs_mcp import server as srv

    src = inspect.getsource(srv)
    # Count occurrences — should appear once per @mcp.tool() (2 tools).
    assert src.count("readOnlyHint=True") >= 2
    assert src.count("idempotentHint=True") >= 2
    assert src.count("openWorldHint=True") >= 2


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
    """AC-7: pydocs-mcp search --help mentions hybrid, __project__, Examples."""
    help_text = _cli_help("search")
    low = help_text.lower()
    assert "hybrid" in low
    assert "__project__" in help_text  # case-sensitive sentinel
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
    """AC-9: forbidden internal jargon scan across MCP + CLI prose."""
    inst, search, lookup = _mcp_tool_docstrings()
    cli_search = _cli_help("search")
    cli_lookup = _cli_help("lookup")
    blob = "\n".join([inst, search, lookup, cli_search, cli_lookup])

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
