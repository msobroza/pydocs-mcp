"""End-to-end acceptance suite for per-project directory exclusion.

Drives the REAL write-side composition root (``build_project_indexer``,
``storage/factories.py``) over tmp projects that declare
``[tool.pydocs-mcp] exclude_dirs`` in their own ``pyproject.toml`` and/or
``extraction.discovery.*.exclude_dirs`` in a YAML overlay. Pins the
spec's end-to-end acceptance criteria: exclusion of chunks AND symbols
(AC-17), widen-and-reindex removal including the member-only-content
fingerprint case (AC-18), file-name-collision no-op parity (AC-19),
zero decision records from a TOML-excluded ADR directory (AC-21,
end-to-end clause), dependency-cache isolation in both directions
(AC-23), the four conditional-fold behaviors (AC-24), and the YAML
surface through the real composition root (AC-26).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.application.indexing_service import IndexingStats
from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig


# ── Offline seam (same monkeypatch pattern as tests/storage/test_build_project_indexer.py) ──


@pytest.fixture(autouse=True)
def _offline_factories(monkeypatch):
    """MockEmbedder + FakeLlmClient so the composition root never downloads
    ONNX weights or touches the OpenAI network (the factory resolves both
    lazily via deferred imports — the documented monkeypatch seam)."""
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.retrieval import llm_clients as _llm_clients
    from tests._fakes import FakeLlmClient, MockEmbedder

    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: MockEmbedder())
    monkeypatch.setattr(
        _llm_clients,
        "build_llm_client",
        lambda cfg: FakeLlmClient(responses={}),
    )


# ── Fixture-project builders ─────────────────────────────────────────────

_CORE_PY = '"""Core module."""\n\n\ndef core_fn():\n    """Core."""\n    return 1\n'
_SAMPLE_PY = '"""Fixture sample."""\n\n\ndef sample_fn():\n    """Sample."""\n    return 2\n'
_GEN_PY = '"""Generated tool."""\n\n\ndef gen_fn():\n    """Gen."""\n    return 3\n'
_GUIDE_MD = "# Guide\n\nReal documentation.\n"
_API_MD = "# Generated API\n\nAuto-generated noise.\n"
_DATA_MD = "# Fixture data\n\nSynthetic content.\n"


def _write_pyproject(
    root: Path,
    *,
    exclude_dirs: list[str] | None = None,
    dependencies: tuple[str, ...] = (),
) -> None:
    """(Re)write the project's own pyproject.toml — the TOML surface.

    ADR 0021 T1 widened the default include_extensions to cover ``.toml``,
    so pyproject.toml is now an indexed file whose mtime feeds the
    ``(path, mtime)`` package fingerprint. These tests isolate the
    exclude-dirs *fold*, not pyproject-as-content, so we pin the file's
    mtime across rewrites — the exclude info enters the hash via the fold
    digest, never via mtime noise from re-emitting the TOML.
    """
    pyproject = root / "pyproject.toml"
    # hash_files keys on st_mtime_ns — preserve nanosecond precision so the
    # rewrite is invisible to the (path, mtime) fingerprint.
    preserved_ns = pyproject.stat().st_mtime_ns if pyproject.exists() else None
    deps = ", ".join(f'"{d}"' for d in dependencies)
    text = f'[project]\nname = "e2e-excl"\nversion = "0.1.0"\ndependencies = [{deps}]\n'
    if exclude_dirs is not None:
        entries = ", ".join(f'"{e}"' for e in exclude_dirs)
        text += f"\n[tool.pydocs-mcp]\nexclude_dirs = [{entries}]\n"
    pyproject.write_text(text, encoding="utf-8")
    if preserved_ns is not None:
        os.utime(pyproject, ns=(preserved_ns, preserved_ns))


def _make_worked_example_tree(root: Path) -> None:
    """The spec §4 worked-example tree (minus .venv — the floor is pinned
    by the discoverer unit tests, not re-proven here)."""
    (root / "docs" / "generated").mkdir(parents=True)
    (root / "docs" / "generated" / "api.md").write_text(_API_MD, encoding="utf-8")
    (root / "docs" / "guide.md").write_text(_GUIDE_MD, encoding="utf-8")
    (root / "src" / "myproj" / "fixtures").mkdir(parents=True)
    (root / "src" / "myproj" / "core.py").write_text(_CORE_PY, encoding="utf-8")
    (root / "src" / "myproj" / "fixtures" / "sample.py").write_text(_SAMPLE_PY, encoding="utf-8")
    (root / "fixtures").mkdir()
    (root / "fixtures" / "data.md").write_text(_DATA_MD, encoding="utf-8")
    (root / "tools" / "generated").mkdir(parents=True)
    (root / "tools" / "generated" / "gen.py").write_text(_GEN_PY, encoding="utf-8")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh SQLite DB file — schema materialised up front."""
    p = tmp_path / "e2e_excl.db"
    open_index_database(p).close()
    return p


