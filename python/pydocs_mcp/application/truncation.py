"""Per-response truncation ledger (spec §D7).

The acceptance rule: no renderer may drop content without registering an
entry, and every entry renders a recovery pointer. The ledger is scoped to
ONE response via a ContextVar (the ``_sqlite_transaction`` precedent) so
concurrent MCP tool calls never interleave records — the same shared-mutable-
state hazard CLAUDE.md documents for ``RetrieverState.scratch``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TruncationEntry:
    """One elision: human description + a recovery pointer token (§D5 syntax)."""

    description: str
    recovery: str


@dataclass(slots=True)
class TruncationLedger:
    """Accumulates the elisions of one response. Mutable by design, one per scope."""

    _entries: list[TruncationEntry] = field(default_factory=list)

    def record(self, entry: TruncationEntry) -> None:
        self._entries.append(entry)

    @property
    def entries(self) -> tuple[TruncationEntry, ...]:
        return tuple(self._entries)


_active_ledger: ContextVar[TruncationLedger | None] = ContextVar("_active_ledger", default=None)


def get_active_ledger() -> TruncationLedger | None:
    """The ledger of the response currently being rendered, if any."""
    return _active_ledger.get()


@contextmanager
def ledger_scope():
    """Open a fresh ledger for one response; restore the outer one on exit."""
    ledger = TruncationLedger()
    token = _active_ledger.set(ledger)
    try:
        yield ledger
    finally:
        _active_ledger.reset(token)
