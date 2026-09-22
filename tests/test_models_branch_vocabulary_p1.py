"""P1 vocabulary: landing kinds, merge evidence, the landing step, the P1 record fields.

Spec §6.1 (v18 columns), §6.2 (``first_parent_landings`` rows), §6.5b (landing
units) and §6.8a (merge evidence); ADR 0024 decision 2.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import (
    BranchIndexSource,
    BranchSlice,
    BranchStatus,
    FileChangeKind,
    LandingKind,
    LandingStep,
    MergeEvidence,
)
from pydocs_mcp.storage.branch_records import BranchRecord, LandingPatchId
from pydocs_mcp.storage.index_metadata import (
    IndexMetadata,
    read_index_metadata,
    write_index_metadata,
)

_UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")

# Built with P0 arguments only, so every P1 field on it keeps its default.
_PLAIN_BRANCH = BranchRecord(
    name="main",
    head_sha="a" * 40,
    source=BranchIndexSource.WORKING_TREE,
    pipeline_hash="p",
    indexed_at=1.0,
    last_used_at=1.0,
)

# The v17 ``index_metadata`` shape; the v18 one is this plus ``diff_retain_hash``.
_V17_METADATA_COLUMNS = (
    "id INTEGER PRIMARY KEY CHECK (id = 1), project_name TEXT, project_root TEXT, "
    "embedding_provider TEXT, embedding_model TEXT, embedding_dim INTEGER, "
    "pipeline_hash TEXT, indexed_at REAL, git_head TEXT, activity_summary TEXT, "
    "overview_summary TEXT, loadable_grammars TEXT"
)


def _record(**overrides: Any) -> BranchRecord:
    return replace(_PLAIN_BRANCH, **overrides)


def _unmigrated_metadata_connection(db: Path, columns: str) -> sqlite3.Connection:
    """A plain connection over a hand-built ``index_metadata`` of ``columns`` —
    no migration runs, as through ``factories.build_freshness_probe``."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute(f"CREATE TABLE index_metadata ({columns})")
    return conn


@pytest.mark.parametrize(
    "vocabulary",
    [LandingKind, MergeEvidence, BranchStatus, BranchIndexSource, BranchSlice, FileChangeKind],
)
def test_branch_vocabularies_are_str_enums_with_upper_snake_members(
    vocabulary: type[StrEnum],
) -> None:
    """Owner rule (2026-07-26): a closed vocabulary is a StrEnum, never a Literal
    alias; members are UPPER_SNAKE and store their lowercase name as the value
    (the TEXT the ``branches`` columns hold)."""
    assert issubclass(vocabulary, StrEnum)
    for member in vocabulary:
        assert _UPPER_SNAKE.match(member.name), member.name
        assert member.value == member.name.lower()


def test_landing_vocabularies_carry_exactly_the_spec_values() -> None:
    assert LandingKind.MERGE_COMMIT == "merge_commit"
    assert {k.value for k in LandingKind} == {"merge_commit", "single_commit", "linear_snapshot"}
    # Exact set: the gone-upstream signal is a column, never evidence (spec
    # §6.8a), so an UPSTREAM_GONE member fails here.
    assert {e.value for e in MergeEvidence} == {
        "ancestor",
        "patch_id_match",
        "rebase_patch_id_match",
    }


def test_plain_branch_record_defaults_to_no_landing_fields() -> None:
    record = _record()
    assert record.landing_kind is None
    assert record.landed_at is None
    assert record.diff_generation_key is None
    assert record.merge_evidence is None
    assert record.landing_sha is None
    assert record.upstream_gone is False
    assert record.is_landing_unit is False


def test_landing_unit_record_is_flagged_by_landing_kind() -> None:
    unit = _record(
        name="b" * 40,
        source=BranchIndexSource.GIT_OBJECTS,
        landing_kind=LandingKind.SINGLE_COMMIT,
        landed_at=2.0,
        merge_base_sha="c" * 40,
    )
    assert unit.is_landing_unit is True
    with pytest.raises(FrozenInstanceError):
        unit.landing_kind = None  # type: ignore[misc]


