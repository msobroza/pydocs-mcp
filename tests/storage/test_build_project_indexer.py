"""build_project_indexer — the write-side composition root (storage/factories).

The factory must hand back everything ``__main__._run_indexing`` previously
wired inline, so any consumer (CLI, watch loop, tests, a future programmatic
API) gets identical wiring without re-deriving it.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.index_metadata import IndexMetadata, read_index_metadata


@pytest.fixture(autouse=True)
def _offline_factories(monkeypatch):
    """MockEmbedder + FakeLlmClient so the factory never downloads ONNX
    weights or touches the OpenAI network (same monkeypatch seam
    tests/test_cli.py uses — the factory resolves both lazily)."""
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.retrieval import llm_clients as _llm_clients
    from tests._fakes import FakeLlmClient, MockEmbedder

    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: MockEmbedder())
    monkeypatch.setattr(
        _llm_clients,
        "build_llm_client",
        lambda cfg: FakeLlmClient(responses={}),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "myproject_abc123.db"
    open_index_database(path).close()
    return path


def test_bundle_shape_and_shared_wiring(db_path: Path) -> None:
    from pydocs_mcp.application import IndexingService, ProjectIndexer
    from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
    from pydocs_mcp.storage.factories import IndexerBundle, build_project_indexer

    config = AppConfig.load()
    bundle = build_project_indexer(config, db_path, use_inspect=True, inspect_depth=None)

    assert isinstance(bundle, IndexerBundle)
    assert isinstance(bundle.orchestrator, ProjectIndexer)
    assert isinstance(bundle.indexing_service, IndexingService)
    assert bundle.pipeline_hash == config.compute_ingestion_pipeline_hash()
    assert isinstance(bundle.uow_factory(), CompositeUnitOfWork)
    # One factory shared everywhere — the indexing transaction spans every
    # backend without per-service branching.
    assert bundle.orchestrator.uow_factory is bundle.uow_factory
    assert bundle.indexing_service.uow_factory is bundle.uow_factory
    assert bundle.orchestrator.indexing_service is bundle.indexing_service
    assert bundle.indexing_service.node_scores_enabled is config.reference_graph.node_scores.enabled


def test_bundle_is_frozen(db_path: Path) -> None:
    from pydocs_mcp.storage.factories import build_project_indexer

    bundle = build_project_indexer(AppConfig.load(), db_path, use_inspect=True, inspect_depth=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        bundle.pipeline_hash = "clobbered"  # type: ignore[misc]


def test_inspect_depth_explicit_wins(db_path: Path) -> None:
    from pydocs_mcp.extraction import InspectMemberExtractor
    from pydocs_mcp.storage.factories import build_project_indexer

    bundle = build_project_indexer(AppConfig.load(), db_path, use_inspect=True, inspect_depth=7)
    extractor = bundle.orchestrator.member_extractor
    assert isinstance(extractor, InspectMemberExtractor)
    assert extractor.depth == 7


def test_inspect_depth_none_falls_back_to_yaml(db_path: Path) -> None:
    from pydocs_mcp.storage.factories import build_project_indexer

    config = AppConfig.load()
    bundle = build_project_indexer(config, db_path, use_inspect=True, inspect_depth=None)
    assert bundle.orchestrator.member_extractor.depth == (config.extraction.members.inspect_depth)


def test_no_inspect_uses_ast_extractor(db_path: Path) -> None:
    from pydocs_mcp.extraction import AstMemberExtractor
    from pydocs_mcp.storage.factories import build_project_indexer

    bundle = build_project_indexer(AppConfig.load(), db_path, use_inspect=False, inspect_depth=None)
    assert isinstance(bundle.orchestrator.member_extractor, AstMemberExtractor)


async def test_maintenance_callables_run_against_the_db(db_path: Path) -> None:
    from pydocs_mcp.storage.factories import build_project_indexer

    bundle = build_project_indexer(AppConfig.load(), db_path, use_inspect=True, inspect_depth=None)

    # Fresh schema: chunks(embedded=1)==0 and the synthesized empty .tq
    # index==0, so the sweep is a clean no-op.
    assert await bundle.check_integrity() == []
    # FTS rebuild on an empty chunks table must not raise.
    await bundle.rebuild_fts()

    meta = IndexMetadata(
        project_name="myproject",
        project_root="/tmp/myproject",
        embedding_provider="fastembed",
        embedding_model="model-x",
        embedding_dim=384,
        pipeline_hash=bundle.pipeline_hash,
        indexed_at=123.0,
    )
    bundle.stamp_metadata(meta)
    conn = open_index_database(db_path)
    try:
        assert read_index_metadata(conn) == meta
    finally:
        conn.close()


def test_member_extractor_wired_with_yaml_project_excludes(
    db_path: Path,
    tmp_path: Path,
) -> None:
    """Spec §7.7 wiring pin (AC-26 groundwork): YAML
    ``extraction.discovery.project.exclude_dirs`` must reach
    AstMemberExtractor.scope_exclude_dirs through the REAL composition
    root — in both the static path and (via static_fallback) inspect mode.
    An implementation that adds the field but forgets the factories.py
    wiring passes the in-test-injection tests and silently never applies
    YAML excludes to member extraction."""
    from pydocs_mcp.storage.factories import build_project_indexer

    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        'extraction:\n  discovery:\n    project:\n      exclude_dirs: ["fixtures"]\n',
        encoding="utf-8",
    )
    config = AppConfig.load(explicit_path=overlay)

    static_bundle = build_project_indexer(config, db_path, use_inspect=False, inspect_depth=None)
    assert static_bundle.orchestrator.member_extractor.scope_exclude_dirs == ("fixtures",)

    inspect_bundle = build_project_indexer(config, db_path, use_inspect=True, inspect_depth=None)
    assert inspect_bundle.orchestrator.member_extractor.static_fallback.scope_exclude_dirs == (
        "fixtures",
    )


def test_bundle_wires_the_chunkers_grammar_fingerprint(db_path: Path) -> None:
    """``run_index_pass`` stamps whatever ``grammar_fingerprint`` reports; the
    bundle must hand it the SAME memo the content-hash salt reads, or the stamp
    and the hash could describe two verdicts (issue #246 item 3)."""
    from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
        loadable_grammar_fingerprint,
    )
    from pydocs_mcp.storage.factories import build_project_indexer

    bundle = build_project_indexer(AppConfig.load(), db_path, use_inspect=False, inspect_depth=None)
    assert bundle.grammar_fingerprint is loadable_grammar_fingerprint


