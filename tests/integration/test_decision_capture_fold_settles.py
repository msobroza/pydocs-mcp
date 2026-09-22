"""A changed ``decision_capture`` re-extracts ONCE and then settles (#263).

``decision_capture`` parameterizes the decision miner, and every mined decision
becomes a chunk — but those settings reached no cache key. ``content_hash`` runs
after ``capture_decisions`` and ``embed_chunks`` in ``pipelines/ingestion.yaml``,
so a "cache hit" skips the database write, not the mining and not the
embedding. Mining runs on every pass regardless; what the changed knob added was
re-embedding the changed decision chunks and discarding them on every pass,
forever, healed only by ``--force``.
The same loop ``extraction.chunking`` had (see test_chunk_tree_salt_settles.py).

Proven over real passes through a real composition root, with a knob that
really changes a decision chunk's text (``inline_markers.context_lines`` sizes
the evidence window around a ``# WHY:`` marker):

1. the tuned pass re-extracts and WRITES the narrower decision chunk;
2. the next tuned pass settles — no write, no embedding, stored hash frozen;
3. switching back to the shipped defaults writes once, restores the EXACT stock
   hash (a stock config folds nothing), and then settles too.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, ChunkOrigin
from pydocs_mcp.retrieval.config import AppConfig, DecisionCaptureConfig
from tests._fakes import CountingEmbedder, MockEmbedder
from tests._index_fixture import run_pass_with_embedder

_MARKER = "# WHY: retries stay bounded so a hung backend never stalls indexing"
_TUNED_CONTEXT_LINES = 1
# Body lines on EACH side of the marker (see ``project_dir``).
_LINES_AROUND_MARKER = 6


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate ``AppConfig.load()`` from the developer's own config: "stock"
    here must mean the shipped defaults, not whatever ``PYDOCS_CONFIG_PATH``,
    ``./pydocs-mcp.yaml``, ``~/.config/pydocs-mcp/config.yaml`` or a
    ``PYDOCS_DECISION_CAPTURE*`` env var says."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    for name in [name for name in os.environ if name.startswith("PYDOCS_DECISION_CAPTURE")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """One module whose ``# WHY:`` marker sits inside a window wider than the
    tuned context, so the tuned evidence text is strictly shorter."""
    root = tmp_path / "proj"
    pkg = root / "app"
    pkg.mkdir(parents=True)
    body = "\n".join(f"    step_{i} = value + {i}" for i in range(_LINES_AROUND_MARKER))
    (pkg / "retry.py").write_text(
        '"""Retry policy."""\n\n\n'
        "def attempt(value: int) -> int:\n"
        f"{body}\n    {_MARKER}\n{body.replace('step_', 'after_')}\n"
        "    return value\n"
    )
    return root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "decisions.db"
    open_index_database(path).close()
    return path


@pytest.fixture
def tuned_config(tmp_path: Path) -> AppConfig:
    overlay = tmp_path / "narrow_decision_window.yaml"
    overlay.write_text(
        f"decision_capture:\n  inline_markers:\n    context_lines: {_TUNED_CONTEXT_LINES}\n"
    )
    config = AppConfig.load(explicit_path=overlay)
    assert config.decision_capture != DecisionCaptureConfig()
    return config


def _project_hash(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT content_hash FROM packages WHERE name = ?", (PROJECT_PACKAGE_NAME,)
        ).fetchone()
    assert row is not None, "the project package was never persisted"
    return str(row[0])


def _decision_chunk_texts(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT text FROM chunks WHERE origin = ? ORDER BY text",
            (ChunkOrigin.DECISION_RECORD.value,),
        ).fetchall()
    return [str(row[0]) for row in rows]


def test_a_changed_decision_capture_knob_settles_instead_of_looping(
    db_path: Path, project_dir: Path, tuned_config: AppConfig
) -> None:
    embedder = CountingEmbedder(inner=MockEmbedder(dim=384, model_name="mock"))
    stock = AppConfig.load()

    first = run_pass_with_embedder(stock, db_path, project_dir, embedder=embedder)
    assert first.project_indexed is True
    stock_hash = _project_hash(db_path)
    stock_decisions = _decision_chunk_texts(db_path)
    assert any(_MARKER in text for text in stock_decisions), "the marker was never mined"

    # (1) the knob moves the gate: one re-extraction that persists the new
    # decision text. Before the fix this pass embedded the narrower chunk and
    # then threw it away as a cache hit.
    tuned_pass = run_pass_with_embedder(tuned_config, db_path, project_dir, embedder=embedder)
    assert tuned_pass.project_indexed is True
    tuned_hash = _project_hash(db_path)
    assert tuned_hash != stock_hash
    tuned_decisions = _decision_chunk_texts(db_path)
    assert tuned_decisions != stock_decisions
    marker_texts = [text for text in tuned_decisions if _MARKER in text]
    assert all(len(text.splitlines()) < _LINES_AROUND_MARKER for text in marker_texts)

    # (2) …and then it settles: no write, nothing embedded, the hash frozen.
    calls_before = list(embedder.calls)
    settled = run_pass_with_embedder(tuned_config, db_path, project_dir, embedder=embedder)
    assert settled.project_indexed is False
    assert embedder.calls == calls_before, "a settled pass must embed nothing"
    assert _project_hash(db_path) == tuned_hash

    # (3) back to the shipped defaults: one write that restores the exact stock
    # hash — the fold is conditional, so a stock config folds nothing — then a
    # settled pass.
    restored = run_pass_with_embedder(stock, db_path, project_dir, embedder=embedder)
    assert restored.project_indexed is True
    assert _project_hash(db_path) == stock_hash
    assert _decision_chunk_texts(db_path) == stock_decisions

    calls_before = list(embedder.calls)
    restored_settled = run_pass_with_embedder(stock, db_path, project_dir, embedder=embedder)
    assert restored_settled.project_indexed is False
    assert embedder.calls == calls_before
    assert _project_hash(db_path) == stock_hash
