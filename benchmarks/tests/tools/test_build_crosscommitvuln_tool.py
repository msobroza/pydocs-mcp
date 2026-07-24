"""Unit tests for the pure decision logic of the network build tool.
The tool is NOT an installed module — load it from its file path. Git and
network are stubbed; the heavy construction run is a documented one-time
step (design §6.3), not a test."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_TOOL = Path(__file__).parents[2] / "tools" / "build_crosscommitvuln.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("build_crosscommitvuln", _TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _annotation(cve_id: str) -> dict:
    return {
        "cve_id": cve_id,
        "repo": "https://github.com/exampleorg/exampleproj",
        "ecosystem": "PyPI",
        "cwe_ids": ["CWE-78"],
        "severity_combined": "high",
        "summary": "OS command injection via exec_cmd()",
        "fix_commit": "a" * 40,
        "annotation_status": "complete+sast",
        "contributing_commits": [
            {
                "hash": "b" * 40,
                "short_hash": "bbbbbbbb",
                "date": "2024-01-15",
                "subject": "refactor",
                "role": "SINK — exec_cmd with user input",
                "files_changed": ["app/jobs.py"],
            }
        ],
        "vulnerability_chain": {"description": "taint flows to exec_cmd sink."},
    }


def test_co_resident_cves_excludes_self_and_uses_predicate() -> None:
    tool = _load_tool()
    record = _annotation("CVE-2099-0001")
    siblings = [record, _annotation("CVE-2099-0002"), _annotation("CVE-2099-0003")]
    co = tool.co_resident_cves(
        record, siblings, is_assembled=lambda other: other["cve_id"] == "CVE-2099-0002"
    )
    # The drop path (design §5.2): CVE-0002 co-resides -> the caller DROPS
    # this record; self and non-assembled CVE-0003 are never reported.
    assert co == ("CVE-2099-0002",)


def test_build_record_shape_and_leak_check_wired() -> None:
    tool = _load_tool()
    record, banned_row = tool.build_record(_annotation("CVE-2099-0001"), "f" * 40)
    assert record["task_id"] == "cve-2099-0001"
    assert record["repo_url"] == "https://github.com/exampleorg/exampleproj"
    assert record["prefix_sha"] == "f" * 40
    assert record["gold"]["cve_id"] == "CVE-2099-0001"
    assert record["gold"]["cwe_ids"] == ["CWE-78"]
    assert record["gold"]["files"] == ["app/jobs.py"]
    assert record["gold"]["mechanism"] == "taint flows to exec_cmd sink."
    assert record["metadata"]["co_resident_cves"] == ""
    assert banned_row["task_id"] == "cve-2099-0001" and "exec_cmd" in banned_row["banned"]
    # The query the record carries already passed assert_query_clean inside
    # build_record; verify it re-passes against the stored ban list.
    from pydocs_eval.datasets._crosscommitvuln_build import assert_query_clean

    assert_query_clean(record["query"], banned_row["banned"])
