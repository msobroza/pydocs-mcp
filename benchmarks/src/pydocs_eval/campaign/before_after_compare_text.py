"""The comparison as a markdown block fit to post on the umbrella issue (#372).

Rendering only: the numbers and the verdicts arrive computed
(``before_after_compare``, ``before_after_acceptance``). The table pairs each
variant's estimates with its Holm-adjusted p; under it, one line per variant
names its verdict, the rule that decided it and every reason the rule read, and
a verdict the bounded step-8 rule reached gets the owner's sign-off line.
"""

from __future__ import annotations

from collections.abc import Callable

from pydocs_eval.campaign.before_after import SHORT_SHA_CHARS
from pydocs_eval.campaign.before_after_acceptance import (
    COMPLETENESS_RULE_SUMMARY,
    STANDARD_RULE_SUMMARY,
    PointPair,
    VariantDecision,
    VariantEstimates,
    VariantVerdict,
)
from pydocs_eval.campaign.before_after_compare import (
    Comparison,
    LabelledArm,
    PairedTest,
    VariantComparison,
)
from pydocs_eval.campaign.before_after_report_text import UNDEFINED_CELL
from pydocs_eval.campaign.before_after_table_cells import metric_cell, p_value_cell

_SIGN_OFF = "Owner sign-off required: record it in the umbrella issue before adopting the step."

# Each estimate column and the estimate it prints — one table, so the header
# and every row can never fall out of step.
_ESTIMATE_COLUMNS: tuple[tuple[str, Callable[[VariantEstimates], PointPair | None]], ...] = (
    ("penalised turns", lambda estimates: estimates.penalised_turns),
    ("budget exhausted", lambda estimates: estimates.budget_exhausted),
    ("needle cited", lambda estimates: estimates.needle_cited),
    ("gold-site coverage", lambda estimates: estimates.gold_site_coverage),
)
_COLUMNS = (
    "variant",
    "pairs",
    *(label for label, _read in _ESTIMATE_COLUMNS),
    *PairedTest,
    "verdict",
)


def render_comparison(comparison: Comparison, *, split: str) -> str:
    """What was compared, under which band and rule, the table, and each verdict.

    Example:
        >>> print(render_comparison(comparison, split="repoqa-qa/dev"))  # doctest: +SKIP
        ## Before/after comparison
        ...
    """
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
            *(line for variant in comparison.variants for line in _verdict_lines(variant)),
        ]
    )


def _provenance_lines(comparison: Comparison, split: str) -> list[str]:
    band, variants = comparison.band, len(comparison.variants)
    rule = STANDARD_RULE_SUMMARY
    if comparison.completeness_arm:
        rule = f"{COMPLETENESS_RULE_SUMMARY}, {STANDARD_RULE_SUMMARY}"
    return [
        f"- split: `{split}`",
        f"- baseline: {_arm_line(comparison.baseline)}",
        f"- A/A replicate: {_arm_line(comparison.replicate)}",
        f"- {_band_line('needle cited', band.needle_cited)}",
        f"- {_band_line('gold-site coverage', band.gold_site_coverage)}",
        f"- rule: {rule}",
        "- p: one-sided Wilcoxon signed-rank (turns, calls), McNemar exact two-sided "
        f"(needle reached, needle cited), Holm-adjusted across {variants} variant(s)",
    ]


def _arm_line(arm: LabelledArm) -> str:
    sha = arm.metrics.commit.sha[:SHORT_SHA_CHARS]
    return f"`{arm.label}` (`{sha}`), {arm.metrics.trajectories} task(s)"


def _band_line(name: str, band: float | None) -> str:
    if band is None:
        return f"{name} band {UNDEFINED_CELL}: the A/A pair shares no task that defines it"
    return f"{name} band {band:.3f} (the larger of the A/A difference and one task)"


def _variant_row(variant: VariantComparison) -> str:
    cells = [
        f"`{variant.arm.label}`",
        str(variant.pairs),
        *(_pair_cell(read(variant.estimates)) for _label, read in _ESTIMATE_COLUMNS),
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


def _verdict_cell(decision: VariantDecision) -> str:
    if decision.verdict is VariantVerdict.NO_VERDICT:
        return str(decision.verdict)
    return f"{decision.verdict} ({decision.rule})"


def _verdict_lines(variant: VariantComparison) -> list[str]:
    decision = variant.decision
    reasons = "; ".join(decision.reasons)
    lines = [f"- `{variant.arm.label}`: {_verdict_cell(decision)}: {reasons}."]
    return [*lines, f"  - {_SIGN_OFF}"] if decision.owner_sign_off else lines


__all__ = ("render_comparison",)
