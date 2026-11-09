"""The ``before-after`` command: plan by default, run both arms on ``--confirm-spend``.

Two subcommands, one pair:

- ``before-after`` is what an operator types. Without ``--confirm-spend`` it
  prints the plan and stops, having spent nothing. With it, it checks each
  commit out into a git worktree, runs the arm in a CHILD process whose
  ``PYTHONPATH`` puts that worktree's ``python/`` first, and writes the report.
- ``before-after-arm`` is that child. It is not meant to be typed by hand, and
  it refuses to run when the product it imported is not the commit it was asked
  to measure — a shadowing install would otherwise measure the same code twice
  and report a difference of zero as a finding.

``--report-only`` is the third door on the first subcommand: it re-renders the
report from the arm summaries a finished run left under ``--out``, running no
arm at all. The report stage is the LAST thing a paid run does, so a crash there
would otherwise cost the whole run again at the endpoint.

The worktree, the child's path and the "is this really that commit" check are
``before_after_product``'s, because the plan-time block probe
(``before_after_block_probe``) has to ask its question under the very same
environment an arm will run in. Only the PRODUCT comes from the worktree: the
eval suite — the harness bridge, the split loader, the metric layer — always
comes from the checkout the command was launched from, so one implementation of
every metric scores both arms.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from pydocs_eval.campaign.before_after import (
    ArmLlmBlock,
    ArmRole,
    CostModel,
    MeasurementPlan,
    MeasurementPlanError,
    TokenCounter,
    build_plan,
)
from pydocs_eval.campaign.before_after_answers import AnswerKey, answer_key_for
from pydocs_eval.campaign.before_after_arm import (
    ARM_SETTINGS_FILENAME,
    ARM_SUMMARY_FILENAME,
    ArmSettings,
    ArmSummary,
    read_arm_summary,
    run_arm,
)
from pydocs_eval.campaign.before_after_arm_launch import (
    add_variant_arm_arguments,
    overlay_flags_given,
    refuse_a_foreign_resume,
    refuse_a_serving_change_on_resume,
    run_one_arm,
    variants_from_args,
)
from pydocs_eval.campaign.before_after_arm_variant import ArmVariants
from pydocs_eval.campaign.before_after_block_probe import probe_arm_block
from pydocs_eval.campaign.before_after_corpora import (
    DEFAULT_USD_PER_1M_EMBED,
    CorpusWorkspaceError,
    TaskWorkspaces,
    build_missing_workspaces,
    plan_task_workspaces,
)
from pydocs_eval.campaign.before_after_fresh_draw import (
    FreshTestDraw,
    add_fresh_draw_arguments,
    fresh_draw_from_args,
)
from pydocs_eval.campaign.before_after_llm_block import (
    LLM_BLOCK_SHAPE,
    load_arm_llm_block,
    refuse_file_sourced_model_settings,
)
from pydocs_eval.campaign.before_after_measure import measure_both_arms
from pydocs_eval.campaign.before_after_plan_text import render_plan
from pydocs_eval.campaign.before_after_product import assert_product_under
from pydocs_eval.campaign.before_after_recorded_draw import (
    draw_the_arms_recorded,
    reconciled_with_flags,
)
from pydocs_eval.campaign.before_after_recorded_variants import (
    accepted_because_it_already_ran,
    recorded_variants,
)
from pydocs_eval.campaign.before_after_report import render_report
from pydocs_eval.campaign.before_after_reuse import (
    ServingPlan,
    checked_reused_baseline,
    link_reused_baseline,
)
from pydocs_eval.campaign.before_after_serving import ServingSettings, serving_settings_from_args
from pydocs_eval.campaign.before_after_split import load_split_tasks, task_ids_of
from pydocs_eval.campaign.budget import HaltReason
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.judge.config import load_judge_config

_PLAN_FILENAME = "plan.txt"
_REPORT_FILENAME = "before_after.md"

# Exit codes: 2 is the operator-error code the eval CLIs already use.
_EXIT_OK = 0
_EXIT_INPUT_ERROR = 2


def add_before_after_commands(sub: argparse._SubParsersAction) -> None:
    """Register both subcommands on the campaign CLI's subparser."""
    _add_before_after(sub)
    _add_before_after_arm(sub)


