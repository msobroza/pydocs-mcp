"""Symbol views — how ``get_symbol`` renders a resolved document-tree node.

One module per the glossary's "Symbol views" section (``CONTEXT.md``): both the
**symbol card** at ``depth="summary"`` and the budgeted **outline** at
``depth="tree"`` live here rather than in ``lookup_service`` (dispatch) or
``formatting`` (every other tool's markdown), because the two depths have to
agree with each other about what a target contains.

The card is the DEFAULT depth, so it is small by construction (ADR 0023): the
signature, the first doc line, and the names of the immediate children under a
YAML cap, ending in "and N more" plus a pointer at the outline when the cap
bites. A module target's card is the same rendering of its module root — its
doc line and its top-level members.

The outline is the structural depth: one compact line per node — kind,
qualified name, line span, indentation showing nesting — fitted to a YAML token
budget by level cut, so the depth an agent reaches for to understand a large
module can no longer flood its context (ADR 0008 measured the JSON it replaces
at up to 5,825 tokens for one module). A cut outline says so: it ends in the
``levels L of D shown, N nodes elided`` footer plus recovery pointers at the
largest elided subtrees, and its ``items[]`` carry exactly the nodes its text
shows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.application.pointer_bundles import (
    render_pointer_bundle,
    rendered_depths,
    token_for_action,
)
from pydocs_mcp.application.truncation import TruncationEntry, get_active_ledger
from pydocs_mcp.pointer_table import PointerTableConfig, PointerVerb, ResponseKind

# The outline's token counter — the SAME one the LLM-prompt tree fitter uses
# (``retrieval/tree_prompt/tree_budget_fitter.py``), which is all the two share.
from pydocs_mcp.retrieval.llm_clients.model_budget import count_tokens
from pydocs_mcp.retrieval.tree_prompt.outline_level_cut import (
    LevelCut,
    OutlineRow,
    cut_outline_to_budget,
    elided_subtrees,
)

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


def _view_bundle(
    kind: ResponseKind,
    target: str,
    pointers: PointerTableConfig,
    rendered_here: frozenset[tuple[str, str]],
) -> str:
    """The pointer bundle one symbol view ends with, or ``""``.

    The single place the two views read the table, so the card and the outline
    cannot drift into offering follow-ups from different sources.
    """
    return render_pointer_bundle(pointers.row_for(kind), target, rendered_here=rendered_here)


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


def render_symbol_card(
    node: DocumentNode, *, child_cap: int, pointers: PointerTableConfig
) -> SymbolCard:
    """Render ``node`` as a symbol card, naming at most ``child_cap`` children.

    Records a truncation entry when the cap bites, so ``meta.truncated`` and the
    envelope footer report the cut the same way every other capped body does
    (the ``depth="source"`` line cap is the precedent). The inline "and N more"
    plus the outline pointer ARE the recovery, so the entry carries none — a
    second copy in the footer would be the duplication ADR 0023 removes. For the
    same reason a capped card's bundle drops the outline it already handed over.

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
        lines.append(token_for_action(PointerVerb.OUTLINE, node.qualified_name))
        _record_cap(node, elided, child_cap)
    already = rendered_depths(node.qualified_name, PointerVerb.OUTLINE) if elided else frozenset()
    bundle = _view_bundle(ResponseKind.SYMBOL_CARD, node.qualified_name, pointers, already)
    return SymbolCard(
        text="\n".join(lines) + "\n" + bundle,
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
    return f"{node.source_path}:{_span(node)}"


def _span(node: DocumentNode) -> str:
    """``start-end``, collapsed to ``start`` for a one-line node."""
    if node.start_line == node.end_line:
        return str(node.start_line)
    return f"{node.start_line}-{node.end_line}"


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


# ── the outline (``depth="tree"``) ─────────────────────────────────────────

# Two spaces per level of nesting: deep enough to read as a tree, cheap enough
# that a six-level outline spends a couple of tokens on indentation.
_INDENT = "  "

# get_symbol is not an LLM call, so there is no model to count against and
# ``count_tokens`` falls back to its fixed o200k_base encoding (see
# ``model_budget._encoding_for``). Deliberate: the budget is one stable output
# bound across every deployment, not a context-window guarantee for one model.
_NO_MODEL = ""


@dataclass(frozen=True, slots=True)
class Outline:
    """One rendered outline: its text, the nodes it shows, and what it cut.

    ``nodes`` is pre-order and is exactly what the text lists, so the caller's
    ``items[]`` rows carry the same node set — the one place in the surface
    where items are narrowed to the text (contract §3.3, argued in ADR 0023).
    ``elided`` is 0 exactly when the whole tree fitted.
    """

    text: str
    nodes: tuple[DocumentNode, ...]
    levels_shown: int
    total_levels: int
    elided: int


def render_outline(
    node: DocumentNode,
    *,
    token_budget: int,
    recovery_pointer_count: int,
    pointers: PointerTableConfig,
) -> Outline:
    """Render ``node``'s document tree as an outline fitted to ``token_budget``.

    The budget is measured on the text this function returns — footer, recovery
    pointers and the closing bundle included — so a cut can never be pushed back
    over the bound by the very lines that announce it. ``token_budget <= 0``
    turns fitting off. A cut records a truncation entry, so ``meta.truncated`` is
    true and the envelope footer names it the way every other capped body does;
    the inline footer and its pointers ARE the recovery, so the entry carries
    none.

    Example::

        outline = render_outline(module_root, token_budget=2048, recovery_pointer_count=3)
        outline.text.splitlines()[0]   # 'module pkg.mod · pkg/mod.py:1-20'
    """
    bundle = _outline_bundle(node, pointers)

    def measure(candidate: LevelCut) -> int:
        return count_tokens(_render_cut(node, candidate, recovery_pointer_count, bundle), _NO_MODEL)

    cut = cut_outline_to_budget(node, max_tokens=token_budget, measure=measure)
    if cut.elided:
        _record_cut(node, cut)
    return Outline(
        text=_render_cut(node, cut, recovery_pointer_count, bundle),
        nodes=tuple(row.node for row in cut.rows),
        levels_shown=cut.levels_shown,
        total_levels=cut.total_levels,
        elided=cut.elided,
    )


def _outline_bundle(root: DocumentNode, pointers: PointerTableConfig) -> str:
    """The outline's closing bundle — what comes AFTER the structure.

    The outline IS this root's tree, so a row naming the outline action would
    point at what the response just rendered; the cut's own recovery pointers
    name elided SUBTREES, which is a different target every time.
    """
    return _view_bundle(
        ResponseKind.OUTLINE,
        root.qualified_name,
        pointers,
        rendered_depths(root.qualified_name, PointerVerb.OUTLINE),
    )


def _render_cut(root: DocumentNode, cut: LevelCut, recovery_pointer_count: int, bundle: str) -> str:
    """The outline text for one candidate cut — the unit the budget measures."""
    lines = _render_rows(cut.rows, root.source_path)
    if cut.elided:
        lines.extend(("", _cut_footer(cut), *_recovery_pointers(cut, recovery_pointer_count)))
    return "\n".join(lines) + "\n" + bundle


def _render_rows(rows: tuple[OutlineRow, ...], root_path: str) -> list[str]:
    """One line per row, each trimmed parent's ``and N more`` after its children.

    The marker has to close the parent's child list, not open it: printed right
    under the parent it would read as the FIRST of that parent's children and
    claim the ones below it were the extras.
    """
    lines: list[str] = []
    pending: list[OutlineRow] = []
    for row in rows:
        _close_trimmed_parents(lines, pending, row.depth)
        lines.append(_outline_line(row, root_path))
        if row.trimmed:
            pending.append(row)
    _close_trimmed_parents(lines, pending, 0)
    return lines


def _close_trimmed_parents(lines: list[str], pending: list[OutlineRow], depth: int) -> None:
    """Emit the ``and N more`` of every trimmed parent the next row has left."""
    while pending and pending[-1].depth >= depth:
        parent = pending.pop()
        lines.append(f"{_INDENT * (parent.depth + 1)}and {parent.trimmed} more")


def _outline_line(row: OutlineRow, root_path: str) -> str:
    """``<indent><kind> <qualified name> · <span>`` — the outline's one line shape.

    The QUALIFIED name, not a name relative to the parent: every line is then a
    target an agent can hand straight back to ``get_symbol`` / ``get_context``
    without reassembling it out of the indentation.
    """
    kind = str(row.node.kind)
    head = f"{_INDENT * row.depth}{kind} {row.node.qualified_name}"
    location = _location(row.node) if row.depth == 0 else _descendant_location(row.node, root_path)
    return _FIELD_SEPARATOR.join([head, location]) if location else head


def _descendant_location(node: DocumentNode, root_path: str) -> str:
    """A bare line span for a node in the target's file, its full location else.

    WHY: a document tree is one file, so the path on the target's own line
    already covers every descendant — repeating it on three hundred lines
    spends the budget on a constant. A node from another file keeps its path so
    the line never claims a span in the wrong file.
    """
    if node.source_path and node.source_path != root_path:
        return _location(node)
    return _span(node)


def _cut_footer(cut: LevelCut) -> str:
    """``levels L of D shown, N nodes elided`` — wording fixed by ADR 0023 (f).

    Fixed to the letter, plural included: agents and the eval suite match this
    line, so ``1 nodes elided`` is a deliberate constant rather than a typo.
    """
    return f"levels {cut.levels_shown} of {cut.total_levels} shown, {cut.elided} nodes elided"


def _recovery_pointers(cut: LevelCut, count: int) -> tuple[str, ...]:
    """Up to ``count`` ready-made outline calls at the largest elided subtrees.

    Empty when the only node with elided descendants is the target itself (a
    wide module trimmed per parent): a pointer at it would re-issue this very
    call, and the inline ``and N more`` already says what is missing.
    """
    return tuple(
        token_for_action(PointerVerb.OUTLINE, node.qualified_name)
        for node, _dropped in elided_subtrees(cut)[:count]
    )


def _record_cut(node: DocumentNode, cut: LevelCut) -> None:
    """Report the level cut to the active truncation ledger, when one is open."""
    ledger = get_active_ledger()
    if ledger is None:
        return
    ledger.record(
        TruncationEntry(
            description=(
                f"{cut.elided} node(s) of `{node.qualified_name}` beyond the outline "
                f"token budget (levels {cut.levels_shown} of {cut.total_levels} shown)"
            ),
            recovery="",  # the inline footer + its pointers ARE the recovery
        )
    )


__all__ = ("Outline", "SymbolCard", "render_outline", "render_symbol_card")
