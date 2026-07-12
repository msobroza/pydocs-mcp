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

from pydocs_mcp.ask_your_docs.architectures import (
    AgentArchitectureError,
    AgentBuildContext,
    agent_registry,
)

# weave_attachments moved to attachments.py (spec 2026-07-11-multimodal-image-
# agent §3.1); re-exported so app.py and existing tests keep this import path.
from pydocs_mcp.ask_your_docs.attachments import weave_attachments  # noqa: F401
from pydocs_mcp.ask_your_docs.catalog import render_catalog, workspace_catalog
from pydocs_mcp.ask_your_docs.multimodal import ModelCapabilities, detect_capabilities

# ALL prompt text is centralized under ask_your_docs/prompts/ (versioned .j2
# templates, one directory per architecture with a shared/ fallback).
# SYSTEM_PROMPT is re-exported here for its existing import path.
from pydocs_mcp.ask_your_docs.prompts import (
    SYSTEM_PROMPT,  # noqa: F401 — re-export for the existing import path
    prompts_for,
    rewrite_prompt,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

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

# The CURRENT question's session image store (name → ImageAttachment) for the
# reinspect_images tool. Same isolation rationale as _active_scope: the
# compiled agent graph is cached across sessions, so per-session state must
# ride a contextvar set inside ask(), never be baked into the tools.
_active_image_store: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "active_image_store", default=None
)

# Per-turn reinspect accounting: {"calls": <vision calls so far>, "memo":
# {(names, question): facts}} — fresh per ask() so the budget and the memo
# never leak across turns or sessions. Necessity gating: repeated same-args
# calls are free (memo) and a turn cannot exceed images.max_reinspect_per_turn
# vision calls.
_reinspect_state: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "reinspect_state", default=None
)

# Which corpus filters each tool actually accepts (see pydocs_mcp.server):
# ``project`` — all six tools; ``package`` — search_codebase + get_overview;
# ``scope`` (own vs deps) — search_codebase only. The interceptor forces a pin
# only where the tool can honor it.
_PACKAGE_TOOLS = frozenset({"search_codebase", "get_overview"})


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


def _build_architecture(
    name: str,
    *,
    llm,
    tools,
    prompt: str,
    capabilities: ModelCapabilities,
    config: AskYourDocsConfig,
    model: str,
):
    """Validate + build the named architecture (spec §3.4.4).

    Split out of :func:`build_agent` so tests exercise validation and graph
    construction without an MCP server subprocess.
    """
    arch_cls = agent_registry.get(name)
    if arch_cls is None:
        raise ValueError(f"unknown architecture {name!r}; known: {agent_registry.names()}")
    if arch_cls.requires_multimodal and not capabilities.multimodal:
        raise AgentArchitectureError(
            f"architecture {name!r} requires a multimodal model, but "
            f"{model!r} was detected text-only (source={capabilities.source}). "
            "Set ask_your_docs.multimodal.detection.override: true in your YAML "
            "if the detection is wrong, or select architecture: auto."
        )
    ctx = AgentBuildContext(
        llm=llm, tools=tools, prompt=prompt, capabilities=capabilities, config=config
    )
    return arch_cls().build(ctx)


async def build_agent(
    workspace: str,
    model: str,
    base_url: str | None = None,
    pydocs_config: str | None = None,
    pydocs_cmd: list[str] | None = None,
    catalog: dict[str, list[str]] | None = None,
    *,
    architecture: str | None = None,
    config: AskYourDocsConfig | None = None,
    capabilities: ModelCapabilities | None = None,
):
    """Start pydocs-mcp over the workspace; return ``(agent, llm)``.

    Pass ``catalog`` (from :func:`ask_your_docs.catalog.workspace_catalog`) to
    reuse a scan the caller already did — this keeps the prompt's project list
    identical to whatever the UI shows. When omitted it is scanned here.

    ``pydocs_cmd`` defaults to ``[sys.executable, "-m", "pydocs_mcp"]`` so the
    MCP server subprocess always runs under the SAME interpreter as this app —
    no reliance on ``pydocs-mcp`` being on the child's PATH.

    ``architecture`` overrides ``config.architecture`` (default "auto" —
    routed by the detected capability); ``capabilities`` is injectable so the
    UI can detect once and share the result with its badge.
    """
    command, *prefix = pydocs_cmd or [sys.executable, "-m", "pydocs_mcp"]
    # --config is a root flag: it must come BEFORE the serve subcommand.
    config_args = ["--config", pydocs_config] if pydocs_config else []
    args = [*prefix, *config_args, "serve", "--workspace", workspace]
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

    llm = ChatOpenAI(model=model, base_url=base_url)
    cfg = config or AskYourDocsConfig()
    name = architecture or cfg.architecture
    # Per-architecture system prompt by the directory convention (an
    # architecture without prompts/<name>/system_v1.j2 gets shared/). Note:
    # `auto` composes with its own (shared) system prompt even when it
    # delegates the graph — a per-arch system override applies when that
    # architecture is selected directly.
    system = prompts_for(name).render("system_v1")
    prompt = f"{system}\nIndexed projects and packages:\n{render_catalog(catalog)}"
    caps = capabilities
    if caps is None:
        caps = await detect_capabilities(model, base_url, cfg.multimodal.detection)
    graph = _build_architecture(
        name,
        llm=llm,
        tools=tools,
        prompt=prompt,
        capabilities=caps,
        config=cfg,
        model=model,
    )
    return graph, llm


