"""How one number is written in a before/after table cell.

The report's table (``before_after_report``) and the comparison's
(``before_after_compare_text``) print their numbers alike, so a figure read in
one reads the same in the other. An undefined value's token is
``before_after_report_text.UNDEFINED_CELL``, which prose quotes too.
"""

from __future__ import annotations


def metric_cell(value: float) -> str:
    """A metric value: whole numbers bare, everything else to three decimals.

    Example:
        >>> metric_cell(4.0), metric_cell(0.66666)
        ('4', '0.667')
    """
    return f"{value:.0f}" if value == int(value) else f"{value:.3f}"


def p_value_cell(p_value: float) -> str:
    """A p-value at three significant figures, so a tiny one stays readable.

    Example:
        >>> p_value_cell(0.001953125)
        '0.00195'
    """
    return f"{p_value:.3g}"


__all__ = ("metric_cell", "p_value_cell")
