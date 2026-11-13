"""The alignment draw: which stored answers become alignment items (spec 9d, Q58).

The pool is every stored agent answer the A/A arms and the forced-cap arms
wrote, less the ``reserved`` held-out questions, which never enter it: they are
consumed once, by final confirmation. A draw takes four strata — answers Jev
put in its borderline window on a first pass, answers where Jev's verdict
(outside ``band_start``) contradicts the code check, answers with a vendored
injection planted in them (inside the item count), and a random share that
keeps the set representative. Planted injections are hosted by answers outside
the two active strata, which are the scarce ones. A draw is seeded, and never
draws an item the label file already holds.

Pass 2 labels the swe-qa-questions answers, and refuses to run until that
dataset's dev/test slices exist (the unlock: ``SWE_QA_QUESTION_SLICES`` in
``pydocs_eval.datasets.swe_qa_question_slices``, step 7d).

Example:
    >>> pool = AlignmentPool.of(candidates, reserved=reserved_ids)  # doctest: +SKIP
    >>> select_alignment_items(pool, size=10, config=config, injections=injections, seed=0)  # doctest: +SKIP
"""

from __future__ import annotations

import hashlib
import importlib
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from pydocs_eval.judge.alignment_request import alignment_plan
from pydocs_eval.judge.alignment_rows import (
    AlignmentItemContext,
    AlignmentLabelRow,
    AnswerSource,
    CodeCheck,
    LabellerEntry,
)
from pydocs_eval.judge.alignment_vocabulary import AlignmentPass, AlignmentStratum
from pydocs_eval.judge.config import JevConfig
from pydocs_eval.judge.jev_questions import SCORE_KINDS, JevQuestionKind, kind_of
from pydocs_eval.judge.jev_requests import JudgedDataset, JudgedSite
from pydocs_eval.judge.needle_citation import NeedleSite, score_needle_citation
from pydocs_eval.judge.planted_injections import PlantedInjection, planted_answer
from pydocs_eval.judge.role_config import AlignmentConfig
from pydocs_eval.judge.thresholds import JevVerdict, NoulBand, noul_verdict

# The swe-qa-questions unlock (step 7d): the module that defines its slices, and the name.
_UNLOCK_MODULE = "pydocs_eval.datasets.swe_qa_question_slices"
_UNLOCK_NAME = "SWE_QA_QUESTION_SLICES"
_ITEM_ID_CHARS = 16
_ACTIVE = (AlignmentStratum.BORDERLINE, AlignmentStratum.CODE_VS_JEV)
# The per-location question each multi-location dataset asks once per gold site or file.
_LOCATED_KIND = {
    JudgedDataset.EXAMPLE_NEEDLE_CHAT: JevQuestionKind.SITE,
    JudgedDataset.SWE_QA_QUESTIONS: JevQuestionKind.GOLD_FILE,
}


class AlignmentPassLockedError(Exception):
    """An alignment pass asked for before the data it labels can exist."""


@dataclass(frozen=True, slots=True)
class AlignmentCandidate:
    """One stored answer the alignment set may draw, with what the strata read of it.

    ``jev_probabilities`` are a first Jev pass's yes-probabilities by question
    id; empty when no first pass ran, which leaves the answer in no active stratum.
    """

    dataset: JudgedDataset
    task_id: str
    source: AnswerSource
    answer: str
    code_check: CodeCheck
    jev_probabilities: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class AlignmentPool:
    """The answers a draw may take: every candidate but the reserved questions'."""

    candidates: tuple[AlignmentCandidate, ...]
    excluded_reserved: int

    @classmethod
    def of(
        cls, candidates: Iterable[AlignmentCandidate], *, reserved: frozenset[str]
    ) -> AlignmentPool:
        """``candidates`` without any whose task is one of ``reserved``."""
        every = list(candidates)
        kept = tuple(candidate for candidate in every if candidate.task_id not in reserved)
        return cls(kept, excluded_reserved=len(every) - len(kept))


@dataclass(frozen=True, slots=True)
class DrawnItem:
    """One drawn answer, the stratum it was drawn for, and the injection planted in it."""

    candidate: AlignmentCandidate
    stratum: AlignmentStratum
    injection: PlantedInjection | None = None

    @property
    def item_id(self) -> str:
        injection = self.injection.id if self.injection else ""
        return alignment_item_id(self.candidate, injection)


