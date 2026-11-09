"""What one before/after arm leaves on disk: its per-task rows and its summary.

Split out of ``before_after_arm`` so the records an arm WRITES — read back by
``--report-only``, the compare verb, ``--reuse-baseline`` and a resumed arm —
live apart from the loop that produces them. ``before_after_arm`` re-exports
every name, so existing imports keep working.

Every field added after the first ``arm.json`` shipped is defaulted, so a file
written before it still loads; the defaults read as "not recorded", never as a
measured value.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from pydocs_eval.campaign.before_after_index_step import BundleIndexStep
from pydocs_eval.trajectory.ask_outcome import UNKNOWN_TURN_BUDGET, TaskOutcome

ARM_SUMMARY_FILENAME = "arm.json"
_SERVING_OVERRIDE_FIELD = "serving_override"
# The forced-reuse record's sidecar beside a candidate's ``arm.json``.
SERVING_OVERRIDE_FILENAME = "serving_override.json"


@dataclass(frozen=True, slots=True)
class ArmTaskRecord:
    """Where one answered task's evidence landed, its gold, and how the run ended.

    The last four fields are defaulted so an ``arm.json`` written before they
    existed still loads: its rows read ``UNRECORDED``, and the measurement
    back-fills what a legacy row can still tell (``ask_outcome.legacy_outcome_of``).
    ``tool_calls`` is ``None`` when not recorded — undefined, never "no calls".
    ``answer`` never holds LangGraph's canned apology (``recorded_answer``).
    """

    task_id: str
    trajectory_id: str
    trace_dir: str
    gold_files: list[str]
    turns: int
    wall_seconds: float
    answer_chars: int
    answer: str = ""
    outcome: TaskOutcome = TaskOutcome.UNRECORDED
    tool_calls: int | None = None
    near_cap: bool = False

    @classmethod
    def from_dict(cls, row: Mapping[str, object]) -> ArmTaskRecord:
        """One ``arm.json`` row; an unknown outcome is refused by name.

        A row without an ``outcome`` keeps the field's own default.
        """
        fields = dict(row)
        if "outcome" in fields:
            fields["outcome"] = _task_outcome(fields["outcome"])
        return cls(**fields)  # type: ignore[arg-type]


def _task_outcome(value: object) -> TaskOutcome:
    """``value`` as a :class:`TaskOutcome`, or a ValueError naming it and the vocabulary."""
    try:
        return TaskOutcome(str(value))
    except ValueError:
        accepted = ", ".join(outcome.value for outcome in TaskOutcome)
        raise ValueError(f"arm.json outcome {value!r} is not one of: {accepted}") from None


# The flag a forced reuse names; spelled once, here, for the record and the CLI.
ALLOW_SERVING_MISMATCH_FLAG = "--allow-serving-mismatch"


@dataclass(frozen=True, slots=True)
class ServingMismatchOverride:
    """A reuse ``--allow-serving-mismatch`` forced: which baseline, and past which keys.

    Recorded beside the CANDIDATE's ``arm.json``, in ``serving_override.json``
    (and nowhere when no reuse was forced), so every report that reads the
    candidate shows the comparison was made against a baseline served
    differently — or one that never said.
    """

    reused_baseline: str
    differing_keys: list[str]
    fingerprint_recorded: bool

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ServingMismatchOverride:
        keys: Sequence[object] = payload["differing_keys"]  # type: ignore[assignment]
        return cls(
            reused_baseline=str(payload["reused_baseline"]),
            differing_keys=[str(key) for key in keys],
            fingerprint_recorded=bool(payload["fingerprint_recorded"]),
        )

    def provenance_text(self) -> str:
        """One line of a report: the reuse was forced, and over what."""
        keys = ", ".join(f"`{key}`" for key in self.differing_keys)
        forced = f"reuse of `{self.reused_baseline}` forced by `{ALLOW_SERVING_MISMATCH_FLAG}`"
        if not self.fingerprint_recorded:
            return f"{forced}; it recorded no serving fingerprint, so these went unverified: {keys}"
        return f"{forced} over differing serving key(s): {keys}"


@dataclass(frozen=True, slots=True)
class ArmVariantRecord:
    """What an arm recorded about the settings it ran under beyond its commit.

    ``settings_digest == ""`` means not recorded (an arm written before
    variant arms): nothing is printed for it and nothing can match it.
    """

    architecture: str = ""
    task_scaffold: str = ""
    guidance_sections_path: str | None = None
    settings_digest: str = ""
    serving_override: ServingMismatchOverride | None = None

    @property
    def recorded(self) -> bool:
        return bool(self.settings_digest)

    def provenance_text(self) -> str:
        """One line of a report's provenance: enough to tell two variant arms apart."""
        guidance = (
            "none" if self.guidance_sections_path is None else f"`{self.guidance_sections_path}`"
        )
        return (
            f"architecture `{self.architecture}`, scaffold `{self.task_scaffold}`, "
            f"guidance {guidance}, digest `{self.settings_digest}`"
        )


