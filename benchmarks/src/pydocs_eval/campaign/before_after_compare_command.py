"""``before-after-compare``: the acceptance verdict over finished arms (#372).

Reads one baseline arm, its A/A replicate (a second baseline arm on the same
commit and settings, which sets the correctness band) and up to three variant
arms. Campaign arms and chat runner outputs read alike, since both write
``arm.json`` beside ``arm_settings.json``. Every stored answer is scored against
the split's gold, and the comparison is printed. It spends nothing: no arm runs.

``--guidance-task-clauses`` (the guidance-task experiment, #391, only) prints
that experiment's flip clauses under each verdict and the rows it registered in
advance (``before_after_flip_clauses``); it changes no verdict and no exit status.

Exit status: 0 when every variant passes, 1 when any fails, 2 on an input error
or when a variant gets no verdict (its arms, or the A/A replicate, stored no
answers).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pydocs_eval.campaign.before_after import CommitUnderTest, MeasurementPlanError
from pydocs_eval.campaign.before_after_acceptance import VariantVerdict
from pydocs_eval.campaign.before_after_answers import AnswerKey, answer_key_for
from pydocs_eval.campaign.before_after_arm import (
    ARM_SETTINGS_FILENAME,
    ARM_SUMMARY_FILENAME,
    ArmSummary,
    read_arm_settings,
    read_arm_summary,
)
from pydocs_eval.campaign.before_after_compare import (
    MAX_VARIANTS,
    ArmIdentity,
    Comparison,
    ComparisonInputError,
    LabelledArm,
    check_mode_combination,
    check_variant_count,
    compare_arms,
)
from pydocs_eval.campaign.before_after_compare_text import render_comparison
from pydocs_eval.campaign.before_after_flip_clauses import RERUN_SLICE, FlipClauseMode
from pydocs_eval.campaign.before_after_fresh_draw import FreshTestDraw
from pydocs_eval.campaign.before_after_measure import measure_arm
from pydocs_eval.campaign.before_after_recorded_draw import draw_the_arms_recorded
from pydocs_eval.campaign.before_after_split import load_split_tasks
from pydocs_eval.campaign.budget import HaltReason
from pydocs_eval.judge.config import load_judge_config

# Exit codes: 2 is the operator-error code the eval CLIs already use; a variant
# with no verdict shares it, since the fix is an input (arms with answers).
_EXIT_ALL_PASSED = 0
_EXIT_SOME_FAILED = 1
_EXIT_INPUT_ERROR = 2


def add_before_after_compare_command(sub: argparse._SubParsersAction) -> None:
    """Register ``before-after-compare`` on the campaign CLI's subparser.

    Example:
        >>> parser = argparse.ArgumentParser()
        >>> add_before_after_compare_command(parser.add_subparsers())
        >>> parser.parse_args(["before-after-compare", "--baseline", "b", "--aa-replicate", "aa",
        ...                    "--variant", "v", "--split", "repoqa-qa/dev"]).completeness_arm
        False
    """
    parser = sub.add_parser(
        "before-after-compare",
        help=f"decide up to {MAX_VARIANTS} variant arms against one baseline (spends nothing)",
    )
    _add_arm_arguments(parser)
    parser.add_argument(
        "--split", required=True, help="<dataset>/<split> the arms answered; its gold scores them"
    )
    parser.add_argument(
        "--completeness-arm",
        action="store_true",
        help="try the bounded step-8 rule (Q44) first; adopting on it needs the owner's sign-off",
    )
    _add_flip_clause_arguments(parser)
    parser.set_defaults(func=cmd_before_after_compare)


def _add_flip_clause_arguments(parser: argparse.ArgumentParser) -> None:
    """The guidance-task experiment's opt-in: its flip clauses and the noise edge they read."""
    parser.add_argument(
        "--guidance-task-clauses",
        action="store_true",
        help="for the guidance-task experiment (#391) only: after each standard verdict, print "
        "one finding per flip clause ((b) unanswered outcomes, (c) minimum gain, (a) tokens) "
        "and the pre-registered rows; the verdict and the exit status are unchanged",
    )
    parser.add_argument(
        "--noise-edge",
        type=float,
        default=None,
        help="with --guidance-task-clauses: the slice's noise edge in penalised turns that "
        "clause (c) reads (0.6 for the 20-task slice); omit it when reading the re-run on "
        f"{RERUN_SLICE}, which is read without the clause",
    )


def _flip_clause_mode(args: argparse.Namespace) -> FlipClauseMode | None:
    """The guidance-task mode the flags ask for; a refusal for an edge without the mode."""
    if not args.guidance_task_clauses:
        if args.noise_edge is not None:
            raise ComparisonInputError(
                f"--noise-edge needs --guidance-task-clauses: got --noise-edge {args.noise_edge} "
                "alone, expected both or neither"
            )
        return None
    try:
        mode = FlipClauseMode(noise_edge=args.noise_edge)
    except ValueError as exc:
        raise ComparisonInputError(str(exc)) from exc
    check_mode_combination(completeness_arm=args.completeness_arm, flip_clauses=mode)
    return mode


