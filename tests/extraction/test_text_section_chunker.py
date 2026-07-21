"""Unit tests for :class:`TextSectionChunker` (ADR 0021 T2).

Covers per-format golden trees + real 1-indexed span correctness against
on-disk fixture files, the fixed-line windows fallback, the JSON cap +
oversize summary collapse, and empty/corrupt tolerance (degrade, never raise).
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.config import ChunkingConfig, TextSectionConfig
from pydocs_mcp.extraction.model import DocumentNode, NodeKind, flatten_to_chunks
from pydocs_mcp.extraction.serialization import chunker_registry
from pydocs_mcp.extraction.strategies.chunkers import TextSectionChunker
from pydocs_mcp.models import ChunkOrigin


def _build(
    content: str,
    *,
    rel_path: str,
    root: Path,
    window_lines: int = 80,
    json_max_chunks: int = 50,
) -> DocumentNode:
    return TextSectionChunker(
        window_lines=window_lines,
        json_max_chunks=json_max_chunks,
    ).build_tree(
        path=str(root / rel_path),
        content=content,
        package="proj",
        root=root,
    )


def _write_and_build(
    tmp_path: Path,
    rel_path: str,
    content: str,
    **kwargs: int,
) -> DocumentNode:
    """Write ``content`` to a real fixture file, then chunk THAT file — so span
    assertions are checked against the on-disk line layout, not just a string."""
    fpath = tmp_path / rel_path
    fpath.write_text(content, encoding="utf-8")
    on_disk = fpath.read_text(encoding="utf-8")
    return _build(on_disk, rel_path=rel_path, root=tmp_path, **kwargs)


def _titles(node: DocumentNode) -> list[str]:
    return [c.title for c in node.children]


# -- registration -------------------------------------------------------------


def test_registered_for_every_text_config_extension() -> None:
    for ext in (".rst", ".txt", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".json"):
        assert chunker_registry[ext] is TextSectionChunker


# -- rst golden tree + spans --------------------------------------------------


def test_rst_underline_and_over_under_titles(tmp_path: Path) -> None:
    content = (
        "======\n"  # 1 overline
        "Top\n"  # 2 title (over+under)
        "======\n"  # 3 underline
        "\n"  # 4
        "intro paragraph\n"  # 5
        "\n"  # 6
        "Section A\n"  # 7 title (underline-only)
        "---------\n"  # 8 underline
        "\n"  # 9
        "body of a\n"  # 10
    )
    root = _write_and_build(tmp_path, "guide.rst", content)

    assert root.kind == NodeKind.MODULE
    assert _titles(root) == ["Top", "Section A"]
    top, sec = root.children
    assert top.kind == NodeKind.TEXT_SECTION
    # over+under block starts at the overline (line 1); runs to the line before
    # the next title's block (Section A block starts at line 7 → end 6).
    assert (top.start_line, top.end_line) == (1, 6)
    # underline-only block starts at the title text line (7) → to EOF (10).
    assert (sec.start_line, sec.end_line) == (7, 10)
    assert "body of a" in sec.text


def test_rst_adornment_lines_not_reparsed_as_titles(tmp_path: Path) -> None:
    """The underline of an over+under title must not also match as an
    underline-only title on the next scan step."""
    content = "====\nName\n====\ntext\n"
    root = _write_and_build(tmp_path, "a.rst", content)
    assert _titles(root) == ["Name"]


# -- rst / txt windows fallback ----------------------------------------------


def test_txt_without_titles_falls_back_to_windows(tmp_path: Path) -> None:
    content = "\n".join(f"line {i}" for i in range(1, 11)) + "\n"
    root = _write_and_build(tmp_path, "notes.txt", content, window_lines=4)

    # 10 lines / window 4 → windows [1-4], [5-8], [9-10].
    assert _titles(root) == ["lines 1-4", "lines 5-8", "lines 9-10"]
    spans = [(c.start_line, c.end_line) for c in root.children]
    assert spans == [(1, 4), (5, 8), (9, 10)]
    # MODULE direct text is empty so the body isn't double-counted with windows.
    assert root.text == ""
    assert all(c.kind == NodeKind.TEXT_SECTION for c in root.children)


# -- toml / ini bracket sections ---------------------------------------------


def test_toml_top_level_tables_and_preamble(tmp_path: Path) -> None:
    content = (
        "title = 'root'\n"  # 1 preamble
        "\n"  # 2
        "[tool.black]\n"  # 3
        "line-length = 88\n"  # 4
        "\n"  # 5
        "[[tool.mypy.overrides]]\n"  # 6 array-of-tables
        "module = 'x'\n"  # 7
    )
    root = _write_and_build(tmp_path, "pyproject.toml", content)

    assert _titles(root) == ["tool.black", "tool.mypy.overrides"]
    assert "title = 'root'" in root.text  # preamble on MODULE
    black, mypy = root.children
    assert (black.start_line, black.end_line) == (3, 5)
    assert (mypy.start_line, mypy.end_line) == (6, 7)


def test_ini_sections(tmp_path: Path) -> None:
    content = "[section_one]\nkey = 1\n[section_two]\nkey = 2\n"
    root = _write_and_build(tmp_path, "setup.cfg", content)
    assert _titles(root) == ["section_one", "section_two"]


# -- yaml top-level keys ------------------------------------------------------


def test_yaml_top_level_keys_only(tmp_path: Path) -> None:
    content = (
        "name: demo\n"  # 1 top-level
        "deps:\n"  # 2 top-level
        "  - a\n"  # 3 nested (list item, not a key)
        "  nested: v\n"  # 4 nested key — must NOT become a section
        "version: 1\n"  # 5 top-level
    )
    root = _write_and_build(tmp_path, "conf.yaml", content)

    assert _titles(root) == ["name", "deps", "version"]
    deps = root.children[1]
    # deps section spans its key line (2) to the line before ``version`` (5-1).
    assert (deps.start_line, deps.end_line) == (2, 4)
    assert "nested: v" in deps.text


# -- json cap + oversize ------------------------------------------------------


def test_json_top_level_keys_within_cap(tmp_path: Path) -> None:
    content = '{\n  "alpha": 1,\n  "beta": {\n    "inner": 2\n  }\n}\n'
    root = _write_and_build(tmp_path, "data.json", content)
    # Only the minimum-indent keys are top-level; ``inner`` is nested.
    assert _titles(root) == ["alpha", "beta"]
    alpha = root.children[0]
    assert alpha.start_line == 2


def test_json_over_cap_collapses_to_one_summary_node(tmp_path: Path) -> None:
    lines = ["{"] + [f'  "k{i}": {i},' for i in range(10)] + ["}"]
    root = _write_and_build(tmp_path, "big.json", "\n".join(lines), json_max_chunks=3)
    assert root.children == ()
    assert root.kind == NodeKind.MODULE
    assert root.extra_metadata.get("truncated") is True


def test_json_minified_blob_collapses_when_oversize(tmp_path: Path) -> None:
    # One long line, no line-anchored keys → oversize summary, truncated preview.
    content = '{"x":"' + "z" * 5000 + '"}'
    root = _write_and_build(tmp_path, "min.json", content)
    assert root.children == ()
    assert root.extra_metadata.get("truncated") is True
    assert len(root.text) <= 2000


def test_small_unkeyed_json_keeps_full_content(tmp_path: Path) -> None:
    content = "[1, 2, 3]\n"
    root = _write_and_build(tmp_path, "arr.json", content)
    assert root.children == ()
    assert root.extra_metadata.get("truncated") is None
    assert "1, 2, 3" in root.text


# -- flatten: origin + chunk emission ----------------------------------------


def test_sections_flatten_to_text_section_origin_chunks(tmp_path: Path) -> None:
    content = "[a]\nx = 1\n[b]\ny = 2\n"
    root = _write_and_build(tmp_path, "c.ini", content)
    chunks = flatten_to_chunks(root, "proj")
    origins = {c.metadata.get("origin") for c in chunks if c.metadata.get("kind") == "text_section"}
    assert origins == {ChunkOrigin.TEXT_SECTION.value}
    # Every section is searchable (TEXT_SECTION is not structural-only).
    assert len(chunks) >= 2


# -- empty / corrupt tolerance ------------------------------------------------


def test_empty_file_degrades_to_single_module(tmp_path: Path) -> None:
    root = _build("", rel_path="empty.rst", root=tmp_path)
    assert root.kind == NodeKind.MODULE
    assert root.children == ()


def test_corrupt_json_does_not_raise(tmp_path: Path) -> None:
    # Unbalanced braces / trailing comma — byte-driven parse must not crash.
    content = '{\n  "ok": 1,\n  "broken": \n'
    root = _write_and_build(tmp_path, "bad.json", content)
    assert root.kind == NodeKind.MODULE
    assert isinstance(root, DocumentNode)


# -- from_config --------------------------------------------------------------


def test_from_config_propagates_text_section_tunables() -> None:
    cfg = ChunkingConfig(text_section=TextSectionConfig(window_lines=5, json_max_chunks=7))
    chunker = TextSectionChunker.from_config(cfg)
    assert chunker.window_lines == 5
    assert chunker.json_max_chunks == 7
