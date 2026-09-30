"""``needle cited``: the answer names every gold site, by file or by name.

A site is cited when any alias of its path or of its symbol matches what the
answer cites — except on a needle of one function (the repoqa shape), which
only the function's name cites: the right file with the wrong function is the
plausible-but-wrong stop the guard exists to catch (#366 owner decision,
2026-09-28). On a multi-site record ``needle cited`` needs every site, and
``any_site_cited`` and ``gold_site_coverage`` are its companions; on one site
the three coincide. What an answer lists under ``Not confirmed:`` it did not
confirm, so it cites nothing there.
"""

from __future__ import annotations

import pytest

from pydocs_eval.judge.config import DEFAULT_CITATION_EXTENSIONS
from pydocs_eval.judge.needle_citation import NeedleCitation, NeedleSite, score_needle_citation

_STRATEGIES = NeedleSite("src/needle/scoring/strategies.py", "MaxSimScorer")
_RETRIEVERS = NeedleSite("src/needle/retrieval/page_retrievers.py", "_score")
_DOCS = NeedleSite("docs/retrievers.md", "multi_vector")
# Chat q11's needle: two gold sites, two spans of one file.
_RELEASE_TRIGGER = NeedleSite(".github/workflows/release.yml", "workflow_dispatch")
_RELEASE_GUARD = NeedleSite(".github/workflows/release.yml", "startsWith")
# A repoqa needle named like a plain word (psf/black), and one named like its own file.
_VISIT = NeedleSite("src/black/nodes.py", "visit")
_VISIT_IN_VISIT_PY = NeedleSite("src/pkg/visit.py", "visit")


def _score(answer: str, *sites: NeedleSite) -> NeedleCitation:
    return score_needle_citation(answer, sites, extensions=DEFAULT_CITATION_EXTENSIONS)


@pytest.mark.parametrize(
    "answer",
    [
        "It lives in `src/needle/scoring/strategies.py:42-43`.",
        "See strategies.py:54-60 for the scorer.",
        "Browse https://github.com/o/r/blob/main/src/needle/scoring/strategies.py for it.",
        "The module `needle.scoring.strategies` holds it.",
    ],
)
def test_a_file_only_site_is_cited_by_any_spelling_of_its_file(answer: str) -> None:
    citation = _score(answer, NeedleSite("src/needle/scoring/strategies.py"))

    assert (citation.needle_cited, citation.any_site_cited) == (True, True)
    assert citation.gold_site_coverage == 1.0


@pytest.mark.parametrize(
    "answer",
    [
        "It is `needle.scoring.strategies.MaxSimScorer.score`.",
        "It is `MaxSimScorer`, in `src/needle/scoring/strategies.py`.",
        "It is `MaxSimScorer`.",
    ],
)
def test_a_one_function_needle_is_cited_by_the_functions_name(answer: str) -> None:
    assert _score(answer, _STRATEGIES).needle_cited


@pytest.mark.parametrize(
    "answer",
    [
        "It lives in `src/needle/scoring/strategies.py:42-43`.",
        "Browse https://github.com/o/r/blob/main/src/needle/scoring/strategies.py#L42 for it.",
        "The module `needle.scoring.strategies` holds it.",
        "It is `CosineScorer` in `src/needle/scoring/strategies.py`.",
    ],
)
def test_a_one_function_needle_is_not_cited_by_its_file_alone(answer: str) -> None:
    """The right file with the wrong function — or none — is the stop the guard must catch."""
    citation = _score(answer, _STRATEGIES)

    assert (citation.needle_cited, citation.any_site_cited) == (False, False)


@pytest.mark.parametrize(
    "answer",
    [
        "It is `sklearn/base.py::get_params`.",
        "It is `sklearn/base.py::BaseEstimator.get_params`.",
        "It is `sklearn/base.py::BaseEstimator::get_params`.",
        "It is sklearn/base.py:get_params.",
        "It is [get_params](sklearn/base.py#L120).",
    ],
)
def test_a_function_named_against_its_path_is_cited(answer: str) -> None:
    """The repoqa prompt asks for the function and its path: ``path::fn`` names both."""
    assert _score(answer, NeedleSite("sklearn/base.py", "BaseEstimator.get_params")).needle_cited


