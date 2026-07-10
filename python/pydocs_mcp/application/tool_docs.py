"""Single source of truth for tool documentation (spec §D13).

``TOOL_DOCS[name]`` becomes the MCP tool description AND (first line only) the
CLI subcommand help text; ``SERVER_INSTRUCTIONS`` is the FastMCP server-level
orientation.
The §D13 lint test enforces the six-section structure and size budgets, so
edits here fail fast instead of drifting.
"""

from __future__ import annotations

# --- §D13 contract constants (importable: the offline optimizer's validate()
# shares them; drift here is drift in the lint) ---
REQUIRED_MARKERS = (
    "When to use",
    "When NOT to use",
    "Workflow",
    "Response contract",
    "Examples",
)
CHARS_PER_TOKEN = 4
PER_TOOL_TOKEN_BUDGET = 500
TOTAL_TOKEN_BUDGET = 2400

_WORKFLOW = (
    "Workflow: get_overview → search_codebase → get_context → "
    "get_symbol / get_references; get_why before architectural changes."
)
_CONTRACT = (
    "Response contract: every response starts with an [index: …] freshness "
    "line — silence means current; a [⚠ index stale…] line means re-index "
    "before trusting details. Code-backed hits end with a ready-made "
    "follow-up call. Elided content carries a recovery pointer whenever a "
    "target is resolvable."
)

TOOL_DOCS: dict[str, str] = {
    "get_overview": f"""Orient yourself: what is indexed and what shape this repo/package has.

When to use: first call on an unfamiliar project; refreshing your map after a re-index; checking what packages/modules exist before searching. With several projects loaded, the no-argument call lists them all — scope with project= to go deeper.
When NOT to use: you already know a dotted path (get_symbol) or a topic (search_codebase).
{_WORKFLOW}
{_CONTRACT}
Examples:
  get_overview()
  get_overview(package="fastapi")
  get_overview(package="__project__", project="backend")
""",
    "search_codebase": f"""Find code, docs, or decisions about a topic you can't name exactly.

When to use: keyword/concept/partial-name queries; "how do I X"; "where is the code for X".
When NOT to use: you know the exact dotted path (get_symbol); you're asking WHY code is designed a certain way (get_why).
kind="decision" searches recorded design decisions (get_why is the richer entry).
{_WORKFLOW}
{_CONTRACT}
Examples:
  search_codebase(query="batch inference", kind="docs")
  search_codebase(query="retry logic", package="requests")
  search_codebase(query="our parser", scope="project", project="backend")
""",
    "get_symbol": f"""Details — or verbatim source — for a dotted path you already know.

When to use: known package/module/class/method paths; depth="tree" for the full nested subtree; depth="source" for the verbatim source, up to the configured line cap (this is how you recover content a truncated response elided).
When NOT to use: you only have a keyword (search_codebase); you want the whole dependency closure (get_context).
{_WORKFLOW}
{_CONTRACT}
Examples:
  get_symbol(target="fastapi.routing.APIRouter")
  get_symbol(target="pkg.mod.BigClass", depth="source")
  get_symbol(target="app.db.Pool", depth="tree", project="backend")
""",
    "get_context": f"""Everything needed to understand one or more targets, packed under a token budget.

When to use: before reading or modifying code — one call replaces separate doc/signature/dependency reads. Pass ALL targets (up to 20) in ONE call: one shared budget beats N sequential calls.
When NOT to use: single known symbol, full source wanted (get_symbol); pure who-calls-what (get_references).
{_WORKFLOW}
{_CONTRACT}
Examples:
  get_context(targets=["pydocs_mcp.retrieval.pipeline"])
  get_context(targets=["pkg.mod.A", "pkg.mod.B"], project="backend")
""",
    "get_references": f"""Who calls X, what X calls, what X extends, what breaks if X changes, or which decisions govern X.

When to use: direction="callers" for usage sites; "callees" for dependencies; "inherits" for base classes; "impact" for the ranked transitive blast radius before a risky change; "governed_by" for the mined decisions that govern this symbol.
When NOT to use: you want source or docs (get_symbol / get_context).
{_WORKFLOW}
{_CONTRACT}
Examples:
  get_references(target="fastapi.routing.APIRouter.include_router", direction="callers")
  get_references(target="pkg.mod.f", direction="impact", project="backend")
""",
    "get_why": f"""Why is this code the way it is — which recorded decisions govern it?

When to use: before proposing architectural changes; questions like "why sqlite here?"; pass ALL symbols of interest (up to 20) in ONE call via targets. No arguments = governance dashboard.
When NOT to use: what/where questions (search_codebase); implementation details (get_symbol).
{_WORKFLOW}
{_CONTRACT}
Examples:
  get_why(query="why are vectors in a sidecar file")
  get_why(targets=["pydocs_mcp.db"], project="backend")
""",
}

SERVER_INSTRUCTIONS = (
    "pydocs-mcp indexes your project's source AND every installed dependency "
    "into a local hybrid index (dense embeddings + BM25 + a reference graph). "
    "Use it before web search for: installed-library APIs, symbols in the "
    'user\'s own code (package "__project__"), call-graph navigation, and '
    "design rationale. Six task-shaped tools: "
    + _WORKFLOW
    + " "
    + _CONTRACT
    + " Do NOT use for libraries that aren't installed here (use web search)."
)