def alignment_item_id(candidate: AlignmentCandidate, injection_id: str = "") -> str:
    """A stable id for one answer, planted or not: a batch row id and the label file's key.

    Example:
        >>> len(alignment_item_id(candidate))  # doctest: +SKIP
        16
    """
    parts = (
        candidate.dataset.value,
        candidate.task_id,
        candidate.source.trajectory_id,
        injection_id,
    )
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:_ITEM_ID_CHARS]


def stratum_of(candidate: AlignmentCandidate, config: AlignmentConfig) -> AlignmentStratum:
    """The active stratum ``candidate`` belongs to, else ``RANDOM``; borderline comes first."""
    low, high = config.borderline_window
    nouls = {
        question_id: probability
        for question_id, probability in candidate.jev_probabilities.items()
        if kind_of(question_id) not in SCORE_KINDS
    }
    if any(low <= probability <= high for probability in nouls.values()):
        return AlignmentStratum.BORDERLINE
    band = NoulBand(low=config.band_start[0], high=config.band_start[1])
    for question_id, cited in candidate.code_check.verdicts.items():
        verdict = noul_verdict(nouls[question_id], band) if question_id in nouls else None
        if verdict in (JevVerdict.TRUE, JevVerdict.FALSE) and (verdict is JevVerdict.TRUE) != cited:
            return AlignmentStratum.CODE_VS_JEV
    return AlignmentStratum.RANDOM


def select_alignment_items(
    pool: AlignmentPool,
    *,
    size: int,
    config: AlignmentConfig,
    injections: Sequence[PlantedInjection],
    seed: int,
    existing: Sequence[AlignmentLabelRow] = (),
) -> tuple[DrawnItem, ...]:
    """Up to ``size`` new items from ``pool``: planted injections, the active strata, then at random.

    ``existing`` are the rows the label file already holds: none of their items
    is drawn again, and the injections they planted count against
    ``planted_injections``.
    """
    held = {row.item_id for row in existing}
    fresh = [c for c in _shuffled(pool.candidates, seed) if alignment_item_id(c) not in held]
    planted = _planted(fresh, _injections_to_plant(injections, existing, size, config), config)
    remaining = _without(fresh, planted)
    random_count = min(round(config.random_share * size), size - len(planted))
    active = _active(remaining, size - len(planted) - random_count, config)
    shortfall = size - len(planted) - len(active)
    at_random = [
        DrawnItem(candidate, AlignmentStratum.RANDOM)
        for candidate in _without(remaining, active)[:shortfall]
    ]
    return (*planted, *active, *at_random)


def _injections_to_plant(
    injections: Sequence[PlantedInjection],
    existing: Sequence[AlignmentLabelRow],
    size: int,
    config: AlignmentConfig,
) -> list[PlantedInjection]:
    """The injections still to plant: ``planted_injections`` less those the label file holds."""
    planted_ids = {row.planted_injection for row in existing if row.planted_injection}
    unused = [injection for injection in injections if injection.id not in planted_ids]
    wanted = max(0, config.planted_injections - len(planted_ids))
    return unused[: min(wanted, size)]


def _without(
    candidates: Sequence[AlignmentCandidate], drawn: Sequence[DrawnItem]
) -> list[AlignmentCandidate]:
    """``candidates`` less the answers ``drawn`` already took (planted or not)."""
    taken = {alignment_item_id(item.candidate) for item in drawn}
    return [candidate for candidate in candidates if alignment_item_id(candidate) not in taken]


def _shuffled(candidates: Sequence[AlignmentCandidate], seed: int) -> list[AlignmentCandidate]:
    """``candidates`` in a seeded order that does not depend on the order they came in."""
    ordered = sorted(candidates, key=lambda c: (c.dataset.value, c.task_id, c.source.trajectory_id))
    random.Random(seed).shuffle(ordered)
    return ordered


def _planted(
    candidates: Sequence[AlignmentCandidate],
    injections: Sequence[PlantedInjection],
    config: AlignmentConfig,
) -> list[DrawnItem]:
    """One host per injection, taken from outside the active strata while any is left."""
    hosts = sorted(candidates, key=lambda c: stratum_of(c, config) in _ACTIVE)
    return [
        DrawnItem(host, AlignmentStratum.PLANTED_INJECTION, injection)
        for host, injection in zip(hosts, injections, strict=False)
    ]