@pytest.mark.parametrize(
    ("answer", "needle", "cited"),
    [
        ("It is in `visit.py`.", _VISIT_IN_VISIT_PY, False),
        ("See [visit.py](src/pkg/visit.py).", _VISIT_IN_VISIT_PY, False),
        ("It is in visit.py.", _VISIT, False),
        ("It is `visit()` in `visit.py`.", _VISIT_IN_VISIT_PY, True),
    ],
    ids=["its-own-file", "a-link-to-it", "another-file", "the-name-beside-it"],
)
def test_a_file_name_is_a_path_not_the_functions_name(
    answer: str, needle: NeedleSite, cited: bool
) -> None:
    """``visit.py`` names a file, even when the function is called ``visit`` too (#410)."""
    assert _score(answer, needle).needle_cited is cited


def test_a_function_named_like_its_module_is_cited_by_its_bare_name() -> None:
    """``glob`` in ``src/glob.py``: the module shares the spelling, which still names the function."""
    assert _score("It is `glob`.", NeedleSite("src/glob.py", "glob")).needle_cited


@pytest.mark.parametrize(
    ("answer", "cited"),
    [
        ("It is `get_params`.", True),
        ("It is `BaseEstimator.get_params` in `sklearn/base.py`.", True),
        ("It is `sklearn.base.BaseEstimator.get_params`.", True),
        ("It is in `sklearn/base.py`.", False),
        ("See `sklearn.base`.", False),
    ],
)
def test_a_method_needle_is_cited_by_the_methods_name_only(answer: str, cited: bool) -> None:
    method = NeedleSite("sklearn/base.py", "BaseEstimator.get_params")

    assert _score(answer, method).needle_cited is cited


def test_a_module_part_spelled_like_a_file_still_names_the_function() -> None:
    """``flask.json`` is a package here, not a file: the name after it names the function."""
    dumps = NeedleSite("src/flask/json/provider.py", "dumps")

    assert _score("It is `flask.json.provider.dumps`.", dumps).needle_cited


def test_a_method_named_like_a_citable_extension_reads_as_a_file_only_as_stem_ext() -> None:
    """A written limit (#410): ``Response.json`` is spelled as a file name, so it names no ``json``.

    No repoqa needle is named so. A longer chain, or the bare call, still names it.
    """
    json_method = NeedleSite("src/requests/models.py", "Response.json")

    assert not _score("It is `Response.json()`.", json_method).needle_cited
    assert _score("It is `requests.models.Response.json()`.", json_method).needle_cited
    assert _score("It calls `json()` on the response.", json_method).needle_cited


def test_a_function_named_only_as_not_confirmed_is_not_cited() -> None:
    answer = "It is in `sklearn/base.py`.\nNot confirmed: whether `get_params` is the one.\n"

    assert not _score(answer, NeedleSite("sklearn/base.py", "get_params")).needle_cited


def test_a_function_named_like_a_plain_word_is_cited_only_when_written_as_code() -> None:
    """A written limit (#410): 12 of the 100 repoqa needles are named like words (``visit``)."""
    prose = "The visit method in src/black/nodes.py walks the tree."
    code = "The `visit` method in src/black/nodes.py walks the tree."

    assert (_score(prose, _VISIT).needle_cited, _score(code, _VISIT).needle_cited) == (False, True)


def test_the_functions_name_beside_another_file_still_cites_it() -> None:
    """A written limit (owner, #410): the rule reads the name; a namesake elsewhere is Jev's call."""
    merge = NeedleSite("src/black/trans.py", "_merge_string_group")

    assert _score("It is `_merge_string_group` in `src/black/linegen.py`.", merge).needle_cited


def test_a_needle_of_two_spans_in_one_file_keeps_the_path_rule() -> None:
    """Only a needle of one gold site needs its name.

    q11 has two gold sites, two spans of one file, which the path-or-symbol
    rule scores as one site.
    """
    answer = "The workflow is `.github/workflows/release.yml`."

    assert _score(answer, _RELEASE_TRIGGER, _RELEASE_GUARD).needle_cited


@pytest.mark.parametrize(
    "answer",
    [
        "The scorer computes a max over similarities.",
        "It lives in `other/strategies.py`.",
        "It is `CosineScorer.score` in `needle.scoring.base`.",
    ],
)
def test_an_answer_that_names_something_else_does_not_cite_the_site(answer: str) -> None:
    citation = _score(answer, _STRATEGIES)

    assert (citation.needle_cited, citation.any_site_cited) == (False, False)
    assert citation.gold_site_coverage == 0.0


