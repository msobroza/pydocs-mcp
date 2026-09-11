"""get_symbol(depth="source") on a CLASS or MODULE rebuilds the whole span (spec §2).

A chunk holds only a node's DIRECT text — a class chunk stops before its first
method, a module chunk is the dedented docstring — while ``items[0]`` reports the
whole node span. Rendering that chunk as the span's source is a lie the reader
cannot detect. The fix stitches the span back together from indexed node text:
each verbatim run gets its own ``python`` fence, and every line the index does
not hold becomes a marker OUTSIDE the fences.

AC2.1 (fenced lines are the file's lines at their true numbers, and fences plus
markers tile the span), AC2.2 (markers are body text, never ``truncated``),
AC2.3 (the cap bounds the span window), AC2.4 (what never contributes), AC2.5
(every other target byte-identical), AC2.7 (no filesystem read).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from pathlib import Path

import pytest

from pydocs_mcp.application.symbol_source import SymbolSourceService
from pydocs_mcp.application.symbol_source_span import (
    SPAN_SOURCE_KINDS,
    SpanRun,
    indexed_lines_by_number,
    span_runs,
    window_end,
)
from pydocs_mcp.application.truncation import ledger_scope
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk
from tests._fakes import (
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)

# The file the fake tree describes. Line numbers below are 1-indexed into it,
# exactly as a real ast_python pass would record them.
_SOURCE = '''\
"""Module doc."""

import os


@deco
class Klass:
    """Docstring."""

    ATTR = 1

    def a(self) -> int:
        return 1

    # a comment
    def b(self) -> int:
        return 2
'''
_LINES = _SOURCE.splitlines()
_PATH = "pkg/mod.py"


def _file_line(number: int) -> str:
    """The 1-indexed line ``number`` of :data:`_SOURCE`."""
    return _LINES[number - 1]


def _slice(start: int, end: int) -> str:
    """What a chunker's ``_slice_lines`` stores for an inclusive line range."""
    return "\n".join(_LINES[start - 1 : end])


def _node(
    kind: NodeKind,
    qname: str,
    start: int,
    end: int,
    text: str,
    *,
    children: Iterable[DocumentNode] = (),
    path: str = _PATH,
) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname,
        kind=kind,
        source_path=path,
        start_line=start,
        end_line=end,
        text=text,
        content_hash="h",
        children=tuple(children),
    )


def _method(name: str, start: int, end: int) -> DocumentNode:
    return _node(NodeKind.METHOD, f"pkg.mod.Klass.{name}", start, end, _slice(start, end))


def _klass(*, children: Iterable[DocumentNode] | None = None) -> DocumentNode:
    kids = (_method("a", 12, 13), _method("b", 16, 17)) if children is None else tuple(children)
    return _node(NodeKind.CLASS, "pkg.mod.Klass", 7, 17, _slice(7, 11), children=kids)


def _module(*, klass: DocumentNode | None = None) -> DocumentNode:
    imports = _node(NodeKind.IMPORT_BLOCK, "pkg.mod.__imports__", 3, 3, _slice(3, 3))
    return _node(
        NodeKind.MODULE,
        "pkg.mod",
        1,
        17,
        "Module doc.",  # ast.get_docstring output — dedented, NOT a source slice
        children=(imports, klass if klass is not None else _klass()),
    )


def _chunk(node: DocumentNode) -> Chunk:
    """The flat chunk the FTS pass writes for ``node`` — direct text only."""
    return Chunk(
        text=node.text,
        metadata={
            "package": "pkg",
            "module": "pkg.mod",
            "qualified_name": node.qualified_name,
            "source_path": node.source_path,
            "start_line": node.start_line,
            "end_line": node.end_line,
        },
    )


def _service(root: DocumentNode, *, max_lines: int = 400) -> SymbolSourceService:
    """A service whose fake uow serves the whole ``pkg.mod`` tree and its chunks."""
    trees = InMemoryDocumentTreeStore()
    chunks = InMemoryChunkStore()
    asyncio.run(trees.save_many((root,), package="pkg"))
    asyncio.run(chunks.upsert(tuple(_chunk(n) for n in _walk(root))))
    return SymbolSourceService(
        uow_factory=make_fake_uow_factory(chunks=chunks, trees=trees),
        max_lines=max_lines,
    )


def _walk(node: DocumentNode) -> Iterable[DocumentNode]:
    yield node
    for child in node.children:
        yield from _walk(child)


_MARKER_RE = re.compile(r"^\[lines? (\d+)(?:-(\d+))? not in the index\]$")