def test_the_branch_indexer_reads_the_working_trees_extraction_key(
    db_path: Path, tmp_path: Path
) -> None:
    """#310 review: one source for the key both passes write and look up under.
    Two keys would miss every row the checkout wrote, and each pass's GC would
    sweep the other's rows as superseded."""
    from pydocs_mcp.storage.factories import build_branch_indexer, build_project_indexer

    config = AppConfig.load()
    bundle = build_project_indexer(config, db_path, use_inspect=False, inspect_depth=None)
    indexer = build_branch_indexer(config, tmp_path, bundle)
    # The bound method of the very builder the working-tree pass keys with.
    builder = bundle.orchestrator.manifest_builder
    assert indexer.current_extraction_cache_key == builder.current_extraction_cache_key


# ── #347: the member-extraction token the content-hash stage folds ────────

_TUNED_MEMBERS_YAML = (
    "extraction:\n  members:\n    inspect_depth: 3\n    members_per_module_cap: 50\n"
    "    signature_max_chars: 60\n    docstring_max_chars: 70\n"
)
_TINY_CAPS_YAML = (
    "extraction:\n  members:\n    inspect_depth: 5\n    members_per_module_cap: 1\n"
    "    signature_max_chars: 1\n    docstring_max_chars: 1\n"
)


@pytest.fixture
def _clean_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate the #347 pins' ``AppConfig.load()`` calls from the developer's
    own config: ``PYDOCS_CONFIG_PATH``, ``./pydocs-mcp.yaml``,
    ``~/.config/pydocs-mcp/config.yaml`` and ``PYDOCS_EXTRACTION*`` env vars
    (which override even the explicit overlay) would otherwise move the literal
    tokens they expect."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    for name in [name for name in os.environ if name.startswith("PYDOCS_EXTRACTION")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


def _overlay_config(tmp_path: Path, yaml_text: str) -> AppConfig:
    overlay = tmp_path / "members-overlay.yaml"
    overlay.write_text(yaml_text, encoding="utf-8")
    return AppConfig.load(explicit_path=overlay)


def _content_hash_stage_token(bundle: Any) -> str:
    from pydocs_mcp.extraction.pipeline.stages import ContentHashStage

    stages = bundle.orchestrator.chunk_extractor.pipeline.stages
    (stage,) = [s for s in stages if isinstance(s, ContentHashStage)]
    return stage.member_extraction_token