def test_a_multi_site_needle_is_cited_only_when_every_site_is() -> None:
    answer = "`MaxSimScorer` scores; `MultimodalEmbedderRetriever._score` dispatches."

    citation = _score(answer, _STRATEGIES, _RETRIEVERS, _DOCS)

    assert citation.sites_cited == (True, True, False)
    assert (citation.needle_cited, citation.any_site_cited) == (False, True)
    assert citation.gold_site_coverage == pytest.approx(2 / 3)


def test_a_name_several_files_share_cites_none_of_them_alone() -> None:
    """``matches_filter`` in two files names neither: only the file or module does."""
    utils = NeedleSite("src/needle/retrieval/page_retriever_utils.py", "matches_filter")
    pipeline = NeedleSite("src/needle/pipeline.py", "matches_filter")

    bare = _score("It calls `matches_filter`.", utils, pipeline)
    qualified = _score(
        "It calls `needle.retrieval.page_retriever_utils.matches_filter`.", utils, pipeline
    )

    assert bare.sites_cited == (False, False)
    assert qualified.sites_cited == (True, False)


def test_sites_in_one_file_share_its_short_names() -> None:
    """Two sites of ``strategies.py`` beside a third file: its bare name still cites both."""
    score_all = NeedleSite("src/needle/scoring/strategies.py", "score_all")

    citation = _score("See strategies.py:29-43.", _STRATEGIES, score_all, _RETRIEVERS)

    assert citation.sites_cited == (True, True, False)


def test_a_needle_inside_one_file_is_one_site() -> None:
    """The multi-site rule starts at two gold files: q11's two ``release.yml`` spans are one site."""
    citation = _score("It runs on `workflow_dispatch`.", _RELEASE_TRIGGER, _RELEASE_GUARD)

    assert (citation.needle_cited, citation.any_site_cited, citation.gold_site_coverage) == (
        True,
        True,
        1.0,
    )


def test_multi_location_says_whether_the_needle_spans_several_files() -> None:
    """Two spans of one file are one site; sites in two files are a multi-location needle."""
    one_file = _score("It runs on `workflow_dispatch`.", _RELEASE_TRIGGER, _RELEASE_GUARD)

    assert not one_file.multi_location
    assert _score("It is `MaxSimScorer`.", _STRATEGIES, _DOCS).multi_location


def test_what_the_answer_did_not_confirm_is_not_cited() -> None:
    answer = (
        "The scorer is `MaxSimScorer` (`src/needle/scoring/strategies.py`).\n"
        "Not confirmed: whether `page_retrievers.py` `_score` mirrors it.\n"
    )

    assert _score(answer, _STRATEGIES, _RETRIEVERS).sites_cited == (True, False)


def test_cited_path_precision_is_the_share_of_cited_files_that_are_gold() -> None:
    answer = "See `src/needle/scoring/strategies.py` and `src/needle/config.py`."

    assert _score(answer, _STRATEGIES).cited_path_precision == 0.5
    assert _score("It is `MaxSimScorer`.", _STRATEGIES).cited_path_precision is None


def test_cited_path_precision_counts_files_not_spellings() -> None:
    """A file cited by two spellings is one file, gold or not."""
    gold_twice = (
        "See `strategies.py` (`src/needle/scoring/strategies.py:42`) and `src/needle/util.py`."
    )
    other_twice = "See `src/needle/scoring/strategies.py`, `util.py` and `src/needle/util.py`."

    assert _score(gold_twice, _STRATEGIES, _RETRIEVERS).cited_path_precision == 0.5
    assert _score(other_twice, _STRATEGIES, _RETRIEVERS).cited_path_precision == 0.5


def test_a_root_gold_file_and_a_deeper_namesake_are_two_files() -> None:
    """``README.md`` is the gold; ``docs/README.md`` is another file, not a spelling of it."""
    readme = NeedleSite("README.md", "Usage")

    assert _score("See `README.md` and `docs/README.md`.", readme).cited_path_precision == 0.5


def test_a_needle_without_sites_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one site"):
        _score("anything")