def _fenced_lines_by_number(body: str, start: int) -> dict[int, str]:
    """Map every fenced line to the file line number the rendering claims for it.

    Fails the tiling invariant loudly: a marker must resume exactly where the
    previous run stopped, so fences plus markers cover the span with no overlap
    and no hole (AC2.1).
    """
    numbered: dict[int, str] = {}
    cursor, inside = start, False
    for line in body.splitlines():
        if line.startswith("```"):
            inside = line != "```"
            continue
        if inside:
            numbered[cursor] = line
            cursor += 1
            continue
        match = _MARKER_RE.match(line)
        if match is None:
            continue
        assert int(match.group(1)) == cursor, f"marker {line!r} does not resume at {cursor}"
        cursor = int(match.group(2) or match.group(1)) + 1
    return numbered


# ── AC2.1: the span is rebuilt, verbatim, at its true line numbers ─────────


def test_class_source_renders_each_indexed_run_in_its_own_fence() -> None:
    out = asyncio.run(_service(_module()).source_for("pkg.mod.Klass"))
    assert out.count("```python") == 3  # 7-10, 12-13 and 16-17
    assert "[lines 14-15 not in the index]" in out
    assert "def b(self) -> int:" in out  # the method chunk path never showed this


@pytest.mark.parametrize("target,start", [("pkg.mod.Klass", 7), ("pkg.mod", 1)])
def test_every_fenced_line_is_the_file_line_at_its_number(target: str, start: int) -> None:
    out = asyncio.run(_service(_module()).source_for(target))
    numbered = _fenced_lines_by_number(out, start)
    assert numbered  # the span is not empty
    assert all(text == _file_line(number) for number, text in numbered.items())


def test_fences_and_markers_tile_the_whole_span() -> None:
    out = asyncio.run(_service(_module()).source_for("pkg.mod.Klass"))
    covered = set(_fenced_lines_by_number(out, 7))
    # Line 11 is the blank line that closes the class header: ``_slice_lines``
    # joins with "\n", so a TRAILING blank line is not recoverable from the
    # stored text. Calling that a gap is the honest index-only answer.
    gaps = {11, 14, 15}
    assert covered | gaps == set(range(7, 18))
    assert not covered & gaps


# ── AC2.2: gaps are body text, never a truncation ─────────────────────────


def test_gap_note_states_the_count_and_the_span_to_read() -> None:
    out = asyncio.run(_service(_module()).source_for("pkg.mod.Klass"))
    assert f"[3 lines of this span are not in the index — read {_PATH} lines 7-17" in out


def test_a_single_uncovered_line_is_singular() -> None:
    klass = _klass(children=(_method("a", 12, 13), _method("b", 15, 17)))
    out = asyncio.run(_service(_module(klass=klass)).source_for("pkg.mod.Klass"))
    assert "[line 14 not in the index]" in out
    assert "[lines" not in out


def test_markers_sit_outside_every_fence() -> None:
    out = asyncio.run(_service(_module()).source_for("pkg.mod.Klass"))
    assert 14 not in _fenced_lines_by_number(out, 7)


def test_gaps_alone_record_no_ledger_entry() -> None:
    with ledger_scope() as ledger:
        asyncio.run(_service(_module()).source_for("pkg.mod.Klass"))
    assert ledger.entries == ()


# ── AC2.3: the cap bounds the span WINDOW, gap lines included ──────────────


def test_module_over_the_cap_renders_only_the_window() -> None:
    with ledger_scope() as ledger:
        out = asyncio.run(_service(_module(), max_lines=5).source_for("pkg.mod"))
    assert set(_fenced_lines_by_number(out, 1)) == {3}
    assert f"[… 12 more lines — read {_PATH} directly]" in out
    assert len(ledger.entries) == 1


def test_module_docstring_is_never_placed_as_code() -> None:
    out = asyncio.run(_service(_module()).source_for("pkg.mod"))
    assert "Module doc." not in "\n".join(_fenced_lines_by_number(out, 1).values())
    assert "[lines 1-2 not in the index]" in out


# ── AC2.4: what never contributes a line ──────────────────────────────────


@pytest.mark.parametrize("target,start", [("pkg.mod.Klass", 7), ("pkg.mod", 1)])
def test_code_example_children_contribute_nothing(target: str, start: int) -> None:
    """A stub span of 1-1 would otherwise inject docstring prose at file line 1."""
    example = _node(NodeKind.CODE_EXAMPLE, "pkg.mod.Klass.ex0", 1, 1, "print('hi')")
    klass = _klass(children=(example, _method("a", 12, 13), _method("b", 16, 17)))
    out = asyncio.run(_service(_module(klass=klass)).source_for(target))
    assert "print('hi')" not in out
    numbered = _fenced_lines_by_number(out, start)
    assert numbered and all(text == _file_line(number) for number, text in numbered.items())


def test_child_text_longer_than_its_span_is_skipped() -> None:
    fat = _node(NodeKind.METHOD, "pkg.mod.Klass.b", 16, 17, "one\ntwo\nthree\nfour")
    klass = _klass(children=(_method("a", 12, 13), fat))
    out = asyncio.run(_service(_module(klass=klass)).source_for("pkg.mod.Klass"))
    assert "three" not in out
    assert "[lines 14-17 not in the index]" in out


