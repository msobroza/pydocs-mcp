"""BundleReader.branches() — AC-14 / AC-14b of the branch-scope UI spec."""

from __future__ import annotations

import sqlite3

import pytest

from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch, SqliteBundleReader
from pydocs_mcp.models import BranchStatus

from ._fixture import make_bundle

_SHA = "3e1a9c2" + "0" * 33  # 40 hex characters — a landing-unit name
_HEAD = "a" * 40


def test_branches_are_ordered_default_first_then_by_name(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        branches=[
            ("zeta", _HEAD, "main", 1, "active", None),
            ("feature/x", _HEAD, "main", 0, "active", None),
            ("main", _HEAD, None, 0, "active", None),
        ],
    )
    names = [row.name for row in SqliteBundleReader(db).branches()]
    # WHY the default is the alphabetically LAST name: with the default on an
    # already-first name the expectation is identical under a plain `ORDER BY
    # name`, so the "default first" half of the rule has no coverage at all.
    assert names == ["zeta", "feature/x", "main"]


def test_rows_carry_status_base_and_default_flag(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        branches=[("feature/x", _HEAD, "main", 1, "active", None)],
    )
    (row,) = SqliteBundleReader(db).branches()
    assert row == IndexedBranch(
        name="feature/x",
        head_sha=_HEAD,
        base_name="main",
        is_default=True,
        status=BranchStatus.ACTIVE,
        merged_into=None,
        landing_kind=None,
        indexed_at=1.0,
    )
    # WHY `is True`: the dataclass comparison above passes with SQLite's raw
    # int, since 1 == True. Only an identity check pins the bool coercion.
    assert row.is_default is True


def test_pre_v16_bundle_yields_no_rows(tmp_path):
    db = make_bundle(tmp_path / "demo_0123456789.db", with_branch_tables=False)
    assert SqliteBundleReader(db).branches() == ()


def test_other_operational_errors_are_re_raised(tmp_path):
    db = make_bundle(tmp_path / "demo_0123456789.db", with_branch_tables=False)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE branches (name TEXT)")  # a wrong shape, not a missing table
    with pytest.raises(sqlite3.OperationalError):
        SqliteBundleReader(db).branches()


def test_branches_never_migrates_the_bundle(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        branches=[("main", _HEAD, None, 1, "active", None)],
    )
    SqliteBundleReader(db).branches()
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 99


def test_landing_units_and_tombstones_are_distinguishable(tmp_path):
    """AC-14b: one default row, one MERGED tombstone, one 40-hex landing row."""
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        branches=[
            ("main", _HEAD, None, 1, "active", None),
            ("feature/old", _HEAD, "main", 0, "merged", _SHA),
            (_SHA, _SHA, "main", 0, "active", None),
        ],
    )
    rows = {row.name: row for row in SqliteBundleReader(db).branches()}
    assert len(rows) == 3
    assert rows[_SHA].is_landing_unit and not rows["main"].is_landing_unit
    assert rows["feature/old"].status is BranchStatus.MERGED
    assert rows["feature/old"].merged_into == _SHA
