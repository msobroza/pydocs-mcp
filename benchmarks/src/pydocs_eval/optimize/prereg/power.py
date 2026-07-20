"""Exact gate-power arithmetic for the pre-registration report (ADR 0018 §Evidence).

The registered ADR 0018 acceptance rule is the paired **one-sided** exact McNemar
test — accept iff ``mcnemar_exact_p_one_sided(b, c) <= alpha`` (the directional
upper tail in the candidate-better direction; ``b <= c`` gives ``p > 0.5``, never
significant). This is EXACTLY the rule the campaign gate applies
(:func:`~pydocs_eval.optimize.gepa_harness.acceptance.decide_acceptance`; the
frozen pre-registration pins ``gate_rule=paired_exact_mcnemar_one_sided``), so the
power tables here describe the real gate, not a proxy. Its realized type-I is
≈ ``alpha`` — conservative by the exact test's discreteness (0.038–0.043 at
N = 559, α = 0.05), NOT ``alpha/2``. This module computes, in closed form, the
EXACT probability that rule accepts a candidate whose true resolve-rate delta is
``delta`` on a paired val split of ``n_val`` instances with discordance ``pi_d``:

- ``false_accept_rate`` = P(accept | delta = 0) — the type-I error the gate holds
  at ≈ ``alpha`` (the one property strict-improvement and a raw margin lack).
- ``power_at`` = P(accept | delta) — honest-weak at Δ_min = 0.05 (0.67–0.98 at
  N = 559 depending on π_d).

The model (ADR 0016 §Statistics, reused verbatim): each instance is discordant
with prob ``pi_d``; among discordant pairs the candidate wins with
``p_win = 0.5 + delta/(2·pi_d)``. So ``n_disc ~ Binomial(n_val, pi_d)`` and, given
``n_disc``, ``b ~ Binomial(n_disc, p_win)`` with ``c = n_disc − b``. P(accept) is
the EXACT double sum over that joint — no simulation, no scipy. The per-(b, c)
accept boundary is the exact integer one-sided tail, **bit-identical** to
:func:`~pydocs_eval.metrics.aggregate.mcnemar_exact_p_one_sided`; the cross-pin
test in ``test_power.py`` asserts :func:`gate_accepts` equals the gate's predicate
on ``(b, c) ∈ [0..30]² × α ∈ {0.01, 0.05, 0.10}`` — the drift-class killer. A
delete+recompute is byte-stable.

WHY these numbers differ from the ADR 0018 body prose: the body describes a
two-sided operationalization (``mcnemar_exact_p(b,c) < alpha`` AND ``b > c``,
realized ≈ α/2) that drifted from the registered one-sided rule; it is superseded
by ADR 0018's dated 2026-07-20 amendment. The tables here reflect the registered
rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_eval.metrics.aggregate import mcnemar_sample_size

__all__ = [
    "PowerRow",
    "false_accept_rate",
    "gate_accept_prob",
    "gate_accepts",
    "power_at",
    "power_rows",
]


def gate_accept_prob(n_val: int, pi_d: float, delta: float, *, alpha: float) -> float:
    """Exact P(the registered one-sided rule accepts) at effect ``delta`` (ADR 0018).

    Sums the joint ``n_disc ~ Binomial(n_val, pi_d)`` × ``b ~ Binomial(n_disc,
    p_win)`` over every ``(n_disc, b)`` the gate accepts (one-sided exact
    ``mcnemar_exact_p_one_sided(b, c) <= alpha``, which implies ``b > c``). The
    one-sided tail is built with the EXACT integer binomial recurrence and both
    outer binomial tails INCREMENTALLY, so N = 559 is sub-second, not a 60 s
    ``math.comb`` blowup.

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


def gate_accepts(b: int, c: int, *, alpha: float) -> bool:
    """Whether the registered one-sided gate accepts the discordant split ``(b, c)``.

    EXACTLY :func:`~pydocs_eval.optimize.gepa_harness.acceptance.decide_acceptance`'s
    statistical predicate — ``mcnemar_exact_p_one_sided(b, c) <= alpha`` — computed
    through the SAME :func:`_critical_c` machinery :func:`gate_accept_prob` sums
    over, so a drift between the power tables and the live gate is a test failure,
    not a silent divergence. The cross-pin in ``test_power.py`` asserts this equals
    the gate's own predicate on ``(b, c) ∈ [0..30]² × α ∈ {0.01, 0.05, 0.10}``.

    Raises:
        ValueError: if ``b`` or ``c`` is negative (counts, not signed deltas), or
            ``alpha`` is outside ``(0, 1)``.
    """
    if b < 0 or c < 0:
        raise ValueError(f"discordant counts must be >= 0, got b={b!r}, c={c!r}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha!r}; it is a significance level")
    return _critical_c(b + c, alpha) >= c


def false_accept_rate(n_val: int, pi_d: float, *, alpha: float) -> float:
    """Type-I: P(accept | delta = 0). Held at ≈ ``alpha`` (conservative) by the one-sided rule."""
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
    ADR-pinned π_d = 0.10/0.20/0.30). ``powered_n`` comes from the z-form sizing
    function, NOT this module's exact tails, so it is unaffected by the one-sided
    acceptance-rule alignment (2026-07-20 amendment).
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
    """Largest minor-count ``c`` (with ``b = n_disc − c``) the one-sided gate accepts.

    The registered rule accepts iff ``mcnemar_exact_p_one_sided(b, c) <= alpha``;
    that one-sided tail ``Σ_{i=0}^{c} C(n_disc, i) / 2**n_disc`` is monotone
    increasing in ``c`` for fixed ``n_disc``, so the accepted ``c`` form a
    down-closed interval ``[0, crit]``. The tail is accumulated with the EXACT
    integer binomial recurrence — the same ``tail / 2**n`` division
    :func:`mcnemar_exact_p_one_sided` performs, so the decision is bit-identical to
    the live gate — and the scan breaks at the first non-significant ``c``. Returns
    -1 when no directional split clears ``alpha``. The ``c < n_disc − c`` guard is a
    safe scan bound: for ``alpha < 0.5`` any accepted ``c`` already has ``c <
    n_disc/2`` (the ``b > c`` half), so the guard never excludes a significant split.
    """
    if n_disc == 0:
        return -1
    denom = 2**n_disc
    comb = 1  # C(n_disc, 0)
    tail = comb  # integer Σ_{i=0}^{0} C(n_disc, i)
    crit = -1
    c = 0
    while c < n_disc - c:  # b = n_disc - c > c: the improvement half
        if tail / denom <= alpha:
            crit = c
        else:
            break
        c += 1
        comb = comb * (n_disc - c + 1) // c
        tail += comb
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