def _token_implied_by(extractor: Any) -> str:
    """The token the BUILT extractor's own settings imply — spelled out here,
    not through the production helper, so the pin cannot drift along with it."""
    from pydocs_mcp.extraction import AstMemberExtractor

    if isinstance(extractor, AstMemberExtractor):
        return "static"
    return (
        f"inspect|depth={extractor.depth}|cap={extractor.members_per_module_cap}"
        f"|sig={extractor.signature_max_chars}|doc={extractor.docstring_max_chars}"
    )


@pytest.mark.parametrize(
    ("use_inspect", "inspect_depth", "expected"),
    [
        (False, None, "static"),
        (True, 7, "inspect|depth=7|cap=50|sig=60|doc=70"),
        (True, None, "inspect|depth=3|cap=50|sig=60|doc=70"),
    ],
    ids=["no-inspect", "explicit-depth", "yaml-depth-fallback"],
)
@pytest.mark.usefixtures("_clean_config_env")
def test_content_hash_stage_folds_the_token_the_built_member_extractor_implies(
    db_path: Path,
    tmp_path: Path,
    use_inspect: bool,
    inspect_depth: int | None,
    expected: str,
) -> None:
    """Issue #347 wiring pin: the stage must fold exactly the settings the
    member extractor was built with — the CLI-resolved depth, not the YAML one,
    and never a cap that static mode ignores. A token computed from anything
    else would let the hash settle over members extracted with other settings."""
    from pydocs_mcp.storage.factories import build_project_indexer

    config = _overlay_config(tmp_path, _TUNED_MEMBERS_YAML)
    bundle = build_project_indexer(
        config, db_path, use_inspect=use_inspect, inspect_depth=inspect_depth
    )

    assert _content_hash_stage_token(bundle) == expected
    assert _token_implied_by(bundle.orchestrator.member_extractor) == expected


def _write_members_project(root: Path) -> Path:
    """A module whose members would all be visibly truncated or dropped if any
    cap applied: several members, long signatures, long docstrings."""
    pkg = root / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""App package docstring."""\n', encoding="utf-8")
    (pkg / "scoring.py").write_text(
        '"""Scoring helpers."""\n\n\n'
        "def rank(hits: list[int], *, reverse: bool = False, limit: int = 10) -> list[int]:\n"
        '    """Rank the hits by score, best first, keeping at most ``limit``."""\n'
        "    return sorted(hits, reverse=not reverse)[:limit]\n\n\n"
        "class Ranker:\n"
        '    """Stateful ranker that remembers the last ranking it produced."""\n\n'
        "    def score(self, hit: int, weight: float = 1.0) -> float:\n"
        '        """Score one hit with an optional weight multiplier."""\n'
        "        return hit * weight\n",
        encoding="utf-8",
    )
    return root


def _member_rows(members: tuple) -> list[dict]:
    return sorted((dict(m.metadata) for m in members), key=repr)


@pytest.mark.usefixtures("_clean_config_env")
async def test_project_members_ignore_every_member_extraction_setting(
    db_path: Path, tmp_path: Path
) -> None:
    """Issue #347 D1 guard: the member-extraction fold is DEPENDENCY-only
    because project members come from the same AST extractor whatever the
    mode, depth or caps (spec §9.2: the project is never live-imported). If
    this ever fails, a member setting CAN change project members, and the
    content-hash stage must start folding the member token for the project
    too — otherwise a changed setting settles over stale project members."""
    from pydocs_mcp.storage.factories import build_project_indexer

    project = _write_members_project(tmp_path / "proj")
    tiny = _overlay_config(tmp_path, _TINY_CAPS_YAML)
    inspect_bundle = build_project_indexer(tiny, db_path, use_inspect=True, inspect_depth=None)
    static_bundle = build_project_indexer(
        AppConfig.load(), db_path, use_inspect=False, inspect_depth=None
    )

    inspected = await inspect_bundle.orchestrator.member_extractor.extract_from_project(project)
    static = await static_bundle.orchestrator.member_extractor.extract_from_project(project)

    assert _member_rows(inspected) == _member_rows(static)
    # The caps above would visibly bite had they applied: more than one member
    # per module, and docstrings longer than one character.
    assert len(static) > 1
    assert any(len(str(m.metadata.get("docstring") or "")) > 1 for m in static)
