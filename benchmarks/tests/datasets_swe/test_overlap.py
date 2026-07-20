"""R2 overlap check (ADR 0013 deliverable 2)."""

from __future__ import annotations

from pydocs_eval.datasets_swe.overlap import (
    compute_overlap,
    excluded_instance_ids,
    render_markdown,
)
from pydocs_eval.datasets_swe.records import LiveRecord


def _rec(instance_id: str, repo: str) -> LiveRecord:
    return LiveRecord(instance_id=instance_id, repo=repo, difficulty_files=1, created_at_year=2024)


def _live_like_ansible() -> list[LiveRecord]:
    # Mirrors the measured shape: no repo-level collision, an ansible org near-miss.
    return [
        _rec("ansible__ansible-lint-1", "ansible/ansible-lint"),
        _rec("ansible__ansible-lint-2", "ansible/ansible-lint"),
        _rec("ansible__ansible-lint-3", "ansible/ansible-lint"),
        _rec("ansible__molecule-1", "ansible/molecule"),
        _rec("ansible__molecule-2", "ansible/molecule"),
        _rec("conan__conan-1", "conan-io/conan"),
        _rec("flask__flask-1", "pallets/flask"),
    ]


_PRO = ["ansible/ansible", "internetarchive/openlibrary", "qutebrowser/qutebrowser"]


def test_repo_intersection_is_empty_org_intersection_finds_ansible():
    report = compute_overlap(_live_like_ansible(), _PRO)
    assert report.repo_intersection == ()  # different repos → clean at repo granularity
    assert report.org_intersection == ("ansible",)


def test_org_exclusion_removes_five_ansible_org_instances():
    report = compute_overlap(_live_like_ansible(), _PRO)
    assert report.excluded_instances == 5
    assert dict(report.excluded_by_repo) == {
        "ansible/ansible-lint": 3,
        "ansible/molecule": 2,
    }


def test_excluded_instance_ids_are_the_ansible_org_ids():
    excluded = excluded_instance_ids(_live_like_ansible(), _PRO)
    assert excluded == {
        "ansible__ansible-lint-1",
        "ansible__ansible-lint-2",
        "ansible__ansible-lint-3",
        "ansible__molecule-1",
        "ansible__molecule-2",
    }


def test_render_markdown_reports_empty_repo_intersection():
    report = compute_overlap(_live_like_ansible(), _PRO, live_raw_rows=8, pro_python_instances=266)
    md = render_markdown(report)
    assert "∅ (empty)" in md
    assert "`ansible`" in md
    assert "266" in md
