"""Pointer bundles — turning one pointer-table row into the lines a response ends with.

A *pointer bundle* (``CONTEXT.md``) is the set of follow-up calls one response
offers, split into calls that are independent of each other ("together") and calls
that need a prior result first ("then"). The row comes from the pointer table
(:mod:`pydocs_mcp.pointer_table`), the target comes from the renderer — which alone
knows the qualified name or path it just rendered — and the tokens come from the
pointer grammar (:mod:`pydocs_mcp.application.pointer_grammar`), so both surfaces
render from one path.

Two bundle shapes, one self-pointing rule (:func:`_admitted_pairs`):
:func:`render_pointer_bundle` for a response about one target (or a row that names
several at once), and :func:`render_fanout_bundle` for a response that LISTS many
symbols, where a group collapses into one **batch call** past the table's batch
threshold. :func:`offered_pointer` is the bare-token form the truncation ledger
carries as a **recovery pointer**.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydocs_mcp.application.pointer_grammar import (
    READ_ACTION,
    TARGET_SEPARATOR,
    THEN_LABEL,
    TOGETHER_LABEL,
    pointer_token,
)
from pydocs_mcp.pointer_table import PointerTableConfig, PointerTableRow, pointer_action


def token_for_action(action_name: str, target: str) -> str:
    """The surface-neutral token for one pointer-table action aimed at ``target``.

    ``""`` for the ``read`` action, whose payload is a line WINDOW rather than a
    name: only the renderer that knows which lines it elided can build one
    (:func:`read_pointer_line`). Rendering it from a bare target would put a
    windowless — and therefore unresolvable — token in the response, which is
    the raw-token leak this machinery exists to prevent.
    """
    if action_name == READ_ACTION:
        return ""
    action = pointer_action(action_name)
    return pointer_token(action.token_action, target, action.show)


def rendered_depths(target: str, *action_names: str) -> frozenset[tuple[str, str]]:
    """The depths a response has already rendered for ``target``.

    The one vocabulary every renderer uses to declare what it showed, so the
    self-pointing rule is stated the same way everywhere (CONTEXT.md
    "self-pointing"). A hit that printed its whole span declares
    ``rendered_depths(qname, "source")``; a listing about one symbol declares
    every action its row names.

    Example: ``rendered_depths("pkg.mod", "outline")`` → ``{("outline", "pkg.mod")}``.
    """
    return frozenset((name, target) for name in action_names)


def _admitted_pairs(
    action_names: Sequence[str],
    targets: Sequence[str],
    rendered_here: frozenset[tuple[str, str]],
) -> list[tuple[str, str]]:
    """The action/target pairs a bundle may offer — the ONE self-pointing check.

    Both bundle shapes filter here and nowhere else: a pair whose target this
    response already rendered at that depth is dropped, because offering it
    would hand the agent back the lines it just read.

    Action-major over ``targets``: a row that names one action and several
    targets (a decision naming every symbol it governs) yields that action's
    pairs in the order the renderer supplied them.
    """
    return [
        (name, target)
        for name in action_names
        for target in targets
        if (name, target) not in rendered_here
    ]


def _pointer_group_line(
    label: str,
    action_names: Sequence[str],
    targets: Sequence[str],
    rendered_here: frozenset[tuple[str, str]],
) -> str:
    """One bundle line, or ``""`` when the group renders no token.

    A group renders none when it is empty, when every pointer in it is
    self-pointing, or when its only action carries a window (see
    :func:`token_for_action`).
    """
    wanted = _admitted_pairs(action_names, targets, rendered_here)
    tokens = [token for name, target in wanted if (token := token_for_action(name, target))]
    return f"{label} {' '.join(tokens)}\n" if tokens else ""


def render_pointer_bundle(
    row: PointerTableRow,
    targets: str | Sequence[str],
    *,
    rendered_here: frozenset[tuple[str, str]] = frozenset(),
) -> str:
    """Render one response kind's pointer bundle: the together line, then the then line.

    ``targets`` is the one thing the bundle aims at, or the several a single row
    fans out over (a decision names every symbol it governs). The tokens stay
    surface-neutral, so ``ResponseEnvelope`` resolves the bundle to the MCP or
    the CLI call form through the same ``pointer_grammar.resolve_pointers``
    every other pointer goes through — one rendering path, two surfaces.

    ``rendered_here`` carries the ``(action, target)`` pairs this response has
    already rendered, so a bundle can never point at its own content
    (CONTEXT.md "self-pointing").

    Example::

        render_pointer_bundle(PointerTableRow(together=("outline",)), "pkg.mod")
        # "Together: [[next:lookup-show:pkg.mod:tree]]\\n"
    """
    aimed = (targets,) if isinstance(targets, str) else tuple(targets)
    return _pointer_group_line(
        TOGETHER_LABEL, row.together, aimed, rendered_here
    ) + _pointer_group_line(THEN_LABEL, row.then, aimed, rendered_here)


def _batch_group_line(
    label: str,
    action_names: Sequence[str],
    targets: Sequence[str],
    pointers: PointerTableConfig,
    listed_rows: int,
) -> str:
    """One bundle line whose calls each carry SEVERAL targets at once.

    The line states how many of the rows above it the call does NOT cover — the
    ones past the batch ceiling, plus any whose symbol the follow-up tools
    cannot address — so a capped batch call is never read as covering the page.
    """
    named = pointers.batch_targets(targets)
    payload = TARGET_SEPARATOR.join(named)
    tokens = [token for name in action_names if (token := token_for_action(name, payload))]
    if not tokens:
        return ""
    unnamed = max(0, listed_rows - len(named))
    noun = "row" if unnamed == 1 else "rows"
    remainder = f" ({unnamed} more {noun} not named)" if unnamed else ""
    return f"{label} {' '.join(tokens)}{remainder}\n"


def _fanout_group_line(
    label: str,
    action_names: Sequence[str],
    targets: Sequence[str],
    pointers: PointerTableConfig,
    listed_rows: int,
    rendered_here: frozenset[tuple[str, str]],
) -> str:
    """One group of a many-target bundle: the batch call, or a call per target.

    The group's actions split by how many targets one of their calls can carry
    (``PointerAction.batch``). At or above the batch threshold the batchable
    action renders ONE call over the targets and the per-target actions stand
    down — the fan-out they would render is exactly what the batch replaces.
    Below it there is no fan-out worth collapsing, so each target keeps its own
    per-target call and the batchable action stays silent.
    """
    batch_names = [name for name in action_names if pointer_action(name).batch]
    admitted = _admitted_pairs(batch_names, targets, rendered_here)
    aimed = tuple(dict.fromkeys(target for _name, target in admitted))
    # With no batchable action ``aimed`` is empty and the threshold (>= 1) can
    # never be reached, so a group of per-target actions falls straight through.
    if pointers.consolidates(len(aimed)):
        return _batch_group_line(label, batch_names, aimed, pointers, listed_rows)
    per_target = [name for name in action_names if not pointer_action(name).batch]
    return _pointer_group_line(label, per_target, targets, rendered_here)


def render_fanout_bundle(
    row: PointerTableRow,
    targets: Sequence[str],
    *,
    pointers: PointerTableConfig,
    listed_rows: int,
    about: str,
) -> str:
    """Render the pointer bundle of a response that lists MANY symbols.

    The sibling of :func:`render_pointer_bundle` for reference and impact
    listings: same group labels, same surface-neutral tokens, same
    self-pointing check — but a row here fans out over one target per listed
    row, so each group consolidates into one **batch call** once the fan-out
    reaches ``pointers.batch_threshold`` (CONTEXT.md).

    ``about`` is the symbol the page answers about. A listing IS that answer, so
    a follow-up aimed back at it would re-issue the call that produced the page,
    whichever depth the row names — the renderer derives that set itself rather
    than asking every call site to remember it.

    ``listed_rows`` is how many rows the response shows, which is what a capped
    batch call reports against — a call naming eight of fifty says so.

    Example::

        render_fanout_bundle(row, ("a", "b", "c"), pointers=cfg, listed_rows=3, about="z")
        # 'Together: [[next:lookup-show:a,b,c:context]]\\n'
    """
    aimed = tuple(dict.fromkeys(targets))  # dedupe, first-seen order = render order
    rendered_here = rendered_depths(about, *row.together, *row.then)
    return _fanout_group_line(
        TOGETHER_LABEL, row.together, aimed, pointers, listed_rows, rendered_here
    ) + _fanout_group_line(THEN_LABEL, row.then, aimed, pointers, listed_rows, rendered_here)


def offered_pointer(row: PointerTableRow, target: str) -> str:
    """The single token ``row`` offers for ``target`` — the recovery-pointer form.

    Used where the follow-up belongs in the truncation ledger rather than in a
    bundle line: the ledger renders the cut and the call that recovers it as one
    footer (``TruncationEntry.recovery``), so a second copy in the body would be
    the duplication ADR 0023 removes. The FIRST action the row names wins —
    a recovery is one call, not a bundle.

    ``""`` when the row names nothing, or only the window-payload ``read``
    action, whose form is :func:`offered_read_pointer`.

    Example: ``offered_pointer(PointerTableRow(together=("overview-package",)), "numpy")``
    → ``"[[next:overview-package:numpy]]"``.
    """
    for name in (*row.together, *row.then):
        token = token_for_action(name, target)
        if token:
            return token
    return ""


def read_pointer_token(path: str, offset: int, limit: int) -> str:
    """The surface-neutral token for a ready-made ``read_file`` window.

    ``""`` for a path the grammar cannot carry — empty, or holding the ``:`` /
    ``]`` that would corrupt the token (contract §3.6). A response must never
    advertise a follow-up call its own grammar mangles.

    Example: ``read_pointer_token("src/app.py", 118, 40)`` →
    ``"[[next:read:src/app.py:118+40]]"``.
    """
    if not path or ":" in path or "]" in path:
        return ""
    return pointer_token(READ_ACTION, path, f"{offset}+{limit}")


def _read_group_label(row: PointerTableRow) -> str:
    """The bundle-group label naming the ``read`` action in ``row``, or ``""``."""
    for label, names in ((TOGETHER_LABEL, row.together), (THEN_LABEL, row.then)):
        if READ_ACTION in names:
            return label
    return ""


def offered_read_pointer(row: PointerTableRow, path: str, offset: int, limit: int) -> str:
    """The read token ``row`` offers for this window — the recovery-pointer form.

    Used where the window recovers ELIDED content: the truncation ledger
    resolves it into the response footer, so the cut and its remedy render
    together (``TruncationEntry.recovery``).
    """
    return read_pointer_token(path, offset, limit) if _read_group_label(row) else ""


def read_pointer_line(row: PointerTableRow, path: str, offset: int, limit: int) -> str:
    """The bundle line a path-shaped response body ends with, or ``""``.

    Same group labels as :func:`render_pointer_bundle`, so both bundle shapes
    strip and resolve through one path.

    Example: ``read_pointer_line(row, "src/app.py", 118, 40)`` →
    ``"Together: [[next:read:src/app.py:118+40]]"``.
    """
    label = _read_group_label(row)
    token = read_pointer_token(path, offset, limit) if label else ""
    return f"{label} {token}" if token else ""
