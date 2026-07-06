"""AstPythonChunker — parses ``.py`` into a MODULE → CLASS / FUNCTION tree.

Structure: MODULE → (IMPORT_BLOCK? | FUNCTION* | CLASS*) with METHOD
children under each CLASS, and nested ``def``s recursively emitted as
FUNCTION children under their enclosing FUNCTION / METHOD.
Docstrings on MODULE / FUNCTION / METHOD /
CLASS may contribute CODE_EXAMPLE grandchildren via fenced-block
extraction (spec §8.1).

Parse failure: logs a warning and returns a single MODULE node whose
``text`` is the full source — the file still produces a searchable
chunk, no crash.

Reference capture: accepts an optional ``ref_collector`` for cross-node
reference capture (sub-PR #5b). When ``None`` (default) no references
are emitted, so existing callers see zero behavior change.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.extraction.config import ChunkingConfig
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.serialization import _register_chunker
from pydocs_mcp.extraction.strategies.chunkers._shared import (
    _FENCED_RE,
    _code_example_node,
    _content_hash,
    _docstring_summary,
    _fallback_module_node,
    _header_from_text,
    _relative_module_parts,
    _relpath,
    _slice_lines,
)
from pydocs_mcp.extraction.strategies.references import (
    canonical_dotted,
    capture_calls,
    capture_imports,
    capture_inherits,
)

if TYPE_CHECKING:
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector

log = logging.getLogger("pydocs-mcp")


def _capture_safely(
    capture_fn: Callable[..., None],
    label: str,
    **kwargs: object,
) -> None:
    """Run a reference-capture function, swallowing any failure at WARNING.

    Single owner of the swallow-and-warn policy: a broken file must
    degrade to 'no references captured', never abort indexing.
    ``capture_fn.__name__`` keeps the per-capture log messages identical
    to the pre-hoist per-site copies.
    """
    try:
        capture_fn(**kwargs)
    except Exception as exc:
        log.warning("%s failed on %s: %s", capture_fn.__name__, label, exc)


@_register_chunker(".py")
@dataclass(frozen=True, slots=True)
class AstPythonChunker:
    def build_tree(
        self,
        path: str,
        content: str,
        package: str,
        root: Path,
        ref_collector: ReferenceCollector | None = None,
    ) -> DocumentNode:
        module = _module_from_path(path, root)
        tree = _safe_parse(content, path)
        if tree is None:
            return _fallback_module_node(module, path, content, root)
        # Module-level capture: imports + alias table for downstream resolver.
        if ref_collector is not None:
            _capture_safely(
                capture_imports,
                path,
                body=tree.body,
                from_package=package,
                module_qname=module,
                collector=ref_collector,
            )
        return _module_node_from_ast(
            tree,
            module,
            path,
            content,
            root,
            ref_collector=ref_collector,
            package=package,
        )

    @classmethod
    def from_config(cls, cfg: ChunkingConfig) -> AstPythonChunker:
        return cls()


def _safe_parse(content: str, path: str) -> ast.Module | None:
    """Parse source; return ``None`` and log on ``SyntaxError``."""
    try:
        return ast.parse(content)
    except SyntaxError as exc:
        log.warning("ast.parse failed on %s: %s", path, exc)
        return None


def _module_node_from_ast(
    tree: ast.Module,
    module: str,
    path: str,
    content: str,
    root: Path,
    *,
    ref_collector: ReferenceCollector | None = None,
    package: str = "",
) -> DocumentNode:
    """Build the MODULE root + all children from a parsed ``ast.Module``."""
    lines = content.splitlines()
    rel = _relpath(path, root)
    children = _extract_module_children(
        tree,
        module,
        lines,
        rel,
        ref_collector=ref_collector,
        package=package,
    )
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
    tree: ast.Module,
    module: str,
    lines: list[str],
    rel: str,
    *,
    ref_collector: ReferenceCollector | None = None,
    package: str = "",
) -> list[DocumentNode]:
    """One IMPORT_BLOCK per contiguous import run + one FUNCTION / CLASS
    per top-level def. Returned in source order: scattered imports
    (lazy/runtime/TYPE_CHECKING imports) appear at their actual line
    positions instead of being hoisted to the front by the two-pass
    collection. Downstream consumers (tree-flatten, lookup navigation)
    rely on line-sorted children.
    """
    children: list[DocumentNode] = []
    for suffix_counter, run in enumerate(_consecutive_import_runs(tree.body)):
        children.append(
            _import_block_node(
                run,
                module,
                lines,
                rel,
                suffix=suffix_counter,
            )
        )
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            children.append(
                _function_node(
                    stmt,
                    module,
                    lines,
                    rel,
                    parent_id=module,
                    kind=NodeKind.FUNCTION,
                    ref_collector=ref_collector,
                    package=package,
                )
            )
        elif isinstance(stmt, ast.ClassDef):
            children.append(
                _class_node(
                    stmt,
                    module,
                    lines,
                    rel,
                    ref_collector=ref_collector,
                    package=package,
                )
            )
    # Two-pass collection above produces import blocks first, then
    # defs — out of source order when a second import run lives below
    # a def. Stable sort by ``start_line`` restores intuitive ordering.
    children.sort(key=lambda n: n.start_line)
    return children


def _consecutive_import_runs(
    body: list[ast.stmt],
) -> list[list[ast.Import | ast.ImportFrom]]:
    """Group top-level Import/ImportFrom nodes into contiguous runs.

    A run is a maximal sequence of adjacent import statements (no
    non-import statements between them in source order). Returning
    multiple runs preserves the user's intent — lazy / conditional
    imports separated by code stay separate IMPORT_BLOCK nodes so each
    block's ``text`` is exactly its own ``import ...`` lines.
    """
    runs: list[list[ast.Import | ast.ImportFrom]] = []
    current: list[ast.Import | ast.ImportFrom] = []
    for stmt in body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            current.append(stmt)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _import_block_node(
    imports: list[ast.Import | ast.ImportFrom],
    module: str,
    lines: list[str],
    rel: str,
    *,
    suffix: int = 0,
) -> DocumentNode:
    """One IMPORT_BLOCK from a single contiguous import run.

    ``suffix`` disambiguates the synthetic ``qualified_name`` when a
    module has multiple import runs — they each need a unique node_id
    so DocumentTreeStore doesn't collide on the (package, module) PK.
    The first block keeps ``module.__imports__`` for backward shape
    compatibility with single-block files.
    """
    start = imports[0].lineno
    end = imports[-1].end_lineno or start
    txt = _slice_lines(lines, start, end)
    qname = f"{module}.__imports__" if suffix == 0 else f"{module}.__imports__{suffix}"
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title="imports" if suffix == 0 else f"imports@{start}",
        kind=NodeKind.IMPORT_BLOCK,
        source_path=rel,
        start_line=start,
        end_line=end,
        text=txt,
        content_hash=_content_hash(txt, NodeKind.IMPORT_BLOCK, "imports"),
        parent_id=module,
    )


# Bound a single decorator label so a pathological decorator (a long call
# arg list, a subscript, a call returning a callable) can't bloat the
# LLM-visible tree.
_DECORATOR_LABEL_MAX_CHARS = 100


def _decorator_labels(decorators: list[ast.expr]) -> tuple[str, ...]:
    """``@<decorator>`` for each decorator, in source order.

    Call decorators INCLUDE their arguments (``@app.route('/x')`` stays
    ``@app.route('/x')``): the route path / call args carry signal for query
    matching, so the full ``ast.unparse`` render is kept (bounded by
    ``_DECORATOR_LABEL_MAX_CHARS``). Bare dotted names take the
    ``canonical_dotted`` fast-path (also used for class bases) for a
    version-stable dotted form; everything else (subscripts, complex call
    targets, calls with args) falls back to a bounded ``ast.unparse``, which
    is total over valid parsed expressions.
    """
    labels: list[str] = []
    for dec in decorators:
        # Fast-path bare dotted names (``@property``, ``@app.route`` with no
        # call); Call decorators fall through to ast.unparse so their args
        # survive.
        if not isinstance(dec, ast.Call):
            dotted = canonical_dotted(dec)
            if dotted:
                labels.append(f"@{dotted}"[:_DECORATOR_LABEL_MAX_CHARS])
                continue
        labels.append(("@" + ast.unparse(dec))[:_DECORATOR_LABEL_MAX_CHARS])
    return tuple(labels)


def _function_node(
    stmt: ast.FunctionDef | ast.AsyncFunctionDef,
    module: str,
    lines: list[str],
    rel: str,
    *,
    parent_id: str,
    kind: NodeKind,
    ref_collector: ReferenceCollector | None = None,
    package: str = "",
) -> DocumentNode:
    """Shared FUNCTION / METHOD builder. ``kind`` + ``parent_id`` make
    the only difference (methods qualify under the class, functions
    under the module)."""
    qname = f"{parent_id}.{stmt.name}"
    if ref_collector is not None:
        _capture_safely(
            capture_calls,
            qname,
            body=stmt.body,
            from_package=package,
            from_node_id=qname,
            collector=ref_collector,
        )
    doc = ast.get_docstring(stmt) or ""
    start = stmt.lineno
    end = stmt.end_lineno or start
    txt = _slice_lines(lines, start, end)
    is_async = isinstance(stmt, ast.AsyncFunctionDef)
    title = f"{'async def' if is_async else 'def'} {stmt.name}()"
    # Capture the FULL def header (multi-line signatures collapse to one line)
    # via the paren-balancing scanner, not just the first physical line. ``txt``
    # is the def's source (header + body); _header_from_text stops at the first
    # paren-depth-0 ``:``, so the body never leaks into the signature.
    sig_line = _header_from_text(txt)
    # Nested defs become their own FUNCTION children so inner helpers are
    # indexed and retrievable — a benchmark gold that was a def inside
    # main() was unmatchable at any file-size cap without this
    # (PAGEINDEX_DIVS.md F3). ref_collector is deliberately NOT threaded
    # down: capture_calls above already walked the whole body (nested
    # bodies included) attributing CALLS edges to the enclosing def, so
    # re-capturing at nested depth would emit duplicate edges.
    nested_defs = tuple(
        _function_node(
            s,
            module,
            lines,
            rel,
            parent_id=qname,
            kind=NodeKind.FUNCTION,
            ref_collector=None,
            package=package,
        )
        for s in stmt.body
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
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
        extra_metadata={
            "module": module,
            "docstring": doc,
            "signature": sig_line,
            "decorators": _decorator_labels(stmt.decorator_list),
        },
        parent_id=parent_id,
        children=tuple(examples) + nested_defs,
    )


def _class_node(
    stmt: ast.ClassDef,
    module: str,
    lines: list[str],
    rel: str,
    *,
    ref_collector: ReferenceCollector | None = None,
    package: str = "",
) -> DocumentNode:
    """CLASS node with METHOD children. Direct text = class line through
    the line before the first method (spec §4.1.1 direct-text rule)."""
    qname = f"{module}.{stmt.name}"
    if ref_collector is not None:
        _capture_safely(
            capture_inherits,
            qname,
            bases=list(stmt.bases),
            from_package=package,
            class_qname=qname,
            collector=ref_collector,
        )
    doc = ast.get_docstring(stmt) or ""
    method_stmts = [s for s in stmt.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
    start = stmt.lineno
    end = stmt.end_lineno or start
    direct_end = (method_stmts[0].lineno - 1) if method_stmts else end
    direct_txt = _slice_lines(lines, start, direct_end)
    method_nodes = [
        _function_node(
            m,
            module,
            lines,
            rel,
            parent_id=qname,
            kind=NodeKind.METHOD,
            ref_collector=ref_collector,
            package=package,
        )
        for m in method_stmts
    ]
    doc_examples = _extract_code_examples(doc, qname, rel)
    # Non-dotted shapes (Subscript, Call, etc.) become "<complex>".
    inherits = tuple(canonical_dotted(b) or "<complex>" for b in stmt.bases)
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
            "decorators": _decorator_labels(stmt.decorator_list),
        },
        parent_id=module,
        children=tuple(doc_examples) + tuple(method_nodes),
    )


def _extract_code_examples(
    docstring: str,
    parent_qname: str,
    rel: str,
) -> list[DocumentNode]:
    """Pull triple-backtick fenced blocks out of a docstring as
    CODE_EXAMPLE nodes. Fence line-offsets inside the docstring don't map
    cleanly back to source lines (the docstring's position in source is
    stmt.lineno+1, but the fence offset within the docstring is opaque),
    so start/end_line are stubbed to 1 — consumers key examples by
    ``qualified_name`` / ``node_id``."""
    if not docstring:
        return []
    return [
        _code_example_node(
            match.group("body"),
            (match.group("lang") or "").strip(),
            i,
            parent_qname,
            rel,
        )
        for i, match in enumerate(_FENCED_RE.finditer(docstring), start=1)
    ]


def _python_package_root(source_file: Path) -> Path:
    """Find the parent directory of the topmost ``__init__.py`` ancestor.

    Walks upward from ``source_file`` collecting consecutive directories
    that contain ``__init__.py``. The PARENT of the topmost such dir is
    the right "root" for computing a dotted module qname — it matches
    what Python's import machinery uses when ``source_file``'s package
    is added to ``sys.path``.

    Sub-PR #5c, AC #13: pre-#5c, project-source qnames came out as
    ``python.pydocs_mcp.X`` because the indexing ``root`` was the project
    directory and the filesystem walked through ``python/``. Using this
    helper, the root becomes the parent of the topmost
    ``__init__.py``-containing dir (typically ``project/python/``), so
    the qname matches ``import pydocs_mcp.X``.

    Falls back to ``source_file.parent`` when no ``__init__.py`` is
    found anywhere up the chain — handles loose scripts / scratch files.
    """
    p = source_file.resolve() if source_file.is_absolute() else (Path.cwd() / source_file).resolve()
    cur = p.parent
    topmost_pkg: Path | None = None
    while True:
        if (cur / "__init__.py").exists():
            topmost_pkg = cur
            if cur.parent == cur:
                break
            cur = cur.parent
        else:
            break
    return topmost_pkg.parent if topmost_pkg is not None else p.parent


def _module_from_path(path: str, root: Path) -> str:
    """Convert a ``.py`` path to a dotted module name.

    Strips suffix, drops a trailing ``__init__``, joins ``/`` → ``.`` —
    matches Python's import machinery.

    Sub-PR #5c (AC #13): if the file lives inside a real Python package
    (its parent directory has ``__init__.py``), we use the package root
    discovered by :func:`_python_package_root` as the effective root so
    the resulting qname matches ``import pkg.mod`` (not
    ``python.pkg.mod``). When the file's parent is NOT a package — e.g.
    synthetic paths used in unit tests, or loose ``.py`` scripts — the
    passed-in ``root`` is honored unchanged so test fixtures that
    construct ``tmp_path/pkg/mod.py`` without ``__init__.py`` still get
    a ``pkg.mod`` qname relative to the caller's root.
    """
    p = Path(path)
    is_in_package = p.parent.is_dir() and (p.parent / "__init__.py").exists()
    effective_root = _python_package_root(p) if is_in_package else root
    parts, _p2 = _relative_module_parts(path, effective_root)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or p.stem


__all__ = ("AstPythonChunker", "_module_from_path", "_python_package_root")