def test_a_non_python_path_stays_on_the_chunk_path() -> None:
    klass = _node(NodeKind.CLASS, "pkg.mod.Klass", 7, 17, "class Klass {}", path="pkg/mod.rs")
    root = _node(NodeKind.MODULE, "pkg.mod", 1, 17, "", children=(klass,), path="pkg/mod.rs")
    out = asyncio.run(_service(root).source_for("pkg.mod.Klass"))
    assert out == "# Source — `pkg.mod.Klass`  ·  pkg/mod.rs\n\n```python\nclass Klass {}\n```\n"


# ── AC2.5: every other target is byte-identical to the chunk path ──────────


def test_a_method_target_is_never_rebuilt_from_its_span() -> None:
    """Only a CLASS/MODULE chunk is a fragment; a def chunk already IS its span."""
    partial = _node(NodeKind.METHOD, "pkg.mod.Klass.b", 16, 17, _file_line(16))
    klass = _klass(children=(_method("a", 12, 13), partial))
    out = asyncio.run(_service(_module(klass=klass)).source_for("pkg.mod.Klass.b"))
    assert out == f"# Source — `pkg.mod.Klass.b`  ·  {_PATH}\n\n```python\n{_file_line(16)}\n```\n"


def test_method_target_output_is_byte_identical() -> None:
    out = asyncio.run(_service(_module()).source_for("pkg.mod.Klass.a"))
    body = _slice(12, 13)
    assert out == f"# Source — `pkg.mod.Klass.a`  ·  {_PATH}\n\n```python\n{body}\n```\n"


def test_markdown_heading_target_output_is_byte_identical() -> None:
    heading = _node(
        NodeKind.MARKDOWN_HEADING, "README#intro", 1, 3, "# Intro\n\nProse.", path="README.md"
    )
    root = _node(NodeKind.MODULE, "README", 1, 3, "", children=(heading,), path="README.md")
    out = asyncio.run(_service(root).source_for("README#intro"))
    assert out == "# Source — `README#intro`  ·  README.md\n\n```python\n# Intro\n\nProse.\n```\n"


# ── AC2.7: the service never touches the filesystem ────────────────────────


def test_span_rendering_reads_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def _explode(*args: object, **kwargs: object) -> object:
        raise AssertionError("get_symbol(depth='source') must not read the filesystem")

    for reader in ("read_text", "read_bytes", "open"):
        monkeypatch.setattr(Path, reader, _explode)
    out = asyncio.run(_service(_module()).source_for("pkg.mod.Klass"))
    assert "```python" in out


# ── the span helpers on their own ──────────────────────────────────────────


def test_span_source_kinds_are_exactly_class_and_module() -> None:
    assert frozenset({NodeKind.CLASS, NodeKind.MODULE}) == SPAN_SOURCE_KINDS


def test_indexed_lines_are_clamped_to_the_node_span() -> None:
    """A stale or malformed child cannot paint lines the target does not own."""
    stray = _node(NodeKind.METHOD, "pkg.mod.Klass.stray", 1, 3, _slice(1, 3))
    indexed = indexed_lines_by_number(_klass(children=(_method("a", 12, 13), stray)))
    assert min(indexed) >= 7 and max(indexed) <= 17
    assert indexed[12] == _file_line(12)


def test_class_own_text_never_reaches_its_first_child() -> None:
    """A class whose own text overruns its first method must still leave the gap."""
    greedy = _node(
        NodeKind.CLASS, "pkg.mod.Klass", 7, 17, _slice(7, 17), children=_klass().children
    )
    indexed = indexed_lines_by_number(greedy)
    assert indexed[11] == _file_line(11)
    assert 14 not in indexed  # the comment line is the methods' business, not the class text


@pytest.mark.parametrize(
    "indexed,end,expected",
    [
        ({1: "a", 2: "b"}, 2, [SpanRun(True, 1, 2)]),
        (
            {2: "b"},
            3,
            [SpanRun(False, 1, 1), SpanRun(True, 2, 2), SpanRun(False, 3, 3)],
        ),
        ({}, 3, [SpanRun(False, 1, 3)]),
    ],
)
def test_span_runs_are_maximal_and_alternate(
    indexed: dict[int, str], end: int, expected: list[SpanRun]
) -> None:
    assert span_runs(indexed, 1, end) == expected
    assert [run.length for run in span_runs(indexed, 1, end)] == [run.length for run in expected]


@pytest.mark.parametrize("start,end,cap,expected", [(1, 17, 5, 5), (1, 3, 5, 3), (7, 17, 400, 17)])
def test_window_end_is_the_cap_or_the_span_end(
    start: int, end: int, cap: int, expected: int
) -> None:
    assert window_end(start, end, cap) == expected
