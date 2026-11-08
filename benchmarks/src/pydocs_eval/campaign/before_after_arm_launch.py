"""How the ``before-after`` parent launches its arms: the variant flags, each arm's
settings file, its worktree and its child process.

Split out of ``before_after_command`` (the composition root keeps the plan, the
report and the spend gate). An arm is a whole child process because the product
under test cannot be imported twice; everything that child needs to know beyond
its commit travels in ``arm_settings.json``, built here from the plan.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from pydocs_eval.campaign.before_after import (
    ArmLlmBlock,
    ArmRole,
    MeasurementPlan,
    MeasurementPlanError,
)
from pydocs_eval.campaign.before_after_arm import (
    ARM_SETTINGS_FILENAME,
    ArmSettings,
    ArmSummary,
    read_arm_summary,
    write_arm_settings,
)
from pydocs_eval.campaign.before_after_arm_settings import ARM_OVERLAY_SHAPE, load_arm_overlay
from pydocs_eval.campaign.before_after_arm_variant import ArmVariants, arm_variants
from pydocs_eval.campaign.before_after_product import arm_environment, product_worktree

_BASELINE_SETTINGS_FLAG = "--baseline-settings"
_CANDIDATE_SETTINGS_FLAG = "--candidate-settings"


def add_variant_arm_arguments(parser: argparse.ArgumentParser) -> None:
    """The per-arm overlays and the A/A escape hatch (issue #385)."""
    for flag, role in (
        (_BASELINE_SETTINGS_FLAG, "baseline"),
        (_CANDIDATE_SETTINGS_FLAG, "candidate"),
    ):
        parser.add_argument(
            flag,
            type=Path,
            default=None,
            help=f"YAML overlay the {role} arm runs under ({ARM_OVERLAY_SHAPE})",
        )
    parser.add_argument(
        "--allow-identical",
        action="store_true",
        help="run one commit under one setting twice (the A/A arm); refused otherwise",
    )
    parser.add_argument(
        "--reuse-baseline",
        type=Path,
        default=None,
        help="a finished baseline arm directory to compare against instead of running "
        "one; refused unless its commit, model, turn cap and settings digest match",
    )


def variants_from_args(
    args: argparse.Namespace, *, llm_block: ArmLlmBlock | None, serving_turns: int
) -> ArmVariants:
    """Both arms' variants: each overlay validated, the ``--llm-block`` merged under its llm."""
    baseline, candidate = (
        None if path is None else load_arm_overlay(path, flag=flag)
        for path, flag in (
            (args.baseline_settings, _BASELINE_SETTINGS_FLAG),
            (args.candidate_settings, _CANDIDATE_SETTINGS_FLAG),
        )
    )
    return arm_variants(baseline, candidate, llm_block=llm_block, serving_turns=serving_turns)


def run_one_arm(args: argparse.Namespace, plan: MeasurementPlan, role: ArmRole) -> ArmSummary:
    """Check the arm's commit out, run it in a child process, read its summary.

    Raises:
        MeasurementPlanError: the arm's directory links to a reused baseline;
            writing through the link would rewrite that accepted arm in place.
    """
    arm_dir = Path(args.out) / role
    if arm_dir.is_symlink():
        raise MeasurementPlanError(
            f"{arm_dir} links to a reused baseline ({arm_dir.resolve()}), which an arm "
            "never writes into; pass --reuse-baseline again, or choose a fresh --out"
        )
    arm_dir.mkdir(parents=True, exist_ok=True)
    settings = arm_settings_for(args, plan, role=role, arm_dir=arm_dir)
    write_arm_settings(arm_dir, settings)
    with product_worktree(args.repo, settings.commit, Path(args.out) / "worktrees") as worktree:
        _spawn_arm(worktree, arm_dir=arm_dir, split=args.split, limit=args.limit)
    return read_arm_summary(arm_dir)


def arm_settings_for(
    args: argparse.Namespace, plan: MeasurementPlan, *, role: ArmRole, arm_dir: Path
) -> ArmSettings:
    """One arm's settings file: the plan's shared inputs plus THAT arm's variant."""
    commit = plan.baseline if role == ArmRole.BASELINE else plan.candidate
    return ArmSettings(
        role=role,
        commit=commit.sha,
        workspace=str(plan.workspace),
        model=plan.model,
        trace_root=str(arm_dir / "traces"),
        out_dir=str(arm_dir),
        estimated_usd_per_rollout=plan.estimated_usd_per_rollout,
        cost_ceiling_usd=_ceiling(args, plan),
        base_url=args.base_url,
        pydocs_config=str(args.config),
        task_workspaces=plan.task_workspaces.as_map(),
        # Each arm re-extracts these bundles under its own product before it
        # serves them (issue #380): the bundle key carries no chunk-tree rule.
        bundle_sources=plan.task_workspaces.bundle_sources(),
        # Architecture, turn cap, llm block (the --llm-block file with this arm's
        # overlay llm merged over it), harness keys, guidance, scaffold, digest.
        # Without overlays both arms get the same values, so they differ by the commit.
        **plan.arm_variants.for_role(role).arm_settings_fields(),
    )


def _ceiling(args: argparse.Namespace, plan: MeasurementPlan) -> float:
    """The per-arm cost ceiling: the operator's ``--max-usd``, else the estimate.

    The guard needs a positive number, and the estimate is the only cost signal
    this harness produces — so an operator who names no ceiling still gets one
    that stops a run which has already spent what the plan predicted.

    ``--max-usd`` bounds the whole run, and building the missing per-corpus
    workspaces spends embedding tokens against it first: what is left for the
    arms this run spawns is the ceiling minus that build.
    """
    if args.max_usd is not None:
        left = float(args.max_usd) - plan.task_workspaces.missing_embed_usd
        return left / plan.arms_run
    return max(plan.estimated_usd, plan.estimated_usd_per_rollout, 1.0)


def _spawn_arm(worktree: Path, *, arm_dir: Path, split: str, limit: int | None) -> None:
    """Run one arm in a child whose product is the worktree's, and eval is ours."""
    command = [
        sys.executable,
        "-m",
        "pydocs_eval.campaign",
        "before-after-arm",
        "--settings",
        str(arm_dir / ARM_SETTINGS_FILENAME),
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


__all__ = (
    "add_variant_arm_arguments",
    "arm_settings_for",
    "run_one_arm",
    "variants_from_args",
)
