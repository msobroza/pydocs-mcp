"""The comparison as a markdown block fit to post on the umbrella issue (#372).

Rendering only: the numbers and the verdicts arrive computed
(``before_after_compare``, ``before_after_acceptance``). The table pairs each
variant's estimates with its Holm-adjusted p; under it, one line per variant
names its verdict, the rule that decided it and every reason the rule read.
"""

from __future__ import annotations

from pydocs_eval.campaign.before_after import SHORT_SHA_CHARS
from pydocs_eval.campaign.before_after_acceptance import (
    COMPLETENESS_TURN_ALLOWANCE,
    Decision,
    PointPair,
    Verdict,
)
from pydocs_eval.campaign.before_after_compare import (
    Comparison,
    LabelledArm,
    PairedTest,
    VariantComparison,
)
from pydocs_eval.campaign.before_after_report_text import (
    UNDEFINED_CELL,
    metric_cell,
    p_value_cell,
)

_SIGN_OFF = "Owner sign-off required: record it in the umbrella issue before adopting the step."
_ESTIMATE_COLUMNS = (
    "penalised turns",
    "budget exhausted",
    "needle cited",
    "gold-site coverage",
)
_COLUMNS = ("variant", "pairs", *_ESTIMATE_COLUMNS, *PairedTest, "verdict")
_STANDARD_RULE = (
    "standard (Q10): penalised turns-to-answer down, budget exhaustion not up, "
    "needle cited within its band (and gold-site coverage within its own on multi-site tasks)"
)


def render_comparison(comparison: Comparison, *, split: str) -> str:
    """What was compared, under which band and rule, the table, and each verdict."""
    return "\n".join(
        [
            "## Before/after comparison",
            "",
            *_provenance_lines(comparison, split),
            "",
            "| " + " | ".join(_COLUMNS) + " |",
            "|" + "---|" * len(_COLUMNS),
            *(_variant_row(variant) for variant in comparison.variants),
            "",
            *(_verdict_line(variant) for variant in comparison.variants),
        ]
    )


def _provenance_lines(comparison: Comparison, split: str) -> list[str]:
    band, variants = comparison.band, len(comparison.variants)
    return [
        f"- split: `{split}`",
        f"- baseline: {_arm_line(comparison.baseline)}",
        f"- A/A replicate: {_replicate_line(comparison.replicate)}",
        f"- {_band_line('needle cited', band.needle_cited)}",
        f"- {_band_line('gold-site coverage', band.gold_site_coverage)}",
        f"- rule: {_rule_line(comparison.completeness_arm)}",
        "- p: one-sided Wilcoxon signed-rank (turns, calls), McNemar exact two-sided "
        f"(needle reached, needle cited), Holm-adjusted across {variants} variant(s)",
    ]


def _arm_line(arm: LabelledArm) -> str:
    sha = arm.metrics.commit.sha[:SHORT_SHA_CHARS]
    return f"`{arm.label}` (`{sha}`), {arm.metrics.trajectories} task(s)"


def _replicate_line(replicate: LabelledArm | None) -> str:
    if replicate is None:
        return "none given, so each band is its one-task floor"
    return _arm_line(replicate)


def _band_line(name: str, band: float | None) -> str:
    if band is None:
        return f"{name} band {UNDEFINED_CELL} (the baseline defines it for no task)"
    return f"{name} band {band:.3f} (the larger of the A/A difference and one task)"


def _rule_line(completeness_arm: bool) -> str:
    if not completeness_arm:
        return _STANDARD_RULE
    return (
        "bounded step-8 (Q44) first: gold-site coverage up and penalised turns up by at most "
        f"+{COMPLETENESS_TURN_ALLOWANCE}, adopted on the owner's sign-off; otherwise the "
        f"{_STANDARD_RULE}"
    )


def _variant_row(variant: VariantComparison) -> str:
    estimates = variant.estimates
    cells = [
        f"`{variant.arm.label}`",
        str(variant.pairs),
        _pair_cell(estimates.penalised_turns),
        _pair_cell(estimates.budget_exhausted),
        _pair_cell(estimates.needle_cited),
        _pair_cell(estimates.gold_site_coverage),
        *(_p_cell(variant.p_values[test]) for test in PairedTest),
        _verdict_cell(variant.decision),
    ]
    return "| " + " | ".join(cells) + " |"


def _pair_cell(pair: PointPair | None) -> str:
    if pair is None:
        return UNDEFINED_CELL
    return f"{metric_cell(pair.baseline)} → {metric_cell(pair.variant)}"


def _p_cell(p_value: float | None) -> str:
    return UNDEFINED_CELL if p_value is None else p_value_cell(p_value)


def _verdict_cell(decision: Decision) -> str:
    if decision.verdict is Verdict.NO_VERDICT:
        return str(decision.verdict)
    return f"{decision.verdict} ({decision.rule})"


def _verdict_line(variant: VariantComparison) -> str:
    decision = variant.decision
    line = f"- `{variant.arm.label}`: {_verdict_cell(decision)}: {'; '.join(decision.reasons)}."
    return f"{line} {_SIGN_OFF}" if decision.owner_sign_off else line


__all__ = ("render_comparison",)
