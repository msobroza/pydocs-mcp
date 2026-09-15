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
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from pydocs_eval.campaign.before_after import (
    ArmLlmBlock,
    CostModel,
    MeasurementPlan,
    MeasurementPlanError,
    TokenCounter,
    build_plan,
    render_plan,
)
from pydocs_eval.campaign.before_after_arm import (
    ARM_SUMMARY_FILENAME,
    ArmSettings,
    ArmSummary,
    read_arm_summary,
    run_arm,
)
from pydocs_eval.campaign.before_after_block_probe import probe_arm_block
from pydocs_eval.campaign.before_after_corpora import (
    DEFAULT_USD_PER_1M_EMBED,
    CorpusWorkspaceError,
    IndexIdentity,
    TaskWorkspaces,
    build_missing_workspaces,
    plan_task_workspaces,
)
from pydocs_eval.campaign.before_after_llm_block import (
    load_arm_llm_block,
    refuse_file_sourced_model_settings,
)
from pydocs_eval.campaign.before_after_measure import measure_arm
from pydocs_eval.campaign.before_after_product import (
    arm_environment,
    assert_product_under,
    product_worktree,
)
from pydocs_eval.campaign.before_after_report import render_report
from pydocs_eval.campaign.before_after_split import load_split_tasks, task_ids_of
from pydocs_eval.campaign.index_cache import resolve_scope_id
from pydocs_eval.datasets.base_dataset import EvalTask

_ARM_SETTINGS_FILENAME = "arm_settings.json"
_PLAN_FILENAME = "plan.txt"
_REPORT_FILENAME = "before_after.md"
_ARM_ROLES = ("baseline", "candidate")

# What the plan prints when nothing named an endpoint — the SDK's own host.
_VENDOR_DEFAULT_ENDPOINT = "vendor default"

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
        "(base_url, auth, provider, params, parallel_tool_calls); the model comes from --model",
    )
    parser.add_argument("--split", required=True, help="<dataset-or-task-name>/<split>")
    parser.add_argument("--workspace", type=Path, required=True, help="indexed bundle directory")
    parser.add_argument("--model", required=True, help="chat model both arms answer with")
    parser.add_argument("--base-url", default=None, help="override the config's endpoint")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N tasks")
    parser.add_argument("--out", type=Path, default=Path("runs/before-after"))
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="the product git checkout")
    parser.add_argument("--max-usd", type=float, default=None, help="cost ceiling for BOTH arms")
    _add_cost_model_arguments(parser)
    _add_corpus_workspace_arguments(parser)
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
    parser.add_argument("--settings", type=Path, required=True, help=f"{_ARM_SETTINGS_FILENAME}")
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
        tasks = asyncio.run(load_split_tasks(args.split, limit=args.limit))
        plan = _plan_from_args(args, tasks=tasks)
        if args.report_only:
            return _rerender_recorded_arms(args, plan)
        if not args.confirm_spend:
            print(render_plan(plan))
            return _EXIT_OK
        return _execute(args, plan)
    except (MeasurementPlanError, CorpusWorkspaceError) as exc:
        # An arm that could not be checked out or that exited non-zero lands
        # here too: the operator fixes the input, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_INPUT_ERROR


def _plan_from_args(args: argparse.Namespace, *, tasks: Sequence[EvalTask]) -> MeasurementPlan:
    """Resolve the two commits, the endpoint, the turn budget and the corpora into a plan."""
    llm_block = _arm_llm_block(args)
    serving = _serving_settings(args, llm_block)
    return build_plan(
        repo=args.repo,
        baseline_ref=args.baseline,
        candidate_ref=args.candidate,
        split_spec=args.split,
        task_ids=task_ids_of(tasks),
        model=args.model,
        endpoint=serving.endpoint,
        workspace=args.workspace,
        max_agent_turns=serving.max_agent_turns,
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
        ),
        count_tokens=_description_token_counter(args.model),
        llm_block=llm_block,
        # The composition root wires the real probe: a pinned block must be
        # accepted by BOTH products, and only a worktree of each can say so.
        probe_block=probe_arm_block,
    )


