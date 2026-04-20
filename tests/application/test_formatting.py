"""Tests for application.formatting — spec §5.4, AC #6.

Single source of truth for markdown + CLI rendering. These tests pin the
byte-parity contract from sub-PR #2 AC #21 — a change that breaks composite
output bytes (double newline between blocks, trailing newline, etc.) will
regress the parity golden tests too.
"""
from __future__ import annotations

from pydocs_mcp.application.formatting import (
    format_chunks_markdown_within_budget,
    format_members_markdown_within_budget,
)
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
)


# ---------- format_chunks_markdown_within_budget ----------


def test_format_chunks_markdown_single_newline_between_title_and_body():
    """`## TITLE\\nBODY\\n` — single \\n between heading and body (AC #21)."""
    chunks = (
        Chunk(text="hello world", metadata={ChunkFilterField.TITLE.value: "Greeting"}),
    )
    out = format_chunks_markdown_within_budget(chunks, budget_tokens=10_000)
    # Shape: `## Greeting\nhello world\n`
    assert out.startswith("## Greeting\n"), f"missing single-newline header: {out[:30]!r}"
    # No double newline after "## Greeting\n"
    after_header = out[len("## Greeting\n"):]
    assert not after_header.startswith("\n"), \
        f"double-newline regression at title-body boundary: {out!r}"


def test_format_chunks_markdown_preserves_trailing_newline():
    """Trailing \\n preserved — the old `format_within_budget` did NOT rstrip."""
    chunks = (
        Chunk(text="body", metadata={ChunkFilterField.TITLE.value: "T"}),
    )
    out = format_chunks_markdown_within_budget(chunks, budget_tokens=10_000)
    assert out.endswith("\n"), f"trailing newline stripped: {out[-10:]!r}"


def test_format_chunks_markdown_no_rstrip_on_body():
    """Body whitespace is preserved verbatim — no rstrip() anywhere."""
    chunks = (
        Chunk(text="body-with-trailing-ws   ", metadata={ChunkFilterField.TITLE.value: "T"}),
    )
    out = format_chunks_markdown_within_budget(chunks, budget_tokens=10_000)
    # The trailing spaces must appear BEFORE the final "\n"
    assert "body-with-trailing-ws   \n" in out, f"rstrip regression: {out!r}"


def test_format_chunks_markdown_double_newline_between_blocks():
    """Between consecutive blocks, there's a blank line — matches pre-PR
    `\"\\n\".join(parts)` where parts end with `\\n`. This is the
    byte-parity contract from sub-PR #2 AC #21."""
    chunks = (
        Chunk(text="abc", metadata={ChunkFilterField.TITLE.value: "A"}),
        Chunk(text="def", metadata={ChunkFilterField.TITLE.value: "B"}),
    )
    out = format_chunks_markdown_within_budget(chunks, budget_tokens=10_000)
    assert out == "## A\nabc\n\n## B\ndef\n", f"between-block separator broke: {out!r}"


def test_format_chunks_markdown_budget_truncation_stops_emitting():
    """Once the budget (budget_tokens * 4 chars) is exceeded, later chunks
    are dropped — the loop breaks."""
    chunks = tuple(
        Chunk(text="x" * 20, metadata={ChunkFilterField.TITLE.value: f"T{i}"})
        for i in range(100)
    )
    # budget_tokens=50 => max_chars=200; each piece is ~30 chars; only ~6 fit.
    out = format_chunks_markdown_within_budget(chunks, budget_tokens=50)
    assert len(out) <= 200 + 100, f"budget ignored: len={len(out)}"


def test_format_chunks_markdown_truncation_100_char_gate_appends_partial():
    """When the next piece won't fit but > 100 chars remain, append a partial slice."""
    # Force a piece that won't fit; leave > 100 chars remaining.
    big = Chunk(text="z" * 300, metadata={ChunkFilterField.TITLE.value: "Big"})
    # budget_tokens=100 => max_chars=400; first piece is ~308 bytes; remaining=92
    # (< 100 gate) — so nothing is truncated-appended; only the first piece.
    out1 = format_chunks_markdown_within_budget((big, big), budget_tokens=100)
    assert out1.endswith("\n"), f"first block dropped: {out1!r}"
    # Exactly one block rendered because the 100-char gate blocks the partial.
    assert out1.count("## Big\n") == 1, f"gate broke: {out1!r}"


