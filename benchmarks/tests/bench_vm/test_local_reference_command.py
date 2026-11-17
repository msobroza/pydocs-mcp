"""``local-command SPEC``: the runner command for an uninterrupted local run of a retrieval spec.

The printed command is the supervisor's child argv for the spec, with the
state directory moved to a local, git-ignored results directory and without
``--run-id``: the local reference runs the plain runner, not the resume path
the comparison checks. No cloud, no git, no credentials.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from pydocs_eval.bench_vm import load_run_spec
from pydocs_eval.bench_vm.__main__ import main

from ._spec_dicts import agent_track_spec, retrieval_spec, write_spec

Capsys = pytest.CaptureFixture[str]


def test_it_prints_the_runner_argv_with_local_paths_and_no_run_id(
    tmp_path: Path, capsys: Capsys
) -> None:
    spec = retrieval_spec(
        dataset="repoqa", configs=["benchmarks/configs/bm25.yaml"], args=["--limit", "4"]
    )
    path = write_spec(tmp_path, "cpu-resume-smoke", spec)
    local = f"benchmarks/results/bench-vm-local/{load_run_spec(path).run_id}"

    exit_code = main(["local-command", str(path)])

    out = capsys.readouterr()
    assert exit_code == 0, out.err
    assert shlex.split(out.out) == [
        "python",
        "-m",
        "pydocs_eval.runner",
        "--configs",
        "benchmarks/configs/bm25.yaml",
        "--dataset",
        "repoqa",
        "--results-dir",
        f"{local}/results",
        "--report",
        f"{local}/report.md",
        "--bench-cache",
        "on",
        "--limit",
        "4",
    ]


def test_a_paid_kind_has_no_local_reference_and_is_refused(tmp_path: Path, capsys: Capsys) -> None:
    path = write_spec(tmp_path, "agents", agent_track_spec())

    exit_code = main(["local-command", str(path)])

    out = capsys.readouterr()
    assert exit_code == 1
    assert out.out == ""
    assert (
        f"{path}: local-command refused: got kind agent_track, expected kind retrieval" in out.err
    )


def test_an_invalid_spec_is_refused_with_its_field_errors(tmp_path: Path, capsys: Capsys) -> None:
    path = write_spec(tmp_path, "typo", retrieval_spec(spto=False))

    exit_code = main(["local-command", str(path)])

    out = capsys.readouterr()
    assert exit_code == 1
    assert out.out == ""
    assert f"{path}: spto: got False, expected a known field" in out.err


def test_results_dir_moves_the_runs_local_directory(tmp_path: Path, capsys: Capsys) -> None:
    path = write_spec(tmp_path, "cpu-resume-smoke", retrieval_spec())
    local = tmp_path / "elsewhere" / load_run_spec(path).run_id

    exit_code = main(["local-command", str(path), "--results-dir", str(tmp_path / "elsewhere")])

    argv = shlex.split(capsys.readouterr().out)
    assert exit_code == 0
    assert argv[argv.index("--results-dir") + 1] == str(local / "results")
    assert argv[argv.index("--report") + 1] == str(local / "report.md")
