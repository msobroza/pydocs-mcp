"""Eval-side adapter onto the product's ask-your-docs harness runner.

The optimize layer drives the ask agent through the PRODUCT run contract
(`python/pydocs_mcp/harness/core/run_contract.py`): one sample in, one
:class:`Trajectory` out. This module owns only what the eval side adds on
top — the architecture-name registry the ``ask_architecture`` artifact
validates against, the per-task timeout the campaign relies on, the
candidate → ``guidance_sections`` projection, and the offline fake.

Deleted here by the run-contract design §7 adaptation ledger (2026-07-27),
recorded so the history is not lost:

- ``AskTranscript`` / ``ToolCallRecord`` — fused into the product
  ``Trajectory`` / ``ToolCallRecord`` (which additionally carry
  ``trajectory_id``, ``trace_dir`` and the ``observed_by`` provenance tag).
- ``AskRunner`` Protocol — consolidated into ``HarnessRunner``.
- ``LangGraphAskRunner`` — replaced by the product binding's held-session,
  traced, per-run runner (``make_harness_runner``); this module now only
  wraps it with the per-task timeout.
- ``AskBuildRequest`` / ``AskArchitectureSpec.build`` / ``_resolve_build_agent``
  / ``make_ask_prompts`` — the eval side no longer calls ``build_agent``;
  candidates reach the agent as ``guidance_sections`` through the product's
  single assembly site.
- ``_digest`` / ``_flatten_answer`` — tool-call digesting and answer
  extraction now happen inside the product binding
  (``observability.trace_reader.tool_args_digest`` and ``run_task``).

Every product import is DEFERRED behind an actionable guard, and anything
that can SPEND additionally calls ``_require_ask_extra()`` first — so
``import pydocs_eval.optimize`` pulls neither langgraph nor ``pydocs_mcp``
(the ``[ask]`` extra is opt-in like ``[retrieval]``, and the fitness registry
populates on a base install).
"""

from __future__ import annotations

import asyncio
import importlib.util
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra
from pydocs_eval.optimize._agent_track_binding import DEFAULT_TASK_TIMEOUT_SECONDS
from pydocs_eval.optimize.protocols import OptimizableArtifact
from pydocs_eval.registries import (
    _Registry,  # WHY: same in-repo registry mechanic as the optimize axes; a second copy would drift
)
from pydocs_eval.task_rendering import TASK_SCAFFOLD_VERSION

if TYPE_CHECKING:
    from pydocs_mcp.harness.core.run_contract import HarnessRunner, Trajectory


def _run_contract() -> ModuleType:
    """The product run-contract module, imported behind the extras guard.

    DEFERRED (ADR 0009's 2026-07-27 amendment, route 1 in its guarded form):
    this module declares the run-contract coupling, but importing it eagerly
    would drag ``pydocs_mcp`` into the fitness REGISTRY population path — a
    base-install surface that must stay library-free. The guard turns a
    missing/too-old library into the actionable install hint.
    """
    try:
        import pydocs_mcp.harness.core.run_contract as contract
    except ImportError as exc:
        raise_missing_retrieval_extra(exc)
    return contract


# WHY: prompt campaigns pin every candidate to ONE architecture (the
# no-joint-search rule, spec §3.3.2/§4.2). "text_react" is the product
# agent_registry's plain text ReAct graph — the ask default on text models.
_DEFAULT_ASK_ARCHITECTURE = "text_react"

# Where per-trajectory ADR 0009 traces land. Single Python source; the run
# config YAML restates it for user-facing clarity.
DEFAULT_ASK_TRACE_ROOT = "~/.cache/pydocs-mcp/ask-traces"

# Which observation point the deterministic gates read (run-contract design
# §3): the SERVER slice of the trajectory, i.e. the trace itself. Recorded in
# the objective identity so a gate re-pointing is never a silent re-scoring.
_GATES_OBSERVATION_SOURCE = "server_trace"

# The product agent_registry names bridged one-to-one (spec §7-Q1), with the
# descriptions the dry-run listing shows. Adding a product architecture means
# adding ONE row here.
_PRODUCT_BRIDGES: Mapping[str, str] = {
    "auto": "capability-routed product default (text_react or vision_subagent)",
    "inline": "single ReAct graph with images inline in the conversation",
    "text_react": "plain text ReAct agent over the nine task-shaped tools",
    "vision_subagent": "text ReAct plus a one-shot vision extraction subagent",
}


