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

It must also have been SERVED like that baseline (owner follow-up, 2026-11-08):
its ``serving.json`` fingerprint (``before_after_serving``) — the serving
YAML's content, the endpoint, the index it served and the llm block, key by key
— must equal the one this plan's baseline would record. Each differing key is
named. An arm that recorded no fingerprint (written before it existed) is
refused the same way, as "not recorded". ``--allow-serving-mismatch`` forces a
serving mismatch through — never a commit, model, turn-cap, digest, halt or
task-set one — and a forced reuse is never silent: one structured log line, the
plan text and the candidate's ``arm.json`` (hence every report) name its keys.

An arm the vendored chat runner wrote (``benchmarks/tools/run_example_needle_chat_repro.py``)
is read like any other: it records the same four fields and the same fingerprint.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, fields
from pathlib import Path

from pydocs_eval.campaign.before_after import ArmRole, MeasurementPlanError, resolve_commit
from pydocs_eval.campaign.before_after_arm_records import (
    ALLOW_SERVING_MISMATCH_FLAG,
    ARM_SUMMARY_FILENAME,
    ArmSummary,
    ServingMismatchOverride,
    read_arm_summary,
)
from pydocs_eval.campaign.before_after_arm_variant import ArmVariant
from pydocs_eval.campaign.before_after_serving import (
    SERVING_FINGERPRINT_FILENAME,
    ServingBasis,
    ServingFingerprint,
    read_serving_fingerprint,
)
from pydocs_eval.campaign.budget import HaltReason

log = logging.getLogger("pydocs-eval.campaign.before-after-reuse")

# The structured event a forced reuse emits, once per plan: grep a run.log for it.
SERVING_MISMATCH_FORCED_EVENT = "before_after_serving_mismatch_forced"
_PLAN_INDENT = " " * 12


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


@dataclass(frozen=True, slots=True)
class ServingReuse:
    """How a reused arm's serving fingerprint compared with the plan's baseline.

    ``forced`` is set only when ``--allow-serving-mismatch`` forced a mismatch
    (otherwise the reuse was refused); it is exactly what the candidate's
    ``arm.json`` then records, so the plan and every report say the same thing.
    """

    arm_dir: str
    compared_keys: tuple[str, ...]
    override_given: bool
    forced: ServingMismatchOverride | None = None

    @property
    def differing_keys(self) -> tuple[str, ...]:
        return () if self.forced is None else tuple(self.forced.differing_keys)

    @property
    def fingerprint_recorded(self) -> bool:
        return self.forced is None or self.forced.fingerprint_recorded

    def override_record(self) -> ServingMismatchOverride | None:
        """What the candidate's ``arm.json`` records; None when nothing was forced."""
        return self.forced

    def plan_lines(self) -> list[str]:
        """The plan's serving line under ``reused from``: a match, or what was forced."""
        return [f"{_PLAN_INDENT}serving:    {self._verdict_text()}"]

    def _verdict_text(self) -> str:
        flag, count = ALLOW_SERVING_MISMATCH_FLAG, len(self.differing_keys)
        keys = ", ".join(self.differing_keys)
        if not count:
            spare = f"; {flag} had nothing to force" if self.override_given else ""
            return f"matches the reused baseline on all {len(self.compared_keys)} key(s){spare}"
        if not self.fingerprint_recorded:
            forced = f"reuse forced by {flag}: {count} key(s) unverified: {keys}"
            return f"NOT RECORDED by the reused arm, {forced}"
        return f"MISMATCH on {count} key(s), reuse forced by {flag}: {keys}"


@dataclass(frozen=True, slots=True)
class ServingPlan:
    """The plan's serving side: what each arm records, and how a reuse compared."""

    basis: ServingBasis
    reuse: ServingReuse | None = None


def checked_reused_baseline(
    args: argparse.Namespace,
    baseline: ArmVariant,
    task_ids: Sequence[str],
    serving: ServingBasis,
) -> ServingReuse | None:
    """``--reuse-baseline``, checked against the baseline this plan would run; None without it.

    Resolves the baseline commit itself, so the check runs before ``build_plan``
    probes any arm — a refused reuse costs no worktree.

    Raises:
        MeasurementPlanError: the reuse is refused, or ``--allow-serving-mismatch``
            was given without ``--reuse-baseline`` (it would force nothing).
    """
    if args.reuse_baseline is None:
        _refuse_an_override_without_reuse(args)
        return None
    sha, _subject = resolve_commit(args.repo, ArmRole.BASELINE, args.baseline)
    expected = BaselineIdentity(
        commit=sha,
        model=args.model,
        max_agent_turns=baseline.max_agent_turns,
        settings_digest=baseline.digest,
    )
    reuse = reusable_baseline(
        args.reuse_baseline,
        expected,
        task_ids=task_ids,
        serving=serving.fingerprint(baseline.llm_block),
        allow_serving_mismatch=args.allow_serving_mismatch,
    )
    _log_forced_reuse(reuse, args)
    return reuse


