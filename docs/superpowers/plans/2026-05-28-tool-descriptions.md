# Tool descriptions: MCP + CLI improvements — TDD plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`. 5 tasks. Each task: failing test → verify FAIL → minimal impl → verify PASS → commit.

**Spec:** `docs/superpowers/specs/2026-05-28-tool-descriptions-design.md`
**Worktree:** `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/tool-descriptions/`
**Branch:** `feature/improve-tool-descriptions`
**Baseline:** main at `0b96fe6`, 1409 unit + 283 benchmark tests passing.

---

## Goal

Rewrite MCP tool descriptions + CLI argparse help to surface the three shipped capabilities currently invisible to AI clients and CLI users: project-code indexing (`__project__`), hybrid retrieval (BM25 + dense embeddings), and reference-graph traversal (`lookup --show callers/callees/inherits`).

## Architecture

The implementation is prose-only — no new tool params, no new flags, no new tools. Edits land in three files: `python/pydocs_mcp/server.py` (MCP), `python/pydocs_mcp/__main__.py` (CLI), and a new `tests/test_tool_descriptions.py` (pins).

**Two seams to update:**

1. **MCP side** (`server.py`):
   - `FastMCP("pydocs-mcp", instructions=<server-level frame>)` — sets server scope once for the AI session.
   - `@mcp.tool(readOnlyHint=True, idempotentHint=True, openWorldHint=True)` annotations on both tools.
   - `search` + `lookup` docstrings rewritten per Decisions B-E.

2. **CLI side** (`__main__.py`):
   - `sub.add_parser("search", description=..., epilog=...)` and same for `lookup`.
   - Every flag carries a non-empty `help=` (today `--scope`, `--limit`, `--show`, `--project-dir` are silent).
   - `__project__` + workflow-framing prose mirrors the MCP-side wording.

**Tests** pin substring presence + jargon absence on both audiences. `argparse.ArgumentParser.format_help()` exposes the CLI text; FastMCP docstring fields are inspectable via the decorated function's `__doc__`.

## Tech stack

- Python 3.11+ (existing project requirement)
- No new runtime deps — uses existing `mcp` (FastMCP) + stdlib `argparse`
- Tests run under existing `pytest` setup
- Python interpreter: `/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python`

## Tech-stack reminders the implementer MUST mirror

- **NO `Co-Authored-By:` trailer** in any commit message (global rule from `~/.claude/CLAUDE.md`).
- One commit per task. Body in HEREDOC.
- Comments explain WHY, not WHAT (CLAUDE.md §"Code Comments").
- No internal jargon in user-facing prose (CLAUDE.md §"README files" — same rule applies to MCP / CLI descriptions).
- Single source of truth for defaults — both MCP and CLI use the same `__project__` priority signal sentence; if it changes in one place it must change in the other (Risk R2 mitigation).

---

## Task 1 — Survey + write failing test file

**Goal:** confirm FastMCP supports `instructions=` and `@mcp.tool()` accepts annotation kwargs. Write the full test file with all 12 AC pins (all FAIL initially).

