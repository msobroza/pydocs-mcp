"""HeadingMarkdownChunker — parses ``.md`` into MODULE + flat heading children.

Heading levels outside ``[min_heading_level, max_heading_level]`` are
skipped. Fenced triple-backtick blocks inside a heading's direct text
become CODE_EXAMPLE children of that heading; the code is removed from
the heading's ``text`` so search results don't double-count. If the
file contains no heading-in-range, the MODULE carries the full file
body as its direct text (no structural children).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.config import ChunkingConfig
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.serialization import _register_chunker
from pydocs_mcp.extraction.strategies.chunkers._shared import (
    _FENCED_RE,
    _code_example_node,
    _content_hash,
    _docstring_summary,
    _module_from_doc_path,
    _relpath,
    _unclosed_fence_start,
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@_register_chunker(".md")
@dataclass(frozen=True, slots=True)
class HeadingMarkdownChunker:
    min_heading_level: int = 1
    max_heading_level: int = 3

    def build_tree(
        self,
        path: str,
        content: str,
        package: str,
        root: Path,
    ) -> DocumentNode:
        module = _module_from_doc_path(path, root)
        rel = _relpath(path, root)
        headings = _parse_md_headings(
            content,
            self.min_heading_level,
            self.max_heading_level,
        )
        if not headings:
            return _md_module_node(module, rel, content, content, headings=())
        first_line = headings[0]["line"]
        preamble = "\n".join(content.splitlines()[: first_line - 1])
        tree_children = _build_heading_nodes(
            headings,
            content,
            module,
            rel,
            parent_id=module,
        )
        return _md_module_node(
            module,
            rel,
            content,
            preamble,
            headings=tree_children,
        )

    @classmethod
    def from_config(cls, cfg: ChunkingConfig) -> HeadingMarkdownChunker:
        return cls(
            min_heading_level=cfg.markdown.min_heading_level,
            max_heading_level=cfg.markdown.max_heading_level,
        )


def _parse_md_headings(
    content: str,
    min_level: int,
    max_level: int,
) -> list[dict]:
    """Scan ``content`` for ``#``-style headings within ``[min, max]``.

    Lines inside triple-backtick fenced code blocks are SKIPPED — a
    line like ``# this is a Python comment`` inside ```` ```python ````
    is code, not a heading. Pre-fix, the regex matched both and
    polluted the tree with phantom level-1 headings drawn from code
    comments.

    An unclosed trailing fence (author error / truncated file) is masked
    to end-of-document too, per CommonMark §4.5 — otherwise every
    ``#``-prefixed comment line inside it re-parses as a phantom heading
    (the same bug, resurrected for the unclosed case).
    """
    fenced_ranges = [(m.start(), m.end()) for m in _FENCED_RE.finditer(content)]
    unclosed_start = _unclosed_fence_start(content)
    if unclosed_start is not None:
        fenced_ranges.append((unclosed_start, len(content)))

    def _in_fence(pos: int) -> bool:
        return any(start <= pos < end for start, end in fenced_ranges)

    headings: list[dict] = []
    for m in _HEADING_RE.finditer(content):
        if _in_fence(m.start()):
            continue
        level = len(m.group(1))
        if level < min_level or level > max_level:
            continue
        line = content[: m.start()].count("\n") + 1
        headings.append(
            {
                "level": level,
                "title": m.group(2),
                "line": line,
            }
        )
    return headings


def _build_heading_nodes(
    headings: list[dict],
    content: str,
    module: str,
    rel: str,
    *,
    parent_id: str,
) -> tuple[DocumentNode, ...]:
    """Build flat MARKDOWN_HEADING nodes — one per in-range heading.

    Hierarchy is deliberately kept flat in this chunker (spec §8.2 —
    each heading is a direct child of MODULE). Fenced code blocks
    inside a heading's direct-text span are extracted as CODE_EXAMPLE
    children and removed from the heading's ``text``.
    """
    lines = content.splitlines()
    nodes: list[DocumentNode] = []
    slug_counts: dict[str, int] = {}
    for i, h in enumerate(headings):
        start_line = h["line"] + 1
        end_line = headings[i + 1]["line"] - 1 if i + 1 < len(headings) else len(lines)
        raw_text = "\n".join(lines[start_line - 1 : end_line]) if start_line <= end_line else ""
        qname = f"{module}#{_dedup_slug(_slugify(h['title']), slug_counts)}"
        cleaned, examples = _extract_md_fenced_examples(
            raw_text,
            qname,
            rel,
            start_line=start_line,
            end_line=end_line,
        )
        nodes.append(
            DocumentNode(
                node_id=qname,
                qualified_name=qname,
                title=h["title"],
                kind=NodeKind.MARKDOWN_HEADING,
                source_path=rel,
                start_line=h["line"],
                end_line=end_line,
                text=cleaned,
                content_hash=_content_hash(
                    cleaned,
                    NodeKind.MARKDOWN_HEADING,
                    h["title"],
                ),
                summary=_docstring_summary(cleaned),
                extra_metadata={"module": module, "level": h["level"]},
                parent_id=parent_id,
                children=tuple(examples),
            )
        )
    return tuple(nodes)


def _extract_md_fenced_examples(
    raw_text: str,
    parent_qname: str,
    rel: str,
    *,
    start_line: int,
    end_line: int,
) -> tuple[str, list[DocumentNode]]:
    """Strip fenced blocks from ``raw_text`` and return ``(cleaned, examples)``.

    Each fenced block becomes a CODE_EXAMPLE child keyed by
    ``f"{parent_qname}.__example_{i}__"`` with the fence tag captured in
    ``extra_metadata["language"]``.
    """
    cleaned_parts: list[str] = []
    examples: list[DocumentNode] = []
    last = 0
    for i, m in enumerate(_FENCED_RE.finditer(raw_text), start=1):
        cleaned_parts.append(raw_text[last : m.start()])
        examples.append(
            _code_example_node(
                m.group("body"),
                (m.group("lang") or "").strip(),
                i,
                parent_qname,
                rel,
                start_line=start_line,
                end_line=end_line,
            )
        )
        last = m.end()
    cleaned_parts.append(raw_text[last:])
    cleaned = "".join(cleaned_parts).strip()
    return cleaned, examples


def _md_module_node(
    module: str,
    rel: str,
    full_content: str,
    direct_text: str,
    *,
    headings: tuple[DocumentNode, ...],
) -> DocumentNode:
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module,
        kind=NodeKind.MODULE,
        source_path=rel,
        start_line=1,
        end_line=max(len(full_content.splitlines()), 1),
        text=direct_text,
        content_hash=_content_hash(direct_text, NodeKind.MODULE, module),
        extra_metadata={"module": module},
        children=headings,
    )


def _slugify(text: str) -> str:
    """Lowercase + collapse non-alphanumerics to single hyphens. Empty
    slug falls back to ``"untitled"`` so every heading has a stable id."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "untitled"


def _dedup_slug(slug: str, seen: dict[str, int]) -> str:
    """Disambiguate a repeated slug with a ``-N`` suffix (mirrors the
    ``__imports__N`` scheme in ``ast_python.py``).

    Two headings that slugify identically — repeated titles ('### Fixed'
    in every CHANGELOG release section) or non-ASCII titles that both
    collapse to the empty-slug ``"untitled"`` fallback — would otherwise
    share one ``node_id``/``qualified_name``. ``find_node_by_qualified_name``
    only ever returns the first match, so the collision silently hides
    every subsequent same-slug section. ``seen`` is mutated in place; it
    is a fresh local dict per ``build_tree`` call (single-threaded, one
    dict per document), never shared across parallel branches.
    """
    count = seen.get(slug, 0)
    seen[slug] = count + 1
    return slug if count == 0 else f"{slug}-{count + 1}"


__all__ = ("HeadingMarkdownChunker",)
