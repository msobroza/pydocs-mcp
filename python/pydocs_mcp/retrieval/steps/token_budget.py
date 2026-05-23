"""TokenBudgetStep — render the result as a budgeted composite Chunk.

Renderer step (Task 8): reads ``state.candidates`` (the intermediate
ranked list produced by fetch + score + filter steps) and writes
``state.result`` as a one-item composite chunk carrying the
budget-truncated markdown. This is the single state-shape transition
from candidate list to MCP output payload — every other step in the
pipeline operates on ``state.candidates``.

Rendering delegates to :mod:`pydocs_mcp.application.formatting` (the
same helpers MCP / CLI fallback paths use), so byte-identical output
is preserved across call sites (AC #6 single-source-of-truth; AC #21
byte-parity with pre-sub-PR-2 ``format_within_budget``).

``COMPOSITE_TITLE_SENTINEL`` is defined here because this stage is the
sentinel's producer; consumers like :class:`MetadataPostFilterStep`
import it from this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
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
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.protocols import ResultFormatter


# Sentinel title on composite formatter output. ``MetadataPostFilterStep``
# bypasses title-based filters when it sees this marker so downstream
# post-filters never drop the budgeted answer chunk (AC #34).
COMPOSITE_TITLE_SENTINEL = "_composite"


@step_registry.register("token_budget_formatter")
@dataclass(frozen=True, slots=True)
class TokenBudgetStep(RetrieverStep):
    formatter: "ResultFormatter"
    budget: int
    # WHY: inherited ``RetrieverStep.name`` has no default; redeclaring as
    # ``kw_only`` lets non-default subclass fields (formatter, budget)
    # come before it without violating "non-default after default" rule.
    name: str = field(default="token_budget_formatter", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        # Read from ``state.candidates`` (Task 8 — the renderer is the
        # candidates→result transition). Fall back to ``state.result`` for
        # backward-compat with legacy callers that haven't migrated yet.
        source = state.candidates if state.candidates is not None else state.result
        if source is None or not source.items:
            return state
        if isinstance(source, ChunkList):
            composite_text = format_chunks_markdown_within_budget(
                source.items, self.budget,
            )
        else:
            composite_text = format_members_markdown_within_budget(
                source.items, self.budget,
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
    def from_dict(cls, data: dict, context: BuildContext) -> "TokenBudgetStep":
        return cls(
            formatter=context.formatter_registry.build(data["formatter"], context),
            budget=data["budget"],
        )


__all__ = ("COMPOSITE_TITLE_SENTINEL", "TokenBudgetStep")
