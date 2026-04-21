"""Unit tests for :class:`HeadingMarkdownChunker` (Task 15 — sub-PR #5, spec §8.2).

Covers:
- No heading-in-range → single MODULE node carrying full content as text.
- Single ``# Title`` → MODULE + one MARKDOWN_HEADING child.
- Multiple sibling headings → MODULE + flat list of MARKDOWN_HEADING children
  (deep hierarchy not modelled by this chunker — each heading node is flat).
- Min/max heading-level filtering honoured.
- Preamble text before first heading → MODULE ``text``.
- Fenced code blocks inside a heading's direct text → CODE_EXAMPLE children
  AND the block text is removed from the heading's ``text`` (no double-count).
- Fenced-block language tag captured under ``extra_metadata["language"]``.
- ``from_config`` honours ``markdown.{min,max}_heading_level``.
- Decorator registration under ``.md``.
- Empty content is handled gracefully.
- Only fenced blocks + no headings → MODULE carries full content.
"""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.chunkers import HeadingMarkdownChunker
from pydocs_mcp.extraction.config import ChunkingConfig, MarkdownConfig
from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind
from pydocs_mcp.extraction.serialization import chunker_registry


def _build(content: str, *, path: str = "docs/guide.md",
           root: Path | None = None,
           min_level: int = 1, max_level: int = 3) -> DocumentNode:
    root = root if root is not None else Path("/tmp/fake_md_root")
    return HeadingMarkdownChunker(
        min_heading_level=min_level,
        max_heading_level=max_level,
    ).build_tree(
        path=str(Path(root) / path),
        content=content,
        package="docs",
        root=Path(root),
    )


# -- 1. No headings in range --------------------------------------------------

def test_no_headings_yields_module_with_full_content(tmp_path: Path) -> None:
    src = "Just a paragraph.\nAnother line.\n"
    root = _build(src, root=tmp_path)
    assert root.kind == NodeKind.MODULE
    assert root.children == ()
    assert root.text == src


# -- 2. Single heading --------------------------------------------------------

def test_single_heading_produces_one_heading_child(tmp_path: Path) -> None:
    src = "# Title\n\nBody paragraph.\n"
    root = _build(src, root=tmp_path)
    assert root.kind == NodeKind.MODULE
    headings = [c for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING]
    assert len(headings) == 1
    assert headings[0].title == "Title"
    assert "Body paragraph." in headings[0].text


# -- 3. Siblings are flat -----------------------------------------------------

def test_multiple_siblings_are_flat_children_of_module(tmp_path: Path) -> None:
    src = "# A\nA body.\n## B\nB body.\n## C\nC body.\n"
    root = _build(src, root=tmp_path)
    headings = [c for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING]
    titles = [h.title for h in headings]
    assert titles == ["A", "B", "C"]


# -- 4. Heading above min_level excluded --------------------------------------

def test_heading_above_min_level_is_excluded(tmp_path: Path) -> None:
    src = "# Top\nIntro.\n## Sub\nBody.\n"
    root = _build(src, root=tmp_path, min_level=2, max_level=3)
    headings = [c for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING]
    assert [h.title for h in headings] == ["Sub"]


# -- 5. Heading below max_level excluded --------------------------------------

def test_heading_below_max_level_is_excluded(tmp_path: Path) -> None:
    src = "# Top\n###### Tiny\nBody.\n"
    root = _build(src, root=tmp_path, min_level=1, max_level=3)
    headings = [c for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING]
    assert [h.title for h in headings] == ["Top"]


# -- 6. Preamble before first heading → MODULE.text ---------------------------

def test_preamble_before_first_heading_becomes_module_text(tmp_path: Path) -> None:
    src = "intro line 1\nintro line 2\n# Heading\nbody\n"
    root = _build(src, root=tmp_path)
    assert "intro line 1" in root.text
    assert "intro line 2" in root.text
    # The heading itself is a child.
    assert any(c.title == "Heading" for c in root.children)


# -- 7. Fenced code block → CODE_EXAMPLE child, code removed from heading.text

def test_fenced_block_becomes_code_example_and_removed_from_heading_text(
    tmp_path: Path,
) -> None:
    src = (
        "# Section\n"
        "Pre-code prose.\n"
        "```python\n"
        "x = 1\n"
        "```\n"
        "Post-code prose.\n"
    )
    root = _build(src, root=tmp_path)
    heading = next(c for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING)
    examples = [c for c in heading.children if c.kind == NodeKind.CODE_EXAMPLE]
    assert len(examples) == 1
    assert examples[0].text == "x = 1"
    assert examples[0].extra_metadata["language"] == "python"
    # Code must not appear in the heading's direct text.
    assert "x = 1" not in heading.text
    assert "Pre-code prose." in heading.text
    assert "Post-code prose." in heading.text


# -- 8. from_config honours markdown.{min,max}_heading_level ------------------

def test_from_config_propagates_heading_bounds() -> None:
    cfg = ChunkingConfig(markdown=MarkdownConfig(
        min_heading_level=2, max_heading_level=4,
    ))
    inst = HeadingMarkdownChunker.from_config(cfg)
    assert inst.min_heading_level == 2
    assert inst.max_heading_level == 4


# -- 9. Decorator registered under ".md" --------------------------------------

def test_decorator_registered_under_md() -> None:
    assert chunker_registry[".md"] is HeadingMarkdownChunker


# -- 10. Empty content --------------------------------------------------------

def test_empty_content_yields_empty_module(tmp_path: Path) -> None:
    root = _build("", root=tmp_path)
    assert root.kind == NodeKind.MODULE
    assert root.children == ()
    assert root.text == ""


# -- 11. Fenced blocks but no headings → MODULE carries full content ----------

def test_only_fenced_blocks_no_headings_module_holds_full_content(
    tmp_path: Path,
) -> None:
    src = "```python\nprint('hi')\n```\nSome prose.\n"
    root = _build(src, root=tmp_path)
    assert root.kind == NodeKind.MODULE
    # Spec §8.2: with zero heading-in-range, MODULE text = full file body,
    # and no CODE_EXAMPLE extraction happens at MODULE level.
    assert root.text == src
    assert root.children == ()


# -- 12. qualified_name format: "module#slug" ---------------------------------

def test_heading_qualified_name_format(tmp_path: Path) -> None:
    src = "# My Title\nBody.\n"
    root = _build(src, path="docs/guide.md", root=tmp_path)
    heading = next(c for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING)
    # module id: "docs.guide"; slug: "my-title".
    assert heading.qualified_name == "docs.guide#my-title"


# -- 13. Heading level recorded in extra_metadata -----------------------------

def test_heading_level_recorded(tmp_path: Path) -> None:
    src = "## Level 2\nBody.\n### Level 3\nBody3.\n"
    root = _build(src, root=tmp_path)
    h2 = next(c for c in root.children if c.title == "Level 2")
    h3 = next(c for c in root.children if c.title == "Level 3")
    assert h2.extra_metadata["level"] == 2
    assert h3.extra_metadata["level"] == 3
