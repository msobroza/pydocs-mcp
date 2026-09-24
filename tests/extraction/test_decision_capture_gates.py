"""The capture gates say exactly where decision work runs (#347, #346).

The content hash folds a setting wherever one of these predicates holds, so
each predicate must agree with the decision-capture stages it describes:

- ``decision_mining_applies`` — where ``capture_decisions`` mines at all. The
  decision token folds into a DEPENDENCY hash exactly there (#346).
- ``llm_structuring_applies`` — where the structuring LLM runs. Its identity
  rides inside the decision token exactly there (#347).

If a predicate said "runs" where the work is skipped, a knob change would
re-extract for nothing; if it said "skipped" where the work runs, the new output
would be discarded as a cache hit on every pass — the loop the folds exist to
end. So this suite pins each predicate against what the real composite DOES,
over every combination of its switches and the two target kinds, with the LLM
builder replaced by a recording fake.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pydocs_mcp.extraction.decisions.capture_gates import (
    decision_mining_applies,
    llm_structuring_applies,
)
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


def _pipeline(config: DecisionCaptureConfig) -> CaptureDecisionsPipeline:
    app_config = SimpleNamespace(decision_capture=config, llm=LlmConfig())
    return CaptureDecisionsPipeline.from_dict({}, SimpleNamespace(app_config=app_config))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(TargetKind))
@pytest.mark.parametrize("include_deps", [True, False], ids=["deps-on", "deps-off"])
@pytest.mark.parametrize("capture", [True, False], ids=["capture-on", "capture-off"])
async def test_the_mining_predicate_matches_when_the_capture_pipeline_mines(
    tmp_path: Path, capture: bool, include_deps: bool, kind: TargetKind
) -> None:
    """``include_deps`` opens dependency mining (#346); ``enabled`` still closes
    all of it."""
    config = DecisionCaptureConfig.model_validate(
        {"enabled": capture, "include_deps": include_deps}
    )

    out = await _pipeline(config).run(_state(kind, tmp_path))

    assert bool(out.decisions) is decision_mining_applies(config, kind)


@pytest.mark.parametrize(
    ("overlay", "kind", "expected"),
    [
        ({}, TargetKind.PROJECT, True),
        ({}, TargetKind.DEPENDENCY, False),
        ({"include_deps": True}, TargetKind.DEPENDENCY, True),
        ({"enabled": False, "include_deps": True}, TargetKind.DEPENDENCY, False),
        ({"enabled": False}, TargetKind.PROJECT, False),
    ],
    ids=["stock-project", "stock-dependency", "deps-on", "deps-on-capture-off", "capture-off"],
)
def test_the_mining_predicate_reads_enabled_and_include_deps(
    overlay: dict[str, bool], kind: TargetKind, expected: bool
) -> None:
    config = DecisionCaptureConfig.model_validate(overlay)

    assert decision_mining_applies(config, kind) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(TargetKind))
@pytest.mark.parametrize("include_deps", [True, False], ids=["deps-on", "deps-off"])
@pytest.mark.parametrize("structuring", [True, False], ids=["structuring-on", "structuring-off"])
@pytest.mark.parametrize("capture", [True, False], ids=["capture-on", "capture-off"])
async def test_the_predicate_matches_when_the_capture_pipeline_consults_the_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture: bool,
    structuring: bool,
    include_deps: bool,
    kind: TargetKind,
) -> None:
    """``include_deps`` mines dependencies but never structures them (#346), so
    the LLM stays project-only whichever way it is set."""
    builder = RecordingLlmClientBuilder()
    monkeypatch.setattr(llm_clients, "build_llm_client", builder)
    config = DecisionCaptureConfig.model_validate(
        {
            "enabled": capture,
            "include_deps": include_deps,
            "llm_structuring": {"enabled": structuring},
        }
    )

    await _pipeline(config).run(_state(kind, tmp_path))

    assert (builder.chat_calls > 0) is llm_structuring_applies(config, kind)