def _history_line(m) -> str:
    """One REWRITE_PROMPT history line — never a Python-list repr.

    History is text-by-construction (§3.6), but harden anyway: content-block
    messages flatten to their text parts plus "[image]" markers, so a
    multimodal message can never mangle the rewrite prompt.
    """
    content = m.content
    if isinstance(content, str):
        return f"{m.type}: {content}"
    parts = [
        b.get("text", "") if b.get("type") == "text" else "[image]"
        for b in content
        if isinstance(b, dict)
    ]
    return f"{m.type}: {' '.join(p for p in parts if p)}"


async def reformulate(llm: ChatOpenAI, history: list, question: str) -> str:
    """Condense the last question + conversation into a standalone question.

    Text-only by contract: it runs on the woven question BEFORE image blocks
    are attached (§3.6 decision 1), and history carries only text +
    placeholders — ``_history_line`` enforces that shape defensively.
    """
    if not history:
        return question
    lines = "\n".join(_history_line(m) for m in history)
    reply = await llm.ainvoke(rewrite_prompt(history=lines, question=question))
    return str(reply.content).strip() or question


async def ask(
    agent,
    history: list,
    question: str,
    scope: ToolScope | None = None,
    max_history: int = 8,
    *,
    images: tuple = (),
    image_store: dict | None = None,
    transient_note: str = "",
) -> str:
    """One conversation turn under ``scope``; updates ``history`` in place.

    The pin is applied two ways: forced onto every tool call (via the contextvar
    the interceptor reads) and surfaced to the model as a "[pinned scope: ...]"
    note. Only the note is transient — ``history`` keeps the BARE question, so a
    later scope change can't leak a stale pin into reformulation or the answer.

    ``images`` (ImageAttachment tuple) are per-turn ephemera like the scope
    note: the blocks ride only on the CURRENT HumanMessage; history keeps a
    textual "[attached images: ...]" placeholder so later reformulations know
    an image existed without re-paying vision tokens (§3.6 decision 2).
    """
    scope = scope or {}
    token = _active_scope.set(scope)
    store_token = _active_image_store.set(image_store)
    reinspect_token = _reinspect_state.set({"calls": 0, "memo": {}})
    try:
        # transient_note (e.g. the describe-mode cannot-see note) attaches
        # AFTER reformulation, exactly like the scope prefix — prefixing it
        # before the rewrite would let the rewrite LLM strip it, and storing
        # it in history would leak a stale note into later reformulations.
        note = f"{transient_note}\n" if transient_note else ""
        prefixed = scope_prefix(scope) + note + question
        content: str | list = prefixed
        if images:
            content = [
                {"type": "text", "text": prefixed},
                *(att.as_content_block() for att in images),
            ]
        result = await agent.ainvoke({"messages": [*history, HumanMessage(content=content)]})
        answer = result["messages"][-1].content
    finally:
        _active_scope.reset(token)
        _active_image_store.reset(store_token)
        _reinspect_state.reset(reinspect_token)
    placeholder = f" [attached images: {', '.join(att.name for att in images)}]" if images else ""
    history += [HumanMessage(question + placeholder), AIMessage(answer)]
    del history[:-max_history]
    return answer
