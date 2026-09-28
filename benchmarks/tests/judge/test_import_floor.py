"""The judge package imports nothing from the product it measures.

The code-first check must score a run of any product version — including one
that predates the constants it reads — so the judge package, and everything it
imports, keeps a zero-``pydocs_mcp`` floor. ``sys.modules[name] = None`` makes
``import pydocs_mcp`` raise ``ImportError``, so the fresh import below fails if
anything on the judge's import path reaches the product.
"""

from __future__ import annotations

import importlib
import sys

import pytest

_FLOOR_PACKAGES = ("pydocs_mcp", "pydocs_eval.judge", "pydocs_eval.trajectory")


def _hide_the_product(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forget every floor module, then make ``pydocs_mcp`` unimportable (restored after)."""
    for name in list(sys.modules):
        if name.startswith(_FLOOR_PACKAGES):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "pydocs_mcp", None)


def test_the_judge_scores_an_answer_without_the_product(monkeypatch: pytest.MonkeyPatch) -> None:
    _hide_the_product(monkeypatch)

    needle_citation = importlib.import_module("pydocs_eval.judge.needle_citation")
    config = importlib.import_module("pydocs_eval.judge.config")

    citation = needle_citation.score_needle_citation(
        "It is `pkg.mod.run`.",
        [needle_citation.NeedleSite("src/pkg/mod.py", "run")],
        extensions=config.load_judge_config().jev.citation_extensions,
    )
    assert citation.needle_cited
    assert not any(name.startswith("pydocs_mcp.") for name in sys.modules)
