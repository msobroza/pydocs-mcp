"""The pointer grammar — the surface-neutral token, and how it renders per surface.

A *pointer* (``CONTEXT.md``) is a ready-made follow-up call rendered in a response.
Renderers emit a surface-NEUTRAL token, because the code that renders a hit cannot
know whether the response leaves via MCP or the CLI. ``ResponseEnvelope`` calls
:func:`resolve_pointers` once, at the router layer, and every token in the body
becomes that surface's call form.

This module owns the token shape and both call forms. WHICH pointers a response
offers is the pointer table's job (:mod:`pydocs_mcp.pointer_table`), and assembling
a table row into a **pointer bundle** is :mod:`pydocs_mcp.application.pointer_bundles`
— which builds on this module. Nothing here knows about response kinds.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pydocs_mcp.application.mcp_inputs import is_symbol_target  # single source: target grammar

# The closed action vocabulary (spec §D5). Token payloads are dotted names /
# show-mode words (no ':' or ']]'), which keeps the grammar regex-parsable.
#
# The ``overview`` action's target is an optional PROJECT selector. Empty
# (``[[next:overview:]]``) is the zero-hit-search recovery step (spec §D1 empty
# contract) — get_overview scopes to a package, not a symbol, so no payload is
# needed; the target group is ``*`` (not ``+``) so the empty shape parses.
# Non-empty is the workspace card's per-project deepening pointer
# (``get_overview(project=...)`` on a multi-repo server).
#
# ``overview-package`` is its sibling: the same tool, the OTHER corpus-scope
# selector (``get_overview(package=...)``). Two actions rather than one,
# because a bare target cannot say which selector it belongs to — the
# package-doc truncation recovery emits a PACKAGE name, and routing it through
# ``overview`` advertised a call that sends a package to the project selector.
#
# The ``why`` action deepens into the decision surface (spec §D17 block 8): with
# an EMPTY target it opens the governance dashboard (``get_why()``); with a
# non-empty target it runs a decision search over that query. The target group is
# ``*`` so both shapes parse.
#
# The ``read`` action (ADR 0023 Decision (c)) carries a line WINDOW rather than a
# name: the target group holds the file path and the third group holds
# ``<offset>+<limit>`` — three arguments through the grammar's two payload
# groups, so the token shape itself is unchanged. ``[[next:read:src/app.py:118+40]]``
# resolves to ``read_file(file_path="src/app.py", offset=118, limit=40)``. A path
# carrying ``:`` or ``]`` cannot be expressed and is never emitted
# (``read_pointer_token``), the same rejection rule ``WhyInput`` applies.
#
# Alternation is longest-first so a shorter action never shadows a longer one
# that starts with it (``overview`` vs ``overview-package``).
_POINTER_RE = re.compile(
    r"\[\[next:(lookup-show|lookup|search|overview-package|overview|why|read)"
    r":([^:\]]*)(?::([^:\]]+))?\]\]"
)

# Token + its leading blanks + its line ending — the one elision span shared by
# ``resolve_pointers``'s suppression pre-pass and ``strip_pointers``, so a
# suppressed token disappears byte-identically to the ``pointers_enabled=False``
# strip path. ``_elided_pointer_span`` decides what the span leaves behind: an
# own-line token takes its whole line (no leftover blank line where the token's
# line used to be); an inline token keeps the line break it sat before — the
# overview / workspace bullets carry their token at the end of the line, and
# eating that newline merged every bullet whose pointer was elided into the next
# one. The ``[ \t]*`` prefix is ungrouped, so ``_POINTER_RE``'s group indices
# (read by ``_is_invalid_symbol_pointer``) are unchanged.
_POINTER_SPAN_RE = re.compile(r"[ \t]*" + _POINTER_RE.pattern + r"\n?")
# The same span for ANY pointer-shaped token — the strip path removes them all.
_ANY_POINTER_SPAN_RE = re.compile(r"[ \t]*\[\[next:[^\]]*\]\]\n?")


def _elided_pointer_span(match: re.Match[str]) -> str:
    """What an elided pointer span leaves behind: ``""`` for an own-line token
    (the span starts at column 0), the line break for an inline one."""
    start = match.start()
    if start == 0 or match.string[start - 1] == "\n":
        return ""
    return "\n" if match.group(0).endswith("\n") else ""


# The one show word whose target payload may carry SEVERAL qualified names:
# ``get_context`` is the only tool in the frozen surface that takes a target
# list, so it is the only call a fan-out can consolidate into (CONTEXT.md "batch
# call"). The names travel comma-separated through the grammar's existing target
# group — a comma is not a character any symbol target can hold
# (``mcp_inputs._TARGET_RE``), so the payload stays unambiguous and the token
# shape is unchanged.
_BATCH_SHOW = "context"
TARGET_SEPARATOR = ","

# show-mode → (mcp renderer, cli renderer). context maps to a get_context batch
# of one or more targets; tree/default stay on get_symbol via depth.
_SHOW_TO_TOOL: dict[str, tuple[str, str]] = {
    "callers": (
        'get_references(target="{t}", direction="callers")',
        "pydocs-mcp refs {t} --direction callers",
    ),
    "callees": (
        'get_references(target="{t}", direction="callees")',
        "pydocs-mcp refs {t} --direction callees",
    ),
    "inherits": (
        'get_references(target="{t}", direction="inherits")',
        "pydocs-mcp refs {t} --direction inherits",
    ),
    "impact": (
        'get_references(target="{t}", direction="impact")',
        "pydocs-mcp refs {t} --direction impact",
    ),
    # The fifth direction the tools accept. Without it a full governed_by page's
    # recovery pointer resolved to nothing and leaked its raw token.
    "governed_by": (
        'get_references(target="{t}", direction="governed_by")',
        "pydocs-mcp refs {t} --direction governed_by",
    ),
    # ``{t}`` is the rendered target LIST, not one name — see ``_context_payload``.
    "context": ("get_context(targets=[{t}])", "pydocs-mcp context {t}"),
    "tree": ('get_symbol(target="{t}", depth="tree")', "pydocs-mcp symbol {t} --depth tree"),
    "source": ('get_symbol(target="{t}", depth="source")', "pydocs-mcp symbol {t} --depth source"),
}


def pointer_token(action: str, target: str, show: str = "") -> str:
    """Build a surface-neutral next-step token.

    The third group is emitted whenever ``show`` carries one: the show word for
    ``lookup-show``, the ``<offset>+<limit>`` window for ``read``. Every other
    action leaves it empty and renders the two-group shape.
    """
    if show:
        return f"[[next:{action}:{target}:{show}]]"
    return f"[[next:{action}:{target}]]"


# Per-action pointer resolution — ``(cli, mcp)`` renderers keyed by action.
# ``lookup-show`` (needs ``show``) and the ``lookup`` default fall through to
# their own branches; the table covers the actions whose render is a pure
# function of ``surface`` + ``target``. Keeping one small closure per action
# holds ``_render_pointer``'s branching flat (complexity gate).
_POINTER_RENDERERS: dict[str, tuple[Callable[[str], str], Callable[[str], str]]] = {
    # Empty target → the whole-scope orientation card (zero-hit-search
    # recovery); non-empty → that project's card (workspace-card deepening).
    "overview": (
        lambda t: f"→ pydocs-mcp overview --project {t}" if t else "→ pydocs-mcp overview",
        lambda t: f'→ get_overview(project="{t}")' if t else "→ get_overview()",
    ),
    # The package selector — the CLI takes it as the positional argument.
    "overview-package": (
        lambda t: f"→ pydocs-mcp overview {t}" if t else "→ pydocs-mcp overview",
        lambda t: f'→ get_overview(package="{t}")' if t else "→ get_overview()",
    ),
    # Empty target → the governance dashboard (get_why with no query);
    # non-empty → a decision search over that query.
    "why": (
        lambda t: f'→ pydocs-mcp why "{t}"' if t else "→ pydocs-mcp why",
        lambda t: f'→ get_why(query="{t}")' if t else "→ get_why()",
    ),
    "search": (
        lambda t: f'→ pydocs-mcp search "{t}"',
        lambda t: f'→ search_codebase(query="{t}")',
    ),
}


# The one action a path-shaped response can offer, and the one whose payload
# is a line window rather than a name (CONTEXT.md "pointer table").
READ_ACTION = "read"

# ``<offset>+<limit>``: the ``read`` action's window payload, 1-indexed start
# line and how many lines to take.
_READ_WINDOW_RE = re.compile(r"^(\d+)\+(\d+)$")


def _render_read_call(path: str, window: str | None, surface: str) -> str | None:
    """The ``read_file`` call for one window payload, or ``None`` when it is not one.

    ``None`` means the token is indexed chunk content that merely LOOKS like the
    grammar (this repo indexes its own tests and docs), never a live pointer —
    the same literal-content precedence ``lookup-show`` applies to an unknown
    show word.
    """
    parsed = _READ_WINDOW_RE.match(window or "")
    if parsed is None:
        return None
    offset, limit = parsed.group(1), parsed.group(2)
    if surface == "cli":
        return f"→ pydocs-mcp read_file {path} --offset {offset} --limit {limit}"
    return f'→ read_file(file_path="{path}", offset={offset}, limit={limit})'


def _context_payload(target: str, surface: str) -> str:
    """The target-list substitution of a ``get_context`` call form.

    One name renders exactly as it did before batching existed
    (``get_context(targets=["a"])`` / ``pydocs-mcp context a``); several render
    as the list each surface spells.

    Example: ``_context_payload("a,b", "mcp")`` → ``'"a", "b"'``.
    """
    names = target.split(TARGET_SEPARATOR)
    if surface == "cli":
        return " ".join(names)
    return ", ".join(f'"{name}"' for name in names)


def _render_pointer(match: re.Match[str], surface: str) -> str:
    action, target, show = match.group(1), match.group(2), match.group(3)
    if action == READ_ACTION:
        return _render_read_call(target, show, surface) or match.group(0)
    renderers = _POINTER_RENDERERS.get(action)
    if renderers is not None:
        cli_render, mcp_render = renderers
        return cli_render(target) if surface == "cli" else mcp_render(target)
    if action == "lookup-show":
        # ``show`` is None (no show word) or an unrecognized word whenever a
        # pointer-SHAPED literal comes from indexed chunk content rather than
        # ``pointer_token()`` — e.g. this repo indexes its own tests/docs as
        # __project__, and test/doc bodies quote the grammar verbatim. Only a
        # renderer-produced token is guaranteed to have a valid show word, so
        # an unknown one means "not actually a live token" — leave it as-is
        # rather than KeyError on ``_SHOW_TO_TOOL``.
        renderer = _SHOW_TO_TOOL.get(show) if show is not None else None
        if renderer is None:
            return match.group(0)
        mcp_fmt, cli_fmt = renderer
        fmt = cli_fmt if surface == "cli" else mcp_fmt
        payload = _context_payload(target, surface) if show == _BATCH_SHOW else target
        return "→ " + fmt.format(t=payload)
    if surface == "cli":
        return f"→ pydocs-mcp symbol {target}"
    return f'→ get_symbol(target="{target}")'


# Pointer-bundle group labels (CONTEXT.md "pointer bundle"): the calls that are
# independent of each other, then the ones that need a prior result first.
TOGETHER_LABEL = "Together:"
THEN_LABEL = "Then:"

_GROUP_LABELS = rf"(?:{re.escape(TOGETHER_LABEL)}|{re.escape(THEN_LABEL)})"
# What marks a line as carrying live pointer machinery — the grammar's opening,
# whatever action follows it.
_POINTER_MARK = "[[next:"
# A whole bundle line — a group label, at least one pointer-shaped token, and
# whatever qualifies them (a batch call states how many rows it left unnamed).
# Stripping or suppressing tokens one at a time would leave the label, and now
# the qualifier too, behind as an orphan line, so both elision paths match the
# LINE first. The "at least one token" requirement is what keeps indexed prose
# that happens to carry a bare ``Then:`` line out of the match.
_BUNDLE_LINE_RE = re.compile(
    rf"^{_GROUP_LABELS}(?:[ \t]*\[\[next:[^\]]*\]\])+[^\n]*\n?", re.MULTILINE
)


def _is_invalid_symbol_pointer(match: re.Match[str]) -> bool:
    """Live lookup / lookup-show token whose target the symbol tools reject.

    Only these two actions render dotted-target follow-ups (``get_symbol`` /
    ``get_context`` / ``get_references``); search/why/overview targets are
    queries/selectors with their own grammars. Literal-content precedence
    stays first: a lookup-show token with a missing or unknown show word is
    indexed chunk content (see ``_render_pointer``), never suppressed.
    """
    action, target, show = match.group(1), match.group(2), match.group(3)
    if action == "lookup":
        return not is_symbol_target(target)
    if action == "lookup-show":
        if show is None or show not in _SHOW_TO_TOOL:
            return False
        return not _every_target_is_valid(target, show)
    return False


def _every_target_is_valid(target: str, show: str) -> bool:
    """Whether every name in a pointer's target payload is a valid symbol target.

    Only the batch show word carries several names. For every other show a comma
    is simply a character no target may hold, so splitting there would let one
    malformed name through as two valid ones.
    """
    names = target.split(TARGET_SEPARATOR) if show == _BATCH_SHOW else [target]
    return all(is_symbol_target(name) for name in names)


def resolve_pointers(text: str, surface: str) -> str:
    """Rewrite every pointer token to ``surface`` syntax ("mcp" | "cli").

    Symbol-tool pointers whose target fails ``is_symbol_target`` are
    suppressed instead of rendered — a response must never advertise a
    follow-up call the tool's own input validator rejects (markdown /
    decision document paths like ``docs.adr.0001-x.md``).
    """
    text = _BUNDLE_LINE_RE.sub(_suppressed_bundle_line, text)
    text = _POINTER_SPAN_RE.sub(_suppress_invalid_symbol_pointer, text)
    return _POINTER_RE.sub(lambda m: _render_pointer(m, surface), text)


def _suppress_invalid_symbol_pointer(match: re.Match[str]) -> str:
    """Elide an invalid symbol-tool token span; leave any other span verbatim."""
    if _is_invalid_symbol_pointer(match):
        return _elided_pointer_span(match)
    return match.group(0)


def _suppressed_bundle_line(match: re.Match[str]) -> str:
    """One bundle line minus its invalid symbol-tool tokens.

    A line every one of whose pointers is suppressed goes entirely, qualifier
    included — advertising a group label with no call behind it, or a count of
    the rows a vanished call did not name, would be worse than advertising none.
    """
    kept = _POINTER_SPAN_RE.sub(_suppress_invalid_symbol_pointer, match.group(0))
    return kept if _POINTER_MARK in kept else ""


def strip_pointers(text: str) -> str:
    """Remove every pointer token — restores pre-§D5 bytes.

    A whole bundle line goes with its group label; an own-line token takes its
    whole line; an inline token goes with its leading blanks but keeps the line
    break it sat before.
    """
    return _ANY_POINTER_SPAN_RE.sub(_elided_pointer_span, _BUNDLE_LINE_RE.sub("", text))
