"""What did the agent's own searches retrieve? — ranked scores per search call.

A recorded agent run issues its own queries, so every ``search_codebase`` call in
a trajectory is a retrieval run in miniature: a query, a ranked list of rows, and
the task's gold. This module scores them with the SAME relevance predicate the
retrieval sweep uses (``metrics/_relevance.py``) and the SAME metric classes
(``recall@k`` / ``hit@k`` / ``mrr``), so a trajectory number and a sweep number
mean the same thing and no formula has a second implementation here.

Two levels, and they answer different questions:

- **per call** — ``recall@k``, ``hit@k`` and ``mrr`` over one call's ranking.
  ``recall@k`` is this suite's ``recall@k``: ``1.0`` when a gold row sits at rank
  ``<= k``, else ``0.0`` (``metrics/hit_at_k.py`` explains why the two names are
  one number). The first call and the best call are both reported, because the
  first is what the agent got for its opening question and the best is what its
  searching was ultimately worth.
- **per trajectory** — the share of the task's gold items covered by the UNION of
  every call's top-``k``. This one IS fractional: reformulating is how an agent
  reaches a second gold file, and a union that covers two of two gold files must
  read higher than one call that covers one of them.

**Undefined, not zero.** A trajectory that never searched, or a task with no gold
to find, has no retrieval question to answer: those numbers read ``None`` rather
than ``0.0``, so "never searched" cannot average into a before/after comparison
as "searched and found nothing". A search that ran and returned nothing is a
measured ``0.0`` — the opposite case, and the one a change is supposed to move.

**Row paths reach the predicate raw, NOT workspace-normalized.** The shared
predicate matches a gold path as a ``/``-boundary suffix, which is what lets it
score the tmp-prefixed corpus paths a sweep produces; folding a trace row through
``path_normalizer`` first would make a trajectory number mean something a sweep
number does not. ``gold_reach`` does normalize, because its question ("did the
evidence reach the model") is workspace-scoped by construction, so a dependency
file whose name suffix-matches a gold path can score here and still not count as
reaching the gold. The two layers answer different questions on purpose.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer

# WHY (private module): ``_relevance`` is the single relevance source every sweep
# metric already consumes — ``matched_gold_key`` credits each gold item once and
# ``ground_truth_count`` is the denominator it agrees with by construction.
# Reaching for them is what keeps a trajectory's recall the same measurement as a
# sweep's, instead of a second, divergent notion of "found it".
from pydocs_eval.metrics._relevance import ground_truth_count, matched_gold_key
from pydocs_eval.metrics.hit_at_k import HitAtK
from pydocs_eval.metrics.mrr import MRR
from pydocs_eval.metrics.recall_at_k import RecallAtK
from pydocs_eval.systems.base_system import RetrievedItem
from pydocs_eval.trajectory.call_efficiency import QUERY_ARGUMENT, SEARCH_TOOL
from pydocs_eval.trajectory.schema import ToolEvent

# The cutoffs every retrieval number here is reported at — the sweep's own
# ``k ∈ {1, 5, 10}``, so the two reports line up column for column.
RETRIEVAL_K: tuple[int, ...] = (1, 5, 10)

# One instance per name and cutoff, built once: the metric classes ARE the
# formulas, so scoring a call is calling them rather than re-deriving them.
_RECALL = tuple(RecallAtK(k) for k in RETRIEVAL_K)
_HIT = tuple(HitAtK(k) for k in RETRIEVAL_K)
_MRR = MRR()

# The gold identity a trajectory carries is a FILE SET (the gold patch's files,
# or the task's cited files), never an AST body: a trace records identifier
# atoms, not chunk text, so the file-set branch of the predicate is the one that
# applies and a row's text is deliberately empty.
_TRAJECTORY_TASK_ID = "trajectory"


def _no_corpus() -> Path:
    """A recorded trajectory scores an existing ranking; no corpus is built."""
    raise NotImplementedError("scoring recorded search calls never materializes a corpus")


def _task_for(gold_files: frozenset[str]) -> EvalTask:
    """The gold as the relevance predicate expects it: one file-set task."""
    return EvalTask(
        task_id=_TRAJECTORY_TASK_ID,
        query="",
        gold=GoldAnswer(file_set=tuple(sorted(gold_files))),
        corpus_source=_no_corpus,
    )


def _row_path(row: Mapping[str, Any]) -> str:
    """The row's path atom, or ``""`` for a row that carries none (an edge)."""
    path = row.get("path")
    return path if isinstance(path, str) else ""


def _ranked_items(event: ToolEvent) -> tuple[RetrievedItem, ...]:
    """One call's result rows as the ranked items the metric classes score.

    Rank is the row's position in the recorded result, which is the order the
    tool returned it. ``text`` stays empty: the trace keeps identifier atoms,
    not chunk bodies (see the module note on which relevance branch applies).
    """
    return tuple(
        RetrievedItem(
            rank=rank,
            text="",
            source_path=_row_path(row),
            qualified_name=row.get("qualified_name"),
        )
        for rank, row in enumerate(event.result_ids or (), start=1)
    )


def _query_of(event: ToolEvent) -> str:
    """The query a search call carried; ``""`` when it named none."""
    query = event.args.get(QUERY_ARGUMENT)
    return query if isinstance(query, str) else ""


