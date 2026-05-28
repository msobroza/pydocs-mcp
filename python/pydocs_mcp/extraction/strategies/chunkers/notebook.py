"""NotebookChunker — parses ``.ipynb`` JSON into MODULE + one child per cell.

``include_outputs`` controls whether code-cell outputs are appended to
each cell's ``text`` (default ``False`` — cell outputs are noisy, often
stderr or base64 image data, and pollute FTS). Malformed JSON falls
back to a single MODULE carrying the raw file content, so an unreadable
notebook still produces a searchable chunk (spec §8.3).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.config import ChunkingConfig
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.serialization import _register_chunker
from pydocs_mcp.extraction.strategies.chunkers._shared import (
    _content_hash,
    _fallback_module_node,
    _module_from_doc_path,
    _relpath,
)

log = logging.getLogger("pydocs-mcp")


@_register_chunker(".ipynb")
@dataclass(frozen=True, slots=True)
class NotebookChunker:
    include_outputs: bool = False

    def build_tree(
        self,
        path: str,
        content: str,
        package: str,
        root: Path,
    ) -> DocumentNode:
        module = _module_from_doc_path(path, root)
        rel = _relpath(path, root)
        cells = _safe_load_cells(content, path)
        if cells is None:
            return _fallback_module_node(module, path, content, root)
        children = tuple(
            _notebook_cell_node(
                cell,
                i,
                module,
                rel,
                include_outputs=self.include_outputs,
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
    def from_config(cls, cfg: ChunkingConfig) -> NotebookChunker:
        return cls(include_outputs=cfg.notebook.include_outputs)


def _safe_load_cells(content: str, path: str) -> list[dict] | None:
    """Parse notebook JSON and return its ``cells`` array.

    Returns ``None`` on malformed JSON / unexpected shape so the caller
    can fall back to a single MODULE — we prefer a lossy-but-searchable
    chunk over crashing the ingestion pipeline on a corrupt file.
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
    cell: dict,
    index: int,
    module: str,
    rel: str,
    *,
    include_outputs: bool,
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


__all__ = ("NotebookChunker",)
