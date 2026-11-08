"""``--reuse-baseline <arm dir>``: stand a finished baseline arm in for this run's.

A ladder compares every step's candidate against the same accepted state, so
re-running the baseline arm each time pays for the same answers again. A
finished arm may stand in for this plan's baseline only when it ran what that
baseline would run: the same commit, the same chat model, the same turn cap and
the same settings digest (``ArmVariant.digest`` — architecture, llm block,
harness keys, guidance text, scaffold version). Anything else would measure
the candidate against a different experiment, so every mismatch is refused by
name, all at once, at plan time.

An arm the vendored chat runner wrote (``benchmarks/tools/run_example_needle_chat_repro.py``)
is read like any other: it records the same four fields.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

from pydocs_eval.campaign.before_after import ArmRole, MeasurementPlan, MeasurementPlanError
from pydocs_eval.campaign.before_after_arm_records import (
    ARM_SUMMARY_FILENAME,
    ArmSummary,
    read_arm_summary,
)


@dataclass(frozen=True, slots=True)
class BaselineIdentity:
    """What a reused baseline must share with the plan's baseline arm."""

    commit: str
    model: str
    max_agent_turns: int
    settings_digest: str

    @classmethod
    def of_plan(cls, plan: MeasurementPlan) -> BaselineIdentity:
        variant = plan.arm_variants.baseline
        return cls(
            commit=plan.baseline.sha,
            model=plan.model,
            max_agent_turns=variant.max_agent_turns,
            settings_digest=variant.digest,
        )

    @classmethod
    def of_summary(cls, summary: ArmSummary) -> BaselineIdentity:
        return cls(
            commit=summary.commit,
            model=summary.model,
            max_agent_turns=summary.max_agent_turns,
            settings_digest=summary.settings_digest,
        )


def reusable_baseline(arm_dir: Path, expected: BaselineIdentity) -> ArmSummary:
    """The finished arm in ``arm_dir``, if it ran what ``expected`` describes.

    Raises:
        MeasurementPlanError: no readable ``arm.json`` there, or any of the four
            fields differs — every differing field is named with both values.
    """
    summary = _recorded_summary(arm_dir)
    recorded = BaselineIdentity.of_summary(summary)
    mismatches = [
        f"{field.name}: recorded {getattr(recorded, field.name)!r}, "
        f"the plan's baseline {getattr(expected, field.name)!r}"
        for field in fields(BaselineIdentity)
        if getattr(recorded, field.name) != getattr(expected, field.name)
    ]
    if mismatches:
        raise MeasurementPlanError(
            f"--reuse-baseline {arm_dir} ran something other than this plan's baseline "
            f"({'; '.join(mismatches)}); reuse an arm that matches, or drop the flag "
            "to run the baseline"
        )
    return summary


def _recorded_summary(arm_dir: Path) -> ArmSummary:
    try:
        return read_arm_summary(arm_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MeasurementPlanError(
            f"--reuse-baseline {arm_dir} holds no readable {ARM_SUMMARY_FILENAME} ({exc}); "
            "expected a finished arm — a before-after arm or a chat runner output"
        ) from exc


def link_reused_baseline(arm_dir: Path, out_dir: Path) -> Path:
    """Point ``<out>/baseline`` at the reused arm, so this run reads it as its own.

    Nothing to do when ``arm_dir`` already IS that directory.

    Raises:
        MeasurementPlanError: ``<out>/baseline`` already holds something else.
    """
    link = out_dir / ArmRole.BASELINE
    if link.resolve() == arm_dir.resolve():
        return link
    if link.exists() or link.is_symlink():
        raise MeasurementPlanError(
            f"--reuse-baseline {arm_dir}: {link} already holds another arm; "
            "choose another --out, or reuse that arm instead"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    link.symlink_to(arm_dir.resolve(), target_is_directory=True)
    return link


__all__ = ("BaselineIdentity", "link_reused_baseline", "reusable_baseline")
