"""capture_decisions sub-pipeline — mine + merge + structure + emit (spec §D8).

Exercises the composed :class:`CaptureDecisionsPipeline` end-to-end (the same
composite ``ingestion.yaml`` wires as a single ``{ type: capture_decisions }``
entry): project-target gating, the ``config.enabled`` short-circuit,
decision-as-chunk emission (title + evidence, ``origin`` + ``decision_key``
metadata), the merged tuple landing on ``state.decisions``, per-source failure
isolation, and the opt-in §D12 structuring hook. The git subprocess is
neutralised by pointing the context at a non-repo ``tmp_path``
(``read_git_log`` degrades to "").

Unit tests for the individual sub-stages (mine guard, merge/emit pure
transforms) live in ``test_decision_stages.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydocs_mcp.extraction.decisions._types import RawDecision
from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.pipeline.ingestion import (
    ChunkBundle,
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages.decisions.capture_decisions import (
    CaptureDecisionsPipeline,
)
from pydocs_mcp.models import ChunkOrigin
from pydocs_mcp.retrieval.config.models import DecisionCaptureConfig


class _Ctx:
    """Minimal BuildContext stand-in: the sub-stages only read these two fields."""

    def __init__(self, config: DecisionCaptureConfig, *, pipeline_hash: str = "") -> None:
        self.app_config = type("_AC", (), {"decision_capture": config, "llm": object()})()
        self.pipeline_hash = pipeline_hash


def _pipeline(
    config: DecisionCaptureConfig, *, pipeline_hash: str = ""
) -> CaptureDecisionsPipeline:
    """Build the composite the way ``ingestion.yaml`` does — via ``from_dict``."""
    return CaptureDecisionsPipeline.from_dict(
        {"type": "capture_decisions"}, _Ctx(config, pipeline_hash=pipeline_hash)
    )


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
    trees: tuple[DocumentNode, ...],
    target_kind: TargetKind = TargetKind.PROJECT,
    root: Path,
) -> IngestionState:
    return IngestionState(
        files=FileBundle(
            target=root,
            target_kind=target_kind,
            package_name="__project__",
            root=root,
        ),
        chunks=ChunkBundle(trees=trees),
    )


def _cfg(**overrides: Any) -> DecisionCaptureConfig:
    # Only inline_markers so the test doesn't depend on git/docs sources.
    base: dict[str, object] = {"sources": ["inline_markers"]}
    base.update(overrides)
    return DecisionCaptureConfig(**base)  # type: ignore[arg-type]


async def test_mines_marker_and_emits_decision_chunk(tmp_path: Path) -> None:
    tree = _module_tree("x = 1\n# DECISION: use sidecar for vectors\ny = 2\n")
    out = await _pipeline(_cfg()).run(_state(trees=(tree,), root=tmp_path))

    # Merged decision on state + one decision-as-chunk appended.
    assert len(out.decisions) == 1
    assert out.decisions[0].title == "use sidecar for vectors"
    decision_chunks = [
        c
        for c in out.chunks.chunks
        if c.metadata.get("origin") == ChunkOrigin.DECISION_RECORD.value
    ]
    assert len(decision_chunks) == 1
    chunk = decision_chunks[0]
    assert chunk.metadata["title"] == "use sidecar for vectors"
    assert chunk.metadata["decision_key"] == decision_key("use sidecar for vectors")
    assert chunk.metadata["package"] == "__project__"
    assert "use sidecar for vectors" in chunk.text


async def test_dependency_target_is_noop(tmp_path: Path) -> None:
    tree = _module_tree("# DECISION: dependency internal choice\n")
    state = _state(trees=(tree,), target_kind=TargetKind.DEPENDENCY, root=tmp_path)
    out = await _pipeline(_cfg()).run(state)
    assert out is state  # untouched — dependency targets never mine decisions


async def test_disabled_config_is_noop(tmp_path: Path) -> None:
    tree = _module_tree("# DECISION: something\n")
    state = _state(trees=(tree,), root=tmp_path)
    out = await _pipeline(_cfg(enabled=False)).run(state)
    assert out is state
    assert out.decisions == ()


async def test_per_source_failure_isolated(tmp_path: Path, monkeypatch) -> None:
    tree = _module_tree("# DECISION: keep going\n")

    # Force the inline_markers source instance's mine to raise; the mine stage
    # must log + skip, returning empty (no chunk), not propagate the exception.
    from pydocs_mcp.extraction.decisions.sources import inline_markers as im

    async def _boom(self, ctx):
        raise RuntimeError("source blew up")

    monkeypatch.setattr(im.InlineMarkersSource, "mine", _boom)
    out = await _pipeline(_cfg()).run(_state(trees=(tree,), root=tmp_path))
    assert out.decisions == ()
    assert all(
        c.metadata.get("origin") != ChunkOrigin.DECISION_RECORD.value for c in out.chunks.chunks
    )


async def test_from_dict_pulls_config_and_pipeline_hash() -> None:
    # The mine sub-stage carries the config + pipeline_hash threaded from context.
    from pydocs_mcp.extraction.pipeline.stages.decisions.mine_decisions import MineDecisionsStage

    pipeline = _pipeline(DecisionCaptureConfig(merge_jaccard=0.5), pipeline_hash="ph-123")
    mine = pipeline.stages[0]
    assert isinstance(mine, MineDecisionsStage)
    assert mine.config.merge_jaccard == 0.5
    assert mine.pipeline_hash == "ph-123"


async def test_decision_travels_as_rawdecision(tmp_path: Path) -> None:
    # Type sanity: the state field carries RawDecision instances.
    tree = _module_tree("# WHY: chose async for the retrieval pipeline\n")
    out = await _pipeline(_cfg()).run(_state(trees=(tree,), root=tmp_path))
    assert all(isinstance(d, RawDecision) for d in out.decisions)


# ── optional LLM-structuring hook (default OFF, spec §D12) ──


def test_from_dict_builds_no_client_when_structuring_off() -> None:
    # llm_structuring.enabled defaults to False → NO client is constructed
    # (build_llm_client is never called, so no eager OpenAI import cost).
    from pydocs_mcp.extraction.pipeline.stages.decisions.structure_decisions import (
        StructureDecisionsStage,
    )

    pipeline = _pipeline(DecisionCaptureConfig())
    # Index 3: mine, merge, emit_governs_edges, structure, emit_decision_chunks.
    structure = pipeline.stages[3]
    assert isinstance(structure, StructureDecisionsStage)
    assert structure.llm_client is None


def test_from_dict_builds_client_when_structuring_on(monkeypatch) -> None:
    # llm_structuring.enabled=True → the structure sub-stage builds a client via
    # build_llm_client(app_config.llm). The autouse conftest patch returns a
    # FakeLlmClient, keeping this offline.
    from tests._fakes import FakeLlmClient

    cfg = DecisionCaptureConfig(llm_structuring={"enabled": True})  # type: ignore[arg-type]
    pipeline = _pipeline(cfg)
    # Index 3: mine, merge, emit_governs_edges, structure, emit_decision_chunks.
    structure = pipeline.stages[3]
    assert isinstance(structure.llm_client, FakeLlmClient)


async def test_structuring_hook_populates_decision_structured(tmp_path: Path) -> None:
    from pydocs_mcp.extraction.pipeline.stages.decisions.capture_decisions import (
        CaptureDecisionsPipeline,
    )
    from pydocs_mcp.extraction.pipeline.stages.decisions.emit_decision_chunks import (
        EmitDecisionChunksStage,
    )
    from pydocs_mcp.extraction.pipeline.stages.decisions.merge_decisions import (
        MergeDecisionsStage,
    )
    from pydocs_mcp.extraction.pipeline.stages.decisions.mine_decisions import MineDecisionsStage
    from pydocs_mcp.extraction.pipeline.stages.decisions.structure_decisions import (
        StructureDecisionsStage,
    )
    from tests._fakes import FakeLlmClient

    tree = _module_tree("x = 1\n# DECISION: use sidecar for vectors\ny = 2\n")
    reply = (
        '{"decisions": [{"title": "use sidecar for vectors", '
        '"decision": "use sidecar for vectors"}]}'
    )
    client = FakeLlmClient(responses={"": reply})
    cfg = _cfg(llm_structuring={"enabled": True})
    # Compose the sub-pipeline with an explicitly-wired client (bypassing
    # from_dict's build_llm_client so the fake's canned reply is used).
    pipeline = CaptureDecisionsPipeline(
        stages=(
            MineDecisionsStage(config=cfg),
            MergeDecisionsStage(config=cfg),
            StructureDecisionsStage(config=cfg, llm_client=client),
            EmitDecisionChunksStage(),
        )
    )
    out = await pipeline.run(_state(trees=(tree,), root=tmp_path))

    key = decision_key("use sidecar for vectors")
    assert key in out.decision_structured
    structured, verification = out.decision_structured[key]
    assert structured["decision"] == "use sidecar for vectors"
    assert verification == "verified"


async def test_no_structuring_when_client_absent(tmp_path: Path) -> None:
    # Enabled config but NO client wired (the structure sub-stage carries
    # llm_client=None) → the hook is a no-op; decision_structured stays empty.
    from pydocs_mcp.extraction.pipeline.stages.decisions.emit_decision_chunks import (
        EmitDecisionChunksStage,
    )
    from pydocs_mcp.extraction.pipeline.stages.decisions.merge_decisions import (
        MergeDecisionsStage,
    )
    from pydocs_mcp.extraction.pipeline.stages.decisions.mine_decisions import MineDecisionsStage
    from pydocs_mcp.extraction.pipeline.stages.decisions.structure_decisions import (
        StructureDecisionsStage,
    )

    tree = _module_tree("# DECISION: use sidecar for vectors\n")
    cfg = _cfg(llm_structuring={"enabled": True})
    pipeline = CaptureDecisionsPipeline(
        stages=(
            MineDecisionsStage(config=cfg),
            MergeDecisionsStage(config=cfg),
            StructureDecisionsStage(config=cfg, llm_client=None),
            EmitDecisionChunksStage(),
        )
    )
    out = await pipeline.run(_state(trees=(tree,), root=tmp_path))
    assert out.decision_structured == {}
