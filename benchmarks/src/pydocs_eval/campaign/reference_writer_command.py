"""``write-references``: reference answers for repoqa-qa tasks, from ground truth (judge 9c).

Without ``--confirm-spend`` it prints the plan — the tasks still without a
reference, the pinned writer and fallback, the batches an earlier run left open
— and spends nothing. With it, the batches are submitted at OpenRouter. Every
batch id is journaled at submit, so a run that stops waiting on a slow batch
(or dies) is resumed by running the command again: it collects the batch by id
instead of buying it twice. Each kept row carries its model id and prompt hash;
a task still without a reference is listed, never kept silently.

It writes repoqa-qa only: the ladder measures on repoqa-qa (the owner's
2026-09-28 decision on the turn-efficiency umbrella), and the chat records'
references wait until a step is set to run on chat.

Exit status: 0 when every task has a reference and every ended batch was
deleted, 1 when a task was listed or a batch could not be deleted, 2 when the
input is refused or OpenRouter refuses a request (the journal keeps every batch
already submitted, for the next run).

Usage:
    python -m pydocs_eval.campaign write-references --split repoqa-qa/small_dev \\
        [--limit N] [--confirm-spend]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import httpx

from pydocs_eval._bench_cache import cache_root
from pydocs_eval.campaign.before_after import MeasurementPlanError, parse_split
from pydocs_eval.campaign.before_after_split import load_split_tasks
from pydocs_eval.campaign.reference_writer_run import (
    ReferenceRunReport,
    ReferenceWriterPlan,
    plan_reference_writing,
    run_reference_writing,
)
from pydocs_eval.campaign.reference_writer_text import render_plan, render_report
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.reference_answers import (
    ReferenceRowsError,
    vendored_repoqa_reference_path,
)
from pydocs_eval.judge.config import load_judge_config, load_judge_deployment
from pydocs_eval.judge.judge_errors import (
    JudgeConfigError,
    JudgeModelMismatchError,
    JudgeRequestError,
)
from pydocs_eval.judge.openrouter_chat import OpenRouterChatClient
from pydocs_eval.judge.openrouter_http import bearer_from_env
from pydocs_eval.judge.reference_journal import ReferenceJournal, ReferenceJournalError
from pydocs_eval.judge.reference_writer import WriterClients
from pydocs_eval.judge.role_config import DEPLOYMENT_YAML, ReferenceWriterConfig
from pydocs_eval.judge.roles import reference_writer_fallback_role, reference_writer_role

_DATASET = "repoqa-qa"
_JOURNAL_DIR = "reference_writer"
_EXIT_COMPLETE = 0
_EXIT_INCOMPLETE = 1
_EXIT_REFUSED = 2
# What a plan can be refused with: each names the offending input.
_INPUT_ERRORS = (
    MeasurementPlanError,
    JudgeConfigError,
    ReferenceRowsError,
    ReferenceJournalError,
    ValueError,
    OSError,
)

#: Loads a split's tasks — ``load_split_tasks``, or a test's fixture loader.
TaskLoader = Callable[..., Coroutine[Any, Any, tuple[EvalTask, ...]]]


def add_write_references_command(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``write-references`` on the campaign CLI's subparser."""
    parser = sub.add_parser(
        "write-references",
        help="write repoqa-qa reference answers from ground truth (a plan unless --confirm-spend)",
    )
    parser.add_argument(
        "--split", required=True, help="repoqa-qa/<slice>, e.g. repoqa-qa/small_dev"
    )
    parser.add_argument(
        "--deployment",
        type=Path,
        default=Path(DEPLOYMENT_YAML),
        help="the judge deployment YAML pinning reference_writer.model and its fallback",
    )
    parser.add_argument("--out", type=Path, help="the rows file (default: the vendored one)")
    parser.add_argument("--journal", type=Path, help="the batch journal (default: bench cache)")
    parser.add_argument("--limit", type=int, help="only the slice's first N tasks (a pilot)")
    parser.add_argument(
        "--confirm-spend", action="store_true", help="submit the batches: spends at OpenRouter"
    )
    parser.set_defaults(func=cmd_write_references)


def cmd_write_references(
    args: argparse.Namespace, *, load_tasks: TaskLoader = load_split_tasks
) -> int:
    """Print the plan; on ``--confirm-spend`` carry it out and print what it did.

    Example:
        >>> cmd_write_references(args)  # doctest: +SKIP
        0
    """
    try:
        writer = _pinned_writer(args)
        plan = _plan_from_arguments(args, writer, load_tasks)
        if args.confirm_spend:
            bearer_from_env(writer.api_key_env)
    except _INPUT_ERRORS as exc:
        print(f"write-references: {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    print(render_plan(args.split, plan, writer))
    if not args.confirm_spend:
        print("Spends nothing without --confirm-spend.")
        return _EXIT_COMPLETE
    return _spend(plan, writer)


def _spend(plan: ReferenceWriterPlan, writer: ReferenceWriterConfig) -> int:
    """Carry ``plan`` out and print what it did; a refused request stops it, journal intact."""
    try:
        report = _write_with_openrouter(plan, writer)
    except (JudgeRequestError, JudgeModelMismatchError) as exc:
        print(
            f"write-references: {exc}; every batch already submitted is in "
            f"{plan.journal.path} and is collected by the next run",
            file=sys.stderr,
        )
        return _EXIT_REFUSED
    print(render_report(report))
    incomplete = report.result.gaps or report.not_deleted
    return _EXIT_INCOMPLETE if incomplete else _EXIT_COMPLETE


def _pinned_writer(args: argparse.Namespace) -> ReferenceWriterConfig:
    """The deployment's writer block, refused unless the split is repoqa-qa and both are pinned."""
    selector, _ = parse_split(args.split)
    if selector != _DATASET:
        raise MeasurementPlanError(
            f"--split {args.split!r} names {selector!r}; write-references writes {_DATASET} "
            "references only (the ladder measures on repoqa-qa)"
        )
    writer = load_judge_deployment(args.deployment).reference_writer
    reference_writer_role(writer)
    reference_writer_fallback_role(writer)
    return writer


def _plan_from_arguments(
    args: argparse.Namespace, writer: ReferenceWriterConfig, load_tasks: TaskLoader
) -> ReferenceWriterPlan:
    out = args.out or vendored_repoqa_reference_path()
    journal = args.journal or cache_root() / _JOURNAL_DIR / f"{out.stem}.journal.jsonl"
    tasks = asyncio.run(load_tasks(args.split, limit=args.limit))
    return plan_reference_writing(
        tasks, out=out, journal=ReferenceJournal(journal), context_lines=writer.context_lines
    )


def _write_with_openrouter(
    plan: ReferenceWriterPlan, writer: ReferenceWriterConfig
) -> ReferenceRunReport:
    extensions = load_judge_config().jev.citation_extensions
    with httpx.Client() as http:
        clients = WriterClients(
            primary=OpenRouterChatClient(reference_writer_role(writer), http),
            fallback=OpenRouterChatClient(reference_writer_fallback_role(writer), http),
        )
        return run_reference_writing(plan, clients, retries=writer.retries, extensions=extensions)


__all__ = ("add_write_references_command", "cmd_write_references")
