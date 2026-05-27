# Tool descriptions: MCP + CLI improvements — design

**Status:** spec — ready for implementation planning
**Tracks:** developer-experience / discoverability
**Related:** issue #12 (background, research, references)
**Files to touch:** `python/pydocs_mcp/server.py`, `python/pydocs_mcp/__main__.py`, new `tests/test_tool_descriptions.py`

---

## 1. Goal

Improve discoverability of `pydocs-mcp`'s two tools — `search` + `lookup` — for both audiences that consume them:

- **AI clients** reading MCP tool descriptions to decide which tool to call and how
- **CLI users** reading `--help` output

Today's descriptions are functional but understate three shipped capabilities: **project-code indexing** (under `__project__`), **hybrid retrieval** (BM25 + dense embeddings), and **reference-graph traversal** (`lookup --show callers/callees/inherits`).

## 2. Context

The MCP tool surface is fixed at 2 tools (`search` + `lookup`) per CLAUDE.md §"MCP API surface vs YAML configuration". This PR is **description-only** — no new tool params, no new tools, no new CLI flags.

Issue #12 carries the full research comparison of Context7, Brave Search MCP, Filesystem MCP, and Neuledge Context. Refer there for the audited patterns and anti-patterns.

## 3. Locked-in decisions

### Decision A — Description prose only (no params, no tools)

The MCP and CLI surfaces stay byte-identical. Only `description=` / `epilog=` / `help=` / `instructions=` change. AC checks must include: no new MCP tool params, no new CLI flags.

### Decision B — Server-level `FastMCP(instructions=...)` block

One server-level instructions string sets scope for the whole MCP session: which questions to route here, which to route elsewhere, the fixed 2-tool surface. Saves restating the frame on every per-tool docstring.

### Decision C — Lead with capability + decision rule, not algorithm

Tool descriptions name what the tool does for the user (hybrid keyword + semantic; reference graph traversal), NOT how (BM25, FTS5, RRF, TurboQuant). Implementation jargon belongs in `DOCUMENTATION.md`, not in the AI's tool list.

### Decision D — `__project__` priority signal in `--package` AND `--scope`

Both the MCP param doc and the CLI `--package` / `--scope` help text explicitly mention `__project__` and `scope=project` with the **priority rule**: "Use this when the user asks about THEIR code, not a library." Single line that drives correct routing.

### Decision E — Workflow framing on `lookup --show` modes

Each `show` mode carries a parenthetical "use to answer …" hint. Same prose works in MCP docstring AND CLI `--help`.

### Decision F — Tool annotations on MCP tools

Ship `readOnlyHint=True`, `idempotentHint=True`, `openWorldHint=True` on both `@mcp.tool()` decorators. Both tools read indexed data, are repeatable, and the indexed deps change over time (open world).

### Decision G — Test by `format_help()` substring assertions

Pin description content via `argparse.ArgumentParser.format_help()` (for CLI) and direct docstring inspection (for MCP). Assertions check for required substrings (capability claims) + absence of forbidden substrings (internal jargon).

### Decision H — NO `Co-Authored-By:` trailers

Every commit on this branch sole-authored by `msobroza`. Standing global rule.

## 4. Scope

### 4.1 In scope

1. **Server-level `FastMCP(instructions=...)`** at `server.py:115`. Single string covering: project + deps indexing, hybrid retrieval, fixed 2-tool surface, when to use this server vs Context7 / web search.
2. **`search` MCP docstring rewrite** — lead with the differentiator, `When to use` line, params with examples, `__project__` priority signal, output shape teaser.
3. **`lookup` MCP docstring rewrite** — lead with reference-graph capability, workflow framing for `show` modes ("use to answer 'who uses X?'"), `__project__` mention, output shape teaser.
4. **Tool annotations** — `readOnlyHint=True`, `idempotentHint=True`, `openWorldHint=True` on both `@mcp.tool()` decorators.
5. **CLI `search` subparser** — `description=` (first-line differentiator + `__project__` signal), `epilog=` with 4 concrete examples, fill missing `help=` on `--scope`, `--limit`, `--project-dir`, expand `--package` with `__project__` priority signal.
6. **CLI `lookup` subparser** — `description=` (reference-graph framing), `epilog=` with 5 examples, fill missing `help=` on `--show` with workflow framing, expand `target` help with examples.
7. **New tests** at `tests/test_tool_descriptions.py` (or merged into existing tests):
   - MCP `search` description contains: `hybrid`, `project`, `dependency`
   - MCP `lookup` description contains: `callers`, `callees`, `inherits`, `reference graph`
   - Server `instructions` mentions `__project__` and "two tools only"
   - MCP tool annotations set (`readOnlyHint`, `idempotentHint`, `openWorldHint`)
   - CLI `search --help` contains: `hybrid`, `__project__`, `Examples:`
   - CLI `lookup --help` contains: `reference graph`, `who uses X?`, `Examples:`
   - Every CLI flag on both subparsers has non-empty `help=` (no silent `--scope` / `--limit` / `--show`)
   - Forbidden-jargon scan: neither MCP nor CLI descriptions contain `PR #`, `sub-PR`, `Task N of`, `RRF`, `FTS5`, `TurboQuant`

### 4.2 Out of scope

