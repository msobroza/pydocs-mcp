"""The chunk-tree salt — what makes a chunker change reach an existing index.

The package content hash folds path+mtime, the exclusion fingerprint, the
project-only module-id token, the loadable-grammar fingerprint and the identity
salt. None of them carries anything about the CHUNKERS, so a change to what the
chunkers emit could not invalidate a cached package: issues #246 item 1 (#257)
and item 4 (#258) both changed chunk trees and both had to tell operators to
touch the files or run ``index . --force``.

Two halves, one token:

- ``CHUNK_TREE_RULE_VERSION`` — bumped by hand for a chunker change that is not
  visible in the declarative data below (#258's row-splitting fix is the shape);
- the language-spec digest — derived from ``LANGUAGE_SPECS``, so a tree-sitter
  query edit (#257's shape) invalidates on its own and cannot be forgotten.
  Every spec field reaches it, not only the query: the tests below vary the
  grammar accessor and the item-kind map too.

Both are varied through the seams rather than asserted against literals, so this
suite says nothing about the token's current value or which grammar wheels are
installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.model import NodeKind
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ContentHashStage
from pydocs_mcp.extraction.pipeline.stages import content_hash as stage_module
from pydocs_mcp.extraction.strategies.chunkers import chunk_tree_rules
from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import LANGUAGE_SPECS

_PIPELINE_HASH = "PIPE-1"


@pytest.fixture
def one_file(tmp_path: Path) -> Path:
    """Written ONCE per test: ``hash_files`` folds each path's mtime, so a
    rewrite between two runs would move the base digest and make every
    "the hash changed" assertion pass for the wrong reason."""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    return f


def _state(f: Path, kind: TargetKind = TargetKind.PROJECT) -> IngestionState:
    bundle = FileBundle(
        target=f.parent,
        target_kind=kind,
        package_name="__project__" if kind is TargetKind.PROJECT else "somedep",
        paths=(str(f),),
    )
    return IngestionState(files=bundle)


async def _hash(state: IngestionState) -> str:
    stage = ContentHashStage(pipeline_hash=_PIPELINE_HASH)
    return (await stage.run(state)).files.content_hash


@pytest.fixture
def pinned_grammars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold the grammar fingerprint still so a moved hash can only be the
    chunk-tree salt — the two folds are neighbours and could swallow each
    other."""
    monkeypatch.setattr(stage_module, "_grammar_fingerprint", lambda: ".rs,.ts")


def _respec(monkeypatch: pytest.MonkeyPatch, ext: str, **changes: object) -> None:
    """Replace one LANGUAGE_SPECS entry's field(s), leaving the rest intact."""
    fields = ("grammar_module", "accessor", "query", "kinds")
    current = dict(zip(fields, LANGUAGE_SPECS[ext], strict=True))
    current.update(changes)
    patched = dict(LANGUAGE_SPECS) | {ext: tuple(current[f] for f in fields)}
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.chunkers.multilang_queries.LANGUAGE_SPECS", patched
    )


# ── the derived half: a query change invalidates on its own ───────────────


@pytest.mark.asyncio
async def test_package_hash_moves_when_a_tree_sitter_query_changes(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_grammars: None
) -> None:
    """Issue #246 item 1's shape: `.ts` gained `export_statement` patterns, so
    every exported declaration became a symbol. Without this fold a cached
    package keeps the trees the OLD query produced, forever."""
    baseline = await _hash(_state(one_file))

    _respec(monkeypatch, ".ts", query="(program (class_declaration) @item)")

    assert await _hash(_state(one_file)) != baseline


@pytest.mark.asyncio
async def test_package_hash_moves_when_an_item_kind_remaps(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_grammars: None
) -> None:
    """A query can stay byte-identical while the node it captures starts
    persisting as a different NodeKind — same trees, different kinds, and
    ``get_symbol`` / ``parent_rollup`` read the kind."""
    baseline = await _hash(_state(one_file))

    remapped = {t: NodeKind.FUNCTION for t in LANGUAGE_SPECS[".java"][3]}
    _respec(monkeypatch, ".java", kinds=remapped)

    assert await _hash(_state(one_file)) != baseline


@pytest.mark.asyncio
async def test_package_hash_moves_when_a_grammar_module_is_swapped(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_grammars: None
) -> None:
    """The loadable-grammar fingerprint records WHICH EXTENSIONS load, not which
    wheel or accessor produced them, so a dialect swap (`.tsx` served by the
    `.ts` accessor) would otherwise reparse every file with no hash movement."""
    baseline = await _hash(_state(one_file))

    _respec(monkeypatch, ".tsx", accessor="language_typescript")

    assert await _hash(_state(one_file)) != baseline


def test_the_spec_digest_is_blind_to_a_renderer_refactor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_esm_top_level_query`` renders the JS/TS queries from a declaration
    tuple; #257 proved the rendered text was byte-identical to the literals it
    replaced. A refactor that keeps the text must NOT cost every user a
    re-extraction, so the digest reads the rendered strings, never the source
    that produced them.

    Stood in for by a freshly built table whose strings are equal but not the
    same objects — which is exactly what a different renderer produces.
    """
    before = chunk_tree_rules.chunk_tree_fingerprint()

    rerendered = {
        ext: (module, accessor, "".join(query), dict(kinds))
        for ext, (module, accessor, query, kinds) in LANGUAGE_SPECS.items()
    }
    assert all(rerendered[ext][2] is not LANGUAGE_SPECS[ext][2] for ext in LANGUAGE_SPECS), (
        "the stand-in has to be a NEW string, or it proves nothing"
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.chunkers.multilang_queries.LANGUAGE_SPECS", rerendered
    )

    assert chunk_tree_rules.chunk_tree_fingerprint() == before


# ── the hand-bumped half: everything the data cannot see ──────────────────


@pytest.mark.asyncio
async def test_package_hash_moves_when_the_rule_version_is_bumped(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_grammars: None
) -> None:
    """Issue #246 item 4's shape: the row splitter changed inside the chunker,
    with every query byte-identical. Only a hand bump can reach that."""
    baseline = await _hash(_state(one_file))

    monkeypatch.setattr(chunk_tree_rules, "CHUNK_TREE_RULE_VERSION", "chunk-trees/99")

    assert await _hash(_state(one_file)) != baseline


# ── scope: every package, not just the project ────────────────────────────


@pytest.mark.asyncio
async def test_a_dependency_hash_folds_the_salt_too(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_grammars: None
) -> None:
    """Unlike the project-only module-id token: dependencies are chunked by the
    same chunkers, and code extensions reach the dependency scope through YAML
    (``discovery.dependency.include_extensions``, ADR 0022)."""
    baseline = await _hash(_state(one_file, TargetKind.DEPENDENCY))

    monkeypatch.setattr(chunk_tree_rules, "CHUNK_TREE_RULE_VERSION", "chunk-trees/99")

    assert await _hash(_state(one_file, TargetKind.DEPENDENCY)) != baseline


def test_the_fingerprint_carries_both_halves() -> None:
    """One token, so ``ContentHashStage`` grows one fold rather than two; the
    hand-bumped half must be readable in it, or a bump could be silently
    swallowed by a digest collision."""
    assert chunk_tree_rules.CHUNK_TREE_RULE_VERSION in chunk_tree_rules.chunk_tree_fingerprint()
