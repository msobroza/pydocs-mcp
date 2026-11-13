"""Pins for MCP tool descriptions + CLI help text (issue #12).

Substring assertions (case-insensitive) catch capability claims and
forbidden internal jargon. Resilient to whitespace + minor wording
changes — future tightening of the prose is unblocked as long as the
required signals survive.

The server-instruction rules that restate the chat prompt are the exception:
they are pinned sentence for sentence against ``system_v3`` (issues #379,
#395), because their job is to agree with it word for word.
"""

from __future__ import annotations

import re

import pytest


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


# ── get_overview's triggers agree with the chat prompt (issue #379) ──────
#
# system_v3's rule 14 calls get_overview only after a search misses or for a
# question about the project's structure; the packaged description and the
# server-instructions Workflow line name the same two triggers, so the prompt
# and the surface every MCP client reads never disagree.

_OVERVIEW_TRIGGERS = "after a search misses, or for a question about the project's structure"


def _served_instructions() -> str:
    # Read at call time: other tests rebind tool_docs.SERVER_INSTRUCTIONS.
    from pydocs_mcp.application.tool_docs import SERVER_INSTRUCTIONS

    return SERVER_INSTRUCTIONS


def _server_instruction_line(prefix: str) -> str:
    lines = [ln for ln in _served_instructions().splitlines() if ln.startswith(prefix)]
    assert len(lines) == 1, f"expected one server-instruction line opening {prefix!r}: {lines!r}"
    return lines[0]


def test_get_overview_is_no_longer_the_first_call() -> None:
    from pydocs_mcp.application.tool_docs import TOOL_DOCS

    assert "first call on an unfamiliar project" not in TOOL_DOCS["get_overview"]


def test_get_overview_when_to_use_names_the_two_prompt_triggers() -> None:
    from pydocs_mcp.application.tool_docs import TOOL_DOCS

    (when,) = [ln for ln in TOOL_DOCS["get_overview"].splitlines() if ln.startswith("When to use:")]
    assert when.startswith(f"When to use: {_OVERVIEW_TRIGGERS};")


def test_the_workflow_starts_at_search_codebase() -> None:
    workflow = _server_instruction_line("Workflow:")
    assert workflow.startswith("Workflow: search_codebase → get_context")
    assert "get_overview → search_codebase" not in workflow
    assert "get_overview only after a search misses or for a question about the project's" in (
        workflow
    )


# ── the follow-up rules agree with system_v3 (issue #395) ────────────────
#
# One server hands out two policy surfaces: the chat agent's system prompt and
# SERVER_INSTRUCTIONS, which every external MCP client reads and chat never
# receives. system_v3 rewrote v2's rules 7, 9 and 12, so the three
# server-instruction lines that restated them now carry v3's sentences. v3 is
# rendered by name: it is byte-frozen, and a later prompt version that moves
# these rules elsewhere does not change what an external client should be told.
# The golden (tests/application/test_description_loading.py) pins every byte.

# rule -> (how its server-instruction line opens, the system_v3 sentences it carries)
_SERVER_LINES_CARRYING_V3_RULES = {
    "rule-7-follow-ups-are-offers": (
        "- Follow-up calls are offers",
        (
            "are offers, not orders: take only the ones the question needs.",
            'The calls on a "Together:" line are independent of each other, so when you '
            "take several, issue them in one turn.",
        ),
    ),
    "rule-9-what-already-shown-means": (
        "- Never re-read",
        (
            'A hit whose follow-up calls offer no source call (no depth="source", no '
            "read_file) has already shown its whole span.",
            'When a view says lines are "not in the index", read exactly those lines, once.',
        ),
    ),
    "rule-12-the-card-only-for-a-member-list": (
        "- To see a symbol's code",
        (
            'To see a symbol\'s code, make one get_symbol call with depth="source" or one '
            "windowed read_file call.",
            "Ask for the symbol card (get_symbol's default depth) only when the question "
            "needs its member list.",
        ),
    ),
}


@pytest.mark.parametrize(
    ("opening", "v3_sentences"),
    list(_SERVER_LINES_CARRYING_V3_RULES.values()),
    ids=list(_SERVER_LINES_CARRYING_V3_RULES),
)
def test_the_server_instruction_line_carries_the_system_v3_rule(
    opening: str, v3_sentences: tuple[str, ...]
) -> None:
    from pydocs_mcp.harness.core.prompts import render_core_prompt

    # The template wraps its rules across lines, so v3 is matched unwrapped.
    system_v3 = " ".join(render_core_prompt("system_v3").split())
    line = _server_instruction_line(opening)
    for sentence in v3_sentences:
        assert sentence in system_v3, f"system_v3 no longer says {sentence!r}"
        assert sentence in line, f"the {opening!r} line omits {sentence!r}"


def test_the_call_inducing_wording_is_gone_from_the_server_instructions() -> None:
    """No obligation to issue every "Together:" call, no card-first rule, and no
    "ask for a different depth instead": on a hit that has already shown its
    whole span, another depth is the same text again."""
    instructions = _served_instructions()
    assert "issue all of them in ONE turn" not in instructions
    assert 'before asking for depth="source"' not in instructions
    assert "ask for a different depth" not in instructions