def _add_before_after(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "before-after", help="measure one split under two product commits (plan by default)"
    )
    parser.add_argument("--baseline", required=True, help="git ref of the commit to measure first")
    parser.add_argument("--candidate", required=True, help="git ref of the commit under test")
    parser.add_argument(
        "--config", type=Path, required=True, help="ask-your-docs serving YAML both arms use"
    )
    parser.add_argument(
        "--llm-block",
        type=Path,
        default=None,
        help="YAML/JSON file holding the ask_your_docs.llm block BOTH arms send "
        f"({LLM_BLOCK_SHAPE}); the model comes from --model",
    )
    parser.add_argument("--split", required=True, help="<dataset-or-task-name>/<split>")
    add_fresh_draw_arguments(parser)
    parser.add_argument("--workspace", type=Path, required=True, help="indexed bundle directory")
    parser.add_argument("--model", required=True, help="chat model both arms answer with")
    parser.add_argument("--base-url", default=None, help="override the config's endpoint")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N tasks")
    parser.add_argument("--out", type=Path, default=Path("runs/before-after"))
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="the product git checkout")
    parser.add_argument("--max-usd", type=float, default=None, help="cost ceiling for BOTH arms")
    _add_cost_model_arguments(parser)
    _add_corpus_workspace_arguments(parser)
    add_variant_arm_arguments(parser)
    parser.add_argument(
        "--confirm-spend",
        action="store_true",
        help="execute both arms; without it the command only prints the plan",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="re-render the report from the arm summaries already under --out; "
        "runs no arm, builds no workspace and spends nothing",
    )
    parser.set_defaults(func=cmd_before_after)


def _add_cost_model_arguments(parser: argparse.ArgumentParser) -> None:
    """The estimate's assumptions, each overridable and each printed in the plan."""
    defaults = CostModel()
    parser.add_argument("--calls-per-turn", type=float, default=defaults.calls_per_turn)
    parser.add_argument(
        "--context-tokens-per-turn", type=int, default=defaults.context_tokens_per_turn
    )
    parser.add_argument(
        "--output-tokens-per-turn", type=int, default=defaults.output_tokens_per_turn
    )
    parser.add_argument("--usd-per-1m-input", type=float, default=defaults.usd_per_1m_input)
    parser.add_argument("--usd-per-1m-output", type=float, default=defaults.usd_per_1m_output)
    parser.add_argument(
        "--expected-turns-per-rollout",
        type=float,
        default=defaults.expected_turns_per_rollout,
        help="price each rollout at this many turns, capped at the budget (default: the budget)",
    )


def _add_corpus_workspace_arguments(parser: argparse.ArgumentParser) -> None:
    """The per-corpus workspaces: what building the missing ones would cost, and the go-ahead."""
    parser.add_argument(
        "--usd-per-1m-embed",
        type=float,
        default=DEFAULT_USD_PER_1M_EMBED,
        help="price of the embedding tokens a missing workspace would cost to build",
    )
    parser.add_argument(
        "--build-indexes",
        action="store_true",
        help="build the missing per-corpus workspaces before the arms (spends embedding tokens)",
    )


def _add_before_after_arm(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "before-after-arm",
        help="INTERNAL: run one before/after arm under this process's product",
    )
    parser.add_argument("--settings", type=Path, required=True, help=ARM_SETTINGS_FILENAME)
    parser.add_argument("--split", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--expect-product-under",
        type=Path,
        required=True,
        help="refuse to run unless the imported pydocs_mcp lives under this directory",
    )
    parser.set_defaults(func=cmd_before_after_arm)


