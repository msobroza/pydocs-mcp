"""A chunk-tree rule bump re-extracts ONCE and then settles.

Adding an input to the package content hash is the shape that produced the
worst cache bug this repo has had: the package gate is checked FIRST, so an
input that invalidates it without also landing in the stored hash re-extracts
and re-embeds on every pass forever, healed only by ``index --force``
(``packages.embedding_model``'s sweep, and the pipeline/tier gap fixed in the
ingestion-cache-gates work). The chunk-tree salt is a new such input, so it gets
the same proof the others did rather than the assumption that it behaves.

Three properties, all over real passes through a real composition root:

1. an unchanged bump-free pass is a cache hit — the salt is not a nonce (the
   CROSS-PROCESS half of that claim is in
   tests/extraction/test_chunk_tree_fingerprint.py, which runs a subprocess:
   every pass here shares one interpreter, so this file cannot see a salt that
   varies per process);
2. a bump re-extracts exactly once, and the pass AFTER it hits the cache again;
3. a changed ``extraction.chunking`` knob does the same — that one was a LIVE
   loop before this change, not a hypothetical.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.strategies.chunkers import chunk_tree_rules
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import CountingEmbedder, MockEmbedder
from tests._index_fixture import run_pass_with_embedder


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    pkg = root / "app"
    pkg.mkdir(parents=True)
    for i in range(4):
        (pkg / f"mod_{i}.py").write_text(
            f'"""Module {i}."""\n\n\ndef run_{i}(value: int) -> int:\n    return value + {i}\n'
        )
    return root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "salt.db"
    open_index_database(path).close()
    return path


def _project_hash(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT content_hash FROM packages WHERE name = ?", (PROJECT_PACKAGE_NAME,)
        ).fetchone()
    assert row is not None, "the project package was never persisted"
    return str(row[0])


def test_a_rule_bump_re_extracts_once_and_then_settles(
    db_path: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = AppConfig.load()
    embedder = CountingEmbedder(inner=MockEmbedder(dim=384, model_name="mock"))

    first = run_pass_with_embedder(config, db_path, project_dir, embedder=embedder)
    assert first.project_indexed is True
    baseline_hash = _project_hash(db_path)

    # (1) no bump → the stored hash is reproduced, so the package is skipped.
    second = run_pass_with_embedder(config, db_path, project_dir, embedder=embedder)
    assert second.project_indexed is False
    assert _project_hash(db_path) == baseline_hash

    # (2) bump → one re-extraction, and the NEW hash lands in the row. Patched on
    # the chunkers module rather than on the stage: the stage imports the
    # function lazily per call, which is what makes it reachable at all.
    monkeypatch.setattr(chunk_tree_rules, "CHUNK_TREE_RULE_VERSION", "chunk-trees/99")

    third = run_pass_with_embedder(config, db_path, project_dir, embedder=embedder)
    assert third.project_indexed is True
    bumped_hash = _project_hash(db_path)
    assert bumped_hash != baseline_hash

    # (3) the pass after the bump hits the cache — the re-extract-and-discard
    # loop would show up right here, as a second project_indexed=True.
    fourth = run_pass_with_embedder(config, db_path, project_dir, embedder=embedder)
    assert fourth.project_indexed is False
    assert _project_hash(db_path) == bumped_hash


def test_the_bump_re_extracts_without_re_embedding_unchanged_chunks(
    db_path: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The salt is a package-gate input only. Chunk hashes fold the TEXT, so a
    bump that does not actually move any chunk text must cost re-extraction and
    nothing else — otherwise every future chunker fix would re-embed the world.
    """
    config = AppConfig.load()
    embedder = CountingEmbedder(inner=MockEmbedder(dim=384, model_name="mock"))

    run_pass_with_embedder(config, db_path, project_dir, embedder=embedder)
    calls_after_first = list(embedder.calls)
    assert calls_after_first, "the first pass must embed something to be a baseline"

    monkeypatch.setattr(chunk_tree_rules, "CHUNK_TREE_RULE_VERSION", "chunk-trees/99")
    assert (
        run_pass_with_embedder(config, db_path, project_dir, embedder=embedder).project_indexed
        is True
    )

    assert embedder.calls == calls_after_first, (
        "a rule bump re-embedded chunks whose text never changed — the chunk-level "
        "diff is supposed to absorb that"
    )


def test_a_changed_chunker_tunable_settles_instead_of_looping(
    tmp_path: Path, db_path: Path, project_dir: Path
) -> None:
    """Regression for a LIVE loop found reviewing this change.

    ``text_section.window_lines`` and its three neighbours parameterize the
    chunkers from YAML and reached no hash at all: ``ChunkingStage.to_dict``
    returns only its type, and ``ingestion_pipeline_hash`` folds the ingestion
    PIPELINE yaml, not ``default_config.yaml`` or the user overlay.

    That was not stale trees. ``content_hash`` sits after ``embed_chunks`` in
    ``pipelines/ingestion.yaml``, so the pass re-chunked and re-embedded, then
    compared a hash that had not moved and discarded the result as a cache hit —
    on every pass, forever, healed only by ``--force``. Observed before the fix:
    four consecutive passes each embedded six texts with ``wrote_to_db=False``
    and the stored hash frozen.
    """
    (project_dir / "notes.rst").write_text("\n".join(f"line {i}" for i in range(40)) + "\n")
    embedder = CountingEmbedder(inner=MockEmbedder(dim=384, model_name="mock"))

    stock = AppConfig.load()
    assert run_pass_with_embedder(stock, db_path, project_dir, embedder=embedder).project_indexed
    baseline_hash = _project_hash(db_path)

    overlay = tmp_path / "narrow_windows.yaml"
    overlay.write_text("extraction:\n  chunking:\n    text_section:\n      window_lines: 5\n")
    tuned = AppConfig.load(explicit_path=overlay)
    assert tuned.extraction.chunking.text_section.window_lines == 5

    # The knob moves the gate: one re-extraction…
    assert run_pass_with_embedder(tuned, db_path, project_dir, embedder=embedder).project_indexed
    tuned_hash = _project_hash(db_path)
    assert tuned_hash != baseline_hash

    # …and then it settles. Before the fix this stayed True forever.
    assert not run_pass_with_embedder(
        tuned, db_path, project_dir, embedder=embedder
    ).project_indexed
    assert _project_hash(db_path) == tuned_hash
