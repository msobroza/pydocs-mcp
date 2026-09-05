"""Closing-report skeleton generator (ADR 0020 §The closing report contract).

Emits the report's fixed structure with the numbers that already exist filled in
and every paid-arc number left as an explicit ``[TO BE MEASURED]`` slot. The report
**answers the pre-registered questions — it does not invent new ones**: the Phase 3
questions of ADR 0016 (§Pre-registered analysis plan) and the Phase 4 questions of
ADR 0018, referenced by ADR number. The one thing computable now with zero spend is
the **optimization trajectory** — proposed / valid / validity-rejected / gated /
accepted counts read straight from the candidate ledger, including the
zero-rollout-cost rejection count that demonstrates R3's validity firewall. Every
results table (resolve / localization / cost, per dev/val/test split) is a slot the
paid arc fills; test-layer rows exist only for the seed + one frozen config.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_eval.optimize.candidates.ledger import CandidateLedger

__all__ = ["TrajectoryCounts", "render_closing_report_skeleton", "trajectory_counts"]

_TBM = "[TO BE MEASURED]"


@dataclass(frozen=True, slots=True)
class TrajectoryCounts:
    """The zero-spend optimization-trajectory counts (ADR 0020 §Optimization trajectory).

    ``validity_rejected`` are the zero-rollout-cost firewall rejections (R3); ``gated``
    are candidates that reached a val gate; ``accepted`` are those the campaign's
    paired-exact rule accepted (supplied by the caller — the acceptance verdict is a
    decision, not a persisted ledger field).
    """

    proposed: int
    valid: int
    validity_rejected: int
    gated: int
    accepted: int


def trajectory_counts(
    ledger: CandidateLedger, *, accepted_hashes: frozenset[str] = frozenset()
) -> TrajectoryCounts:
    """Count proposed/valid/rejected/gated/accepted candidates from the ledger."""
    records = ledger.records()
    valid = [r for r in records if r.valid]
    return TrajectoryCounts(
        proposed=len(records),
        valid=len(valid),
        validity_rejected=len(records) - len(valid),
        gated=sum(1 for r in records if r.gate is not None),
        accepted=sum(1 for r in records if r.candidate_hash in accepted_hashes),
    )


def render_closing_report_skeleton(
    ledger: CandidateLedger, *, accepted_hashes: frozenset[str] = frozenset()
) -> str:
    """Render the closing-report skeleton: ledger-derived trajectory + unfilled slots."""
    counts = trajectory_counts(ledger, accepted_hashes=accepted_hashes)
    sections = [
        *_header(),
        *_trajectory(counts),
        *_results_layers(),
        *_qualitative(),
        *_headline_and_recommendation(),
    ]
    return "\n".join(sections).rstrip() + "\n"


def _header() -> list[str]:
    return [
        "# Phase 4 closing report (skeleton)",
        "",
        "Answers the PRE-REGISTERED questions only (ADR 0016 §Pre-registered analysis",
        "plan for dev/val; ADR 0018 for the val gate) — no post-hoc questions.",
        "",
    ]


def _trajectory(counts: TrajectoryCounts) -> list[str]:
    return [
        "## Optimization trajectory (from the candidate ledger)",
        "",
        f"- proposed: {counts.proposed}",
        f"- valid: {counts.valid}",
        f"- validity-rejected (zero rollout cost, R3): {counts.validity_rejected}",
        f"- gated (reached a val gate): {counts.gated}",
        f"- accepted (paired-exact rule, ADR 0018): {counts.accepted}",
        "",
    ]


def _results_layers() -> list[str]:
    """Three layers × three splits; test-layer rows only for seed + one."""
    layers = ("resolve (paired Δ, CI, McNemar p)", "localization (ADRs 0011/0012)", "cost")
    lines = ["## Results per stratum and split", ""]
    for split in ("dev", "val", "test (seed + one only)"):
        lines.append(f"### {split}")
        lines.extend(f"- {layer}: {_TBM}" for layer in layers)
        lines.append("")
    return lines


def _qualitative() -> list[str]:
    return [
        "## Qualitative diff analysis",
        "",
        f"Per-section seed → frozen-candidate diff (ADR 0017 lineage refs): {_TBM}",
        "",
    ]


def _headline_and_recommendation() -> list[str]:
    return [
        "## Headline and recommendation",
        "",
        f'- Contingency headline (if the test misses significance): "no detectable '
        f'difference at this power" — achieved power at measured π_d: {_TBM}',
        f"- Shipped-default recommendation (owner decides; incl. Phase 1 default-UX "
        f"smoke result): {_TBM}",
        "",
    ]
