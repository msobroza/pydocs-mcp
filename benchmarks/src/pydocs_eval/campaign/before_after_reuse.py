"""``--reuse-baseline <arm dir>``: stand a finished baseline arm in for this run's.

A ladder compares every step's candidate against the same accepted state, so
re-running the baseline arm each time pays for the same answers again. A
finished arm may stand in for this plan's baseline only when it ran what that
baseline would run: the same commit, the same chat model, the same turn cap and
the same settings digest (``ArmVariant.digest`` — architecture, llm block,
harness keys, guidance text, scaffold version). It must also have FINISHED
(halt reason ``completed``) over this plan's tasks: a killed or guard-halted
arm, or one over another split or ``--limit``, answered a different task set.
Anything else would measure the candidate against a different experiment, so
every mismatch is refused by name, all at once, at plan time — before either
arm is probed.

An arm the vendored chat runner wrote (``benchmarks/tools/run_example_needle_chat_repro.py``)
is read like any other: it records the same four fields.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, fields
from pathlib import Path

from pydocs_eval.campaign.before_after import ArmRole, MeasurementPlanError, resolve_commit
from pydocs_eval.campaign.before_after_arm_records import (
    ARM_SUMMARY_FILENAME,
    ArmSummary,
    read_arm_summary,
)
from pydocs_eval.campaign.before_after_arm_variant import ArmVariant
from pydocs_eval.campaign.budget import HaltReason


@dataclass(frozen=True, slots=True)
class BaselineIdentity:
    """What a reused baseline must share with the plan's baseline arm."""

    commit: str
    model: str
    max_agent_turns: int
    settings_digest: str

    @classmethod
    def of_summary(cls, summary: ArmSummary) -> BaselineIdentity:
        return cls(
            commit=summary.commit,
            model=summary.model,
            max_agent_turns=summary.max_agent_turns,
            settings_digest=summary.settings_digest,
        )


def checked_reused_baseline(
    args: argparse.Namespace, baseline: ArmVariant, task_ids: Sequence[str]
) -> str | None:
    """``--reuse-baseline``, checked against the baseline this plan would run; None without it.

    Resolves the baseline commit itself, so the check runs before ``build_plan``
    probes any arm — a refused reuse costs no worktree.
    """
    if args.reuse_baseline is None:
        return None
    sha, _subject = resolve_commit(args.repo, ArmRole.BASELINE, args.baseline)
    expected = BaselineIdentity(
        commit=sha,
        model=args.model,
        max_agent_turns=baseline.max_agent_turns,
        settings_digest=baseline.digest,
    )
    reusable_baseline(args.reuse_baseline, expected, task_ids=task_ids)
    return str(args.reuse_baseline)


def reusable_baseline(
    arm_dir: Path, expected: BaselineIdentity, *, task_ids: Sequence[str]
) -> ArmSummary:
    """The finished arm in ``arm_dir``, if it ran what ``expected`` describes over ``task_ids``.

    Raises:
        MeasurementPlanError: no readable ``arm.json`` there; or any of the four
            fields differs, the arm did not finish, or it answered other tasks —
            every problem is named with both values.
    """
    summary = _recorded_summary(arm_dir)
    mismatches = [
        *_identity_mismatches(BaselineIdentity.of_summary(summary), expected),
        *_unfinished(summary),
        *_task_set_mismatches(summary, task_ids),
    ]
    if mismatches:
        raise MeasurementPlanError(
            f"--reuse-baseline {arm_dir} ran something other than this plan's baseline "
            f"({'; '.join(mismatches)}); reuse an arm that matches, or drop the flag "
            "to run the baseline"
        )
    return summary


def _identity_mismatches(recorded: BaselineIdentity, expected: BaselineIdentity) -> list[str]:
    return [
        f"{field.name}: recorded {getattr(recorded, field.name)!r}, "
        f"the plan's baseline {getattr(expected, field.name)!r}"
        for field in fields(BaselineIdentity)
        if getattr(recorded, field.name) != getattr(expected, field.name)
    ]


def _unfinished(summary: ArmSummary) -> list[str]:
    """A killed (``in_progress``) or guard-halted arm answered only part of its split."""
    if summary.halt_reason == HaltReason.COMPLETED:
        return []
    return [
        f"halt_reason: recorded {summary.halt_reason!r}, "
        f"a reused baseline must be {HaltReason.COMPLETED.value!r}"
    ]


def _task_set_mismatches(summary: ArmSummary, task_ids: Sequence[str]) -> list[str]:
    """Rows outside the plan's tasks, or too few to have covered them."""
    planned = set(task_ids)
    foreign = sorted(row.task_id for row in summary.tasks if row.task_id not in planned)
    covered = len(summary.tasks) + summary.excluded
    problems = [f"tasks not in this plan's split: {foreign!r}"] if foreign else []
    if covered < len(planned):
        problems.append(f"answered or excluded {covered} of the plan's {len(planned)} task(s)")
    return problems


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


__all__ = (
    "BaselineIdentity",
    "checked_reused_baseline",
    "link_reused_baseline",
    "reusable_baseline",
)
