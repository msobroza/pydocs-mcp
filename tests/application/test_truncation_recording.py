"""Renderers register every elision on the active ledger (spec §D7 rule)."""

from pydocs_mcp.application.formatting import (
    format_chunks_markdown_within_budget,
    format_references,
)
from pydocs_mcp.application.truncation import ledger_scope
from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.node_reference import NodeReference


def _chunk(i: int) -> Chunk:
    # ``from_metadata`` does not exist on the real model — the qualified_name
    # lookup target lives in metadata, so route it through the test factory.
    return Chunk.from_test_inputs(
        title=f"T{i}",
        text="x" * 400,
        package="pkg",
        module="pkg.mod",
        metadata={"qualified_name": f"pkg.mod.f{i}"},
    )


def test_budget_drop_records_entry_with_recovery() -> None:
    chunks = tuple(_chunk(i) for i in range(10))
    with ledger_scope() as ledger:
        # Budget fits ~2 of 10 pieces: the rest are elided.
        format_chunks_markdown_within_budget(chunks, budget_tokens=200)
    assert len(ledger.entries) == 1
    entry = ledger.entries[0]
    assert "elided" in entry.description
    assert entry.recovery.startswith("[[next:")


def test_no_entry_when_everything_fits() -> None:
    with ledger_scope() as ledger:
        format_chunks_markdown_within_budget((_chunk(0),), budget_tokens=5000)
    assert ledger.entries == ()


def test_no_ledger_active_is_harmless() -> None:
    # Rendering outside a scope (unit tests, pipeline steps) must not raise.
    chunks = tuple(_chunk(i) for i in range(10))
    assert format_chunks_markdown_within_budget(chunks, budget_tokens=200)


def test_references_limit_hit_records_entry() -> None:
    rows = tuple(
        NodeReference(
            from_package="pkg",
            from_node_id=f"pkg.mod.f{i}",
            to_name="pkg.mod.target",
            to_node_id="pkg.mod.target",
            kind="calls",
        )
        for i in range(5)
    )
    with ledger_scope() as ledger:
        format_references(rows, target="pkg.mod.target", show="callers", limit=5)
    assert len(ledger.entries) == 1
    assert "possibly more" in ledger.entries[0].description
    assert ledger.entries[0].recovery == "[[next:lookup-show:pkg.mod.target:callers]]"


def test_references_under_limit_records_nothing() -> None:
    # A single-element tuple literal — ``tuple(NodeReference(...),)`` would try
    # to iterate the frozen dataclass, so build the 1-tuple directly.
    rows = (
        NodeReference(
            from_package="pkg",
            from_node_id="pkg.mod.f0",
            to_name="t",
            to_node_id="t",
            kind="calls",
        ),
    )
    with ledger_scope() as ledger:
        format_references(rows, target="t", show="callers", limit=50)
    assert ledger.entries == ()
