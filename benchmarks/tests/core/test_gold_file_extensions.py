"""The gold file types have one home, and it matches the product's text/config set."""

from __future__ import annotations

from pydocs_eval.datasets.example_needle_chat import CORPUS_GLOBS
from pydocs_eval.gold_extensions import GOLD_FILE_EXTENSIONS
from pydocs_eval.judge.config import DEFAULT_CITATION_EXTENSIONS, JevConfig, load_judge_config


def test_the_gold_file_types_are_python_markdown_and_the_products_text_config_set() -> None:
    # Asserted where the product IS installed: the eval package itself keeps a
    # zero-pydocs_mcp import floor, so it mirrors the set rather than importing it.
    from pydocs_mcp.extraction.config import _TEXT_CONFIG_EXTENSIONS

    assert set(GOLD_FILE_EXTENSIONS) == {".py", ".md", *_TEXT_CONFIG_EXTENSIONS}
    assert len(GOLD_FILE_EXTENSIONS) == len(set(GOLD_FILE_EXTENSIONS))


def test_the_chat_corpus_reads_the_gold_file_types_in_a_fixed_order() -> None:
    assert CORPUS_GLOBS == (
        "*.py",
        "*.md",
        "*.toml",
        "*.yaml",
        "*.yml",
        "*.cfg",
        "*.ini",
        "*.txt",
        "*.json",
        "*.rst",
    )


def test_the_judge_cites_every_gold_file_type() -> None:
    assert DEFAULT_CITATION_EXTENSIONS is GOLD_FILE_EXTENSIONS
    assert JevConfig().citation_extensions == GOLD_FILE_EXTENSIONS


def test_the_shipped_judge_yaml_restates_the_gold_file_types() -> None:
    """``judge.yaml`` is what a run loads, so its hand-written copy may not drift."""
    assert load_judge_config().jev.citation_extensions == GOLD_FILE_EXTENSIONS
