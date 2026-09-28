"""``before-after-compare``: the acceptance verdict over finished arms (#372).

Reads one baseline arm, up to three variant arms and, optionally, the
baseline's A/A replicate. Campaign arms and chat runner outputs read alike,
since both write ``arm.json`` beside ``arm_settings.json``. Every stored answer
is scored against the split's gold, and the comparison is printed. It spends
nothing: no arm runs.

Exit status: 0 when every variant passes, 1 when any fails, and 2 on an input
error or when a variant gets no verdict (its arms stored no answers).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pydocs_eval.campaign.before_after import CommitUnderTest, MeasurementPlanError
from pydocs_eval.campaign.before_after_acceptance import Verdict
from pydocs_eval.campaign.before_after_answers import AnswerKey, answer_key_for
from pydocs_eval.campaign.before_after_arm import (
    ARM_SETTINGS_FILENAME,
    ARM_SUMMARY_FILENAME,
    read_arm_settings,
    read_arm_summary,
)
from pydocs_eval.campaign.before_after_compare import (
    MAX_VARIANTS,
    Comparison,
    LabelledArm,
    check_variant_count,
    compare_arms,
)
from pydocs_eval.campaign.before_after_compare_text import render_comparison
from pydocs_eval.campaign.before_after_measure import measure_arm
from pydocs_eval.campaign.before_after_split import load_split_tasks
from pydocs_eval.judge.config import load_judge_config

_EXIT_ALL_PASSED = 0
_EXIT_SOME_FAILED = 1
_EXIT_INPUT_ERROR = 2


def add_before_after_compare_command(sub: argparse._SubParsersAction) -> None:
    """Register ``before-after-compare`` on the campaign CLI's subparser."""
    parser = sub.add_parser(
        "before-after-compare",
        help="decide up to three variant arms against one baseline (spends nothing)",
    )
    parser.add_argument("--baseline", type=Path, required=True, help="the baseline arm directory")
    parser.add_argument(
        "--variant",
        type=Path,
        action="append",
        required=True,
        help=f"a variant arm directory; repeat for up to {MAX_VARIANTS}",
    )
    parser.add_argument(
        "--aa-replicate",
        type=Path,
        default=None,
        help="a second baseline arm on the same commit and settings: the A/A pair the band "
        "is computed from (without it, the band is one task)",
    )
    parser.add_argument(
        "--split", required=True, help="<dataset>/<split> the arms answered; its gold scores them"
    )
    parser.add_argument(
        "--completeness-arm",
        action="store_true",
        help="try the bounded step-8 rule (Q44) first; adopting on it needs the owner's sign-off",
    )
    parser.set_defaults(func=cmd_before_after_compare)


def cmd_before_after_compare(args: argparse.Namespace) -> int:
    """Print the comparison; the exit status is its verdict."""
    try:
        comparison = _comparison_from_args(args)
    except MeasurementPlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_INPUT_ERROR
    print(render_comparison(comparison, split=args.split))
    return _exit_status(comparison)


def _comparison_from_args(args: argparse.Namespace) -> Comparison:
    check_variant_count(len(args.variant))
    tasks = asyncio.run(load_split_tasks(args.split))
    answer_key = answer_key_for(tasks, load_judge_config().jev)
    replicate = None if args.aa_replicate is None else _read_arm(args.aa_replicate, answer_key)
    return compare_arms(
        _read_arm(args.baseline, answer_key),
        [_read_arm(arm_dir, answer_key) for arm_dir in args.variant],
        replicate=replicate,
        completeness_arm=args.completeness_arm,
    )


def _read_arm(arm_dir: Path, answer_key: AnswerKey) -> LabelledArm:
    """One finished arm, measured off its traces; a refusal naming a directory that has none."""
    try:
        summary, settings = read_arm_summary(arm_dir), read_arm_settings(arm_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MeasurementPlanError(
            f"{arm_dir} holds no readable arm ({exc}); expected the {ARM_SUMMARY_FILENAME} and "
            f"{ARM_SETTINGS_FILENAME} a campaign arm or the chat runner writes"
        ) from exc
    commit = CommitUnderTest(
        role=summary.role, sha=summary.commit, subject=str(arm_dir), description_tokens=0
    )
    metrics = measure_arm(
        summary, commit, workspace=Path(settings.workspace), answer_key=answer_key
    )
    return LabelledArm(label=str(arm_dir), metrics=metrics)


def _exit_status(comparison: Comparison) -> int:
    verdicts = {variant.decision.verdict for variant in comparison.variants}
    if Verdict.NO_VERDICT in verdicts:
        return _EXIT_INPUT_ERROR
    return _EXIT_SOME_FAILED if Verdict.FAILED in verdicts else _EXIT_ALL_PASSED


__all__ = ("add_before_after_compare_command", "cmd_before_after_compare")