@dataclass(frozen=True, slots=True)
class ArmSummary:
    """One arm's result index — what the report reads instead of re-running.

    ``max_agent_turns`` is the budget this arm ran under, so its outcomes can be
    read against its OWN cap; ``UNKNOWN_TURN_BUDGET`` for an ``arm.json`` written
    before the field. ``index_steps`` is what the arm's incremental re-index
    cost per bundle — wall time and embed diff; ``[]`` for an older ``arm.json``.

    The last four fields say what the arm ran under beyond its commit (issue
    #385) — what the report's provenance prints and ``--reuse-baseline``
    matches on. ``""`` / ``None`` in an older ``arm.json``: not recorded.
    """

    role: str
    commit: str
    model: str
    trace_root: str
    tasks: list[ArmTaskRecord]
    estimated_usd: float
    halt_reason: str
    excluded: int
    max_agent_turns: int = UNKNOWN_TURN_BUDGET
    index_steps: list[BundleIndexStep] = field(default_factory=list)
    architecture: str = ""
    task_scaffold: str = ""
    guidance_sections_path: str | None = None
    settings_digest: str = ""
    # Set only on a candidate compared against a reuse forced past a serving
    # mismatch, read from the ``serving_override.json`` sidecar beside it; left
    # out of ``arm.json`` otherwise, so that file's bytes stay put.
    serving_override: ServingMismatchOverride | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        if self.serving_override is None:
            del payload[_SERVING_OVERRIDE_FIELD]
        return payload

    def variant_record(self) -> ArmVariantRecord:
        return ArmVariantRecord(
            architecture=self.architecture,
            task_scaffold=self.task_scaffold,
            guidance_sections_path=self.guidance_sections_path,
            settings_digest=self.settings_digest,
            serving_override=self.serving_override,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ArmSummary:
        rows: Sequence[Mapping[str, object]] = payload.get("tasks", [])  # type: ignore[assignment]
        return cls(
            role=str(payload["role"]),
            commit=str(payload["commit"]),
            model=str(payload["model"]),
            trace_root=str(payload["trace_root"]),
            tasks=[ArmTaskRecord.from_dict(row) for row in rows],
            estimated_usd=float(payload["estimated_usd"]),
            halt_reason=str(payload["halt_reason"]),
            excluded=int(payload["excluded"]),
            max_agent_turns=int(payload.get("max_agent_turns", UNKNOWN_TURN_BUDGET)),  # type: ignore[call-overload]
            index_steps=[BundleIndexStep(**step) for step in payload.get("index_steps", [])],  # type: ignore[attr-defined]
            serving_override=_recorded_override(payload),
            **_recorded_variant(payload),
        )


def _recorded_override(payload: Mapping[str, object]) -> ServingMismatchOverride | None:
    recorded = payload.get(_SERVING_OVERRIDE_FIELD)
    return ServingMismatchOverride.from_dict(recorded) if isinstance(recorded, Mapping) else None


def _recorded_variant(payload: Mapping[str, object]) -> dict[str, Any]:
    """The variant fields an ``arm.json`` recorded; absent ones keep their defaults."""
    keys = ("architecture", "task_scaffold", "guidance_sections_path", "settings_digest")
    return {key: payload[key] for key in keys if key in payload}


def read_arm_summary(out_dir: Path) -> ArmSummary:
    """Load the summary an arm process wrote into ``out_dir``, with any forced-reuse record."""
    payload = json.loads((out_dir / ARM_SUMMARY_FILENAME).read_text(encoding="utf-8"))
    summary = ArmSummary.from_dict(payload)
    override = read_serving_override(out_dir)
    return summary if override is None else replace(summary, serving_override=override)


def write_serving_override(arm_dir: Path, override: ServingMismatchOverride | None) -> None:
    """Record (or, for None, clear) the forced reuse the arm in ``arm_dir`` is compared against.

    A sidecar the parent writes BEFORE it spawns the arm, never a field it
    stamps into ``arm.json`` afterwards: a parent killed between the child's
    last write and the stamp lost it, and the child rewrites ``arm.json``
    after every task. The run identity stays untouched — the override is a
    fact about the comparison, not about how the candidate ran.
    """
    path = arm_dir / SERVING_OVERRIDE_FILENAME
    if override is None:
        path.unlink(missing_ok=True)
        return
    path.write_text(json.dumps(asdict(override), indent=2, sort_keys=True), encoding="utf-8")


def read_serving_override(arm_dir: Path) -> ServingMismatchOverride | None:
    """The forced-reuse record beside an arm's ``arm.json``; None when no reuse was forced."""
    path = arm_dir / SERVING_OVERRIDE_FILENAME
    if not path.is_file():
        return None
    return ServingMismatchOverride.from_dict(json.loads(path.read_text(encoding="utf-8")))


def write_arm_summary(out_dir: Path, summary: ArmSummary) -> Path:
    """Persist the arm summary; return the file written.

    Atomic (a sibling file, then a rename): the arm rewrites this file after
    every answered task, and a kill mid-write must leave the previous
    checkpoint whole rather than a truncated file that loses every row.
    """
    path = out_dir / ARM_SUMMARY_FILENAME
    partial = path.with_name(f"{ARM_SUMMARY_FILENAME}.partial")
    partial.write_text(json.dumps(summary.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    partial.replace(path)
    return path
