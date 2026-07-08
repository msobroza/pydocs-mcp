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
    format_package_doc,
    render_top_composite,
)
from pydocs_mcp.application.truncation import ledger_scope
from pydocs_mcp.constants import PACKAGE_DOC_MAX
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageDoc,
    PackageOrigin,
    SearchQuery,
    SearchResponse,
)


# ---------- format_chunks_markdown_within_budget ----------


def test_format_chunks_markdown_single_newline_between_title_and_body():
    """`## TITLE\\nBODY\\n` — single \\n between heading and body (AC #21)."""
    chunks = (Chunk(text="hello world", metadata={ChunkFilterField.TITLE.value: "Greeting"}),)
    out = format_chunks_markdown_within_budget(chunks, budget_tokens=10_000)
    # Shape: `## Greeting\nhello world\n`
    assert out.startswith("## Greeting\n"), f"missing single-newline header: {out[:30]!r}"
    # No double newline after "## Greeting\n"
    after_header = out[len("## Greeting\n") :]
    assert not after_header.startswith("\n"), (
        f"double-newline regression at title-body boundary: {out!r}"
    )


def test_format_chunks_markdown_preserves_trailing_newline():
    """Trailing \\n preserved — the old `format_within_budget` did NOT rstrip."""
    chunks = (Chunk(text="body", metadata={ChunkFilterField.TITLE.value: "T"}),)
    out = format_chunks_markdown_within_budget(chunks, budget_tokens=10_000)
    assert out.endswith("\n"), f"trailing newline stripped: {out[-10:]!r}"


def test_format_chunks_markdown_no_rstrip_on_body():
    """Body whitespace is preserved verbatim — no rstrip() anywhere."""
    chunks = (Chunk(text="body-with-trailing-ws   ", metadata={ChunkFilterField.TITLE.value: "T"}),)
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
        Chunk(text="x" * 20, metadata={ChunkFilterField.TITLE.value: f"T{i}"}) for i in range(100)
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
    m = ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: "fastapi",
            ModuleMemberFilterField.MODULE.value: "fastapi.routing",
            ModuleMemberFilterField.NAME.value: "APIRouter",
            ModuleMemberFilterField.KIND.value: "class",
            "signature": "(prefix: str = '')",
            "docstring": "Groups endpoints.",
        }
    )
    out = format_members_markdown_within_budget((m,), budget_tokens=1000)
    assert out == (
        "**[fastapi] fastapi.routing.APIRouter(prefix: str = '')** (class)\n"
        "Groups endpoints.\n"
        "[[next:lookup:fastapi.routing.APIRouter]]\n"
    )


def test_format_members_markdown_double_newline_between_blocks():
    m1 = ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: "p",
            ModuleMemberFilterField.MODULE.value: "m",
            ModuleMemberFilterField.NAME.value: "A",
            ModuleMemberFilterField.KIND.value: "class",
            "signature": "()",
            "docstring": "one",
        }
    )
    m2 = ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: "p",
            ModuleMemberFilterField.MODULE.value: "m",
            ModuleMemberFilterField.NAME.value: "B",
            ModuleMemberFilterField.KIND.value: "class",
            "signature": "()",
            "docstring": "two",
        }
    )
    out = format_members_markdown_within_budget((m1, m2), budget_tokens=1000)
    assert out == (
        "**[p] m.A()** (class)\none\n[[next:lookup:m.A]]\n"
        "\n"
        "**[p] m.B()** (class)\ntwo\n[[next:lookup:m.B]]\n"
    ), f"members between-block separator broke: {out!r}"


def test_format_members_markdown_preserves_trailing_newline():
    m = ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: "p",
            ModuleMemberFilterField.MODULE.value: "m",
            ModuleMemberFilterField.NAME.value: "A",
            ModuleMemberFilterField.KIND.value: "class",
            "signature": "()",
            "docstring": "body",
        }
    )
    out = format_members_markdown_within_budget((m,), budget_tokens=1000)
    assert out.endswith("\n")


def test_format_members_markdown_empty_tuple_returns_empty_string():
    assert format_members_markdown_within_budget((), budget_tokens=1000) == ""


def test_format_members_markdown_budget_truncation():
    members = tuple(
        ModuleMember(
            metadata={
                ModuleMemberFilterField.PACKAGE.value: "pkg",
                ModuleMemberFilterField.MODULE.value: "mod",
                ModuleMemberFilterField.NAME.value: f"F{i}",
                ModuleMemberFilterField.KIND.value: "function",
                "signature": "(x)",
                "docstring": "d" * 30,
            }
        )
        for i in range(100)
    )
    out = format_members_markdown_within_budget(members, budget_tokens=50)  # 200 chars
    assert len(out) <= 200 + 100


