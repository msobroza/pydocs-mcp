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
    assert resolve_pointers(text, "mcp") == 'hit\n→ get_symbol(target="pkg.mod.X")\n'


def test_resolve_cli_syntax() -> None:
    text = "hit\n[[next:lookup:pkg.mod.X]]\n"
    assert resolve_pointers(text, "cli") == "hit\n→ pydocs-mcp symbol pkg.mod.X\n"


def test_resolve_show_variants_map_to_new_tools() -> None:
    assert resolve_pointers("[[next:lookup-show:pkg.mod.X:callers]]", "mcp") == (
        '→ get_references(target="pkg.mod.X", direction="callers")'
    )
    assert resolve_pointers("[[next:lookup-show:pkg.mod.X:impact]]", "cli") == (
        "→ pydocs-mcp refs pkg.mod.X --direction impact"
    )
    assert resolve_pointers("[[next:lookup-show:pkg.mod.X:context]]", "mcp") == (
        '→ get_context(targets=["pkg.mod.X"])'
    )
    assert resolve_pointers("[[next:lookup-show:pkg.mod.X:tree]]", "mcp") == (
        '→ get_symbol(target="pkg.mod.X", depth="tree")'
    )


def test_search_action_token() -> None:
    assert pointer_token("search", "retry logic") == "[[next:search:retry logic]]"
    assert resolve_pointers("[[next:search:retry logic]]", "mcp") == (
        '→ search_codebase(query="retry logic")'
    )
    assert resolve_pointers("[[next:search:retry logic]]", "cli") == (
        '→ pydocs-mcp search "retry logic"'
    )


def test_strip_restores_pre_pointer_bytes() -> None:
    with_token = "## T\nbody\n[[next:lookup:pkg.mod.X]]\n"
    assert strip_pointers(with_token) == "## T\nbody\n"
