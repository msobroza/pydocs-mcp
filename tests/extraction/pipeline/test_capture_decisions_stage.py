"""CaptureDecisionsStage — mine + merge + emit decision-as-chunks (spec §D8).

Exercises the stage in isolation: project-target gating, the ``config.enabled``
short-circuit, decision-as-chunk emission (title + evidence, ``origin`` +
``decision_key`` metadata), the merged tuple landing on ``state.decisions``, and
per-source failure isolation. The git subprocess is neutralised by pointing the
context at a non-repo ``tmp_path`` (``read_git_log`` degrades to "").
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
from pydocs_mcp.extraction.pipeline.stages.capture_decisions import CaptureDecisionsStage
from pydocs_mcp.models import ChunkOrigin
from pydocs_mcp.retrieval.config.models import DecisionCaptureConfig


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


def _cfg(**overrides: object) -> DecisionCaptureConfig:
    # Only inline_markers so the test doesn't depend on git/docs sources.
    base: dict[str, object] = {"sources": ["inline_markers"]}
    base.update(overrides)
    return DecisionCaptureConfig(**base)  # type: ignore[arg-type]


async def test_mines_marker_and_emits_decision_chunk(tmp_path: Path) -> None:
    tree = _module_tree("x = 1\n# DECISION: use sidecar for vectors\ny = 2\n")
    stage = CaptureDecisionsStage(config=_cfg())
    out = await stage.run(_state(trees=(tree,), root=tmp_path))

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
    stage = CaptureDecisionsStage(config=_cfg())
    state = _state(trees=(tree,), target_kind=TargetKind.DEPENDENCY, root=tmp_path)
    out = await stage.run(state)
    assert out is state  # untouched — dependency targets never mine decisions


async def test_disabled_config_is_noop(tmp_path: Path) -> None:
    tree = _module_tree("# DECISION: something\n")
    stage = CaptureDecisionsStage(config=_cfg(enabled=False))
    state = _state(trees=(tree,), root=tmp_path)
    out = await stage.run(state)
    assert out is state
    assert out.decisions == ()


async def test_per_source_failure_isolated(tmp_path: Path, monkeypatch) -> None:
    tree = _module_tree("# DECISION: keep going\n")
    stage = CaptureDecisionsStage(config=_cfg())

    # Force the inline_markers source instance's mine to raise; the stage must
    # log + skip, returning empty (no chunk), not propagate the exception.
    from pydocs_mcp.extraction.decisions.sources import inline_markers as im

    async def _boom(self, ctx):
        raise RuntimeError("source blew up")

    monkeypatch.setattr(im.InlineMarkersSource, "mine", _boom)
    out = await stage.run(_state(trees=(tree,), root=tmp_path))
    assert out.decisions == ()
    assert all(
        c.metadata.get("origin") != ChunkOrigin.DECISION_RECORD.value for c in out.chunks.chunks
    )


async def test_from_dict_pulls_config_and_pipeline_hash() -> None:
    class _Ctx:
        app_config = type(
            "_AC", (), {"decision_capture": DecisionCaptureConfig(merge_jaccard=0.5)}
        )()
        pipeline_hash = "ph-123"

    stage = CaptureDecisionsStage.from_dict({"type": "capture_decisions"}, _Ctx())
    assert stage.config.merge_jaccard == 0.5
    assert stage.pipeline_hash == "ph-123"


def test_bare_stage_defaults_config() -> None:
    # A bare CaptureDecisionsStage() (no config) must default-construct one, not
    # carry a shared mutable None.
    stage = CaptureDecisionsStage()
    assert isinstance(stage.config, DecisionCaptureConfig)
    assert stage.config.enabled is True


async def test_decision_travels_as_rawdecision(tmp_path: Path) -> None:
    # Type sanity: the state field carries RawDecision instances.
    tree = _module_tree("# WHY: chose async for the retrieval pipeline\n")
    stage = CaptureDecisionsStage(config=_cfg())
    out = await stage.run(_state(trees=(tree,), root=tmp_path))
    assert all(isinstance(d, RawDecision) for d in out.decisions)


# ── optional LLM-structuring hook (default OFF, spec §D12) ──


def test_from_dict_builds_no_client_when_structuring_off() -> None:
    # llm_structuring.enabled defaults to False → NO client is constructed
    # (build_llm_client is never called, so no eager OpenAI import cost).
    class _Ctx:
        app_config = type(
            "_AC",
            (),
            {"decision_capture": DecisionCaptureConfig(), "llm": object()},
        )()
        pipeline_hash = ""

    stage = CaptureDecisionsStage.from_dict({"type": "capture_decisions"}, _Ctx())
    assert stage.llm_client is None


def test_from_dict_builds_client_when_structuring_on(monkeypatch) -> None:
    # llm_structuring.enabled=True → the stage builds a client via
    # build_llm_client(app_config.llm). The autouse conftest patch returns a
    # FakeLlmClient, keeping this offline.
    from tests._fakes import FakeLlmClient

    cfg = DecisionCaptureConfig(llm_structuring={"enabled": True})  # type: ignore[arg-type]

    class _Ctx:
        app_config = type("_AC", (), {"decision_capture": cfg, "llm": object()})()
        pipeline_hash = ""

    stage = CaptureDecisionsStage.from_dict({"type": "capture_decisions"}, _Ctx())
    assert isinstance(stage.llm_client, FakeLlmClient)


async def test_structuring_hook_populates_decision_structured(tmp_path: Path) -> None:
    from tests._fakes import FakeLlmClient

    tree = _module_tree("x = 1\n# DECISION: use sidecar for vectors\ny = 2\n")
    reply = (
        '{"decisions": [{"title": "use sidecar for vectors", '
        '"decision": "use sidecar for vectors"}]}'
    )
    client = FakeLlmClient(responses={"": reply})
    stage = CaptureDecisionsStage(
        config=_cfg(llm_structuring={"enabled": True}),
        llm_client=client,
    )
    out = await stage.run(_state(trees=(tree,), root=tmp_path))

    key = decision_key("use sidecar for vectors")
    assert key in out.decision_structured
    structured, verification = out.decision_structured[key]
    assert structured["decision"] == "use sidecar for vectors"
    assert verification == "verified"


async def test_no_structuring_when_client_absent(tmp_path: Path) -> None:
    # Enabled config but NO client wired (from_dict didn't build one) → the hook
    # is a no-op; decision_structured stays empty.
    tree = _module_tree("# DECISION: use sidecar for vectors\n")
    stage = CaptureDecisionsStage(config=_cfg(llm_structuring={"enabled": True}))
    out = await stage.run(_state(trees=(tree,), root=tmp_path))
    assert out.decision_structured == {}
