"""Bootstrap-CI aggregation for per-task metric values (spec §4.11).

Free functions, no class — aggregation has no state and no plug-in axis
to swap. If a second strategy is ever needed (e.g. studentized CI), add a
sibling function rather than retrofitting a Protocol.

The paired-design statistics half (ADR 0016 §Statistics) lives here too:
``mcnemar_exact_p`` (stdlib exact binomial on discordant counts),
``mcnemar_sample_size`` (the Δ_min-pinned Connor/Lachin power curve), and
``mcnemar_from_pairs`` (per-instance 0/1 arrays → the paired 2×2 + both CIs).
All stdlib-only — no scipy/statsmodels — matching the module's
dependency-free precedent (``random``/``math`` only).

Reference: https://en.wikipedia.org/wiki/Bootstrapping_(statistics)
Reference: Connor 1987, Biometrics 43:207-211; Lachin 2011 §5.7 (McNemar).
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence

# Single source of truth for the bootstrap resample count (CLAUDE.md
# §"Default values"): both the single-sample and paired helpers read it,
# so bumping the resolution is a one-line change with no drift between siblings.
_DEFAULT_BOOTSTRAP_ITER = 1000


def mean_with_bootstrap_ci(
    values: Sequence[float],
    *,
    n_resamples: int = _DEFAULT_BOOTSTRAP_ITER,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Return ``(mean, ci_low, ci_high)`` at 95% via percentile bootstrap.

    Empty input degrades to ``(0.0, 0.0, 0.0)`` so a metric with no eligible
    tasks (e.g. dataset filter excluded everything) does not abort the run.

    Determinism: ``seed`` fully controls the resampling — identical inputs
    plus identical seed yield bit-identical output across runs.

    Raises:
        ValueError: if ``n_resamples`` is not positive — 0 (or negative)
            looks like a natural "skip the CI" value to a programmatic
            caller, but would otherwise index into an empty resample list
            and raise a bare, uninformative ``IndexError``.
    """
    if n_resamples <= 0:
        raise ValueError(
            f"n_resamples must be a positive int, got {n_resamples!r}; "
            "there is no 0-resample bootstrap — call with the default "
            f"({_DEFAULT_BOOTSTRAP_ITER}) or omit n_resamples entirely"
        )
    if not values:
        return (0.0, 0.0, 0.0)

    n = len(values)
    mean = sum(values) / n

    # WHY: a fresh Random instance instead of touching the module-level RNG
    # keeps callers deterministic regardless of any other randomness in the
    # process (test parallelism, other libs reseeding). ``rng.choices``
    # samples with replacement in one C-level call, replacing what would
    # otherwise be n_resamples × n Python-level ``randrange`` invocations.
    rng = random.Random(seed)
    resample_means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(n_resamples))

    # WHY: symmetric inclusive percentile — 25 samples trimmed each tail at
    # n=1000, n=2.5%. ``high = n - 1 - low`` mirrors ``low`` around the median
    # so an asymmetry in indexing never biases the interval.
    low_idx = int(0.025 * n_resamples)
    high_idx = n_resamples - 1 - low_idx
    return (mean, resample_means[low_idx], resample_means[high_idx])


