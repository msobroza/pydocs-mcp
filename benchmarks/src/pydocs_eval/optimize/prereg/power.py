"""Exact gate-power arithmetic for the pre-registration report (ADR 0018 §Evidence).

The ADR 0018 acceptance rule is the paired exact McNemar test — accept iff
``mcnemar_exact_p(b, c) < alpha`` AND ``b > c`` (a one-sided directional signal
with realized type-I ≈ α/2). This module computes, in closed form, the EXACT
probability that rule accepts a candidate whose true resolve-rate delta is
``delta`` on a paired val split of ``n_val`` instances with discordance ``pi_d``:

- ``false_accept_rate`` = P(accept | delta = 0) — the type-I error the gate holds
  at ≈ α/2 (the one property strict-improvement and a raw margin lack).
- ``power_at`` = P(accept | delta) — honest-weak at Δ_min = 0.05 (0.55–0.96 at
  N = 559 depending on π_d).

The model (ADR 0016 §Statistics, reused verbatim): each instance is discordant
with prob ``pi_d``; among discordant pairs the candidate wins with
``p_win = 0.5 + delta/(2·pi_d)``. So ``n_disc ~ Binomial(n_val, pi_d)`` and, given
``n_disc``, ``b ~ Binomial(n_disc, p_win)`` with ``c = n_disc − b``. P(accept) is
the EXACT double sum over that joint — no simulation, no scipy, only the repo's
own :func:`~pydocs_eval.metrics.aggregate.mcnemar_exact_p`. Reproduces the
gate-power evidence tables (``2026-07-20-phase4-evidence-gate-power-costs.md`` §1)
bit-for-bit; a delete+recompute is byte-stable.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_eval.metrics.aggregate import mcnemar_sample_size

__all__ = [
    "PowerRow",
    "false_accept_rate",
    "gate_accept_prob",
    "power_at",
    "power_rows",
]


def gate_accept_prob(n_val: int, pi_d: float, delta: float, *, alpha: float) -> float:
    """Exact P(the paired-exact rule accepts) at effect ``delta`` (ADR 0018).

    Sums the joint ``n_disc ~ Binomial(n_val, pi_d)`` × ``b ~ Binomial(n_disc,
    p_win)`` over every ``(n_disc, b)`` the rule accepts (``b > c`` AND
    ``mcnemar_exact_p(b, c) < alpha``). Both binomial tails are built INCREMENTALLY
    so N = 559 is sub-second, not a 60 s ``math.comb`` blowup.

    Raises:
        ValueError: if ``n_val`` < 0, or ``pi_d``/``alpha`` fall outside (0, 1),
            or ``p_win`` leaves [0, 1] (``delta`` too large for this ``pi_d``).
    """
    _validate(n_val, pi_d, delta, alpha)
    p_win = 0.5 + delta / (2 * pi_d)
    p_disc = _binom_pmf_zero(n_val, pi_d)  # P(n_disc = 0)
    total = 0.0
    for n_disc in range(n_val + 1):
        crit = _critical_c(n_disc, alpha)
        if crit >= 0:
            total += p_disc * _accept_mass(n_disc, crit, p_win)
        p_disc = _next_binom_pmf(p_disc, n_val, n_disc, pi_d)
    return total


def false_accept_rate(n_val: int, pi_d: float, *, alpha: float) -> float:
    """Type-I: P(accept | delta = 0). Held at ≈ α/2 by the paired-exact rule."""
    return gate_accept_prob(n_val, pi_d, 0.0, alpha=alpha)


def power_at(n_val: int, pi_d: float, delta: float, *, alpha: float) -> float:
    """Power: P(accept | true effect = ``delta``). Honest-weak at Δ_min = 0.05."""
    return gate_accept_prob(n_val, pi_d, delta, alpha=alpha)


@dataclass(frozen=True, slots=True)
class PowerRow:
    """One (π_d) row of the pre-registration power table (ADR 0018 §Decision).

    ``false_accept`` is the null-candidate type-I; ``power`` is P(accept) at
    ``delta_min``; ``powered_n`` is the per-cell N ``mcnemar_sample_size`` needs to
    reach 0.80 power at ``delta_min`` (the mult-of-12 ceiling, 300/624/936 at the
    ADR-pinned π_d = 0.10/0.20/0.30).
    """

    pi_d: float
    false_accept: float
    power: float
    powered_n: int


def power_rows(
    n_val: int, pi_ds: tuple[float, ...], *, alpha: float, delta_min: float
) -> tuple[PowerRow, ...]:
    """Build one :class:`PowerRow` per ``pi_d`` for the report generator (item 3)."""
    return tuple(_power_row(n_val, pi_d, alpha=alpha, delta_min=delta_min) for pi_d in pi_ds)


def _power_row(n_val: int, pi_d: float, *, alpha: float, delta_min: float) -> PowerRow:
    _, _, _, powered_n = mcnemar_sample_size(delta_min, pi_d, alpha=alpha)
    return PowerRow(
        pi_d=pi_d,
        false_accept=false_accept_rate(n_val, pi_d, alpha=alpha),
        power=power_at(n_val, pi_d, delta_min, alpha=alpha),
        powered_n=powered_n,
    )


def _validate(n_val: int, pi_d: float, delta: float, alpha: float) -> None:
    if n_val < 0:
        raise ValueError(f"n_val must be >= 0, got {n_val!r}")
    if not 0.0 < pi_d < 1.0:
        raise ValueError(f"pi_d must be in (0, 1), got {pi_d!r}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha!r}")
    p_win = 0.5 + delta / (2 * pi_d)
    if not 0.0 <= p_win <= 1.0:
        raise ValueError(
            f"delta={delta!r} with pi_d={pi_d!r} gives p_win={p_win!r}, "
            f"expected p_win in [0, 1] (delta must satisfy |delta| <= pi_d)"
        )


def _critical_c(n_disc: int, alpha: float) -> int:
    """Largest minor-count ``c`` (with ``b = n_disc − c > c``) still exact-significant.

    ``mcnemar_exact_p`` is monotone increasing in ``c`` for fixed ``n_disc``, so the
    fair-coin tail is accumulated incrementally and the scan breaks at the first
    non-significant ``c``. Returns -1 when no directional split clears ``alpha``.
    """
    if n_disc == 0:
        return -1
    pmf = 0.5**n_disc  # P(X = 0) under the null fair coin
    tail = pmf
    crit = -1
    c = 0
    while c < n_disc - c:  # enforce b = n_disc - c > c (the directional half)
        if 2.0 * tail < alpha:
            crit = c
        else:
            break
        c += 1
        pmf *= (n_disc - c + 1) / c
        tail += pmf
    return crit


def _accept_mass(n_disc: int, crit: int, p_win: float) -> float:
    """Σ_{c=0..crit} Binomial(n_disc, b = n_disc − c; p_win) — the accepted b's."""
    pmf = p_win**n_disc  # b = n_disc (c = 0)
    total = pmf
    for c in range(1, crit + 1):
        b = n_disc - c + 1  # stepping b -> b-1
        pmf *= b / (n_disc - b + 1) * (1 - p_win) / p_win
        total += pmf
    return total


def _binom_pmf_zero(n: int, p: float) -> float:
    """Binomial P(k = 0) = (1 − p)ⁿ, the incremental-tail seed."""
    return (1 - p) ** n


def _next_binom_pmf(pmf_k: float, n: int, k: int, p: float) -> float:
    """Advance a Binomial(n, p) pmf from k to k+1 by the ratio recurrence."""
    if k >= n:
        return 0.0
    return pmf_k * (n - k) / (k + 1) * p / (1 - p)