@dataclass(frozen=True, slots=True)
class HarnessBridge:
    """One harness the ``arms:`` block may name, resolved LAZILY (design §6/§7).

    The architecture-level ``_PRODUCT_BRIDGES`` rows above bridge product
    agent-graph NAMES; this is the same mechanism widened to harness level: a
    row declares the dotted factory path an arm's ``runner`` spells, the extra
    that ships it, the modules whose absence proves the extra is missing, and
    the dotted path of the harness's section→channel delivery-map digest (the
    third component of arm identity). Nothing here imports anything — adding a
    harness is ONE row, and a harness behind an optional extra costs zero
    until an arm actually runs it.
    """

    dotted_path: str
    extra: str
    required_modules: tuple[str, ...]
    delivery_map_digest_path: str
    description: str


_ASK_HARNESS_BRIDGE = HarnessBridge(
    dotted_path="pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner",
    extra="ask",
    required_modules=("langgraph", "pydocs_mcp"),
    delivery_map_digest_path="pydocs_mcp.harness.ask_your_docs.binding:delivery_map_digest",
    description="the ask agent",
)

_HARNESS_BRIDGES: Mapping[str, HarnessBridge] = {
    _ASK_HARNESS_BRIDGE.dotted_path: _ASK_HARNESS_BRIDGE,
}


def harness_bridge_for(dotted_path: str) -> HarnessBridge:
    """Look up a harness bridge by its dotted factory path — NAME check only.

    The ``arms:`` firewall's byte-identical-name rule: an unregistered path is
    a ``KeyError`` naming the offending value and the known rows, raised at
    config load time WITHOUT importing the harness.

    Raises:
        KeyError: ``dotted_path`` names no registered harness bridge.
    """
    bridge = _HARNESS_BRIDGES.get(dotted_path)
    if bridge is None:
        raise KeyError(f"unknown harness runner {dotted_path!r}; have {list(_HARNESS_BRIDGES)}")
    return bridge


def _missing_module_for(required_modules: tuple[str, ...]) -> str | None:
    """Name the first absent module of ``required_modules``, or ``None``.

    ``find_spec`` only (no import side effects) — the ``ensure_available``
    shape from the skillopt adapter.
    """
    for module in required_modules:
        if importlib.util.find_spec(module) is None:
            return module
    return None


def _ask_extra_missing_module() -> str | None:
    """Name the first missing ``[ask]`` dependency, or ``None`` when complete."""
    return _missing_module_for(_ASK_HARNESS_BRIDGE.required_modules)


def _missing_extra_hint(missing: str, bridge: HarnessBridge) -> str:
    """The one install-hint sentence every harness guard raises (single source)."""
    return (
        f"{missing!r} is not installed — run "
        f'pip install "pydocs-mcp-eval[{bridge.extra}]" to drive {bridge.description} '
        f"(the product version floor is pinned in benchmarks/pyproject.toml)"
    )


def require_harness_extra(bridge: HarnessBridge) -> None:
    """Raise an actionable ``RuntimeError`` when ``bridge``'s extra is absent."""
    missing = _missing_module_for(bridge.required_modules)
    if missing is not None:
        raise RuntimeError(_missing_extra_hint(missing, bridge))


def _require_ask_extra() -> None:
    """Raise an actionable ``RuntimeError`` when the ``[ask]`` extra is absent.

    The ask harness's spelling of :func:`require_harness_extra`; kept as its
    own name because the spend-guarded call sites (and their tests) read it as
    the one thing they must do before touching langgraph. Routed through
    ``_ask_extra_missing_module`` so those tests can still monkeypatch the
    probe rather than the whole guard.
    """
    missing = _ask_extra_missing_module()
    if missing is not None:
        raise RuntimeError(_missing_extra_hint(missing, _ASK_HARNESS_BRIDGE))


def resolve_harness_runner_factory(dotted_path: str) -> Callable[..., object]:
    """Import and return the harness runner factory an arm's ``runner`` names.

    The ONE place a ``runner:`` string becomes a callable, and the only place
    the harness module is imported — so config loading never pays for a
    harness it does not run, and a missing extra fails with the install hint
    instead of a bare ``ModuleNotFoundError``.

    Raises:
        KeyError: ``dotted_path`` names no registered harness bridge.
        RuntimeError: the bridge's extra is not installed.
    """
    bridge = harness_bridge_for(dotted_path)
    require_harness_extra(bridge)
    return _import_dotted(bridge.dotted_path)


def harness_delivery_map_hash(dotted_path: str) -> str:
    """The harness's section→channel delivery-map digest (arm-identity input).

    Resolved through the same lazy bridge row as the runner, so the digest an
    arm hash folds in is the one the harness that runs it actually declares
    (run-contract design §4: two arms delivering the same text differently are
    different arms).

    WHY no ``require_harness_extra``: the digest is a pure product constant
    (the ``ask_binding_identity`` precedent) and the zero-spend dry run must
    be able to compute an arm hash on a machine without the agent runtime.
    """
    bridge = harness_bridge_for(dotted_path)
    digest = _import_dotted(bridge.delivery_map_digest_path)
    return str(digest())


