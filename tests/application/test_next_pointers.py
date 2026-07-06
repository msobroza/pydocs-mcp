"""Next-step pointer tokens + per-surface resolution (spec §D5)."""

from __future__ import annotations

from pydocs_mcp.application.formatting import (
    format_chunks_markdown_within_budget,
    format_members_markdown_within_budget,
    pointer_token,
    resolve_pointers,
    strip_pointers,
)
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
)


def _chunk(title: str, text: str, qualified_name: str = "") -> Chunk:
    # The models expose no ``Chunk.from_metadata`` — mirror the direct
    # constructor tests/application/test_formatting.py uses; qualified_name
    # travels in metadata exactly as the v7 column round-trips it
    # (see storage/sqlite/row_mappers.row_to_chunk).
    metadata: dict[str, object] = {ChunkFilterField.TITLE.value: title}
    if qualified_name:
        metadata["qualified_name"] = qualified_name
    return Chunk(text=text, metadata=metadata)


def test_pointer_token_shape() -> None:
    assert pointer_token("lookup", "pkg.mod.X") == "[[next:lookup:pkg.mod.X]]"


def test_code_backed_chunk_gets_lookup_token() -> None:
    out = format_chunks_markdown_within_budget(
        (_chunk("T", "body", qualified_name="pkg.mod.X"),),
        budget_tokens=500,
    )
    assert "[[next:lookup:pkg.mod.X]]" in out


def test_prose_chunk_gets_no_token() -> None:
    out = format_chunks_markdown_within_budget(
        (_chunk("README", "prose"),),
        budget_tokens=500,
    )
    assert "[[next:" not in out


def test_member_gets_lookup_token_from_module_dot_name() -> None:
    member = ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: "pkg",
            ModuleMemberFilterField.MODULE.value: "pkg.mod",
            ModuleMemberFilterField.NAME.value: "X",
            ModuleMemberFilterField.KIND.value: "class",
            "signature": "()",
            "docstring": "d",
        }
    )
    out = format_members_markdown_within_budget((member,), budget_tokens=500)
    assert "[[next:lookup:pkg.mod.X]]" in out


def test_resolve_mcp_syntax() -> None:
    text = "hit\n[[next:lookup:pkg.mod.X]]\n"
    assert resolve_pointers(text, "mcp") == 'hit\n→ lookup(target="pkg.mod.X")\n'


def test_resolve_cli_syntax() -> None:
    text = "hit\n[[next:lookup:pkg.mod.X]]\n"
    assert resolve_pointers(text, "cli") == "hit\n→ pydocs-mcp lookup pkg.mod.X\n"


def test_resolve_show_variant() -> None:
    text = "[[next:lookup-show:pkg.mod.X:callers]]"
    assert resolve_pointers(text, "mcp") == '→ lookup(target="pkg.mod.X", show="callers")'
    assert resolve_pointers(text, "cli") == "→ pydocs-mcp lookup pkg.mod.X --show callers"


def test_strip_restores_pre_pointer_bytes() -> None:
    with_token = "## T\nbody\n[[next:lookup:pkg.mod.X]]\n"
    assert strip_pointers(with_token) == "## T\nbody\n"
