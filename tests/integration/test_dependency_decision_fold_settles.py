"""``decision_capture.include_deps`` mines a dependency ONCE, then settles (#346).

With ``include_deps: true`` the capture pipeline mines a dependency's inline
markers, and each decision becomes a chunk plus a ``decision_records`` row under
the dependency's own package. The package hash must fold the setting exactly
where mining runs: fold it nowhere and turning the setting on is a cache hit
that never persists the rows (or, turned off, never deletes them); fold it
everywhere and every dependency re-extracts for nothing.

Proven over real passes through the real composition root, with a synthetic
installed distribution (``tests/_installed_dist_fixture.py``) whose one module
carries a ``# WHY:`` marker. No dist in the frozen dev venv carries one:

1. ``include_deps`` false → true re-extracts the dependency once, persists its
   decision row and chunk under its package, and embeds that one chunk;
2. the next pass settles: the dependency is a cache hit, nothing is embedded;
3. back to false re-extracts once more, deletes the rows, and restores the EXACT
   stock dependency hash, then settles too.

The project re-extracts on each flip as well (``include_deps`` is one more knob
in its whole-config decision digest), but its chunks do not change, so it embeds
nothing.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import ChunkOrigin
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import CountingEmbedder, MockEmbedder
from tests._index_fixture import run_pass_with_embedder
from tests._installed_dist_fixture import InstalledDistribution, installed_distribution

_DECISION = "retries stay bounded so a hung backend never stalls a caller"


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
def dependency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[InstalledDistribution]:
    """A unique name per test, so no distribution another test installed (or
    the venv ships) can answer for it."""
    name = f"pydocs_decision_dep_{uuid.uuid4().hex[:8]}"
    # Inside a function: the Python chunker's module node carries the
    # docstring, not top-level comments, so that is where a marker is mined.
    module = (
        '"""Retry policy."""\n\n\n'
        "def attempt(value: int) -> int:\n"
        f"    # WHY: {_DECISION}\n"
        "    return value\n"
    )
    with installed_distribution(
        tmp_path, monkeypatch, name=name, modules={"retry.py": module}
    ) as dist:
        yield dist


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    pkg = root / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""App."""\n')
    (pkg / "core.py").write_text('"""Core."""\n\n\ndef run() -> int:\n    return 1\n')
    return root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "dependency-decisions.db"
    open_index_database(path).close()
    return path


@pytest.fixture
def include_deps_config(tmp_path: Path) -> AppConfig:
    overlay = tmp_path / "include-deps.yaml"
    overlay.write_text("decision_capture:\n  include_deps: true\n")
    config = AppConfig.load(explicit_path=overlay)
    assert config.decision_capture.include_deps is True
    return config


def _index(
    config: AppConfig, db_path: Path, project_dir: Path, dependency: str
) -> tuple[bool, int, int, int]:
    """One pass with ``dependency`` as the only dependency; no pass may fail.

    Returns whether the project re-extracted, how many dependencies were
    indexed and how many cached, and how many texts the pass embedded.
    """
    embedder = CountingEmbedder(inner=MockEmbedder(dim=config.embedding.dim))
    stats = run_pass_with_embedder(
        config, db_path, project_dir, embedder=embedder, dependency_names=(dependency,)
    )
    # A dependency failure is swallowed into stats.failed, so without this a
    # crash in dependency mining would read as a pass.
    assert stats.failed == 0, "a dependency pass failed"
    embedded = sum(n_texts for _method, n_texts in embedder.calls)
    return stats.project_indexed, stats.indexed, stats.cached, embedded


def _dependency_hash(db_path: Path, dependency: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT content_hash FROM packages WHERE name = ?", (dependency,)
        ).fetchone()
    assert row is not None, "the dependency was never persisted"
    return str(row[0])


def _decision_rows(db_path: Path, dependency: str) -> list[tuple]:
    """The dependency's decision records and decision chunks, side by side."""
    with sqlite3.connect(db_path) as conn:
        records = conn.execute(
            "SELECT id, title, staleness_score, branch FROM decision_records WHERE package = ?",
            (dependency,),
        ).fetchall()
        chunks = conn.execute(
            "SELECT title, embedded FROM chunks WHERE package = ? AND origin = ?",
            (dependency, ChunkOrigin.DECISION_RECORD.value),
        ).fetchall()
    return [tuple(row) for row in (*records, *chunks)]


def test_include_deps_mines_a_dependency_once_then_settles_and_restores_stock(
    db_path: Path,
    project_dir: Path,
    dependency: InstalledDistribution,
    include_deps_config: AppConfig,
) -> None:
    name = dependency.name
    stock = AppConfig.load()

    first = _index(stock, db_path, project_dir, name)
    assert first[:3] == (True, 1, 0)
    stock_hash = _dependency_hash(db_path, name)
    assert _decision_rows(db_path, name) == [], "stock never mines a dependency"

    # (1) false → true: one re-extraction that persists the dependency's
    # decision — its record and its chunk, the chunk embedded (doc_pages tier).
    # The record lives in the dependency tier ('', #307), which every branch reads.
    assert _index(include_deps_config, db_path, project_dir, name) == (True, 1, 0, 1)
    mined_hash = _dependency_hash(db_path, name)
    assert mined_hash != stock_hash
    mined = _decision_rows(db_path, name)
    (_record_id, title, staleness, branch), (chunk_title, embedded) = mined
    assert title == chunk_title == _DECISION
    assert staleness == 0.0
    assert branch == ""
    assert embedded == 1

    # (2) …and then it settles: a cache hit that embeds nothing.
    assert _index(include_deps_config, db_path, project_dir, name) == (False, 0, 1, 0)
    assert _dependency_hash(db_path, name) == mined_hash
    assert _decision_rows(db_path, name) == mined

    # (3) back to false: one re-extraction deletes the rows and restores the
    # exact stock hash (a dependency that mines nothing folds nothing), then a
    # settled pass.
    assert _index(stock, db_path, project_dir, name) == (True, 1, 0, 0)
    assert _dependency_hash(db_path, name) == stock_hash
    assert _decision_rows(db_path, name) == []
    assert _index(stock, db_path, project_dir, name) == (False, 0, 1, 0)