def _import_dotted(dotted_path: str) -> Callable[..., object]:
    """Resolve ``module.path:attribute`` to the attribute, behind the guard."""
    module_name, _, attribute = dotted_path.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise_missing_retrieval_extra(exc)
    resolved = getattr(module, attribute)
    return resolved  # type: ignore[no-any-return]


def ask_binding_identity() -> dict[str, str]:
    """What about THIS execution path can move a recorded verdict (design §8).

    Folded into ``rubric_config_hash(..., binding_identity=…)`` so the three
    verdict-moving stage-3 changes — the shared task scaffold now reaching the
    ask path, the harness's section→channel delivery map, and the gates
    reading the server trace — land as ONE versioned objective change instead
    of three silent re-scorings.

    WHY no ``_require_ask_extra()``: the digest is a pure product constant,
    and the zero-spend dry run must be able to compute the objective hash on a
    machine without langgraph.
    """
    from pydocs_mcp.harness.ask_your_docs.binding import delivery_map_digest

    return {
        "scaffold": TASK_SCAFFOLD_VERSION,
        "delivery_map": delivery_map_digest(),
        "gates_source": _GATES_OBSERVATION_SOURCE,
    }


def known_task_names() -> tuple[str, ...]:
    """The product loader's enumerated v1 task names (design §5), deferred.

    The single source an arm's ``task_name`` is checked against — v1 names are
    a FIXED set, and widening them is a deliberate product event, never an
    eval-side config edit, so no benchmarks-side copy exists.
    """
    try:
        from pydocs_mcp.harness.core.skill_artifact_loader import TASK_NAMES
    except ImportError as exc:
        raise_missing_retrieval_extra(exc)
    return TASK_NAMES


def guidance_sections_for_candidate(artifact: OptimizableArtifact) -> dict[str, str]:
    """Project a candidate artifact into the contract's section mapping (design §4).

    Delimited text families (``ask_prompt``, and any future sectioned family)
    parse into named sections the harness delivers through its own channels.
    Families whose render is NOT a delimited document — the YAML cells
    (``ask_architecture``, ``retrieval_config``) and the free-form
    ``usage_skill`` — yield an empty mapping: their effect rides the runner
    settings / serve overlay exactly as before, and the empty mapping is the
    honest statement that no section text is being delivered.

    Example:
        >>> guidance_sections_for_candidate(artifact)  # doctest: +SKIP
        {'SYSTEM_PROMPT': '…', 'REWRITE_PROMPT': '…'}
    """
    try:
        from pydocs_mcp.application.description_source import parse_sections
    except ImportError as exc:
        raise_missing_retrieval_extra(exc)
    return parse_sections(artifact.render())


@dataclass(frozen=True, slots=True)
class AskArchitectureSpec:
    """A named way of assembling the ask agent for evaluation (spec §3.2.2).

    Registry row only: the ``ask_architecture`` artifact validates a cell's
    ``architecture`` against these names, and the runner passes the chosen
    name to the product binding as a setting. Assembly itself is the product's
    job (``build_agent`` is no longer called from the eval side).
    """

    name: str = ""
    description: str = ""


ask_architecture_registry: _Registry[AskArchitectureSpec] = _Registry()


def _bridge_factory(arch_name: str, description: str) -> Callable[[], AskArchitectureSpec]:
    """One registrable zero-arg factory per product name.

    WHY a factory and not a subclass: ``_Registry.build`` instantiates the
    registered entry with no args, and slotted-dataclass subclasses cannot
    re-declare inherited fields as defaults — a closure-captured factory
    gives each name its spec without fighting ``__slots__``.
    """

    def factory() -> AskArchitectureSpec:
        return AskArchitectureSpec(name=arch_name, description=description)

    factory.__name__ = f"bridge_{arch_name}"
    return factory


for _name, _description in _PRODUCT_BRIDGES.items():
    ask_architecture_registry.register(_name)(_bridge_factory(_name, _description))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class TimeoutBoundedAskRunner:
    """Bounds one product harness run by the campaign's per-task timeout.

    The product binding owns the whole rollout (held serve session, trace,
    guidance delivery); this wrapper adds only the eval-side failure policy:
    a hung tool call (timeout) or a runaway candidate
    (``TurnBudgetExceededError``) yields a sentinel FAILED trajectory rather
    than an exception, so one bad candidate costs its own sample and never the
    whole campaign — the gates then fail that sample deterministically
    (``turns = cap + 1`` fails ``max_turns``; the empty answer fails
    ``min_answer_chars``).
    """

    inner: HarnessRunner
    task_timeout_seconds: float
    max_agent_turns: int

    async def run(
        self, sample: Mapping[str, object], guidance_sections: Mapping[str, str]
    ) -> Trajectory:
        turn_budget_exceeded = _run_contract().TurnBudgetExceededError
        started = time.monotonic()
        try:
            return await asyncio.wait_for(
                self.inner.run(sample, guidance_sections), timeout=self.task_timeout_seconds
            )
        except (TimeoutError, turn_budget_exceeded):
            return _failed_trajectory(
                turns=self.max_agent_turns + 1, wall_seconds=time.monotonic() - started
            )


