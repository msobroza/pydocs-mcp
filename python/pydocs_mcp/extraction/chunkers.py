"""Concrete :class:`~pydocs_mcp.extraction.protocols.Chunker` strategies.

Ships one chunker per supported file extension; Tasks 15 and 16 will append
``HeadingMarkdownChunker`` (``.md``) and ``NotebookChunker`` (``.ipynb``).

All chunkers:
- Are decorated with ``@_register_chunker(ext)`` so
  :data:`~pydocs_mcp.extraction.serialization.chunker_registry` is populated
  at import time (spec §7.5).
- Provide a ``from_config(cfg: ChunkingConfig) -> Self`` classmethod so the
  :class:`~pydocs_mcp.extraction.stages.ChunkingStage` can build instances
  uniformly (spec §8 preamble).

Direct-text rule (spec §4.1.1): each node's ``.text`` contains ONLY prose
between this node's start and its first child's start. MODULE carries the
module docstring; CLASS carries the span up to the first method's line;
FUNCTION / METHOD carry the full def span (since their code-example
children live in docstrings, not in the source line range).
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.config import ChunkingConfig
from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind
from pydocs_mcp.extraction.serialization import _register_chunker

log = logging.getLogger("pydocs-mcp")


# Triple-backtick fenced blocks inside docstrings. Captures language tag
# (group 1) and body (group 2). Non-greedy DOTALL so consecutive blocks
# don't merge.
_FENCED_RE = re.compile(r"```(\w*)\n(.*?)\n```", re.DOTALL)


@_register_chunker(".py")
@dataclass(frozen=True, slots=True)
class AstPythonChunker:
    """Parses a ``.py`` file into a MODULE tree.

    Structure: MODULE → (IMPORT_BLOCK? | FUNCTION* | CLASS*) with METHOD
    children under each CLASS. Docstrings on MODULE / FUNCTION / METHOD /
    CLASS may contribute CODE_EXAMPLE grandchildren via fenced-block
    extraction (spec §8.1).

    Parse failure: logs a warning and returns a single MODULE node whose
    ``text`` is the full source — the file still produces a searchable
    chunk, no crash.
    """

    def build_tree(
        self, path: str, content: str, package: str, root: Path,
    ) -> DocumentNode:
        module = _module_from_path(path, root)
        tree = _safe_parse(content, path)
        if tree is None:
            return _fallback_module_node(module, path, content, root)
        return _module_node_from_ast(tree, module, path, content, root)

    @classmethod
    def from_config(cls, cfg: ChunkingConfig) -> "AstPythonChunker":
        return cls()


# ── SRP helpers ───────────────────────────────────────────────────────────


def _safe_parse(content: str, path: str) -> ast.Module | None:
    """Parse source; return ``None`` and log on ``SyntaxError``."""
    try:
        return ast.parse(content)
    except SyntaxError as exc:
        log.warning("ast.parse failed on %s: %s", path, exc)
        return None


def _fallback_module_node(
    module: str, path: str, content: str, root: Path,
) -> DocumentNode:
    """Single-node MODULE tree for unparsable files — the file still gets
    one chunk keyed by its full source."""
    end = max(len(content.splitlines()), 1)
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module,
        kind=NodeKind.MODULE,
        source_path=_relpath(path, root),
        start_line=1,
        end_line=end,
        text=content,
        content_hash=_content_hash(content, NodeKind.MODULE, module),
    )


def _module_node_from_ast(
    tree: ast.Module, module: str, path: str, content: str, root: Path,
) -> DocumentNode:
    """Build the MODULE root + all children from a parsed ``ast.Module``."""
    lines = content.splitlines()
    rel = _relpath(path, root)
    children = _extract_module_children(tree, module, lines, rel)
    doc = ast.get_docstring(tree) or ""
    doc_examples = _extract_code_examples(doc, module, rel)
    all_children = tuple(children) + tuple(doc_examples)
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module,
        kind=NodeKind.MODULE,
        source_path=rel,
        start_line=1,
        end_line=max(len(lines), 1),
        text=doc,
        content_hash=_content_hash(doc, NodeKind.MODULE, module),
        summary=_docstring_summary(doc),
        extra_metadata={"module": module, "docstring": doc},
        children=all_children,
    )


def _extract_module_children(
    tree: ast.Module, module: str, lines: list[str], rel: str,
) -> list[DocumentNode]:
    """IMPORT_BLOCK (if any imports) + one FUNCTION / CLASS per top-level
    def / class. Preserves source order of functions and classes."""
    children: list[DocumentNode] = []
    imports = [s for s in tree.body if isinstance(s, (ast.Import, ast.ImportFrom))]
    if imports:
        children.append(_import_block_node(imports, module, lines, rel))
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            children.append(_function_node(
                stmt, module, lines, rel,
                parent_id=module, kind=NodeKind.FUNCTION,
            ))
        elif isinstance(stmt, ast.ClassDef):
            children.append(_class_node(stmt, module, lines, rel))
    return children


def _import_block_node(
    imports: list[ast.Import | ast.ImportFrom],
    module: str, lines: list[str], rel: str,
) -> DocumentNode:
    """Coalesce all top-level imports into one IMPORT_BLOCK."""
    start = imports[0].lineno
    end = imports[-1].end_lineno or start
    txt = _slice_lines(lines, start, end)
    qname = f"{module}.__imports__"
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title="imports",
        kind=NodeKind.IMPORT_BLOCK,
        source_path=rel,
        start_line=start,
        end_line=end,
        text=txt,
        content_hash=_content_hash(txt, NodeKind.IMPORT_BLOCK, "imports"),
        parent_id=module,
    )


def _function_node(
    stmt: ast.FunctionDef | ast.AsyncFunctionDef,
    module: str, lines: list[str], rel: str,
    *, parent_id: str, kind: NodeKind,
) -> DocumentNode:
    """Shared FUNCTION / METHOD builder. ``kind`` + ``parent_id`` make
    the only difference (methods qualify under the class, functions under
    the module)."""
    qname = f"{parent_id}.{stmt.name}"
    doc = ast.get_docstring(stmt) or ""
    start = stmt.lineno
    end = stmt.end_lineno or start
    txt = _slice_lines(lines, start, end)
    is_async = isinstance(stmt, ast.AsyncFunctionDef)
    title = f"{'async def' if is_async else 'def'} {stmt.name}()"
    sig_line = lines[start - 1].strip() if 0 <= start - 1 < len(lines) else ""
    examples = _extract_code_examples(doc, qname, rel)
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=title,
        kind=kind,
        source_path=rel,
        start_line=start,
        end_line=end,
        text=txt,
        content_hash=_content_hash(txt, kind, title),
        summary=_docstring_summary(doc),
        extra_metadata={"module": module, "docstring": doc, "signature": sig_line},
        parent_id=parent_id,
        children=tuple(examples),
    )


def _class_node(
    stmt: ast.ClassDef, module: str, lines: list[str], rel: str,
) -> DocumentNode:
    """CLASS node with METHOD children. Direct text = class line through
    the line before the first method (spec §4.1.1 direct-text rule)."""
    qname = f"{module}.{stmt.name}"
    doc = ast.get_docstring(stmt) or ""
    method_stmts = [
        s for s in stmt.body
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    start = stmt.lineno
    end = stmt.end_lineno or start
    direct_end = (method_stmts[0].lineno - 1) if method_stmts else end
    direct_txt = _slice_lines(lines, start, direct_end)
    method_nodes = [
        _function_node(m, module, lines, rel, parent_id=qname, kind=NodeKind.METHOD)
        for m in method_stmts
    ]
    doc_examples = _extract_code_examples(doc, qname, rel)
    inherits = tuple(ast.unparse(b) for b in stmt.bases)
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=f"class {stmt.name}",
        kind=NodeKind.CLASS,
        source_path=rel,
        start_line=start,
        end_line=end,
        text=direct_txt,
        content_hash=_content_hash(direct_txt, NodeKind.CLASS, stmt.name),
        summary=_docstring_summary(doc),
        extra_metadata={
            "module": module,
            "docstring": doc,
            "inherits_from": inherits,
        },
        parent_id=module,
        children=tuple(doc_examples) + tuple(method_nodes),
    )


def _extract_code_examples(
    docstring: str, parent_qname: str, rel: str,
) -> list[DocumentNode]:
    """Pull triple-backtick fenced blocks out of a docstring as
    CODE_EXAMPLE nodes. Fence line-offsets inside the docstring don't map
    cleanly back to source lines (the docstring's position in source is
    stmt.lineno+1, but the fence offset within the docstring is opaque),
    so start/end_line are stubbed to 1 — consumers key examples by
    ``qualified_name`` / ``node_id``."""
    if not docstring:
        return []
    examples: list[DocumentNode] = []
    for i, match in enumerate(_FENCED_RE.finditer(docstring), start=1):
        lang = match.group(1) or ""
        code = match.group(2)
        qname = f"{parent_qname}.__example_{i}__"
        examples.append(DocumentNode(
            node_id=qname,
            qualified_name=qname,
            title=f"example {i}",
            kind=NodeKind.CODE_EXAMPLE,
            source_path=rel,
            start_line=1,
            end_line=1,
            text=code,
            content_hash=_content_hash(code, NodeKind.CODE_EXAMPLE, f"example {i}"),
            extra_metadata={"language": lang},
            parent_id=parent_qname,
        ))
    return examples


def _module_from_path(path: str, root: Path) -> str:
    """Convert a ``.py`` path to a dotted module name.

    Strips suffix, drops a trailing ``__init__``, joins ``/`` → ``.`` —
    matches Python's import machinery. If ``path`` isn't under ``root``
    (tests using fake paths, vendored files, etc.), falls back to the
    basename stem.
    """
    p = Path(path)
    root_abs = root.resolve() if root.is_absolute() else Path.cwd() / root
    try:
        rel = p.resolve().relative_to(root_abs)
    except ValueError:
        rel = Path(p.name)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or p.stem


def _relpath(path: str, root: Path) -> str:
    """Relative source path from the indexing root; opaque strings pass
    through unchanged (satisfies spec §4.3 ``source_path`` contract)."""
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except ValueError:
        return path


def _slice_lines(lines: list[str], start: int, end: int) -> str:
    """1-indexed inclusive line slice. Returns joined text (no trailing
    newline). Clamps negative / zero starts to line 1."""
    s = max(1, start) - 1
    e = max(s, end)
    return "\n".join(lines[s:e])


def _content_hash(text: str, kind: NodeKind, title: str) -> str:
    """Stable 12-char MD5 prefix over ``(kind, title, text)``. Used for
    incremental re-index — identical inputs produce identical hashes
    across processes (hashlib is deterministic unlike Python ``hash()``)."""
    h = hashlib.md5(f"{kind.value}:{title}:{text}".encode()).hexdigest()
    return h[:12]


def _docstring_summary(doc: str) -> str:
    """First line of the docstring, truncated to 140 chars. Empty
    docstring → empty summary (never raises, never returns ``None``)."""
    if not doc:
        return ""
    first = doc.strip().split("\n", 1)[0]
    return first[:140]


# ── HeadingMarkdownChunker (.md) ─────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@_register_chunker(".md")
@dataclass(frozen=True, slots=True)
class HeadingMarkdownChunker:
    """Parses ``.md`` into a MODULE root with flat MARKDOWN_HEADING children
    and optional CODE_EXAMPLE grandchildren (spec §8.2).

    Heading levels outside ``[min_heading_level, max_heading_level]`` are
    skipped. Fenced triple-backtick blocks inside a heading's direct text
    become CODE_EXAMPLE children of that heading; the code is removed from
    the heading's ``text`` so search results don't double-count. If the
    file contains no heading-in-range, the MODULE carries the full file
    body as its direct text (no structural children).
    """

    min_heading_level: int = 1
    max_heading_level: int = 3

    def build_tree(
        self, path: str, content: str, package: str, root: Path,
    ) -> DocumentNode:
        module = _module_from_doc_path(path, root)
        rel = _relpath(path, root)
        headings = _parse_md_headings(
            content, self.min_heading_level, self.max_heading_level,
        )
        if not headings:
            return _md_module_node(module, rel, content, content, headings=())
        first_line = headings[0]["line"]
        preamble = "\n".join(content.splitlines()[: first_line - 1])
        tree_children = _build_heading_nodes(
            headings, content, module, rel, parent_id=module,
        )
        return _md_module_node(
            module, rel, content, preamble, headings=tree_children,
        )

    @classmethod
    def from_config(cls, cfg: ChunkingConfig) -> "HeadingMarkdownChunker":
        return cls(
            min_heading_level=cfg.markdown.min_heading_level,
            max_heading_level=cfg.markdown.max_heading_level,
        )


# ── Markdown SRP helpers ─────────────────────────────────────────────────────


def _module_from_doc_path(path: str, root: Path) -> str:
    """Doc-file module id = relative path with ``/`` → ``.`` and the final
    suffix (``.md`` / ``.ipynb``) stripped. Paths outside ``root`` fall
    back to the basename stem."""
    p = Path(path)
    root_abs = root.resolve() if root.is_absolute() else Path.cwd() / root
    try:
        rel = p.resolve().relative_to(root_abs)
    except ValueError:
        rel = Path(p.name)
    parts = list(rel.with_suffix("").parts)
    return ".".join(parts) or p.stem


def _parse_md_headings(
    content: str, min_level: int, max_level: int,
) -> list[dict]:
    """Scan ``content`` for ``#``-style headings within ``[min, max]``.

    Returns one dict per in-range heading with keys:
    - ``level`` (1–6)
    - ``title`` (heading text, ``#``s stripped)
    - ``line`` (1-indexed source line of the heading marker)
    """
    headings: list[dict] = []
    for m in _HEADING_RE.finditer(content):
        level = len(m.group(1))
        if level < min_level or level > max_level:
            continue
        line = content[: m.start()].count("\n") + 1
        headings.append({
            "level": level,
            "title": m.group(2),
            "line": line,
        })
    return headings


def _build_heading_nodes(
    headings: list[dict], content: str, module: str, rel: str,
    *, parent_id: str,
) -> tuple[DocumentNode, ...]:
    """Build flat MARKDOWN_HEADING nodes — one per in-range heading.

    Hierarchy is deliberately kept flat in this chunker (spec §8.2 — each
    heading is a direct child of MODULE). Fenced code blocks inside a
    heading's direct-text span are extracted as CODE_EXAMPLE children and
    removed from the heading's ``text``.
    """
    lines = content.splitlines()
    nodes: list[DocumentNode] = []
    for i, h in enumerate(headings):
        start_line = h["line"] + 1
        end_line = (
            headings[i + 1]["line"] - 1
            if i + 1 < len(headings)
            else len(lines)
        )
        raw_text = (
            "\n".join(lines[start_line - 1 : end_line])
            if start_line <= end_line
            else ""
        )
        qname = f"{module}#{_slugify(h['title'])}"
        cleaned, examples = _extract_md_fenced_examples(
            raw_text, qname, rel, start_line=start_line, end_line=end_line,
        )
        nodes.append(DocumentNode(
            node_id=qname,
            qualified_name=qname,
            title=h["title"],
            kind=NodeKind.MARKDOWN_HEADING,
            source_path=rel,
            start_line=h["line"],
            end_line=end_line,
            text=cleaned,
            content_hash=_content_hash(
                cleaned, NodeKind.MARKDOWN_HEADING, h["title"],
            ),
            summary=_docstring_summary(cleaned),
            extra_metadata={"module": module, "level": h["level"]},
            parent_id=parent_id,
            children=tuple(examples),
        ))
    return tuple(nodes)


def _extract_md_fenced_examples(
    raw_text: str, parent_qname: str, rel: str,
    *, start_line: int, end_line: int,
) -> tuple[str, list[DocumentNode]]:
    """Strip fenced blocks from ``raw_text`` and return (cleaned_text, examples).

    Each fenced block becomes a CODE_EXAMPLE child keyed by
    ``f"{parent_qname}.__example_{i}__"`` with the fence tag captured in
    ``extra_metadata["language"]``.
    """
    cleaned_parts: list[str] = []
    examples: list[DocumentNode] = []
    last = 0
    for i, m in enumerate(_FENCED_RE.finditer(raw_text), start=1):
        cleaned_parts.append(raw_text[last : m.start()])
        lang = m.group(1) or ""
        code = m.group(2)
        qname = f"{parent_qname}.__example_{i}__"
        examples.append(DocumentNode(
            node_id=qname,
            qualified_name=qname,
            title=f"example {i}",
            kind=NodeKind.CODE_EXAMPLE,
            source_path=rel,
            start_line=start_line,
            end_line=end_line,
            text=code,
            content_hash=_content_hash(
                code, NodeKind.CODE_EXAMPLE, f"example {i}",
            ),
            extra_metadata={"language": lang},
            parent_id=parent_qname,
        ))
        last = m.end()
    cleaned_parts.append(raw_text[last:])
    cleaned = "".join(cleaned_parts).strip()
    return cleaned, examples


def _md_module_node(
    module: str, rel: str, full_content: str, direct_text: str,
    *, headings: tuple[DocumentNode, ...],
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


# ── NotebookChunker (.ipynb) ────────────────────────────────────────────────


@_register_chunker(".ipynb")
@dataclass(frozen=True, slots=True)
class NotebookChunker:
    """Parses ``.ipynb`` JSON into a MODULE root with one child per cell.

    ``include_outputs`` controls whether code-cell outputs are appended to
    each cell's ``text`` (default ``False`` — cell outputs are noisy, often
    stderr or base64 image data, and pollute FTS). Malformed JSON falls
    back to a single MODULE carrying the raw file content, so an unreadable
    notebook still produces a searchable chunk (spec §8.3).
    """

    include_outputs: bool = False

    def build_tree(
        self, path: str, content: str, package: str, root: Path,
    ) -> DocumentNode:
        module = _module_from_doc_path(path, root)
        rel = _relpath(path, root)
        cells = _safe_load_cells(content, path)
        if cells is None:
            return _fallback_module_node(module, path, content, root)
        children = tuple(
            _notebook_cell_node(
                cell, i, module, rel, include_outputs=self.include_outputs,
            )
            for i, cell in enumerate(cells)
        )
        return DocumentNode(
            node_id=module,
            qualified_name=module,
            title=module,
            kind=NodeKind.MODULE,
            source_path=rel,
            start_line=1,
            end_line=max(len(cells), 1),
            text="",
            content_hash=_content_hash("", NodeKind.MODULE, module),
            extra_metadata={"module": module, "cell_count": len(cells)},
            children=children,
        )

    @classmethod
    def from_config(cls, cfg: ChunkingConfig) -> "NotebookChunker":
        return cls(include_outputs=cfg.notebook.include_outputs)


# ── Notebook SRP helpers ────────────────────────────────────────────────────


def _safe_load_cells(content: str, path: str) -> list[dict] | None:
    """Parse notebook JSON and return its ``cells`` array.

    Returns ``None`` on malformed JSON / unexpected shape so the caller can
    fall back to a single MODULE — we prefer a lossy-but-searchable chunk
    over crashing the ingestion pipeline on a corrupt file.
    """
    try:
        nb = json.loads(content)
    except (ValueError, TypeError) as exc:
        log.warning("notebook json parse failed on %s: %s", path, exc)
        return None
    if not isinstance(nb, dict):
        log.warning("notebook top-level is not an object: %s", path)
        return None
    cells = nb.get("cells", [])
    if not isinstance(cells, list):
        log.warning("notebook 'cells' is not a list: %s", path)
        return None
    return cells


def _notebook_cell_node(
    cell: dict, index: int, module: str, rel: str,
    *, include_outputs: bool,
) -> DocumentNode:
    """Build one MARKDOWN / CODE cell node keyed by ``module#cell-{index}``."""
    cell_type = cell.get("cell_type", "")
    text = _join_source(cell.get("source", ""))
    if cell_type == "markdown":
        kind = NodeKind.NOTEBOOK_MARKDOWN_CELL
        first_line = text.split("\n", 1)[0].strip()
        title = (first_line or f"cell {index}")[:80]
    else:
        kind = NodeKind.NOTEBOOK_CODE_CELL
        title = f"cell {index}"
        if include_outputs:
            out = _format_cell_outputs(cell.get("outputs", []))
            if out:
                text = f"{text}\n\n# Output:\n{out}"
    qname = f"{module}#cell-{index}"
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=title,
        kind=kind,
        source_path=rel,
        start_line=1,
        end_line=1,
        text=text,
        content_hash=_content_hash(text, kind, title),
        extra_metadata={
            "module": module,
            "cell_index": index,
            "cell_type": cell_type,
        },
        parent_id=module,
    )


def _join_source(src: object) -> str:
    """Jupyter stores ``source`` as either a single string or a list of
    line strings — normalise both to one string."""
    if isinstance(src, list):
        return "".join(str(x) for x in src)
    return str(src) if src else ""


def _format_cell_outputs(outputs: list) -> str:
    """Concatenate plain-text output segments from a code cell.

    Prefers ``out["text"]`` (stream outputs) and falls back to
    ``out["data"]["text/plain"]`` (execute_result / display_data). All
    other rich MIME types are ignored — base64 PNGs, SVGs etc. would
    bloat the FTS index without helping search.
    """
    parts: list[str] = []
    for out in outputs:
        if not isinstance(out, dict):
            continue
        if "text" in out:
            parts.append(_join_source(out["text"]))
        elif isinstance(out.get("data"), dict):
            plain = out["data"].get("text/plain")
            if plain is not None:
                parts.append(_join_source(plain))
    return "\n".join(p for p in parts if p)
