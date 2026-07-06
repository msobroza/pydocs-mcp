"""``inline_markers`` source — mine decision markers from extracted trees (spec §D8).

Walks the already-extracted :class:`DocumentNode` trees (no file re-reads —
nodes carry ``text`` / ``source_path`` / ``start_line``) and scans each node's
direct text for the six decision markers (``# WHY:`` … ``# WORKAROUND:``). Each
hit becomes a :class:`RawDecision` whose sole evidence span is the marker line
plus a bounded context window of surrounding source lines (verbatim, nothing
paraphrased at capture, §D8).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.extraction.decisions._types import (
    _MARKER_RE,
    CaptureContext,
    DecisionEvidence,
    RawDecision,
    decision_source_registry,
)
from pydocs_mcp.extraction.model import DocumentNode, NodeKind

# Deterministic mining is a strong signal but not authoritative — the LLM
# structuring gate (default OFF) never gets to lower this, and ADR files
# (author-curated) outrank it at 1.0. Module constant per single-source rule.
_CONFIDENCE = 0.95
_TITLE_MAX = 80
_NAME = "inline_markers"


@decision_source_registry.register(_NAME)
@dataclass(frozen=True, slots=True)
class InlineMarkersSource:
    """Mines ``# DECISION:`` / ``# WHY:`` / … markers from the document trees."""

    name: str = _NAME

    async def mine(self, ctx: CaptureContext) -> tuple[RawDecision, ...]:
        context_lines = ctx.config.inline_markers.context_lines
        raws: list[RawDecision] = []
        for root in ctx.trees:
            _mine_node(root, module_qname=None, context_lines=context_lines, out=raws)
        return tuple(raws)


def _mine_node(
    node: DocumentNode,
    *,
    module_qname: str | None,
    context_lines: int,
    out: list[RawDecision],
) -> None:
    """Scan one node's text, then recurse into children (DFS).

    ``module_qname`` threads the nearest ancestor MODULE node's qualified name
    down the tree so a marker on a nested FUNCTION/METHOD node still attributes
    to its module. A node with no MODULE ancestor attributes to itself.
    """
    current_qname = node.qualified_name if node.kind is NodeKind.MODULE else module_qname
    lines = node.text.splitlines()
    for offset, line in enumerate(lines):
        raw = _marker_to_raw(node, current_qname, lines, offset, line, context_lines)
        if raw is not None:
            out.append(raw)
    for child in node.children:
        _mine_node(child, module_qname=current_qname, context_lines=context_lines, out=out)


def _marker_to_raw(
    node: DocumentNode,
    module_qname: str | None,
    lines: list[str],
    offset: int,
    line: str,
    context_lines: int,
) -> RawDecision | None:
    """Turn one matching line into a :class:`RawDecision`, or ``None`` if no match."""
    match = _MARKER_RE.search(line)
    if match is None:
        return None
    keyword, payload = match.group(1), match.group(2).strip()
    window = _context_window(lines, offset, context_lines)
    locator = f"{node.source_path}:{node.start_line + offset}"
    evidence = DecisionEvidence(source=_NAME, locator=locator, text=window)
    qname = module_qname if module_qname is not None else node.qualified_name
    return RawDecision(
        title=payload[:_TITLE_MAX],
        status="rejected" if keyword == "REJECTED" else "active",
        source=_NAME,
        confidence=_CONFIDENCE,
        evidence=(evidence,),
        affected_files=(node.source_path,),
        affected_qnames=(qname,),
    )


def _context_window(lines: list[str], offset: int, context_lines: int) -> str:
    """Verbatim marker line ± ``context_lines`` surrounding lines, clamped to bounds."""
    start = max(0, offset - context_lines)
    end = min(len(lines), offset + context_lines + 1)
    return "\n".join(lines[start:end])
