"""Which names an answer writes as code — the symbol half of what it cites.

A name counts when the answer writes it as code: inside backticks or a fenced
block, as a dotted chain, called, or spelled as only code is (snake_case, an
inner capital). A plain prose word never does, so an answer that merely uses the
word "search" does not cite a symbol named ``search``.
"""

from __future__ import annotations

import time

import pytest

from pydocs_eval.judge.config import DEFAULT_CITATION_EXTENSIONS
from pydocs_eval.judge.needle_citation import extract_citations, extract_dotted_names


def test_a_dotted_chain_yields_every_contiguous_part() -> None:
    names = extract_dotted_names("It is **`needle.scoring.strategies.MaxSimScorer.score`**.")

    assert {
        "needle.scoring.strategies.MaxSimScorer.score",
        "needle.scoring.strategies",
        "needle.scoring.strategies.MaxSimScorer",
        "MaxSimScorer.score",
        "MaxSimScorer",
        "score",
    } <= names


def test_a_single_name_counts_only_when_written_as_code() -> None:
    names = extract_dotted_names("The search runs first; then `score_all` and rank(docs) follow.")

    assert "score_all" in names
    assert "rank" in names, "a called name is code"
    assert "search" not in names
    assert "runs" not in names


def test_a_prose_word_spelled_as_only_code_is_counts() -> None:
    """A repoqa answer often names its function bare: ``get_params`` is no English word."""
    names = extract_dotted_names("get_params is defined next to MaxSimScorer and the Retriever.")

    assert {"get_params", "MaxSimScorer"} <= names
    assert "Retriever" not in names, "a capitalised word is still English"
    assert "defined" not in names


def test_a_dotted_chain_in_prose_is_code() -> None:
    names = extract_dotted_names("Then needle.pipeline.RetrievalPipeline answers the query.")

    assert {"needle.pipeline", "RetrievalPipeline", "needle.pipeline.RetrievalPipeline"} <= names


def test_a_nested_name_on_a_path_keeps_only_its_first_part() -> None:
    """Every needle but a one-function one reads pytest's ``a/b.py::Cls::fn`` as #409 did.

    The owner kept those needles' scores, and the Jev audit's candidates that
    read this function, as they were (decision 1 on #366). #410 reads the
    nested name whole on a one-function needle only, so widening this reading
    would move them.
    """
    assert extract_dotted_names("It is `a/b.py::Cls::fn`.") == {"Cls"}


def test_names_inside_a_fenced_block_count() -> None:
    answer = "Example:\n\n```python\nscorer = get_scorer(multi_vector=True)\n```\n"

    assert {"scorer", "get_scorer", "multi_vector"} <= extract_dotted_names(answer)


def test_a_file_path_contributes_no_names() -> None:
    names = extract_dotted_names("See `src/needle/scoring/strategies.py:42-43`.")

    assert "scoring" not in names
    assert "needle" not in names


def test_a_runaway_dotted_run_yields_only_name_sized_parts() -> None:
    """A degenerate 12 KB ``a.a.a…`` answer: every part of a chain was cubic (256 s)."""
    names = extract_dotted_names("`" + ".".join(["a"] * 6000) + "`")

    assert max(name.count(".") + 1 for name in names) == 16


@pytest.mark.parametrize(
    "runaway",
    ["[x](" * 30000, ".".join(["a"] * 60000), "abc.def/" * 15000],
    ids=["brackets", "dotted", "slashed"],
)
def test_a_120_kb_runaway_answer_scores_in_linear_time(runaway: str) -> None:
    """Every scan is anchored: unanchored, one of these took 42-265 s; each now takes ms."""
    start = time.perf_counter()

    extract_dotted_names(runaway)
    extract_citations(runaway, extensions=DEFAULT_CITATION_EXTENSIONS)

    assert time.perf_counter() - start < 5.0
