"""StructureDecisionsStage — opt-in LLM structuring of merged decisions (spec §D12).

Runs in the ``capture_decisions`` sub-pipeline once ``state.decisions`` is
materialized. Off by default: a client is wired ONLY when both
``decision_capture.enabled`` and ``decision_capture.llm_structuring.enabled``
hold (built by the composite's ``from_dict`` via :func:`_maybe_build_llm_client`
from ``app_config.llm``), so the deterministic path never touches an LLM. When
a client is present, ``structure_decisions`` LLM-structures + grounds
``state.decisions`` into ``state.decision_structured`` (keyed by
``decision_key(title)`` → (grounded fields, verification tier)); otherwise this
stage is an identity.

The overlay is NOT consumed here — it rides ``state.decision_structured`` out
via :class:`ExtractionResult` into ``IndexingService.reindex_package``, which
stamps ``structured`` / ``verification`` onto the matching ``DecisionRecord``
before persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from pydocs_mcp.extraction.decisions.structuring import structure_decisions
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.retrieval.config import DecisionCaptureConfig
from pydocs_mcp.retrieval.protocols import LlmClient


@dataclass(frozen=True, slots=True)
class StructureDecisionsStage:
    """LLM-structure ``state.decisions`` → ``state.decision_structured`` (opt-in).

    ``llm_client`` is wired ONLY on the enabled path (built by the composite's
    ``from_dict``); ``None`` = the default-off path where no client is
    constructed and no LLM is touched during indexing.
    """

    config: DecisionCaptureConfig = field(default_factory=DecisionCaptureConfig)
    # Kept as a field (not built in ``run``) so the eager OpenAI import cost is
    # paid at composition time, not per-index-call. ``None`` = default-off.
    llm_client: LlmClient | None = None
    name: str = "structure_decisions"

    async def run(self, state: IngestionState) -> IngestionState:
        # No client wired → default-off path: identity out, no allocation.
        # ``structure_decisions`` already short-circuits on a disabled config or
        # empty records, so the client-presence guard is what keeps the off path
        # from even constructing an empty overlay dict.
        if self.llm_client is None:
            return state
        structured = await structure_decisions(
            state.decisions, self.llm_client, self.config.llm_structuring
        )
        return replace(state, decision_structured=structured)


def _maybe_build_llm_client(config: DecisionCaptureConfig, app_config: Any) -> LlmClient | None:
    """Build the structuring client ONLY when the default-off gates are enabled.

    Off path (the default): return ``None`` without importing or constructing
    any client, so composition pays no eager OpenAI-import cost. Both gates
    must hold — ``decision_capture.enabled=false`` disables the whole capture,
    so it must never construct a client even if ``llm_structuring.enabled`` is
    left on in the YAML. On path: build via the shared ``build_llm_client``
    from ``app_config.llm`` — imported lazily so the module attribute (and test
    patches of it) resolve at call time.
    """
    if not (config.enabled and config.llm_structuring.enabled) or app_config is None:
        return None
    from pydocs_mcp.retrieval.llm_clients import build_llm_client

    return build_llm_client(app_config.llm)


__all__ = ("StructureDecisionsStage",)