def paired_bootstrap_ci(
    values_a: Sequence[float],
    values_b: Sequence[float],
    *,
    n_resamples: int = _DEFAULT_BOOTSTRAP_ITER,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Return ``(mean_diff, ci_low, ci_high)`` for the paired difference ``a - b``.

    ``values_a`` and ``values_b`` are per-task scores for two systems over the
    SAME tasks (index ``i`` is the same task in both). Used to answer "is system
    A better than system B on the same tasks?" with a 95% percentile bootstrap.

    WHY paired: the two systems are correlated through the task — a hard task
    drags both scores down, an easy task lifts both. Resampling ``a`` and ``b``
    independently would shatter that correlation and inflate the variance of the
    difference, producing a falsely WIDE (and statistically wrong) CI. So each
    resample draws ONE set of task indices with replacement and applies those
    SAME indices to both arrays before differencing the means. Preserving the
    pairing yields the tighter, correct interval for the head-to-head question.

    ``mean_diff`` is the point estimate ``mean(a) - mean(b)`` on the original
    data (not resampled), so it is exact regardless of ``n_resamples``.

    Empty input degrades to ``(0.0, 0.0, 0.0)`` — matching the sibling's
    degrade-don't-abort philosophy when a metric has no eligible tasks. A length
    mismatch, however, raises ``ValueError``: a paired test on unequal-length
    series is undefined (the pairing has no meaning), i.e. a caller bug, not a
    degrade case.

    Determinism: ``seed`` fully controls the resampling — identical inputs plus
    identical seed yield bit-identical output across runs.

    Raises:
        ValueError: if ``n_resamples`` is not positive — same rationale as
            the single-sample sibling: 0 would otherwise index into an
            empty resample list and raise a bare ``IndexError``.
    """
    if len(values_a) != len(values_b):
        raise ValueError(
            "paired_bootstrap_ci requires equal-length series "
            f"(got {len(values_a)} and {len(values_b)}); pairing is undefined "
            "for unequal lengths"
        )
    if n_resamples <= 0:
        raise ValueError(
            f"n_resamples must be a positive int, got {n_resamples!r}; "
            "there is no 0-resample bootstrap — call with the default "
            f"({_DEFAULT_BOOTSTRAP_ITER}) or omit n_resamples entirely"
        )
    if not values_a:
        return (0.0, 0.0, 0.0)

    n = len(values_a)
    mean_diff = sum(values_a) / n - sum(values_b) / n

    # WHY: one ``rng.choices(range(n), ...)`` call per resample yields the shared
    # index set; reusing ``idx`` for both arrays is what keeps the pairing intact
    # (see the docstring). A fresh Random isolates determinism from any other RNG
    # use in the process, exactly as the single-sample sibling does.
    rng = random.Random(seed)
    resample_diffs = sorted(
        sum(values_a[i] for i in idx) / n - sum(values_b[i] for i in idx) / n
        for idx in (rng.choices(range(n), k=n) for _ in range(n_resamples))
    )

    # Same symmetric inclusive percentile indexing as ``mean_with_bootstrap_ci``.
    low_idx = int(0.025 * n_resamples)
    high_idx = n_resamples - 1 - low_idx
    return (mean_diff, resample_diffs[low_idx], resample_diffs[high_idx])


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile, deterministic. Empty → 0.0.

    ``q`` is in [0, 1]. Matches numpy.percentile's default ``linear``
    interpolation method so external comparisons stay sane.

    Used by ``runner.run_sweep`` to aggregate per-task latency observations
    (``indexing_seconds`` / ``search_seconds``) into p50/p95/p99 triples.
    The semantic disambiguation between ``(mean, ci_low, ci_high)`` quality
    aggregates and ``(p50, p95, p99)`` latency aggregates is by metric-name
    suffix convention: any name ending ``_seconds`` → percentile.
    """
    if not values:
        return 0.0
    s = sorted(values)
    k = q * (len(s) - 1)
    f = int(k)
    if f >= len(s) - 1:
        return s[-1]
    frac = k - f
    return s[f] + frac * (s[f + 1] - s[f])


# --- Exact McNemar test (ADR 0016 §Statistics) ---------------------------


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact-binomial McNemar p-value on discordant counts ``b, c``.

    ``b`` = pairs where arm A resolves and arm B does not; ``c`` = the reverse
    (ADR 0016's 2×2 convention). Under H0 (no arm effect) each of the
    ``n = b + c`` discordant pairs is a fair coin, so the two-sided p is
    ``min(1, 2·Σ_{i=0}^{min(b,c)} C(n, i)·0.5ⁿ)`` — an exact binomial tail via
    ``math.comb``, no normal approximation and no scipy. ``b = c = 0`` (no
    discordant pairs, no signal) returns ``1.0``.

    Raises:
        ValueError: if ``b`` or ``c`` is negative — counts, not signed deltas.
    """
    if b < 0 or c < 0:
        raise ValueError(f"discordant counts must be >= 0, got b={b!r}, c={c!r}")
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1))
    return min(1.0, 2.0 * tail / (2**n))


def mcnemar_exact_p_one_sided(b: int, c: int) -> float:
    """One-sided exact-binomial McNemar p-value for H1: arm A resolves MORE than arm B.

    ``b`` = pairs where A resolves and B does not; ``c`` = the reverse (same 2×2
    convention as :func:`mcnemar_exact_p`). Under H0 the number of A-wins is
    ``Binomial(n, 0.5)`` with ``n = b + c``, so the one-sided p in the A-better
    direction is the upper tail ``P(X >= b) = Σ_{i=0}^{c} C(n, i)·0.5ⁿ`` (equal by
    the binomial symmetry ``C(n, i) = C(n, n−i)``).

    This is NOT ``mcnemar_exact_p / 2``: halving the two-sided p is only correct
    when ``b >= c`` and would falsely report a small p when ``b < c`` (arm A did
    WORSE). Here ``b < c`` correctly yields ``p > 0.5`` — no evidence of
    improvement — because the tail then covers more than half the mass. ``b = c``
    gives ``p >= 0.5`` (a tie is not improvement); ``b = c = 0`` returns ``1.0``.

    Used by the ADR 0018 paired-exact acceptance gate (accept iff ``p <= alpha``),
    where A = candidate, B = incumbent.

    Raises:
        ValueError: if ``b`` or ``c`` is negative — counts, not signed deltas.
    """
    if b < 0 or c < 0:
        raise ValueError(f"discordant counts must be >= 0, got b={b!r}, c={c!r}")
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(c + 1))
    return min(1.0, tail / (2**n))


# --- Δ_min-pinned McNemar sample size (ADR 0016 §Evidence power curve) ----

# WHY hardcoded z-quantiles are NOT used: alpha/power are parameters, so we
# invert the normal CDF (stdlib only). Acklam's rational approximation seeds a
# single Halley step refined through math.erfc, reaching double precision and
# reproducing the ADR's pinned z_{0.975}=1.959963985 / z_{0.80}=0.841621234.
_ACKLAM_A = (
    -3.969683028665376e01,
    2.209460984245205e02,
    -2.759285104469687e02,
    1.383577518672690e02,
    -3.066479806614716e01,
    2.506628277459239e00,
)
_ACKLAM_B = (
    -5.447609879822406e01,
    1.615858368580409e02,
    -1.556989798598866e02,
    6.680131188771972e01,
    -1.328068155288572e01,
)
_ACKLAM_C = (
    -7.784894002430293e-03,
    -3.223964580411365e-01,
    -2.400758277161838e00,
    -2.549732539343734e00,
    4.374664141464968e00,
    2.938163982698783e00,
)
_ACKLAM_D = (
    7.784695709041462e-03,
    3.224671290700398e-01,
    2.445134137142996e00,
    3.754408661907416e00,
)
_ACKLAM_PLOW = 0.02425


def _horner(coeffs: tuple[float, ...], x: float) -> float:
    """Evaluate a polynomial with ``coeffs`` high-degree-first at ``x``."""
    acc = 0.0
    for coeff in coeffs:
        acc = acc * x + coeff
    return acc


def _acklam_ppf(p: float) -> float:
    """Acklam's rational-approximation inverse standard-normal CDF (initial)."""
    if p < _ACKLAM_PLOW:
        q = math.sqrt(-2 * math.log(p))
        return _horner(_ACKLAM_C, q) / (_horner(_ACKLAM_D, q) * q + 1)
    if p > 1 - _ACKLAM_PLOW:
        q = math.sqrt(-2 * math.log(1 - p))
        return -_horner(_ACKLAM_C, q) / (_horner(_ACKLAM_D, q) * q + 1)
    q = p - 0.5
    r = q * q
    return _horner(_ACKLAM_A, r) * q / (_horner(_ACKLAM_B, r) * r + 1)


def _inv_norm_cdf(p: float) -> float:
    """Inverse standard-normal CDF to double precision. ``p`` in (0, 1)."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in the open interval (0, 1), got {p!r}")
    x = _acklam_ppf(p)
    # One Halley step: err = Φ(x) − p, refined through the exact erfc CDF.
    err = 0.5 * math.erfc(-x / math.sqrt(2)) - p
    u = err * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


def mcnemar_sample_size(
    delta_min: float,
    pi_d: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
) -> tuple[float, int, int, int]:
    """Δ_min-pinned McNemar per-cell sizing (ADR 0016 §Evidence, §Statistics).

    Returns ``(p_bc, n_disc, n_total, n_total_mult12)`` where ``p_bc`` is the
    directional split pinned to the registered minimum effect
    ``p_bc = 0.5 + delta_min/(2·pi_d)``, ``n_disc`` the discordant-pair count and
    ``n_total`` the per-cell instance count from the conservative z-form
    ``N_disc = ((z_{1-α/2}/2 + z_{1-β}·√(p_bc(1−p_bc))) / (p_bc − 0.5))²``,
    ``N_total = N_disc / pi_d`` (Connor 1987 / Lachin 2011 §5.7). ``n_total_mult12``
    rounds ``n_total`` UP to a multiple of 12 so GEPA (minibatch 3) and skillopt
    (minibatch 4) tile it evenly in Phase 4.

    ``n_disc`` and ``n_total`` are the rounded values pinned by the ADR table
    (π_d 0.10/0.20/0.30 → 289/616/934 → 300/624/936 at delta_min=0.05); the
    actionable conservative N is always the mult-of-12 ceiling, which is ≥ raw.

    Raises:
        ValueError: if ``pi_d <= delta_min`` — then ``p_bc >= 1`` (the winning
            arm takes every discordant pair), a degenerate, un-sizeable point;
            valid inputs need ``pi_d > delta_min``. Also if ``pi_d`` is not in
            (0, 1] or ``alpha``/``power`` are not in (0, 1).
    """
    if not 0.0 < pi_d <= 1.0:
        raise ValueError(f"pi_d must be in (0, 1], got {pi_d!r}")
    if pi_d <= delta_min:
        raise ValueError(
            f"pi_d must exceed delta_min for p_bc <= 1, got pi_d={pi_d!r} "
            f"<= delta_min={delta_min!r} (p_bc would be {0.5 + delta_min / (2 * pi_d)!r})"
        )
    if not 0.0 < alpha < 1.0 or not 0.0 < power < 1.0:
        raise ValueError(f"alpha and power must be in (0, 1), got alpha={alpha!r}, power={power!r}")
    p_bc = 0.5 + delta_min / (2 * pi_d)
    z_alpha = _inv_norm_cdf(1 - alpha / 2)
    z_beta = _inv_norm_cdf(power)
    numer = z_alpha / 2 + z_beta * math.sqrt(p_bc * (1 - p_bc))
    n_disc_raw = (numer / (p_bc - 0.5)) ** 2
    n_total = round(n_disc_raw / pi_d)
    return (p_bc, round(n_disc_raw), n_total, math.ceil(n_total / 12) * 12)


# --- Paired-cell convenience (ADR 0016 §Campaign mechanics pairing) -------


def _require_binary(mapping: Mapping[str, int], ids: Sequence[str], name: str) -> None:
    """Raise if any value in ``mapping`` over ``ids`` is not 0 or 1."""
    for instance_id in ids:
        value = mapping[instance_id]
        if value not in (0, 1):
            raise ValueError(f"{name}[{instance_id!r}] must be 0 or 1, got {value!r}")


def mcnemar_from_pairs(
    hard_a: Mapping[str, int],
    hard_b: Mapping[str, int],
    *,
    seed: int = 0,
) -> tuple[int, int, int, float, float, tuple[float, float, float]]:
    """Pair two per-instance hard 0/1 dicts → ``(b, c, n, delta, mcnemar_p, ci)``.

    ``hard_a``/``hard_b`` map ``instance_id → resolved (1) | unresolved (0)`` for
    two arms over the SAME instances. ``b`` counts A-only resolves, ``c`` B-only,
    ``n`` the paired total, ``delta = mean(a) − mean(b) = (b − c)/n`` the resolve
    delta, ``mcnemar_p`` the exact two-sided p, and ``ci`` the paired bootstrap
    95% interval on delta. Instances are sorted for deterministic bootstrap.

    Raises:
        ValueError: if the two key sets differ — the campaign guarantees
            identical instance lists, so a mismatch is a bug, not data; the
            message names the symmetric-difference keys. Also if any value is
            not 0 or 1.
    """
    if hard_a.keys() != hard_b.keys():
        diff = sorted(set(hard_a) ^ set(hard_b))
        raise ValueError(f"instance-id key sets differ; symmetric difference: {diff}")
    ids = sorted(hard_a)
    _require_binary(hard_a, ids, "hard_a")
    _require_binary(hard_b, ids, "hard_b")
    a = [hard_a[i] for i in ids]
    bs = [hard_b[i] for i in ids]
    b = sum(1 for x, y in zip(a, bs) if x == 1 and y == 0)
    c = sum(1 for x, y in zip(a, bs) if x == 0 and y == 1)
    n = len(ids)
    delta = (b - c) / n if n else 0.0
    return (b, c, n, delta, mcnemar_exact_p(b, c), paired_bootstrap_ci(a, bs, seed=seed))