def _failed_trajectory(*, turns: int, wall_seconds: float) -> Trajectory:
    """The sentinel a timed-out / runaway candidate scores as.

    WHY cost_usd is 0.0: the agent runs against an OpenAI-compatible endpoint
    whose pricing the harness cannot know (often a local server); the metered
    spend is the judge arm, bounded by ``budget.max_judge_calls`` — the
    documented spend asymmetry, mirrored in the runbook.
    """
    return _run_contract().Trajectory(
        trajectory_id="",
        trace_dir=Path(),
        answer="",
        tool_calls=(),
        turns=turns,
        cost_usd=0.0,
        wall_seconds=wall_seconds,
    )


def build_harness_runner(
    dotted_path: str,
    settings: Mapping[str, object],
    *,
    task_timeout_seconds: float,
    max_agent_turns: int,
) -> TimeoutBoundedAskRunner:
    """Construct ONE arm's harness runner from its own opaque settings mapping.

    The ``arms:`` construction site (run-contract design §6): the settings
    mapping travels UNINSPECTED to the harness factory, which owns its keys
    and validates them (``extra="forbid"``, so a typo fails loud before any
    spend). Resolution goes through :func:`resolve_harness_runner_factory`, so
    the harness module is imported HERE and nowhere earlier — an arm that is
    never run costs no import.

    Raises:
        KeyError: ``dotted_path`` names no registered harness bridge.
        RuntimeError: the bridge's extra is not installed.
    """
    factory = resolve_harness_runner_factory(dotted_path)
    return TimeoutBoundedAskRunner(
        inner=factory(dict(settings)),  # type: ignore[arg-type]
        task_timeout_seconds=task_timeout_seconds,
        max_agent_turns=max_agent_turns,
    )


def build_ask_harness_runner(
    *,
    workspace: Path,
    model: str,
    architecture: str,
    max_agent_turns: int,
    base_url: str | None = None,
    pydocs_config: Path | None = None,
    trace_root: str = DEFAULT_ASK_TRACE_ROOT,
    task_timeout_seconds: float = DEFAULT_TASK_TIMEOUT_SECONDS,
) -> TimeoutBoundedAskRunner:
    """Build the timeout-bounded ask ``HarnessRunner`` from typed keyword args.

    The ask harness's typed spelling of :func:`build_harness_runner` — for
    callers that hold the individual settings rather than an arm's opaque
    mapping. Construction is guarded (AC-18): calling this without the
    ``[ask]`` extra raises the actionable RuntimeError before any spend.
    """
    _require_ask_extra()
    return build_harness_runner(
        _ASK_HARNESS_BRIDGE.dotted_path,
        {
            "workspace": str(workspace),
            "model": model,
            "base_url": base_url,
            "pydocs_config": str(pydocs_config) if pydocs_config is not None else None,
            "architecture": architecture,
            "max_agent_turns": max_agent_turns,
            "trace_root": trace_root,
        },
        task_timeout_seconds=task_timeout_seconds,
        max_agent_turns=max_agent_turns,
    )


@dataclass(slots=True)
class FakeAskRunner:
    """Scripted offline double: canned trajectories keyed by ``record_id``.

    An unscripted sample returns the empty trajectory (which fails the shipped
    gates deterministically). ``calls`` lets tests prove per-sample resume
    never re-runs the agent (spec AC-11, AC-19); ``seen_guidance_sections``
    records what the fitness delivered so tests can pin the candidate → section
    projection without a live agent.
    """

    scripted: Mapping[str, Trajectory]
    calls: int = field(default=0, init=False)
    seen_guidance_sections: list[Mapping[str, str]] = field(default_factory=list, init=False)

    async def run(
        self, sample: Mapping[str, object], guidance_sections: Mapping[str, str]
    ) -> Trajectory:
        self.calls += 1
        self.seen_guidance_sections.append(dict(guidance_sections))
        scripted = self.scripted.get(str(sample["record_id"]))
        return scripted if scripted is not None else _failed_trajectory(turns=0, wall_seconds=0.0)
