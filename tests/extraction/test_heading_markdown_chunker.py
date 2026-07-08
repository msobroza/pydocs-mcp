"""Unit tests for :class:`HeadingMarkdownChunker` (sub-PR #5, spec §8.2).

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

from pydocs_mcp.extraction.strategies.chunkers import HeadingMarkdownChunker
from pydocs_mcp.extraction.config import ChunkingConfig, MarkdownConfig
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.serialization import chunker_registry


def _build(
    content: str,
    *,
    path: str = "docs/guide.md",
    root: Path | None = None,
    min_level: int = 1,
    max_level: int = 3,
) -> DocumentNode:
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
    src = "# Section\nPre-code prose.\n```python\nx = 1\n```\nPost-code prose.\n"
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
    cfg = ChunkingConfig(
        markdown=MarkdownConfig(
            min_heading_level=2,
            max_heading_level=4,
        )
    )
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
    # F20: doc-file qualified_names keep the extension as a trailing
    # dotted segment so .md / .ipynb / .py with the same stem don't
    # collide on the DocumentTreeStore (package, module) PK.
    assert heading.qualified_name == "docs.guide.md#my-title"


# -- 13. Heading level recorded in extra_metadata -----------------------------


def test_heading_level_recorded(tmp_path: Path) -> None:
    src = "## Level 2\nBody.\n### Level 3\nBody3.\n"
    root = _build(src, root=tmp_path)
    h2 = next(c for c in root.children if c.title == "Level 2")
    h3 = next(c for c in root.children if c.title == "Level 3")
    assert h2.extra_metadata["level"] == 2
    assert h3.extra_metadata["level"] == 3


# -- 14. Fenced-block masking (F16) -------------------------------------------


def test_python_style_comments_inside_fenced_block_not_treated_as_headings(
    tmp_path: Path,
) -> None:
    """F16: a ``#``-prefixed comment line INSIDE a ```python fenced block
    must NOT be parsed as a Markdown heading. Pre-fix, the regex matched
    every line that started with ``#``, polluting the tree with phantom
    headings drawn from code comments.
    """
    src = (
        "# Real Heading\n"
        "Intro.\n"
        "\n"
        "```python\n"
        "# This is a Python comment, not a heading\n"
        "# Another comment\n"
        "def foo():\n"
        "    pass\n"
        "```\n"
        "\n"
        "## Another Real Heading\n"
        "Tail.\n"
    )
    root = _build(src, root=tmp_path)
    headings = [c for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING]
    titles = [h.title for h in headings]
    assert titles == ["Real Heading", "Another Real Heading"], (
        f"phantom heading detected — expected only real headings, got {titles}"
    )


def test_shell_style_comments_in_bash_fence_not_treated_as_headings(
    tmp_path: Path,
) -> None:
    """Same as Python — covers ``bash`` / ``sh`` code blocks where ``#``
    is a comment. Ensures the fix isn't language-specific."""
    src = (
        "# Setup\nRun these:\n\n```bash\n# install deps\npip install x\n# run tests\npytest\n```\n"
    )
    root = _build(src, root=tmp_path)
    titles = [c.title for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING]
    assert titles == ["Setup"]


# -- 15. PK collision avoidance with .py / .ipynb siblings (F20) --------------


def test_doc_path_module_id_keeps_extension_to_avoid_pk_collision(
    tmp_path: Path,
) -> None:
    """F20: ``pkg/foo.py``, ``pkg/foo.md``, ``pkg/foo.ipynb`` all yield
    a MODULE root that's eventually written to ``document_trees`` keyed
    by ``(package, qualified_name)``. Pre-fix, all three produced
    ``pkg.foo`` and the PK collided — only the last writer's tree
    survived. Doc-file module ids now carry their extension."""
    md_root = _build("# H\n", path="pkg/foo.md", root=tmp_path)
    assert md_root.qualified_name == "pkg.foo.md"


# -- 16. A4: CommonMark-faithful fence scanner (broader F16) ------------------


def test_four_backtick_fence_masks_inner_hashes(tmp_path: Path) -> None:
    """A4: CommonMark §4.5 requires 4+ backticks when body contains
    triple-backticks. The old 3-backtick-only regex missed this case
    and the # comment inside leaked as a phantom heading."""
    src = (
        "# Real\n"
        "Intro.\n"
        "\n"
        "````markdown\n"
        "# This is markdown-inside-markdown, not a heading\n"
        "```python\n"
        "x = 1\n"
        "```\n"
        "````\n"
    )
    root = _build(src, root=tmp_path)
    titles = [c.title for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING]
    assert titles == ["Real"]


def test_tilde_fence_masks_inner_hashes(tmp_path: Path) -> None:
    """A4: CommonMark §4.5 also accepts tilde fences. ``~~~python`` with
    a ``# comment`` inside used to leak through F16's backtick-only
    regex."""
    src = "# Real\nIntro.\n\n~~~python\n# python comment\nx = 1\n~~~\n"
    root = _build(src, root=tmp_path)
    titles = [c.title for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING]
    assert titles == ["Real"]


def test_hyphenated_lang_tag_fence_masks_inner_hashes(tmp_path: Path) -> None:
    """A4: ``\\w*`` rejected hyphens / plus signs. ``\\`\\`\\`c++`` and
    ``\\`\\`\\`text/plain`` blocks now match. Pin a c++ example."""
    src = "# Real\nCode:\n\n```c++\n// #define is not a heading either\n#include <foo>\n```\n"
    root = _build(src, root=tmp_path)
    titles = [c.title for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING]
    assert titles == ["Real"]


