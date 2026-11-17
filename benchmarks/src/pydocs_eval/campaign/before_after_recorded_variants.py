"""The variants a finished run's arms RAN, for ``--report-only`` to re-render under.

A re-render scores answers already given, so each arm's settings beyond its
commit — architecture, turn cap, llm block, harness keys, guidance, scaffold,
digest — come from the ``arm_settings.json`` the arm wrote, never from the
flags (the sibling of ``before_after_recorded_draw``, which does the same for
the task draw). Building them from the flags made a same-commit variant run
unrenderable without its overlay flags (the identical-arms guard refused it),
probed each arm again when they were repeated, and rewrote the report's
provenance from whatever the flags now said.

An overlay flag given on a re-render must name the digest its arm recorded.
An arm directory with no settings file (one written by hand, or before the
file existed) keeps the flags' variant, as every re-render did before.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from pydocs_eval.campaign.before_after import (
    ArmRole,
    CommitUnderTest,
    MeasurementPlanError,
)
from pydocs_eval.campaign.before_after_arm import ARM_SETTINGS_FILENAME, read_arm_settings
from pydocs_eval.campaign.before_after_arm_variant import ArmVariant, ArmVariants
from pydocs_eval.campaign.before_after_block_probe import ArmBlockAcceptance, ArmBlockVerdict


def recorded_variants(
    out_dir: Path, flags: ArmVariants, *, flagged: Mapping[ArmRole, str]
) -> ArmVariants:
    """Both arms' variants as ``<out_dir>/<role>/arm_settings.json`` recorded them.

    ``flags`` are the variants the command line describes, and ``flagged``
    maps each role whose overlay flag was given to that flag's name.

    Raises:
        MeasurementPlanError: a settings file is unreadable, or an overlay flag
            names settings whose digest is not the one its arm recorded.
    """
    baseline, candidate = (
        _recorded_variant(out_dir / role, flags.for_role(role), flagged.get(role))
        for role in (ArmRole.BASELINE, ArmRole.CANDIDATE)
    )
    moved = (baseline.digest, candidate.digest) != (flags.baseline.digest, flags.candidate.digest)
    return ArmVariants(baseline=baseline, candidate=candidate, overlaid=flags.overlaid or moved)


def _recorded_variant(arm_dir: Path, from_flags: ArmVariant, flag: str | None) -> ArmVariant:
    path = arm_dir / ARM_SETTINGS_FILENAME
    if not path.is_file():
        return from_flags
    try:
        settings = read_arm_settings(arm_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MeasurementPlanError(
            f"{path} is unreadable ({exc}); expected the settings file a before/after arm "
            "writes, which --report-only re-renders the arm under"
        ) from exc
    recorded = ArmVariant.from_arm_settings(settings, source=f"recorded in {path}")
    if flag is None:
        return recorded
    _refuse_another_overlay(path, recorded, from_flags, flag)
    return replace(recorded, source=from_flags.source)


def _refuse_another_overlay(
    path: Path, recorded: ArmVariant, from_flags: ArmVariant, flag: str
) -> None:
    if from_flags.digest == recorded.digest:
        return
    raise MeasurementPlanError(
        f"{flag} {from_flags.source} names settings with digest {from_flags.digest}, but "
        f"{path} recorded {recorded.digest}; --report-only re-renders what the arm ran: "
        f"leave {flag} out, or pass the overlay it ran under"
    )


def accepted_because_it_already_ran(
    repo: Path, commit: CommitUnderTest, variant: ArmVariant
) -> ArmBlockAcceptance:
    """``--report-only``'s probe: asks nothing, checks nothing out, spawns nothing.

    WHY accept: the arm already ran under this variant, so whether its product
    takes the block is a settled fact, not a question; probing would cost a
    worktree and a child process per arm on a path that promises neither.
    """
    return ArmBlockAcceptance(role=commit.role, sha=commit.sha, verdict=ArmBlockVerdict.ACCEPTS)


__all__ = ("accepted_because_it_already_ran", "recorded_variants")
