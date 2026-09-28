"""How one number is written in a before/after table cell.

The report's table (``before_after_report``) and the comparison's
(``before_after_compare_text``) print their numbers alike, so a figure read in
one reads the same in the other, and both print an undefined value as
:data:`UNDEFINED_CELL`, the token the report's prose quotes too.
"""

from __future__ import annotations

#: How a table prints an undefined value, and how the prose around it quotes that cell.
UNDEFINED_CELL = "n/a"


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


__all__ = ("UNDEFINED_CELL", "metric_cell", "p_value_cell")
