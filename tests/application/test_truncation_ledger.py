"""Per-response TruncationLedger — ContextVar-scoped (spec §D7)."""

import asyncio

from pydocs_mcp.application.truncation import (
    TruncationEntry,
    get_active_ledger,
    ledger_scope,
)


def test_no_active_ledger_outside_scope() -> None:
    assert get_active_ledger() is None


def test_entries_recorded_inside_scope() -> None:
    with ledger_scope() as ledger:
        active = get_active_ledger()
        assert active is ledger
        active.record(
            TruncationEntry(description="2 results elided", recovery="[[next:lookup:pkg.mod]]")
        )
    assert len(ledger.entries) == 1
    assert get_active_ledger() is None


def test_nested_scopes_do_not_leak() -> None:
    with ledger_scope() as outer:
        with ledger_scope() as inner:
            get_active_ledger().record(TruncationEntry(description="inner", recovery="r"))
        assert get_active_ledger() is outer
    assert [e.description for e in inner.entries] == ["inner"]
    assert outer.entries == ()


def test_concurrent_responses_have_disjoint_ledgers() -> None:
    """Two concurrent tool calls must never share ledger entries (spec §D7)."""

    async def one_response(tag: str) -> tuple[str, ...]:
        with ledger_scope() as ledger:
            await asyncio.sleep(0)  # force interleaving
            get_active_ledger().record(TruncationEntry(description=tag, recovery="r"))
            await asyncio.sleep(0)
            get_active_ledger().record(TruncationEntry(description=tag, recovery="r"))
        return tuple(e.description for e in ledger.entries)

    async def main():
        return await asyncio.gather(one_response("A"), one_response("B"))

    got_a, got_b = asyncio.run(main())
    assert got_a == ("A", "A")
    assert got_b == ("B", "B")