# ── Indexing through the REAL composition root ───────────────────────────


async def _index_run(
    project: Path,
    db: Path,
    config: AppConfig | None = None,
    *,
    include_deps: bool = False,
) -> IndexingStats:
    """One full index pass via build_project_indexer — a fresh bundle per
    run, exactly like a fresh CLI invocation (the per-run TOML loader of
    spec §5 is exercised for real)."""
    from pydocs_mcp.storage.factories import build_project_indexer

    bundle = build_project_indexer(
        config or AppConfig.load(),
        db,
        use_inspect=False,
        inspect_depth=None,
    )
    return await bundle.orchestrator.index_project(
        project,
        force=False,
        include_project_source=True,
        include_dependencies=include_deps,
        workers=1,
    )


# ── SQLite observation helpers ───────────────────────────────────────────


def _rows(db: Path, sql: str, *params: object) -> list[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _chunk_modules(db: Path) -> set[str]:
    return {r[0] for r in _rows(db, "SELECT module FROM chunks WHERE package = '__project__'")}


def _member_modules(db: Path) -> set[str]:
    return {
        r[0] for r in _rows(db, "SELECT module FROM module_members WHERE package = '__project__'")
    }


def _package_hash(db: Path, name: str = "__project__") -> str:
    rows = _rows(db, "SELECT content_hash FROM packages WHERE name = ?", name)
    assert rows, f"no packages row for {name!r}"
    return rows[0][0]


def _has_component(modules: set[str], component: str) -> bool:
    """True iff any dotted module id carries *component* as a path segment
    (module ids dot-join directory components; doc files keep their
    extension as a trailing segment, e.g. 'fixtures.data.md')."""
    return any(component in m.split(".") for m in modules)


# ── AC-17: pyproject excludes remove chunks and symbols ──────────────────


async def test_ac17_pyproject_excludes_remove_chunks_and_symbols(
    tmp_path: Path, db_path: Path
) -> None:
    """Index a project whose OWN pyproject.toml declares exclude_dirs;
    neither chunks nor ModuleMember rows exist for the excluded
    directories, while every sibling survives — including the
    leaf-name-collision sibling tools/generated/ (spec §4)."""
    _make_worked_example_tree(tmp_path)
    _write_pyproject(tmp_path, exclude_dirs=["docs/generated", "fixtures"])

    stats = await _index_run(tmp_path, db_path)
    assert stats.project_indexed is True

    chunks = _chunk_modules(db_path)
    members = _member_modules(db_path)

    # Bare "fixtures" prunes BOTH occurrences (root-level and nested).
    assert not _has_component(chunks, "fixtures"), f"fixtures chunks leaked: {chunks}"
    assert not _has_component(members, "fixtures"), f"fixtures symbols leaked: {members}"
    # Anchored "docs/generated" prunes exactly its own subtree...
    assert not any(m.startswith("docs.generated.") for m in chunks)
    # ...while the sibling with the same leaf name survives on BOTH tables.
    assert "tools.generated.gen" in chunks
    assert "tools.generated.gen" in members
    # Untouched content is fully present on both tables.
    assert "docs.guide.md" in chunks
    assert "src.myproj.core" in chunks
    assert "src.myproj.core" in members


# ── AC-19: directories-only rule — filename collisions are a no-op ───────


async def test_ac19_filename_collision_is_uniform_noop(tmp_path: Path, db_path: Path) -> None:
    """An entry colliding with a FILE name (anchored 'docs/conf.py' where
    docs/conf.py is a file, and the bare filename 'conf.py') excludes
    nothing on EITHER walk: the file's chunks and its symbols are both
    present — never one without the other (spec §4 directories-only rule,
    §7.5 chunk/member divergence guard)."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "conf.py").write_text(
        '"""Sphinx-style conf."""\n\n\ndef setup(app):\n    """Setup."""\n    return app\n',
        encoding="utf-8",
    )
    (tmp_path / "docs" / "guide.md").write_text(_GUIDE_MD, encoding="utf-8")
    _write_pyproject(tmp_path, exclude_dirs=["docs/conf.py", "conf.py"])

    stats = await _index_run(tmp_path, db_path)
    assert stats.project_indexed is True

    chunks = _chunk_modules(db_path)
    members = _member_modules(db_path)
    # Parity: the collided file keeps chunks AND symbols.
    assert "docs.conf" in chunks, f"chunk walk dropped docs/conf.py: {chunks}"
    assert "docs.conf" in members, f"member post-filter dropped docs/conf.py: {members}"
    # The rest of docs/ is untouched (the anchored entry excluded nothing).
    assert "docs.guide.md" in chunks


# ── AC-18: widen the excludes → reindex removes rows, SQLite + .tq coherent ──


async def test_ac18_widen_and_reindex_removes_previously_indexed_rows(
    tmp_path: Path, db_path: Path
) -> None:
    """Widening exclude_dirs between two runs misses the package hash,
    skips the cached path, and atomically removes the newly-excluded
    directory's chunks AND symbols; the .tq sidecar stays coherent with
    SQLite (observed via the bundle's integrity sweep)."""
    from pydocs_mcp.storage.factories import build_project_indexer

    _make_worked_example_tree(tmp_path)
    _write_pyproject(tmp_path)  # no excludes yet

    stats1 = await _index_run(tmp_path, db_path)
    assert stats1.project_indexed is True
    hash_before = _package_hash(db_path)
    assert _has_component(_chunk_modules(db_path), "fixtures")
    assert _has_component(_member_modules(db_path), "fixtures")

    # Widen: exclude fixtures. pyproject.toml's content changes but its
    # mtime is pinned by _write_pyproject (ADR 0021 T1 made .toml an indexed
    # file), so the (path, mtime) fingerprint moves ONLY because discovery
    # now prunes fixtures/.
    _write_pyproject(tmp_path, exclude_dirs=["fixtures"])
    stats2 = await _index_run(tmp_path, db_path)
    assert stats2.project_indexed is True, "cached-skip path must NOT be taken"
    assert _package_hash(db_path) != hash_before

    assert not _has_component(_chunk_modules(db_path), "fixtures"), "chunk rows orphaned"
    assert not _has_component(_member_modules(db_path), "fixtures"), "member rows orphaned"
    # Vector-count coherence: the integrity sweep compares chunks(embedded=1)
    # against the .tq sidecar and returns [] iff nothing is orphaned/stranded.
    bundle = build_project_indexer(AppConfig.load(), db_path, use_inspect=False, inspect_depth=None)
    assert await bundle.check_integrity() == []


async def test_ac18_member_only_directory_still_misses_via_fingerprint(
    tmp_path: Path, db_path: Path
) -> None:
    """The §9 fingerprint-fold case: the newly-excluded directory holds
    ONLY member-producing, chunk-invisible content (a .py above
    max_file_size_bytes, default 1_000_000). Excluding it leaves the
    chunk-discovered path set unchanged — a path-only hash would hit the
    cache and strand the symbols. The exclusion fingerprint must force
    the miss and the symbols must vanish."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(_CORE_PY, encoding="utf-8")
    (tmp_path / "bigonly").mkdir()
    # > 1 MB of valid Python: one real symbol + comment padding. The size
    # budget applies to chunk discovery only; walk_py_files (members) has
    # no budget, so this file yields a ModuleMember but zero chunks.
    big_src = '"""Big module."""\n\n\ndef big_fn():\n    """Big."""\n    return 1\n\n' + (
        "# pad\n" * 170_000
    )
    (tmp_path / "bigonly" / "big.py").write_text(big_src, encoding="utf-8")
    _write_pyproject(tmp_path)

    stats1 = await _index_run(tmp_path, db_path)
    assert stats1.project_indexed is True
    hash_before = _package_hash(db_path)
    # Precondition the case depends on: members yes, chunks no.
    assert "bigonly.big" in _member_modules(db_path)
    assert "bigonly.big" not in _chunk_modules(db_path)

    _write_pyproject(tmp_path, exclude_dirs=["bigonly"])
    stats2 = await _index_run(tmp_path, db_path)
    assert stats2.project_indexed is True, (
        "path set unchanged but exclusion set widened — the fingerprint "
        "fold must force a hash miss (spec §9, Goal 6)"
    )
    assert _package_hash(db_path) != hash_before
    assert "bigonly.big" not in _member_modules(db_path), "member-only symbols survived"


# ── AC-23: per-scope fingerprint — project edits never invalidate dep caches ──


def _write_overlay(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


async def test_ac23_dependency_cache_isolation_both_directions(
    tmp_path: Path, db_path: Path
) -> None:
    """Editing PROJECT excludes (TOML, then YAML) re-extracts the project
    but leaves every dependency on the cached-skip path (stats.cached);
    editing YAML DEPENDENCY excludes misses the dependency hashes without
    touching the project's (spec §9.1, decision D10)."""
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "app.py").write_text(_CORE_PY, encoding="utf-8")
    (proj / "fixtures").mkdir()
    (proj / "fixtures" / "data.md").write_text(_DATA_MD, encoding="utf-8")
    _write_pyproject(proj, dependencies=("iniconfig",))

    # Run A — baseline: project + iniconfig both extracted.
    stats_a = await _index_run(proj, db_path, include_deps=True)
    assert stats_a.project_indexed is True
    assert stats_a.indexed == 1 and stats_a.failed == 0
    dep_hash_a = _package_hash(db_path, "iniconfig")

    # Run B — TOML direction: edit ONLY the project's own exclude_dirs.
    _write_pyproject(proj, exclude_dirs=["fixtures"], dependencies=("iniconfig",))
    stats_b = await _index_run(proj, db_path, include_deps=True)
    assert stats_b.project_indexed is True, "project hash must miss"
    assert stats_b.cached == 1 and stats_b.indexed == 0, (
        "iniconfig must take the cached-skip path — project TOML excludes "
        "must never reach the dependency fold"
    )
    assert _package_hash(db_path, "iniconfig") == dep_hash_a

    # Run C — YAML project direction: add a YAML project exclude on top.
    overlay_c = _write_overlay(
        tmp_path / "overlay_c.yaml",
        'extraction:\n  discovery:\n    project:\n      exclude_dirs: ["docs"]\n',
    )
    stats_c = await _index_run(
        proj, db_path, AppConfig.load(explicit_path=overlay_c), include_deps=True
    )
    assert stats_c.project_indexed is True, "project fold changed → miss"
    assert stats_c.cached == 1 and stats_c.indexed == 0
    assert _package_hash(db_path, "iniconfig") == dep_hash_a

    # Run D — dependency direction: SAME project excludes as run C, plus a
    # dependency-scope exclude. Project skips; the dependency misses.
    overlay_d = _write_overlay(
        tmp_path / "overlay_d.yaml",
        "extraction:\n  discovery:\n"
        '    project:\n      exclude_dirs: ["docs"]\n'
        '    dependency:\n      exclude_dirs: ["tests"]\n',
    )
    stats_d = await _index_run(
        proj, db_path, AppConfig.load(explicit_path=overlay_d), include_deps=True
    )
    assert stats_d.project_indexed is False, (
        "dependency-scope excludes must never reach the project fold"
    )
    assert stats_d.indexed == 1 and stats_d.cached == 0, "dependency fold changed → miss"
    assert _package_hash(db_path, "iniconfig") != dep_hash_a


# ── AC-24: upgrade compatibility — the fold is conditional (spec §9.2) ────


async def test_ac24_conditional_fold_all_four_behaviors(tmp_path: Path, db_path: Path) -> None:
    """(a) No user excludes → the stored hash equals TODAY'S framing, pure
    hash_files(paths) — a pre-upgrade index skips as cached; (b) first
    user exclude → miss; (c) removing the last exclude → miss AND the
    hash returns to the unfolded value of (a); (d) a floor-duplicate-only
    list ('.git') → same hash as (a), no spurious miss."""
    from pydocs_mcp._fast import hash_files
    from pydocs_mcp.extraction.config import DiscoveryScopeConfig
    from pydocs_mcp.extraction.strategies.discovery import ProjectFileDiscoverer

    _make_worked_example_tree(tmp_path)
    _write_pyproject(tmp_path)

    # (a) baseline: byte-identical to the pre-change framing.
    stats_a = await _index_run(tmp_path, db_path)
    assert stats_a.project_indexed is True
    hash_a = _package_hash(db_path)
    paths, _root, _effective = ProjectFileDiscoverer(scope=DiscoveryScopeConfig()).discover(
        tmp_path
    )
    raw = hash_files(list(paths))
    expected_unfolded = raw if isinstance(raw, str) else raw.hex()
    assert hash_a == expected_unfolded, (
        "no-excludes hash must equal pure hash_files(paths) so every "
        "pre-upgrade stored hash keeps matching (spec §9.2)"
    )

    # (b) adding the first user exclude → miss.
    _write_pyproject(tmp_path, exclude_dirs=["fixtures"])
    stats_b = await _index_run(tmp_path, db_path)
    assert stats_b.project_indexed is True
    hash_b = _package_hash(db_path)
    assert hash_b != hash_a

    # (c) removing the last user exclude → miss, hash returns to (a).
    _write_pyproject(tmp_path)
    stats_c = await _index_run(tmp_path, db_path)
    assert stats_c.project_indexed is True
    assert _package_hash(db_path) == hash_a, "fold must drop out entirely"

    # (d) floor duplicates only → effective set == floor → no fold, no miss.
    _write_pyproject(tmp_path, exclude_dirs=[".git"])
    stats_d = await _index_run(tmp_path, db_path)
    assert stats_d.project_indexed is False, "floor-duplicate entry caused a spurious miss"
    assert _package_hash(db_path) == hash_a


# ── AC-26: YAML exclude_dirs reaches BOTH walks via storage/factories.py ──


async def test_ac26_yaml_excludes_through_real_composition_root(
    tmp_path: Path, db_path: Path
) -> None:
    """Load extraction.discovery.project.exclude_dirs from a YAML overlay
    through the real AppConfig.load + build_project_indexer — NO in-test
    construction of AstMemberExtractor or the discoverers — and assert
    chunks AND ModuleMember rows from fixtures/ are both absent. Pins the
    factories.py wiring of scope_exclude_dirs (spec §7.7): forgetting it
    passes the unit tests (field injected in-test) while YAML excludes
    silently never reach member extraction."""
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "app.py").write_text(_CORE_PY, encoding="utf-8")
    (proj / "fixtures").mkdir()
    (proj / "fixtures" / "sample.py").write_text(_SAMPLE_PY, encoding="utf-8")
    (proj / "fixtures" / "data.md").write_text(_DATA_MD, encoding="utf-8")
    _write_pyproject(proj)  # NO TOML excludes — YAML is the only surface here.

    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        'extraction:\n  discovery:\n    project:\n      exclude_dirs: ["fixtures"]\n',
        encoding="utf-8",
    )
    stats = await _index_run(proj, db_path, AppConfig.load(explicit_path=overlay))
    assert stats.project_indexed is True

    chunks = _chunk_modules(db_path)
    members = _member_modules(db_path)
    assert not _has_component(chunks, "fixtures"), (
        f"YAML excludes never reached chunk discovery: {chunks}"
    )
    assert not _has_component(members, "fixtures"), (
        f"YAML excludes never reached member extraction "
        f"(scope_exclude_dirs unwired in storage/factories.py): {members}"
    )
    # The non-excluded module survives on both tables.
    assert "src.app" in chunks
    assert "src.app" in members