- New MCP tools (the surface stays at 2).
- New MCP tool parameters.
- New CLI subcommands or flags.
- Implementation-stack jargon in user-facing prose (lives in `DOCUMENTATION.md`).
- `DOCUMENTATION.md` / `README.md` rewrites (separate concern; this PR is server.py + __main__.py descriptions only).

## 5. Components touched

| Component | Change |
|---|---|
| `python/pydocs_mcp/server.py:115` | `FastMCP("pydocs-mcp", instructions=...)` |
| `python/pydocs_mcp/server.py:117-153` | `search` docstring rewrite + tool annotations |
| `python/pydocs_mcp/server.py:155-188` | `lookup` docstring rewrite + tool annotations |
| `python/pydocs_mcp/__main__.py` (around lines 122-138) | `search` subparser: `description=`, `epilog=`, filled `help=` strings |
| `python/pydocs_mcp/__main__.py` (around lines 140-155) | `lookup` subparser: `description=`, `epilog=`, filled `help=` strings |
| `tests/test_tool_descriptions.py` | NEW: substring assertions on MCP + CLI prose |

Approximate LOC: **~30 MCP + ~40 CLI + ~80 tests = ~150 total.**

## 6. Risks

### R1 — Description length bloat

Every byte of MCP tool description ships on every tool-list request. Overshoot → context-window cost on every AI session.

**Mitigation:** keep server-level `instructions` to ~5 sentences, each tool docstring to <2KB. Existing docstrings are ~600 bytes each; targeting ~1.2KB each. Test asserts on substring presence, not exact text, so future tightening is unblocked.

### R2 — Drift between MCP docstring and CLI help

DRY-by-prose risks the two falling out of sync. Particularly for the `show` workflow framing.

**Mitigation:** the WORKFLOW HINTS are inherently the same content (`"callers — use to answer 'who uses X?'"`) for both audiences. Tests pin the substring "who uses X?" appears in BOTH the MCP `lookup` docstring AND the CLI `--show` help. If they drift, both fail.

### R3 — `argparse.ArgumentParser.format_help()` formatting differences across Python versions

`format_help()` output differs slightly between Python 3.11, 3.12, 3.13 (argparse internals shift). Substring assertions are robust to whitespace; exact-text assertions would be brittle.

**Mitigation:** all assertions are case-insensitive substring checks (`assert "hybrid" in help_text.lower()`).

### R4 — Tool annotations not surfaced by all MCP clients

Some MCP clients ignore `readOnlyHint` / `idempotentHint` / `openWorldHint`. They're advisory.

**Mitigation:** they cost nothing to ship; clients that read them benefit; clients that ignore them get no regression.

### R5 — FastMCP `instructions=` parameter availability

FastMCP's `instructions=` parameter has been in the library since v0.2.x (verify in the version pinned by this project before implementing). If absent, the alternative is putting the server-level frame into a privileged `prompt`-style tool — heavier, not preferred.

**Mitigation:** survey first; implementer confirms before writing the test.

## 7. Acceptance criteria

1. **AC-1 — `FastMCP(instructions=...)` set** with text mentioning `__project__`, "two tools only", and a `Do NOT use for:` line.
2. **AC-2 — `search` MCP docstring** starts with the capability+differentiator sentence; contains `hybrid`, `project`, `dependency`.
3. **AC-3 — `lookup` MCP docstring** starts with the reference-graph capability; contains `callers`, `callees`, `inherits`, `reference graph`.
4. **AC-4 — `__project__` priority signal** appears in BOTH the MCP `package` param doc AND the CLI `--package` help.
5. **AC-5 — `lookup --show` workflow framing** ("use to answer 'who uses X?'") appears in BOTH the MCP `show` param doc AND the CLI `--show` help.
6. **AC-6 — Tool annotations** — both `@mcp.tool()` decorators carry `readOnlyHint=True, idempotentHint=True, openWorldHint=True`.
7. **AC-7 — CLI `search --help`** contains `hybrid`, `__project__`, `Examples:`. All flags carry non-empty `help=`.
8. **AC-8 — CLI `lookup --help`** contains `reference graph`, `who uses X?`, `Examples:`. All flags carry non-empty `help=`.
9. **AC-9 — Forbidden-jargon scan clean** — no `PR #`, `sub-PR`, `Task N of`, `RRF`, `FTS5`, `TurboQuant` in any MCP or CLI prose.
10. **AC-10 — Authorship audit clean** — every commit sole-authored by `msobroza`, no `Co-Authored-By` trailers.
11. **AC-11 — Full test suite green** — baseline + new tests pass; ruff clean.
12. **AC-12 — No surface change** — MCP tool params unchanged; CLI flag set unchanged. (`pytest tests/test_main_cli.py` and related interface tests still pass.)

## 8. Open items (resolved during implementation)

- **O1** — Confirm `FastMCP(instructions=...)` parameter exists in the MCP package version pinned by `pyproject.toml`. Survey first (Risk R5).
- **O2** — Confirm whether `@mcp.tool()` accepts `readOnlyHint=` etc. directly or via a wrapper.
- **O3** — Decide whether to ship a single test file `tests/test_tool_descriptions.py` or two (`test_mcp_tool_descriptions.py` + `test_cli_help_descriptions.py`). Lean: single file unless it grows past ~150 lines.

## 9. Next step

Invoke `superpowers:writing-plans` to produce the TDD task plan, then ship via `superpowers:subagent-driven-development` with the same 4-reviewer pattern as PR #49 (spec-compliance + code-quality + gstack + simplify).
