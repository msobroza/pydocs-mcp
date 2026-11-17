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
    ARM_SUMMARY_FILENAME,
    ArmSettings,
    ArmSummary,
    read_arm_summary,
    write_arm_settings,
)
from pydocs_eval.campaign.before_after_arm_records import (
    ALLOW_SERVING_MISMATCH_FLAG,
    ServingMismatchOverride,
    write_serving_override,
)
from pydocs_eval.campaign.before_after_arm_resume import refuse_rows_of_another_run
from pydocs_eval.campaign.before_after_arm_settings import ARM_OVERLAY_SHAPE, load_arm_overlay
from pydocs_eval.campaign.before_after_arm_variant import ArmVariants, arm_variants
from pydocs_eval.campaign.before_after_product import arm_environment, product_worktree
from pydocs_eval.campaign.before_after_serving import (
    SERVING_FINGERPRINT_FILENAME,
    ServingFingerprint,
    read_serving_fingerprint,
    write_serving_fingerprint,
)

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
        "one; refused unless its commit, model, turn cap, settings digest and serving "
        "fingerprint match",
    )
    parser.add_argument(
        ALLOW_SERVING_MISMATCH_FLAG,
        action="store_true",
        help="force a --reuse-baseline whose serving fingerprint differs or was never "
        "recorded; logged, and named in the plan, beside the candidate's arm.json and in the report",
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


def overlay_flags_given(args: argparse.Namespace) -> dict[ArmRole, str]:
    """Each role whose overlay flag was given, mapped to that flag's name."""
    given = (
        (ArmRole.BASELINE, _BASELINE_SETTINGS_FLAG, args.baseline_settings),
        (ArmRole.CANDIDATE, _CANDIDATE_SETTINGS_FLAG, args.candidate_settings),
    )
    return {role: flag for role, flag, path in given if path is not None}


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
    _record_serving_fingerprint(plan, role, arm_dir)
    # Before the spawn, so a parent that dies after the arm still leaves it on disk.
    write_serving_override(arm_dir, _forced_reuse(plan, role))
    with product_worktree(args.repo, settings.commit, Path(args.out) / "worktrees") as worktree:
        _spawn_arm(worktree, arm_dir=arm_dir, split=args.split, limit=args.limit)
    return read_arm_summary(arm_dir)


def _record_serving_fingerprint(plan: MeasurementPlan, role: ArmRole, arm_dir: Path) -> None:
    """``serving.json`` beside the arm: what a later ``--reuse-baseline`` matches it by.

    Not written over an arm that already holds rows but no fingerprint (one
    started before fingerprints existed): its earlier rows were served under a
    setup nobody recorded, and claiming this one for them would let it qualify.
    """
    fingerprint = _arm_fingerprint(plan, role)
    if fingerprint is None:
        return
    unrecorded_rows = (arm_dir / ARM_SUMMARY_FILENAME).exists() and (
        read_serving_fingerprint(arm_dir) is None
    )
    if not unrecorded_rows:
        write_serving_fingerprint(arm_dir, fingerprint)


def _arm_fingerprint(plan: MeasurementPlan, role: ArmRole) -> ServingFingerprint | None:
    if plan.serving is None:
        return None
    return plan.serving.basis.fingerprint(plan.arm_variants.for_role(role).llm_block)


def refuse_a_serving_change_on_resume(args: argparse.Namespace, plan: MeasurementPlan) -> None:
    """Before ANY arm runs: an arm dir served under another fingerprint is not resumed.

    A resumed arm keeps the rows it already answered (``rows_to_resume``), whose
    identity has no serving part — so resuming under another serving YAML,
    endpoint or index would mix two setups under one ``serving.json``.

    Raises:
        MeasurementPlanError: naming each arm and key that differs.
    """
    changes = [line for role in _roles_run(plan) for line in _serving_changes(args, plan, role)]
    if changes:
        raise MeasurementPlanError(
            f"{args.out} already holds arms served differently ({'; '.join(changes)}); "
            "resuming would keep their answered rows under this serving setup — "
            "choose a fresh --out, or restore the setup they ran under"
        )


def refuse_a_foreign_resume(args: argparse.Namespace, plan: MeasurementPlan) -> None:
    """Before ANY write to an arm dir: rows another run answered are not resumed.

    The child refuses them too (``rows_to_resume``), but only after this parent
    has written the new run's ``arm_settings.json`` and ``serving.json`` beside
    them — and the compare verb and ``--report-only`` then score the old answers
    under the new settings' cap, gold and workspace.

    Raises:
        MeasurementPlanError: an arm dir holds rows of another role, commit,
            model or settings digest, or of tasks outside the plan's.
    """
    for role in _roles_run(plan):
        arm_dir = Path(args.out) / role
        recorded = _recorded_summary(arm_dir)
        if recorded is None:
            continue
        expected = arm_settings_for(args, plan, role=role, arm_dir=arm_dir).run_identity()
        refuse_rows_of_another_run(recorded, expected, plan.task_ids, arm_dir)


def _recorded_summary(arm_dir: Path) -> ArmSummary | None:
    """The ``arm.json`` an earlier run left in ``arm_dir``; None for a fresh arm."""
    if not (arm_dir / ARM_SUMMARY_FILENAME).is_file():
        return None
    try:
        return read_arm_summary(arm_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MeasurementPlanError(
            f"{arm_dir / ARM_SUMMARY_FILENAME} is unreadable ({exc}); expected the arm.json "
            "an earlier run of this arm wrote, or none: give this run a fresh --out"
        ) from exc


def _roles_run(plan: MeasurementPlan) -> tuple[ArmRole, ...]:
    """The arms this run spawns: the candidate alone over a reused baseline."""
    return (ArmRole.CANDIDATE,) if plan.reused_baseline else tuple(ArmRole)


def _serving_changes(args: argparse.Namespace, plan: MeasurementPlan, role: ArmRole) -> list[str]:
    arm_dir, fingerprint = Path(args.out) / role, _arm_fingerprint(plan, role)
    try:
        recorded = None if fingerprint is None else read_serving_fingerprint(arm_dir)
    except (OSError, ValueError) as exc:
        return [f"{role}: unreadable {SERVING_FINGERPRINT_FILENAME} ({exc})"]
    if recorded is None or fingerprint is None:
        return []
    label = f"this plan's {role}"
    return [
        f"{role} {line}" for line in recorded.difference_lines(fingerprint, expected_label=label)
    ]


def _forced_reuse(plan: MeasurementPlan, role: ArmRole) -> ServingMismatchOverride | None:
    """What the candidate records about a forced reuse; None for the baseline, or nothing forced.

    Recorded by the parent, not by the child: the forced reuse is a fact about
    the COMPARISON, not about how the candidate ran — its settings, and so its
    run identity and resume, are the same either way. None clears a record an
    earlier run into this ``--out`` left: this comparison forced nothing.
    """
    reuse = None if plan.serving is None else plan.serving.reuse
    if role != ArmRole.CANDIDATE or reuse is None:
        return None
    return reuse.override_record()


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
        # The fresh redraw, its excluded ids frozen here: the child loads the
        # plan's tasks even if the exclusion file is edited mid-run.
        task_draw=plan.task_draw,
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
    "overlay_flags_given",
    "refuse_a_foreign_resume",
    "refuse_a_serving_change_on_resume",
    "run_one_arm",
    "variants_from_args",
)
