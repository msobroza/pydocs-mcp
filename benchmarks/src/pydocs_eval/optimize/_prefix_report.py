"""Per-dataset-prefix metric breakdown for combined-dataset runs (design §6.4).

``CombinedDataset`` tags every task_id with its member prefix (``sweqapro/…``,
``ccv/…``). At ≤33 ccv vs ~260 swe-qa-pro tasks the ccv slice is ~8-11% of a
blended score and can silently regress — so run reporting groups counts and
mean scores by ``task_id.split("/", 1)[0]``. Pure functions, no I/O; callers
(the CLI split echo today; holdout-metric reporting as it lands) do the
formatting. ccv-slice deltas at N≈12-16 are directional only (design §10).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping


def task_id_prefix(task_id: str) -> str:
    """The dataset prefix of a combined task id (``"ccv/cve-x"`` → ``"ccv"``).

    An un-prefixed id (no ``"/"``) groups under itself, so single-dataset
    runs degrade to one group instead of raising.

    Example:
        >>> task_id_prefix("ccv/cve-2099-0001")
        'ccv'
    """
    return task_id.split("/", 1)[0]


def count_by_task_prefix(task_ids: Iterable[str]) -> dict[str, int]:
    """Task counts grouped by dataset prefix — the split-echo breakdown.

    Example:
        >>> count_by_task_prefix(["ccv/a", "ccv/b", "sweqapro/x"])
        {'ccv': 2, 'sweqapro': 1}
    """
    return dict(Counter(task_id_prefix(task_id) for task_id in task_ids))


def mean_score_by_task_prefix(scores: Mapping[str, float]) -> dict[str, float]:
    """Mean of per-task scores grouped by dataset prefix.

    ``scores`` maps task_id → score (verdicts, recall@k, …); reporting each
    prefix's mean side by side keeps a ccv regression visible under an
    improving blended score (design §6.4).

    Example:
        >>> mean_score_by_task_prefix({"ccv/a": 1.0, "ccv/b": 0.0})
        {'ccv': 0.5}
    """
    grouped: dict[str, list[float]] = {}
    for task_id, score in scores.items():
        grouped.setdefault(task_id_prefix(task_id), []).append(score)
    return {prefix: sum(vals) / len(vals) for prefix, vals in grouped.items()}
