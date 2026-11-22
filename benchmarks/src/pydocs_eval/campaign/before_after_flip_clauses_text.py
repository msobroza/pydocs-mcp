"""How the guidance-task flip clauses print in the comparison (#391): rendering only.

The findings and the rows arrive computed (``before_after_flip_clauses``). This
module words the three additions the mode makes to ``before_after_compare_text``:
the clauses named on the ``rule:`` line, one indented line per finding under a
variant's verdict, and the pre-registered rows, reported and not gating.
"""

from __future__ import annotations

from pydocs_eval.campaign.before_after_compare import Comparison, VariantComparison
from pydocs_eval.campaign.before_after_flip_clauses import (
    CALLS_AFTER_FIRST_GOLD_LABEL,
    CALLS_AFTER_FIRST_GOLD_READ_LABEL,
    CLAUSE_ORDER,
    SEARCH_CALLS_LABEL,
    TOKENS_IN_PER_TASK_LABEL,
    TURNS_AFTER_NEEDLE_LABEL,
    UNCACHED_PER_TURN_LABEL,
    UNCACHED_TOTAL_LABEL,
    FlipClause,
    FlipClauseMode,
    FlipClauseRows,
    clause_label,
    pair_text,
)
from pydocs_eval.trajectory.ask_outcome import TaskOutcome

PRE_REGISTERED_HEADING = "### Pre-registered rows (reported, not gating)"
_TURNS_BEFORE_NEEDLE_LABEL = "turns before the needle (the headline less turns after needle)"


def flip_clause_rule_text(mode: FlipClauseMode) -> str:
    """The clauses as the ``rule:`` line names them, after the standard rule.

    Example:
        >>> flip_clause_rule_text(FlipClauseMode(noise_edge=0.6)).endswith(
        ...     "(c) minimum gain (noise edge 0.600), (a) tokens"
        ... )
        True
    """
    edge = (
        "no noise edge given: not evaluated"
        if mode.noise_edge is None
        else f"noise edge {mode.noise_edge:.3f}"
    )
    named = ", ".join(
        f"{clause_label(clause)} ({edge})"
        if clause is FlipClause.MINIMUM_GAIN
        else clause_label(clause)
        for clause in CLAUSE_ORDER
    )
    return (
        "then the guidance-task flip clauses, for this experiment only and never changing "
        f"the verdict: {named}"
    )


def flip_finding_lines(variant: VariantComparison) -> list[str]:
    """One indented line per clause finding, in reading order; none without the mode."""
    return [f"  - {finding.text()}" for finding in variant.flip_findings]


def pre_registered_lines(comparison: Comparison) -> list[str]:
    """The rows the experiment registered in advance, per variant; none without the mode."""
    if comparison.flip_clauses is None:
        return []
    blocks = [
        _variant_block(comparison.baseline.label, variant.arm.label, rows)
        for variant in comparison.variants
        if (rows := variant.flip_rows) is not None
    ]
    return ["", PRE_REGISTERED_HEADING, "", *(line for block in blocks for line in block)]


def _variant_block(baseline_label: str, variant_label: str, rows: FlipClauseRows) -> list[str]:
    """One variant's pre-registered rows, each as ``label baseline → variant``."""
    named = (
        f"{pair_text(SEARCH_CALLS_LABEL, rows.search_calls)} (expected: not up)",
        pair_text(TURNS_AFTER_NEEDLE_LABEL, rows.turns_after_needle),
        pair_text(CALLS_AFTER_FIRST_GOLD_LABEL, rows.calls_after_first_gold),
        pair_text(CALLS_AFTER_FIRST_GOLD_READ_LABEL, rows.calls_after_first_gold_read),
        pair_text(_TURNS_BEFORE_NEEDLE_LABEL, rows.turns_before_needle),
        pair_text(TOKENS_IN_PER_TASK_LABEL, rows.tokens_in_per_task),
        pair_text(UNCACHED_TOTAL_LABEL, rows.uncached_tokens_in_total),
        pair_text(UNCACHED_PER_TURN_LABEL, rows.uncached_tokens_in_per_turn),
        _outcomes_text(rows),
    )
    header = f"- `{variant_label}` against `{baseline_label}`, baseline → variant:"
    return [header, *(f"  - {line}" for line in named)]


def _outcomes_text(rows: FlipClauseRows) -> str:
    """The outcome tally, one count per outcome, every outcome named."""
    counts = rows.outcome_counts
    tally = ", ".join(counts[outcome].text(outcome) for outcome in TaskOutcome)
    return f"outcomes: {tally}"


__all__ = (
    "PRE_REGISTERED_HEADING",
    "flip_clause_rule_text",
    "flip_finding_lines",
    "pre_registered_lines",
)
