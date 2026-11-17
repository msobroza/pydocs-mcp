"""``label-alignment``: draw alignment items from stored arms and have both blind labellers label them.

Without ``--confirm-spend`` it prints the plan — the items it would draw by
stratum, the pinned labellers, the prompt size and the estimate — and spends
nothing. ``--dry-run`` rehearses the whole run offline at $0: synthetic labels
from two offline labellers, written to the ``--labels`` file it must be given
(never the vendored one). ``--confirm-spend`` asks the two pinned labellers on
OpenRouter's Batch API. The label file is the batch journal, so a run that
stops waiting on a slow batch (or dies) is resumed by running the command
again: it collects the batch by id instead of buying it twice. The pilot
(``--pass pilot``, the default, ``judge.alignment.pilot_items`` items) ends with
the measured cost per item, the first κ per question and how many items the
sub-budget buys for pass 1 and pass 2; a later pass grows the same file.

Exit status: 0 when every item is labelled by both labellers and every ended
batch was deleted, 1 when one is still running, failed or not deleted, 2 when
the input is refused or OpenRouter refuses a request (every batch already
submitted is in the label file, for the next run).

Usage:
    python -m pydocs_eval.campaign label-alignment --split repoqa-qa/small_dev \\
        --arm RUNS/aa-374/baseline --arm RUNS/aa-374/candidate \\
        [--pass pilot|pass_1|pass_2] [--items N] [--seed N] [--labels FILE] \\
        [--jev-first-pass] [--dry-run | --confirm-spend]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

import httpx

from pydocs_eval.campaign.alignment_label_dry_run import dry_run_clients
from pydocs_eval.campaign.alignment_label_run import (
    AlignmentLabelPlan,
    AlignmentLabelRequest,
    plan_alignment_labelling,
    run_alignment_labelling,
)
from pydocs_eval.campaign.alignment_label_text import render_plan, render_report
from pydocs_eval.campaign.before_after import MeasurementPlanError, parse_split
from pydocs_eval.campaign.before_after_arm import read_arm_summary
from pydocs_eval.campaign.before_after_split import load_split_tasks
from pydocs_eval.campaign.judge_openers import JevOpener, open_openrouter_jev
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.judge.alignment_label_file import (
    AlignmentLabelFileError,
    alignment_label_path,
    write_alignment_labels,
)
from pydocs_eval.judge.alignment_labelling import LabellerClients
from pydocs_eval.judge.alignment_pool import (
    AlignmentPassLockedError,
    refuse_locked_pass,
    swe_qa_questions_unlocked,
)
from pydocs_eval.judge.alignment_vocabulary import AlignmentPass
from pydocs_eval.judge.config import JudgeConfig, JudgeDeployment, load_judge_deployment
from pydocs_eval.judge.jev_requests import JudgedDataset
from pydocs_eval.judge.judge_errors import (
    JudgeConfigError,
    JudgeModelMismatchError,
    JudgeRequestError,
)
from pydocs_eval.judge.openrouter_chat import OpenRouterChatClient
from pydocs_eval.judge.openrouter_http import bearer_from_env
from pydocs_eval.judge.role_config import DEPLOYMENT_YAML
from pydocs_eval.judge.roles import jev_model, labeller_roles

_RESERVED_SPLIT = "example-needle-chat/reserved"
_EXIT_COMPLETE = 0
_EXIT_INCOMPLETE = 1
_EXIT_REFUSED = 2
# What a plan can be refused with: each names the offending input.
_INPUT_ERRORS = (
    MeasurementPlanError,
    JudgeConfigError,
    AlignmentPassLockedError,
    AlignmentLabelFileError,
    ValueError,
    OSError,
)
_SPEND_ERRORS = (JudgeRequestError, JudgeModelMismatchError)

#: Loads a split's tasks — ``load_split_tasks``, or a test's fixture loader.
TaskLoader = Callable[..., Coroutine[Any, Any, tuple[EvalTask, ...]]]
#: Opens the two pinned labellers for the run's duration.
LabellerOpener = Callable[[JudgeConfig], AbstractContextManager[LabellerClients]]


def add_label_alignment_command(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``label-alignment`` on the campaign CLI's subparser."""
    parser = sub.add_parser(
        "label-alignment",
        help="draw alignment items from stored arms and label them blind (a plan unless "
        "--dry-run or --confirm-spend)",
    )
    _add_draw_arguments(parser)
    _add_spend_arguments(parser)
    parser.set_defaults(func=cmd_label_alignment)


