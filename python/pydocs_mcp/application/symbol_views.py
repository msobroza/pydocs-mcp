"""Symbol views — how ``get_symbol`` renders a resolved document-tree node.

One module per the glossary's "Symbol views" section (``CONTEXT.md``): the
**symbol card** at ``depth="summary"`` lives here, and the budgeted **outline**
at ``depth="tree"`` joins it beside the card rather than growing
``lookup_service`` (dispatch) or ``formatting`` (every other tool's markdown).

The card is the DEFAULT depth, so it is small by construction (ADR 0023): the
signature, the first doc line, and the names of the immediate children under a
YAML cap, ending in "and N more" plus a pointer at the outline when the cap
bites. A module target's card is the same rendering of its module root — its
doc line and its top-level members.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.application.formatting import pointer_token
from pydocs_mcp.application.truncation import TruncationEntry, get_active_ledger

# The signature-from-a-node derivation (decorators + the real ``def`` / ``class``
# header, capped) has one owner; the card shows the same string the LLM-visible
# tree does rather than re-deriving a second, drifting notion of "signature".
from pydocs_mcp.retrieval.tree_prompt.pageindex_serializer import enriched_title

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode

# Field separator of the card's identity line. Matches the freshness header's
# own separator, so one response reads with one punctuation vocabulary.
_FIELD_SEPARATOR = " · "

_NO_MEMBERS_LINE = "No members."


@dataclass(frozen=True, slots=True)
class SymbolCard:
    """One rendered card: its text, the nodes it names, and what it left out.

    ``nodes`` is the target followed by exactly the children the text lists, so
    the caller's ``items[]`` rows carry the card's node set and nothing else
    (contract §3.3). ``elided`` is the number of children the cap dropped.
    """

    text: str
    nodes: tuple[DocumentNode, ...]
    elided: int


def render_symbol_card(node: DocumentNode, *, child_cap: int) -> SymbolCard:
    """Render ``node`` as a symbol card, naming at most ``child_cap`` children.

    Records a truncation entry when the cap bites, so ``meta.truncated`` and the
    envelope footer report the cut the same way every other capped body does
    (the ``depth="source"`` line cap is the precedent). The inline "and N more"
    plus the outline pointer ARE the recovery, so the entry carries none — a
    second copy in the footer would be the duplication ADR 0023 removes.

    Example::

        card = render_symbol_card(class_node, child_cap=20)
        card.text.splitlines()[-1]   # 'Members (2): run, stat'
    """
    listed = tuple(node.children[:child_cap])
    elided = len(node.children) - len(listed)
    lines = [_identity_line(node)]
    doc = _first_doc_line(node)
    if doc:
        lines.append(doc)
    lines.append("")
    lines.append(_members_line(node, listed, elided))
    if elided:
        lines.append(pointer_token("lookup-show", node.qualified_name, "tree"))
        _record_cap(node, elided, child_cap)
    return SymbolCard(
        text="\n".join(lines) + "\n",
        nodes=(node, *listed),
        elided=elided,
    )


def _identity_line(node: DocumentNode) -> str:
    """Signature · qualified name · location — the fields that differ.

    A module's signature IS its qualified name (its title is the module id), so
    the middle field is dropped rather than printed twice; a node with no
    source path (synthetic scaffolding) drops the location.
    """
    signature = enriched_title(node)
    fields = [signature]
    if node.qualified_name != signature:
        fields.append(node.qualified_name)
    location = _location(node)
    if location:
        fields.append(location)
    return _FIELD_SEPARATOR.join(fields)


def _location(node: DocumentNode) -> str:
    """``path:start-end``, collapsed to ``path:line`` for a one-line node."""
    if not node.source_path:
        return ""
    if node.start_line == node.end_line:
        return f"{node.source_path}:{node.start_line}"
    return f"{node.source_path}:{node.start_line}-{node.end_line}"


def _first_doc_line(node: DocumentNode) -> str:
    """The node's summary, first line only — ``""`` when it is undocumented."""
    lines = node.summary.strip().splitlines()
    return lines[0].strip() if lines else ""


def _members_line(node: DocumentNode, listed: tuple[DocumentNode, ...], elided: int) -> str:
    """``Members (N): a, b, and K more`` — or a definitive "nothing inside".

    The count is the TRUE child count, not the listed count, so an agent reading
    a capped card knows how much it is not seeing. Children are named, never
    filtered by kind: the card's member set must be the node set the outline
    renders at ``depth="tree"``, and a hidden filter would make the two depths
    disagree about what the target contains.
    """
    if not listed:
        return _NO_MEMBERS_LINE
    names = ", ".join(_child_name(node, child) for child in listed)
    tail = f", and {elided} more" if elided else ""
    return f"Members ({len(node.children)}): {names}{tail}"


def _child_name(parent: DocumentNode, child: DocumentNode) -> str:
    """The child's own name — its qualified name minus the parent's prefix.

    Falls back to the full qualified name when the child does not sit under the
    parent's dotted prefix (markdown heading anchors, notebook cells), so the
    card never advertises a name that cannot be looked up.
    """
    prefix = f"{parent.qualified_name}."
    qualified = child.qualified_name
    return qualified[len(prefix) :] if qualified.startswith(prefix) else qualified


def _record_cap(node: DocumentNode, elided: int, child_cap: int) -> None:
    """Report the cap to the active truncation ledger, when one is open."""
    ledger = get_active_ledger()
    if ledger is None:
        return
    ledger.record(
        TruncationEntry(
            description=(
                f"{elided} member(s) of `{node.qualified_name}` beyond the "
                f"{child_cap}-member card cap"
            ),
            recovery="",  # the inline outline pointer IS the recovery
        )
    )


__all__ = ("SymbolCard", "render_symbol_card")
