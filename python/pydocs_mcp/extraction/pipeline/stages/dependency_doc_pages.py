"""DependencyDocPagesStage — one docstring "page" chunk per dependency module.

Big dependencies (torch, sklearn, ...) carry most of their prose in docstrings,
not ``.md`` files. Embedding every code chunk of such packages is the dominant
indexing cost, so the selective embed policy skips dependency code chunks — and
this stage preserves the *semantic* coverage by distilling each dependency
module into ONE page: the module docstring plus every public top-level class /
function signature + docstring (code bodies excluded). The page is emitted as a
regular chunk with ``origin=dependency_module_doc``, which the embed policy
treats as documentation → it DOES get a dense vector. One embedding per module
instead of one per def: torch drops from ~50k embedded chunks to ~2.6k pages.

No-op for project targets and for dependencies with no docstrings at all.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState, TargetKind
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import Chunk, ChunkFilterField, ChunkOrigin

log = logging.getLogger("pydocs-mcp")

# ~2000 tokens at the 4-chars/token heuristic — inside every shipped embedder's
# max_seq_length, so a page never silently truncates inside the embedder.
_DEFAULT_MAX_CHARS = 8000


def _module_name(path: str, root: Path, package: str) -> str:
    """Dotted module path for ``path``, mirroring the chunkers' relpath rule."""
    try:
        rel = Path(path).resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        rel = Path(path)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    dotted = ".".join(parts)
    return dotted or package


def _signature(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """A one-line ``def``/``class`` header reconstructed from the AST."""
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        return f"class {node.name}({bases}):" if bases else f"class {node.name}:"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({ast.unparse(node.args)}):"


def build_doc_page_text(content: str) -> str:
    """Distil one module's source into its docstring page (may be empty).

    Module docstring first, then each PUBLIC top-level class/def as its
    signature + docstring. Entries without a docstring are omitted — the page
    is a semantic artifact, not an API catalog (``module_members`` already
    covers signatures exhaustively for ``kind="api"`` search).
    """
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return ""
    parts: list[str] = []
    mod_doc = ast.get_docstring(tree)
    if mod_doc:
        parts.append(mod_doc.strip())
    for node in tree.body:
        if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name.startswith("_"):
            continue
        doc = ast.get_docstring(node)
        if not doc:
            continue
        parts.append(f'{_signature(node)}\n    """{doc.strip()}"""')
    return "\n\n".join(parts)


@stage_registry.register("dependency_doc_pages")
@dataclass(frozen=True, slots=True)
class DependencyDocPagesStage:
    """Append one ``dependency_module_doc`` page chunk per dependency module."""

    max_chars: int = _DEFAULT_MAX_CHARS
    name: str = "dependency_doc_pages"

    async def run(self, state: IngestionState) -> IngestionState:
        if state.files.target_kind is not TargetKind.DEPENDENCY:
            return state
        package = state.files.package_name
        pages: list[Chunk] = []
        for path, content in state.files.file_contents:
            if not path.endswith(".py"):
                continue
            text = build_doc_page_text(content)
            if not text:
                continue
            module = _module_name(path, state.files.root, package)
            pages.append(
                Chunk(
                    text=text[: self.max_chars],
                    metadata={
                        ChunkFilterField.PACKAGE.value: package,
                        ChunkFilterField.TITLE.value: f"{module} documentation",
                        ChunkFilterField.MODULE.value: module,
                        ChunkFilterField.ORIGIN.value: ChunkOrigin.DEPENDENCY_MODULE_DOC.value,
                        "qualified_name": module,
                        "kind": "module",
                    },
                )
            )
        if not pages:
            return state
        log.debug("dependency_doc_pages: %s -> %d page(s)", package, len(pages))
        bundle = replace(state.chunks, chunks=state.chunks.chunks + tuple(pages))
        return replace(state, chunks=bundle)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> DependencyDocPagesStage:
        return cls(max_chars=data.get("max_chars", _DEFAULT_MAX_CHARS))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "dependency_doc_pages"}
        if self.max_chars != _DEFAULT_MAX_CHARS:
            d["max_chars"] = self.max_chars
        return d


__all__ = ("DependencyDocPagesStage", "build_doc_page_text")
