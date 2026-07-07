"""EmitDecisionChunksStage — one decision-as-chunk per merged decision (spec §D9).

Final stage of the ``capture_decisions`` sub-pipeline. A pure transform: build
one *decision-as-chunk* per ``state.decisions`` and append them to
``state.chunks.chunks`` so architectural rationale flows through the SAME
hashing → embedding → retrieval machinery as code/doc chunks. Each chunk carries
``origin=decision_record`` and a ``decision_key`` (normalized-title key) that the
persistence layer maps to the assigned ``decision_id``.

Ordered (via the parent sub-pipeline) BEFORE ``assign_chunk_content_hash`` in
``ingestion.yaml`` so the new chunks pick up the pipeline-aware hash on the
normal path. Empty ``decisions`` → identity out (no chunk appended).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pydocs_mcp.extraction.decisions._types import RawDecision
from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.models import Chunk, ChunkOrigin


@dataclass(frozen=True, slots=True)
class EmitDecisionChunksStage:
    """Append one searchable decision-as-chunk per merged decision."""

    name: str = "emit_decision_chunks"

    async def run(self, state: IngestionState) -> IngestionState:
        # Empty in → identity out: keeps the dependency/disabled path returning
        # the untouched state (no decisions means no chunks to append).
        if not state.decisions:
            return state
        decision_chunks = tuple(
            _decision_to_chunk(decision, package=state.files.package_name)
            for decision in state.decisions
        )
        new_chunks = replace(state.chunks, chunks=(*state.chunks.chunks, *decision_chunks))
        return replace(state, chunks=new_chunks)


def _decision_to_chunk(decision: RawDecision, *, package: str) -> Chunk:
    """One merged decision → a searchable decision-as-chunk (spec §D9).

    ``text`` = title + a blank line + the joined evidence texts, so BM25 / dense
    retrieval sees both the decision statement and its verbatim grounding.
    ``decision_key`` lets the persistence layer stamp ``decision_id`` after the
    record's id is assigned.
    """
    evidence_text = "\n\n".join(ev.text for ev in decision.evidence)
    text = f"{decision.title}\n\n{evidence_text}" if evidence_text else decision.title
    return Chunk(
        text=text,
        metadata={
            "package": package,
            "module": "",
            "title": decision.title,
            "origin": ChunkOrigin.DECISION_RECORD.value,
            "decision_key": decision_key(decision.title),
        },
    )


__all__ = ("EmitDecisionChunksStage",)