def _refuse_an_override_without_reuse(args: argparse.Namespace) -> None:
    if not args.allow_serving_mismatch:
        return
    raise MeasurementPlanError(
        f"{ALLOW_SERVING_MISMATCH_FLAG} was given without --reuse-baseline; it forces "
        "the reuse of a baseline served differently, so it needs --reuse-baseline <arm dir>"
    )


def reusable_baseline(
    arm_dir: Path,
    expected: BaselineIdentity,
    *,
    task_ids: Sequence[str],
    serving: ServingFingerprint,
    allow_serving_mismatch: bool = False,
) -> ServingReuse:
    """Whether the finished arm in ``arm_dir`` ran — and was served — as ``expected``.

    Raises:
        MeasurementPlanError: no readable ``arm.json`` there; or any of the four
            fields differs, the arm did not finish, it answered other tasks, or
            (without ``allow_serving_mismatch``) a serving key differs or was
            never recorded — every problem is named with both values, at once.
    """
    summary = _recorded_summary(arm_dir)
    recorded = _recorded_fingerprint(arm_dir)
    serving_refusals = _serving_mismatches(arm_dir, recorded, serving)
    mismatches = [
        *_identity_mismatches(BaselineIdentity.of_summary(summary), expected),
        *_unfinished(summary),
        *_task_set_mismatches(summary, task_ids),
        *([] if allow_serving_mismatch else serving_refusals),
    ]
    if mismatches:
        forcible = bool(serving_refusals) and not allow_serving_mismatch
        raise MeasurementPlanError(_refusal(arm_dir, mismatches, forcible=forcible))
    return _serving_reuse(arm_dir, recorded, serving, override_given=allow_serving_mismatch)


def _refusal(arm_dir: Path, mismatches: Sequence[str], *, forcible: bool) -> str:
    """The refusal; it suggests the override only while a serving key is what it forces."""
    force = (
        f", or pass {ALLOW_SERVING_MISMATCH_FLAG} to force the serving key(s) "
        "(it forces nothing else)"
        if forcible
        else ""
    )
    return (
        f"--reuse-baseline {arm_dir} ran something other than this plan's baseline "
        f"({'; '.join(mismatches)}); reuse an arm that matches, or drop the flag "
        f"to run the baseline{force}"
    )


def _recorded_fingerprint(arm_dir: Path) -> ServingFingerprint | None:
    try:
        return read_serving_fingerprint(arm_dir)
    except (OSError, ValueError) as exc:
        raise MeasurementPlanError(
            f"--reuse-baseline {arm_dir}: its {SERVING_FINGERPRINT_FILENAME} is unreadable "
            f"({exc}); expected a mapping of serving key -> value under 'fingerprint'"
        ) from exc


def _serving_mismatches(
    arm_dir: Path, recorded: ServingFingerprint | None, expected: ServingFingerprint
) -> list[str]:
    """Each differing serving key; for an arm that recorded none, one line naming them all."""
    if recorded is not None:
        return recorded.difference_lines(expected)
    return [
        f"serving fingerprint: not recorded — {arm_dir} holds no {SERVING_FINGERPRINT_FILENAME} "
        "(an arm written before serving fingerprints), so none of the plan's baseline "
        f"serving keys can be checked: {', '.join(sorted(expected.values))}"
    ]


def _serving_reuse(
    arm_dir: Path,
    recorded: ServingFingerprint | None,
    expected: ServingFingerprint,
    *,
    override_given: bool,
) -> ServingReuse:
    compared = tuple(sorted(expected.values))
    differing = compared if recorded is None else recorded.differing_keys(expected)
    forced = ServingMismatchOverride(
        reused_baseline=str(arm_dir),
        differing_keys=list(differing),
        fingerprint_recorded=recorded is not None,
    )
    return ServingReuse(
        arm_dir=str(arm_dir),
        compared_keys=compared,
        override_given=override_given,
        forced=forced if differing else None,
    )


def _log_forced_reuse(reuse: ServingReuse, args: argparse.Namespace) -> None:
    """One structured line per plan that forces a reuse, naming its keys (never values).

    It also says whether this invocation spends (``confirm_spend``) or re-renders
    (``report_only``): a plan print forces nothing yet, and an audit that greps
    the event must be able to tell the dry runs from the paid one.
    """
    if reuse.forced is None:
        return
    log.warning(
        json.dumps(
            {
                "event": SERVING_MISMATCH_FORCED_EVENT,
                "flag": ALLOW_SERVING_MISMATCH_FLAG,
                "reused_baseline": reuse.arm_dir,
                "fingerprint_recorded": reuse.fingerprint_recorded,
                "differing_keys": list(reuse.differing_keys),
                "confirm_spend": bool(args.confirm_spend),
                "report_only": bool(args.report_only),
            }
        )
    )


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
    "SERVING_MISMATCH_FORCED_EVENT",
    "BaselineIdentity",
    "ServingPlan",
    "ServingReuse",
    "checked_reused_baseline",
    "link_reused_baseline",
    "reusable_baseline",
)
