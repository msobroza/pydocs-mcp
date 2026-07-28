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

Trace lifecycle: the ADR 0009 env channel rides the serve connection's
explicit env map (children start from a MINIMAL default environment, so
parent-environ mutation would never reach them — and would race concurrent
runs). The per-trajectory directory persists under ``settings.trace_root``
and the candidate skill document is written next to it for provenance.
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

import contextlib
import hashlib
import json
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

from pydocs_mcp.exceptions import PydocsMCPError
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
from pydocs_mcp.observability.trace_reader import read_tool_call_records, tool_args_digest
from pydocs_mcp.observability.trace_writer import SERVER_EVENTS_FILENAME
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

_CANDIDATE_SKILL_FILENAME = "candidate_skill.md"

# WHY 2: a LangGraph "super-step" alternates model turn / tool execution, so
# one agent turn costs two graph steps — the recursion limit mirrors the
# eval runner's established mapping.
_SUPER_STEPS_PER_TURN = 2

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
    payload = json.dumps(
        {
            "delivered": dict(DELIVERED_SECTION_CHANNELS),
            "recognized_undelivered": list(RECOGNIZED_UNDELIVERED_SECTIONS),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AskSampleContractError(PydocsMCPError, ValueError):
    """A sample row missing the run contract's required keys (rule 6)."""

    def __init__(self, *, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__(
            f"sample is missing required key(s) {list(missing)} — the run "
            "contract requires record_id, task_name, rendered_prompt, gold"
        )


class AskTraceMissingError(PydocsMCPError, RuntimeError):
    """A trace-enabled run came back traceless (contract rule 4).

    Phase 2's silently-disabled-capture incident is the motivating scar: a
    traceless run must never be scored.
    """

    def __init__(self, *, trace_dir: Path) -> None:
        self.trace_dir = trace_dir
        super().__init__(
            f"no server trace at {trace_dir} after a trace-enabled run — "
            "refusing to return a scoreable trajectory (ADR 0009 correlation "
            "contract; check the serve subprocess env wiring)"
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
    max_agent_turns: int = 12
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


def _trace_subprocess_env(trace_root: Path, trajectory_id: str) -> dict[str, str]:
    """The ADR 0009 correlation identity, as the serve connection's env map.

    Delegates to ``observability.trace_env`` — the one spelling of the three
    variable names, shared with the composed CLI harness (2026-07-28): a second
    copy is how a rename disables capture on one path and not the other.
    """
    return trace_subprocess_env(trace_root, trajectory_id)


def _client_only_records(messages: list, server_call_counts: dict[str, int]) -> tuple:
    """CLIENT-observed calls: message tool calls the server never saw.

    Matches by name multiset against the trace — an agent-local tool
    (``reinspect_images``) never reaches the server, so its calls surface
    here with ``observed_by=CLIENT``.
    """
    from pydocs_mcp.harness.core.run_contract import ToolCallObservation, ToolCallRecord

    remaining = dict(server_call_counts)
    records = []
    for message in messages:
        for call in getattr(message, "tool_calls", ()) or ():
            name = call.get("name", "")
            if remaining.get(name, 0) > 0:
                remaining[name] -= 1
                continue
            records.append(
                ToolCallRecord(
                    tool_name=name,
                    args_digest=tool_args_digest(call.get("args", {})),
                    observed_by=ToolCallObservation.CLIENT,
                )
            )
    return tuple(records)


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
    """
    missing = missing_sample_keys(sample)
    if missing:
        raise AskSampleContractError(missing=missing)
    overrides, skill_sections = _partition_guidance(guidance_sections)

    trajectory_id = uuid.uuid4().hex
    trace_root = Path(settings.trace_root).expanduser()
    trace_dir = trace_root / trajectory_id

    skill_override: Path | None = None
    task_name: str | None = None
    if skill_sections:
        skill_override = _write_candidate_skill(skill_sections, trace_dir)
        task_name = str(sample["task_name"])

    started = time.monotonic()
    answer, messages = await _build_and_execute(
        sample=sample,
        settings=settings,
        overrides=overrides,
        skill_override=skill_override,
        task_name=task_name,
        trace_env=_trace_subprocess_env(trace_root, trajectory_id),
    )
    wall_seconds = time.monotonic() - started

    if not (trace_dir / SERVER_EVENTS_FILENAME).exists():
        raise AskTraceMissingError(trace_dir=trace_dir)
    server_records = read_tool_call_records(trace_dir)
    counts: dict[str, int] = {}
    for record in server_records:
        counts[record.tool_name] = counts.get(record.tool_name, 0) + 1

    from langchain_core.messages import AIMessage

    return Trajectory(
        trajectory_id=trajectory_id,
        trace_dir=trace_dir,
        answer=answer,
        tool_calls=(*server_records, *_client_only_records(messages, counts)),
        turns=sum(isinstance(message, AIMessage) for message in messages),
        # WHY 0.0: this toolkit path does not observe spend; documented in
        # the contract (0.0 == unobserved, deliberately not None).
        cost_usd=0.0,
        wall_seconds=wall_seconds,
    )


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

    connection = serve_connection(
        settings.workspace, settings.pydocs_config, subprocess_env=dict(trace_env)
    )
    client = MultiServerMCPClient({"pydocs": connection})
    async with client.session("pydocs") as session:
        yield await load_mcp_tools(session, tool_interceptors=[_intercept])


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
    from langgraph.errors import GraphRecursionError

    from pydocs_mcp.harness.ask_your_docs.agent import build_agent

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
        )
        try:
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content=str(sample["rendered_prompt"]))]},
                {"recursion_limit": _SUPER_STEPS_PER_TURN * settings.max_agent_turns},
            )
        except GraphRecursionError as exc:
            raise TurnBudgetExceededError(turn_limit=settings.max_agent_turns) from exc
    messages = result["messages"]
    return str(messages[-1].content), list(messages)


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
