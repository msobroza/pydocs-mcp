"""Batch samplers for a multi-dataset pool — the WITHIN-a-run axis.

The motivating failure is measured, not asserted from theory: with ~260
swe-qa-pro rows against ~12 crosscommitvuln rows, uniform sampling of a batch of
12 draws well under one ccv row on average, so most batches contain NONE and the
skill never sees the behaviour they are meant to teach.

``uniform`` is kept as the CONTROL — a comparison against today's behaviour is
only meaningful if today's behaviour is one of the arms.
"""

from __future__ import annotations

from statistics import fmean

import pytest

from pydocs_eval.optimize.multitask.sampling import (
    build_sampler,
    sampler_registry,
    type_counts,
)

_SHIPPED = ("oversample", "stratified", "uniform")


def _pool(**by_type: int) -> tuple[dict[str, object], ...]:
    """A row pool with ``n`` rows of each named task type."""
    return tuple(
        {"task_id": f"{t}/{i}", "task_type": t, "question": "q", "gold": "g"}
        for t, n in sorted(by_type.items())
        for i in range(n)
    )


#: The real shape: crosscommitvuln is ~5% of the combined train pool.
_IMBALANCED = _pool(sweqapro=260, ccv=12)


def test_registry_ships_exactly_the_three_arms() -> None:
    assert tuple(sorted(sampler_registry.names())) == _SHIPPED


# --------------------------------------------------------------------------- #
# uniform — the control, and the measured motivation
# --------------------------------------------------------------------------- #


def test_uniform_starves_the_minority_dataset() -> None:
    """Documents WHY the other arms exist; if this ever fails, revisit them."""
    sampler = build_sampler("uniform")
    ccv_per_batch = [
        type_counts(sampler.sample(_IMBALANCED, 12, seed)).get("ccv", 0) for seed in range(200)
    ]
    assert fmean(ccv_per_batch) < 1.0
    assert ccv_per_batch.count(0) > 100  # most batches contain no ccv row at all


def test_uniform_is_deterministic_under_a_seed() -> None:
    sampler = build_sampler("uniform")
    assert sampler.sample(_IMBALANCED, 12, 7) == sampler.sample(_IMBALANCED, 12, 7)


# --------------------------------------------------------------------------- #
# stratified
# --------------------------------------------------------------------------- #


def test_stratified_always_includes_every_task_type() -> None:
    sampler = build_sampler("stratified")
    for seed in range(50):
        counts = type_counts(sampler.sample(_IMBALANCED, 12, seed))
        assert counts.get("ccv", 0) >= 1
        assert counts.get("sweqapro", 0) >= 1


def test_stratified_is_proportional_beyond_the_floor() -> None:
    """A balanced pool splits ~evenly; the floor only bites for a minority."""
    counts = type_counts(build_sampler("stratified").sample(_pool(a=100, b=100), 10, 0))
    assert counts == {"a": 5, "b": 5}


def test_stratified_honours_explicit_type_weights() -> None:
    """`weights` overrides proportionality — the 'more ccv than its share' knob."""
    sampler = build_sampler("stratified", weights={"ccv": 3.0, "sweqapro": 1.0})
    counts = type_counts(sampler.sample(_pool(ccv=100, sweqapro=100), 12, 0))
    assert counts["ccv"] > counts["sweqapro"]
    assert counts["ccv"] + counts["sweqapro"] == 12


def test_stratified_never_exceeds_a_types_available_rows() -> None:
    """Availability caps the quota, and the shortfall goes to types that have rows.

    Weighted so the cap actually binds: `a`'s share of 20 would be ~18 rows, but
    only 2 exist. A naive proportional split would emit 18 (duplicating, or
    silently returning a short batch) — this arm must never duplicate.
    """
    sampler = build_sampler("stratified", weights={"a": 10.0, "b": 1.0})
    picked = sampler.sample(_pool(a=2, b=100), 20, 0)
    counts = type_counts(picked)

    assert counts["a"] == 2  # capped by availability, not by its 10x weight
    assert sum(counts.values()) == 20  # the shortfall was absorbed by `b`
    ids = [row["task_id"] for row in picked]
    assert len(ids) == len(set(ids))  # stratified never repeats a row


def test_stratified_handles_a_batch_smaller_than_the_type_count() -> None:
    """No crash, no duplication — just a short batch, deterministically chosen."""
    picked = build_sampler("stratified").sample(_pool(a=5, b=5, c=5), 2, 0)
    assert len(picked) == 2
    assert len(type_counts(picked)) == 2  # two distinct types, not one twice


def test_stratified_is_deterministic_and_interleaved() -> None:
    sampler = build_sampler("stratified")
    first = sampler.sample(_IMBALANCED, 12, 3)
    assert first == sampler.sample(_IMBALANCED, 12, 3)
    # Not grouped by type: the trainer must not see one dataset as a block.
    types = [row["task_type"] for row in sampler.sample(_pool(a=50, b=50), 20, 1)]
    assert types != sorted(types)


# --------------------------------------------------------------------------- #
# oversample
# --------------------------------------------------------------------------- #


def test_oversample_lifts_the_minority_share() -> None:
    counts = type_counts(build_sampler("oversample").sample(_IMBALANCED, 12, 0))
    assert counts.get("ccv", 0) >= 1


def test_oversample_may_repeat_a_row_within_a_batch() -> None:
    """The documented cost of this arm — 12 ccv rows cannot fill 6 slots uniquely."""
    picked = build_sampler("oversample", target_share={"ccv": 0.5}).sample(
        _pool(ccv=2, x=100), 10, 0
    )
    ccv_ids = [r["task_id"] for r in picked if r["task_type"] == "ccv"]
    assert len(ccv_ids) > len(set(ccv_ids))


# --------------------------------------------------------------------------- #
# Shared contract
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", _SHIPPED)
def test_every_sampler_returns_exactly_count_rows(name: str) -> None:
    assert len(build_sampler(name).sample(_IMBALANCED, 12, 0)) == 12


@pytest.mark.parametrize("name", _SHIPPED)
def test_every_sampler_handles_a_pool_smaller_than_the_batch(name: str) -> None:
    assert len(build_sampler(name).sample(_pool(a=3), 10, 0)) <= 10


@pytest.mark.parametrize("name", _SHIPPED)
def test_every_sampler_returns_nothing_for_a_non_positive_count(name: str) -> None:
    assert build_sampler(name).sample(_IMBALANCED, 0, 0) == []