def _add_arm_arguments(parser: argparse.ArgumentParser) -> None:
    """The arm directories: the baseline, its A/A replicate, and the variants."""
    parser.add_argument("--baseline", type=Path, required=True, help="the baseline arm directory")
    parser.add_argument(
        "--aa-replicate",
        type=Path,
        required=True,
        help="a second baseline arm on the same commit and settings: the A/A pair the "
        "correctness band is computed from",
    )
    parser.add_argument(
        "--variant",
        type=Path,
        action="append",
        required=True,
        help=f"a variant arm directory; repeat for up to {MAX_VARIANTS}",
    )


def cmd_before_after_compare(args: argparse.Namespace) -> int:
    """Print the comparison; the exit status is its verdict.

    Example:
        >>> main(["before-after-compare", "--baseline", "runs/b", "--aa-replicate", "runs/aa",
        ...       "--variant", "runs/v", "--split", "repoqa-qa/small_test"])  # doctest: +SKIP
        0
    """
    try:
        comparison, draw = _comparison_from_args(args)
    except (MeasurementPlanError, ComparisonInputError) as exc:
        # MeasurementPlanError: the split loader refused the --split spec, or
        # the arms answered different fresh draws.
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_INPUT_ERROR
    if draw is not None:
        print("\n".join(draw.plan_lines()))
    print(render_comparison(comparison, split=args.split))
    return _exit_status(comparison)


def _comparison_from_args(args: argparse.Namespace) -> tuple[Comparison, FreshTestDraw | None]:
    """The comparison, and the fresh draw its arms answered (None for none).

    The gold is loaded through the draw the arms RECORDED, never re-drawn from
    flags: a fresh-draw run's answers belong to that redraw's tasks.
    """
    # Refused before any arm is read, so a mistyped command costs no trace reading.
    check_variant_count(len(args.variant))
    flip_clauses = _flip_clause_mode(args)
    draw = draw_the_arms_recorded([args.baseline, args.aa_replicate, *args.variant])
    tasks = asyncio.run(load_split_tasks(args.split, draw=draw))
    answer_key = answer_key_for(tasks, load_judge_config().jev)
    comparison = compare_arms(
        _read_arm(args.baseline, answer_key),
        [_read_arm(arm_dir, answer_key) for arm_dir in args.variant],
        replicate=_read_arm(args.aa_replicate, answer_key),
        completeness_arm=args.completeness_arm,
        flip_clauses=flip_clauses,
    )
    return comparison, draw


def _read_arm(arm_dir: Path, answer_key: AnswerKey) -> LabelledArm:
    """One finished arm, measured off its traces; a refusal naming a directory that has none."""
    try:
        summary, settings = read_arm_summary(arm_dir), read_arm_settings(arm_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ComparisonInputError(
            f"{arm_dir} holds no readable arm ({exc}); expected the {ARM_SUMMARY_FILENAME} and "
            f"{ARM_SETTINGS_FILENAME} a campaign arm or the chat runner writes"
        ) from exc
    _refuse_an_unfinished_arm(arm_dir, summary)
    # The verdict reads answers and trajectories, never a description surface or
    # a commit subject, so neither is looked up.
    commit = CommitUnderTest(
        role=summary.role, sha=summary.commit, subject="", description_tokens=0
    )
    # The settings file always records the arm's cap; an arm.json that predates
    # its own cap field reads its outcomes against this one, as --report-only does.
    metrics = measure_arm(
        summary,
        commit,
        workspace=Path(settings.workspace),
        max_agent_turns=settings.max_agent_turns,
        answer_key=answer_key,
    )
    identity = ArmIdentity(
        commit=summary.commit,
        model=summary.model,
        max_agent_turns=summary.max_agent_turns or settings.max_agent_turns,
        settings_digest=summary.settings_digest,
    )
    return LabelledArm(label=str(arm_dir), metrics=metrics, identity=identity)


def _refuse_an_unfinished_arm(arm_dir: Path, summary: ArmSummary) -> None:
    """An arm killed or still running has checkpointed k of its N rows (issue #385).

    The verdict pairs over the tasks both arms hold, so k rows would read as a
    finished arm. A guard-halted arm is a separate, older question, left as it was.
    """
    if summary.halt_reason != HaltReason.IN_PROGRESS:
        return
    raise ComparisonInputError(
        f"{arm_dir} is not finished (halt_reason {HaltReason.IN_PROGRESS.value}, "
        f"{len(summary.tasks)} task(s) checkpointed); expected a finished arm, halt_reason "
        f"{HaltReason.COMPLETED.value}: resume it with before-after --confirm-spend"
    )


def _exit_status(comparison: Comparison) -> int:
    verdicts = {variant.decision.verdict for variant in comparison.variants}
    if VariantVerdict.NO_VERDICT in verdicts:
        return _EXIT_INPUT_ERROR
    return _EXIT_SOME_FAILED if VariantVerdict.FAILED in verdicts else _EXIT_ALL_PASSED


__all__ = ("add_before_after_compare_command", "cmd_before_after_compare")
