"""Each stored answer, scored against its needle by code — the report's answer rows.

``needle cited`` is the correctness guard the acceptance rule reads (ADR 0025),
so it is computed here, from what the run persisted, before any judge is asked.
A task's needle comes from its dataset's gold: a chat record's sites pair each
span with its symbol, a repoqa-qa needle is one file and its function, and any
other gold names its files.

Example:
    >>> key = answer_key_for(tasks, load_judge_config().jev)  # doctest: +SKIP
    >>> key.sites_by_task["repoqa-qa/repo_qa/x"]  # doctest: +SKIP
    (NeedleSite(path='sklearn/base.py', symbol='get_params'),)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pydocs_eval.campaign.before_after_arm import ArmTaskRecord
from pydocs_eval.campaign.before_after_task_measurement import UNSCORED_ANSWER, AnswerScore
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.example_needle_chat import gold_sites_of
from pydocs_eval.datasets.repo_qa import GOLD_SYMBOL_KEY
from pydocs_eval.judge.config import JevConfig
from pydocs_eval.judge.needle_citation import (
    NeedleSite,
    is_multi_location,
    score_needle_citation,
)


@dataclass(frozen=True, slots=True)
class AnswerKey:
    """What a run's stored answers are scored against: each task's needle, under ``jev``."""

    sites_by_task: Mapping[str, tuple[NeedleSite, ...]]
    jev: JevConfig

    def score(self, record: ArmTaskRecord) -> AnswerScore:
        """``record``'s stored answer against its task's needle (:func:`score_task_answer`)."""
        return score_task_answer(record, self.sites_by_task.get(record.task_id, ()), self.jev)


#: No needle known for any task, under the shipped Jev defaults.
NO_ANSWER_KEY = AnswerKey(sites_by_task=MappingProxyType({}), jev=JevConfig())


def answer_key_for(tasks: Iterable[EvalTask], jev: JevConfig) -> AnswerKey:
    """Every task's needle, keyed by task id, scored under ``jev``."""
    return AnswerKey({task.task_id: needle_sites_of(task) for task in tasks}, jev)


def needle_sites_of(task: EvalTask) -> tuple[NeedleSite, ...]:
    """Where ``task``'s answer must point: its gold sites, by file and symbol."""
    chat_sites = gold_sites_of(task)
    if chat_sites:
        return tuple(NeedleSite(site.path, site.symbol) for site in chat_sites)
    symbol = task.gold.extra.get(GOLD_SYMBOL_KEY)
    if isinstance(symbol, str) and len(task.gold.file_set) == 1:
        return (NeedleSite(task.gold.file_set[0], symbol),)
    return tuple(NeedleSite(path) for path in task.gold.file_set)


def score_task_answer(
    record: ArmTaskRecord, sites: tuple[NeedleSite, ...], jev: JevConfig
) -> AnswerScore:
    """What ``record``'s stored answer names of ``sites``; undefined where it cannot say.

    A row stored before answers were (``UNRECORDED``) is undefined throughout. A
    task with no known needle still says whether its answer ran over the cap,
    which reads the answer alone. An unanswered run stored an empty answer,
    which cites nothing: that is a measured miss.
    """
    if not record.outcome.is_recorded:
        return UNSCORED_ANSWER
    over_cap = int(len(record.answer) > jev.max_answer_chars)
    if not sites:
        return AnswerScore(answer_over_cap=over_cap)
    citation = score_needle_citation(record.answer, sites, extensions=jev.citation_extensions)
    multi_location = is_multi_location(site.path for site in sites)
    return AnswerScore(
        needle_cited=int(citation.needle_cited),
        gold_site_coverage=citation.gold_site_coverage if multi_location else None,
        cited_path_precision=citation.cited_path_precision if multi_location else None,
        answer_over_cap=over_cap,
    )


__all__ = (
    "NO_ANSWER_KEY",
    "AnswerKey",
    "answer_key_for",
    "needle_sites_of",
    "score_task_answer",
)
