"""Each stored answer, scored against its needle by code — the report's answer rows.

``needle cited`` is the correctness guard the acceptance rule reads (ADR 0025),
so it is computed here, from what the run persisted, before any judge is asked.
A task's needle comes from its dataset's gold: a chat record's sites pair each
span with its symbol, a repoqa-qa needle is one file and its function, and any
other gold names its files.

Example:
    >>> needle_sites_by_task(tasks)["repoqa-qa/repo_qa/x"]  # doctest: +SKIP
    (NeedleSite(path='sklearn/base.py', symbol='get_params'),)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydocs_eval.campaign.before_after_arm import ArmTaskRecord
from pydocs_eval.campaign.before_after_task_measurement import UNSCORED_ANSWER, AnswerScore
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.example_needle_chat import SITE_KEY_PREFIX, SYMBOL_KEY_PREFIX
from pydocs_eval.datasets.repo_qa import GOLD_SYMBOL_KEY
from pydocs_eval.judge.config import JevConfig
from pydocs_eval.judge.needle_citation import NeedleSite, score_needle_citation

#: Where each task of a run must point, by task id.
NeedleSites = Mapping[str, tuple[NeedleSite, ...]]


def needle_sites_of(task: EvalTask) -> tuple[NeedleSite, ...]:
    """Where ``task``'s answer must point: its gold sites, by file and symbol."""
    spans = _site_spans(task)
    if spans:
        return tuple(
            NeedleSite(
                span.rsplit(":", 1)[0], str(task.gold.extra.get(f"{SYMBOL_KEY_PREFIX}{i}", ""))
            )
            for i, span in enumerate(spans)
        )
    symbol = task.gold.extra.get(GOLD_SYMBOL_KEY)
    if isinstance(symbol, str) and len(task.gold.file_set) == 1:
        return (NeedleSite(task.gold.file_set[0], symbol),)
    return tuple(NeedleSite(path) for path in task.gold.file_set)


def needle_sites_by_task(tasks: Iterable[EvalTask]) -> dict[str, tuple[NeedleSite, ...]]:
    """Every task's needle, keyed by task id — what the report scores answers against."""
    return {task.task_id: needle_sites_of(task) for task in tasks}


def _site_spans(task: EvalTask) -> list[str]:
    """``site_0``, ``site_1``, … in index order, up to the first missing one."""
    spans: list[str] = []
    while (span := task.metadata.get(f"{SITE_KEY_PREFIX}{len(spans)}")) is not None:
        spans.append(span)
    return spans


def score_task_answer(
    record: ArmTaskRecord, sites: tuple[NeedleSite, ...], jev: JevConfig
) -> AnswerScore:
    """What ``record``'s stored answer names of ``sites``; unscored when it cannot say.

    A row stored before answers were (``UNRECORDED``) and a task with no known
    needle are undefined, never zero. An unanswered run stored an empty answer,
    which cites nothing: that is a measured miss.
    """
    if not sites or not record.outcome.is_recorded:
        return UNSCORED_ANSWER
    citation = score_needle_citation(record.answer, sites, extensions=jev.citation_extensions)
    multi_location = len({site.path for site in sites}) > 1
    return AnswerScore(
        needle_cited=int(citation.needle_cited),
        gold_site_coverage=citation.gold_site_coverage if multi_location else None,
        cited_path_precision=citation.cited_path_precision if multi_location else None,
        answer_over_cap=int(len(record.answer) > jev.max_answer_chars),
    )


__all__ = (
    "NeedleSites",
    "needle_sites_by_task",
    "needle_sites_of",
    "score_task_answer",
)
