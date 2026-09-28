"""How a number is written in a before/after table cell — the report's and the comparison's."""

from __future__ import annotations

from pydocs_eval.campaign.before_after_table_cells import (
    UNDEFINED_CELL,
    metric_cell,
    p_value_cell,
)


def test_a_whole_number_prints_bare_and_anything_else_to_three_decimals() -> None:
    assert (metric_cell(4.0), metric_cell(0.66666), metric_cell(-0.5)) == ("4", "0.667", "-0.500")


def test_a_p_value_keeps_three_significant_figures() -> None:
    assert (p_value_cell(0.001953125), p_value_cell(0.5), p_value_cell(1.0)) == (
        "0.00195",
        "0.5",
        "1",
    )


def test_an_undefined_value_reads_n_a_never_zero() -> None:
    assert UNDEFINED_CELL == "n/a"
