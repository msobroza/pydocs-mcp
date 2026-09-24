"""Focused unit tests for the decision sub-stages (spec §D8-§D12).

Each stage is exercised in isolation so a regression pins to one stage:

* :class:`MineDecisionsStage` — the 5-source fan-out with the Jaccard merge
  folded in → ``state.decisions``; mining nothing is an identity. For a
  dependency target it runs only the tree-reading source and never reads git.
* :class:`EmitDecisionChunksStage` pure transform — merged → decision-as-chunks,
  empty → identity.

The mining gate (``decision_mining_applies``) lives on
:class:`CaptureDecisionsPipeline` (not on the sub-stages); guard behavior and
end-to-end runs of the composed pipeline live in
``test_capture_decisions_stage.py``.
"""

from __future__ import annotations

import json
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
from tests._fakes import RecordingGitLogReader


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
    package_name: str = "__project__",
) -> IngestionState:
    return IngestionState(
        files=FileBundle(
            target=root,
            target_kind=target_kind,
            package_name=package_name,
            root=root,
        ),
        chunks=ChunkBundle(trees=trees),
        decisions=decisions,
    )


def _dependency_state(root: Path, *trees: DocumentNode) -> IngestionState:
    """A dependency ``somedep`` whose discovery root is ``root``."""
    return _state(trees=trees, target_kind=TargetKind.DEPENDENCY, root=root, package_name="somedep")


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


async def test_marker_locator_counts_chunk_rows_not_splitlines_breaks(tmp_path: Path) -> None:
    """A form feed inside chunk text is a character of its line, not a row: the
    tree-sitter chunker keeps it that way (issue #246 item 4), so the locator
    must count ``\\n`` only — ``splitlines()`` placed the marker a line late."""
    tree = _module_tree("# header\x0c note\n\n# DECISION: keep u8\n")
    out = await MineDecisionsStage(config=_cfg()).run(_state(trees=(tree,), root=tmp_path))
    (decision,) = out.decisions
    assert decision.evidence[0].locator == "pkg/mod.py:3"


async def test_marker_evidence_on_crlf_raw_module_text_matches_lf(tmp_path: Path) -> None:
    """Raw-content MODULE nodes (markerless text/config, headingless markdown,
    the SyntaxError fallback) carry the file's own CRLF bytes; the evidence
    window must strip the `\\r` exactly as `splitlines()` did, or the evidence
    hash — and the decision chunk's content hash — would move on every CRLF
    checkout (issue #246 item 4)."""
    stage = MineDecisionsStage(config=_cfg())
    lf = await stage.run(
        _state(trees=(_module_tree("# DECISION: keep u8\nbody\n"),), root=tmp_path)
    )
    crlf = await stage.run(
        _state(trees=(_module_tree("# DECISION: keep u8\r\nbody\r\n"),), root=tmp_path)
    )
    assert crlf.decisions[0].evidence == lf.decisions[0].evidence


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


# ── MineDecisionsStage × dependency targets (#346) ──────────────────────────
#
# A dependency's ``state.files.root`` is the SHARED site-packages directory, so
# only a source that reads the dependency's own trees attributes correctly.


def _write_root_level_decision_files(root: Path) -> None:
    """An ADR and a changelog at ``root`` — what a project mines, and what a
    dependency must NOT mine from the site-packages root every dist shares."""
    adr = root / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-x.md").write_text("# 1. Use SQLite\n\nStatus: Accepted\n\n## Decision\nYes.\n")
    (root / "CHANGELOG.md").write_text("## 1.0\n\n- Switched to SQLite because it is simpler.\n")


def _evidence_sources(state: IngestionState) -> set[str]:
    return {evidence.source for decision in state.decisions for evidence in decision.evidence}


async def test_a_dependency_target_never_reads_git(tmp_path: Path) -> None:
    """``git -C <site-packages> log`` walks up to whatever repository encloses a
    project-local venv, so a dependency would mine the PROJECT's commits."""
    reader = RecordingGitLogReader()
    stage = MineDecisionsStage(
        config=DecisionCaptureConfig(include_deps=True), git_log_reader=reader
    )

    await stage.run(_dependency_state(tmp_path, _module_tree("# WHY: x\n")))

    assert reader.calls == []


async def test_a_project_target_reads_the_bounded_git_log_once(tmp_path: Path) -> None:
    """The control: the injected reader is the one the project path calls."""
    reader = RecordingGitLogReader()
    config = DecisionCaptureConfig()

    await MineDecisionsStage(config=config, git_log_reader=reader).run(_state(root=tmp_path))

    bounds = config.commit_messages
    assert reader.calls == [(tmp_path, bounds.max_commits, bounds.timeout_seconds)]


async def test_a_dependency_target_runs_only_the_inline_markers_source(tmp_path: Path) -> None:
    _write_root_level_decision_files(tmp_path)
    trees = (_module_tree("# DECISION: use sidecar for vectors\n"),)
    stage = MineDecisionsStage(
        config=DecisionCaptureConfig(include_deps=True), git_log_reader=RecordingGitLogReader()
    )

    dependency = await stage.run(_dependency_state(tmp_path, *trees))
    project = await stage.run(_state(trees=trees, root=tmp_path))

    assert _evidence_sources(dependency) == {"inline_markers"}
    assert {"adr_files", "inline_markers"} <= _evidence_sources(project)


async def test_a_dependency_logs_the_sources_it_skips(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    stage = MineDecisionsStage(
        config=DecisionCaptureConfig(include_deps=True), git_log_reader=RecordingGitLogReader()
    )

    with caplog.at_level(logging.DEBUG, logger="pydocs-mcp"):
        await stage.run(_dependency_state(tmp_path))

    (event,) = (
        json.loads(record.getMessage())
        for record in caplog.records
        if "dependency_decision_sources_skipped" in record.getMessage()
    )
    assert event == {
        "event": "dependency_decision_sources_skipped",
        "package": "somedep",
        "skipped": ["adr_files", "commit_messages", "changelog", "docs_prose"],
    }


async def test_a_dependency_with_no_tree_source_configured_mines_nothing(tmp_path: Path) -> None:
    _write_root_level_decision_files(tmp_path)
    config = DecisionCaptureConfig(include_deps=True, sources=["adr_files", "changelog"])
    state = _dependency_state(tmp_path, _module_tree("# WHY: x\n"))

    out = await MineDecisionsStage(config=config, git_log_reader=RecordingGitLogReader()).run(state)

    assert out is state
