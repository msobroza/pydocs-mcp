"""The widened citation extractor: text and config files cite, the ``.py`` rule holds.

``extract_citations`` is the answer judge's extractor. It widens the pseudo-qrel
extractor (``datasets._citations.extract_path_citations``) past ``.py`` without
changing a single SWE-QA pseudo-qrel, and it keeps that extractor's rule that a
dotted module name is not a file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_eval.datasets._citations import extract_path_citations
from pydocs_eval.judge.config import DEFAULT_CITATION_EXTENSIONS
from pydocs_eval.judge.needle_citation import extract_citations

_FIXTURES = Path(__file__).parents[1] / "fixtures"


def _swe_qa_answers() -> list[str]:
    answers = []
    for name in ("swe_qa_mini.jsonl", "swe_qa_pro_mini.jsonl"):
        lines = (_FIXTURES / name).read_text(encoding="utf-8").splitlines()
        answers += [json.loads(line)["answer"] for line in lines if line.strip()]
    return answers


def test_text_and_config_files_are_cited() -> None:
    answer = (
        "See `docs/retrievers.md:79,105`, the table in CONTRIBUTING.md#adding-a-retriever, "
        "`config.yaml`, ci.yml, pyproject.toml, setup.cfg, tox.ini, docs/index.rst, "
        "requirements.txt and package.json."
    )

    assert extract_citations(answer, extensions=DEFAULT_CITATION_EXTENSIONS) == (
        "docs/retrievers.md",
        "CONTRIBUTING.md",
        "config.yaml",
        "ci.yml",
        "pyproject.toml",
        "setup.cfg",
        "tox.ini",
        "docs/index.rst",
        "requirements.txt",
        "package.json",
    )


@pytest.mark.parametrize(
    "prose",
    [
        "Use matplotlib.pyplot to draw the figure.",
        "The hot loop lives in _speedups.pyx now.",
        "Delete the stale mod.pyc and retry.",
        "The markdown renderer is mdx.markdown_it here.",
        "numpy.json_normalize is not a file.",
    ],
)
def test_a_dotted_name_or_longer_suffix_is_not_a_citation(prose: str) -> None:
    assert extract_citations(prose, extensions=DEFAULT_CITATION_EXTENSIONS) == ()


def test_a_path_cited_twice_is_listed_once_in_first_order() -> None:
    answer = "`src/a.py:1-2` then README.md, then src/a.py again and `README.md#usage`."

    assert extract_citations(answer, extensions=DEFAULT_CITATION_EXTENSIONS) == (
        "src/a.py",
        "README.md",
    )


def test_python_only_extraction_is_the_pseudo_qrel_extractor() -> None:
    """Parity: restricted to ``.py`` it cites exactly what the pseudo-qrels cite."""
    for answer in _swe_qa_answers():
        pseudo_qrel = tuple(dict.fromkeys(path for path, _, _ in extract_path_citations(answer)))

        assert extract_citations(answer, extensions=(".py",)) == pseudo_qrel, answer


def test_widening_changes_no_python_citation() -> None:
    for answer in _swe_qa_answers():
        widened = extract_citations(answer, extensions=DEFAULT_CITATION_EXTENSIONS)
        python_only = extract_citations(answer, extensions=(".py",))

        assert tuple(path for path in widened if path.endswith(".py")) == python_only
