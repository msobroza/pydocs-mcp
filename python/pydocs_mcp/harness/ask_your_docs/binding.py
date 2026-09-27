"""The ask-your-docs `HarnessRunner` binding (run-contract design §9 stage 2).

The ONLY module that knows this harness's concrete settings type: generic
callers hold a dotted path to :func:`make_harness_runner`, pass a plain
settings mapping, and get back the port. Heavy toolkit imports stay
function-local behind the ``[harness-ask-your-docs]`` extra, exactly like
``agent.py``.

Delivery map (constraint C2 / design §4): guidance sections route to this
harness's channels — ``SYSTEM_PROMPT``/``REWRITE_PROMPT`` through the
prompt-override seam, the skill artifact's sections through the
``system_prompt_suffix`` skill block at the single assembly site. External
harness task heads are RECOGNIZED but undelivered here (they are other
harnesses' slices of the same candidate); anything else raises
:class:`~pydocs_mcp.harness.core.run_contract.UndeliverableGuidanceError`.
The map's digest folds into the arm cell fingerprint (delivery mode is a
first-order variable).

Trace lifecycle: the ADR 0009 env channel rides the serve connection's env
map over a SEALED inherited environment (``harness.core.serve_child_env``);
the per-trajectory directory and candidate skill persist under ``trace_root``.
Session lifetime (stage 3, first owned item — RESOLVED): the MCP stdio
client's default opens a session per tool call, which would re-spawn the
server and trip the trajectory-id reuse guard. ``_build_and_execute``
therefore holds ONE ``client.session()`` open for the whole run, binds the
tools to it via ``load_mcp_tools`` (interceptors included), and hands them
to ``build_agent(mcp_tools=...)`` — one subprocess, one header, one trace
per trajectory. Real-rollout trace validation against an indexed workspace
remains stage 3's integration step.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import translate_auth_errors

# Redundant aliases = explicit re-exports: tests and callers reach both through ``binding``.
from pydocs_mcp.harness.ask_your_docs.binding_llm_block import (
    clear_config_block_cache as clear_config_block_cache,
)
from pydocs_mcp.harness.ask_your_docs.binding_llm_block import (
    connection_block_for_binding as connection_block_for_binding,
)
from pydocs_mcp.harness.ask_your_docs.binding_sent_settings import (
    sealed_arm_wire,
    write_sent_settings,
)

# The eval harness bridge names ``binding:sent_settings_fingerprint`` (D3 arm identity).
from pydocs_mcp.harness.ask_your_docs.binding_sent_settings import (
    sent_settings_fingerprint as sent_settings_fingerprint,
)
from pydocs_mcp.harness.ask_your_docs.binding_sidecars import stamp_killed_run_sidecars
from pydocs_mcp.harness.ask_your_docs.binding_trajectory import (
    AskTraceMissingError as AskTraceMissingError,
)
from pydocs_mcp.harness.ask_your_docs.binding_trajectory import finished_trajectory
from pydocs_mcp.harness.ask_your_docs.first_turn import seeded_search_for
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    LlmConnection,
    bearer_for_connection,
    resolve_llm_connection,
)
from pydocs_mcp.harness.ask_your_docs.run_trace_handle import (
    ACTIVE_RUN_TRACE_HANDLE,
    AskRunTraceHandle,
)
from pydocs_mcp.harness.ask_your_docs.turn_budget import turn_run_config
from pydocs_mcp.harness.core.prompt_override import PromptOverrides
from pydocs_mcp.harness.core.run_contract import (
    Trajectory,
    TurnBudgetExceededError,
    UndeliverableGuidanceError,
    missing_sample_keys,
)
from pydocs_mcp.harness.core.skill_artifact_loader import (
    BACKBONE_HEADER,
    SKILL_ARTIFACT_HEADERS,
    TASK_HEAD_SECTION_HEADERS,
    TASK_NAMES,
    harness_task_head_section_header,
    parse_skill_artifact,
)
from pydocs_mcp.observability.trace_env import trace_subprocess_env
from pydocs_mcp.retrieval.config.ask_your_docs_models import (
    _DEFAULT_MAX_AGENT_TURNS,
    AskYourDocsConfig,
)

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

_CANDIDATE_SKILL_FILENAME = "candidate_skill.md"

_THIS_HARNESS = "ask_your_docs"
_SKILL_BLOCK_CHANNEL = "system_prompt_suffix.skill_block"

# Section → channel. The two prompt sections ride the existing override
# seam; the skill sections compose into the skill block at the single
# assembly site. External harness task heads are the same candidate's slices
# for OTHER harnesses: recognized, undelivered, never an error.
#
# WHY derived rather than spelled out: the task-head and harness-task-head
# keys ARE ``skill_artifact_loader``'s enumeration, and a hand-written copy is
# a second spelling that a widening or rename event must hand-edit in lockstep
# (the 2026-07-27 ``repo_qa`` widening and the 2026-07-28 taxonomy
# consolidation both had to). The digest below hashes the RESOLVED map, so
# deriving it leaves ``delivery_map_digest()`` byte-identical to the literal.
DELIVERED_SECTION_CHANNELS: Mapping[str, str] = MappingProxyType(
    {
        "SYSTEM_PROMPT": "prompt_override.system_prompt",
        "REWRITE_PROMPT": "prompt_override.rewrite_prompt",
        BACKBONE_HEADER: _SKILL_BLOCK_CHANNEL,
        **dict.fromkeys(TASK_HEAD_SECTION_HEADERS, _SKILL_BLOCK_CHANNEL),
        **dict.fromkeys(
            (
                harness_task_head_section_header(_THIS_HARNESS, task_name)
                for task_name in TASK_NAMES
            ),
            _SKILL_BLOCK_CHANNEL,
        ),
    }
)
RECOGNIZED_UNDELIVERED_SECTIONS: tuple[str, ...] = tuple(
    key for key in SKILL_ARTIFACT_HEADERS if key not in DELIVERED_SECTION_CHANNELS
)


def delivery_map_digest() -> str:
    """SHA-256 of the canonical delivery map — folded into the arm cell
    fingerprint so a delivery change is a recorded configuration change."""
    canonical = {
        "delivered": dict(DELIVERED_SECTION_CHANNELS),
        "recognized_undelivered": list(RECOGNIZED_UNDELIVERED_SECTIONS),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AskSampleContractError(PydocsMCPError, ValueError):
    """A sample row missing the run contract's required keys (rule 6)."""

    def __init__(self, *, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__(
            f"sample is missing required key(s) {list(missing)} — the run "
            "contract requires record_id, task_name, rendered_prompt, gold"
        )


class AskYourDocsRunnerSettings(BaseModel):
    """This harness's private settings — validated HERE, nowhere upstream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace: str
    model: str
    trace_root: str
    base_url: str | None = None
    pydocs_config: str | None = None
    architecture: str | None = None
    tool_names: tuple[str, ...] | None = None
    max_agent_turns: int = _DEFAULT_MAX_AGENT_TURNS
    harness: AskYourDocsConfig = AskYourDocsConfig()


def _partition_guidance(
    guidance_sections: Mapping[str, str],
) -> tuple[PromptOverrides, dict[str, str]]:
    """Split a candidate into this harness's channels, failing loud on the rest.

    Returns the prompt overrides and the skill-document sections (which
    include the recognized external harness task heads — the skill grammar
    requires the full section set, so the document travels whole).
    """
    accepted = tuple(DELIVERED_SECTION_CHANNELS) + RECOGNIZED_UNDELIVERED_SECTIONS
    unknown = tuple(key for key in guidance_sections if key not in accepted)
    if unknown:
        # deliverable names only the truly DELIVERED channels; the external
        # harness task heads are accepted-but-undelivered and must not be
        # advertised as covered (they are other harnesses' slices).
        raise UndeliverableGuidanceError(
            sections=unknown, deliverable=tuple(DELIVERED_SECTION_CHANNELS)
        )
    overrides = PromptOverrides(
        system_prompt=guidance_sections.get("SYSTEM_PROMPT"),
        rewrite_prompt=guidance_sections.get("REWRITE_PROMPT"),
    )
    skill_sections = {
        key: text for key, text in guidance_sections.items() if key in SKILL_ARTIFACT_HEADERS
    }
    return overrides, skill_sections


def _write_candidate_skill(skill_sections: Mapping[str, str], trace_dir: Path) -> Path:
    """Validate + persist the candidate skill document beside its trajectory.

    Validation delegates to the product loader (one validator, both sides —
    the parity rule by identity); persisting beside the trace makes "what
    text ran" auditable from the trajectory directory alone.
    """
    from pydocs_mcp.application.description_source import render_sections

    ordered = {key: skill_sections[key] for key in SKILL_ARTIFACT_HEADERS if key in skill_sections}
    text = render_sections(ordered)
    parse_skill_artifact(text, origin="arm candidate (fix the candidate, not the seed)")
    trace_dir.mkdir(parents=True, exist_ok=True)
    path = trace_dir / _CANDIDATE_SKILL_FILENAME
    path.write_text(text, encoding="utf-8")
    return path


async def run_task(
    sample: Mapping[str, object],
    guidance_sections: Mapping[str, str],
    settings: AskYourDocsRunnerSettings,
) -> Trajectory:
    """One sample through this harness, returning its trajectory.

    Serve-per-run: each call spawns a trace-enabled server, executes the
    sample's rendered prompt, and joins the client observations with the
    server trace. The all-empty-guidance, default-settings path is
    byte-identical to a plain ``build_agent`` + invoke.

    At the turn budget the prebuilt agent RETURNS on its canned apology, and
    that run comes back flagged ``budget_exhausted`` (``binding_trajectory``);
    only a hand-built graph RAISES, and that becomes the contract's
    :class:`TurnBudgetExceededError`, carrying where the run left its trace.
    """
    missing = missing_sample_keys(sample)
    if missing:
        raise AskSampleContractError(missing=missing)
    overrides, skill_sections = _partition_guidance(guidance_sections)

    trajectory_id = uuid.uuid4().hex
    trace_root = Path(settings.trace_root).expanduser()
    trace_dir = trace_root / trajectory_id
    # Before anything can hang: a caller whose timeout kills the run still learns where
    # it was writing (run_trace_handle; the null handle when no caller asked).
    trace_handle = ACTIVE_RUN_TRACE_HANDLE.get()
    trace_handle.record_identity(trajectory_id, trace_dir)

    skill_override = _write_candidate_skill(skill_sections, trace_dir) if skill_sections else None
    task_name = str(sample["task_name"]) if skill_sections else None

    with (
        _recursion_limit_as_turn_budget_error(settings.max_agent_turns, trajectory_id, trace_dir),
        _sidecars_stamped_if_killed(trace_dir, trace_handle),
    ):
        started = time.monotonic()
        answer, messages = await _build_and_execute(
            sample=sample,
            settings=settings,
            overrides=overrides,
            skill_override=skill_override,
            task_name=task_name,
            # ``observability.trace_env`` is the ONE spelling of the three ADR 0009
            # variable names, shared with the composed CLI harness (2026-07-28): a
            # second copy is how a rename disables capture on one path only.
            trace_env=trace_subprocess_env(trace_root, trajectory_id),
        )
    return finished_trajectory(
        trajectory_id=trajectory_id,
        trace_dir=trace_dir,
        answer=answer,
        messages=messages,
        wall_seconds=time.monotonic() - started,
    )


@contextlib.contextmanager
def _recursion_limit_as_turn_budget_error(
    turn_limit: int, trajectory_id: str, trace_dir: Path
) -> Iterator[None]:
    """A hand-built graph's ``GraphRecursionError``, raised as the contract's typed error.

    Shaped like ``translate_auth_errors``. The prebuilt agent never raises at its cap
    (``binding_trajectory`` flags its apology instead), so only a hand-built graph
    lands here — and the error keeps where the run left its trace, so a wrapper can
    still read the calls it made.

    WHY ``except*``: the recursion error is raised inside the held serve session, and
    the MCP ``ClientSession``'s task group re-raises whatever its body raised inside an
    ExceptionGroup (mcp 1.28 / anyio 4.15). A group holding nothing else comes out as
    the BARE typed error — the one exception the eval's timeout wrapper catches.
    """
    # WHY function-local: langgraph lives behind the optional extra.
    from langgraph.errors import GraphRecursionError

    try:
        yield
    except* GraphRecursionError as recursion:
        raise TurnBudgetExceededError(
            turn_limit=turn_limit, trajectory_id=trajectory_id, trace_dir=trace_dir
        ) from recursion


@contextlib.contextmanager
def _sidecars_stamped_if_killed(trace_dir: Path, trace_handle: AskRunTraceHandle) -> Iterator[None]:
    """A run its caller kills (a timeout) still leaves both sidecars for what it had.

    The messages are the ones ``trace_handle`` recorded, so a run nobody waits on (the
    null handle) stamps nothing, exactly as before. The cancellation is re-raised
    untouched: the caller's timeout must still fire.
    """
    try:
        yield
    except asyncio.CancelledError:
        stamp_killed_run_sidecars(trace_dir, trace_handle.messages)
        raise


@contextlib.asynccontextmanager
async def _serve_session_tools(settings: AskYourDocsRunnerSettings, trace_env: Mapping[str, str]):
    """ONE held serve session for a whole run, yielding its bound tools.

    The stdio client's default opens a session per tool call — that would
    re-spawn the trace-enabled server and trip the trajectory-id reuse
    guard, so this context is the run's single subprocess and single trace.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.tools import load_mcp_tools

    from pydocs_mcp.harness.ask_your_docs.agent import _intercept, serve_connection

    # WHY sealed: settings-in, trajectory-out. The child inherits the embedder key, TMPDIR,
    # proxies and CA bundles, never a shell's PYDOCS_* / OPENAI_BASE_URL (serve_child_env).
    connection = serve_connection(
        settings.workspace, settings.pydocs_config, subprocess_env=trace_env, seal_config_tier=True
    )
    client = MultiServerMCPClient({"pydocs": connection})
    async with client.session("pydocs") as session:
        yield await load_mcp_tools(session, tool_interceptors=[_intercept])


def _llm_connection_for_run(settings: AskYourDocsRunnerSettings) -> LlmConnection:
    """The endpoint, model, auth mode and vision rule this run talks to (§4.11).

    The same fold ``agent._launch_connection`` performs for an unresolved
    build; that identity is what keeps the no-block control arm byte-identical.

    WHY an empty environment tier: it blocks the LAUNCHER variables
    (``OPENAI_BASE_URL`` / ``LLM_MODEL``) — the binding is settings-in,
    trajectory-out, so a shell must not re-point an arm's endpoint. It does NOT
    seal the block itself: ``PYDOCS_ASK_YOUR_DOCS__LLM__*`` reaches it through
    ``AppConfig.load`` by design (spec §4.11), and
    :func:`_warn_if_env_overlays_the_block` is how a campaign log shows that.
    """
    return resolve_llm_connection(
        connection_block_for_binding(settings),
        {},
        ConnectionOverride(settings.base_url, settings.model),
        ConnectionOverride(),
        config_path=settings.pydocs_config,
    )


async def _build_and_execute(
    *,
    sample: Mapping[str, object],
    settings: AskYourDocsRunnerSettings,
    overrides: PromptOverrides,
    skill_override: Path | None,
    task_name: str | None,
    trace_env: Mapping[str, str],
) -> tuple[str, list]:
    """Hold ONE serve session for the run; build, execute, return.

    Monkeypatch seam for tests; the session-per-tool-call default would
    re-spawn the trace-enabled server and trip the id-reuse guard, so the
    session opened here is the run's single subprocess and single trace.
    """
    # WHY function-local: langgraph/langchain live behind the optional extra.
    from langchain_core.messages import HumanMessage

    from pydocs_mcp.harness.ask_your_docs.agent import build_agent

    # WHY before the session: an invalid arm block raises HERE, so a bad config
    # never spawns a trace-enabled subprocess. Inlining it into the
    # ``connection=`` kwarg below would move validation behind the spawn. The same
    # holds for the model settings (model-params v2 §6): a file-sourced block (P4)
    # and a value the static tables would hide both raise before any spend.
    llm_connection = _llm_connection_for_run(settings)
    wire_profile_used, wire = sealed_arm_wire(llm_connection)
    write_sent_settings(trace_env, wire_profile_used, wire)
    async with _serve_session_tools(settings, trace_env) as tools:
        graph, _ = await build_agent(
            settings.workspace,
            settings.model,
            base_url=settings.base_url,
            pydocs_config=settings.pydocs_config,
            architecture=settings.architecture,
            config=settings.harness,
            prompts=overrides if (overrides.system_prompt or overrides.rewrite_prompt) else None,
            tool_names=settings.tool_names,
            skill_override=skill_override,
            task_name=task_name,
            mcp_tools=tools,
            connection=llm_connection,
            wire=wire,
        )
        # WHY the bearer here: the registry hands back the object the agent's own model
        # holds, and an untranslated 401/403 carries the SDK's response body — which a
        # gateway fills with the credential it just rejected (E4/H4). The page sealed
        # this boundary when the dialog shipped; a campaign log had no such seal.
        with translate_auth_errors(bearer_for_connection(llm_connection)):
            prompt = str(sample["rendered_prompt"])
            seed = seeded_search_for(settings.harness.seed_search_with_question, tools)
            # No page scope in a campaign: this path invokes the graph directly
            # and never enters ask(), so no question scope is bound and the
            # interceptor is a strict passthrough — the arm's corpus is exactly
            # the bundle the serve child was started over. The seed asks the
            # row's bare question (#384): the scaffold is for the model, not a query.
            query = str(sample.get("question", prompt))
            seeded = await seed.messages_for(query) if seed is not None else []
            state = await _final_state_recording_steps(
                graph,
                {"messages": [HumanMessage(content=prompt), *seeded]},
                turn_run_config(settings.max_agent_turns),
            )
    messages = state["messages"]
    return str(messages[-1].content), list(messages)


async def _final_state_recording_steps(
    graph: Any, payload: dict[str, Any], config: dict[str, int]
) -> dict[str, Any]:
    """The graph's final state, streamed so the active trace handle holds every step.

    ``stream_mode="values"`` yields the input state first, then the whole state after
    each step, and the last one is exactly what ``ainvoke`` returns (langgraph 1.2.8);
    recording each is what lets a run a caller kills still leave the messages it had
    (``run_trace_handle``). WHY a graph that streams nothing raises: falling back to the
    payload would read the question back as the run's answer.
    """
    handle = ACTIVE_RUN_TRACE_HANDLE.get()
    state: dict[str, Any] | None = None
    async for state in graph.astream(payload, config, stream_mode="values"):
        handle.record_messages(state["messages"])
    if state is None:
        raise RuntimeError(
            f"agent graph {type(graph).__name__} streamed no state, expected at least its "
            "input values (stream_mode='values')"
        )
    return state


class _AskHarnessRunner:
    """The port object: conforms to ``HarnessRunner`` structurally AND
    nominally (``isinstance`` via the runtime-checkable Protocol)."""

    def __init__(self, settings: AskYourDocsRunnerSettings) -> None:
        self._settings = settings

    async def run(
        self, sample: Mapping[str, object], guidance_sections: Mapping[str, str]
    ) -> Trajectory:
        return await run_task(sample, guidance_sections, self._settings)


def make_harness_runner(settings: Mapping[str, object]) -> _AskHarnessRunner:
    """Build this harness's ``HarnessRunner`` from a plain settings mapping.

    The generic composition root resolves this function by dotted path and
    never sees the concrete settings type — validation (``extra="forbid"``,
    so typos fail loud) happens here, before any spend.
    """
    return _AskHarnessRunner(AskYourDocsRunnerSettings.model_validate(dict(settings)))