**Survey:**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/tool-descriptions
/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -c "
import inspect
from mcp.server.fastmcp import FastMCP
print('FastMCP __init__ params:')
print(inspect.signature(FastMCP.__init__))
print()
print('FastMCP.tool params:')
print(inspect.signature(FastMCP.tool))
"
```

Expected: `FastMCP.__init__` has `instructions: str = ''` (or similar). `FastMCP.tool` accepts annotation kwargs. If NOT, the implementer reports BLOCKED — the spec needs a fallback design.

**Step 1 — Write failing tests.** Create `tests/test_tool_descriptions.py`:

```python
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
        pattern = rf'async def {fn_name}\([^)]*\)[^:]*:\s*\"\"\"(.*?)\"\"\"'
        m = re.search(pattern, src_text, re.DOTALL)
        return m.group(1) if m else ""

    search_doc = _extract_docstring(src, "search")
    lookup_doc = _extract_docstring(src, "lookup")

    # Server-level instructions: pull from the FastMCP() call.
    inst_match = re.search(
        r"FastMCP\([^)]*instructions\s*=\s*([\"']{1,3})(.*?)\1",
        src, re.DOTALL,
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
    l = lookup.lower()
    assert "callers" in l
    assert "callees" in l
    assert "inherits" in l
    assert "reference graph" in l or "reference-graph" in l


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
    subparsers_action = next(
        a for a in parser._actions if hasattr(a, "choices") and a.choices
    )
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
        assert m is None, f"forbidden jargon '{m.group()}' (pattern {pattern!r}) found in user-facing prose"
```

**Step 2 — Verify FAIL.**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/tool-descriptions
/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/test_tool_descriptions.py -q
```

Expected: ~9 of 10 tests fail (no `instructions=`, docstrings lack `hybrid`/`reference graph`, CLI flags silent, etc.). The "no internal jargon" test likely PASSES already.

**Step 3 — Commit (test file + spec/plan).**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/tool-descriptions
git add docs/superpowers/specs/2026-05-28-tool-descriptions-design.md \
        docs/superpowers/plans/2026-05-28-tool-descriptions.md \
        tests/test_tool_descriptions.py
git commit -m "$(cat <<'EOM'
plan + tests: improve MCP + CLI tool descriptions (closes #12)

Spec at docs/superpowers/specs/2026-05-28-tool-descriptions-design.md
documents the 8 locked decisions; plan splits delivery into 5 TDD
tasks. The pinning test file lands first (TDD red) so subsequent
commits show the green transition.

10 assertions across MCP + CLI surfaces:
  - server-level FastMCP instructions= present and scoped
  - search docstring announces hybrid + project + deps capability
  - lookup docstring announces reference-graph traversal
  - __project__ priority signal in search params
  - "use to answer 'who uses X?'" workflow framing on lookup.show
  - readOnlyHint / idempotentHint / openWorldHint on both tools
  - CLI search --help carries hybrid + __project__ + Examples
  - CLI lookup --help carries reference graph + who uses X + Examples
  - Every flag on both subparsers has non-empty help= (today --scope,
    --limit, --show, --project-dir are silent)
  - Forbidden-jargon scan: no PR #N, sub-PR, Task N of, RRF, FTS5,
    TurboQuant in any user-facing prose.

All assertions are substring-based and case-insensitive so future
prose tightening is unblocked.
EOM
)"
```

---

## Task 2 — MCP side: `server.py` rewrite

**Goal:** add `FastMCP(instructions=...)`, tool annotations, rewrite `search` + `lookup` docstrings. Tests AC-1 through AC-6 + AC-9 turn green.

**Survey:** confirm the MCP package supports `instructions=` and tool annotations (from Task 1).

**Step 1 — Implement.** Edit `python/pydocs_mcp/server.py`:

(a) `FastMCP("pydocs-mcp", instructions=...)` at line 115:

```python
_SERVER_INSTRUCTIONS = (
    "pydocs-mcp indexes your current project's source code AND every "
    "installed dependency into a local hybrid (BM25 + dense embeddings) "
    "index. Use this server before web search whenever the user asks "
    "about: an installed library's API, a function/class in their own "
    "project, who-calls-what / call graph navigation, or `__project__` "
    "modules. The surface is two tools only — `search` and `lookup` — "
    "pick `search` for keyword/topic queries and `lookup` for known "
    "dotted paths or reference-graph traversal. Do NOT use for: "
    "refactoring, writing new code from scratch, runtime debugging, "
    "or libraries that aren't installed in this project (use Context7 "
    "or web search for those)."
)

mcp = FastMCP("pydocs-mcp", instructions=_SERVER_INSTRUCTIONS)
```

(b) `@mcp.tool(readOnlyHint=True, idempotentHint=True, openWorldHint=True)` on both `search` and `lookup` decorators.

(c) `search` docstring rewrite:

```python
    @mcp.tool(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
    async def search(
        query: str,
        kind: str = "any",
        package: str = "",
        scope: str = "all",
        limit: int = 10,
    ) -> str:
        """Hybrid keyword + semantic search across your project's source AND every installed dependency (docs + code).

        When to use this tool:
          - Topic, keyword, concept, or partial name (you don't know the exact dotted path)
          - "How do I do X" / "Where is the code for X" style questions
          - Use `lookup` instead if you know the exact dotted path OR want to walk the code graph

        Params:
          query:   search terms (space-separated; both prose and identifiers work)
          kind:    "docs" (prose / README chunks) | "api" (functions / classes) | "any" (default)
          package: restrict to one package (e.g. "fastapi"). Use "__project__" for the USER's
                   code, not a library. "" = all packages.
          scope:   "project" (user's code only) | "deps" (installed deps only) | "all" (default).
                   Use scope="project" or package="__project__" when the user asks about THEIR
                   code, not a library — this is the most common routing mistake to avoid.
          limit:   max results 1-1000, default 10.

        Examples:
          search(query="batch inference", kind="docs")
          search(query="HTTPBasicAuth", kind="api")
          search(query="retry logic", package="requests")
          search(query="our parser", scope="project")
          search(query="ValidationError", package="__project__")

        Returns markdown with up to `limit` ranked hits, each block carrying
        package, module path, and a code/docs excerpt.
        """
```

(d) `lookup` docstring rewrite:

```python
    @mcp.tool(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
    async def lookup(target: str = "", show: str = "default") -> str:
        """Navigate to a known symbol (dotted path) and optionally traverse its reference graph — callers, callees, base classes.

        When to use this tool:
          - You know the exact dotted path of a package / module / class / method
          - You want to walk the code graph from a known symbol (who calls X, what X calls)
          - Use `search` instead if you only have a keyword, topic, or partial name

        Params:
          target: dotted path
            ""                                          → list all indexed packages
            "fastapi"                                   → package overview + deps
            "fastapi.routing"                           → module tree
            "fastapi.routing.APIRouter"                 → class + children
            "fastapi.routing.APIRouter.include_router"  → method details
            "__project__.my_module.MyClass"             → YOUR class (not a library)

          show:
            "default"  → symbol summary + immediate children (start here)
            "tree"     → full nested subtree (use when "default" is too shallow)
            "callers"  → every site that calls/references this symbol — use to answer "who uses X?"
            "callees"  → every symbol this calls — use to answer "what does X depend on?"
            "inherits" → base classes and interface chain — use to answer "what does X extend?"

        Examples:
          lookup(target="")
          lookup(target="fastapi.routing.APIRouter")
          lookup(target="fastapi.routing.APIRouter.include_router", show="callers")
          lookup(target="requests.auth.HTTPBasicAuth", show="inherits")

        Returns markdown — exact shape varies by `show` mode (a summary block
        for "default", a tree for "tree", a list of caller / callee entries
        for the graph modes).
        """
```

**Step 2 — Verify PASS.**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/tool-descriptions
/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/test_tool_descriptions.py -q
/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest -q
/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m ruff check python/ tests/
```

Expected: 7 MCP-side tests PASS; CLI-side tests still FAIL (they land in Task 3); full suite no regressions.

**Step 3 — Commit.**

```bash
git add python/pydocs_mcp/server.py
git commit -m "$(cat <<'EOM'
mcp: rewrite search + lookup descriptions + add server-level instructions

Spec decisions B-F land here.

  - FastMCP(instructions=...) — session-level scope frame. Tells the AI
    when to reach for pydocs-mcp (installed libraries, user's project
    code, call graph) vs Context7 / web search. Pins the fixed 2-tool
    surface so the AI doesn't try to synthesize list_packages /
    get_doc.

  - search docstring leads with "Hybrid keyword + semantic search across
    your project's source AND every installed dependency". Workflow
    block at the top distinguishes search vs lookup. __project__
    priority signal explicit in the package + scope param docs.

  - lookup docstring leads with the reference-graph capability. show
    modes carry "use to answer ..." framing — Neuledge-style workflow
    encoding instead of a bare menu.

  - Both tools ship readOnlyHint=True, idempotentHint=True,
    openWorldHint=True per FastMCP spec — advisory hints for MCP
    clients that surface tool semantics.

No new tool params; no new tools. Description prose only.
EOM
)"
```

---

## Task 3 — CLI side: `__main__.py` rewrite

**Goal:** add `description=` + `epilog=` + fill missing `help=` on both subparsers. CLI tests turn green.

**Step 1 — Implement.** Edit `python/pydocs_mcp/__main__.py` around the `sub.add_parser("search", ...)` and `sub.add_parser("lookup", ...)` blocks:

```python
    sp_search = sub.add_parser(
        "search",
        help="Hybrid keyword + semantic search over project + deps",
        description=(
            "Hybrid keyword + semantic search across your project's source AND every "
            "installed dependency (docs + code), ranked by BM25 plus dense embeddings. "
            "Use --package __project__ or --scope project to restrict to YOUR code, "
            "not a library."
        ),
        epilog=(
            "Examples:\n"
            "  pydocs-mcp search 'batch inference' --kind docs\n"
            "  pydocs-mcp search HTTPBasicAuth --kind api\n"
            "  pydocs-mcp search 'retry logic' --package requests\n"
            "  pydocs-mcp search parser --scope project\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_search.add_argument("query", help="Search terms (space-separated; prose AND identifiers work)")
    sp_search.add_argument(
        "--kind", choices=["docs", "api", "any"], default="any",
        help="Which index to search: 'docs' = prose / README, 'api' = functions / classes, 'any' = both (default).",
    )
    sp_search.add_argument(
        "-p", "--package", dest="package", default="",
        help='Restrict to one package (e.g. "fastapi"). Use "__project__" for YOUR code, not a library. Default: all packages.',
    )
    sp_search.add_argument(
        "--scope", choices=["project", "deps", "all"], default="all",
        help='Restrict by scope: "project" = your code only, "deps" = installed deps only, "all" = both (default). Use "project" when the user asks about THEIR code.',
    )
    sp_search.add_argument(
        "--limit", type=int, default=10,
        help="Max number of results (1-1000, default: 10).",
    )
    sp_search.add_argument(
        "--project-dir", dest="project", default=".",
        help="Path to the project root (default: current directory). Determines which cache database is loaded.",
    )
    sp_search.add_argument("--no-rust", **_no_rust)
    sp_search.add_argument("--cache-dir", **_cache_dir)
    sp_search.add_argument("-v", "--verbose", **_verbose)

    sp_lookup = sub.add_parser(
        "lookup",
        help="Navigate to a known symbol + walk its reference graph",
        description=(
            "Navigate to a known symbol (dotted path) and optionally traverse its "
            "reference graph — callers, callees, base classes. Use this when you "
            "know the exact target; use 'search' when you only have a keyword or topic."
        ),
        epilog=(
            "Examples:\n"
            "  pydocs-mcp lookup                                                           # list all indexed packages\n"
            "  pydocs-mcp lookup fastapi                                                   # package overview\n"
            "  pydocs-mcp lookup fastapi.routing.APIRouter                                 # class + members\n"
            "  pydocs-mcp lookup fastapi.routing.APIRouter.include_router --show callers   # who calls this method\n"
            "  pydocs-mcp lookup requests.auth.HTTPBasicAuth --show inherits               # base classes\n"
            "  pydocs-mcp lookup __project__.my_module.MyClass                             # YOUR class, not a library\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_lookup.add_argument(
        "target", nargs="?", default="",
        help='Dotted path (e.g. "fastapi.routing.APIRouter"). Use "__project__.<module>.<symbol>" for YOUR code. Empty = list all indexed packages.',
    )
    sp_lookup.add_argument(
        "--show", choices=["default", "tree", "callers", "callees", "inherits"], default="default",
        help=(
            "What to show: 'default' = symbol summary + immediate children (start here); "
            "'tree' = full nested subtree (use when 'default' is too shallow); "
            "'callers' = who references this — use to answer 'who uses X?'; "
            "'callees' = what this calls — use to answer 'what does X depend on?'; "
            "'inherits' = base classes / interface chain — use to answer 'what does X extend?'."
        ),
    )
    sp_lookup.add_argument(
        "--project-dir", dest="project", default=".",
        help="Path to the project root (default: current directory). Determines which cache database is loaded.",
    )
    sp_lookup.add_argument("--no-rust", **_no_rust)
    sp_lookup.add_argument("--cache-dir", **_cache_dir)
    sp_lookup.add_argument("-v", "--verbose", **_verbose)
```

Note: `formatter_class=argparse.RawDescriptionHelpFormatter` preserves the line breaks in `epilog=`. Must import argparse if not already.

**Step 2 — Verify PASS.**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/tool-descriptions
/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/test_tool_descriptions.py -q
/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest -q
/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m ruff check python/ tests/
```

All 10 tests in `test_tool_descriptions.py` green; full suite no regressions.

**Step 3 — Commit.**

```bash
git add python/pydocs_mcp/__main__.py
git commit -m "$(cat <<'EOM'
cli: rewrite search + lookup subparser descriptions + fill missing help=

CLI users running `pydocs-mcp search --help` or `pydocs-mcp lookup --help`
previously saw silent --scope / --limit / --show / --project-dir flags
and no examples. This commit fills every gap.

Mirrors the MCP-side prose where the audience overlap is genuine
(the __project__ priority signal; the lookup.show workflow framing
"use to answer 'who uses X?'") so the two surfaces don't drift.

Adds:
  - sp_search.description: hybrid + __project__ capability claim
  - sp_search.epilog: 4 concrete usage examples
  - help= on --kind, --package, --scope, --limit, --project-dir
  - sp_lookup.description: reference-graph framing
  - sp_lookup.epilog: 6 concrete usage examples (including __project__)
  - help= on target, --show (workflow framing), --project-dir

No new flags. No new subcommands. argparse.RawDescriptionHelpFormatter
preserves the epilog line breaks for readable example blocks.
EOM
)"
```

---

## Task 4 — Audit + tighten (catch missed gaps)

**Goal:** re-run the verification gauntlet, check for any AC the impl missed, fix anything that broke during the rewrite.

**Step 1 — Verification gauntlet.**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/tool-descriptions
echo "=== Full Python suite ===" && \
/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest -q
echo "" && \
echo "=== Tool descriptions specifically ===" && \
/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/test_tool_descriptions.py -v
echo "" && \
echo "=== Lint ===" && \
/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m ruff check python/ tests/
echo "" && \
echo "=== Confirm CLI help renders ===" && \
/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pydocs_mcp search --help | head -30
echo "---"
/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pydocs_mcp lookup --help | head -30
echo "" && \
echo "=== Authorship audit ===" && \
git log 0b96fe6^..HEAD --format='%H %an <%ae>%n%(trailers:key=Co-Authored-By,unfold,only)' | grep -cE "Claude|noreply@anthropic" || echo 0
echo "(should be 0)"
```

**Step 2 — Address any failures.** If any test fails or any AC slipped, fixup commit. The fixup goes into Task 5 if the gap is genuine; otherwise we report DONE and skip Task 5.

**Step 3 — Print AC coverage matrix.**

```
AC #  Description                                          Test                                                     Verdict
AC-1  FastMCP(instructions=...) set                        test_server_instructions_set                             PASS
AC-2  search announces hybrid+project+deps                 test_search_docstring_announces_hybrid_project_dependency PASS
AC-3  lookup announces reference graph                     test_lookup_docstring_announces_reference_graph          PASS
AC-4  __project__ priority signal in MCP + CLI             test_search_documents_project_sentinel +                 PASS
                                                           test_cli_search_help_announces_capability
AC-5  show workflow framing                                test_lookup_show_modes_carry_workflow_framing            PASS
AC-6  Tool annotations                                     test_mcp_tools_have_readonly_idempotent_annotations      PASS
AC-7  CLI search --help carries hybrid, __project__, ex.   test_cli_search_help_announces_capability +              PASS
                                                           test_cli_search_flags_all_have_help_text
AC-8  CLI lookup --help                                    test_cli_lookup_help_announces_reference_graph +         PASS
                                                           test_cli_lookup_flags_all_have_help_text
AC-9  No internal jargon                                   test_no_internal_jargon_in_any_description               PASS
AC-10 Authorship clean                                     git log audit (manual)                                   PASS
AC-11 Full suite green                                     pytest -q                                                PASS
AC-12 No surface change                                    existing test_main_cli.py + test_main_cli_watch.py       PASS
```

**Step 4 — Commit any fixups** if needed, or note "no fixups required."

---

## Self-review

After all 4 tasks, verify against the spec:

1. **Spec coverage:** all 12 ACs map to a test. (Walked above.)
2. **Placeholder scan:** no `TBD` / `implement appropriately` / `as needed` left in code or commits.
3. **Type consistency:** `_SERVER_INSTRUCTIONS` is the constant name (module-level); referenced exactly that way at the `FastMCP(...)` call site.
4. **Author + jargon checks** ship as automated assertions (AC-9, AC-10).

If any task surfaces something the spec missed, file a follow-up issue and note it in the commit message; do NOT silently expand scope.