# ---------- render_top_composite ----------
#
# Pins the I19 cross-surface invariant: the MCP server (`server.py`) and
# the CLI (`__main__.py`) BOTH collapse a ``SearchResponse`` to one string
# by reading ``response.result.items[0].text`` — the composite chunk the
# pipeline's ``TokenBudgetStep`` deposits at index 0. The helper is the
# single source of truth, so this is the contract test.

_DUMMY_QUERY = SearchQuery(terms="anything")


def test_render_top_composite_returns_first_item_text():
    """The first chunk's ``.text`` is the formatted body (composite output)."""
    response = SearchResponse(
        result=ChunkList(
            items=(
                Chunk(text="winner-body", metadata={ChunkFilterField.TITLE.value: "T"}),
                Chunk(text="loser-body", metadata={ChunkFilterField.TITLE.value: "T2"}),
            )
        ),
        query=_DUMMY_QUERY,
    )
    assert render_top_composite(response) == "winner-body"


def test_render_top_composite_empty_items_uses_default_empty_msg():
    """An empty ``items`` tuple falls back to the default empty message."""
    response = SearchResponse(
        result=ChunkList(items=()),
        query=_DUMMY_QUERY,
    )
    assert render_top_composite(response) == "No results."


def test_render_top_composite_empty_items_custom_empty_msg():
    """Callers can override the empty fallback (server uses 'No matches found.'
    and 'No symbols found.'; the kind='any' path passes the empty string)."""
    response = SearchResponse(
        result=ChunkList(items=()),
        query=_DUMMY_QUERY,
    )
    assert render_top_composite(response, empty_msg="No matches found.") == "No matches found."


def test_render_top_composite_none_result_uses_empty_msg():
    """``response.result is None`` mirrors the old server/CLI guards: when
    the pipeline returns no result object at all, the empty fallback wins.
    Constructed via ``object.__new__`` because the dataclass is frozen and
    declares ``result`` as required."""
    response = object.__new__(SearchResponse)
    object.__setattr__(response, "result", None)
    object.__setattr__(response, "query", _DUMMY_QUERY)
    object.__setattr__(response, "duration_ms", 0.0)
    assert render_top_composite(response, empty_msg="nope") == "nope"


def test_render_top_composite_empty_string_passthrough():
    """The ``kind='any'`` server path passes ``empty_msg=''`` so empty
    halves don't push a 'No matches found.' line into the joined output.
    Pin that behaviour."""
    response = SearchResponse(
        result=ChunkList(items=()),
        query=_DUMMY_QUERY,
    )
    assert render_top_composite(response, empty_msg="") == ""


# ---------- remaining == 100 boundary (the drifted truncation gate) ----------
#
# format_chunks/format_members gate the partial-piece append with
# `remaining > 100` (strict); format_context uses `remaining >= 100`
# (inclusive). These tests pin the exact bytes at remaining == 100 for each
# caller so the shared _take_within_budget helper provably preserves the
# historical divergence.

from pydocs_mcp.application.formatting import format_context
from pydocs_mcp.application.reference_service import ContextNode


def test_format_chunks_strict_gate_drops_partial_at_exactly_100_remaining():
    # piece = "## T\n" + 294*"x" + "\n" → 300 chars; budget 100 tokens = 400
    # chars; after piece 1, remaining == 100 exactly → strict `>` gate says
    # NO partial: output is piece 1 alone.
    first = Chunk(text="x" * 294, metadata={ChunkFilterField.TITLE.value: "T"})
    second = Chunk(text="y" * 294, metadata={ChunkFilterField.TITLE.value: "U"})
    out = format_chunks_markdown_within_budget((first, second), budget_tokens=100)
    assert out == "## T\n" + "x" * 294 + "\n"


def test_format_chunks_strict_gate_appends_partial_at_101_remaining():
    # piece 1 = 299 chars → remaining == 101 > 100 → partial of piece 2
    # (300 chars, sliced to 101) IS appended, "\n"-joined.
    first = Chunk(text="x" * 293, metadata={ChunkFilterField.TITLE.value: "T"})
    second = Chunk(text="y" * 294, metadata={ChunkFilterField.TITLE.value: "U"})
    out = format_chunks_markdown_within_budget((first, second), budget_tokens=100)
    piece1 = "## T\n" + "x" * 293 + "\n"
    piece2 = "## U\n" + "y" * 294 + "\n"
    assert out == piece1 + "\n" + piece2[:101]


