"""The budgeted chunk renderer's optional facts mapping (issue #393, ADR 0023 (j)).

A hit the caller hands facts for carries its ``Members (N): …`` line between
its header and its body; an empty mapping renders every block byte-for-byte
as the renderer did before facts existed.
"""

from __future__ import annotations

from pydocs_mcp.application.formatting import format_chunks_markdown_within_budget
from pydocs_mcp.application.search_hit_facts import SearchHitFacts
from pydocs_mcp.models import Chunk, ChunkFilterField
from pydocs_mcp.pointer_table import PointerTableConfig

_SHIPPED = PointerTableConfig()
_BOX_MEMBERS = SearchHitFacts(members_line="Members (2): open, close")


def _located_chunk(qualified_name: str, span: tuple[int, int]) -> Chunk:
    return Chunk(
        text="body",
        metadata={
            "qualified_name": qualified_name,
            ChunkFilterField.TITLE.value: qualified_name,
            ChunkFilterField.SOURCE_PATH.value: "pkg/mod.py",
            ChunkFilterField.START_LINE.value: span[0],
            ChunkFilterField.END_LINE.value: span[1],
        },
    )


_RUN = _located_chunk("pkg.mod.run", (10, 42))
_BOX = _located_chunk("pkg.mod.Box", (50, 60))


def test_a_hit_with_facts_names_its_members_between_the_header_and_the_body() -> None:
    out = format_chunks_markdown_within_budget(
        (_RUN, _BOX), budget_tokens=1000, pointers=_SHIPPED, facts={1: _BOX_MEMBERS}
    )
    assert "## pkg.mod.Box — pkg/mod.py:50-60\nMembers (2): open, close\nbody\n" in out
    assert out.startswith("## pkg.mod.run — pkg/mod.py:10-42\nbody\n")


def test_the_members_line_is_the_only_byte_a_fact_adds() -> None:
    """Same bundle, same separators, same budget arithmetic for everything else."""
    plain = format_chunks_markdown_within_budget((_RUN, _BOX), 1000, pointers=_SHIPPED)
    with_facts = format_chunks_markdown_within_budget(
        (_RUN, _BOX), 1000, pointers=_SHIPPED, facts={1: _BOX_MEMBERS}
    )
    assert with_facts.replace("Members (2): open, close\n", "", 1) == plain


def test_an_empty_facts_mapping_renders_the_blocks_byte_for_byte() -> None:
    hits = (Chunk(text="abc", metadata={ChunkFilterField.TITLE.value: "A"}), _RUN)
    out = format_chunks_markdown_within_budget(hits, 1000, pointers=_SHIPPED, facts={})
    assert out == (
        "## A\nabc\n\n"
        "## pkg.mod.run — pkg/mod.py:10-42\nbody\n"
        "Together: [[next:lookup:pkg.mod.run]] [[next:lookup-show:pkg.mod.run:callers]]\n"
        "Then: [[next:lookup-show:pkg.mod.run:source]]\n"
    )
