"""Inter-rater agreement: Cohen's κ and raw percent agreement over paired ratings.

The answer judge's alignment set is labelled by two blind labellers; their
per-question κ is the ceiling every judge's agreement target is read against,
and the escalation judge's agreement with the alignment label is a raw percent
agreement (spec 9d). Both live beside ``aggregate.mcnemar_from_pairs`` in the
paired-statistics family and are stdlib-only like it. A rating is any hashable
category: ``"true"`` / ``"false"`` for a yes/no question, ``"L0"`` … ``"L3"``
for a levelled one.

Example:
    >>> round(cohens_kappa([("true", "true"), ("false", "false"), ("true", "false")]), 2)
    0.4
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Sequence

Rating = Hashable


def percent_agreement(pairs: Sequence[tuple[Rating, Rating]]) -> float | None:
    """The share of pairs whose two ratings are equal; ``None`` when nothing was rated.

    Example:
        >>> percent_agreement([("true", "true"), ("true", "false")])
        0.5
    """
    if not pairs:
        return None
    return sum(1 for first, second in pairs if first == second) / len(pairs)


def cohens_kappa(pairs: Sequence[tuple[Rating, Rating]]) -> float | None:
    """Cohen's κ between the first and the second rater of every pair.

    ``(p_o - p_e) / (1 - p_e)``: observed agreement corrected for the agreement
    the two raters' own category frequencies would give by chance (Cohen 1960).
    ``None`` when undefined: nothing was rated, or both raters only ever used
    one and the same category (``p_e = 1``, so chance explains everything and
    κ is 0/0 — never reported as perfect).

    Example:
        >>> cohens_kappa([("true", "false"), ("false", "true")])
        -1.0
    """
    observed = percent_agreement(pairs)
    if observed is None:
        return None
    expected = _chance_agreement(pairs)
    if expected == 1.0:
        return None
    return (observed - expected) / (1.0 - expected)


def _chance_agreement(pairs: Sequence[tuple[Rating, Rating]]) -> float:
    """``p_e``: the probability two independent raters with these marginals agree."""
    count = len(pairs)
    first = Counter(rating for rating, _ in pairs)
    second = Counter(rating for _, rating in pairs)
    return sum(first[category] * second[category] for category in first) / (count * count)


__all__ = ("cohens_kappa", "percent_agreement")
