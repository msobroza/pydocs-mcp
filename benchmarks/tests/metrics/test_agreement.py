"""Inter-rater agreement: Cohen's κ and raw percent agreement, on worked examples."""

from __future__ import annotations

import pytest

from pydocs_eval.metrics.agreement import cohens_kappa, percent_agreement


def _pairs(counts: dict[tuple[str, str], int]) -> list[tuple[str, str]]:
    return [pair for pair, count in counts.items() for _ in range(count)]


# Wikipedia's worked example: 50 proposals, 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no.
_WIKIPEDIA = _pairs({("yes", "yes"): 20, ("yes", "no"): 5, ("no", "yes"): 10, ("no", "no"): 15})


def test_kappa_matches_the_textbook_example() -> None:
    assert cohens_kappa(_WIKIPEDIA) == pytest.approx(0.4)


def test_percent_agreement_is_the_share_of_equal_pairs() -> None:
    assert percent_agreement(_WIKIPEDIA) == pytest.approx(0.7)


def test_kappa_reads_any_number_of_categories() -> None:
    levels = [("L0", "L0"), ("L1", "L1"), ("L2", "L2"), ("L3", "L3"), ("L1", "L2"), ("L2", "L1")]

    # p_o = 24/36, p_e = 10/36: (24 - 10) / (36 - 10) = 7/13.
    assert cohens_kappa(levels) == pytest.approx(7 / 13)


def test_kappa_is_one_on_perfect_agreement_and_negative_below_chance() -> None:
    assert cohens_kappa([("true", "true"), ("false", "false")]) == pytest.approx(1.0)
    assert cohens_kappa([("true", "false"), ("false", "true")]) == pytest.approx(-1.0)


def test_kappa_is_undefined_when_both_raters_only_ever_used_one_category() -> None:
    """p_e = 1: chance alone explains the agreement, so κ is 0/0, never 1."""
    assert cohens_kappa([("false", "false")] * 5) is None


def test_nothing_rated_has_no_agreement_figure() -> None:
    assert cohens_kappa([]) is None
    assert percent_agreement([]) is None