def test_fence_kind_mismatch_does_not_close(tmp_path: Path) -> None:
    """A4: a tilde opener is NOT closed by a backtick row. The
    backreference enforces same-kind. This documents the contract so
    a future regex 'simplification' that drops the backreference
    breaks the build."""
    # ``~~~`` opens; the body contains ``\`\`\`\`` which is NOT a tilde
    # closer. The match should extend to the next ``~~~`` line. With
    # the strict backreference, any heading-line between the backticks
    # stays masked (still inside the tilde block).
    src = "# Real\n\n~~~\n```\n# not a heading — still inside tilde fence\n```\n~~~\n"
    root = _build(src, root=tmp_path)
    titles = [c.title for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING]
    assert titles == ["Real"]


# -- T2: F20 end-to-end PK-collision pinning ----------------------------------


def test_doc_path_module_ids_pairwise_distinct_for_same_stem(
    tmp_path: Path,
) -> None:
    """T2: F20's existing test asserts qualified_name == 'pkg.foo.md'
    but never actually runs all three chunkers (.py / .md / .ipynb)
    on a shared stem to confirm distinctness. A revert that re-
    introduces the bug at the persistence layer (e.g. sanitising the
    suffix away) would pass the existing test. This pins the actual
    invariant: same stem → three distinct module identities."""
    from pydocs_mcp.extraction.strategies.chunkers import (
        AstPythonChunker,
        NotebookChunker,
    )
    import json

    py_root = AstPythonChunker().build_tree(
        path=str(tmp_path / "pkg" / "foo.py"),
        content="def x(): pass\n",
        package="pkg",
        root=tmp_path,
    )
    md_root = _build("# Heading\nbody\n", path="pkg/foo.md", root=tmp_path)
    notebook = json.dumps(
        {
            "cells": [{"cell_type": "markdown", "source": "# Title"}],
            "metadata": {"kernelspec": {"name": "python3"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )
    ipynb_root = NotebookChunker().build_tree(
        path=str(tmp_path / "pkg" / "foo.ipynb"),
        content=notebook,
        package="pkg",
        root=tmp_path,
    )
    ids = {py_root.qualified_name, md_root.qualified_name, ipynb_root.qualified_name}
    assert ids == {"pkg.foo", "pkg.foo.md", "pkg.foo.ipynb"}, (
        f"F20 PK-collision regression — same-stem siblings produced non-distinct module ids: {ids}"
    )


# -- 17. Duplicate / non-ASCII heading titles must not collide on node_id -----


def test_duplicate_heading_titles_produce_distinct_node_ids(tmp_path: Path) -> None:
    """CHANGELOG-shaped doc: '### Fixed' / '### Added' repeated per release.

    Pre-fix, ``_slugify`` alone drove the qname, so both 'Fixed' sections
    (and both 'Added' sections) collapsed onto the SAME node_id/qualified_name
    (``...#fixed`` / ``...#added``). Downstream, ``find_node_by_qualified_name``
    only ever returns the first match, silently hiding the second release's
    content, and identical-text duplicates additionally collide on
    content_hash and get merged/dropped by the diff-merge.
    """
    src = (
        "## v2.0.0\n"
        "### Fixed\n"
        "Fixed the frobnicator.\n"
        "### Added\n"
        "Added a widget.\n"
        "## v1.0.0\n"
        "### Fixed\n"
        "Fixed the wobbulator.\n"
        "### Added\n"
        "Added a gadget.\n"
    )
    root = _build(src, path="CHANGELOG.md", root=tmp_path)
    headings = [c for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING]
    node_ids = [h.node_id for h in headings]
    qnames = [h.qualified_name for h in headings]
    assert len(node_ids) == len(set(node_ids)), (
        f"duplicate heading titles collided on node_id: {node_ids}"
    )
    assert len(qnames) == len(set(qnames)), (
        f"duplicate heading titles collided on qualified_name: {qnames}"
    )


def test_non_ascii_headings_produce_distinct_node_ids(tmp_path: Path) -> None:
    """Two distinct CJK-only headings both slugify to '' → 'untitled'.

    ``_slugify`` strips every char outside ``[a-z0-9]``, so an all-CJK or
    all-Cyrillic heading (no ASCII letters/digits at all) collapses to the
    empty-slug fallback ``"untitled"`` regardless of the actual title text.
    Two such headings in one doc must still get pairwise-distinct node_ids.
    """
    src = "## 安装\nInstall steps.\n## Установка\nInstall steps in Russian.\n"
    root = _build(src, path="README.md", root=tmp_path)
    headings = [c for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING]
    assert [h.title for h in headings] == ["安装", "Установка"]
    node_ids = [h.node_id for h in headings]
    assert len(node_ids) == len(set(node_ids)), (
        f"non-ASCII headings collided on node_id: {node_ids}"
    )


def test_duplicate_heading_code_example_children_also_distinct(tmp_path: Path) -> None:
    """CODE_EXAMPLE children are keyed off the parent heading's qname
    (``{parent_qname}.__example_{i}__``); if two headings share a qname,
    their first code example also collides. Pins the child-level fallout
    of the same root cause."""
    src = "### Fixed\n```python\nx = 1\n```\n### Fixed\n```python\ny = 2\n```\n"
    root = _build(src, path="CHANGELOG.md", root=tmp_path)
    headings = [c for c in root.children if c.kind == NodeKind.MARKDOWN_HEADING]
    assert len(headings) == 2
    example_ids = [
        ex.node_id for h in headings for ex in h.children if ex.kind == NodeKind.CODE_EXAMPLE
    ]
    assert len(example_ids) == 2
    assert len(set(example_ids)) == 2, (
        f"CODE_EXAMPLE children of duplicate-titled headings collided: {example_ids}"
    )
