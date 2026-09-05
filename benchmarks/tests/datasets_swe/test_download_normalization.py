"""Offline coverage of the parquet-row normalization edge (ADR 0013 deliverable 1).

The download module's heavy deps (pyarrow, huggingface_hub) are function-local, so the
module imports and its pure normalizers are testable without a parquet engine — the
struct/timestamp flattening logic that produces :class:`LiveRecord` is exercised here.
"""

from __future__ import annotations

from datetime import datetime

from pydocs_eval.datasets_swe import download
from pydocs_eval.datasets_swe.records import LiveRecord


def test_year_of_handles_datetime_and_iso_string():
    assert download._year_of(datetime(2024, 5, 1)) == 2024
    assert download._year_of("2025-09-02T00:00:00") == 2025


def test_to_live_record_flattens_difficulty_struct_and_year():
    row = {
        "instance_id": "conan-io__conan-1",
        "repo": "conan-io/conan",
        "difficulty": {"files": 3, "hunks": 2, "lines": 9},
        "created_at": datetime(2024, 1, 1),
    }
    assert download._to_live_record(row) == LiveRecord(
        instance_id="conan-io__conan-1",
        repo="conan-io/conan",
        difficulty_files=3,
        created_at_year=2024,
    )


def test_to_live_record_defaults_missing_difficulty_files_to_zero():
    row = {
        "instance_id": "x__y-1",
        "repo": "x/y",
        "difficulty": None,
        "created_at": "2025-01-01",
    }
    assert download._to_live_record(row).difficulty_files == 0