def _arm_llm_block(args: argparse.Namespace) -> ArmLlmBlock | None:
    """The block both arms pin, validated HERE so a typo never reaches a rollout.

    Also the place the old shape is refused: model settings in the SERVING file
    are what the binding rejects per rollout, so the plan says so once, by name,
    with the flag that replaces them.
    """
    refuse_file_sourced_model_settings(args.config)
    return None if args.llm_block is None else load_arm_llm_block(args.llm_block)


@dataclass(frozen=True, slots=True)
class ServingSettings:
    """What the serving YAML decides for BOTH arms — read once, printed in the plan."""

    endpoint: str
    max_agent_turns: int
    identity: IndexIdentity


def _serving_settings(args: argparse.Namespace, llm_block: ArmLlmBlock | None) -> ServingSettings:
    """Read the serving YAML once: the endpoint, the turn budget, the index identity.

    The endpoint follows the harness's OWN precedence — ``--base-url`` over the
    arm block over the serving file — so the plan names the host the run will
    really talk to, not one a serving file happens to still mention.

    The index identity is what a per-corpus workspace has to match: a bundle
    built with another embedder cannot be served by this config at all, so the
    plan checks it before an arm starts rather than letting the serve child fail.
    """
    from pydocs_mcp.retrieval.config.app_config import AppConfig

    config = AppConfig.load(args.config)
    ask = config.ask_your_docs
    block_url = None if llm_block is None else llm_block.settings.get("base_url")
    file_url = ask.llm.base_url if ask.llm else None
    return ServingSettings(
        endpoint=str(args.base_url or block_url or file_url or _VENDOR_DEFAULT_ENDPOINT),
        max_agent_turns=int(ask.max_agent_turns),
        identity=IndexIdentity(
            embedder_model=config.embedding.model_name,
            embedder_dim=config.embedding.dim,
            scope_id=resolve_scope_id(None, config=config),
        ),
    )


def _description_token_counter(model: str) -> TokenCounter:
    """Count description tokens under the run's own model encoding."""
    from pydocs_mcp.retrieval.llm_clients.model_budget import count_tokens

    return lambda text: count_tokens(text, model)


def _execute(args: argparse.Namespace, plan: MeasurementPlan) -> int:
    """Build any missing workspace, run both arms in child processes, write the report."""
    _settle_task_workspaces(args, plan)
    # Written BEFORE the arms, so a run that dies mid-arm still records what it set out to do.
    _write_plan(Path(args.out), plan)
    summaries = [_run_one_arm(args, plan, role) for role in _ARM_ROLES]
    return _write_report(args, plan, summaries)


def _rerender_recorded_arms(args: argparse.Namespace, plan: MeasurementPlan) -> int:
    """Re-render the report from the arm summaries a finished run already wrote.

    WHY this exists: the report stage runs LAST, after both arms have answered
    every task at the endpoint. A crash there — a metric that cannot read one
    arm's traces, a rendering bug — must never cost a re-run of the paid part.
    Every input it needs is on disk (``<out>/baseline/arm.json``,
    ``<out>/candidate/arm.json`` and the traces they index), so this path checks
    out nothing, spawns no arm, builds no workspace and spends nothing.
    """
    out_dir = Path(args.out)
    summaries = [_recorded_arm_summary(out_dir, role) for role in _ARM_ROLES]
    _write_plan(out_dir, plan)
    return _write_report(args, plan, summaries)


def _recorded_arm_summary(out_dir: Path, role: str) -> ArmSummary:
    """One arm's summary off disk, or a refusal naming the directory that has none."""
    arm_dir = out_dir / role
    try:
        return read_arm_summary(arm_dir)
    except (OSError, ValueError, KeyError) as exc:
        raise MeasurementPlanError(
            f"--report-only found no readable {ARM_SUMMARY_FILENAME} in {arm_dir} ({exc}); "
            f"it re-renders a FINISHED run and runs no arm, so it expects one under "
            f"each of {', '.join(str(out_dir / name) for name in _ARM_ROLES)}"
        ) from exc


