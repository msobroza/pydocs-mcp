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

from collections.abc import Sequence
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
    OVERVIEW_ENTRY_POINT = "overview_entry_point"
    OVERVIEW_DEPENDENCY = "overview_dependency"
    OVERVIEW_DECISIONS = "overview_decisions"
    WORKSPACE_PROJECT = "workspace_project"
    PACKAGE_DOC = "package_doc"
    ZERO_HIT = "zero_hit"


class PointerVerb(StrEnum):
    """The follow-up verbs this repo's renderers name, spelled once.

    The registry below stays the extension point — a deployment-specific verb
    needs no member here — but every verb a renderer names comes from this
    enum, so the table is the only place a verb is spelled, a rename is one
    edit, and ``grep`` finds every site (issue #278). Members are ``str``, so
    they slot in wherever an action name is expected: a table row, a
    ``rendered_depths`` declaration, a ``token_for_action`` call.
    """

    SYMBOL = "symbol"
    READ = "read"
    OUTLINE = "outline"
    SOURCE = "source"
    CALLERS = "callers"
    CALLEES = "callees"
    INHERITS = "inherits"
    IMPACT = "impact"
    GOVERNED_BY = "governed_by"
    CONTEXT = "context"
    SEARCH = "search"
    OVERVIEW = "overview"
    OVERVIEW_PACKAGE = "overview-package"
    WHY = "why"


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

    ``batch`` marks a verb whose call form carries SEVERAL targets at once, so a
    response listing many symbols consolidates one call per row into a single
    **batch call** (CONTEXT.md). ``context`` is the only one: ``get_context`` is
    the one tool in the frozen surface that takes a target list.
    """

    name: str
    token_action: str
    show: str = ""
    batch: bool = False


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
        PointerAction(PointerVerb.SYMBOL, "lookup"),
        # The one action whose payload is a WINDOW (path, offset, limit) rather
        # than a qualified name, so its token is built by
        # ``pointer_bundles.read_pointer_token`` instead of by the shared
        # ``token_for_action``: only the renderer knows which lines it elided.
        PointerAction(PointerVerb.READ, "read"),
        PointerAction(PointerVerb.OUTLINE, "lookup-show", "tree"),
        PointerAction(PointerVerb.SOURCE, "lookup-show", "source"),
        PointerAction(PointerVerb.CALLERS, "lookup-show", "callers"),
        PointerAction(PointerVerb.CALLEES, "lookup-show", "callees"),
        PointerAction(PointerVerb.INHERITS, "lookup-show", "inherits"),
        PointerAction(PointerVerb.IMPACT, "lookup-show", "impact"),
        PointerAction(PointerVerb.GOVERNED_BY, "lookup-show", "governed_by"),
        PointerAction(PointerVerb.CONTEXT, "lookup-show", "context", batch=True),
        PointerAction(PointerVerb.SEARCH, "search"),
        PointerAction(PointerVerb.OVERVIEW, "overview"),
        # The other corpus-scope selector of the same tool: a bare target
        # cannot say which selector it belongs to, so a package name gets its
        # own verb rather than being routed to the PROJECT selector.
        PointerAction(PointerVerb.OVERVIEW_PACKAGE, "overview-package"),
        PointerAction(PointerVerb.WHY, "why"),
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

    ``package_doc`` and ``zero_hit`` are the two rows whose pointer is a
    **recovery pointer** rather than a bundle line — the truncation ledger and
    the empty-result body carry one ready-made call each — so their row names
    the single verb that recovers what the response could not show.
    """
    return {
        ResponseKind.SEARCH_HIT_CODE: PointerTableRow(
            together=(PointerVerb.SYMBOL, PointerVerb.CALLERS), then=(PointerVerb.SOURCE,)
        ),
        ResponseKind.SEARCH_HIT_PROSE: PointerTableRow(
            together=(PointerVerb.SYMBOL,), then=(PointerVerb.SOURCE,)
        ),
        ResponseKind.SYMBOL_CARD: PointerTableRow(
            together=(PointerVerb.OUTLINE, PointerVerb.CALLERS, PointerVerb.CONTEXT),
            then=(PointerVerb.SOURCE,),
        ),
        ResponseKind.OUTLINE: PointerTableRow(together=(PointerVerb.SOURCE, PointerVerb.CALLERS)),
        ResponseKind.SOURCE: PointerTableRow(together=(PointerVerb.READ,)),
        # Two actions, one per fan-out size: below the batch threshold each row
        # keeps the cheapest deepening call there is (the card), and at or above
        # it the batchable ``context`` call replaces the whole fan-out.
        ResponseKind.REFERENCE_ROW: PointerTableRow(
            together=(PointerVerb.SYMBOL, PointerVerb.CONTEXT)
        ),
        ResponseKind.IMPACT_ROW: PointerTableRow(
            together=(PointerVerb.SYMBOL, PointerVerb.CONTEXT)
        ),
        ResponseKind.CONTEXT_SKELETON_BLOCK: PointerTableRow(together=(PointerVerb.SOURCE,)),
        ResponseKind.DECISION: PointerTableRow(together=(PointerVerb.SYMBOL,)),
        ResponseKind.OVERVIEW_MODULE: PointerTableRow(together=(PointerVerb.OUTLINE,)),
        # The orientation card's other rows: an entry point and an indexed
        # dependency each deepen into their own card, the decisions census
        # deepens into the governance surface, and a workspace line opens that
        # project's card.
        ResponseKind.GREP_HIT: PointerTableRow(together=(PointerVerb.READ,)),
        ResponseKind.READ_CONTINUATION: PointerTableRow(together=(PointerVerb.READ,)),
        ResponseKind.OVERVIEW_ENTRY_POINT: PointerTableRow(together=(PointerVerb.SYMBOL,)),
        ResponseKind.OVERVIEW_DEPENDENCY: PointerTableRow(together=(PointerVerb.SYMBOL,)),
        ResponseKind.OVERVIEW_DECISIONS: PointerTableRow(together=(PointerVerb.WHY,)),
        ResponseKind.WORKSPACE_PROJECT: PointerTableRow(together=(PointerVerb.OVERVIEW,)),
        ResponseKind.PACKAGE_DOC: PointerTableRow(together=(PointerVerb.OVERVIEW_PACKAGE,)),
        ResponseKind.ZERO_HIT: PointerTableRow(together=(PointerVerb.OVERVIEW,)),
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


# The staged rollout's migration gate, removed once every renderer read the
# table. Named here so the loader can say what replaced it (see the validator).
_REMOVED_GATE_KEY = "bundles_enabled"


class PointerTableConfig(BaseModel):
    """``output.pointers`` — the pointer table plus the batch bounds it sets.

    Deployment-time tuning, never an MCP parameter (CLAUDE.md §"MCP API surface
    vs YAML configuration"): which follow-ups a response offers is server
    behavior an A/B run can measure, not a per-request corpus selector.
    """

    model_config = ConfigDict(extra="forbid")

    batch_threshold: int = Field(default=_DEFAULT_BATCH_THRESHOLD, ge=1)
    batch_max: int = Field(default=_DEFAULT_BATCH_MAX, ge=1)
    read_window: int = Field(default=_DEFAULT_READ_WINDOW, ge=1)
    table: dict[ResponseKind, PointerTableRow] = Field(default_factory=shipped_pointer_rows)

    @model_validator(mode="before")
    @classmethod
    def _reject_the_removed_migration_gate(cls, value: object) -> object:
        """Name ``bundles_enabled`` as REMOVED rather than as an unknown key.

        It was the staged rollout's gate: while it stood, a renderer that had
        not moved onto the table kept its own hardcoded follow-up. Every
        renderer reads the table now, so the flag has nothing left to switch —
        and ``extra="forbid"`` alone would tell a deployment that still sets it
        only that the key is unknown, not what replaced it.
        """
        if not isinstance(value, dict) or _REMOVED_GATE_KEY not in value:
            return value
        raise ValueError(
            f"output.pointers.{_REMOVED_GATE_KEY} was removed: the pointer table is now the "
            "only source of follow-up pointers. Drop the key; to take a response kind's "
            "follow-ups away, clear that row under output.pointers.table instead."
        )

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
        """This kind's row — the ONLY source of the follow-ups it may offer.

        A kind the loaded table does not list offers nothing, so clearing a row
        in YAML is that response kind's off-switch.

        Example: ``config.output.pointers.row_for(ResponseKind.GREP_HIT)``.
        """
        return self.table.get(kind, _EMPTY_ROW)

    def consolidates(self, target_count: int) -> bool:
        """Whether ``target_count`` same-tool follow-ups collapse into one batch call.

        Example: with the shipped threshold of 3, ``consolidates(2)`` is False
        (two rows are cheaper to follow one at a time) and ``consolidates(3)``
        is True.
        """
        return target_count >= self.batch_threshold

    def batch_targets(self, targets: Sequence[str]) -> tuple[str, ...]:
        """The targets one pointer line names — at most ``batch_max`` of them.

        ``batch_max`` is the table-level ceiling ADR 0023 (d) fixes for how many
        targets one follow-up line may name, whether that line is a single batch
        call (a reference page) or one call per target (a decision naming the
        symbols it governs). The ceiling is also what lets a response state how
        many of the rows it just listed the call leaves out.

        Example: ``PointerTableConfig().batch_targets(("a",) * 10)`` → the first
        eight.
        """
        return tuple(targets[: self.batch_max])

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
