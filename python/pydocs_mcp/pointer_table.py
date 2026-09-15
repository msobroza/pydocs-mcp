"""The pointer table — which ready-made follow-up calls each response kind offers.

Glossary (``CONTEXT.md``): a *pointer* is a ready-made follow-up call rendered in a
response, naming the tool and the arguments for the next step; a *pointer bundle* is
the set one response offers, split into calls that are independent of each other
("together") and calls that need a prior result first ("then"); the *pointer table* is
the single table that says which pointers each kind of response offers, and in which
group.

This module owns the vocabulary and the YAML-facing model only. It is a leaf: it
imports nothing from ``application`` or ``retrieval``, so both layers can depend on it
(the :mod:`pydocs_mcp.filters` precedent of a shared vocabulary + registry). Rendering
a bundle to text lives beside the other renderers in
:mod:`pydocs_mcp.application.formatting` (``render_pointer_bundle``), next to the
per-action MCP/CLI call forms it reuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ResponseKind(StrEnum):
    """The response shapes that can carry a pointer bundle.

    One member per row of the table. The values are the YAML row keys, so a
    misspelled key is rejected at startup rather than silently ignored.
    """

    SEARCH_HIT_CODE = "search_hit_code"
    SEARCH_HIT_PROSE = "search_hit_prose"
    SYMBOL_CARD = "symbol_card"
    OUTLINE = "outline"
    SOURCE = "source"
    REFERENCE_ROW = "reference_row"
    IMPACT_ROW = "impact_row"
    CONTEXT_SKELETON_BLOCK = "context_skeleton_block"
    DECISION = "decision"
    OVERVIEW_MODULE = "overview_module"
    GREP_HIT = "grep_hit"
    READ_CONTINUATION = "read_continuation"


@dataclass(frozen=True, slots=True)
class PointerAction:
    """One follow-up verb in the vocabulary a pointer-table row may name.

    ``token_action`` and ``show`` are the surface-neutral pointer-grammar
    coordinates the renderer hands to ``formatting.pointer_token``; no renderer
    branches on ``name``, so adding a verb costs one
    :func:`register_pointer_action` call plus its call form — never a renderer edit.

    Example: ``PointerAction("callers", "lookup-show", "callers")`` renders
    ``[[next:lookup-show:<target>:callers]]``, which resolves to
    ``get_references(target=..., direction="callers")`` on MCP.
    """

    name: str
    token_action: str
    show: str = ""


_POINTER_ACTIONS: dict[str, PointerAction] = {}


def register_pointer_action(action: PointerAction) -> PointerAction:
    """Add ``action`` to the vocabulary the table's YAML rows may name.

    Called once per verb at import time. The ``read`` verb (issue #277) is one
    such call plus its grammar rendering — nothing in the bundle renderer changes.

    Raises:
        ValueError: when ``action.name`` is already registered, carrying both the
            offending value and the registration that holds the name.
    """
    held = _POINTER_ACTIONS.get(action.name)
    if held is not None:
        raise ValueError(
            f"pointer action {action.name!r} is already registered as {held!r}; "
            "each action name may be registered exactly once"
        )
    _POINTER_ACTIONS[action.name] = action
    return action


def pointer_action(name: str) -> PointerAction:
    """The registered action named ``name``.

    Example: ``pointer_action("outline")`` → ``PointerAction("outline",
    "lookup-show", "tree")``.

    Raises:
        KeyError: naming the offending value and the registered vocabulary.
    """
    action = _POINTER_ACTIONS.get(name)
    if action is None:
        raise KeyError(f"unknown pointer action {name!r}; expected one of {pointer_action_names()}")
    return action


def pointer_action_names() -> tuple[str, ...]:
    """The registered action names, sorted — the vocabulary a YAML row may use."""
    return tuple(sorted(_POINTER_ACTIONS))


def _register_shipped_actions() -> None:
    """Register the verbs whose MCP/CLI call forms the renderers already ship.

    Each entry names the grammar action plus the show word that
    ``formatting._SHOW_TO_TOOL`` / ``formatting._POINTER_RENDERERS`` already
    render, so every shipped row is expressible without a new call form.
    """
    for action in (
        PointerAction("symbol", "lookup"),
        # The one action whose payload is a WINDOW (path, offset, limit) rather
        # than a qualified name, so its token is built by
        # ``formatting.read_pointer_token`` instead of by the shared
        # ``_action_token``: only the renderer knows which lines it elided.
        PointerAction("read", "read"),
        PointerAction("outline", "lookup-show", "tree"),
        PointerAction("source", "lookup-show", "source"),
        PointerAction("callers", "lookup-show", "callers"),
        PointerAction("callees", "lookup-show", "callees"),
        PointerAction("inherits", "lookup-show", "inherits"),
        PointerAction("impact", "lookup-show", "impact"),
        PointerAction("context", "lookup-show", "context"),
        PointerAction("search", "search"),
        PointerAction("overview", "overview"),
        PointerAction("why", "why"),
    ):
        register_pointer_action(action)


_register_shipped_actions()


class PointerTableRow(BaseModel):
    """One response kind's bundle: the independent follow-ups, then the dependent ones.

    Both groups name actions from :func:`pointer_action_names`; the target each
    pointer aims at is supplied by the renderer, which alone knows the qualified
    name / path the response just rendered.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    together: tuple[str, ...] = ()
    then: tuple[str, ...] = ()


# The row a renderer gets for a kind the loaded table does not list: no pointers,
# never a KeyError mid-response.
_EMPTY_ROW = PointerTableRow()

# Single source of truth for the batch bounds (CONTEXT.md "batch call"): at this
# many same-tool follow-ups a renderer collapses them into one batch call, and
# it never names more than the maximum.
_DEFAULT_BATCH_THRESHOLD = 3
_DEFAULT_BATCH_MAX = 8

# Single source of truth for the read window a grep hit hands over (ADR 0023
# Decision (c)): how many lines the advertised read covers, and how far above
# the matching line it starts so the agent also sees what leads up to it.
_DEFAULT_READ_WINDOW = 40
_GREP_MATCH_LEAD_LINES = 10


def shipped_pointer_rows() -> dict[ResponseKind, PointerTableRow]:
    """The shipped table — one row per response kind.

    ``default_config.yaml`` restates these rows for user-facing clarity (the
    CLAUDE.md YAML exemption to the single-source rule); a parity test pins the
    two against each other so they cannot drift.

    ``grep_hit``, ``read_continuation`` and ``source`` are the three
    path-shaped rows: their only follow-up is the ``read`` window, which is
    also the only thing deeper than a source body.
    """
    return {
        ResponseKind.SEARCH_HIT_CODE: PointerTableRow(
            together=("symbol", "callers"), then=("source",)
        ),
        ResponseKind.SEARCH_HIT_PROSE: PointerTableRow(together=("symbol",), then=("source",)),
        ResponseKind.SYMBOL_CARD: PointerTableRow(
            together=("outline", "callers", "context"), then=("source",)
        ),
        ResponseKind.OUTLINE: PointerTableRow(together=("source", "callers")),
        ResponseKind.SOURCE: PointerTableRow(together=("read",)),
        ResponseKind.REFERENCE_ROW: PointerTableRow(together=("context",)),
        ResponseKind.IMPACT_ROW: PointerTableRow(together=("context",)),
        ResponseKind.CONTEXT_SKELETON_BLOCK: PointerTableRow(together=("source",)),
        ResponseKind.DECISION: PointerTableRow(together=("symbol",)),
        ResponseKind.OVERVIEW_MODULE: PointerTableRow(together=("outline",)),
        ResponseKind.GREP_HIT: PointerTableRow(together=("read",)),
        ResponseKind.READ_CONTINUATION: PointerTableRow(together=("read",)),
    }


def _reject_unknown_action(kind: ResponseKind, row: PointerTableRow) -> None:
    """Raise on the first action ``row`` names that no verb is registered for.

    The message carries the offending row, its group and the expected
    vocabulary, so a malformed table fails at startup with an actionable error.
    """
    named = (
        (group, name)
        for group, names in (("together", row.together), ("then", row.then))
        for name in names
    )
    unknown = [pair for pair in named if pair[1] not in _POINTER_ACTIONS]
    if not unknown:
        return
    group, name = unknown[0]
    raise ValueError(
        f"pointer table row {kind.value!r} group {group!r} names unknown pointer "
        f"action {name!r}; expected a list of {pointer_action_names()}"
    )


class PointerTableConfig(BaseModel):
    """``output.pointers`` — the pointer table plus the batch bounds it sets.

    Deployment-time tuning, never an MCP parameter (CLAUDE.md §"MCP API surface
    vs YAML configuration"): which follow-ups a response offers is server
    behavior an A/B run can measure, not a per-request corpus selector.
    """

    model_config = ConfigDict(extra="forbid")

    # WORKAROUND: the expand-step compatibility gate of issue #269's
    # expand–migrate–contract sequence. While it is false every renderer keeps
    # its hardcoded action, so the shipped defaults change no response bytes;
    # issues #275/#276/#277 move one renderer at a time behind it. Issue #278
    # deletes this flag, ``bundle_row``'s ``None`` branch, and every legacy
    # branch that reads it — the table then becomes the only source of pointers.
    bundles_enabled: bool = False
    batch_threshold: int = Field(default=_DEFAULT_BATCH_THRESHOLD, ge=1)
    batch_max: int = Field(default=_DEFAULT_BATCH_MAX, ge=1)
    read_window: int = Field(default=_DEFAULT_READ_WINDOW, ge=1)
    table: dict[ResponseKind, PointerTableRow] = Field(default_factory=shipped_pointer_rows)

    @field_validator("table", mode="before")
    @classmethod
    def _rows_are_keyed_by_response_kind(cls, value: object) -> object:
        """Fail on a row key that is not a response kind, naming the offending row."""
        if not isinstance(value, dict):
            return value
        known = tuple(kind.value for kind in ResponseKind)
        unknown = [str(key) for key in value if str(key) not in known]
        if unknown:
            raise ValueError(
                f"pointer table row {unknown[0]!r} is not a response kind; expected one of {known}"
            )
        return value

    @model_validator(mode="after")
    def _rows_name_registered_actions(self) -> PointerTableConfig:
        for kind, row in self.table.items():
            _reject_unknown_action(kind, row)
        return self

    @model_validator(mode="after")
    def _threshold_within_max(self) -> PointerTableConfig:
        # Same guard shape as SearchOutputConfig._default_le_max: a threshold
        # above the maximum would make a batch call fire and then be capped
        # below the count that triggered it.
        if self.batch_threshold > self.batch_max:
            raise ValueError(
                f"output.pointers.batch_threshold={self.batch_threshold} > "
                f"batch_max={self.batch_max}; a batch call could never name "
                "as many targets as it takes to trigger one. Adjust YAML.",
            )
        return self

    def row_for(self, kind: ResponseKind) -> PointerTableRow:
        """This kind's row, whatever the migration gate says.

        The gate below preserves a renderer's HARDCODED pointer while the
        table takes over from it. The three path-shaped kinds (``grep_hit``,
        ``read_continuation``, ``source``) never had one — the ``read`` action
        is new in issue #277 and no other renderer can emit it — so they read
        their row here and the table alone decides whether the window is
        offered. Clearing the row in YAML is the off-switch.

        Example: ``config.output.pointers.row_for(ResponseKind.GREP_HIT)``.
        """
        return self.table.get(kind, _EMPTY_ROW)

    def read_window_for_match(self, match_line: int, *, last_line: int) -> tuple[int, int]:
        """``(offset, limit)`` a grep hit's read pointer advertises for ``match_line``.

        The window starts ``_GREP_MATCH_LEAD_LINES`` above the match so the
        agent also gets what leads up to it, never before line 1 and never
        past ``last_line`` — an exact window keeps the advertised call honest
        and lets the caller see when it has already rendered all of it. The
        lead never pushes the match out of a narrow ``read_window``.

        Example: ``PointerTableConfig().read_window_for_match(118, last_line=1000)``
        → ``(108, 40)``.
        """
        lead = min(_GREP_MATCH_LEAD_LINES, self.read_window - 1)
        offset = max(1, match_line - lead)
        return offset, max(1, min(self.read_window, last_line - offset + 1))

    def bundle_row(self, kind: ResponseKind) -> PointerTableRow | None:
        """This kind's bundle row, or ``None`` while the migration gate is shut.

        A renderer that gets ``None`` keeps its hardcoded pointer action.

        WORKAROUND: issue #278 deletes ``bundles_enabled`` and this ``None``
        branch together with the legacy branches that read it; the body then
        becomes an unconditional ``self.table.get(kind, _EMPTY_ROW)``.

        Example: ``config.output.pointers.bundle_row(ResponseKind.OVERVIEW_MODULE)``.
        """
        if not self.bundles_enabled:
            return None
        return self.row_for(kind)
