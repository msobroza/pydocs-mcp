"""The loadable-grammar salt on the package content hash (analyzers spec §8.2,
D9). The hash must flip on BOTH grammar-availability transitions, fold
unconditionally (an empty fingerprint is distinguishable from "not folded"),
stay stable within one state, and wrap the exclusion fold rather than replace
it. That is what rescues a package whose graph was indexed empty while its
grammars were unloadable (AC-32, AC-33).

Gating is per test: the blocked-grammar cases block ``tree_sitter`` themselves
and must pass where no grammar is installed."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ContentHashStage
from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import MULTILANG_EXTENSIONS
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
    loadable_grammar_fingerprint,
)
from pydocs_mcp.project_toml import (
    EMPTY_PROJECT_EXCLUDES,
    ProjectExcludes,
    exclusion_fingerprint,
)
from tests.extraction._content_hash_oracle import digest_fold, grammar_folded, raw_hash_files

_requires_rust_grammar = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter") is None
    or importlib.util.find_spec("tree_sitter_rust") is None,
    reason="needs tree-sitter + the Rust grammar wheel",
)


@pytest.fixture(autouse=True)
def _clean_caches() -> Iterator[None]:
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


def _block_module(monkeypatch: pytest.MonkeyPatch, module_name: str) -> None:
    """Make ``module_name`` unimportable, then drop the memoized probes."""
    monkeypatch.setitem(sys.modules, module_name, None)
    _reset_multilang_caches()


def _state(
    tmp_path: Path, f: Path, excludes: ProjectExcludes = EMPTY_PROJECT_EXCLUDES
) -> IngestionState:
    return IngestionState(
        files=FileBundle(
            target=tmp_path,
            target_kind=TargetKind.PROJECT,
            paths=(str(f),),
            effective_excludes=excludes,
        ),
    )


def _one_file(tmp_path: Path) -> Path:
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    return f


# ── loadable_grammar_fingerprint ──────────────────────────────────────────


@_requires_rust_grammar
def test_fingerprint_lists_loadable_extensions_sorted() -> None:
    entries = loadable_grammar_fingerprint().split(",")
    assert ".rs" in entries
    assert entries == sorted(entries)
    assert set(entries) <= set(MULTILANG_EXTENSIONS)


def test_fingerprint_empty_when_grammars_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_module(monkeypatch, "tree_sitter")
    assert loadable_grammar_fingerprint() == ""


@_requires_rust_grammar
def test_fingerprint_drops_only_the_blocked_grammar(monkeypatch: pytest.MonkeyPatch) -> None:
    full = loadable_grammar_fingerprint().split(",")
    _block_module(monkeypatch, "tree_sitter_rust")
    assert loadable_grammar_fingerprint().split(",") == [e for e in full if e != ".rs"]


# ── AC-32: the fold ───────────────────────────────────────────────────────


@_requires_rust_grammar
@pytest.mark.asyncio
async def test_ac32_hash_differs_across_grammar_states_and_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = _one_file(tmp_path)
    stage = ContentHashStage()

    with_grammars = (await stage.run(_state(tmp_path, f))).files.content_hash
    assert (await stage.run(_state(tmp_path, f))).files.content_hash == with_grammars
    assert with_grammars == grammar_folded(raw_hash_files([str(f)]))

    _block_module(monkeypatch, "tree_sitter")
    blocked = (await stage.run(_state(tmp_path, f))).files.content_hash
    assert (await stage.run(_state(tmp_path, f))).files.content_hash == blocked
    assert blocked != with_grammars  # flips on the transition
    # Unconditional: the EMPTY fingerprint is still folded — the blocked
    # hash never collapses to the raw, pre-salt framing.
    assert blocked == digest_fold(raw_hash_files([str(f)]), "grammars:")
    assert blocked != raw_hash_files([str(f)])


@_requires_rust_grammar
@pytest.mark.asyncio
async def test_ac32_grammar_salt_wraps_the_exclusion_fold(tmp_path: Path) -> None:
    """Order is part of the contract: the (conditional) exclusion fold runs
    first and the grammar salt wraps ITS output, so neither erases the other."""
    f = _one_file(tmp_path)
    excludes = ProjectExcludes(names=_EXCLUDED_DIRS | {"fixtures"}, anchored=frozenset())

    out = await ContentHashStage().run(_state(tmp_path, f, excludes))

    exclusion_salt = exclusion_fingerprint(excludes, _EXCLUDED_DIRS)
    assert exclusion_salt is not None
    excluded = digest_fold(raw_hash_files([str(f)]), exclusion_salt)
    assert out.files.content_hash == grammar_folded(excluded)


# ── AC-33: the re-extraction rescue ───────────────────────────────────────


@_requires_rust_grammar
@pytest.mark.asyncio
async def test_ac33_reextraction_rescue_skip_predicate_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A package indexed under blocked grammars stored hash H1; once the
    grammars load, the recomputed hash H2 != H1. Both package-level skips
    compare exactly these values — ``ProjectIndexer._project_is_cached``'s
    ``existing.content_hash != pkg.content_hash`` half and
    ``_index_one_dependency``'s ``existing.content_hash == pkg.content_hash``
    — so the package re-extracts and its references appear without any file
    touch. The end-to-end pass is pinned in
    tests/integration/test_multi_branch_p0.py."""
    f = _one_file(tmp_path)
    stage = ContentHashStage()

    with monkeypatch.context() as blocked:
        blocked.setitem(sys.modules, "tree_sitter", None)
        _reset_multilang_caches()
        stored_while_blocked = (await stage.run(_state(tmp_path, f))).files.content_hash

    _reset_multilang_caches()
    recomputed_with_grammars = (await stage.run(_state(tmp_path, f))).files.content_hash

    assert recomputed_with_grammars != stored_while_blocked
