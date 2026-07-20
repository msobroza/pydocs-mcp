"""Pre-registration power/false-accept report generator (ADR 0018 action item 6).

Given a FILLED registration, emit the owner-budget-checkpoint's power table from
CODE — false-accept, power at Δ_min, and the ``mcnemar_sample_size`` powered N per
π_d — plus the family-wise disclosure ``1 − (1 − α/2)^G`` at the realized G. The
point is that the budget checkpoint reads these numbers off a re-runnable computer,
not off hand math.

The chosen rule is the paired exact McNemar test; the report reflects the
pre-registered α, Δ_min, and N_val verbatim. It refuses to render a power table
while ``pi_d`` (the row selector) is unfilled — the honest headline is "measure
π_d first", not a fabricated row.
"""

from __future__ import annotations

from pydocs_eval.optimize.prereg.config import PreRegistration
from pydocs_eval.optimize.prereg.power import PowerRow, power_rows

__all__ = ["family_wise_false_accept", "render_power_report"]


def family_wise_false_accept(alpha: float, g_gate_evals: int) -> float:
    """``1 − (1 − α/2)^G`` — the chance ≥1 null candidate is accepted over G gates.

    The per-gate control is ≈ α/2; over G sequential screening gates the family-wise
    false-accept compounds, which is WHY the val gate is screening-only and the
    frozen test is the sole confirmatory contrast (ADR 0018 §Decision).

    Raises:
        ValueError: if ``g_gate_evals`` < 0 — a gate count, not a signed delta.
    """
    if g_gate_evals < 0:
        raise ValueError(f"g_gate_evals must be >= 0, got {g_gate_evals!r}")
    return 1.0 - (1.0 - alpha / 2) ** g_gate_evals


def render_power_report(prereg: PreRegistration, pi_ds: tuple[float, ...]) -> str:
    """Render the power/false-accept table for ``pi_ds`` under the frozen registration.

    Uses the registration's own ``alpha``/``delta_min``/``n_val`` so the report can
    never drift from the pre-registered rule. Appends the family-wise disclosure
    only when ``g_gate_evals`` is filled (else it says so).

    Raises:
        ValueError: if ``pi_ds`` is empty — a table needs at least one row.
    """
    if not pi_ds:
        raise ValueError("pi_ds must name >= 1 discordance value, got ()")
    rows = power_rows(prereg.n_val, pi_ds, alpha=prereg.alpha, delta_min=prereg.delta_min)
    lines = [*_header(prereg), *_table(rows), *_family_wise(prereg)]
    return "\n".join(lines) + "\n"


def _header(prereg: PreRegistration) -> list[str]:
    return [
        "campaign pre-registration power report (ADR 0018 — paired exact McNemar)",
        f"registration: v{prereg.version}  rule={prereg.gate_rule}",
        f"alpha={prereg.alpha:.3f} (one-sided)  delta_min={prereg.delta_min:.3f}  "
        f"N_val={prereg.n_val}",
        "",
        f"{'pi_d':>6}  {'false_accept':>12}  {'power@dmin':>10}  {'powered_N':>9}",
    ]


def _table(rows: tuple[PowerRow, ...]) -> list[str]:
    return [_row(row) for row in rows]


def _row(row: PowerRow) -> str:
    return f"{row.pi_d:>6.2f}  {row.false_accept:>12.4f}  {row.power:>10.4f}  {row.powered_n:>9d}"


def _family_wise(prereg: PreRegistration) -> list[str]:
    g = prereg.g_gate_evals
    if g is None:
        return ["", "family-wise false-accept over G gates: G [TO BE MEASURED]"]
    fw = family_wise_false_accept(prereg.alpha, g)
    return [
        "",
        f"family-wise false-accept 1-(1-alpha/2)^G at G={g}: {fw:.4f} "
        "(val gate is screening-only; frozen test is the sole confirmatory contrast)",
    ]