def cmd_before_after(args: argparse.Namespace) -> int:
    """Print the plan; execute both arms only when ``--confirm-spend`` is given.

    ``--report-only`` short-circuits both: it re-renders a finished run's report
    and never reaches the spend gate, because it spends nothing.
    """
    try:
        draw = _task_draw(args)
        tasks = asyncio.run(load_split_tasks(args.split, limit=args.limit, draw=draw))
        plan = _plan_from_args(args, tasks=tasks, draw=draw)
        answer_key = answer_key_for(tasks, load_judge_config().jev)
        if args.report_only:
            return _rerender_recorded_arms(args, plan, answer_key)
        if not args.confirm_spend:
            print(render_plan(plan))
            return _EXIT_OK
        return _execute(args, plan, answer_key)
    except (MeasurementPlanError, CorpusWorkspaceError) as exc:
        # An arm that could not be checked out or that exited non-zero lands
        # here too: the operator fixes the input, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_INPUT_ERROR


def _task_draw(args: argparse.Namespace) -> FreshTestDraw | None:
    """The flags' fresh draw; on ``--report-only``, the one the finished arms recorded.

    A re-render scores answers already given, so it loads the sample the arms
    answered — frozen in their settings — and refuses flags naming another.
    """
    flags = fresh_draw_from_args(args)
    if not args.report_only:
        return flags
    recorded = draw_the_arms_recorded([Path(args.out) / role for role in ArmRole])
    return reconciled_with_flags(recorded, flags)


def _plan_from_args(
    args: argparse.Namespace, *, tasks: Sequence[EvalTask], draw: FreshTestDraw | None
) -> MeasurementPlan:
    """Resolve the two commits, the endpoint, each arm's settings and the corpora into a plan."""
    llm_block = _arm_llm_block(args)
    serving = _serving_settings(args, llm_block)
    variants = _arm_variants(args, llm_block, serving.max_agent_turns)
    reuse = checked_reused_baseline(args, variants.baseline, task_ids_of(tasks), serving.basis)
    plan = build_plan(
        repo=args.repo,
        baseline_ref=args.baseline,
        candidate_ref=args.candidate,
        split_spec=args.split,
        task_ids=task_ids_of(tasks),
        model=args.model,
        endpoint=serving.endpoint,
        workspace=args.workspace,
        max_agent_turns=variants.max_agent_turns,
        task_workspaces=plan_task_workspaces(
            tasks,
            workspace=args.workspace,
            identity=serving.identity,
            usd_per_1m_embed=args.usd_per_1m_embed,
        ),
        cost=CostModel(
            calls_per_turn=args.calls_per_turn,
            context_tokens_per_turn=args.context_tokens_per_turn,
            output_tokens_per_turn=args.output_tokens_per_turn,
            usd_per_1m_input=args.usd_per_1m_input,
            usd_per_1m_output=args.usd_per_1m_output,
            expected_turns_per_rollout=args.expected_turns_per_rollout,
        ),
        count_tokens=_description_token_counter(args.model),
        llm_block=llm_block,
        # The composition root wires the real probe: a pinned block or overlay
        # must be accepted by BOTH products, and only a worktree of each can say so.
        # A re-render asks nothing: its arms already ran, so neither guard applies.
        probe_block=accepted_because_it_already_ran if args.report_only else probe_arm_block,
        variants=variants,
        allow_identical=args.allow_identical or args.report_only,
        reused_baseline=None if reuse is None else reuse.arm_dir,
        task_draw=draw,
    )
    return replace(plan, serving=ServingPlan(basis=serving.basis, reuse=reuse))


def _arm_variants(
    args: argparse.Namespace, llm_block: ArmLlmBlock | None, serving_turns: int
) -> ArmVariants:
    """Each arm's variant from the flags; on ``--report-only``, the one it recorded."""
    variants = variants_from_args(args, llm_block=llm_block, serving_turns=serving_turns)
    if not args.report_only:
        return variants
    return recorded_variants(Path(args.out), variants, flagged=overlay_flags_given(args))


