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
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.ask_your_docs.architectures import (
    INHERIT_FROM_MAIN,
    AgentArchitectureError,
    AgentBuildContext,
    agent_registry,
    require_image_capability,
)

# weave_attachments moved to attachments.py (spec 2026-07-11-multimodal-image-
# agent §3.1); re-exported so app.py and existing tests keep this import path.
from pydocs_mcp.harness.ask_your_docs.attachments import weave_attachments  # noqa: F401
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NO_BEARER, BearerSource
from pydocs_mcp.harness.ask_your_docs.catalog import render_catalog, workspace_catalog
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    LlmConnection,
    bearer_for_connection,
    build_chat_model,
    resolve_llm_connection,
    resolve_vision_capabilities,
)
from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities

# ALL prompt text is centralized under ask_your_docs/prompts/ (versioned .j2
# templates, one directory per architecture, falling back to the shared pool
# in harness/core/prompts/). SYSTEM_PROMPT is re-exported here for its
# existing import path.
from pydocs_mcp.harness.ask_your_docs.prompts import (
    SYSTEM_PROMPT,  # noqa: F401 — re-export for the existing import path
    prompts_for,
)
from pydocs_mcp.harness.ask_your_docs.session_start_injection import (
    build_session_start_context_for_agent_prompt,
)
from pydocs_mcp.harness.core.prompt_override import PromptOverrides, assemble_system_prompt
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig, VisionRule

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


class ToolBindingError(PydocsMCPError, ValueError):
    """A requested bound-tool set that the server does not advertise.

    Silently binding zero (or fewer) tools would produce a fake experiment
    arm, so an unknown name — or an empty request — fails loudly naming the
    offending values and the advertised set (run-contract design §6/§9).
    """

    def __init__(self, *, unknown: tuple[str, ...], advertised: tuple[str, ...]) -> None:
        self.unknown = unknown
        self.advertised = advertised
        super().__init__(
            f"unknown bound tool name(s) {list(unknown)} — the server advertises "
            f"{sorted(advertised)}; a tool surface may only narrow within it"
        )


def _select_bound_tools(tools: list, tool_names: tuple[str, ...]) -> list:
    """Narrow the bound tool set WITHIN what the server advertises (fail-loud).

    The bound set is DATA, never an architecture class: the §6 experiment
    arms differ only in this tuple. Order follows ``tool_names`` so the arm's
    tool ordering is deterministic and lockfile-describable.
    """
    if not tool_names:
        raise ToolBindingError(unknown=("<empty>",), advertised=tuple(t.name for t in tools))
    by_name = {tool.name: tool for tool in tools}
    unknown = tuple(name for name in tool_names if name not in by_name)
    if unknown:
        raise ToolBindingError(unknown=unknown, advertised=tuple(by_name))
    return [by_name[name] for name in tool_names]