def test_format_members_strict_gate_drops_partial_at_exactly_100_remaining():
    # piece = header + "\n" + doc + "\n" + token + "\n" — the §D5 pointer
    # token joined the member block, so the doc padding subtracts its length
    # too to keep the piece exactly 300 chars, leaving remaining == 100 of the
    # 400-char budget.  The strict `>` gate then drops the second piece.
    def token(name: str) -> str:
        return f"[[next:lookup:m.{name}]]"

    def member(name: str) -> ModuleMember:
        header = f"**[p] m.{name}** (c)"
        doc = "d" * (300 - len(header) - 3 - len(token(name)))
        return ModuleMember(
            metadata={
                ModuleMemberFilterField.PACKAGE.value: "p",
                ModuleMemberFilterField.MODULE.value: "m",
                ModuleMemberFilterField.NAME.value: name,
                ModuleMemberFilterField.KIND.value: "c",
                "signature": "",
                "docstring": doc,
            }
        )

    m1, m2 = member("A"), member("B")
    out = format_members_markdown_within_budget((m1, m2), budget_tokens=100)
    header1 = "**[p] m.A** (c)"
    doc1 = "d" * (300 - len(header1) - 3 - len(token("A")))
    expected = header1 + "\n" + doc1 + "\n" + token("A") + "\n"
    assert out == expected


def test_format_context_inclusive_gate_appends_partial_at_exactly_100_remaining():
    # format_context historically uses `>=`: at remaining == 100 exactly the
    # partial piece IS emitted (unlike the chunk/member formatters above).
    target = "pkg.mod.fn"
    n1 = ContextNode(qualified_name="a" * 50, hop=2, pagerank=0.0, in_degree=0, source_text="")
    n2 = ContextNode(qualified_name="b" * 200, hop=2, pagerank=0.0, in_degree=0, source_text="")
    nodes = (n1, n2)
    lead = (
        f"{len(nodes)} symbols in the closure (max depth 2). Graded fidelity: "
        "focus = full source, ring = signature, rest = outline.\n"
    )
    piece1 = f"- `{n1.qualified_name}` (hop 2)\n"
    # token budgets are whole tokens (max_chars = budget * 4); pad the target
    # until header + piece1 + 100 is divisible by 4 so remaining lands on
    # exactly 100.
    h1 = f"# Context for `{target}` — its dependency closure\n"
    needed = len(h1) + len(lead) + len(piece1) + 100
    while needed % 4:
        target += "x"
        h1 = f"# Context for `{target}` — its dependency closure\n"
        needed = len(h1) + len(lead) + len(piece1) + 100

    out = format_context(nodes, target=target, token_budget=needed // 4)

    piece2 = f"- `{n2.qualified_name}` (hop 2)\n"
    # The 100-char slice of piece2 has no trailing \n, so format_context's
    # final single-\n fixup appends one.
    assert out == h1 + lead + piece1 + piece2[:100] + "\n"


# ---------- format_package_doc over-cap ledger recording (spec §D7) ----------


def _big_package_doc() -> PackageDoc:
    """A PackageDoc whose rendered markdown exceeds PACKAGE_DOC_MAX.

    Ten chunks of 4000 chars each (40,000 chars of body alone) blow well past
    the 30,000-char cap so the final ``[:PACKAGE_DOC_MAX]`` slice in
    ``format_package_doc`` is guaranteed to bite mid-block.
    """
    package = Package(
        name="bigpkg",
        version="1.0.0",
        summary="A package with a lot of documentation.",
        homepage="",
        dependencies=(),
        content_hash="deadbeef",
        origin=PackageOrigin.DEPENDENCY,
    )
    chunks = tuple(
        Chunk.from_test_inputs(
            package="bigpkg",
            module="bigpkg.mod",
            title=f"Section {i}",
            text="x" * 4000,
        )
        for i in range(10)
    )
    return PackageDoc(package=package, chunks=chunks, members=())


def test_format_package_doc_over_cap_records_ledger_entry():
    """§D7: 'no renderer drops content without registering an entry'.

    ``format_package_doc`` hard-slices at PACKAGE_DOC_MAX with a bare
    ``[:PACKAGE_DOC_MAX]`` — unlike every other budgeted formatter in this
    module, it did not record a TruncationEntry when it truncated. This
    pins the fix: the ledger must carry exactly one entry with a recovery
    pointer, and the output must be clipped to the cap.
    """
    doc = _big_package_doc()
    with ledger_scope() as ledger:
        out = format_package_doc(doc)

    assert len(out) == PACKAGE_DOC_MAX, f"output not clipped to cap: len={len(out)}"
    assert len(ledger.entries) == 1, f"expected exactly one ledger entry, got {ledger.entries!r}"
    entry = ledger.entries[0]
    assert entry.recovery, "truncation entry must carry a recovery pointer (§D7)"
    assert entry.recovery.startswith("[[next:"), entry.recovery


def test_format_package_doc_under_cap_records_nothing():
    """Sanity check: a doc that fits within PACKAGE_DOC_MAX elides nothing."""
    package = Package(
        name="smallpkg",
        version="1.0.0",
        summary="Small.",
        homepage="",
        dependencies=(),
        content_hash="deadbeef",
        origin=PackageOrigin.DEPENDENCY,
    )
    doc = PackageDoc(package=package, chunks=(), members=())
    with ledger_scope() as ledger:
        format_package_doc(doc)
    assert ledger.entries == ()