def _arm_llm_block(args: argparse.Namespace) -> ArmLlmBlock | None:
    """The block both arms pin, validated HERE so a typo never reaches a rollout.

    Also the place the old shape is refused: model settings in the SERVING file
    are what the binding rejects per rollout, so the plan says so once, by name,
    with the flag that replaces them.
    """
    refuse_file_sourced_model_settings(args.config)
    return None if args.llm_block is None else load_arm_llm_block(args.llm_block)


def _serving_settings(args: argparse.Namespace, llm_block: ArmLlmBlock | None) -> ServingSettings:
    """Read the serving YAML once (``before_after_serving``); replaced by a stub in tests."""
    return serving_settings_from_args(args, llm_block)


def _description_token_counter(model: str) -> TokenCounter:
    """Count description tokens under the run's own model encoding."""
    from pydocs_mcp.retrieval.llm_clients.model_budget import count_tokens

    return lambda text: count_tokens(text, model)


def _execute(args: argparse.Namespace, plan: MeasurementPlan, answer_key: AnswerKey) -> int:
    """Build any missing workspace, run the arms in child processes, write the report.

    An arm dir holding another run's rows is refused before anything is
    written into it. A reused baseline is linked next — before any workspace is
    built — so an ``--out`` that already holds another baseline stops the run
    while it is free.
    """
    refuse_a_foreign_resume(args, plan)
    reused = _linked_reused_baseline(args)
    refuse_a_serving_change_on_resume(args, plan)
    _settle_task_workspaces(args, plan)
    # Written BEFORE the arms, so a run that dies mid-arm still records what it set out to do.
    _write_plan(Path(args.out), plan)
    baseline = reused or run_one_arm(args, plan, ArmRole.BASELINE)
    summaries = [baseline, run_one_arm(args, plan, ArmRole.CANDIDATE)]
    return _write_report(args, plan, summaries, answer_key)


def _linked_reused_baseline(args: argparse.Namespace) -> ArmSummary | None:
    """``<out>/baseline`` pointed at the reused arm, and its summary; None without the flag."""
    if args.reuse_baseline is None:
        return None
    return read_arm_summary(link_reused_baseline(args.reuse_baseline, Path(args.out)))


def _rerender_recorded_arms(
    args: argparse.Namespace, plan: MeasurementPlan, answer_key: AnswerKey
) -> int:
    """Re-render the report from the arm summaries a finished run already wrote.

    WHY this exists: the report stage runs LAST, after both arms have answered
    every task at the endpoint. A crash there — a metric that cannot read one
    arm's traces, a rendering bug — must never cost a re-run of the paid part.
    Every input it needs is on disk (``<out>/baseline/arm.json``,
    ``<out>/candidate/arm.json`` and the traces they index), so this path checks
    out nothing, spawns no arm, builds no workspace and spends nothing — and
    scores each stored answer afresh, so a fixed scorer re-scores a paid run.
    """
    out_dir = Path(args.out)
    summaries = [_recorded_arm_summary(out_dir, role) for role in ArmRole]
    # The plan the run wrote before its arms is kept: it says what the run set out
    # to do, overlay file names included, which no arm_settings.json records.
    if not (out_dir / _PLAN_FILENAME).exists():
        _write_plan(out_dir, plan)
    return _write_report(args, plan, summaries, answer_key)


def _recorded_arm_summary(out_dir: Path, role: ArmRole) -> ArmSummary:
    """One arm's summary off disk, or a refusal naming the directory that has none."""
    arm_dir = out_dir / role
    try:
        summary = read_arm_summary(arm_dir)
    except (OSError, ValueError, KeyError) as exc:
        raise MeasurementPlanError(
            f"--report-only found no readable {ARM_SUMMARY_FILENAME} in {arm_dir} ({exc}); "
            f"it re-renders a FINISHED run and runs no arm, so it expects one under "
            f"each of {', '.join(str(out_dir / each) for each in ArmRole)}"
        ) from exc
    if summary.halt_reason == HaltReason.IN_PROGRESS:
        raise MeasurementPlanError(
            f"--report-only: {arm_dir} was killed mid-run (halt_reason "
            f"{HaltReason.IN_PROGRESS.value}, {len(summary.tasks)} task(s) checkpointed); "
            "resume it with --confirm-spend, then re-render"
        )
    return summary


