"""Ask-your-docs agent — a LangGraph ReAct agent over pydocs-mcp.

agent, llm = await build_agent("~/pydocs-index", model="gpt-4o-mini")
history: list = []
pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", ""),))
answer = await ask(agent, history, "how do I open a database pool?", scope=pin)
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.ask_your_docs.activity_stream import (
    ActivitySink,
    invoke_turn,
    stream_turn,
)
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
from pydocs_mcp.harness.ask_your_docs.catalog import (
    WorkspaceBranchListing,
    workspace_branch_listing,
    workspace_catalog,
)
from pydocs_mcp.harness.ask_your_docs.chat_wire import NO_WIRE_PARAMS, WireParams, connection_wire
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    LlmConnection,
    bearer_for_connection,
    build_chat_model,
    resolve_llm_connection,
    resolve_vision_capabilities,
)
from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities

# The ONE prompt-assembly site lives in prompt_assembly.py (agent.py's line
# budget, AC-29); its names keep this module as their import path.
from pydocs_mcp.harness.ask_your_docs.prompt_assembly import (
    AskPrompts,
    _assemble_prompt,
    _resolved_skill_block,
)

# ALL prompt text is centralized under ask_your_docs/prompts/ (versioned .j2
# templates, one directory per architecture, falling back to the shared pool
# in harness/core/prompts/). SYSTEM_PROMPT is re-exported here for its
# existing import path.
from pydocs_mcp.harness.ask_your_docs.prompts import (
    SYSTEM_PROMPT,  # noqa: F401 — re-export for the existing import path
)
from pydocs_mcp.harness.ask_your_docs.question_scope import QuestionScope, scope_prefix
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    BuiltAgent,
    inspect_scope_capabilities,
)
from pydocs_mcp.harness.ask_your_docs.scope_interceptor import (
    ACTIVE_QUESTION_SCOPE,
    ACTIVE_SCOPE_OBSERVATIONS,
    ACTIVE_SCOPE_RUNTIME,
    ScopeObservations,
    ScopeRuntime,
    intercept_question_scope,
)
from pydocs_mcp.harness.ask_your_docs.serve_spawn import serve_connection
from pydocs_mcp.harness.ask_your_docs.session_start_injection import (
    build_session_start_context_for_agent_prompt,
)
from pydocs_mcp.harness.core.serve_child_env import NO_ENV_OVERLAY
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig, VisionRule

logger = logging.getLogger(__name__)

# The CURRENT question's session image store (name → ImageAttachment) for the
# reinspect_images tool. Same isolation rationale as the question-scope
# contextvars (scope_interceptor.ACTIVE_QUESTION_SCOPE): the compiled agent
# graph is cached across sessions, so per-session state must ride a contextvar
# set inside ask(), never be baked into the tools.
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
    """The question-scope interceptor (scope_interceptor.intercept_question_scope);
    kept under this name because the eval binding imports it.

    ``build_agent(scope_pin=False)`` omits it — the eval harness's searched dimension.
    """
    return await intercept_question_scope(request, handler)


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


async def build_agent_with_scope_capabilities(
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
    subprocess_env: Mapping[str, str] = NO_ENV_OVERLAY,
    mcp_tools: list | None = None,
    connection: LlmConnection | None = None,
    bearer: BearerSource | None = None,
    vision_capabilities: ModelCapabilities | None = None,
    wire: WireParams | None = None,
    branches: WorkspaceBranchListing | None = None,
) -> BuiltAgent:
    """Start pydocs-mcp over the workspace; return a :class:`BuiltAgent`.

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
    pass — so product behavior is byte-identical by default. ``wire`` is the main
    model's resolved settings (model-params v2 §5 rule 7; None = the connection's own).

    The run-contract keywords (§9 stage 2, HARNESS-PRIVATE — the cross-repo
    seam is the run contract, never this signature) — ``tool_names``,
    ``skill_override`` / ``task_name``, ``scope_pin``, ``subprocess_env``,
    ``mcp_tools`` — are each documented at the helper that consumes them
    (:func:`_select_bound_tools`, :func:`_resolved_skill_block`,
    :func:`_intercept`, :func:`serve_connection`). All defaults together
    reproduce the pre-stage-2 build byte-for-byte except the serve child's
    environment, which since 0.6.1 always inherits the parent's
    (``harness.core.serve_child_env``). That is identical for every arm, so
    arms still differ only by these keywords.

    ``branches`` (the workspace's branch listing) feeds the catalog's branch
    segment when the server advertises ``branch``; ``None`` scans the
    workspace in that case and is ignored otherwise.
    """
    cfg = config or AskYourDocsConfig()
    connection, bearer = _connection_and_bearer(
        cfg, model, base_url, pydocs_config, connection, bearer
    )
    if mcp_tools is not None:
        # The caller owns the session/spawn lifecycle (the binding holds ONE
        # session for a whole traced run — the per-tool-call session default
        # would re-spawn the server and trip the trajectory-id reuse guard;
        # the chat page holds ONE per browser session — page_agent.py).
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
    scope_caps = inspect_scope_capabilities(tools)
    if branches is None and scope_caps.branch_selector:
        branches = await asyncio.to_thread(workspace_branch_listing, workspace)
    name = architecture or cfg.architecture
    pack = await build_session_start_context_for_agent_prompt(workspace, pydocs_config)
    prompt = _assemble_prompt(
        name,
        catalog,
        prompts,
        pack,
        _resolved_skill_block(skill_override, task_name),
        scope_capabilities=scope_caps,
        branches=branches,
    )
    # Model-params v2 §5 rule 7: only the main model carries the settings (``wire`` = the
    # dialog's resolution; None = the connection's params over the static tables).
    main_wire = connection_wire(connection) if wire is None else wire
    llm = build_chat_model(
        connection, bearer, capture_reasoning=cfg.ui.reasoning.capture, wire=main_wire
    )
    caps, vision_caps = await _capabilities_for(
        connection, bearer, cfg, capabilities, vision_capabilities
    )
    vision_llm = INHERIT_FROM_MAIN  # the context resolves it to llm — one inherit policy, one place
    if connection.vision_rule is VisionRule.SEPARATE_MODEL:  # same endpoint, same bearer (R6)
        vision_llm = build_chat_model(
            connection, bearer, model=connection.vision_model, wire=NO_WIRE_PARAMS
        )
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
    return BuiltAgent(graph=graph, llm=llm, scope_capabilities=scope_caps)


async def build_agent(*args: Any, **kwargs: Any):
    """Start pydocs-mcp over the workspace; return ``(agent, llm)``.

    The pre-scope shape, kept byte for byte for the eval binding, the CLI and
    the prompt-seam tests (a 2-tuple, never a third element). Everything else
    is :func:`build_agent_with_scope_capabilities`, whose keyword surface this
    wrapper forwards unchanged.
    """
    built = await build_agent_with_scope_capabilities(*args, **kwargs)
    return built.graph, built.llm


# inspect.signature(build_agent) follows __wrapped__, so the keyword-only
# prompts= seam pin (test_prompt_seam.py) still reads the full signature.
build_agent.__wrapped__ = build_agent_with_scope_capabilities  # type: ignore[attr-defined]


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


def _bind_question_context(
    scope: QuestionScope | None,
    scope_runtime: ScopeRuntime | None,
    observations: ScopeObservations | None,
    image_store: dict | None,
) -> list[tuple[contextvars.ContextVar, contextvars.Token]]:
    """Set the per-question contextvars inside ask()'s coroutine; returns the
    tokens to reset. Concurrent questions (two browser tabs on one cached
    agent) each see their own values — never shared mutable state."""
    pairs: list[tuple[contextvars.ContextVar, contextvars.Token]] = []
    for var, value in (
        (ACTIVE_QUESTION_SCOPE, scope),
        (ACTIVE_SCOPE_RUNTIME, scope_runtime),
        (
            ACTIVE_SCOPE_OBSERVATIONS,
            observations if observations is not None else ScopeObservations(),
        ),
        (_active_image_store, image_store),
        (_reinspect_state, {"calls": 0, "memo": {}}),
    ):
        pairs.append((var, var.set(value)))
    return pairs


async def ask(
    agent,
    history: list,
    question: str,
    scope: QuestionScope | None = None,
    max_history: int = 8,
    *,
    images: tuple = (),
    image_store: dict | None = None,
    transient_note: str = "",
    observations: ScopeObservations | None = None,
    scope_runtime: ScopeRuntime | None = None,
    on_event: ActivitySink | None = None,
    live: bool = True,
    on_final: Callable[[Any], None] | None = None,
) -> str:
    """One conversation turn under ``scope``; updates ``history`` in place.

    The scope is applied two ways: on every tool call (the interceptor reads
    the contextvar) and, for a PIN, as a transient "[pinned scope: ...]" note.
    Only the note is transient — ``history`` keeps the BARE question, so a
    later scope change can't leak a stale pin into reformulation or the answer.
    ``None`` (the CLI / eval shape) makes the interceptor a strict passthrough.

    ``observations`` (a container the page owns) receives one record per tool
    call — mutated in place, the ``_reinspect_state`` pattern, because the
    interceptor runs in a copied context. ``scope_runtime`` carries the branch
    listing, the capability record and the fan-out cap.

    ``on_final`` receives the turn's last message (the chat page reads its
    ``finish_reason`` to spot a reply starved while thinking, model-params v2 §5 rule 6).

    ``on_event`` (the chat page's activity panel) receives the turn's activity events —
    streamed as they happen, or replayed after one ``ainvoke`` when ``live`` is False. None
    (the default, and every eval / CLI caller) keeps the plain ``ainvoke`` path.

    ``images`` (ImageAttachment tuple) are per-turn ephemera like the scope
    note: the blocks ride only on the CURRENT HumanMessage; history keeps a
    textual "[attached images: ...]" placeholder so later reformulations know
    an image existed without re-paying vision tokens (§3.6 decision 2).
    """
    bound = _bind_question_context(scope, scope_runtime, observations, image_store)
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
        payload = {"messages": [*history, HumanMessage(content=content)]}
        final = (await _turn_messages(agent, payload, on_event, live))[-1]
        if on_final is not None:
            on_final(final)
        answer = final.content
    finally:
        for var, token in reversed(bound):
            var.reset(token)
    placeholder = f" [attached images: {', '.join(att.name for att in images)}]" if images else ""
    history += [HumanMessage(question + placeholder), AIMessage(answer)]
    del history[:-max_history]
    return answer


async def _turn_messages(agent, payload: dict, on_event: ActivitySink | None, live: bool) -> list:
    """The finished turn's messages; None keeps today's ``ainvoke`` call exactly."""
    if on_event is None:
        return (await agent.ainvoke(payload))["messages"]
    run_turn = stream_turn if live else invoke_turn
    return await run_turn(agent, payload, on_event)
