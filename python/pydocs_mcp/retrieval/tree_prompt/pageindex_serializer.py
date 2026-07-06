"""PageIndex-shape serialization of DocumentNode trees for LLM prompts.

Builds the LLM-visible tree (``qualified_name`` join key, enriched
decorator+signature titles, bounded doc excerpts) — deliberately NOT
``DocumentNode.to_pageindex_json``; see :func:`pageindex_with_qname` for
why ``node_id`` must never reach the LLM.
"""

from __future__ import annotations

from typing import Any

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.strategies.chunkers._shared import (
    _collapse_ws,
    _header_from_text,
)
from pydocs_mcp.retrieval.tree_prompt.doc_excerpt import (
    DEFAULT_DOC_EXCERPT,
    DEFAULT_DOC_EXCERPT_MAX_CHARS,
    doc_excerpt_with_flag,
)

# Cap on the per-node enriched title (decorators + signature) so a giant
# multi-line signature can't dominate the prompt. The header scanner + its
# scan-limit live in the shared chunker utils (``_header_from_text`` in
# extraction/strategies/chunkers/_shared.py).
TITLE_MAX_CHARS = 200


def enriched_title(node: DocumentNode) -> str:
    """Decorators + real signature for code nodes; the plain title otherwise.

    Falls back to ``node.title`` when the derived header doesn't look like a
    signature (e.g. synthetic nodes whose ``text`` isn't real source), so
    only genuine ``def`` / ``class`` headers replace the bare ``def foo()``
    title. The header scanner (``_header_from_text``) and whitespace collapse
    (``_collapse_ws``) are shared with the chunker (see
    ``extraction/strategies/chunkers/_shared.py``). Bounded by
    ``TITLE_MAX_CHARS``. Decorators are NOT in ``node.text`` (Python 3.11
    ``lineno`` points at ``def`` / ``class``), so they're prepended separately.
    """
    decorators = node.extra_metadata.get("decorators") or ()
    if node.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
        header = _header_from_text(node.text, max_chars=TITLE_MAX_CHARS)
        if not header.startswith(("def ", "async def ", "class ")):
            header = node.title
    else:
        header = node.title
    if decorators:
        header = f"{' '.join(str(d) for d in decorators)} {header}".strip()
    return header[:TITLE_MAX_CHARS]


def pageindex_with_qname(
    node: DocumentNode,
    *,
    doc_mode: str = DEFAULT_DOC_EXCERPT,
    doc_max_chars: int = DEFAULT_DOC_EXCERPT_MAX_CHARS,
    _truncations: list[int] | None = None,
) -> dict[str, Any]:
    """Build the LLM-visible tree shape — only fields the prompt asks for.

    The shipped :meth:`DocumentNode.to_pageindex_json` emits ``node_id``,
    ``source_path``, ``start_index``, ``end_index`` (because LookupService
    needs that shape and a contract test in
    ``tests/extraction/test_document_node_lookup_contract.py`` pins it),
    but the LLM here MUST NOT see ``node_id``: the prompts ask for
    ``qualified_name`` (a stable symbol path), while ``node_id`` is a
    per-extraction auto-generated content-hash identifier that doesn't
    exist in chunk metadata. If the LLM saw ``node_id`` it would be an
    attractive nuisance — the model would naturally pick the shorter,
    flatter-looking string, and downstream :func:`_parse_node_list` would
    silently drop every pick (because it filters against the
    ``qualified_name`` set, the only field that joins back to
    ``chunk.metadata["qualified_name"]`` via :func:`flatten_to_chunks`).

    So this helper deliberately bypasses ``to_pageindex_json`` and builds a
    tight shape: ``qualified_name`` (the join key), an enriched ``title``
    (decorators + real signature via :func:`enriched_title`), ``kind``,
    ``summary``, an optional bounded ``doc`` excerpt, and recursive
    ``nodes``. ``doc`` is omitted when empty or identical to ``summary``
    (summary is already the docstring's first line — duplicating it would
    just burn tokens). Source-line spans are dropped — the LLM doesn't pick
    on byte offsets, and omitting them keeps the prompt budget tight.
    """
    out: dict[str, Any] = {
        "qualified_name": node.qualified_name,
        "title": enriched_title(node),
        "kind": node.kind.value,
        "summary": node.summary,
    }
    docstring = str(node.extra_metadata.get("docstring", "") or "")
    excerpt, truncated = doc_excerpt_with_flag(docstring, doc_mode, doc_max_chars)
    # Omit doc when it adds nothing beyond summary: empty, exactly summary, or
    # merely a (possibly longer) cut of the docstring's first line — summary
    # already carries that line, so a duplicate just burns prompt budget. A
    # richer excerpt (first line + Args/Returns/Raises) is longer than the
    # first line, so it survives this check and is kept.
    first_line = _collapse_ws(docstring.strip().split("\n", 1)[0])
    if (
        excerpt
        and excerpt != node.summary
        and not (first_line and excerpt == first_line[: len(excerpt)])
    ):
        out["doc"] = excerpt
        # Record truncation only for an EMITTED doc, so the aggregated warning
        # reflects real dropped content (not excerpts that get omitted anyway).
        if truncated and _truncations is not None:
            _truncations.append(1)
    out["nodes"] = [
        pageindex_with_qname(
            child,
            doc_mode=doc_mode,
            doc_max_chars=doc_max_chars,
            _truncations=_truncations,
        )
        for child in node.children
    ]
    return out


__all__ = ("TITLE_MAX_CHARS", "enriched_title", "pageindex_with_qname")