async def _intercept(request: MCPToolCallRequest, handler):
    """Force the active question's pin onto every MCP tool call.

    Reads the pin from a contextvar rather than a shared dict, so the LLM
    cannot forget or override it and concurrent questions stay isolated.
    ``build_agent(scope_pin=False)`` omits it — the eval harness's searched dimension.
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
    vision_llm=INHERIT_FROM_MAIN,
    vision_capabilities: ModelCapabilities = INHERIT_FROM_MAIN,
    bearer: BearerSource = NO_BEARER,
    vision_model: str | None = None,
):
    """Validate + build the named architecture (spec §3.4.4; design §4.8).

    Split out of :func:`build_agent` so tests exercise validation and graph
    construction without an MCP server subprocess. The three connection
    keywords default to the SAME Null Objects the context itself defaults to
    (``vision_llm`` / ``vision_capabilities`` mirror the main model, ``bearer``
    sends no Authorization header), so an omitted keyword never plants a None.
    ``vision_model`` is the configured ``ask_your_docs.llm.vision.model`` NAME —
    the E13 refusal quotes it, which no model object can supply.
    """
    arch_cls = agent_registry.get(name)
    if arch_cls is None:
        raise ValueError(f"unknown architecture {name!r}; known: {agent_registry.names()}")
    ctx = AgentBuildContext(
        llm=llm,
        tools=tools,
        prompt=prompt,
        capabilities=capabilities,
        config=config,
        vision_llm=vision_llm,
        vision_capabilities=vision_capabilities,
        bearer=bearer,
    )
    require_image_capability(arch_cls, ctx, name, model, vision_model=vision_model)
    return arch_cls().build(ctx)


# Back-compat name: the override type is the harness-generic core seam
# (consumed by the eval binding and the UI through this import site).
AskPrompts = PromptOverrides


def _assemble_prompt(
    name: str,
    catalog: dict[str, list[str]],
    prompts: AskPrompts | None,
    session_start_context: str | None = None,
    skill_block: str | None = None,
) -> str:
    """The ONE prompt-assembly site: candidate-or-shipped system + catalog.

    The fallback is the per-architecture render (``prompts_for(name)``), never
    the ``SYSTEM_PROMPT`` constant — a ``prompts/<name>/system_v1.j2``
    override must apply whenever that architecture is selected (an architecture
    without that template gets ``shared/``; ``auto`` composes with its own
    shared prompt even when it delegates the graph). A second assembly site is
    the one forbidden shape (single source of truth).

    ``session_start_context`` (ADR 0008) appends the harness-injected
    session-start pack after the catalog; ``skill_block`` (run-contract
    design §9 stage 2) appends the skill-artifact guidance after it.
    ``None`` for either — the shipped defaults — keeps the assembled prompt
    byte-identical to the pre-existing shape.
    """
    resolved_system = (
        prompts.system_prompt
        if prompts and prompts.system_prompt
        else prompts_for(name).render("system_v1")
    )
    return assemble_system_prompt(
        resolved_system, render_catalog(catalog), session_start_context, skill_block
    )


def _resolved_skill_block(skill_override: Path | None, task_name: str | None) -> str | None:
    """The skill guidance for this build, or ``None`` — the byte-identity default.

    The backbone folds whenever skill guidance is requested at all
    (``skill_override`` or ``task_name`` given); the harness-invariant task
    head and this harness's harness task head fold only when ``task_name``
    names the arm's task. An unknown task name fails loudly in
    ``task_head_section_header`` (the enumerated v1 set); an invalid override
    document fails loudly in the loader — never a silent fallback.
    """
    if skill_override is None and task_name is None:
        return None
    # WHY function-local: the loader pulls in the description grammar; the
    # default build path (no skill) must not pay that import.
    from pydocs_mcp.harness.core.skill_artifact_loader import load_skill_artifact

    artifact = load_skill_artifact(skill_override)
    if task_name is None:
        return artifact.backbone
    task_head = artifact.task_head(task_name)
    harness_task_head = artifact.harness_task_head("ask_your_docs", task_name)
    return f"{artifact.backbone}\n{task_head}\n{harness_task_head}"


def serve_connection(
    workspace: str,
    pydocs_config: str | None = None,
    pydocs_cmd: list[str] | None = None,
    subprocess_env: dict[str, str] | None = None,
) -> dict:
    """The stdio connection dict for one pydocs-mcp serve subprocess.

    The single source of the serve argv shape — ``build_agent`` and the
    harness binding (which holds a session open for a whole run) both build
    their connection here, so the argv and env rules cannot drift.
    """
    # WHY this default: the serve child then runs under the SAME interpreter as
    # this app — no reliance on ``pydocs-mcp`` being on the child's PATH.
    command, *prefix = pydocs_cmd or [sys.executable, "-m", "pydocs_mcp"]
    # --config is a root flag: it must come BEFORE the serve subcommand.
    config_args = ["--config", pydocs_config] if pydocs_config else []
    args = [*prefix, *config_args, "serve", "--workspace", workspace]
    connection: dict = {"transport": "stdio", "command": command, "args": args}
    # WHY an explicit env map: the MCP stdio spawn starts children from a
    # MINIMAL default environment (not the parent's), so anything the serve
    # subprocess must see — the ADR 0009 PYDOCS_TRACE__* channel above all —
    # must ride the connection's env key, never a parent os.environ mutation
    # (which the child would not inherit AND which races concurrent runs).
    if subprocess_env is not None:
        connection["env"] = dict(subprocess_env)
    return connection


async def build_agent(
    workspace: str,
    model: str | None,
    base_url: str | None = None,
    pydocs_config: str | None = None,
    pydocs_cmd: list[str] | None = None,
    catalog: dict[str, list[str]] | None = None,
    *,
    architecture: str | None = None,
    config: AskYourDocsConfig | None = None,
    capabilities: ModelCapabilities | None = None,
    prompts: AskPrompts | None = None,
    tool_names: tuple[str, ...] | None = None,
    skill_override: Path | None = None,
    task_name: str | None = None,
    scope_pin: bool = True,
    subprocess_env: dict[str, str] | None = None,
    mcp_tools: list | None = None,
    connection: LlmConnection | None = None,
    bearer: BearerSource | None = None,
    vision_capabilities: ModelCapabilities | None = None,
):
    """Start pydocs-mcp over the workspace; return ``(agent, llm)``.

    ``catalog`` (from :func:`ask_your_docs.catalog.workspace_catalog`) reuses a
    scan the caller already did, keeping the prompt's project list identical to
    the UI's; omitted, it is scanned here. ``pydocs_cmd`` defaults to this
    interpreter (:func:`serve_connection`). ``connection`` (LLM-connection
    design §4.5) is the resolved endpoint / model / auth / vision record and
    ``bearer`` the credential it presents (:func:`_connection_and_bearer`);
    ``capabilities`` / ``vision_capabilities`` inject verdicts the app already
    detected (:func:`_capabilities_for`); ``architecture`` overrides
    ``config.architecture`` (default "auto"); ``prompts`` is the
    evaluation-harness seam (:class:`AskPrompts`), which the app and CLI never
    pass — so product behavior is byte-identical by default.

    The run-contract keywords (§9 stage 2, HARNESS-PRIVATE — the cross-repo
    seam is the run contract, never this signature) — ``tool_names``,
    ``skill_override`` / ``task_name``, ``scope_pin``, ``subprocess_env``,
    ``mcp_tools`` — are each documented at the helper that consumes them
    (:func:`_select_bound_tools`, :func:`_resolved_skill_block`,
    :func:`_intercept`, :func:`serve_connection`). All defaults together
    reproduce the pre-stage-2 build byte-for-byte — the experiment's control
    arm is provable.
    """
    cfg = config or AskYourDocsConfig()
    connection, bearer = _connection_and_bearer(
        cfg, model, base_url, pydocs_config, connection, bearer
    )
    if mcp_tools is not None:
        # The caller owns the session/spawn lifecycle (the binding holds ONE
        # session for a whole traced run — the per-tool-call session default
        # would re-spawn the server and trip the trajectory-id reuse guard).
        # The caller also owns interceptor wiring via load_mcp_tools.
        tools = mcp_tools
    else:
        serve = serve_connection(workspace, pydocs_config, pydocs_cmd, subprocess_env)
        client = MultiServerMCPClient(
            {"pydocs": serve}, tool_interceptors=[_intercept] if scope_pin else []
        )
        tools = await client.get_tools()
    if tool_names is not None:
        tools = _select_bound_tools(tools, tool_names)
    # Fold the full project/package catalog into the prompt so the model can
    # pick the right project= / package= filters itself. Built from the bundle
    # files directly: in workspace mode, get_overview(project="") describes only
    # the default project, so it can't produce this listing.
    if catalog is None:
        catalog = await asyncio.to_thread(workspace_catalog, workspace)
    name = architecture or cfg.architecture
    pack = await build_session_start_context_for_agent_prompt(workspace, pydocs_config)
    prompt = _assemble_prompt(
        name, catalog, prompts, pack, _resolved_skill_block(skill_override, task_name)
    )
    llm = build_chat_model(connection, bearer)
    caps, vision_caps = await _capabilities_for(
        connection, bearer, cfg, capabilities, vision_capabilities
    )
    vision_llm = INHERIT_FROM_MAIN  # the context resolves it to llm — one inherit policy, one place
    if connection.vision_rule is VisionRule.SEPARATE_MODEL:  # same endpoint, same bearer (R6)
        vision_llm = build_chat_model(connection, bearer, model=connection.vision_model)
    graph = _build_architecture(
        name,
        llm=llm,
        tools=tools,
        prompt=prompt,
        capabilities=caps,
        config=cfg,
        model=connection.model,
        vision_llm=vision_llm,
        vision_capabilities=vision_caps,
        bearer=bearer,
        vision_model=connection.vision_model,
    )
    return graph, llm


def _connection_and_bearer(
    cfg: AskYourDocsConfig,
    model: str | None,
    base_url: str | None,
    pydocs_config: str | None,
    connection: LlmConnection | None,
    bearer: BearerSource | None,
) -> tuple[LlmConnection, BearerSource]:
    """The endpoint this build talks to and the credential it presents (design §4.3–§4.5).

    The environment tier of the fold is EMPTY on purpose: the app and the CLI fold
    their own environment first, so a stray ``LLM_MODEL`` can never re-point a
    programmatic build. An omitted bearer comes from the per-identity registry, so
    a whole campaign shares one token.
    """
    resolved = connection or _launch_connection(cfg, model, base_url, pydocs_config)
    if resolved.model is None:  # design E19 — before any tool or LLM construction
        raise AgentArchitectureError(
            "no model chosen; set ask_your_docs.llm.model, LLM_MODEL, --model or pick one in "
            "the Connection dialog"
        )
    return resolved, bearer if bearer is not None else bearer_for_connection(resolved)


def _launch_connection(
    cfg: AskYourDocsConfig, model: str | None, base_url: str | None, pydocs_config: str | None
) -> LlmConnection:
    """The precedence fold with only the launch tier set (design §4.3)."""
    launch, dialog = ConnectionOverride(base_url, model), ConnectionOverride()
    return resolve_llm_connection(cfg.llm, {}, launch, dialog, config_path=pydocs_config)


async def _capabilities_for(
    connection: LlmConnection,
    bearer: BearerSource,
    cfg: AskYourDocsConfig,
    capabilities: ModelCapabilities | None,
    vision_capabilities: ModelCapabilities | None,
) -> tuple[ModelCapabilities, ModelCapabilities]:
    """Injected verdicts win (tests, the app's cache); otherwise the single §4.7 call site.

    ``is not None``, never ``or``: "the caller injected a verdict" is an absence test, and a
    verdict is a record, not a truth value — ``or`` only worked because every record is truthy.
    """
    if capabilities is not None:
        return capabilities, capabilities if vision_capabilities is None else vision_capabilities
    main, vision = await resolve_vision_capabilities(connection, bearer, cfg.multimodal.detection)
    return main, vision if vision_capabilities is None else vision_capabilities


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
