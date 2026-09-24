"""The ``branch`` selector, resolved once per request (spec §6.4, §6.5b, §6.11; #311).

Empty means the checked-out branch when it is indexed, else the bundle's
default branch plus a suggestion to index the checkout; a name means that
branch (a retired one raises the §6.8a message); a 7–40 hex string means a
landing unit, by full sha then unique prefix. A branch literally named like
hex wins over the landing lookup. Pure over a :class:`BranchSnapshot` — the
directory did the only I/O.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydocs_mcp.application.branch_directory import BranchSnapshot, is_selectable_branch_row
from pydocs_mcp.application.branch_manifest import SHORT_SHA_LEN
from pydocs_mcp.application.branch_retirement import retired_branch_message
from pydocs_mcp.application.mcp_errors import InvalidArgumentError
from pydocs_mcp.application.suggestions import checkout_not_indexed_suggestion
from pydocs_mcp.models import NON_GIT_BRANCH_NAME, BranchStatus
from pydocs_mcp.storage.branch_records import BranchRecord

# §6.4 / §6.5b: a landing selector is seven to forty lowercase hex digits.
_HEX_SELECTOR = re.compile(r"^[0-9a-f]{7,40}$")


class BranchSelectorKind(StrEnum):
    """How a request named its branch: not at all, by name, or by landing sha."""

    DEFAULT = "default"
    NAME = "name"
    LANDING_SHA = "landing_sha"


@dataclass(frozen=True, slots=True)
class ResolvedBranch:
    """The branch one request answers from, with its read-time facts."""

    name: str
    record: BranchRecord | None
    kind: BranchSelectorKind
    live_head: str | None = None
    suggestion: str | None = None

    @property
    def is_default_selector(self) -> bool:
        """The request named no branch (``branch=""``)."""
        return self.kind is BranchSelectorKind.DEFAULT

    @property
    def is_landing_unit(self) -> bool:
        return self.record is not None and self.record.is_landing_unit

    @property
    def own_heads(self) -> tuple[str | None, str | None]:
        """The branch's own ``(indexed, live)`` pair (§6.5c): its row's head and
        its ``refs/heads`` sha now. A landing unit has no live ref (§6.5b) — its
        pair is a historical fact, so the live side is always unknown."""
        if self.record is None:
            return None, None
        live = None if self.is_landing_unit else self.live_head
        return self.record.head_sha or None, live or None

    @property
    def index_stale(self) -> bool:
        """Stale only when both own heads are known and differ — so never for
        a landing unit."""
        indexed, live = self.own_heads
        return bool(indexed and live and indexed != live)

    @property
    def meta_name(self) -> str | None:
        """``meta.branch``: null for the null resolution and the non-git
        placeholder row (contract §2.4 — "not a git repository")."""
        return None if self.name in ("", NON_GIT_BRANCH_NAME) else self.name


NULL_RESOLUTION = ResolvedBranch("", None, BranchSelectorKind.DEFAULT)

# The §6.11 unknown-sha error names at most this many landings, newest first:
# the window holds up to ``max_landings`` units, and an error stays one line.
_LANDINGS_NAMED_IN_ERROR = 10


def _resolution_from_record(
    record: BranchRecord,
    kind: BranchSelectorKind,
    snapshot: BranchSnapshot,
    suggestion: str | None = None,
) -> ResolvedBranch:
    return ResolvedBranch(
        record.name, record, kind, snapshot.live_heads.get(record.name), suggestion
    )


def _resolve_default(snapshot: BranchSnapshot) -> ResolvedBranch:
    live = snapshot.live_branch
    selectable = {r.name: r for r in snapshot.selectable_rows()}
    if live is not None and live in selectable:
        return _resolution_from_record(selectable[live], BranchSelectorKind.DEFAULT, snapshot)
    default = next((r for r in snapshot.branch_rows() if r.name == snapshot.default_name), None)
    if default is None:
        # No branch rows at all (never indexed under v16+): meta.branch stays null.
        return NULL_RESOLUTION
    suggestion = None
    if live and live != default.name:
        # §6.11: the window between a checkout and the reindex of that branch.
        suggestion = checkout_not_indexed_suggestion(live)
    return _resolution_from_record(default, BranchSelectorKind.DEFAULT, snapshot, suggestion)


def _by_name(selector: str, snapshot: BranchSnapshot) -> ResolvedBranch | None:
    if selector == NON_GIT_BRANCH_NAME:
        # The non-git placeholder is no branch (meta_name hides it too): naming
        # it is an unknown branch, never a selection (#311).
        return None
    record = next((r for r in snapshot.branch_rows() if r.name == selector), None)
    if record is None:
        return None
    if not is_selectable_branch_row(record):
        raise InvalidArgumentError(retired_branch_message(record))
    return _resolution_from_record(record, BranchSelectorKind.NAME, snapshot)


def _short_landing_shas(units: Sequence[BranchRecord]) -> list[str]:
    return [unit.name[:SHORT_SHA_LEN] for unit in units]


def _landings_in_the_window(units: Sequence[BranchRecord]) -> str:
    """The §6.11 list: ACTIVE units only, newest first, capped.

    A unit collected out of the window keeps its tombstone row forever
    (``INACTIVE``, ``retired_at`` stamped — §6.5b), so it is never listed.
    """
    window = sorted(
        (u for u in units if u.status == BranchStatus.ACTIVE),
        key=lambda u: (u.landed_at or 0.0, u.name),
        reverse=True,
    )
    named = _short_landing_shas(window[:_LANDINGS_NAMED_IN_ERROR])
    rest = len(window) - len(named)
    return f"{named} (+{rest} more)" if rest else str(named)


def _by_landing_sha(selector: str, snapshot: BranchSnapshot) -> ResolvedBranch:
    # A unit #316 created at a MERGED transition is KNOWN even before P2 gives
    # it a DIFF slice: it resolves here, and the tools split on it (#315).
    units = snapshot.landing_units()
    exact = [u for u in units if u.name == selector]
    matches = exact or [u for u in units if u.name.startswith(selector)]
    if len(matches) == 1:
        return _resolution_from_record(matches[0], BranchSelectorKind.LANDING_SHA, snapshot)
    if matches:
        raise InvalidArgumentError(
            f"{selector!r} matches {len(matches)} landing units: "
            f"{_short_landing_shas(matches)}; give more digits"
        )
    raise InvalidArgumentError(
        f"no branch or landing unit matches {selector!r}; "
        f"landings in the window: {_landings_in_the_window(units)}"
    )


def _unknown_branch_error(selector: str, snapshot: BranchSnapshot) -> InvalidArgumentError:
    if snapshot.default_name == NON_GIT_BRANCH_NAME:
        # A project outside git has no branch to list and no ``--branch`` to index.
        return InvalidArgumentError(
            f"no indexed branch {selector!r}; the project is not a git repository, "
            "so it has no branches to select"
        )
    # "indexed" lists what a name can select: a retired row raises its own
    # message and a landing unit is not a branch (§6.11). A detached row is a
    # branch (spec §2), so it is listed.
    indexed = sorted(r.name for r in snapshot.selectable_rows() if r.name != NON_GIT_BRANCH_NAME)
    return InvalidArgumentError(
        f"no indexed branch {selector!r}; indexed: {indexed}; "
        f"run pydocs-mcp index . --branch {selector}"
    )


def resolve_branch_selector(selector: str, snapshot: BranchSnapshot) -> ResolvedBranch:
    """Empty → live branch else default (+ suggestion); a name; then a landing sha."""
    if not selector:
        return _resolve_default(snapshot)
    named = _by_name(selector, snapshot)
    if named is not None:
        return named
    if _HEX_SELECTOR.match(selector):
        return _by_landing_sha(selector, snapshot)
    raise _unknown_branch_error(selector, snapshot)


def landing_unit_suggestion(sha7: str) -> str:
    """§6.5b: ``meta.suggestion`` of search_codebase / grep on a landing unit
    outside ``scope=diff`` (the tool split lands with the parameter, #315)."""
    return f"[suggestion: landing unit {sha7} has no tree; use scope=diff or name a branch]"


def landing_unit_error(sha7: str) -> InvalidArgumentError:
    """§6.5b / O17: the six tools without a suggestion field raise this instead."""
    return InvalidArgumentError(
        f"'{sha7}' is a landing unit and has no tree; use search_codebase or grep with "
        "scope=diff, or name a branch"
    )


__all__ = (
    "NULL_RESOLUTION",
    "BranchSelectorKind",
    "ResolvedBranch",
    "landing_unit_error",
    "landing_unit_suggestion",
    "resolve_branch_selector",
)
