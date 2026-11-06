"""``pull SPEC`` (``make bench-vm-pull``): bring a run's prefix home into a git-ignored results directory.

Driven through the entry point against ``FakeBenchCloud``; the run is launched
for real first (record and spec copy), and the harness stands in for the
supervisor's state syncs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.run_control import LOCAL_RUNS_RESULTS_DIR

from ._launch_harness import LaunchHarness
from ._run_control_harness import launch_fixture_run, run_control, run_prefix
from ._spec_dicts import REPO_ROOT, write_spec

Capsys = pytest.CaptureFixture[str]


def _files(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def test_pull_copies_the_runs_results_spec_copy_and_record(
    harness: LaunchHarness, capsys: Capsys, tmp_path: Path
) -> None:
    launched = launch_fixture_run(harness, capsys)
    prefix = run_prefix(harness, launched.run_id)
    harness.cloud.put_object(prefix + "state/results/repoqa.jsonl", b'{"event": "run_start"}\n')
    results_dir = tmp_path / "results"

    result = run_control(
        harness, capsys, "pull", str(launched.spec_path), "--results-dir", str(results_dir)
    )

    assert result.exit_code == 0, result.err
    pulled = results_dir / launched.run_id
    assert _files(pulled) == ["run.json", "spec.yaml", "state/results/repoqa.jsonl"]
    assert (pulled / "spec.yaml").read_bytes() == launched.spec_path.read_bytes()
    assert str(pulled) in result.out


def test_pull_leaves_other_runs_files_alone(
    harness: LaunchHarness, capsys: Capsys, tmp_path: Path
) -> None:
    launched = launch_fixture_run(harness, capsys)
    harness.cloud.put_object(f"gs://bench-runs/{launched.run_id}x/run.json", b"another run")
    results_dir = tmp_path / "results"
    other = results_dir / "another-run-0123abcd"
    other.mkdir(parents=True)
    (other / "run.json").write_text("kept", encoding="utf-8")

    run_control(harness, capsys, "pull", str(launched.spec_path), "--results-dir", str(results_dir))

    assert sorted(path.name for path in results_dir.iterdir()) == sorted(
        [launched.run_id, "another-run-0123abcd"]
    )
    assert (other / "run.json").read_text(encoding="utf-8") == "kept"


def test_a_second_pull_brings_new_files_and_keeps_local_ones(
    harness: LaunchHarness, capsys: Capsys, tmp_path: Path
) -> None:
    launched = launch_fixture_run(harness, capsys)
    results_dir = tmp_path / "results"
    argv = ("pull", str(launched.spec_path), "--results-dir", str(results_dir))
    run_control(harness, capsys, *argv)
    (results_dir / launched.run_id / "report-notes.md").write_text("mine", encoding="utf-8")
    harness.cloud.put_object(run_prefix(harness, launched.run_id) + "state/new.jsonl", b"x\n")

    run_control(harness, capsys, *argv)

    assert {"report-notes.md", "state/new.jsonl"} <= set(_files(results_dir / launched.run_id))


def test_a_run_never_launched_is_refused_and_nothing_is_downloaded(
    harness: LaunchHarness, capsys: Capsys, tmp_path: Path
) -> None:
    path = write_spec(
        harness.specs_dir, "never", {"git_ref": "main", "kind": "retrieval", "configs": ["c.yaml"]}
    )

    result = run_control(harness, capsys, "pull", str(path), "--results-dir", str(tmp_path / "r"))

    assert result.exit_code == 1
    assert "no run record" in result.err
    assert "download_prefix" not in harness.cloud.operations()
    assert not (tmp_path / "r").exists()


def test_the_default_results_directory_is_git_ignored() -> None:
    probe = LOCAL_RUNS_RESULTS_DIR / "cpu-smoke-0123abcd" / "state" / "results" / "x.jsonl"

    completed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "--no-index", "-q", str(probe)],
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert not probe.is_absolute()
    assert completed.returncode == 0, f"{probe} is not git-ignored"
