"""Focused unit tests for the four decision sub-stages (spec §D8-§D12).

Each stage is exercised in isolation so a regression pins to one stage:

* :class:`MineDecisionsStage` guard — dependency / disabled → empty
  ``decisions_raw`` (identity out).
* :class:`MergeDecisionsStage` pure transform — raws → merged, empty → identity.
* :class:`EmitDecisionChunksStage` pure transform — merged → decision-as-chunks,
  empty → identity.
* A composition check that :class:`CaptureDecisionsPipeline` produces the same
  ``decisions`` + decision-chunks a single mine→merge→emit run would.

End-to-end behavior of the composed pipeline lives in
``test_capture_decisions_stage.py``.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.decisions._types import RawDecision
from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.pipeline.ingestion import (
    ChunkBundle,
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages.decisions.emit_decision_chunks import (
    EmitDecisionChunksStage,
)
from pydocs_mcp.extraction.pipeline.stages.decisions.merge_decisions import MergeDecisionsStage
from pydocs_mcp.extraction.pipeline.stages.decisions.mine_decisions import MineDecisionsStage
from pydocs_mcp.models import ChunkOrigin
from pydocs_mcp.retrieval.config.models import DecisionCaptureConfig
from pydocs_mcp.storage.decision_record import DecisionEvidence


def _module_tree(text: str) -> DocumentNode:
    return DocumentNode(
        node_id="pkg.mod",
        kind=NodeKind.MODULE,
        title="mod",
        qualified_name="pkg.mod",
        source_path="pkg/mod.py",
        start_line=1,
        end_line=len(text.splitlines()),
        text=text,
        content_hash="h",
    )


def _state(
    *,
    trees: tuple[DocumentNode, ...] = (),
    target_kind: TargetKind = TargetKind.PROJECT,
    root: Path,
    decisions_raw: tuple[RawDecision, ...] = (),
    decisions: tuple[RawDecision, ...] = (),
) -> IngestionState:
    return IngestionState(
        files=FileBundle(
            target=root,
            target_kind=target_kind,
            package_name="__project__",
            root=root,
        ),
        chunks=ChunkBundle(trees=trees),
        decisions_raw=decisions_raw,
        decisions=decisions,
    )


def _raw(title: str) -> RawDecision:
    return RawDecision(
        title=title,
        status="active",
        source="inline_markers",
        confidence=1.0,
        evidence=(DecisionEvidence(source="inline_markers", locator="pkg/mod.py:2-2", text=title),),
        affected_files=("pkg/mod.py",),
        affected_qnames=(),
    )


def _cfg(**overrides: object) -> DecisionCaptureConfig:
    base: dict[str, object] = {"sources": ["inline_markers"]}
    base.update(overrides)
    return DecisionCaptureConfig(**base)  # type: ignore[arg-type]


# ── MineDecisionsStage guard ────────────────────────────────────────────────


async def test_mine_dependency_target_leaves_decisions_raw_empty(tmp_path: Path) -> None:
    tree = _module_tree("# DECISION: dependency internal choice\n")
    state = _state(trees=(tree,), target_kind=TargetKind.DEPENDENCY, root=tmp_path)
    out = await MineDecisionsStage(config=_cfg()).run(state)
    assert out is state  # dependency targets never mine
    assert out.decisions_raw == ()


async def test_mine_disabled_config_leaves_decisions_raw_empty(tmp_path: Path) -> None:
    tree = _module_tree("# DECISION: something\n")
    state = _state(trees=(tree,), root=tmp_path)
    out = await MineDecisionsStage(config=_cfg(enabled=False)).run(state)
    assert out is state
    assert out.decisions_raw == ()


async def test_mine_project_target_populates_decisions_raw(tmp_path: Path) -> None:
    tree = _module_tree("# DECISION: use sidecar for vectors\n")
    out = await MineDecisionsStage(config=_cfg()).run(_state(trees=(tree,), root=tmp_path))
    assert len(out.decisions_raw) == 1
    assert out.decisions_raw[0].title == "use sidecar for vectors"
    # Mine does NOT merge or emit — those are downstream stages.
    assert out.decisions == ()
    assert out.chunks.chunks == ()


def test_mine_bare_stage_defaults_config() -> None:
    # A bare MineDecisionsStage() default-constructs a config, not a shared None.
    stage = MineDecisionsStage()
    assert isinstance(stage.config, DecisionCaptureConfig)
    assert stage.config.enabled is True


# ── MergeDecisionsStage pure transform ──────────────────────────────────────


async def test_merge_empty_in_is_identity(tmp_path: Path) -> None:
    state = _state(root=tmp_path)
    out = await MergeDecisionsStage(config=_cfg()).run(state)
    assert out is state
    assert out.decisions == ()


async def test_merge_collapses_similar_titles(tmp_path: Path) -> None:
    raws = (_raw("use sidecar for vectors"), _raw("use the sidecar for vectors"))
    state = _state(root=tmp_path, decisions_raw=raws)
    out = await MergeDecisionsStage(config=_cfg(merge_jaccard=0.5)).run(state)
    # Two near-identical titles merge into one decision.
    assert len(out.decisions) == 1


async def test_merge_keeps_distinct_titles(tmp_path: Path) -> None:
    raws = (_raw("use sidecar for vectors"), _raw("adopt fts5 for lexical search"))
    state = _state(root=tmp_path, decisions_raw=raws)
    out = await MergeDecisionsStage(config=_cfg(merge_jaccard=0.5)).run(state)
    assert len(out.decisions) == 2


# ── EmitDecisionChunksStage pure transform ──────────────────────────────────


async def test_emit_empty_in_is_identity(tmp_path: Path) -> None:
    state = _state(root=tmp_path)
    out = await EmitDecisionChunksStage().run(state)
    assert out is state
    assert out.chunks.chunks == ()


async def test_emit_one_chunk_per_decision(tmp_path: Path) -> None:
    decisions = (_raw("use sidecar for vectors"), _raw("adopt fts5"))
    state = _state(root=tmp_path, decisions=decisions)
    out = await EmitDecisionChunksStage().run(state)

    chunks = [
        c
        for c in out.chunks.chunks
        if c.metadata.get("origin") == ChunkOrigin.DECISION_RECORD.value
    ]
    assert len(chunks) == 2
    titles = {c.metadata["title"] for c in chunks}
    assert titles == {"use sidecar for vectors", "adopt fts5"}
    for c in chunks:
        assert c.metadata["decision_key"] == decision_key(c.metadata["title"])
        assert c.metadata["package"] == "__project__"


async def test_emit_preserves_existing_chunks(tmp_path: Path) -> None:
    # The decision chunks APPEND — pre-existing chunks survive.
    from pydocs_mcp.models import Chunk

    existing = Chunk(text="code", metadata={"package": "__project__", "module": "m", "title": "t"})
    state = IngestionState(
        files=FileBundle(package_name="__project__", root=tmp_path),
        chunks=ChunkBundle(chunks=(existing,)),
        decisions=(_raw("use sidecar for vectors"),),
    )
    out = await EmitDecisionChunksStage().run(state)
    assert out.chunks.chunks[0] is existing
    assert len(out.chunks.chunks) == 2


# ── Composition parity ──────────────────────────────────────────────────────


async def test_pipeline_matches_manual_mine_merge_emit(tmp_path: Path) -> None:
    # The composite sub-pipeline (mine → merge → emit, structure off) yields the
    # same decisions + decision-chunks a manual three-stage run does.
    from pydocs_mcp.extraction.pipeline.stages.decisions.capture_decisions import (
        CaptureDecisionsPipeline,
    )
    from pydocs_mcp.extraction.pipeline.stages.decisions.structure_decisions import (
        StructureDecisionsStage,
    )

    tree = _module_tree("# DECISION: use sidecar for vectors\n# WHY: dense vectors are big\n")
    cfg = _cfg()

    # Manual run.
    manual_state = _state(trees=(tree,), root=tmp_path)
    manual_state = await MineDecisionsStage(config=cfg).run(manual_state)
    manual_state = await MergeDecisionsStage(config=cfg).run(manual_state)
    manual_state = await StructureDecisionsStage(config=cfg, llm_client=None).run(manual_state)
    manual_state = await EmitDecisionChunksStage().run(manual_state)

    # Composite run.
    pipeline = CaptureDecisionsPipeline(
        stages=(
            MineDecisionsStage(config=cfg),
            MergeDecisionsStage(config=cfg),
            StructureDecisionsStage(config=cfg, llm_client=None),
            EmitDecisionChunksStage(),
        )
    )
    composite_state = await pipeline.run(_state(trees=(tree,), root=tmp_path))

    manual_titles = [d.title for d in manual_state.decisions]
    composite_titles = [d.title for d in composite_state.decisions]
    assert manual_titles == composite_titles

    def _decision_chunk_keys(state: IngestionState) -> list[str]:
        return [
            c.metadata["decision_key"]
            for c in state.chunks.chunks
            if c.metadata.get("origin") == ChunkOrigin.DECISION_RECORD.value
        ]

    assert _decision_chunk_keys(manual_state) == _decision_chunk_keys(composite_state)
