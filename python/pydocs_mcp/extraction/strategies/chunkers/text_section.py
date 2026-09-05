"""TextSectionChunker — one language-agnostic chunker for the T1 text/config
extension set (ADR 0021 T2).

Dispatch by extension family (all share the MODULE-root + TEXT_SECTION-child
shape and emit real 1-indexed line spans on every node):

- ``.rst`` / ``.txt`` — reStructuredText section titles (under-line, and
  over+under-line adornments). Files with no title fall back to fixed-line
  windows sized by ``ChunkingConfig.text_section.window_lines``.
- ``.toml`` / ``.cfg`` / ``.ini`` — bracket-declared tables/sections
  (``[table]``, ``[[array.table]]``) as section titles. Byte-driven: no
  ``tomllib`` semantics, just the section headers.
- ``.yaml`` / ``.yml`` — top-level mapping keys (column-0 ``key:``) as titles.
- ``.json`` — top-level keys, CAPPED per file (the fixture-flooding finding —
  ``.json`` is 17.9% of READ files but 2.2% of edits): more than
  ``json_max_chunks`` top-level keys, or an unkeyed/minified blob larger than
  the summary preview, collapses to ONE truncated summary node.

Empty or corrupt files never raise — they degrade to a single MODULE node,
matching ``ChunkingStage``'s per-file catch policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.config import (
    _DEFAULT_JSON_MAX_CHUNKS,
    _DEFAULT_TEXT_WINDOW_LINES,
    ChunkingConfig,
)
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.serialization import _register_chunker
from pydocs_mcp.extraction.strategies.chunkers._shared import (
    _content_hash,
    _dedup_slug,
    _docstring_summary,
    _module_from_doc_path,
    _relpath,
    _slice_lines,
    _slugify,
)

# Section marker = (human title, 1-indexed line where the section block starts).
_SectionMarker = tuple[str, int]

_RST_EXTS = frozenset({".rst", ".txt"})
_YAML_EXTS = frozenset({".yaml", ".yml"})
_JSON_EXTS = frozenset({".json"})

# reStructuredText title adornment punctuation (docutils' recommended set). An
# adornment line is a single one of these repeated; it under- (or over- and
# under-) lines the title text.
_RST_ADORNMENT: frozenset[str] = frozenset("=-`:.'\"~^_*+#<>")

# TOML/INI/CFG section header: ``[name]`` or TOML array-of-tables ``[[name]]``.
_BRACKET_RE = re.compile(r"^\s*\[\[?(?P<name>[^\[\]\n]+)\]\]?\s*$")

# YAML top-level mapping key — column 0 only (a leading-space key is nested, not
# top-level). Excludes ``---`` document markers, ``# comments`` and ``- items``.
_YAML_KEY_RE = re.compile(r"^(?P<key>[A-Za-z0-9_.\-]+)\s*:(?:\s|$)")

# JSON object key line. ``indent`` distinguishes top-level keys (minimum indent)
# from nested ones without a full parse — byte-driven and corrupt-tolerant.
_JSON_KEY_RE = re.compile(r'^(?P<indent>\s*)"(?P<key>(?:[^"\\]|\\.)*)"\s*:')

# Preview cap for the collapsed JSON summary node — bounds the embedded blob so
# an oversize fixture can't flood the index with one giant chunk.
_JSON_SUMMARY_MAX_CHARS = 2000


@_register_chunker(".toml")
@_register_chunker(".yaml")
@_register_chunker(".yml")
@_register_chunker(".cfg")
@_register_chunker(".ini")
@_register_chunker(".rst")
@_register_chunker(".txt")
@_register_chunker(".json")
@dataclass(frozen=True, slots=True)
class TextSectionChunker:
    window_lines: int = _DEFAULT_TEXT_WINDOW_LINES
    json_max_chunks: int = _DEFAULT_JSON_MAX_CHUNKS

    def build_tree(
        self,
        path: str,
        content: str,
        package: str,
        root: Path,
    ) -> DocumentNode:
        module = _module_from_doc_path(path, root)
        rel = _relpath(path, root)
        lines = content.splitlines()
        if not lines:
            return _module_node(module, rel, content, direct_text=content, children=())
        ext = Path(path).suffix.lower()
        return self._dispatch(ext, module, rel, content, lines)

    @classmethod
    def from_config(cls, cfg: ChunkingConfig) -> TextSectionChunker:
        return cls(
            window_lines=cfg.text_section.window_lines,
            json_max_chunks=cfg.text_section.json_max_chunks,
        )

    def _dispatch(
        self,
        ext: str,
        module: str,
        rel: str,
        content: str,
        lines: list[str],
    ) -> DocumentNode:
        if ext in _JSON_EXTS:
            return self._json_tree(module, rel, content, lines)
        if ext in _RST_EXTS:
            return self._text_tree(module, rel, content, lines)
        markers = _config_markers(ext, lines)
        return _tree_from_markers(markers, module, rel, content, lines)

    def _text_tree(
        self,
        module: str,
        rel: str,
        content: str,
        lines: list[str],
    ) -> DocumentNode:
        markers = _parse_rst_titles(lines)
        if markers:
            return _tree_from_markers(markers, module, rel, content, lines)
        # No RST titles — fall back to fixed-line windows so a flat .txt/.rst
        # file is still retrievable. Direct text is empty to avoid emitting the
        # whole body twice (once on MODULE, once across the windows).
        children = _window_nodes(lines, module, rel, self.window_lines)
        return _module_node(module, rel, content, direct_text="", children=children)

    def _json_tree(
        self,
        module: str,
        rel: str,
        content: str,
        lines: list[str],
    ) -> DocumentNode:
        markers = _json_markers(lines, content, self.json_max_chunks)
        if markers is None:
            return _summary_module_node(module, rel, content, lines)
        return _tree_from_markers(markers, module, rel, content, lines)


def _config_markers(ext: str, lines: list[str]) -> list[_SectionMarker]:
    if ext in _YAML_EXTS:
        return _parse_yaml_keys(lines)
    return _parse_bracket_sections(lines)


def _parse_bracket_sections(lines: list[str]) -> list[_SectionMarker]:
    """Top-level ``[table]`` / ``[[array.table]]`` headers as section markers."""
    markers: list[_SectionMarker] = []
    for idx, line in enumerate(lines):
        m = _BRACKET_RE.match(line)
        if m:
            markers.append((m.group("name").strip(), idx + 1))
    return markers


def _parse_yaml_keys(lines: list[str]) -> list[_SectionMarker]:
    """Column-0 ``key:`` lines as section markers (nested keys are indented)."""
    markers: list[_SectionMarker] = []
    for idx, line in enumerate(lines):
        m = _YAML_KEY_RE.match(line)
        if m:
            markers.append((m.group("key"), idx + 1))
    return markers


def _parse_rst_titles(lines: list[str]) -> list[_SectionMarker]:
    """Scan for reStructuredText section titles (under- / over+under-line)."""
    markers: list[_SectionMarker] = []
    i = 0
    while i < len(lines):
        consumed, title = _rst_title_at(lines, i)
        if title is not None:
            markers.append((title, i + 1))
        i += consumed
    return markers


def _rst_title_at(lines: list[str], i: int) -> tuple[int, str | None]:
    """Detect an RST title starting at line ``i``.

    Returns ``(lines_consumed, title)``. ``title`` is ``None`` when no title
    begins at ``i`` (consume 1 line and advance). A matched over+under title
    consumes 3 lines, an under-line-only title 2 — so the adornment lines are
    never re-scanned as spurious markers.
    """
    if _is_over_under_title(lines, i):
        return 3, lines[i + 1].strip()
    if _is_underline_title(lines, i):
        return 2, lines[i].strip()
    return 1, None


def _is_over_under_title(lines: list[str], i: int) -> bool:
    if i + 2 >= len(lines):
        return False
    title = lines[i + 1].strip()
    return bool(title) and _is_adornment(lines[i], title) and _is_adornment(lines[i + 2], title)


def _is_underline_title(lines: list[str], i: int) -> bool:
    if i + 1 >= len(lines):
        return False
    title = lines[i].strip()
    # ``lines[i]`` must be text, not itself an adornment (that case is the
    # over-line, handled above) — guard against a lone ``----`` matching.
    if not title or _is_bare_adornment(lines[i]):
        return False
    return _is_adornment(lines[i + 1], title)


def _is_bare_adornment(line: str) -> bool:
    s = line.strip()
    return len(s) >= 2 and set(s) <= _RST_ADORNMENT and len(set(s)) == 1


def _is_adornment(line: str, title: str) -> bool:
    """A single repeated adornment char at least as long as the title text."""
    s = line.strip()
    return _is_bare_adornment(line) and len(s) >= len(title)


def _json_markers(
    lines: list[str],
    content: str,
    cap: int,
) -> list[_SectionMarker] | None:
    """Top-level JSON key markers, or ``None`` to signal a summary collapse.

    Collapses (returns ``None``) when the file exceeds ``cap`` top-level keys
    (the fixture-flooding case) or has no detectable keys yet is larger than
    the summary preview (a minified blob). A small unkeyed file returns ``[]``
    so it becomes a plain MODULE carrying its full content.
    """
    keys = _top_level_json_keys(lines)
    if not keys:
        return None if len(content) > _JSON_SUMMARY_MAX_CHARS else []
    if len(keys) > cap:
        return None
    return keys


def _top_level_json_keys(lines: list[str]) -> list[_SectionMarker]:
    matches: list[tuple[int, str, int]] = []
    for idx, line in enumerate(lines):
        m = _JSON_KEY_RE.match(line)
        if m:
            matches.append((len(m.group("indent")), m.group("key"), idx + 1))
    if not matches:
        return []
    min_indent = min(indent for indent, _, _ in matches)
    return [(key, line_no) for indent, key, line_no in matches if indent == min_indent]


def _tree_from_markers(
    markers: list[_SectionMarker],
    module: str,
    rel: str,
    content: str,
    lines: list[str],
) -> DocumentNode:
    if not markers:
        return _module_node(module, rel, content, direct_text=content, children=())
    preamble = _slice_lines(lines, 1, markers[0][1] - 1)
    children = _section_nodes(markers, lines, module, rel)
    return _module_node(module, rel, content, direct_text=preamble, children=children)


def _section_nodes(
    markers: list[_SectionMarker],
    lines: list[str],
    module: str,
    rel: str,
) -> tuple[DocumentNode, ...]:
    nodes: list[DocumentNode] = []
    seen: dict[str, int] = {}
    for i, (title, start) in enumerate(markers):
        end = markers[i + 1][1] - 1 if i + 1 < len(markers) else len(lines)
        text = _slice_lines(lines, start, end)
        qname = f"{module}#{_dedup_slug(_slugify(title), seen)}"
        nodes.append(_text_section_node(qname, title, rel, start, end, text, module))
    return tuple(nodes)


def _window_nodes(
    lines: list[str],
    module: str,
    rel: str,
    window: int,
) -> tuple[DocumentNode, ...]:
    """Fixed-line windows over a title-less .rst/.txt file — each window is one
    TEXT_SECTION titled ``lines N-M`` with a real span."""
    nodes: list[DocumentNode] = []
    for start in range(1, len(lines) + 1, window):
        end = min(start + window - 1, len(lines))
        text = _slice_lines(lines, start, end)
        title = f"lines {start}-{end}"
        qname = f"{module}#L{start}-{end}"
        nodes.append(_text_section_node(qname, title, rel, start, end, text, module))
    return tuple(nodes)


def _text_section_node(
    qname: str,
    title: str,
    rel: str,
    start: int,
    end: int,
    text: str,
    module: str,
) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=title,
        kind=NodeKind.TEXT_SECTION,
        source_path=rel,
        start_line=start,
        end_line=end,
        text=text,
        content_hash=_content_hash(text, NodeKind.TEXT_SECTION, title),
        summary=_docstring_summary(text),
        extra_metadata={"module": module},
        parent_id=module,
    )


def _module_node(
    module: str,
    rel: str,
    full_content: str,
    *,
    direct_text: str,
    children: tuple[DocumentNode, ...],
) -> DocumentNode:
    end = max(len(full_content.splitlines()), 1)
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module,
        kind=NodeKind.MODULE,
        source_path=rel,
        start_line=1,
        end_line=end,
        text=direct_text,
        content_hash=_content_hash(direct_text, NodeKind.MODULE, module),
        extra_metadata={"module": module},
        children=children,
    )


def _summary_module_node(
    module: str,
    rel: str,
    content: str,
    lines: list[str],
) -> DocumentNode:
    """One truncated MODULE node for an oversize JSON file (fixture-flooding
    guard) — ``extra_metadata['truncated']`` flags the preview."""
    preview = content[:_JSON_SUMMARY_MAX_CHARS]
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module,
        kind=NodeKind.MODULE,
        source_path=rel,
        start_line=1,
        end_line=max(len(lines), 1),
        text=preview,
        content_hash=_content_hash(preview, NodeKind.MODULE, module),
        extra_metadata={"module": module, "truncated": True},
    )


__all__ = ("TextSectionChunker",)