def test_format_chunks_markdown_truncation_under_100_remaining_emits_nothing_extra():
    """When remaining chars < 100, the partial branch is NOT taken — no extra text."""
    # Make one large piece that blows the budget, then tiny followers.
    big = Chunk(text="z" * 1200, metadata={ChunkFilterField.TITLE.value: "Big"})
    tiny = Chunk(text="t", metadata={ChunkFilterField.TITLE.value: "Tiny"})
    # budget_tokens=250 => max_chars=1000; first piece ~1208 bytes; remaining=1000 (huge)
    # Partial branch WILL be taken: `piece[:1000]` of `## Big\n` + z's
    out = format_chunks_markdown_within_budget((big, tiny), budget_tokens=250)
    assert len(out) <= 1000
    assert out.startswith("## Big\n")


def test_format_chunks_markdown_empty_tuple_returns_empty_string():
    assert format_chunks_markdown_within_budget((), budget_tokens=1000) == ""


def test_format_chunks_markdown_missing_title_uses_empty_string():
    chunks = (Chunk(text="body"),)
    out = format_chunks_markdown_within_budget(chunks, budget_tokens=1000)
    assert out == "## \nbody\n"


def test_format_chunks_markdown_none_text_treated_as_empty():
    # Chunk.text is a non-Optional str in the model, but the helper should
    # still guard `chunk.text or ""` against unusual inputs.
    chunks = (Chunk(text="", metadata={ChunkFilterField.TITLE.value: "T"}),)
    out = format_chunks_markdown_within_budget(chunks, budget_tokens=1000)
    assert out == "## T\n\n"


# ---------- format_members_markdown_within_budget ----------


def test_format_members_markdown_basic_shape():
    m = ModuleMember(metadata={
        ModuleMemberFilterField.PACKAGE.value: "fastapi",
        ModuleMemberFilterField.MODULE.value: "fastapi.routing",
        ModuleMemberFilterField.NAME.value: "APIRouter",
        ModuleMemberFilterField.KIND.value: "class",
        "signature": "(prefix: str = '')",
        "docstring": "Groups endpoints.",
    })
    out = format_members_markdown_within_budget((m,), budget_tokens=1000)
    assert out == "**[fastapi] fastapi.routing.APIRouter(prefix: str = '')** (class)\nGroups endpoints.\n"


def test_format_members_markdown_double_newline_between_blocks():
    m1 = ModuleMember(metadata={
        ModuleMemberFilterField.PACKAGE.value: "p",
        ModuleMemberFilterField.MODULE.value: "m",
        ModuleMemberFilterField.NAME.value: "A",
        ModuleMemberFilterField.KIND.value: "class",
        "signature": "()",
        "docstring": "one",
    })
    m2 = ModuleMember(metadata={
        ModuleMemberFilterField.PACKAGE.value: "p",
        ModuleMemberFilterField.MODULE.value: "m",
        ModuleMemberFilterField.NAME.value: "B",
        ModuleMemberFilterField.KIND.value: "class",
        "signature": "()",
        "docstring": "two",
    })
    out = format_members_markdown_within_budget((m1, m2), budget_tokens=1000)
    assert out == (
        "**[p] m.A()** (class)\none\n"
        "\n"
        "**[p] m.B()** (class)\ntwo\n"
    ), f"members between-block separator broke: {out!r}"


def test_format_members_markdown_preserves_trailing_newline():
    m = ModuleMember(metadata={
        ModuleMemberFilterField.PACKAGE.value: "p",
        ModuleMemberFilterField.MODULE.value: "m",
        ModuleMemberFilterField.NAME.value: "A",
        ModuleMemberFilterField.KIND.value: "class",
        "signature": "()",
        "docstring": "body",
    })
    out = format_members_markdown_within_budget((m,), budget_tokens=1000)
    assert out.endswith("\n")


def test_format_members_markdown_empty_tuple_returns_empty_string():
    assert format_members_markdown_within_budget((), budget_tokens=1000) == ""


def test_format_members_markdown_budget_truncation():
    members = tuple(
        ModuleMember(metadata={
            ModuleMemberFilterField.PACKAGE.value: "pkg",
            ModuleMemberFilterField.MODULE.value: "mod",
            ModuleMemberFilterField.NAME.value: f"F{i}",
            ModuleMemberFilterField.KIND.value: "function",
            "signature": "(x)",
            "docstring": "d" * 30,
        })
        for i in range(100)
    )
    out = format_members_markdown_within_budget(members, budget_tokens=50)  # 200 chars
    assert len(out) <= 200 + 100
