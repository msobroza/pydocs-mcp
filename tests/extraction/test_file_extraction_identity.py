"""The per-file extraction identity the blob cache is keyed by (#261, #309).

``file_extraction_identity`` digests the package hash's PER-FILE salts — the
module-id rule token, the reference-capture token, the loadable-grammar salt
and the chunk-tree salt — through the very helpers ``ContentHashStage`` folds,
so the blob cache and the package gate are invalidated by the same events.
This suite pins three things: each included term moves the identity, the
per-file salts are literally the stage's own folds (in the stage's order), and
the settings the identity leaves out cannot reach it at all.

Seams are the fold-composition suite's: ``MODULE_ID_RULE_VERSION`` as bound in
the stage module, ``_grammar_fingerprint`` and ``_chunk_tree_fingerprint``.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from pydocs_mcp.extraction.config import ChunkingConfig, TextSectionConfig
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ContentHashStage
from pydocs_mcp.extraction.pipeline.stages import content_hash as stage_module
from pydocs_mcp.extraction.pipeline.stages.content_hash import file_extraction_identity
from pydocs_mcp.retrieval.config import DecisionCaptureConfig, LlmConfig, ReferenceCaptureConfig

_STOCK_CHUNKING = ChunkingConfig()
_STOCK_REFS = ReferenceCaptureConfig()


@pytest.fixture
def pinned_salts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every module-level salt held at a known value, so only the test moves one."""
    monkeypatch.setattr(stage_module, "MODULE_ID_RULE_VERSION", "package-root/1")
    monkeypatch.setattr(stage_module, "_grammar_fingerprint", lambda: ".rs,.ts")
    monkeypatch.setattr(stage_module, "_chunk_tree_fingerprint", lambda cfg: "chunk-trees/1|x")


def _identity(
    chunking: ChunkingConfig = _STOCK_CHUNKING, refs: ReferenceCaptureConfig = _STOCK_REFS
) -> str:
    return file_extraction_identity(chunking=chunking, reference_capture=refs)


@pytest.mark.usefixtures("pinned_salts")
def test_the_identity_is_a_stable_digest() -> None:
    first = _identity()
    assert first == _identity()
    assert len(first) == 16 and int(first, 16) >= 0


@pytest.mark.usefixtures("pinned_salts")
@pytest.mark.parametrize(
    "seam",
    ["MODULE_ID_RULE_VERSION", "_grammar_fingerprint", "_chunk_tree_fingerprint"],
)
def test_each_module_level_salt_moves_the_identity(
    seam: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _identity()
    moved = {
        "MODULE_ID_RULE_VERSION": "package-root/2",
        "_grammar_fingerprint": lambda: "",  # every grammar disappeared
        "_chunk_tree_fingerprint": lambda cfg: "chunk-trees/2|x",  # a chunker change
    }[seam]
    monkeypatch.setattr(stage_module, seam, moved)
    assert _identity() != before


def test_a_grammar_appearing_moves_the_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stage_module, "_grammar_fingerprint", lambda: ".rs")
    only_rust = _identity()
    monkeypatch.setattr(stage_module, "_grammar_fingerprint", lambda: ".rs,.ts")
    assert _identity() != only_rust


@pytest.mark.parametrize(
    ("chunking", "refs"),
    [
        (ChunkingConfig(text_section=TextSectionConfig(window_lines=7)), _STOCK_REFS),
        (_STOCK_CHUNKING, ReferenceCaptureConfig(kinds=("calls", "imports", "mentions"))),
        (_STOCK_CHUNKING, ReferenceCaptureConfig(enabled=False)),
    ],
    ids=["chunking-knob", "capture-kinds", "capture-disabled"],
)
def test_each_setting_it_folds_moves_the_identity(
    chunking: ChunkingConfig, refs: ReferenceCaptureConfig
) -> None:
    assert _identity(chunking, refs) != _identity()


def test_capture_kind_order_and_duplicates_do_not_move_it() -> None:
    """Capture reads ``frozenset(kinds)``, and so does the stage's token."""
    shuffled = ReferenceCaptureConfig(kinds=("inherits", "calls", "imports", "calls"))
    assert _identity(refs=shuffled) == _identity()


def test_only_the_per_file_settings_are_parameters() -> None:
    """Decision capture, member extraction and the structuring LLM shape no
    per-file artifact of a project branch, so the identity cannot even be
    handed them — a knob among them can never turn a hit into a miss."""
    assert list(inspect.signature(file_extraction_identity).parameters) == [
        "chunking",
        "reference_capture",
    ]


@pytest.mark.usefixtures("pinned_salts")
def test_the_per_file_salts_are_the_stage_s_own_folds_in_its_order(tmp_path: Path) -> None:
    """Drift guard: every per-file salt is one the stage folds, in the stage's
    order — so adding a fold to one list and not the other fails here."""
    refs = ReferenceCaptureConfig(kinds=("calls", "mentions"))
    stage = ContentHashStage(
        pipeline_hash="P",
        decision_capture=DecisionCaptureConfig(merge_jaccard=0.5),
        llm=LlmConfig(model_name="other"),
        reference_capture=refs,
    )
    bundle = FileBundle(target=tmp_path, target_kind=TargetKind.PROJECT, package_name="__project__")
    stage_salts = [s for s in stage._ordered_salts(IngestionState(files=bundle)) if s]
    per_file = list(stage_module._file_extraction_salts(TargetKind.PROJECT, _STOCK_CHUNKING, refs))
    assert all(s is not None for s in per_file)
    positions = [stage_salts.index(s) for s in per_file]
    assert positions == sorted(positions)
    assert len(stage_salts) == len(per_file) + 2  # + the decision token + the identity salt