def _write_plan(out_dir: Path, plan: MeasurementPlan) -> None:
    """Record the plan beside the report, so a run carries its own inputs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / _PLAN_FILENAME).write_text(render_plan(plan), encoding="utf-8")


def _write_report(
    args: argparse.Namespace,
    plan: MeasurementPlan,
    summaries: Sequence[ArmSummary],
    answer_key: AnswerKey,
) -> int:
    """Measure both arms off their recorded traces, write the report, print it."""
    report = render_report(plan, measure_both_arms(plan, summaries, answer_key=answer_key))
    report_path = Path(args.out) / _REPORT_FILENAME
    report_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwrote {report_path}", file=sys.stderr)
    return _EXIT_OK


def _settle_task_workspaces(args: argparse.Namespace, plan: MeasurementPlan) -> None:
    """Refuse to start an arm while a task has no index of its own corpus.

    An arm run against a workspace that does not hold the task's repository
    measures nothing — the agent searches a corpus the question is not about —
    so the missing bundles are either built here, ONCE, before either arm, or
    the run stops before spending a cent on the endpoint.
    """
    workspaces = plan.task_workspaces
    if not workspaces.missing:
        return
    if not args.build_indexes:
        raise MeasurementPlanError(
            f"{len(workspaces.missing)} of {len(workspaces.corpora)} task workspace(s) "
            f"are not built under {workspaces.root}: "
            f"{', '.join(corpus.bundle_dir.name for corpus in workspaces.missing)}. "
            "Each task must search an index of ITS OWN corpus, so no arm starts until "
            f"they exist. Re-run with --build-indexes to build them first "
            f"(~{workspaces.missing_embed_tokens} embedding tokens, "
            f"${workspaces.missing_embed_usd:.2f})."
        )
    _refuse_a_build_over_the_ceiling(args, workspaces)
    build_missing_workspaces(workspaces, python=Path(sys.executable), config=Path(args.config))


def _refuse_a_build_over_the_ceiling(args: argparse.Namespace, workspaces: TaskWorkspaces) -> None:
    """``--max-usd`` bounds the WHOLE run, and the build spends first."""
    if args.max_usd is None or workspaces.missing_embed_usd < float(args.max_usd):
        return
    raise MeasurementPlanError(
        f"building the missing workspaces is estimated at "
        f"${workspaces.missing_embed_usd:.2f}, which --max-usd ${float(args.max_usd):.2f} "
        "does not cover; raise the ceiling or build fewer corpora (--limit)"
    )


def cmd_before_after_arm(args: argparse.Namespace) -> int:
    """Run one arm here, under whatever product this interpreter imported."""
    settings = ArmSettings.from_dict(json.loads(args.settings.read_text(encoding="utf-8")))
    try:
        assert_product_under(args.expect_product_under)
    except MeasurementPlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_INPUT_ERROR
    try:
        # The plan's draw, frozen in the settings file, so both arms answer one task set.
        draw = settings.task_draw
        tasks = asyncio.run(load_split_tasks(args.split, limit=args.limit, draw=draw))
        summary = asyncio.run(run_arm(settings, tasks))
    # The split or its draw was refused, the index step refused a bundle or
    # failed, or the arm dir holds another role.
    except (CorpusWorkspaceError, MeasurementPlanError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_INPUT_ERROR
    print(f"{summary.role}: {len(summary.tasks)} task(s), halt={summary.halt_reason}")
    for step in summary.index_steps:
        print(
            f"  index step {Path(step.bundle_dir).name}: {step.wall_seconds:.1f}s, "
            f"embed diff {step.embed_diff}"
        )
    return _EXIT_OK
