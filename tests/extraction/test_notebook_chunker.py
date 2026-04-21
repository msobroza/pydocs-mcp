"""Unit tests for :class:`NotebookChunker` (Task 16 — sub-PR #5, spec §8.3).

Covers:
- Empty cells list → MODULE with no children + ``cell_count == 0``.
- Markdown cell → NOTEBOOK_MARKDOWN_CELL child with title = first line ≤80 chars.
- Code cell → NOTEBOOK_CODE_CELL child with title ``"cell {index}"``.
- ``include_outputs=False`` (default) → outputs excluded from ``text``.
- ``include_outputs=True`` → outputs appended under ``# Output:`` separator.
- Invalid JSON → graceful fallback to a single MODULE carrying full content.
- ``from_config`` propagates ``notebook.include_outputs``.
- Decorator registration under ``.ipynb``.
- ``source`` field accepts list-of-strings (Jupyter canonical form) and joins.
- ``qualified_name`` format: ``module#cell-{index}``.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydocs_mcp.extraction.chunkers import NotebookChunker
from pydocs_mcp.extraction.config import ChunkingConfig, NotebookConfig
from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind
from pydocs_mcp.extraction.serialization import chunker_registry


def _nb(*cells: dict) -> str:
    return json.dumps({"cells": list(cells)})


def _build(
    content: str, *, path: str = "notebooks/demo.ipynb",
    root: Path | None = None, include_outputs: bool = False,
) -> DocumentNode:
    root = root if root is not None else Path("/tmp/fake_nb_root")
    return NotebookChunker(include_outputs=include_outputs).build_tree(
        path=str(Path(root) / path),
        content=content,
        package="notebooks",
        root=Path(root),
    )


# -- 1. Empty cells list ------------------------------------------------------

def test_empty_notebook_yields_module_with_no_children(tmp_path: Path) -> None:
    root = _build(_nb(), root=tmp_path)
    assert root.kind == NodeKind.MODULE
    assert root.children == ()
    assert root.extra_metadata["cell_count"] == 0


# -- 2. Markdown cell title = first line (truncated to 80) --------------------

def test_markdown_cell_title_is_first_line_truncated(tmp_path: Path) -> None:
    long = "A" * 200
    root = _build(_nb({"cell_type": "markdown", "source": f"{long}\nmore"}),
                  root=tmp_path)
    cell = root.children[0]
    assert cell.kind == NodeKind.NOTEBOOK_MARKDOWN_CELL
    assert cell.title == "A" * 80
    assert len(cell.title) == 80


# -- 3. Code cell title = "cell {index}" --------------------------------------

def test_code_cell_title_is_indexed(tmp_path: Path) -> None:
    root = _build(
        _nb(
            {"cell_type": "markdown", "source": "# Intro"},
            {"cell_type": "code", "source": "x = 1"},
        ),
        root=tmp_path,
    )
    code = root.children[1]
    assert code.kind == NodeKind.NOTEBOOK_CODE_CELL
    assert code.title == "cell 1"


# -- 4. include_outputs=False excludes outputs --------------------------------

def test_include_outputs_false_excludes_output_text(tmp_path: Path) -> None:
    cell = {
        "cell_type": "code",
        "source": "x = 1\n",
        "outputs": [{"text": "stdout-token-42\n"}],
    }
    root = _build(_nb(cell), root=tmp_path, include_outputs=False)
    txt = root.children[0].text
    assert txt == "x = 1\n"
    assert "# Output:" not in txt
    assert "stdout-token-42" not in txt


# -- 5. include_outputs=True appends under "# Output:" ------------------------

def test_include_outputs_true_appends_outputs(tmp_path: Path) -> None:
    cell = {
        "cell_type": "code",
        "source": "print('hi')\n",
        "outputs": [{"text": "hi\n"}],
    }
    root = _build(_nb(cell), root=tmp_path, include_outputs=True)
    txt = root.children[0].text
    assert "print('hi')" in txt
    assert "# Output:" in txt
    assert "hi" in txt


# -- 6. Invalid JSON → fallback to full-content MODULE ------------------------

def test_invalid_json_falls_back_to_module_full_content(tmp_path: Path) -> None:
    src = "not actually JSON"
    root = _build(src, root=tmp_path)
    assert root.kind == NodeKind.MODULE
    assert root.children == ()
    assert root.text == src


# -- 7. from_config propagates include_outputs --------------------------------

def test_from_config_propagates_include_outputs() -> None:
    cfg = ChunkingConfig(notebook=NotebookConfig(include_outputs=True))
    inst = NotebookChunker.from_config(cfg)
    assert inst.include_outputs is True


# -- 8. Decorator registered under ".ipynb" -----------------------------------

def test_decorator_registered_under_ipynb() -> None:
    assert chunker_registry[".ipynb"] is NotebookChunker


# -- 9. Source list-of-strings joined correctly -------------------------------

def test_source_list_of_strings_joined(tmp_path: Path) -> None:
    cell = {"cell_type": "code", "source": ["x = 1\n", "y = 2\n"]}
    root = _build(_nb(cell), root=tmp_path)
    assert root.children[0].text == "x = 1\ny = 2\n"


# -- 10. qualified_name format: "module#cell-{index}" -------------------------

def test_cell_qualified_name_format(tmp_path: Path) -> None:
    root = _build(
        _nb(
            {"cell_type": "markdown", "source": "# Intro"},
            {"cell_type": "code", "source": "x = 1"},
        ),
        path="notebooks/demo.ipynb",
        root=tmp_path,
    )
    assert root.children[0].qualified_name == "notebooks.demo#cell-0"
    assert root.children[1].qualified_name == "notebooks.demo#cell-1"


# -- 11. cell_count + cell_index metadata ------------------------------------

def test_cell_metadata_recorded(tmp_path: Path) -> None:
    root = _build(
        _nb(
            {"cell_type": "markdown", "source": "# Intro"},
            {"cell_type": "code", "source": "x = 1"},
            {"cell_type": "code", "source": "y = 2"},
        ),
        root=tmp_path,
    )
    assert root.extra_metadata["cell_count"] == 3
    assert [c.extra_metadata["cell_index"] for c in root.children] == [0, 1, 2]
    assert [c.extra_metadata["cell_type"] for c in root.children] == [
        "markdown", "code", "code",
    ]


# -- 12. data/text-plain outputs picked up when include_outputs=True ----------

def test_include_outputs_picks_up_data_text_plain(tmp_path: Path) -> None:
    cell = {
        "cell_type": "code",
        "source": "2 + 2",
        "outputs": [{"data": {"text/plain": "4"}}],
    }
    root = _build(_nb(cell), root=tmp_path, include_outputs=True)
    txt = root.children[0].text
    assert "# Output:" in txt
    assert "4" in txt


# -- 13. Markdown cell empty source falls back to "cell {index}" title --------

def test_empty_markdown_source_falls_back_title(tmp_path: Path) -> None:
    root = _build(_nb({"cell_type": "markdown", "source": ""}), root=tmp_path)
    # With no first line we still want a stable title.
    assert root.children[0].title == "cell 0"
