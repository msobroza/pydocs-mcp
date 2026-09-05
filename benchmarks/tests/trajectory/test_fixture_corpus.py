"""Self-validation of the Phase 2 trajectory fixtures (Task 5).

Two families are checked without any network / ``claude`` dependency:

- **The ``widgetlib`` corpus** — each edit task's ``FAIL_TO_PASS`` tests fail in
  the shipped buggy state and pass once the task's gold patch is applied, while
  ``PASS_TO_PASS`` tests stay green. The gold patches are ``-p1`` workspace-root
  diffs that touch only product files (never test files), matching the ADR 0011
  gold semantics.
- **The degenerate synthetic trajectories** — each ``events.jsonl`` round-trips
  through the schema-v1 parser and its ``meta.json`` declares an
  ADR 0012 taxonomy label; the marker-driven cases route through
  ``classify_infra_marker`` to their expected label.

These are the fixtures ADR 0011's attribution-validation gate and ADR 0012's
taxonomy tests consume, so their integrity is pinned here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pydocs_eval.trajectory.eval_report import classify_infra_marker
from pydocs_eval.trajectory.schema import (
    SCHEMA_VERSION,
    TrajectoryHeader,
    ToolEvent,
    parse_event_line,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_CORPUS = _FIXTURES / "corpus"
_SYNTH = _FIXTURES / "trajectories" / "synthetic"

# ADR 0012 failure-taxonomy labels (first-match order). The degenerate fixtures
# each declare exactly one; the taxonomy implementation (Task 7) asserts against
# these same declarations, so the vocabulary is pinned in one place.
_TAXONOMY_LABELS = frozenset(
    {
        "infra_error",
        "empty_trajectory",
        "crash_before_first_tool",
        "patch_apply_failed",
        "budget_exhausted",
        "never_ran_tests",
        "localization_miss",
        "found_but_misdiagnosed",
        "right_idea_broken_edit",
        "regression_introduced",
    }
)

_TASK_NAMES = (
    "calculator_average",
    "inventory_value",
    "textutil_slugify",
    "pricing_discount",
)

_FAILED_LINE = re.compile(r"^FAILED (\S+)")


def _load_task(name: str) -> dict:
    return json.loads((_CORPUS / "tasks" / f"{name}.json").read_text())


def _materialize(dest: Path) -> Path:
    """Copy the buggy corpus into ``dest`` and git-init a base commit."""
    shutil.copytree(_CORPUS / "src", dest, dirs_exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=dest,
        check=True,
    )
    return dest


def _run_pytest(cwd: Path, nodeids: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider", *nodeids],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_buggy_state_fails_exactly_the_f2p_set(tmp_path: Path) -> None:
    """In the shipped buggy state, the FAILED set equals the union of all F2P."""
    ws = _materialize(tmp_path / "ws")
    result = _run_pytest(ws, ["tests/"])
    failed = {m.group(1) for line in result.stdout.splitlines() if (m := _FAILED_LINE.match(line))}
    expected = {nid for name in _TASK_NAMES for nid in _load_task(name)["FAIL_TO_PASS"]}
    assert failed == expected, f"buggy FAILED set {failed!r} != declared F2P {expected!r}"


@pytest.mark.parametrize("name", _TASK_NAMES)
def test_gold_patch_resolves_task(name: str, tmp_path: Path) -> None:
    """Applying a task's gold patch makes its F2P + P2P all pass."""
    task = _load_task(name)
    ws = _materialize(tmp_path / name)
    patch = (_CORPUS / task["gold_patch_path"]).read_text()
    subprocess.run(["git", "apply", "-"], cwd=ws, input=patch, text=True, check=True)
    result = _run_pytest(ws, [*task["FAIL_TO_PASS"], *task["PASS_TO_PASS"]])
    assert result.returncode == 0, f"{name}: post-gold suite not green\n{result.stdout}"


@pytest.mark.parametrize("name", _TASK_NAMES)
def test_gold_patch_touches_only_product_files(name: str) -> None:
    """Gold patches modify product files, never test files (ADR 0011 disjoint)."""
    task = _load_task(name)
    patch = (_CORPUS / task["gold_patch_path"]).read_text()
    touched = re.findall(r"^\+\+\+ b/(\S+)", patch, flags=re.MULTILINE)
    assert touched, f"{name}: gold patch names no target file"
    assert all(not p.startswith("tests/") for p in touched), touched
    assert set(touched) == set(task["modified_files"]), (touched, task["modified_files"])


@pytest.mark.parametrize("name", ["empty_trajectory", "crash_before_first_tool"])
def test_degenerate_trace_roundtrips_and_has_no_tool_call(name: str) -> None:
    """The trace-only degenerate cases parse under schema-v1 with no ToolEvent."""
    events = _parse_events(name)
    assert isinstance(events[0], TrajectoryHeader)
    assert events[0].schema_version == SCHEMA_VERSION
    assert not any(isinstance(e, ToolEvent) for e in events), "expected no tool call"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("patch_apply_failed", "patch_apply_failed"), ("infra_error", "infra_error")],
)
def test_marker_case_routes_to_expected_label(name: str, expected: str) -> None:
    """The marker-driven degenerate cases classify to their declared label."""
    log_text = (_SYNTH / name / "run_log.txt").read_text()
    assert classify_infra_marker(log_text) == expected


@pytest.mark.parametrize(
    "name",
    ["empty_trajectory", "crash_before_first_tool", "patch_apply_failed", "infra_error"],
)
def test_synthetic_meta_declares_known_taxonomy(name: str) -> None:
    """Every synthetic case declares one recognized ADR 0012 taxonomy label."""
    meta = json.loads((_SYNTH / name / "meta.json").read_text())
    assert meta["expected_taxonomy"] in _TAXONOMY_LABELS
    assert meta["case"] == name
    events = _parse_events(name)  # events.jsonl must be present + parseable
    assert isinstance(events[0], TrajectoryHeader)


def _parse_events(name: str) -> list:
    lines = (_SYNTH / name / "events.jsonl").read_text().splitlines()
    return [parse_event_line(json.loads(line)) for line in lines if line.strip()]
