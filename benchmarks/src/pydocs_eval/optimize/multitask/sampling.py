"""Batch samplers for a multi-dataset row pool — the WITHIN-a-run axis.

The generated SkillOpt env adapter builds each batch by shuffling the merged
pool and taking a slice. With ~260 swe-qa-pro rows against ~12 crosscommitvuln
rows that draws well under one ccv row into a batch of 12, so most batches
contain NONE and the skill never sees the behaviour those rows teach.

Three arms, so the question can be answered by measurement rather than argument:

* ``uniform`` — today's behaviour, kept as the CONTROL. A comparison is only
  meaningful if the status quo is one of the arms.
* ``stratified`` — proportional allocation with a floor of one row per type, and
  optional explicit ``weights`` to buy a type more than its proportional share.
* ``oversample`` — replicate the minority to a target share. Cheapest to reason
  about and the only arm that can repeat a row *within* one batch, which is
  precisely its documented cost.

Pure and stdlib-only: a sampler takes ``(pool, count, seed)`` and returns rows.
Nothing here imports SkillOpt, so every arm is exercised offline for free.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pydocs_eval.optimize._prefix_report import task_id_prefix
from pydocs_eval.registries import _Registry

__all__ = [
    "BatchSampler",
    "build_sampler",
    "row_type",
    "sampler_registry",
    "type_counts",
]

#: One pool row as the generated env adapter inlines it.
Row = Mapping[str, object]

#: Every task type present in a batch gets at least this many rows, budget
#: permitting — the floor is what makes a minority dataset visible at all.
_MIN_PER_TYPE = 1


class BatchSampler(Protocol):
    """Chooses ``count`` rows from ``pool`` deterministically for ``seed``."""

    def sample(self, pool: Sequence[Row], count: int, seed: int) -> list[Row]: ...


sampler_registry: _Registry[BatchSampler] = _Registry()


def build_sampler(name: str, **kwargs: object) -> BatchSampler:
    """Build the registered sampler ``name`` with its keyword knobs."""
    return sampler_registry.build(name, **kwargs)


def row_type(row: Row) -> str:
    """A row's task type: explicit ``task_type``, else its ``task_id`` prefix.

    WHY the fallback: the generated SkillOpt env adapter inlines rows as
    ``{"task_id", "question", "gold"}`` with **no** ``task_type`` key. Defaulting
    the missing key to ``"other"`` bucketed every row as one type, which made all
    three arms bit-identical — the module was inert against the only pool that
    matters, and silently so. ``CombinedDataset`` already prefixes its ids
    (``ccv/…``, ``sweqapro/…``), so the type is recoverable without touching the
    adapter.

    Raises:
        ValueError: the row carries neither key, so its type is genuinely
            unknowable — better to say so than to collapse the arms.
    """
    explicit = row.get("task_type")
    if isinstance(explicit, str) and explicit:
        return explicit
    task_id = row.get("task_id")
    if isinstance(task_id, str) and "/" in task_id:
        return task_id_prefix(task_id)
    raise ValueError(
        f"cannot determine a task type for row {task_id!r}: it has no 'task_type' "
        "key and its 'task_id' carries no '<dataset>/' prefix. Without a type "
        "every sampler degrades to uniform, so this is refused rather than guessed"
    )


def type_counts(rows: Sequence[Row]) -> dict[str, int]:
    """Rows per task type — the comparison payload for every sampler test."""
    counts: dict[str, int] = {}
    for row in rows:
        key = row_type(row)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _by_type(pool: Sequence[Row]) -> dict[str, list[Row]]:
    grouped: dict[str, list[Row]] = {}
    for row in pool:
        grouped.setdefault(row_type(row), []).append(row)
    return grouped


def _resolve_shares(weights: Mapping[str, float], sizes: Mapping[str, int]) -> dict[str, float]:
    """Explicit ``weights`` (all types, all non-negative) or proportional sizes.

    A weight is a RATIO between types, so mixing one type's weight with another
    type's raw row count compares incommensurable quantities: ``{"ccv": 10.0}``
    against 260 sweqapro rows silently yielded the 1-row floor, i.e. exactly what
    passing no weights at all would do. Partial weights are therefore refused.
    """
    if not weights:
        return {t: float(sizes[t]) for t in sizes}
    missing = sorted(set(sizes) - set(weights))
    if missing:
        raise ValueError(
            f"stratified weights must cover every task type present; missing "
            f"{missing}. A partial weight is compared against the other types' raw "
            "row counts, so it silently has no effect"
        )
    negative = sorted(t for t in weights if weights[t] < 0)
    if negative:
        raise ValueError(
            f"stratified weights must be non-negative; negative for {negative}. "
            "A negative share produces a negative quota and a batch larger than count"
        )
    shares = {t: float(weights[t]) for t in sizes}
    if sum(shares.values()) <= 0:
        raise ValueError(
            "stratified weights must include at least one positive value; all-zero "
            "shares leave only the per-type floor, so the batch comes back short"
        )
    return shares


@sampler_registry.register("uniform")
@dataclass(frozen=True, slots=True)
class UniformSampler:
    """Shuffle the merged pool and slice — today's behaviour, byte for byte.

    Retained as the control arm. Its weakness is structural, not a bug: a type
    holding 5% of the pool appears in 5% of the slots, so a small batch usually
    misses it entirely.
    """

    def sample(self, pool: Sequence[Row], count: int, seed: int) -> list[Row]:
        if count <= 0:
            return []
        items = list(pool)
        random.Random(seed).shuffle(items)
        return items[:count]


@sampler_registry.register("stratified")
@dataclass(frozen=True, slots=True)
class StratifiedSampler:
    """Proportional allocation with a per-type floor, then interleave.

    ``weights`` (optional) replaces proportionality with an explicit ratio, so a
    type can be bought more than its share of a batch without duplicating rows
    the way ``oversample`` does. The result is shuffled before returning: the
    trainer must never see one dataset as a contiguous block.
    """

    weights: Mapping[str, float] = field(default_factory=dict)

    def sample(self, pool: Sequence[Row], count: int, seed: int) -> list[Row]:
        if count <= 0 or not pool:
            return []
        rng = random.Random(seed)
        grouped = _by_type(pool)
        for rows in grouped.values():
            rng.shuffle(rows)

        sizes = {t: len(rows) for t, rows in grouped.items()}
        quota = _allocate(count, sizes, _resolve_shares(self.weights, sizes))

        picked = [row for t in sorted(grouped) for row in grouped[t][: quota[t]]]
        rng.shuffle(picked)
        return picked


@sampler_registry.register("oversample")
@dataclass(frozen=True, slots=True)
class OversampleSampler:
    """Replicate minority rows until each reaches its target share of the batch.

    ``target_share`` maps a task type to the fraction of the batch it should
    occupy (default: an equal share for every type present). Rows REPEAT when a
    type has fewer distinct rows than its quota — the documented cost of this
    arm, and the reason it risks overfitting a small slice.
    """

    target_share: Mapping[str, float] = field(default_factory=dict)

    def sample(self, pool: Sequence[Row], count: int, seed: int) -> list[Row]:
        if count <= 0 or not pool:
            return []
        rng = random.Random(seed)
        grouped = _by_type(pool)
        for rows in grouped.values():
            rng.shuffle(rows)

        types = sorted(grouped)
        default = 1.0 / len(types)
        quota = {t: int(count * float(self.target_share.get(t, default))) for t in types}
        for t in types:  # hand any rounding remainder to the largest pools
            quota[t] = min(quota[t], count)
        picked: list[Row] = []
        for t in types:
            rows = grouped[t]
            picked += [rows[i % len(rows)] for i in range(quota[t])]

        # Fill any shortfall from the whole pool, then trim to exactly `count`.
        if len(picked) < count:
            filler = list(pool)
            rng.shuffle(filler)
            picked += filler[: count - len(picked)]
        rng.shuffle(picked)
        return picked[:count]


def _allocate(count: int, sizes: Mapping[str, int], shares: Mapping[str, float]) -> dict[str, int]:
    """Largest-remainder allocation of ``count`` across types, floored per type.

    Never allocates a type more rows than it has (``sizes``), and never returns
    more than ``count`` in total — the two invariants a naive proportional split
    breaks on a skewed pool.
    """
    types = sorted(sizes)
    alloc = {t: min(_MIN_PER_TYPE, sizes[t], count) for t in types}
    # A batch smaller than the type count cannot floor every type; drop the
    # largest allocations first so the surviving types stay distinct.
    while sum(alloc.values()) > count:
        alloc[max(types, key=lambda t: (alloc[t], t))] -= 1

    total_share = sum(shares[t] for t in types)
    remaining = count - sum(alloc.values())
    if remaining <= 0 or total_share <= 0:
        return alloc

    exact = {t: remaining * shares[t] / total_share for t in types}
    for t in types:
        alloc[t] += min(int(exact[t]), sizes[t] - alloc[t])

    # Largest remainder for the leftovers, then any capacity still free.
    for t in sorted(types, key=lambda t: (exact[t] % 1, t), reverse=True):
        if sum(alloc.values()) >= count:
            break
        if alloc[t] < sizes[t]:
            alloc[t] += 1
    for t in types:
        while sum(alloc.values()) < count and alloc[t] < sizes[t]:
            alloc[t] += 1
    return alloc
