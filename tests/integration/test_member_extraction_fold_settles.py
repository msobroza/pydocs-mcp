"""A changed member-extraction setting re-extracts each dependency ONCE, then settles (#347).

``ProjectIndexer`` extracts a dependency's members AFTER its package cache
check, with the extractor the composition root builds from ``--no-inspect``,
``--depth`` and ``extraction.members.*``. None of those reached a cache key, so
changing one left the dependency a cache hit and its stored members frozen at
whatever the first settings produced — forever, healed only by ``--force``.

Proven over real passes through the real composition root, with a real small
installed distribution as the dependency (``sniffio``: the frozen dev venv
always carries it, and live-importing it runs no code worth isolating):

1. ``members_per_module_cap`` 120 → 1 re-extracts the dependency once, stores
   fewer member rows, then settles;
2. static mode re-extracts once, stores the AST extractor's rows, then settles —
   and a cap that static mode ignores re-extracts nothing;
3. back to the stock inspect settings restores the EXACT stock dependency hash
   and member rows (a stock token folds nothing), then settles; an explicit
   ``--depth`` equal to the YAML depth is the same identity, so a cache hit.

The project never re-extracts along the way: its members ignore every one of
these settings, so the fold is dependency-only. Nor does any pass after the
first embed anything: the member token folds into the package hash only, so a
re-extracted dependency's chunk hashes do not move and the chunk diff finds
nothing new. Member rows are compared relationally, never as exact counts,
because the dist version follows uv.lock.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import CountingEmbedder, MockEmbedder
from tests._index_fixture import run_pass_with_embedder

_DEPENDENCY = "sniffio"
_MEMBER_ROWS_SQL = (
    "SELECT module, name, kind, signature, docstring FROM module_members "
    "WHERE package = ? ORDER BY module, name"
)


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate ``AppConfig.load()`` from the developer's own config: "stock"
    here must mean the shipped defaults, not whatever ``PYDOCS_CONFIG_PATH``,
    ``./pydocs-mcp.yaml``, ``~/.config/pydocs-mcp/config.yaml`` or a
    ``PYDOCS_EXTRACTION*`` env var says."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    for name in [name for name in os.environ if name.startswith("PYDOCS_EXTRACTION")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


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
    path = tmp_path / "members.db"
    open_index_database(path).close()
    return path


@pytest.fixture
def one_member_config(tmp_path: Path) -> AppConfig:
    overlay = tmp_path / "one-member.yaml"
    overlay.write_text("extraction:\n  members:\n    members_per_module_cap: 1\n")
    return AppConfig.load(explicit_path=overlay)


def _dependency_hash(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT content_hash FROM packages WHERE name = ?", (_DEPENDENCY,)
        ).fetchone()
    assert row is not None, "the dependency was never persisted"
    return str(row[0])


def _member_rows(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        return [tuple(r) for r in conn.execute(_MEMBER_ROWS_SQL, (_DEPENDENCY,)).fetchall()]


def _index(
    config: AppConfig, db_path: Path, project_dir: Path, **flags: object
) -> tuple[bool, int, int, int]:
    """One pass with ``sniffio`` as the only dependency; no pass may fail.

    Returns whether the project re-extracted, how many dependencies were
    indexed and how many cached, and how many texts the pass embedded.
    """
    embedder = CountingEmbedder(inner=MockEmbedder(dim=config.embedding.dim))
    stats = run_pass_with_embedder(
        config, db_path, project_dir, embedder=embedder, dependency_names=(_DEPENDENCY,), **flags
    )
    assert stats.failed == 0, "a dependency pass failed"
    embedded = sum(n_texts for _method, n_texts in embedder.calls)
    return stats.project_indexed, stats.indexed, stats.cached, embedded


def _reextract_then_settle(
    config: AppConfig, db_path: Path, project_dir: Path, **flags: object
) -> tuple[str, list[tuple]]:
    """Exactly one dependency re-extraction, then a cache hit that keeps the
    stored hash and member rows frozen; returns what the re-extraction stored."""
    changed = _index(config, db_path, project_dir, **flags)
    assert changed == (False, 1, 0, 0), (
        "the change must re-extract the dependency once, embed nothing"
    )
    stored = _dependency_hash(db_path), _member_rows(db_path)

    settled = _index(config, db_path, project_dir, **flags)
    assert settled == (False, 0, 1, 0), "the next pass must be a cache hit that embeds nothing"
    assert (_dependency_hash(db_path), _member_rows(db_path)) == stored
    return stored


def _index_stock(db_path: Path, project_dir: Path) -> tuple[str, list[tuple]]:
    """The first pass under the CLI defaults (inspect mode, shipped YAML)."""
    first = _index(AppConfig.load(), db_path, project_dir, use_inspect=True)
    assert first[:3] == (True, 1, 0)
    stock = _dependency_hash(db_path), _member_rows(db_path)
    assert stock[1], "stock inspect mode must store the dependency's members"
    return stock


def test_a_changed_member_cap_reextracts_the_dependency_once_then_settles(
    db_path: Path, project_dir: Path, one_member_config: AppConfig
) -> None:
    stock_hash, stock_rows = _index_stock(db_path, project_dir)

    # Before the fix this pass was a cache hit that kept the stock rows.
    capped_hash, capped_rows = _reextract_then_settle(
        one_member_config, db_path, project_dir, use_inspect=True
    )
    assert capped_hash != stock_hash
    assert 0 < len(capped_rows) < len(stock_rows)

    restored_hash, restored_rows = _reextract_then_settle(
        AppConfig.load(), db_path, project_dir, use_inspect=True
    )
    assert (restored_hash, restored_rows) == (stock_hash, stock_rows)


def test_switching_static_and_inspect_reextracts_the_dependency_once_each_way(
    db_path: Path, project_dir: Path, one_member_config: AppConfig
) -> None:
    stock_hash, stock_rows = _index_stock(db_path, project_dir)

    static_hash, static_rows = _reextract_then_settle(
        AppConfig.load(), db_path, project_dir, use_inspect=False
    )
    assert static_hash != stock_hash
    assert static_rows != stock_rows
    # Static mode reads no cap, so tuning one there must re-extract nothing.
    capped_static = _index(one_member_config, db_path, project_dir, use_inspect=False)
    assert capped_static == (False, 0, 1, 0)

    restored = _reextract_then_settle(AppConfig.load(), db_path, project_dir, use_inspect=True)
    assert restored == (stock_hash, stock_rows)
    # ``--depth 1`` is the depth the YAML already gives: the same identity.
    same_depth = _index(AppConfig.load(), db_path, project_dir, use_inspect=True, inspect_depth=1)
    assert same_depth == (False, 0, 1, 0)
