"""Failure-taxonomy decision tree (ADR 0012 — fully rule-based labels).

Mutually exclusive labels assigned by a deterministic **first-match** decision
tree over trace + parsed-eval facts. First-match ordering IS the
mutual-exclusivity mechanism: a trajectory matching two conditions gets exactly
the earlier label, deterministically. ``taxonomy_version`` is stamped on every
labeled output; any reordering or new label bumps it.

The evaluated order (ADR 0012), with two additions marked ``[+]`` — both total,
neither reorders the failure list:

    infra_error → empty_trajectory → crash_before_first_tool → patch_apply_failed
    → budget_exhausted → resolved [+] → never_ran_tests → localization_miss
    → found_but_misdiagnosed → right_idea_broken_edit → regression_introduced
    → unclassified_failure [+]

``resolved`` [+] is the success terminal (NOT in the ADR's failure list); it is
placed right after ``budget_exhausted`` so a solved run that skipped self-testing
is not mislabeled ``never_ran_tests``. Since every failure branch below it
presupposes a non-resolved outcome, moving the check there loses no failure
diagnosis. ``unclassified_failure`` [+] is the exhaustive terminal — unreachable
on self-consistent inputs, present so the function is total.

Two detectors are versioned rules shipped as config data (``configs/taxonomy.yaml``):

- ``never_ran_tests`` — a loop-side Bash ``tool_use`` whose ``command`` matches
  the versioned ``test_runner_patterns`` set (ADR 0012).
- ``budget_exhausted`` — evaluated STRICTLY against the caps the R2 run-config
  lockfile records: a cap clause fires only when that cap is non-null. Today the
  turn cap is the only live cap (headless claude records token/wall caps as
  ``null`` with ``unrecorded_by_client``), so the token/wall clauses are inert by
  construction — a clause whose cap is ``null`` never fires, keeping the predicate
  total on every trace regardless of which caps its lockfile carries.

``infra_error`` is labeled but EXCLUDED from all score aggregates (owned by the
consumer aggregator, ``consumers.py``); every other label is included.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

import yaml

from pydocs_eval.trajectory.eval_report import GroundTruthOutcome
from pydocs_eval.trajectory.schema import LoopEvent, ToolEvent

_CONFIG_PACKAGE = "pydocs_eval.trajectory.configs"
_TAXONOMY_RESOURCE = "taxonomy.yaml"

# Loop-side client tool whose raw ``command`` input the never_ran_tests detector
# scans (ADR 0009: Bash uses stay loop-side ``tool_use`` LoopEvents, not MCP
# ToolEvents).
_BASH_TOOL = "Bash"

# Canonical label strings (ADR 0012). The ``[+]`` additions are documented above.
INFRA_ERROR = "infra_error"
EMPTY_TRAJECTORY = "empty_trajectory"
CRASH_BEFORE_FIRST_TOOL = "crash_before_first_tool"
PATCH_APPLY_FAILED = "patch_apply_failed"
BUDGET_EXHAUSTED = "budget_exhausted"
RESOLVED = "resolved"
NEVER_RAN_TESTS = "never_ran_tests"
LOCALIZATION_MISS = "localization_miss"
FOUND_BUT_MISDIAGNOSED = "found_but_misdiagnosed"
RIGHT_IDEA_BROKEN_EDIT = "right_idea_broken_edit"
REGRESSION_INTRODUCED = "regression_introduced"
UNCLASSIFIED_FAILURE = "unclassified_failure"


@dataclass(frozen=True, slots=True)
class TaxonomyConfig:
    """The versioned taxonomy detector config, loaded from ``taxonomy.yaml``.

    ``test_runner_patterns`` are compiled regexes for the ``never_ran_tests``
    detector; ``version`` is stamped onto every labeled output.
    """

    version: int
    test_runner_patterns: tuple[re.Pattern[str], ...]


def _compile_patterns(raw: Any) -> tuple[re.Pattern[str], ...]:
    """Compile the ``test_runner_patterns`` list, raising with context on a bad shape."""
    if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
        raise ValueError(
            f"test_runner_patterns must be a list[str]: got {raw!r}, expected e.g. "
            "['\\\\bpytest\\\\b', ...]"
        )
    return tuple(re.compile(p) for p in raw)


@lru_cache(maxsize=1)
def load_taxonomy_config() -> TaxonomyConfig:
    """Load + cache the shipped taxonomy config (``configs/taxonomy.yaml``)."""
    text = files(_CONFIG_PACKAGE).joinpath(_TAXONOMY_RESOURCE).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    version = data.get("taxonomy_version")
    if not isinstance(version, int):
        raise ValueError(
            f"taxonomy_version must be an int: got {version!r} in {_TAXONOMY_RESOURCE}"
        )
    return TaxonomyConfig(
        version=version,
        test_runner_patterns=_compile_patterns(data.get("test_runner_patterns")),
    )


@dataclass(frozen=True, slots=True)
class TaxonomyInputs:
    """The rule-tree inputs for one trajectory — trace facts + parsed-eval facts.

    All fields are recomputable from the canonical ``events.jsonl`` + parsed eval
    report + run-config lockfile (R1). ``turn_cap`` / ``token_cap`` / ``wall_cap``
    are the R2 lockfile caps (``None`` ⇒ the clause is inert, ADR 0012).
    """

    outcome: GroundTruthOutcome
    tool_events: tuple[ToolEvent, ...]
    loop_events: tuple[LoopEvent, ...]
    patch_bytes: int
    gold_surfaced: bool
    patch_touches_gold: bool
    f2p_fraction: float
    p2p_regressions: int
    num_turns: int
    total_tokens: int
    wall_seconds: float
    turn_cap: int | None
    token_cap: int | None = None
    wall_cap: int | None = None


def _has_any_tool(inputs: TaxonomyInputs) -> bool:
    """True when ANY tool ran — an MCP ToolEvent OR a loop-side ``tool_use`` (Bash)."""
    return bool(inputs.tool_events) or any(e.kind == "tool_use" for e in inputs.loop_events)


def _has_pre_tool_activity(inputs: TaxonomyInputs) -> bool:
    """True when the model emitted assistant text (activity that is not a tool call)."""
    return any(e.kind == "assistant" and bool(e.text) for e in inputs.loop_events)


def _has_error_result(inputs: TaxonomyInputs) -> bool:
    """True when a final/loop ``result`` event is flagged ``is_error``."""
    return any(e.kind == "result" and e.is_error for e in inputs.loop_events)


def _is_empty_trajectory(inputs: TaxonomyInputs) -> bool:
    """No tool call, no model activity, no error, empty patch — nothing was produced."""
    return (
        not _has_any_tool(inputs)
        and not _has_pre_tool_activity(inputs)
        and not _has_error_result(inputs)
        and inputs.patch_bytes == 0
    )


def _crashed_before_first_tool(inputs: TaxonomyInputs) -> bool:
    """Assistant activity or an error result, but no tool (MCP or Bash) ever ran."""
    return not _has_any_tool(inputs) and (
        _has_pre_tool_activity(inputs) or _has_error_result(inputs)
    )


def ran_tests(loop_events: tuple[LoopEvent, ...], config: TaxonomyConfig) -> bool:
    """True when a loop-side Bash ``tool_use`` command matches a test-runner pattern.

    The versioned detector for ``never_ran_tests`` (ADR 0012): scans raw Bash
    command strings only — captured loop-side facts, no judgment.

    Example:
        >>> from pydocs_eval.trajectory.schema import LoopEvent
        >>> e = LoopEvent(event_id="x", trajectory_id="t", kind="tool_use", turn=1,
        ...     tool="Bash", tool_input={"command": "pytest -q tests/"})
        >>> ran_tests((e,), load_taxonomy_config())
        True
    """
    for event in loop_events:
        command = _bash_command(event)
        if command and any(p.search(command) for p in config.test_runner_patterns):
            return True
    return False


def _bash_command(event: LoopEvent) -> str | None:
    """The ``command`` string of a loop-side Bash ``tool_use``, else ``None``."""
    if event.kind != "tool_use" or event.tool != _BASH_TOOL or event.tool_input is None:
        return None
    value = event.tool_input.get("command")
    return value if isinstance(value, str) and value else None


def budget_exhausted(inputs: TaxonomyInputs) -> bool:
    """A recorded cap was hit and no patch was produced (ADR 0012 lockfile-aware).

    Each cap clause fires ONLY when its cap is non-null in the R2 lockfile; a
    ``None`` cap clause never fires (inert by construction), so the predicate is
    total on every trace regardless of which caps were recorded.
    """
    if inputs.patch_bytes != 0:
        return False
    return (
        _cap_hit(inputs.num_turns, inputs.turn_cap)
        or _cap_hit(inputs.total_tokens, inputs.token_cap)
        or _cap_hit(inputs.wall_seconds, inputs.wall_cap)
    )


def _cap_hit(observed: float, cap: float | None) -> bool:
    """True iff a cap is recorded (non-null) and the observed value reached it."""
    return cap is not None and observed >= cap


@dataclass(frozen=True, slots=True)
class TaxonomyLabel:
    """A trajectory's taxonomy label + the ``taxonomy_version`` that produced it."""

    label: str
    taxonomy_version: int

    @property
    def excluded_from_aggregates(self) -> bool:
        """``infra_error`` is the only label excluded from score aggregates (ADR 0012)."""
        return self.label == INFRA_ERROR


