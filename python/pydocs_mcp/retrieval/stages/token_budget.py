"""TokenBudgetStage — render the result as a budgeted composite Chunk.

Rendering delegates to :mod:`pydocs_mcp.application.formatting` (the
same helpers MCP / CLI fallback paths use), so byte-identical output
is preserved across call sites (AC #6 single-source-of-truth; AC #21
byte-parity with pre-sub-PR-2 ``format_within_budget``).

``COMPOSITE_TITLE_SENTINEL`` is defined here because this stage is the
sentinel's producer; consumers like :class:`MetadataPostFilterStage`
import it from this module.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from pydocs_mcp.application.formatting import (
    format_chunks_markdown_within_budget,
    format_members_markdown_within_budget,
)
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    ChunkOrigin,
)
from pydocs_mcp.retrieval.pipeline import PipelineState
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.protocols import ResultFormatter


# Sentinel title on composite formatter output. ``MetadataPostFilterStage``
# bypasses title-based filters when it sees this marker so downstream
# post-filters never drop the budgeted answer chunk (AC #34).
COMPOSITE_TITLE_SENTINEL = "_composite"


@stage_registry.register("token_budget_formatter")
@dataclass(frozen=True, slots=True)
class TokenBudgetStage:
    formatter: "ResultFormatter"
    budget: int
    name: str = "token_budget_formatter"

    async def run(self, state: PipelineState) -> PipelineState:
        if state.result is None or not state.result.items:
            return state
        if isinstance(state.result, ChunkList):
            composite_text = format_chunks_markdown_within_budget(
                state.result.items, self.budget,
            )
        else:
            composite_text = format_members_markdown_within_budget(
                state.result.items, self.budget,
            )
        composite = Chunk(
            text=composite_text,
            metadata={
                ChunkFilterField.ORIGIN.value: ChunkOrigin.COMPOSITE_OUTPUT.value,
                ChunkFilterField.TITLE.value: COMPOSITE_TITLE_SENTINEL,
            },
        )
        return replace(state, result=ChunkList(items=(composite,)))

    def to_dict(self) -> dict:
        return {
            "type": "token_budget_formatter",
            "formatter": self.formatter.to_dict(),
            "budget": self.budget,
        }

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "TokenBudgetStage":
        return cls(
            formatter=context.formatter_registry.build(data["formatter"], context),
            budget=data["budget"],
        )


__all__ = ("COMPOSITE_TITLE_SENTINEL", "TokenBudgetStage")
