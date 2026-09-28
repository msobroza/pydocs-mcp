"""The ten repro answers, hand-labelled: what each cites, and q01 / q08 site by site.

These are real agent answers (the 2026-09-24 chat repro over example_needle at
9c170b02), vendored verbatim with labels a person wrote by reading them: the
files each answer cites, and, for the two multi-site questions, which gold site
it names. q01 misses the second mask in ``pipeline.py`` and never cites the
filters doc; q08 names the constants, the config and the PDF extractor but none
of the other defaults.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_eval.judge.config import DEFAULT_CITATION_EXTENSIONS
from pydocs_eval.judge.needle_citation import NeedleSite, extract_citations, score_needle_citation

_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "judge" / "example_needle_chat_repro_answers.jsonl"
)


def _rows() -> list[dict]:
    return [json.loads(line) for line in _FIXTURE.read_text(encoding="utf-8").splitlines()]


def _row(question: str) -> dict:
    (row,) = [row for row in _rows() if row["task_id"].endswith(question)]
    return row


@pytest.mark.parametrize("row", _rows(), ids=lambda row: row["task_id"].rsplit("/", 1)[1])
def test_each_answer_cites_exactly_the_hand_labelled_files(row: dict) -> None:
    cited = extract_citations(row["answer"], extensions=DEFAULT_CITATION_EXTENSIONS)

    assert set(cited) == set(row["cited_paths"])


@pytest.mark.parametrize(("question", "cited", "sites"), [("q01", 4, 6), ("q08", 4, 12)])
def test_a_multi_site_answer_is_scored_site_by_site_as_labelled(
    question: str, cited: int, sites: int
) -> None:
    row = _row(question)
    needle = [NeedleSite(site["path"], site["symbol"]) for site in row["sites"]]

    citation = score_needle_citation(row["answer"], needle, extensions=DEFAULT_CITATION_EXTENSIONS)

    assert list(citation.sites_cited) == row["sites_cited"]
    assert (citation.needle_cited, citation.any_site_cited) == (False, True)
    assert citation.gold_site_coverage == pytest.approx(cited / sites)
