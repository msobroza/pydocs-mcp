"""Ask-your-docs agent — a LangGraph ReAct agent over pydocs-mcp.

agent, llm = await build_agent("~/pydocs-index", model="gpt-4o-mini")
history: list = []
answer = await ask(agent, history, "how do I open a database pool?",
                   scope={"project": "backend"})
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import sys

from langchain_core.messages import AIMessage, HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from pydocs_mcp.ask_your_docs.catalog import render_catalog, workspace_catalog

logger = logging.getLogger(__name__)

# A corpus pin. Keys: "project", "package", and "code" ("all" | "project" |
# "deps" — forwarded as search_codebase's ``scope`` argument).
ToolScope = dict[str, str]

# The active pin for the CURRENT question. ``ask`` sets this inside its own
# coroutine, so two concurrent questions (e.g. two browser tabs sharing one
# cached agent) each read their own frozen snapshot — no shared mutable state.
# Default is None (never a shared mutable dict); readers coalesce to {}.
_active_scope: contextvars.ContextVar[ToolScope | None] = contextvars.ContextVar(
    "active_scope", default=None
)

# Which corpus filters each tool actually accepts (see pydocs_mcp.server):
# ``project`` — all six tools; ``package`` — search_codebase + get_overview;
# ``scope`` (own vs deps) — search_codebase only. The interceptor forces a pin
# only where the tool can honor it.
_PACKAGE_TOOLS = frozenset({"search_codebase", "get_overview"})

SYSTEM_PROMPT = """\
You are a documentation and code assistant for the indexed projects listed below.
You answer ONLY from the results of your tools — never from memory:

- `search_codebase(query, kind, package, scope, limit, project)` — topics,
  keywords, "how do I..." questions. Use kind="docs" for prose, kind="api" for
  functions/classes. Use project="<name>" to scope one repo, package="<name>"
  for one library, scope="project"|"deps" to split own-code vs dependencies.
- `get_symbol(target, depth, project)` — exact dotted paths
  (pkg.mod.Class.method); depth="source" for the full body.
- `get_references(target, direction, project)` — code-graph questions:
  direction="callers" (who uses X), "callees", "inherits",
  "impact" (what breaks if X changes).
- `get_context(targets, project)` — everything needed to understand one or
  more symbols in a single call.
- `get_overview(package, project)` — the shape of a repo or package; empty
  package = the project's own code. The full project/package catalog is
  already listed below — don't call this just to discover what exists.
- `get_why(query, targets, project)` — recorded design decisions and rationale.

Rules:
1. Users often don't know the framework or project name. Infer it from the
   task and the indexed-projects list below. If unsure, search UNSCOPED first
   (all projects) and let the results identify the owner, then narrow.
2. Rewrite follow-up questions into self-contained queries (resolve "it",
   "that function", ... from the conversation) before calling a tool.
3. If results stay ambiguous across projects, or the request is unclear, ask
   ONE short clarifying question instead of guessing.
4. Be concise. Cite the project and package.module for every claim. Put
   signatures and code in fenced code blocks. If the tools found nothing,
   say so plainly — do not invent an answer.
