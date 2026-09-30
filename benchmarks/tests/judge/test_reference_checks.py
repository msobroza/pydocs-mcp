"""The two code checks every reference answer passes before it is kept (judge 9c).

repoqa: the gold path and symbol present and no other file path. chat: every
gold site's path and symbol present and no path outside the gold sites. A path
counts as the gold's when it is one of its aliases (the ``src/``-less path, a
trailing part), so a reference may shorten a path it has already named.
"""

from __future__ import annotations

import pytest

from pydocs_eval.gold_extensions import GOLD_FILE_EXTENSIONS
from pydocs_eval.judge.needle_citation import NeedleSite
from pydocs_eval.judge.reference_checks import check_chat_reference, check_repoqa_reference

_NEEDLE = NeedleSite("fixture_repo/math_helpers.py", "factorial")
_PASSING_REPOQA = (
    "`fixture_repo/math_helpers.py` — `factorial` (lines 3–6)\n\n"
    "`factorial` returns n! by multiplying every integer from 1 to n; "
    "`math_helpers.py` keeps it beside the other number helpers."
)
_SITES = (
    NeedleSite("src/needle/scoring/strategies.py", "MaxSimScorer"),
    NeedleSite("src/needle/retrieval/page_retrievers.py", "_score"),
)
_PASSING_CHAT = (
    "`src/needle/scoring/strategies.py` — `MaxSimScorer` (lines 39–43)\n"
    "`src/needle/retrieval/page_retrievers.py` — `_score` (lines 261–265)\n\n"
    "`MaxSimScorer` sums each query token's best match; `_score` calls it per page."
)


def _repoqa(text: str) -> tuple[str, ...]:
    return check_repoqa_reference(text, _NEEDLE, extensions=GOLD_FILE_EXTENSIONS).problems


def _chat(text: str) -> tuple[str, ...]:
    return check_chat_reference(text, _SITES, extensions=GOLD_FILE_EXTENSIONS).problems


def test_a_repoqa_reference_naming_the_gold_path_and_symbol_passes() -> None:
    assert check_repoqa_reference(_PASSING_REPOQA, _NEEDLE, extensions=GOLD_FILE_EXTENSIONS).passed


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("`math_helpers.py` — `factorial` returns n!.", "path 'fixture_repo/math_helpers.py'"),
        ("`fixture_repo/math_helpers.py` returns n! for n.", "symbol 'factorial'"),
        (
            f"{_PASSING_REPOQA} It is tested in `tests/test_math.py`.",
            "file outside the gold: 'tests/test_math.py'",
        ),
    ],
    ids=["no-gold-path", "no-symbol", "another-file"],
)
def test_a_repoqa_reference_that_misses_the_gold_or_adds_a_file_fails(
    text: str, problem: str
) -> None:
    (only,) = _repoqa(text)

    assert problem in only


def test_a_chat_reference_naming_every_gold_site_passes() -> None:
    assert check_chat_reference(_PASSING_CHAT, _SITES, extensions=GOLD_FILE_EXTENSIONS).passed


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (
            _PASSING_CHAT.replace("`_score` (lines 261–265)", "(lines 261–265)").replace(
                "; `_score` calls it per page", ""
            ),
            "symbol '_score'",
        ),
        (
            _PASSING_CHAT.replace("`src/needle/retrieval/page_retrievers.py`", "the retriever"),
            "path 'src/needle/retrieval/page_retrievers.py'",
        ),
        (
            f"{_PASSING_CHAT} See also `docs/filters.md`.",
            "file outside the gold: 'docs/filters.md'",
        ),
    ],
    ids=["a-site-without-its-symbol", "a-site-without-its-path", "a-file-outside-the-gold"],
)
def test_a_chat_reference_that_misses_a_site_or_adds_a_file_fails(text: str, problem: str) -> None:
    (only,) = _chat(text)

    assert problem in only


def test_every_problem_is_listed_not_just_the_first() -> None:
    assert len(_repoqa("It is in `tests/test_math.py`.")) == 3
