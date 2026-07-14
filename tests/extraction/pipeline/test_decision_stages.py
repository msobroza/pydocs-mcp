"""Focused unit tests for the decision sub-stages (spec §D8-§D12).

Each stage is exercised in isolation so a regression pins to one stage:

* :class:`MineDecisionsStage` — the 5-source fan-out with the Jaccard merge
  folded in → ``state.decisions``; mining nothing is an identity.
* :class:`EmitDecisionChunksStage` pure transform — merged → decision-as-chunks,
  empty → identity.

The project-only + ``config.enabled`` guard lives on
:class:`CaptureDecisionsPipeline` (not on the sub-stages); guard behavior and
end-to-end runs of the composed pipeline live in
``test_capture_decisions_stage.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

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
from pydocs_mcp.extraction.pipeline.stages.decisions.mine_decisions import MineDecisionsStage
from pydocs_mcp.models import ChunkOrigin
from pydocs_mcp.project_toml import ProjectExcludes
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


# ── MineDecisionsStage: fan-out + folded merge ──────────────────────────────


async def test_mine_project_target_populates_decisions(tmp_path: Path) -> None:
    tree = _module_tree("# DECISION: use sidecar for vectors\n")
    out = await MineDecisionsStage(config=_cfg()).run(_state(trees=(tree,), root=tmp_path))
    assert len(out.decisions) == 1
    assert out.decisions[0].title == "use sidecar for vectors"
    # Mine does NOT emit — chunks/edges are downstream stages.
    assert out.chunks.chunks == ()


async def test_mine_merges_similar_titles(tmp_path: Path) -> None:
    tree = _module_tree(
        "# DECISION: use sidecar for vectors\n# DECISION: use the sidecar for vectors\n"
    )
    out = await MineDecisionsStage(config=_cfg(merge_jaccard=0.5)).run(
        _state(trees=(tree,), root=tmp_path)
    )
    # Two near-identical titles Jaccard-merge into one decision inside mine.
    assert len(out.decisions) == 1


async def test_mine_keeps_distinct_titles(tmp_path: Path) -> None:
    tree = _module_tree(
        "# DECISION: use sidecar for vectors\n# DECISION: adopt fts5 for lexical search\n"
    )
    out = await MineDecisionsStage(config=_cfg(merge_jaccard=0.5)).run(
        _state(trees=(tree,), root=tmp_path)
    )
    assert len(out.decisions) == 2


async def test_mine_nothing_found_is_identity(tmp_path: Path) -> None:
    tree = _module_tree("x = 1\n")
    state = _state(trees=(tree,), root=tmp_path)
    out = await MineDecisionsStage(config=_cfg()).run(state)
    assert out is state
    assert out.decisions == ()


async def test_mine_unknown_source_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Defensive path: a source removed after a config was written is logged +
    # skipped (via decision_source_registry.get), never raised. model_copy
    # bypasses the Literal validation that closes this at YAML load.
    cfg = _cfg().model_copy(update={"sources": ["vanished", "inline_markers"]})
    tree = _module_tree("# DECISION: keep going\n")
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        out = await MineDecisionsStage(config=cfg).run(_state(trees=(tree,), root=tmp_path))
    assert len(out.decisions) == 1
    assert any("not registered" in r.getMessage() for r in caplog.records)


def test_mine_bare_stage_defaults_config() -> None:
    # A bare MineDecisionsStage() default-constructs a fresh config per instance.
    stage = MineDecisionsStage()
    assert isinstance(stage.config, DecisionCaptureConfig)
    assert stage.config.enabled is True
    assert stage.config is not MineDecisionsStage().config


def test_state_has_no_decisions_raw_scratch_field(tmp_path: Path) -> None:
    # The mine→merge handoff is folded into MineDecisionsStage; the raw
    # per-source scratch field no longer exists on IngestionState.
    state = _state(root=tmp_path)
    assert not hasattr(state, "decisions_raw")


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


# ── MineDecisionsStage × effective excludes (spec 7.8, D8; AC-21) ───────────


def _excluded_state(root: Path, excludes: ProjectExcludes) -> IngestionState:
    return IngestionState(
        files=FileBundle(
            target=root,
            target_kind=TargetKind.PROJECT,
            package_name="__project__",
            root=root,
            effective_excludes=excludes,
        ),
        chunks=ChunkBundle(trees=()),
    )


async def test_build_context_carries_effective_excludes(tmp_path: Path) -> None:
    excludes = ProjectExcludes(names=frozenset({"docs"}), anchored=frozenset())
    stage = MineDecisionsStage(config=_cfg())
    ctx = await stage._build_context(_excluded_state(tmp_path, excludes), tmp_path)
    # The stage threads the SAME object the discovery walk pruned against —
    # no re-derivation, no second TOML read (spec 9.1).
    assert ctx.excluded is excludes


async def test_mine_run_skips_adr_files_under_excluded_dir(tmp_path: Path) -> None:
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-x.md").write_text("# 1. Use SQLite\n\nStatus: Accepted\n\n## Decision\nYes.\n")
    excludes = ProjectExcludes(names=frozenset({"docs"}), anchored=frozenset())
    out = await MineDecisionsStage(config=_cfg(sources=["adr_files"])).run(
        _excluded_state(tmp_path, excludes)
    )
    # An excluded ADR directory yields no decision records at all (AC-21) —
    # so nothing downstream (get_why / search --kind decision / GOVERNS) can
    # resurface it.
    assert out.decisions == ()


async def test_mine_run_default_bundle_still_mines_adr_files(tmp_path: Path) -> None:
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-x.md").write_text("# 1. Use SQLite\n\nStatus: Accepted\n\n## Decision\nYes.\n")
    out = await MineDecisionsStage(config=_cfg(sources=["adr_files"])).run(
        _state(trees=(), root=tmp_path)
    )
    assert len(out.decisions) == 1  # empty-default bundle → identical to today