# ── AC-21 (end-to-end clause): excluded ADR dir → zero decision records ───

_ADR_MD = "# 1. Use SQLite\n\nStatus: Accepted\n\n## Decision\nYes.\n"
_ADR2_MD = "# 2. Use FTS\n\nStatus: Accepted\n\n## Decision\nAlso yes.\n"


async def test_ac21_pyproject_excluded_adr_dir_yields_no_decision_records(
    tmp_path: Path,
) -> None:
    """Index a tmp project whose OWN pyproject.toml excludes its ADR
    directories: zero decision_records sourced from them. get_why and
    ``search --kind decision`` hydrate exclusively from decision_records
    (via origin='decision_record' chunks), and
    get_references(direction="governed_by") traverses kind='governs'
    edges in node_references — so zero rows on all three tables, observed
    at the single storage layer every one of those read surfaces
    consumes, IS the "surface nothing from it" guarantee (spec AC-21).
    A control project (same tree, no exclude) proves the fixture mines
    for real — the default shipped config already enables the adr_files
    source, so no decision_capture overlay is needed. Both conventional
    docs-side ADR dirs (docs/adr AND docs/decisions) are exercised."""

    def _make(root: Path, *, exclude: bool) -> Path:
        (root / "docs" / "adr").mkdir(parents=True)
        (root / "docs" / "adr" / "0001-use-sqlite.md").write_text(_ADR_MD, encoding="utf-8")
        (root / "docs" / "decisions").mkdir(parents=True)
        (root / "docs" / "decisions" / "0002-use-fts.md").write_text(_ADR2_MD, encoding="utf-8")
        (root / "src").mkdir()
        (root / "src" / "core.py").write_text(_CORE_PY, encoding="utf-8")
        _write_pyproject(root, exclude_dirs=["docs"] if exclude else None)
        return root

    # Control: without the exclude, both ADR dirs mine — otherwise the
    # zero-assertions below would be vacuously green.
    control = _make(tmp_path / "control", exclude=False)
    control_db = tmp_path / "control.db"
    open_index_database(control_db).close()
    stats = await _index_run(control, control_db)
    assert stats.project_indexed is True
    n_adr = _rows(
        control_db,
        "SELECT COUNT(*) FROM decision_records WHERE source = 'adr_files'",
    )[0][0]
    assert n_adr >= 2, "control fixture failed to mine both ADR dirs — pin would be vacuous"

    # Excluded: the same tree with [tool.pydocs-mcp] exclude_dirs = ["docs"].
    # In this fixture EVERY decision record comes from adr_files (no git
    # repo, no CHANGELOG, no README/docs-glob prose, no inline markers), so
    # total-count zero is the strongest observable.
    excl = _make(tmp_path / "excl", exclude=True)
    excl_db = tmp_path / "excl.db"
    open_index_database(excl_db).close()
    stats = await _index_run(excl, excl_db)
    assert stats.project_indexed is True
    assert _rows(excl_db, "SELECT COUNT(*) FROM decision_records")[0][0] == 0
    # Nothing for get_why / `search --kind decision` to hydrate...
    assert (
        _rows(
            excl_db,
            "SELECT COUNT(*) FROM chunks WHERE origin = 'decision_record'",
        )[0][0]
        == 0
    )
    # ...and nothing for get_references(direction="governed_by") to traverse.
    assert (
        _rows(
            excl_db,
            "SELECT COUNT(*) FROM node_references WHERE kind = 'governs'",
        )[0][0]
        == 0
    )
