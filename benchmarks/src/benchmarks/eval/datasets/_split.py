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

# The three accepted ``split`` values. Single source of truth: both
# datasets' ``__post_init__`` validation and the runner's argparse
# ``choices`` mirror this set (``"all"`` is the backward-compat default —
# the whole filtered corpus, no partition).
VALID_SPLITS: tuple[str, ...] = ("all", "dev", "test")


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
) -> list[T]:
    """Return only the rows belonging to ``split``.

    ``"all"`` is a strict no-op (returns ``rows`` unchanged) — the
    backward-compat default. For ``"dev"`` / ``"test"`` the rows are
    partitioned with a STRATIFIED scheme: each stratum (``stratum_of(row)``)
    is split independently into a ``dev`` head and a ``test`` tail, so both
    slices preserve the corpus's per-stratum proportions.

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
    """
    validate_split(split)
    if split == "all":
        return rows

    groups: dict[str, list[T]] = {}
    for row in rows:
        groups.setdefault(stratum_of(row), []).append(row)

    # One RNG, reused across groups — re-seeding per stratum would change
    # the draw sequence and break parity with the original inline split.
    rng = random.Random(seed)
    selected: list[T] = []
    # Sort group keys so the RNG draw sequence is itself deterministic
    # across runs (dict insertion order already is, but pinning the
    # iteration order makes the determinism explicit and robust to a
    # future change in row arrival order).
    for stratum in sorted(groups):
        group = sorted(groups[stratum], key=sort_key)
        rng.shuffle(group)
        n_dev = round(dev_fraction * len(group))
        dev_rows = group[:n_dev]
        test_rows = group[n_dev:]
        selected.extend(dev_rows if split == "dev" else test_rows)
    return selected
