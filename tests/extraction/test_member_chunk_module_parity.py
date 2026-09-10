"""AC-8/10/11: project member module ids match the chunk and tree ids.

``tests/extraction/test_member_module_ids_extractor.py`` pins the rule at the
extractor seam; this file pins what a consumer sees after a real
``ProjectIndexer`` pass — the invariant that makes a member id usable as a
``get_symbol`` target: every ``__project__`` member module also exists in
``chunks.module`` and in ``document_trees`` (spec
2026-09-10-member-module-ids-design §6 AC-8).

AC-10 pins the accepted cost of that parity (owner decision OD-A): package
rooting can map two files onto one module id, exactly as chunks and trees
already collide. The collision tests below record the ACCEPTED behaviour, not
a defect — a later collision spec is expected to change them on purpose.

AC-11 covers an unresolved symlinked root. Under OD-B-declined
(``python_package_root`` still calls ``resolve()``) the ids collapse to bare
stems, so the assertion is member/chunk PARITY only — the property that must
hold whichever way OD-B lands (spec §9 "OD-B declined").
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from tests._project_index_pass import index_project_source

_PYPROJECT = '[project]\nname = "fixture"\nversion = "0.1.0"\n'
# One class + one function per module, so every module carries member rows.
_MODULE_PY = '"""Module docstring."""\n\n\nclass {cls}:\n    """A class."""\n\n\ndef {fn}() -> int:\n    """A function."""\n    return 1\n'


def _module_py(stem: str) -> str:
    """Source whose symbol names identify the FILE it came from."""
    return _MODULE_PY.format(cls=f"Class_{stem}", fn=f"fn_{stem}")


def _write_module(root: Path, rel: str, stem: str | None = None) -> None:
    """Write ``rel`` under ``root`` with file-identifying symbol names."""
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_module_py(stem or target.stem), encoding="utf-8")


def _make_project_root(tmp_path: Path, name: str, *rels: str) -> Path:
    """A project root holding ``pyproject.toml`` plus each ``rels`` module."""
    root = tmp_path / name
    root.mkdir()
    (root / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    for rel in rels:
        _write_module(root, rel)
    return root


def _query(db: Path, sql: str, *extra: str) -> list[tuple]:
    """Run ``sql`` scoped to ``__project__``, plus any ``extra`` parameters."""
    conn = sqlite3.connect(db)
    try:
        return [tuple(r) for r in conn.execute(sql, (PROJECT_PACKAGE_NAME, *extra)).fetchall()]
    finally:
        conn.close()


def _member_modules(db: Path) -> set[str]:
    return {m for (m,) in _query(db, "SELECT DISTINCT module FROM module_members WHERE package=?")}


def _chunk_modules(db: Path) -> set[str]:
    return {m for (m,) in _query(db, "SELECT DISTINCT module FROM chunks WHERE package=?")}


def _tree_modules(db: Path) -> list[str]:
    return [m for (m,) in _query(db, "SELECT module FROM document_trees WHERE package=?")]


def _members_by_module(db: Path, module: str) -> set[str]:
    sql = "SELECT name FROM module_members WHERE package=? AND module=?"
    return {name for (name,) in _query(db, sql, module)}


# ── AC-8: member ⊆ chunk ∩ tree, over every §1 layout ────────────────────


def _src_layout(tmp_path: Path) -> tuple[Path, set[str]]:
    root = _make_project_root(
        tmp_path,
        "src_layout",
        "src/needle/__init__.py",
        "src/needle/scoring/__init__.py",
        "src/needle/scoring/strategies.py",
    )
    return root, {"needle", "needle.scoring", "needle.scoring.strategies"}


def _maturin_layout(tmp_path: Path) -> tuple[Path, set[str]]:
    root = _make_project_root(
        tmp_path, "maturin", "python/myproj/__init__.py", "python/myproj/db.py"
    )
    return root, {"myproj", "myproj.db"}


def _flat_layout(tmp_path: Path) -> tuple[Path, set[str]]:
    root = _make_project_root(
        tmp_path,
        "flat",
        "pkg/__init__.py",
        "pkg/mod.py",
        "top.py",
        "tests/test_x.py",
        "scripts/run.py",
        "setup.py",
    )
    return root, {"pkg", "pkg.mod", "top", "tests.test_x", "scripts.run", "setup"}


def _loose_dir_layout(tmp_path: Path) -> tuple[Path, set[str]]:
    root = _make_project_root(tmp_path, "loose", "src/app.py", "resources/x/src/midpoint.py")
    return root, {"src.app", "resources.x.src.midpoint"}


def _namespace_layout(tmp_path: Path) -> tuple[Path, set[str]]:
    root = _make_project_root(
        tmp_path, "namespace", "src/nsroot/sub/__init__.py", "src/nsroot/sub/mod.py"
    )
    return root, {"sub", "sub.mod"}


def _non_package_subdir_layout(tmp_path: Path) -> tuple[Path, set[str]]:
    root = _make_project_root(tmp_path, "nonpkg", "pkg/data/sub/__init__.py", "pkg/data/sub/m.py")
    return root, {"sub", "sub.m"}


def _root_package_layout(tmp_path: Path) -> tuple[Path, set[str]]:
    # ``tests/`` has no ``__init__.py``, so it is NOT part of the root package
    # and keeps its project-relative id — the same id the chunker gives it.
    root = _make_project_root(
        tmp_path, "rootpkg", "__init__.py", "a.py", "sub/__init__.py", "sub/inner.py", "tests/t.py"
    )
    return root, {"rootpkg", "rootpkg.a", "rootpkg.sub", "rootpkg.sub.inner", "tests.t"}


_LAYOUTS = {
    "src": _src_layout,
    "maturin": _maturin_layout,
    "flat": _flat_layout,
    "loose_dirs": _loose_dir_layout,
    "namespace": _namespace_layout,
    "non_package_subdir": _non_package_subdir_layout,
    "root_package": _root_package_layout,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", sorted(_LAYOUTS))
async def test_project_member_modules_exist_as_chunk_and_tree_modules(
    tmp_path: Path, layout: str
) -> None:
    """AC-8: every project member module is addressable as a chunk AND a tree.

    This is the invariant the fix exists for: search publishes member ids, and
    a published id that no tree carries has no file, no span, and no
    ``get_symbol`` resolution (spec §1 "User impact").
    """
    root, expected = _LAYOUTS[layout](tmp_path)
    db = tmp_path / f"{layout}.db"
    await index_project_source(root, db)

    members = _member_modules(db)
    assert expected <= members, f"{layout}: missing member modules {sorted(expected - members)}"
    assert members <= _chunk_modules(db), f"{layout}: member ids absent from chunks.module"
    assert members <= set(_tree_modules(db)), f"{layout}: member ids absent from document_trees"


# ── AC-10: accepted id collisions (owner decision OD-A) ──────────────────


@pytest.mark.asyncio
async def test_same_named_package_dirs_share_one_member_module(tmp_path: Path) -> None:
    """AC-10/OD-A: ``tests/`` and ``benchmarks/tests/`` collapse onto ``tests``.

    ACCEPTED behaviour, pinned so a later collision spec changes it on
    purpose: both packages are rooted at their own topmost ``__init__.py``,
    so both ``conftest.py`` files land on ``tests.conftest`` and the module
    carries the members of BOTH files, while ``document_trees`` — keyed
    ``(package, module)`` — keeps exactly one row.
    """
    root = _make_project_root(tmp_path, "collide_tests")
    _write_module(root, "tests/__init__.py", stem="root_init")
    _write_module(root, "tests/conftest.py", stem="conftest")
    _write_module(root, "benchmarks/tests/__init__.py", stem="bench_init")
    _write_module(root, "benchmarks/tests/conftest.py", stem="bench_conftest")
    db = tmp_path / "collide_tests.db"
    await index_project_source(root, db)

    assert {"tests", "tests.conftest"} <= _member_modules(db)
    assert _members_by_module(db, "tests.conftest") == {
        "Class_conftest",
        "fn_conftest",
        "Class_bench_conftest",
        "fn_bench_conftest",
    }  # rows from BOTH conftest.py files
    assert _tree_modules(db).count("tests.conftest") == 1


@pytest.mark.asyncio
async def test_sibling_example_packages_share_one_member_module(tmp_path: Path) -> None:
    """AC-10/OD-A: ``examples/{a,b}/app/main.py`` both become ``app.main``."""
    root = _make_project_root(tmp_path, "collide_examples")
    _write_module(root, "examples/a/app/__init__.py", stem="a_init")
    _write_module(root, "examples/a/app/main.py", stem="a_main")
    _write_module(root, "examples/b/app/__init__.py", stem="b_init")
    _write_module(root, "examples/b/app/main.py", stem="b_main")
    db = tmp_path / "collide_examples.db"
    await index_project_source(root, db)

    assert "app.main" in _member_modules(db)
    assert _members_by_module(db, "app.main") == {
        "Class_a_main",
        "fn_a_main",
        "Class_b_main",
        "fn_b_main",
    }  # rows from BOTH main.py files
    assert _tree_modules(db).count("app.main") == 1


# ── AC-11: unresolved symlinked root ─────────────────────────────────────


@pytest.mark.asyncio
async def test_symlinked_root_keeps_member_and_chunk_ids_equal(tmp_path: Path) -> None:
    """AC-11: indexing through an UNRESOLVED symlink keeps the two sides equal.

    pytest resolves ``tmp_path`` itself, so the symlink is created explicitly.
    Only parity is asserted: under OD-B-declined the shared ``resolve()`` in
    ``python_package_root`` collapses both sides to bare stems, and under OD-B
    both sides become package-rooted — the ids move together either way, which
    is the property consumers depend on (spec §9).
    """
    real, _expected = _src_layout(tmp_path)
    link = tmp_path / "linked_root"
    link.symlink_to(real, target_is_directory=True)
    db = tmp_path / "symlinked.db"
    await index_project_source(link, db)

    members = _member_modules(db)
    assert members, "indexing through the symlink produced no project members"
    assert members <= _chunk_modules(db)
    assert members <= set(_tree_modules(db))