def classify(inputs: TaxonomyInputs, *, config: TaxonomyConfig | None = None) -> TaxonomyLabel:
    """Assign the first-match taxonomy label (ADR 0012 order) + stamp the version."""
    cfg = config or load_taxonomy_config()
    return TaxonomyLabel(label=_classify_label(inputs, cfg), taxonomy_version=cfg.version)


def _classify_label(inputs: TaxonomyInputs, config: TaxonomyConfig) -> str:
    """The first-match decision tree — see the module docstring for the full order."""
    outcome = inputs.outcome
    if outcome.infra_error:
        return INFRA_ERROR
    if _is_empty_trajectory(inputs):
        return EMPTY_TRAJECTORY
    if _crashed_before_first_tool(inputs):
        return CRASH_BEFORE_FIRST_TOOL
    if outcome.patch_apply_failed:
        return PATCH_APPLY_FAILED
    if budget_exhausted(inputs):
        return BUDGET_EXHAUSTED
    if outcome.resolved:
        return RESOLVED
    return _classify_failure(inputs, config)


def _classify_failure(inputs: TaxonomyInputs, config: TaxonomyConfig) -> str:
    """The non-resolved failure branches (never_ran_tests … regression_introduced)."""
    if not ran_tests(inputs.loop_events, config):
        return NEVER_RAN_TESTS
    if not inputs.gold_surfaced:
        return LOCALIZATION_MISS
    if not inputs.patch_touches_gold:
        return FOUND_BUT_MISDIAGNOSED
    if inputs.f2p_fraction < 1.0:
        return RIGHT_IDEA_BROKEN_EDIT
    if inputs.p2p_regressions > 0:
        return REGRESSION_INTRODUCED
    return UNCLASSIFIED_FAILURE
