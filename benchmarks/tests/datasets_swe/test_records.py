"""Row model + dedupe rule (ADR 0013 deliverable 1)."""

from __future__ import annotations

import pytest

from pydocs_eval.datasets_swe.records import (
    LiveRecord,
    dedupe_live_records,
    org_of,
)


def _rec(instance_id: str, repo: str = "a/b", files: int = 1, year: int = 2024) -> LiveRecord:
    return LiveRecord(
        instance_id=instance_id, repo=repo, difficulty_files=files, created_at_year=year
    )


def test_org_of_extracts_owner():
    assert org_of("ansible/ansible-lint") == "ansible"
    assert _rec("i", repo="conan-io/conan").org == "conan-io"


def test_org_of_rejects_unslashed_slug():
    with pytest.raises(ValueError, match="invalid repo slug"):
        org_of("noslash")


def test_dedupe_drops_second_conan_occurrence_only():
    records = [
        _rec("conan-io__conan-18153", repo="conan-io/conan"),
        _rec("other-1"),
        _rec("conan-io__conan-18153", repo="conan-io/conan"),  # the known dup
    ]
    out = dedupe_live_records(records)
    ids = [r.instance_id for r in out]
    assert ids == ["conan-io__conan-18153", "other-1"]


def test_dedupe_raises_on_unexpected_duplicate():
    records = [_rec("surprise-dup"), _rec("surprise-dup")]
    with pytest.raises(ValueError, match="unexpected duplicate"):
        dedupe_live_records(records)
