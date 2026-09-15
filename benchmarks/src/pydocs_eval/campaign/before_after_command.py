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

Only the PRODUCT comes from the worktree. The eval suite — the harness bridge,
the split loader, the metric layer — always comes from the checkout the command
was launched from, so one implementation of every metric scores both arms.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import pydocs_eval
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
    ArmSettings,
    ArmSummary,
    read_arm_summary,
    run_arm,
)
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
from pydocs_eval.campaign.before_after_report import render_report
from pydocs_eval.campaign.before_after_split import load_split_tasks, task_ids_of
from pydocs_eval.campaign.index_cache import resolve_scope_id
from pydocs_eval.datasets.base_dataset import EvalTask

_ARM_SETTINGS_FILENAME = "arm_settings.json"
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
    """Print the plan; execute both arms only when ``--confirm-spend`` is given."""
    try:
        tasks = asyncio.run(load_split_tasks(args.split, limit=args.limit))
        plan = _plan_from_args(args, tasks=tasks)
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
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plan.txt").write_text(render_plan(plan), encoding="utf-8")
    summaries = [_run_one_arm(args, plan, role) for role in _ARM_ROLES]
    report = render_report(
        plan,
        [
            measure_arm(summary, commit, workspace=plan.workspace, prices=plan.cost)
            for summary, commit in zip(summaries, (plan.baseline, plan.candidate), strict=True)
        ],
    )
    report_path = out_dir / _REPORT_FILENAME
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


@contextlib.contextmanager
def product_worktree(repo: Path, sha: str, root: Path) -> Iterator[Path]:
    """A detached git worktree of ``sha``, removed again when the arm finishes."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"src-{sha[:12]}"
    _run_git(repo, "worktree", "add", "--detach", str(path), sha)
    try:
        yield path
    finally:
        _run_git(repo, "worktree", "remove", "--force", str(path), check=False)


def _run_git(repo: Path, *args: str, check: bool = True) -> None:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if check and completed.returncode != 0:
        raise MeasurementPlanError(
            f"git {' '.join(args)} failed in {repo}: {completed.stderr.strip()}"
        )


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
    completed = subprocess.run(command, env=_arm_environment(worktree), check=False)
    if completed.returncode != 0:
        raise MeasurementPlanError(
            f"arm in {arm_dir} exited {completed.returncode}; its output is above"
        )


def _arm_environment(worktree: Path) -> dict[str, str]:
    """The child's path: the worktree's PRODUCT, then THIS checkout's eval suite."""
    eval_src = Path(pydocs_eval.__file__).resolve().parents[1]
    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(worktree / "python"), str(eval_src)]),
    }


def cmd_before_after_arm(args: argparse.Namespace) -> int:
    """Run one arm here, under whatever product this interpreter imported."""
    settings = ArmSettings(**json.loads(args.settings.read_text(encoding="utf-8")))
    try:
        _assert_product_under(args.expect_product_under)
    except MeasurementPlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_INPUT_ERROR
    tasks = asyncio.run(load_split_tasks(args.split, limit=args.limit))
    summary = asyncio.run(run_arm(settings, tasks))
    print(f"{summary.role}: {len(summary.tasks)} task(s), halt={summary.halt_reason}")
    return _EXIT_OK


def _assert_product_under(expected: Path) -> None:
    """Refuse to measure a product the path did not actually switch.

    An installed distribution or a ``.pth`` entry can win over ``PYTHONPATH``;
    the arm would then import the SAME code for both commits and the report
    would show a difference of zero that means nothing.
    """
    import pydocs_mcp

    resolved = Path(pydocs_mcp.__file__).resolve()
    if expected.resolve() not in resolved.parents:
        raise MeasurementPlanError(
            f"imported pydocs_mcp from {resolved}, expected it under {expected}; "
            "an installed copy is shadowing the worktree, so this arm would not "
            "measure the commit it was asked to measure"
        )
