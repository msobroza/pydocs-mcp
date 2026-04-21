"""Unit tests for ``extraction/wiring.py`` + ``extraction/chunk_extractor.py`` (Task 22).

Pins spec §7.3 + §7.4 + AC #33 + AC #19:
- ``load_ingestion_pipeline`` builds a 6-stage ``IngestionPipeline`` from the
  shipped ``presets/ingestion.yaml``.
- Paths outside the allowlist raise ``ValueError`` — the same allowlist logic
  as sub-PR #2 retrieval pipelines (reused via
  ``retrieval.config._resolve_pipeline_path``).
- Malformed YAML (no ``stages`` key) raises ``ValueError``.
- ``build_ingestion_pipeline`` falls back to the bundled preset when
  ``cfg.extraction.ingestion.pipeline_path`` is ``None``; user overrides
  resolve through the allowlist.
- ``PipelineChunkExtractor`` delegates to a single ``IngestionPipeline`` for
  both project and dependency modes, producing a 3-tuple
  ``(chunks, trees, package)`` per sub-PR #4 AC #19; missing ``state.package``
  raises ``RuntimeError`` so mis-configured pipelines fail loud.
- Public API surface (``__all__``) is importable end-to-end.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from pydocs_mcp.extraction.chunk_extractor import PipelineChunkExtractor
from pydocs_mcp.extraction.pipeline import (
    IngestionPipeline,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.stages import (
    ChunkingStage,
    ContentHashStage,
    FileDiscoveryStage,
    FileReadStage,
    FlattenStage,
    PackageBuildStage,
)
from pydocs_mcp.extraction.wiring import (
    build_ingestion_pipeline,
    load_ingestion_pipeline,
)
from pydocs_mcp.models import Package, PackageOrigin
from pydocs_mcp.retrieval.config import AppConfig


# ── Helpers ────────────────────────────────────────────────────────────────

_PRESETS_DIR = Path(__file__).resolve().parent.parent.parent / "python" / "pydocs_mcp" / "presets"
_BUNDLED_INGESTION = _PRESETS_DIR / "ingestion.yaml"


def _app_config() -> AppConfig:
    """Baseline AppConfig from the shipped default YAML — all extraction fields
    get their defaults, ``extraction.ingestion.pipeline_path`` is ``None``."""
    return AppConfig.load()


# ── load_ingestion_pipeline ────────────────────────────────────────────────

def test_load_ingestion_pipeline_success() -> None:
    """Shipped preset loads into a 6-stage pipeline of the expected types."""
    cfg = _app_config()
    pipeline = load_ingestion_pipeline(_BUNDLED_INGESTION, cfg)
    assert isinstance(pipeline, IngestionPipeline)
    assert len(pipeline.stages) == 6
    expected_types = [
        FileDiscoveryStage, FileReadStage, ChunkingStage,
        FlattenStage, ContentHashStage, PackageBuildStage,
    ]
    for stage, exp in zip(pipeline.stages, expected_types, strict=True):
        assert isinstance(stage, exp)


def test_load_ingestion_pipeline_rejects_arbitrary_path(tmp_path: Path) -> None:
    """A YAML that's neither inside shipped presets/ nor next to the user
    config must be rejected — reuses the retrieval AC #33 allowlist."""
    cfg = _app_config()
    # tmp_path is outside both the shipped presets dir and the (None) user
    # config dir, so _resolve_pipeline_path raises ValueError.
    stray = tmp_path / "ingestion.yaml"
    stray.write_text("name: x\nstages: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="pipeline_path must be inside"):
        load_ingestion_pipeline(stray, cfg)


def test_load_ingestion_pipeline_missing_stages_key_raises(tmp_path: Path, monkeypatch) -> None:
    """A YAML without a ``stages`` key is a malformed pipeline spec."""
    # Place the bad YAML INSIDE the shipped presets dir so the allowlist
    # accepts it and we genuinely hit the stages-key validation.
    bad = _PRESETS_DIR / "__test_bad_ingestion.yaml"
    bad.write_text("name: bad\n", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="invalid ingestion pipeline YAML"):
            load_ingestion_pipeline(bad, _app_config())
    finally:
        bad.unlink()


# ── build_ingestion_pipeline ───────────────────────────────────────────────

def test_build_ingestion_pipeline_uses_bundled_preset_when_config_none() -> None:
    """Default config has ``pipeline_path=None`` → build falls back to shipped YAML."""
    cfg = _app_config()
    assert cfg.extraction.ingestion.pipeline_path is None
    pipeline = build_ingestion_pipeline(cfg)
    assert isinstance(pipeline, IngestionPipeline)
    assert len(pipeline.stages) == 6


def test_build_ingestion_pipeline_uses_custom_path_when_provided() -> None:
    """Custom path inside the allowlist (shipped presets dir) is honoured."""
    cfg = _app_config()
    # Must live inside an allowed root — the shipped presets dir is the
    # simplest option that doesn't need a user-config file.
    custom = _PRESETS_DIR / "__test_custom_ingestion.yaml"
    custom.write_text(
        "name: custom\nstages:\n  - {type: file_discovery}\n  - {type: file_read}\n",
        encoding="utf-8",
    )
    try:
        cfg.extraction.ingestion.pipeline_path = custom
        pipeline = build_ingestion_pipeline(cfg)
        assert len(pipeline.stages) == 2
        assert isinstance(pipeline.stages[0], FileDiscoveryStage)
        assert isinstance(pipeline.stages[1], FileReadStage)
    finally:
        custom.unlink()


# ── PipelineChunkExtractor ─────────────────────────────────────────────────

@dataclass(frozen=True)
class _FakePipeline:
    """Fake IngestionPipeline — records the state it received and returns a
    canned one without running any real stages."""

    canned_state: IngestionState

    async def run(self, state: IngestionState) -> IngestionState:
        # Preserve the caller's target / kind / package_name so tests can
        # assert on dispatching behaviour, but layer the canned outputs on top.
        return replace(
            self.canned_state,
            target=state.target,
            target_kind=state.target_kind,
            package_name=state.package_name,
        )


def _fake_package(name: str = "__project__") -> Package:
    return Package(
        name=name, version="1.0", summary="", homepage="",
        dependencies=(), content_hash="h", origin=PackageOrigin.PROJECT,
    )


@pytest.mark.asyncio
async def test_pipeline_chunk_extractor_extract_from_project(tmp_path: Path) -> None:
    """PROJECT target_kind is wired through and package is unwrapped."""
    pkg = _fake_package("__project__")
    fake = _FakePipeline(canned_state=IngestionState(
        target=tmp_path, target_kind=TargetKind.PROJECT,
        chunks=(), trees=(), package=pkg,
    ))
    extractor = PipelineChunkExtractor(pipeline=fake)  # type: ignore[arg-type]
    chunks, trees, package = await extractor.extract_from_project(tmp_path)
    assert chunks == ()
    assert trees == ()
    assert package is pkg


@pytest.mark.asyncio
async def test_pipeline_chunk_extractor_extract_from_dependency() -> None:
    """DEPENDENCY target_kind: dep name normalised into package_name."""
    pkg = _fake_package("my_dep")

    captured: dict[str, IngestionState] = {}

    @dataclass(frozen=True)
    class _CaptureFake:
        async def run(self, state: IngestionState) -> IngestionState:
            captured["in"] = state
            return replace(state, package=pkg)

    extractor = PipelineChunkExtractor(pipeline=_CaptureFake())  # type: ignore[arg-type]
    _chunks, _trees, package = await extractor.extract_from_dependency("My-Dep")
    assert package is pkg
    assert captured["in"].target == "My-Dep"
    assert captured["in"].target_kind is TargetKind.DEPENDENCY
    # ``normalize_package_name`` lowercases + swaps '-' for '_'.
    assert captured["in"].package_name == "my_dep"


@pytest.mark.asyncio
async def test_pipeline_chunk_extractor_raises_if_package_missing(tmp_path: Path) -> None:
    """A pipeline that forgets ``package_build`` leaves ``state.package=None``
    — the extractor must fail loud rather than return a malformed tuple."""
    fake = _FakePipeline(canned_state=IngestionState(
        target=tmp_path, target_kind=TargetKind.PROJECT, package=None,
    ))
    extractor = PipelineChunkExtractor(pipeline=fake)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="did not populate state.package"):
        await extractor.extract_from_project(tmp_path)


# ── Public API smoke ───────────────────────────────────────────────────────

def test_public_api_smoke() -> None:
    """Every name in ``extraction.__all__`` is importable from the subpackage."""
    import pydocs_mcp.extraction as mod

    expected = {
        "AstPythonChunker", "HeadingMarkdownChunker", "NotebookChunker",
        "ProjectFileDiscoverer", "DependencyFileDiscoverer",
        "AstMemberExtractor", "InspectMemberExtractor",
        "StaticDependencyResolver",
        "IngestionPipeline", "IngestionState", "IngestionStage", "TargetKind",
        "FileDiscoveryStage", "FileReadStage", "ChunkingStage",
        "FlattenStage", "ContentHashStage", "PackageBuildStage",
        "DocumentNode", "NodeKind", "STRUCTURAL_ONLY_KINDS",
        "PipelineChunkExtractor",
        "build_package_tree", "build_ingestion_pipeline", "load_ingestion_pipeline",
        "flatten_to_chunks",
        "stage_registry", "chunker_registry",
    }
    assert expected.issubset(set(mod.__all__))
    for name in expected:
        assert getattr(mod, name) is not None, f"{name} is None"
