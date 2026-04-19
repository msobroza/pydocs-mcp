"""Tests for ChunkMarkdownFormatter + ModuleMemberMarkdownFormatter."""
from __future__ import annotations

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    MemberKind,
    ModuleMember,
    ModuleMemberFilterField,
)
from pydocs_mcp.retrieval.formatters import (
    ChunkMarkdownFormatter,
    ModuleMemberMarkdownFormatter,
)
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.retrieval.serialization import BuildContext, formatter_registry


def test_chunk_markdown_formatter_renders_title_and_text():
    f = ChunkMarkdownFormatter()
    c = Chunk(
        text="body text",
        metadata={ChunkFilterField.TITLE.value: "Hello"},
    )
    assert f.format(c) == "## Hello\n\nbody text"


def test_chunk_markdown_formatter_empty_title_ok():
    f = ChunkMarkdownFormatter()
    c = Chunk(text="body")
    assert f.format(c) == "## \n\nbody"


def test_member_markdown_formatter_renders_fields():
    f = ModuleMemberMarkdownFormatter()
    m = ModuleMember(metadata={
        ModuleMemberFilterField.PACKAGE.value: "fastapi",
        ModuleMemberFilterField.MODULE.value: "fastapi.routing",
        ModuleMemberFilterField.NAME.value: "APIRouter",
        ModuleMemberFilterField.KIND.value: MemberKind.CLASS.value,
        "signature": "(prefix: str = '')",
        "docstring": "Groups endpoints.",
    })
    result = f.format(m)
    assert "[fastapi]" in result
    assert "fastapi.routing.APIRouter" in result
    assert "(prefix: str = '')" in result
    assert "(class)" in result
    assert "Groups endpoints." in result


def test_formatter_to_dict_from_dict_roundtrip(tmp_path):
    for cls in (ChunkMarkdownFormatter, ModuleMemberMarkdownFormatter):
        instance = cls()
        d = instance.to_dict()
        ctx = BuildContext(
            connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
        )
        rebuilt = formatter_registry.build(d, ctx)
        assert type(rebuilt) is cls