5. Whenever the results describe a usable function or class, end with a SHORT
   "Example" snippet in a fenced ```python block showing a typical call —
   assembled strictly from the retrieved signatures and docstrings (use
   get_symbol with depth="source" when you need the exact signature). Never
   invent parameters, defaults, or return shapes the tools did not show.
6. A question may carry a "[pinned scope: ...]" note set by the app. The app
   already applies those filters to your tool calls for you (the project on
   every tool; the package and own-vs-dependency filters on the search tools),
   so don't fight them or re-ask which project the user means. If a search
   comes back empty, say the pinned scope may be too narrow and suggest
   widening it.
"""

REWRITE_PROMPT = """\
Rewrite the user's last question as ONE self-contained question, resolving any
references to the earlier conversation. Return only the rewritten question.

Conversation:
{history}

Last question: {question}
"""


async def _intercept(request: MCPToolCallRequest, handler):
    """Force the active question's pin onto every MCP tool call.

    Reads the pin from a contextvar rather than a shared dict, so the LLM
    cannot forget or override it and concurrent questions stay isolated.
    """
    scope = _active_scope.get() or {}
    args = dict(request.args)
    if scope.get("project"):
        args["project"] = scope["project"]
    if request.name in _PACKAGE_TOOLS and scope.get("package"):
        args["package"] = scope["package"]
    if request.name == "search_codebase" and scope.get("code", "all") != "all":
        args["scope"] = scope["code"]
    if args != request.args:
        logger.debug("scope pin applied: tool=%s args=%s", request.name, args)
    return await handler(request.override(args=args))


def scope_prefix(scope: ToolScope) -> str:
    """The "[pinned scope: ...]" note prepended to a question, or ""."""
    parts = []
    if scope.get("project"):
        parts.append(f"project={scope['project']}")
    if scope.get("package"):
        parts.append(f"package={scope['package']}")
    if scope.get("code", "all") != "all":
        parts.append("own code only" if scope["code"] == "project" else "dependencies only")
    return f"[pinned scope: {', '.join(parts)}] " if parts else ""


async def build_agent(
    workspace: str,
    model: str,
    base_url: str | None = None,
    pydocs_config: str | None = None,
    pydocs_cmd: list[str] | None = None,
    catalog: dict[str, list[str]] | None = None,
):
    """Start pydocs-mcp over the workspace; return ``(agent, llm)``.

    Pass ``catalog`` (from :func:`ask_your_docs.catalog.workspace_catalog`) to
    reuse a scan the caller already did — this keeps the prompt's project list
    identical to whatever the UI shows. When omitted it is scanned here.

    ``pydocs_cmd`` defaults to ``[sys.executable, "-m", "pydocs_mcp"]`` so the
    MCP server subprocess always runs under the SAME interpreter as this app —
    no reliance on ``pydocs-mcp`` being on the child's PATH.
    """
    command, *prefix = pydocs_cmd or [sys.executable, "-m", "pydocs_mcp"]
    # --config is a root flag: it must come BEFORE the serve subcommand.
    config = ["--config", pydocs_config] if pydocs_config else []
    args = [*prefix, *config, "serve", "--workspace", workspace]
    client = MultiServerMCPClient(
        {"pydocs": {"transport": "stdio", "command": command, "args": args}},
        tool_interceptors=[_intercept],
    )
    tools = await client.get_tools()

    # Fold the full project/package catalog into the prompt so the model can
    # pick the right project= / package= filters itself. Built from the bundle
    # files directly: in workspace mode, get_overview(project="") describes only
    # the default project, so it can't produce this listing.
    if catalog is None:
        catalog = await asyncio.to_thread(workspace_catalog, workspace)
    prompt = f"{SYSTEM_PROMPT}\nIndexed projects and packages:\n{render_catalog(catalog)}"

    llm = ChatOpenAI(model=model, base_url=base_url)
    return create_react_agent(llm, tools, prompt=prompt), llm


async def reformulate(llm: ChatOpenAI, history: list, question: str) -> str:
    """Condense the last question + conversation into a standalone question."""
    if not history:
        return question
    lines = "\n".join(f"{m.type}: {m.content}" for m in history)
    reply = await llm.ainvoke(REWRITE_PROMPT.format(history=lines, question=question))
    return str(reply.content).strip() or question


async def ask(
    agent,
    history: list,
    question: str,
    scope: ToolScope | None = None,
    max_history: int = 8,
) -> str:
    """One conversation turn under ``scope``; updates ``history`` in place.

    The pin is applied two ways: forced onto every tool call (via the contextvar
    the interceptor reads) and surfaced to the model as a "[pinned scope: ...]"
    note. Only the note is transient — ``history`` keeps the BARE question, so a
    later scope change can't leak a stale pin into reformulation or the answer.
    """
    scope = scope or {}
    token = _active_scope.set(scope)
    try:
        prefixed = scope_prefix(scope) + question
        result = await agent.ainvoke({"messages": [*history, HumanMessage(prefixed)]})
        answer = result["messages"][-1].content
    finally:
        _active_scope.reset(token)
    history += [HumanMessage(question), AIMessage(answer)]
    del history[:-max_history]
    return answer
