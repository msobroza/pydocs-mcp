"""``llm_structuring_applies`` says exactly when the structuring LLM runs (#347).

The content hash folds the structuring LLM's identity wherever this predicate
holds, so the predicate must agree with the decision-capture stages it
describes. If it said "runs" where structuring is skipped, a model switch would
re-extract for nothing; if it said "skipped" where structuring runs, the
switched model's answer would be discarded as a cache hit on every pass — the
loop the fold exists to end. The stages keep their own gates, so this suite
pins the predicate against what they actually DO: every combination of the two
switches and the two target kinds, run through the real composite, with the
LLM builder replaced by a recording fake.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pydocs_mcp.extraction.decisions.capture_gates import llm_structuring_applies
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
from pydocs_mcp.retrieval import llm_clients
from pydocs_mcp.retrieval.config import DecisionCaptureConfig, LlmConfig
from tests._fakes import RecordingLlmClientBuilder

_MARKED_SOURCE = "x = 1\n# DECISION: use a sidecar file for vectors\ny = 2\n"


def _marked_module() -> DocumentNode:
    """One module whose marker the inline-marker source always mines, so a
    pipeline that structures has a decision to hand the LLM."""
    return DocumentNode(
        node_id="pkg.mod",
        kind=NodeKind.MODULE,
        title="mod",
        qualified_name="pkg.mod",
        source_path="pkg/mod.py",
        start_line=1,
        end_line=len(_MARKED_SOURCE.splitlines()),
        text=_MARKED_SOURCE,
        content_hash="h",
    )


def _state(kind: TargetKind, root: Path) -> IngestionState:
    """``root`` is a bare tmp dir: no git history, no ADR or changelog files."""
    files = FileBundle(target=root, target_kind=kind, package_name="somepkg", root=root)
    return IngestionState(files=files, chunks=ChunkBundle(trees=(_marked_module(),)))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(TargetKind))
@pytest.mark.parametrize("structuring", [True, False], ids=["structuring-on", "structuring-off"])
@pytest.mark.parametrize("capture", [True, False], ids=["capture-on", "capture-off"])
async def test_the_predicate_matches_when_the_capture_pipeline_consults_the_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture: bool,
    structuring: bool,
    kind: TargetKind,
) -> None:
    builder = RecordingLlmClientBuilder()
    monkeypatch.setattr(llm_clients, "build_llm_client", builder)
    config = DecisionCaptureConfig.model_validate(
        {"enabled": capture, "llm_structuring": {"enabled": structuring}}
    )
    app_config = SimpleNamespace(decision_capture=config, llm=LlmConfig())
    pipeline = CaptureDecisionsPipeline.from_dict({}, SimpleNamespace(app_config=app_config))

    await pipeline.run(_state(kind, tmp_path))

    assert (builder.chat_calls > 0) is llm_structuring_applies(config, kind)