def _add_draw_arguments(parser: argparse.ArgumentParser) -> None:
    """What to draw from, for which pass, how many, and where the labels go."""
    parser.add_argument("--split", required=True, help="the arms' split, e.g. repoqa-qa/small_dev")
    parser.add_argument(
        "--arm", type=Path, action="append", required=True, help="an arm directory (arm.json)"
    )
    parser.add_argument(
        "--pass",
        dest="alignment_pass",
        choices=[each.value for each in AlignmentPass],
        default=AlignmentPass.PILOT.value,
        help="pilot (judge.alignment.pilot_items), pass_1 or pass_2 (judge.alignment.n_items)",
    )
    parser.add_argument(
        "--items",
        type=int,
        help="the pass's size (default judge.alignment.pilot_items, or n_items for pass_1/pass_2)",
    )
    parser.add_argument("--seed", type=int, default=0, help="the draw's seed")
    parser.add_argument(
        "--deployment", type=Path, default=Path(DEPLOYMENT_YAML), help="the judge deployment YAML"
    )
    parser.add_argument("--labels", type=Path, help="the label file (default: the vendored one)")


def _add_spend_arguments(parser: argparse.ArgumentParser) -> None:
    """Whether the run spends, rehearses offline, or only prints its plan."""
    parser.add_argument(
        "--jev-first-pass",
        action="store_true",
        help="score the pool with Jev first (cached) to fill the borderline and code-vs-Jev strata",
    )
    parser.add_argument(
        "--resubmit-not-submitted",
        action="store_true",
        help="ask again rows whose batch submit failed (check OpenRouter's batch list first)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="rehearse offline at $0")
    mode.add_argument(
        "--confirm-spend", action="store_true", help="ask the labellers: spends at OpenRouter"
    )


def cmd_label_alignment(
    args: argparse.Namespace,
    *,
    load_tasks: TaskLoader = load_split_tasks,
    open_labellers: LabellerOpener | None = None,
    open_jev: JevOpener | None = None,
    unlocked: Callable[[], bool] = swe_qa_questions_unlocked,
) -> int:
    """Print the plan; on ``--dry-run`` or ``--confirm-spend`` carry it out and print what it found."""
    try:
        deployment = load_judge_deployment(args.deployment)
        request = _request(args, deployment, load_tasks, unlocked)
        plan = _plan(args, request, deployment.judge, load_tasks, open_jev or open_openrouter_jev)
    except _INPUT_ERRORS as exc:
        print(f"label-alignment: {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    print(render_plan(plan, deployment.judge))
    if not (args.dry_run or args.confirm_spend):
        print("Spends nothing without --confirm-spend (--dry-run rehearses offline at $0).")
        return _EXIT_COMPLETE
    return _run_labelling(args, plan, deployment, open_labellers or _openrouter_labellers)


def _request(
    args: argparse.Namespace,
    deployment: JudgeDeployment,
    load_tasks: TaskLoader,
    unlocked: Callable[[], bool],
) -> AlignmentLabelRequest:
    """The run asked for, refused before anything is read when it cannot run."""
    judge = deployment.judge
    selector, _ = parse_split(args.split)
    dataset = JudgedDataset(selector)
    alignment_pass = AlignmentPass(args.alignment_pass)
    refuse_locked_pass(alignment_pass, (dataset,), unlocked=unlocked())
    _refuse_unsafe_modes(args)
    pinned_jev = jev_model(judge)
    labeller_roles(judge)
    if args.confirm_spend:
        for block in judge.alignment.labellers:
            bearer_from_env(block.api_key_env)
    reserved = asyncio.run(load_tasks(_RESERVED_SPLIT))
    return AlignmentLabelRequest(
        split=args.split,
        dataset=dataset,
        alignment_pass=alignment_pass,
        target_items=_pass_size(args.items, alignment_pass, judge),
        labels_path=args.labels or alignment_label_path(judge.alignment, dataset.value, pinned_jev),
        seed=args.seed,
        reserved=frozenset(task.task_id for task in reserved),
        context_lines=deployment.reference_writer.context_lines,
    )


def _refuse_unsafe_modes(args: argparse.Namespace) -> None:
    if args.dry_run and args.labels is None:
        raise ValueError(
            "--dry-run writes synthetic labels: name a scratch --labels file, never the vendored one"
        )
    if args.jev_first_pass and not args.confirm_spend:
        raise ValueError("--jev-first-pass calls Jev on OpenRouter: it needs --confirm-spend")


def _pass_size(items: int | None, alignment_pass: AlignmentPass, judge: JudgeConfig) -> int:
    """How many items the pass labels: ``--items``, else the pass's configured size."""
    if items is not None:
        if items < 1:
            raise ValueError(f"--items {items}, expected at least 1")
        return items
    sizes = {
        AlignmentPass.PILOT: judge.alignment.pilot_items,
        AlignmentPass.PASS_1: judge.alignment.n_items.pass_1,
        AlignmentPass.PASS_2: judge.alignment.n_items.pass_2,
    }
    return sizes[alignment_pass]


def _plan(
    args: argparse.Namespace,
    request: AlignmentLabelRequest,
    judge: JudgeConfig,
    load_tasks: TaskLoader,
    open_jev: JevOpener,
) -> AlignmentLabelPlan:
    tasks = asyncio.run(load_tasks(args.split))
    arms = [read_arm_summary(arm) for arm in args.arm]
    if not args.jev_first_pass:
        return plan_alignment_labelling(tasks, arms, request=request, judge=judge)
    with open_jev(judge) as jev_judge:
        return plan_alignment_labelling(
            tasks, arms, request=request, judge=judge, jev_judge=jev_judge
        )


def _run_labelling(
    args: argparse.Namespace,
    plan: AlignmentLabelPlan,
    deployment: JudgeDeployment,
    open_labellers: LabellerOpener,
) -> int:
    """Label ``plan``'s rows, offline or on OpenRouter, and print what the run found."""
    judge, writer = deployment.judge, deployment.reference_writer.model
    # The draw is recorded before any spend; the labellers see the rows as written.
    write_alignment_labels(plan.request.labels_path, plan.rows)
    opener = _dry_run_opener(plan) if args.dry_run else open_labellers
    try:
        with opener(judge) as opened:
            report = run_alignment_labelling(
                plan,
                opened,
                judge=judge,
                writer_model=writer,
                resubmit_not_submitted=args.resubmit_not_submitted,
            )
    except _SPEND_ERRORS as exc:
        print(
            f"label-alignment: {exc}; every batch already submitted is recorded in "
            f"{plan.request.labels_path} and is collected by the next run",
            file=sys.stderr,
        )
        return _EXIT_REFUSED
    print(render_report(report, judge, dry_run=args.dry_run))
    print(f"label file: {plan.request.labels_path}")
    return _EXIT_COMPLETE if report.complete else _EXIT_INCOMPLETE


def _dry_run_opener(plan: AlignmentLabelPlan) -> LabellerOpener:
    """Offline labellers scripted for ``plan``'s rows, opened like the paid ones."""

    @contextmanager
    def open_offline(_judge: JudgeConfig) -> Iterator[LabellerClients]:
        yield dry_run_clients(plan.rows)

    return open_offline


@contextmanager
def _openrouter_labellers(judge: JudgeConfig) -> Iterator[LabellerClients]:
    """The two pinned labellers on OpenRouter, over one HTTP client."""
    astra, opus = labeller_roles(judge)
    with httpx.Client() as http:
        yield LabellerClients(
            astra=OpenRouterChatClient(astra, http), opus=OpenRouterChatClient(opus, http)
        )


__all__ = ("add_label_alignment_command", "cmd_label_alignment")