def _write_plan(out_dir: Path, plan: MeasurementPlan) -> None:
    """Record the plan beside the report, so a run carries its own inputs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / _PLAN_FILENAME).write_text(render_plan(plan), encoding="utf-8")


def _write_report(
    args: argparse.Namespace, plan: MeasurementPlan, summaries: Sequence[ArmSummary]
) -> int:
    """Measure both arms off their recorded traces, write the report, print it."""
    report = render_report(
        plan,
        [
            measure_arm(summary, commit, workspace=plan.workspace, prices=plan.cost)
            for summary, commit in zip(summaries, (plan.baseline, plan.candidate), strict=True)
        ],
    )
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


def _run_one_arm(args: argparse.Namespace, plan: MeasurementPlan, role: str) -> ArmSummary:
    """Check the arm's commit out, run it in a child process, read its summary."""
    commit = plan.baseline if role == "baseline" else plan.candidate
    arm_dir = Path(args.out) / role
    arm_dir.mkdir(parents=True, exist_ok=True)
    settings = _arm_settings(args, plan, role=role, commit=commit.sha, arm_dir=arm_dir)
    (arm_dir / _ARM_SETTINGS_FILENAME).write_text(
        json.dumps(asdict(settings), indent=2, sort_keys=True), encoding="utf-8"
    )
    with product_worktree(args.repo, commit.sha, Path(args.out) / "worktrees") as worktree:
        _spawn_arm(worktree, arm_dir=arm_dir, split=args.split, limit=args.limit)
    return read_arm_summary(arm_dir)


def _arm_settings(
    args: argparse.Namespace, plan: MeasurementPlan, *, role: str, commit: str, arm_dir: Path
) -> ArmSettings:
    return ArmSettings(
        role=role,
        commit=commit,
        workspace=str(plan.workspace),
        model=plan.model,
        trace_root=str(arm_dir / "traces"),
        out_dir=str(arm_dir),
        max_agent_turns=plan.max_agent_turns,
        estimated_usd_per_rollout=plan.estimated_usd_per_rollout,
        cost_ceiling_usd=_ceiling(args, plan),
        base_url=args.base_url,
        pydocs_config=str(args.config),
        # Both arms get the SAME mapping object's contents: the byte-identical
        # block is what makes the two columns differ by the commit and nothing else.
        llm_block=dict(plan.llm_block.settings) if plan.llm_block is not None else None,
        task_workspaces=plan.task_workspaces.as_map(),
    )


def _ceiling(args: argparse.Namespace, plan: MeasurementPlan) -> float:
    """The per-arm cost ceiling: the operator's ``--max-usd``, else the estimate.

    The guard needs a positive number, and the estimate is the only cost signal
    this harness produces — so an operator who names no ceiling still gets one
    that stops a run which has already spent what the plan predicted.

    ``--max-usd`` bounds the whole run, and building the missing per-corpus
    workspaces spends embedding tokens against it first: what is left for the
    two arms is the ceiling minus that build.
    """
    if args.max_usd is not None:
        left = float(args.max_usd) - plan.task_workspaces.missing_embed_usd
        return left / len(_ARM_ROLES)
    return max(plan.estimated_usd, plan.estimated_usd_per_rollout, 1.0)


def _spawn_arm(worktree: Path, *, arm_dir: Path, split: str, limit: int | None) -> None:
    """Run one arm in a child whose product is the worktree's, and eval is ours."""
    command = [
        sys.executable,
        "-m",
        "pydocs_eval.campaign",
        "before-after-arm",
        "--settings",
        str(arm_dir / _ARM_SETTINGS_FILENAME),
        "--split",
        split,
        "--expect-product-under",
        str(worktree),
    ]
    if limit is not None:
        command += ["--limit", str(limit)]
    completed = subprocess.run(command, env=arm_environment(worktree), check=False)
    if completed.returncode != 0:
        raise MeasurementPlanError(
            f"arm in {arm_dir} exited {completed.returncode}; its output is above"
        )


def cmd_before_after_arm(args: argparse.Namespace) -> int:
    """Run one arm here, under whatever product this interpreter imported."""
    settings = ArmSettings(**json.loads(args.settings.read_text(encoding="utf-8")))
    try:
        assert_product_under(args.expect_product_under)
    except MeasurementPlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_INPUT_ERROR
    tasks = asyncio.run(load_split_tasks(args.split, limit=args.limit))
    summary = asyncio.run(run_arm(settings, tasks))
    print(f"{summary.role}: {len(summary.tasks)} task(s), halt={summary.halt_reason}")
    return _EXIT_OK
