"""Result formatters — render Chunks / ModuleMembers as markdown strings."""
from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
)
from pydocs_mcp.retrieval.serialization import BuildContext, formatter_registry


@formatter_registry.register("chunk_markdown")
@dataclass(frozen=True, slots=True)
class ChunkMarkdownFormatter:
    """Renders a Chunk as `## {title}\n\n{text}`."""

    name: str = "chunk_markdown"

    def format(self, result: Chunk | ModuleMember) -> str:
        # Type-narrow: this formatter is registered for chunks.
        title = ""
        if isinstance(result, Chunk):
            title = result.metadata.get(ChunkFilterField.TITLE.value, "") or ""
            body = result.text or ""
        else:  # defensive — but not expected per registry dispatch
            body = ""
        return f"## {title}\n\n{body}"

    def to_dict(self) -> dict:
        return {"type": "chunk_markdown"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ChunkMarkdownFormatter":
        return cls()


@formatter_registry.register("member_markdown")
@dataclass(frozen=True, slots=True)
class ModuleMemberMarkdownFormatter:
    """Renders a ModuleMember as `**[{package}] {module}.{name}{signature}** ({kind})\n{docstring}`."""

    name: str = "member_markdown"

    def format(self, result: Chunk | ModuleMember) -> str:
        if not isinstance(result, ModuleMember):
            return ""
        md = result.metadata
        package = md.get(ModuleMemberFilterField.PACKAGE.value, "")
        module = md.get(ModuleMemberFilterField.MODULE.value, "")
        name = md.get(ModuleMemberFilterField.NAME.value, "")
        kind = md.get(ModuleMemberFilterField.KIND.value, "")
        signature = md.get("signature", "") or ""
        docstring = md.get("docstring", "") or ""
        header = f"**[{package}] {module}.{name}{signature}** ({kind})"
        return f"{header}\n{docstring}"

    def to_dict(self) -> dict:
        return {"type": "member_markdown"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ModuleMemberMarkdownFormatter":
        return cls()
