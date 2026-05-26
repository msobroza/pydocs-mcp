"""AC-11: Jinja2 prompt templates load + render with (query, trees)."""
from __future__ import annotations

from pydocs_mcp.retrieval.prompts._loader import render_prompt


def test_render_pydocs_v1_contains_query() -> None:
    out = render_prompt(
        "tree_reasoning_pydocs_v1",
        query="how does the diff-merge handle NULL hashes",
        trees=[{"title": "module_a", "node_id": "1", "summary": "stuff",
                "kind": "MODULE", "nodes": []}],
    )
    assert "how does the diff-merge handle NULL hashes" in out
    assert "module_a" in out
    assert '"node_id"' in out


def test_render_pageindex_v1_contains_query() -> None:
    out = render_prompt(
        "tree_reasoning_pageindex_v1",
        query="what is x",
        trees=[{"title": "t", "node_id": "1", "summary": "s", "nodes": []}],
    )
    assert "what is x" in out
    assert '"node_id"' in out


def test_render_unknown_template_raises() -> None:
    import pytest
    with pytest.raises(FileNotFoundError, match="not_a_template"):
        render_prompt("not_a_template", query="q", trees=[])
