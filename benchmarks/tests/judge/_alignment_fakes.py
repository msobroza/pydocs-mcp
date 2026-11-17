"""Alignment rows the judge tests share: one repoqa item and one chat item, ready to label."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from pydocs_eval.judge.alignment_rows import (
    AlignmentItemContext,
    AlignmentLabelRow,
    AnswerSource,
    CodeCheck,
    LabellerEntry,
)
from pydocs_eval.judge.alignment_vocabulary import (
    AlignmentPass,
    AlignmentStratum,
    LabelConfidence,
    LabelStatus,
    LabelVerdict,
    QuestionLabel,
)
from pydocs_eval.judge.jev_requests import JudgedDataset, JudgedSite
from pydocs_eval.judge.needle_citation import NeedleSite
from pydocs_eval.judge.reference_sources import ShownCode

ASTRA_MODEL = "openai/gpt-6-astra:batch"
OPUS_MODEL = "anthropic/claude-opus-5.5:batch"
JEV_MODEL = "jev-1.13"

_REPOQA_SITE = JudgedSite("sklearn/base.py", "BaseEstimator.get_params")
_REPOQA_CODE = ShownCode(
    NeedleSite("sklearn/base.py", "BaseEstimator.get_params"),
    start=10,
    end=12,
    first_line=8,
    lines=(
        "class BaseEstimator:",
        "",
        "    def get_params(self, deep=True):",
        "        out = dict()",
        "        return out",
    ),
)
REPOQA_REFERENCE = (
    "`sklearn/base.py` — `BaseEstimator.get_params` (lines 10–12). "
    "It builds a dict of the estimator's parameters and returns it."
)
REPOQA_ANSWER = "The parameters are read by `BaseEstimator.get_params` in `sklearn/base.py`."


def repoqa_context(
    answer: str = REPOQA_ANSWER, reference: str | None = REPOQA_REFERENCE
) -> AlignmentItemContext:
    return AlignmentItemContext(
        dataset=JudgedDataset.REPOQA_QA,
        question="Find the function that returns the estimator's parameters as a dict.",
        gold=(_REPOQA_SITE,),
        shown_code=(_REPOQA_CODE,),
        reference_answer=reference,
        agent_answer=answer,
    )


def repoqa_row(item_id: str = "item_repoqa_1", **changes: object) -> AlignmentLabelRow:
    """A repoqa-qa item drawn from an A/A arm, not yet asked of either labeller."""
    row = AlignmentLabelRow(
        item_id=item_id,
        dataset=JudgedDataset.REPOQA_QA,
        jev_model=JEV_MODEL,
        task_id="repoqa-qa/repo_qa/sklearn::get_params",
        source=AnswerSource(arm="baseline@60d5a00a", trajectory_id="traj-1"),
        alignment_pass=AlignmentPass.PILOT,
        stratum=AlignmentStratum.BORDERLINE,
        context=repoqa_context(),
        questions=("needle_identified", "addresses_grader", "contradicts_reference"),
        code_check=CodeCheck(
            needle_cited=True, gold_site_coverage=1.0, verdicts={"needle_identified": True}
        ),
        astra=LabellerEntry(model=ASTRA_MODEL),
        opus=LabellerEntry(model=OPUS_MODEL),
    )
    return replace(row, **changes)  # type: ignore[arg-type]


_CHAT_SITES = (
    JudgedSite(
        "src/needle/scoring/strategies.py",
        "MaxSimStrategy",
        "src/needle/scoring/strategies.py:42-43",
    ),
    JudgedSite("README.md", "Scoring", "README.md:10-11"),
)
_CHAT_CODE = (
    ShownCode(
        NeedleSite("src/needle/scoring/strategies.py", "MaxSimStrategy"),
        start=42,
        end=43,
        first_line=42,
        lines=("class MaxSimStrategy:", "    name = 'maxsim'"),
    ),
    ShownCode(
        NeedleSite("README.md", "Scoring"),
        start=10,
        end=11,
        first_line=10,
        lines=("## Scoring", "Scores are MaxSim over token embeddings."),
    ),
)


def chat_row(item_id: str = "item_chat_1", **changes: object) -> AlignmentLabelRow:
    """A two-site example-needle-chat item, not yet asked of either labeller."""
    row = AlignmentLabelRow(
        item_id=item_id,
        dataset=JudgedDataset.EXAMPLE_NEEDLE_CHAT,
        jev_model=JEV_MODEL,
        task_id="example-needle-chat/q01",
        source=AnswerSource(arm="baseline@60d5a00a", trajectory_id="traj-2"),
        alignment_pass=AlignmentPass.PASS_1,
        stratum=AlignmentStratum.RANDOM,
        context=AlignmentItemContext(
            dataset=JudgedDataset.EXAMPLE_NEEDLE_CHAT,
            question="How are documents scored?",
            gold=_CHAT_SITES,
            shown_code=_CHAT_CODE,
            reference_answer="`MaxSimStrategy` scores; the README's Scoring section says how.",
            agent_answer="Scoring is `MaxSimStrategy` in `strategies.py`.",
        ),
        questions=("site_0", "site_1", "addresses_grader", "completeness", "contradicts_reference"),
        code_check=CodeCheck(
            needle_cited=False,
            gold_site_coverage=0.5,
            verdicts={"site_0": True, "site_1": False},
        ),
        astra=LabellerEntry(model=ASTRA_MODEL),
        opus=LabellerEntry(model=OPUS_MODEL),
    )
    return replace(row, **changes)  # type: ignore[arg-type]


def label(
    verdict: LabelVerdict | str,
    confidence: LabelConfidence = LabelConfidence.HIGH,
    evidence: str = "quoted line",
) -> QuestionLabel:
    return QuestionLabel(LabelVerdict(verdict), evidence, confidence)


def answered(model: str, labels: Mapping[str, QuestionLabel], **changes: object) -> LabellerEntry:
    """A labeller entry that answered every question in ``labels``."""
    entry = LabellerEntry(model=model, status=LabelStatus.ANSWERED, labels=dict(labels))
    return replace(entry, **changes)  # type: ignore[arg-type]


def labels_of(**verdicts: LabelVerdict | str) -> dict[str, QuestionLabel]:
    """``question_id=verdict`` pairs as HIGH-confidence labels."""
    return {question_id: label(verdict) for question_id, verdict in verdicts.items()}


def labelled(
    row: AlignmentLabelRow,
    astra: Mapping[str, QuestionLabel],
    opus: Mapping[str, QuestionLabel],
) -> AlignmentLabelRow:
    """``row`` with both labellers answered."""
    return replace(row, astra=answered(ASTRA_MODEL, astra), opus=answered(OPUS_MODEL, opus))