@dataclass(frozen=True, slots=True)
class SearchCallScores:
    """One search call's ranking, scored against the task's gold."""

    seq: int
    query: str
    results: int
    recall_at_k: Mapping[int, float]
    hit_at_k: Mapping[int, float]
    mrr: float

    def to_dict(self) -> dict[str, object]:
        """Report-ready values, JSON types only."""
        return {
            "seq": self.seq,
            "query": self.query,
            "results": self.results,
            **{f"recall@{k}": value for k, value in self.recall_at_k.items()},
            **{f"hit@{k}": value for k, value in self.hit_at_k.items()},
            "mrr": self.mrr,
        }


def _score_call(
    event: ToolEvent, items: tuple[RetrievedItem, ...], task: EvalTask
) -> SearchCallScores:
    """Score one call's ranking through the shared metric classes."""
    return SearchCallScores(
        seq=event.seq,
        query=_query_of(event),
        results=len(items),
        recall_at_k={metric.k: metric.compute(task, items) for metric in _RECALL},
        hit_at_k={metric.k: metric.compute(task, items) for metric in _HIT},
        mrr=_MRR.compute(task, items),
    )


def _union_recall(rankings: Iterable[Sequence[RetrievedItem]], task: EvalTask, k: int) -> float:
    """Share of the gold items covered by the union of every ranking's top-``k``.

    Each gold item counts once however many rows matched it — the unit
    ``matched_gold_key`` and ``ground_truth_count`` agree on — so a trajectory
    that reaches two of two gold files reads ``1.0`` and one that reaches one of
    them reads ``0.5``, whichever calls got there.
    """
    matched = (matched_gold_key(item, task) for ranking in rankings for item in ranking[:k])
    covered = {key for key in matched if key is not None}
    return len(covered) / ground_truth_count(task)


@dataclass(frozen=True, slots=True)
class SearchRetrieval:
    """Every search call of one trajectory, scored, plus the trajectory rollup.

    ``calls`` is empty when there was nothing to score — no search call, or no
    gold — which is also when the rollup reads ``None`` for every cutoff.
    ``search_calls`` and ``reformulations`` are counts of what the agent did and
    stay defined either way.
    """

    calls: tuple[SearchCallScores, ...]
    search_calls: int
    reformulations: int
    trajectory_recall_at_k: Mapping[int, float | None]

    @property
    def first_call(self) -> SearchCallScores | None:
        """The opening search — what the agent got for its first question."""
        return self.calls[0] if self.calls else None

    @property
    def best_call(self) -> SearchCallScores | None:
        """The call that ranked a gold row highest; ties go to the earlier call.

        Chosen on ``mrr`` because it orders a whole ranking rather than one
        cutoff, and reported as ONE call's block so the numbers stay mutually
        consistent instead of mixing cutoffs from different calls.
        """
        if not self.calls:
            return None
        return max(self.calls, key=lambda call: (call.mrr, -call.seq))

    def to_dict(self) -> dict[str, object]:
        """Report-ready values, JSON types only (``None`` means undefined)."""
        first, best = self.first_call, self.best_call
        return {
            "search_calls": self.search_calls,
            "reformulations": self.reformulations,
            **{f"trajectory_recall@{k}": v for k, v in self.trajectory_recall_at_k.items()},
            "first_call": None if first is None else first.to_dict(),
            "best_call": None if best is None else best.to_dict(),
        }


def _unscored(searches: Sequence[ToolEvent]) -> SearchRetrieval:
    """The counts alone, with every score undefined (no gold, or no search)."""
    return SearchRetrieval(
        calls=(),
        search_calls=len(searches),
        reformulations=len({_query_of(event) for event in searches}),
        trajectory_recall_at_k=dict.fromkeys(RETRIEVAL_K),
    )


def score_search_calls(
    tool_events: Iterable[ToolEvent], gold_files: frozenset[str]
) -> SearchRetrieval:
    """Score every ``search_codebase`` call of one trajectory against its gold.

    ``reformulations`` counts the DISTINCT queries issued: re-asking the same
    query is a repeat, not a reformulation.

    Example:
        >>> from pydocs_eval.trajectory.schema import ToolEvent
        >>> e = ToolEvent(event_id="e", trajectory_id="t", seq=1, ts=0.0, turn=1,
        ...     tool="search_codebase", args={"query": "routing"}, latency_ms=1.0,
        ...     result_ids=({"path": "a.py"},))
        >>> score_search_calls([e], frozenset({"a.py"})).trajectory_recall_at_k[1]
        1.0
    """
    ordered = sorted(tool_events, key=lambda event: event.seq)
    searches = tuple(event for event in ordered if event.tool == SEARCH_TOOL)
    if not searches or not gold_files:
        return _unscored(searches)
    task = _task_for(gold_files)
    rankings = [_ranked_items(event) for event in searches]
    return SearchRetrieval(
        calls=tuple(
            _score_call(event, items, task) for event, items in zip(searches, rankings, strict=True)
        ),
        search_calls=len(searches),
        reformulations=len({_query_of(event) for event in searches}),
        trajectory_recall_at_k={k: _union_recall(rankings, task, k) for k in RETRIEVAL_K},
    )
