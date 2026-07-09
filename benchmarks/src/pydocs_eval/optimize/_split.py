"""Deterministic train/holdout split of agent-track tasks (spec §D3).

A task's split is pinned to ``int(sha256(task_id.encode()).hexdigest(), 16)
% 2`` (``0`` → train, ``1`` → holdout) so train and holdout never drift and
the optimizer physically cannot leak holdout tasks into training. A tiny
task pool can land wholly on one side; that is a config error, not a silent
skew, so ``partition_task_ids`` refuses it loudly (spec §D3).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Literal

# WHY: single source of truth for the split arity — the pinned predicate is
# sha256(task_id) % _SPLIT_MODULUS; the two named sides below are the only
# outcomes, so a rename never drifts across the module.
_SPLIT_MODULUS = 2
_TRAIN: Literal["train"] = "train"
_HOLDOUT: Literal["holdout"] = "holdout"


def task_split(task_id: str) -> Literal["train", "holdout"]:
    """Return the pinned split for ``task_id`` (spec §D3).

    Deterministic and stateless: ``int(sha256(task_id) , 16) % 2 == 0`` is
    ``"train"``, otherwise ``"holdout"``.

    Example:
        >>> task_split("swe-qa-pro:0001") in ("train", "holdout")
        True
    """
    digest = int(hashlib.sha256(task_id.encode()).hexdigest(), 16)
    return _TRAIN if digest % _SPLIT_MODULUS == 0 else _HOLDOUT


def partition_task_ids(
    task_ids: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Partition ``task_ids`` into ``(train, holdout)`` by the pinned predicate.

    Raises ``ValueError`` naming the empty side and the task count when one
    side is empty — a tiny task pool is a config error, not a silent skew
    (spec §D3). Order within each side follows input order.
    """
    ids = tuple(task_ids)
    train = tuple(t for t in ids if task_split(t) == _TRAIN)
    holdout = tuple(t for t in ids if task_split(t) == _HOLDOUT)
    if not train:
        raise ValueError(
            f"train split is empty across {len(ids)} task(s): "
            "the task pool is too small or skewed to train a candidate"
        )
    if not holdout:
        raise ValueError(
            f"holdout split is empty across {len(ids)} task(s): "
            "the task pool is too small or skewed to gate acceptance"
        )
    return train, holdout