def _active(
    candidates: Sequence[AlignmentCandidate], count: int, config: AlignmentConfig
) -> list[DrawnItem]:
    """Up to ``count`` items taken in turn from the borderline and the code-vs-Jev strata."""
    queues = {
        stratum: [c for c in candidates if stratum_of(c, config) is stratum] for stratum in _ACTIVE
    }
    drawn: list[DrawnItem] = []
    while len(drawn) < count and any(queues.values()):
        for stratum in _ACTIVE:
            if queues[stratum] and len(drawn) < count:
                drawn.append(DrawnItem(queues[stratum].pop(0), stratum))
    return drawn


def alignment_row(
    item: DrawnItem,
    *,
    context: AlignmentItemContext,
    jev_model: str,
    labeller_models: tuple[str, str],
    jev: JevConfig,
) -> AlignmentLabelRow:
    """``item`` as a label-file row, asked of no labeller yet; ``context`` is its task's ground truth.

    A planted item's answer is the host's with the injection after it, and its
    code check is the planted answer's.
    """
    if item.injection is not None:
        planted = planted_answer(context.agent_answer, item.injection)
        context = replace(context, agent_answer=planted)
    candidate = item.candidate
    astra_model, opus_model = labeller_models
    return AlignmentLabelRow(
        item_id=item.item_id,
        dataset=candidate.dataset,
        jev_model=jev_model,
        task_id=candidate.task_id,
        source=candidate.source,
        stratum=item.stratum,
        context=context,
        questions=alignment_plan(context, jev=jev).question_ids,
        code_check=code_check_of(
            candidate.dataset,
            context.gold,
            context.agent_answer,
            extensions=jev.citation_extensions,
        ),
        astra=LabellerEntry(model=astra_model),
        opus=LabellerEntry(model=opus_model),
        planted_injection=item.injection.id if item.injection else "",
    )


def code_check_of(
    dataset: JudgedDataset,
    gold: Sequence[JudgedSite],
    answer: str,
    *,
    extensions: Sequence[str],
) -> CodeCheck:
    """What the deterministic check says of ``answer``, and its verdict on each gold question.

    On repoqa-qa the one gold question is ``needle_identified``. On a
    multi-location needle each ``site_<i>`` (or ``gold_file_<i>``) reads its own
    site's flag; on a needle inside one file every site question reads the one
    flag, since citing the file cites all its sites.
    """
    sites = [NeedleSite(site.path, site.symbol) for site in gold]
    citation = score_needle_citation(answer, sites, extensions=extensions)
    if dataset is JudgedDataset.REPOQA_QA:
        verdicts = {JevQuestionKind.NEEDLE_IDENTIFIED.value: citation.needle_cited}
    else:
        flags = (
            citation.sites_cited
            if citation.multi_location
            else (citation.needle_cited,) * len(sites)
        )
        kind = _LOCATED_KIND[dataset].value
        verdicts = {f"{kind}_{index}": flag for index, flag in enumerate(flags)}
    return CodeCheck(citation.needle_cited, citation.gold_site_coverage, verdicts)


def swe_qa_questions_unlocked(module: str = _UNLOCK_MODULE) -> bool:
    """Whether the swe-qa-questions slices have landed: ``module`` defines ``SWE_QA_QUESTION_SLICES``.

    Example:
        >>> swe_qa_questions_unlocked("no_such_module_anywhere")
        False
    """
    try:
        loaded = importlib.import_module(module)
    except ModuleNotFoundError:
        return False
    return hasattr(loaded, _UNLOCK_NAME)


def refuse_locked_pass(
    alignment_pass: AlignmentPass, datasets: Iterable[JudgedDataset], *, unlocked: bool
) -> None:
    """Refuse pass 2, or any swe-qa-questions item, before the swe-qa-questions unlock.

    Raises:
        AlignmentPassLockedError: the pass needs swe-qa-questions answers that cannot exist yet.
    """
    needs_unlock = alignment_pass is AlignmentPass.PASS_2 or JudgedDataset.SWE_QA_QUESTIONS in set(
        datasets
    )
    if needs_unlock and not unlocked:
        raise AlignmentPassLockedError(
            f"alignment {alignment_pass.value} labels swe-qa-questions answers, which need the "
            f"swe-qa-questions unlock ({_UNLOCK_NAME} in {_UNLOCK_MODULE}, step 7d); it has not "
            "landed, so the pass refuses to run"
        )


__all__ = (
    "AlignmentCandidate",
    "AlignmentPassLockedError",
    "AlignmentPool",
    "DrawnItem",
    "alignment_item_id",
    "alignment_row",
    "code_check_of",
    "refuse_locked_pass",
    "select_alignment_items",
    "stratum_of",
    "swe_qa_questions_unlocked",
)