def test_merged_branch_record_keeps_base_name_and_landing_sha_apart() -> None:
    """ADR 0024 O18: ``merged_into`` keeps meaning the base name; the landing
    commit rides in its own column, and the branch itself is NOT a unit."""
    merged = _record(
        name="feature/x",
        status=BranchStatus.MERGED,
        merged_into="main",
        merge_evidence=MergeEvidence.PATCH_ID_MATCH,
        landing_sha="d" * 40,
        upstream_gone=True,
    )
    assert merged.merged_into == "main"
    assert merged.landing_sha == "d" * 40
    assert merged.merge_evidence is MergeEvidence.PATCH_ID_MATCH
    assert merged.is_landing_unit is False


def test_landing_step_and_patch_id_records_are_frozen_values() -> None:
    step = LandingStep(
        sha="a" * 40, parent_shas=("b" * 40,), landed_at=3.0, subject="s", patch_id="p"
    )
    assert step.parent_shas == ("b" * 40,)
    cached = LandingPatchId(sha="a" * 40, patch_id="p")
    assert cached == LandingPatchId(sha="a" * 40, patch_id="p")
    with pytest.raises(FrozenInstanceError):
        step.subject = "t"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        cached.patch_id = "q"  # type: ignore[misc]


def test_index_metadata_diff_retain_hash_defaults_empty() -> None:
    """Old constructors stay valid, and a legacy stamp has no retain digest."""
    legacy = IndexMetadata.legacy_fallback(project_name="p", embedding_model=None)
    assert legacy.diff_retain_hash == ""


def test_index_metadata_diff_retain_hash_round_trips(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    open_index_database(db).close()
    meta = IndexMetadata(
        project_name="p",
        project_root=str(tmp_path),
        embedding_provider="fastembed",
        embedding_model="m",
        embedding_dim=3,
        pipeline_hash="h",
        indexed_at=1.0,
        git_head="a" * 40,
        diff_retain_hash="r1",
    )
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        write_index_metadata(conn, meta)
        stored = read_index_metadata(conn)
    assert stored is not None and stored.diff_retain_hash == "r1"


def test_index_metadata_reads_empty_hash_on_a_bundle_without_the_column(tmp_path: Path) -> None:
    """The freshness probe reads without migrating (factories.build_freshness_probe)."""
    v17_db = tmp_path / "v17.db"
    with closing(_unmigrated_metadata_connection(v17_db, _V17_METADATA_COLUMNS)) as conn:
        conn.execute(
            "INSERT INTO index_metadata (id, project_name, project_root, embedding_provider, "
            "embedding_model, embedding_dim, pipeline_hash, indexed_at, git_head) "
            "VALUES (1, 'p', '/r', 'fastembed', 'm', 3, 'h', 1.0, '')"
        )
        stored = read_index_metadata(conn)
    assert stored is not None and stored.diff_retain_hash == ""


def test_index_metadata_reads_a_stored_diff_retain_hash_and_null_as_empty(tmp_path: Path) -> None:
    """The reader takes the column once a later schema carries it — a
    hand-built v18-shaped row stands in for the migration (plan Task 2)."""
    v18_columns = f"{_V17_METADATA_COLUMNS}, diff_retain_hash TEXT"
    with closing(_unmigrated_metadata_connection(tmp_path / "v18.db", v18_columns)) as conn:
        conn.execute(
            "INSERT INTO index_metadata (id, project_name, indexed_at, diff_retain_hash) "
            "VALUES (1, 'p', 1.0, 'r1')"
        )
        stored = read_index_metadata(conn)
        assert stored is not None and stored.diff_retain_hash == "r1"
        conn.execute("UPDATE index_metadata SET diff_retain_hash = NULL")
        stored = read_index_metadata(conn)
        assert stored is not None and stored.diff_retain_hash == ""
