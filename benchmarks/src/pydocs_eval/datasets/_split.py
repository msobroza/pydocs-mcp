"""Shared stratified dev/test split for evaluation datasets.

The partition logic is dataset-agnostic: it groups rows by a caller-supplied
stratum (``Ds1000Dataset`` uses the normalized library; ``RepoQADataset``
uses the repo), splits EACH group independently into a ``dev`` head and a
``test`` tail, and so keeps every stratum's proportion in both slices. The
two datasets share this one implementation so their splits stay identical
and the partition logic lives in exactly one place.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

# The accepted ``split`` values. Single source of truth: both datasets'
# ``__post_init__`` validation and the runner's argparse ``choices`` mirror
# this set (``"all"`` is the backward-compat default — the whole filtered
# corpus, no partition).
VALID_SPLITS: tuple[str, ...] = ("all", "dev", "test", "small_test", "small_dev")

# Default target size for BOTH small splits: ``small_test`` (fixed-size,
# stratified subsample of the held-out ``test`` tail) and ``small_dev`` (its
# mirror drawn from the ``dev`` head — the burn-free iteration slice; see
# benchmarks/README.md §"Sweep protocol"). One constant on purpose: the
# mirror only earns its keep if it is the SAME size as the slice it stands
# in for. Single source of truth: the datasets' ``small_test_size`` field
# default and ``stratified_split``'s parameter default both read this
# constant.
_DEFAULT_SMALL_TEST_SIZE = 30


def validate_split(split: str) -> None:
    """Reject a ``split`` value outside :data:`VALID_SPLITS`.

    A bad ``split`` is a caller bug — datasets call this from
    ``__post_init__`` so it fails loud at construction rather than silently
    yielding the wrong slice deep in the async loop.
    """
    if split not in VALID_SPLITS:
        raise ValueError(
            f"split must be one of {VALID_SPLITS!r}, got {split!r}",
        )


def stratified_split(
    rows: list[T],
    *,
    split: str,
    dev_fraction: float,
    seed: int,
    stratum_of: Callable[[T], str],
    sort_key: Callable[[T], str],
    small_test_size: int = _DEFAULT_SMALL_TEST_SIZE,
) -> list[T]:
    """Return only the rows belonging to ``split``.

    ``"all"`` is a strict no-op (returns ``rows`` unchanged) — the
    backward-compat default. For ``"dev"`` / ``"test"`` the rows are
    partitioned with a STRATIFIED scheme: each stratum (``stratum_of(row)``)
    is split independently into a ``dev`` head and a ``test`` tail, so both
    slices preserve the corpus's per-stratum proportions. ``"small_test"``
    is a fixed-size (``min(small_test_size, |test|)``) stratified SUBSAMPLE
    of the ``test`` tail — a small, representative, held-out slice for fast
    experiment iteration (``small_test`` ⊂ ``test``).

    Determinism contract: within each stratum group the rows are first
    STABLE-sorted by ``sort_key(row)`` (a deterministic, position-independent
    key) so ordering is independent of the rows' arrival position, then
    shuffled with a fresh ``random.Random(seed)``. The same
    ``random.Random(seed)`` instance is REUSED across groups (it is not
    re-seeded per stratum) and the strata are iterated in sorted-key order,
    so the draw sequence — and therefore the partition membership — is
    byte-identical across runs and load paths for a fixed ``seed``.
    ``n_dev = round(dev_fraction * len(group))`` (Python banker's rounding)
    rows go to ``dev``, the remainder to ``test``.

    ``small_test`` apportions its ``min(small_test_size, |test|)`` rows
    across strata by Hamilton's largest-remainder method (proportional
    floors, then the leftover seats handed to the largest fractional
    remainders, ties broken by sorted stratum key). This hits the target
    size EXACTLY while preserving per-stratum proportions — a plain
    ``round()`` per stratum would over- or under-shoot the target badly when
    many strata round the same direction (e.g. 50 repos each rounding 0.6→1
    yields 50, not 30). Rows are taken from the HEAD of each stratum's
    already-shuffled ``test`` tail, so ``small_test`` is a deterministic
    subset of ``test``.

    ``"small_dev"`` is the exact mirror of ``"small_test"`` drawn from the
    ``dev`` partition instead of ``test``: the same Hamilton
    largest-remainder apportionment, the same ``small_test_size`` target and
    the same seed, taken from the head of each stratum's already-shuffled
    ``dev`` head (``small_dev`` ⊂ ``dev``). It exists so tuning sweeps can
    iterate without consuming test-derived data — see
    ``benchmarks/README.md`` §"Sweep protocol".
    """
    validate_split(split)
    if split == "all":
        return rows

    groups = _groups_by_stratum(rows, stratum_of)

    # One RNG, reused across groups — re-seeding per stratum would change
    # the draw sequence and break parity with the original inline split.
    rng = random.Random(seed)
    # Sort group keys so the RNG draw sequence is itself deterministic
    # across runs (dict insertion order already is, but pinning the
    # iteration order makes the determinism explicit and robust to a
    # future change in row arrival order). dev head + test tail are computed
    # the same way for every split so the shuffle draw sequence — and hence
    # dev/test membership — stays byte-identical to the original.
    # Every stratum is shuffled before any is cut: the cut draws nothing from
    # the RNG, so the draw sequence is the one the inline loop always made.
    dev_by_stratum: dict[str, list[T]] = {}
    test_by_stratum: dict[str, list[T]] = {}
    for stratum, group in _shuffled_strata(groups, rng, sort_key).items():
        n_dev = round(dev_fraction * len(group))
        dev_by_stratum[stratum] = group[:n_dev]
        test_by_stratum[stratum] = group[n_dev:]

    # Flattening in sorted-stratum order reproduces the exact row order the
    # previous flat ``dev_rows`` accumulator produced — dev/test membership
    # AND order stay byte-identical after the small_dev addition, so every
    # recorded baseline number keeps meaning the same slice.
    if split == "dev":
        return [row for stratum in sorted(dev_by_stratum) for row in dev_by_stratum[stratum]]
    if split == "small_dev":
        return _largest_remainder_subsample(dev_by_stratum, target=small_test_size)
    if split == "test":
        return [row for stratum in sorted(test_by_stratum) for row in test_by_stratum[stratum]]
    return _largest_remainder_subsample(test_by_stratum, target=small_test_size)


def reseeded_stratified_subsample(
    rows: list[T],
    *,
    seed: int,
    size: int,
    stratum_of: Callable[[T], str],
    sort_key: Callable[[T], str],
) -> list[T]:
    """A fixed-size stratified subsample of ``rows`` drawn under its OWN ``seed``.

    ``rows`` is a partition already cut (the fresh repoqa test draw passes the
    ``test`` partition minus the consumed ids). Unlike ``small_test`` — which
    takes the heads of the partition's tails, so its seed also moves the
    partition — this redraw leaves the partition alone: each stratum is
    stable-sorted by ``sort_key``, shuffled by ONE ``random.Random(seed)``
    reused across strata in sorted order (``stratified_split``'s determinism
    contract), and ``min(size, len(rows))`` rows are taken by the same
    largest-remainder apportionment ``small_test`` uses.
    """
    groups = _groups_by_stratum(rows, stratum_of)
    shuffled = _shuffled_strata(groups, random.Random(seed), sort_key)
    return _largest_remainder_subsample(shuffled, target=size)


def _shuffled_strata(
    groups: dict[str, list[T]], rng: random.Random, sort_key: Callable[[T], str]
) -> dict[str, list[T]]:
    """Each group stable-sorted by ``sort_key``, then shuffled by ONE ``rng`` in sorted-stratum order.

    The determinism contract both draws share: ordering is independent of the
    rows' arrival, and reusing one RNG across strata (never re-seeding per
    stratum) keeps the draw sequence — so the membership — byte-identical.
    """
    shuffled: dict[str, list[T]] = {}
    for stratum in sorted(groups):
        shuffled[stratum] = sorted(groups[stratum], key=sort_key)
        rng.shuffle(shuffled[stratum])
    return shuffled


def _groups_by_stratum(rows: list[T], stratum_of: Callable[[T], str]) -> dict[str, list[T]]:
    """``rows`` grouped by stratum, each group in arrival order."""
    groups: dict[str, list[T]] = {}
    for row in rows:
        groups.setdefault(stratum_of(row), []).append(row)
    return groups


def _largest_remainder_subsample(
    test_by_stratum: dict[str, list[T]],
    *,
    target: int,
) -> list[T]:
    """Proportional fixed-size subsample of per-stratum row groups.

    Implements the ``small_test`` / ``small_dev`` apportionment described in
    :func:`stratified_split`: ``min(target, |rows|)`` rows split across
    strata in proportion to each stratum's group size via Hamilton's
    largest-remainder method, taken from the head of each (already-shuffled)
    group. Deterministic — ties on the fractional remainder break by sorted
    stratum key.
    """
    total = sum(len(rows) for rows in test_by_stratum.values())
    if total == 0:
        return []
    target = min(target, total)
    strata = sorted(test_by_stratum)
    quotas = {s: target * len(test_by_stratum[s]) / total for s in strata}
    counts = {s: int(quotas[s]) for s in strata}  # proportional floors
    leftover = target - sum(counts.values())
    # Hand the leftover seats to the largest fractional remainders; the
    # ``(-(remainder), s)`` key makes the order — and so the membership —
    # deterministic across runs.
    by_remainder = sorted(strata, key=lambda s: (-(quotas[s] - counts[s]), s))
    for stratum in by_remainder[:leftover]:
        counts[stratum] += 1
    return [row for stratum in strata for row in test_by_stratum[stratum][: counts[stratum]]]
