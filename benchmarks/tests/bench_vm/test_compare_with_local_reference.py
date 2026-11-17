"""``compare SPEC``: a pulled run's final aggregates against a local, uninterrupted run of the same spec.

The run side is the layout ``pull`` writes (``<pulled>/<run id>/state/results/``,
the runner's ``--run-id`` leg files); the local side is the layout
``local-command`` points the plain runner at (``<local>/<run id>/results/``,
one timestamped file per leg). The happy path's files are written by the real
sweep, through the runner-resume test fakes: one leg interrupted on a task
and resumed under a run id, one leg run without interruption.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_eval.bench_vm import load_run_spec
from pydocs_eval.bench_vm.__main__ import main
from pydocs_eval.sweep import run_sweep_detailed
from pydocs_eval.sweep_support import COMMIT_SHA_ENV_VAR

from ..core.runner_resume._resume_fakes import (
    COUNTING_SYSTEM,
    FAKE_DATASET,
    TASK_SCORE_METRIC,
    CountingFakeSystem,
    InjectedTaskFailure,
    fake_query,
    reset_resume_fakes,
)
from ._leg_files import LEG_NAME, leg_name, local_leg_path, run_leg_path, write_leg
from ._spec_dicts import REPO_ROOT, agent_track_spec, retrieval_spec, write_spec

Capsys = pytest.CaptureFixture[str]


@dataclass(frozen=True, slots=True)
class CompareResult:
    exit_code: int
    out: str
    err: str


@pytest.fixture(autouse=True)
def _fresh_fakes_and_a_pinned_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    # The header's commit comes from the image variable, never this checkout's git.
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "no-such-git-dir"))
    monkeypatch.setenv(COMMIT_SHA_ENV_VAR, "c" * 40)
    reset_resume_fakes()
    yield
    reset_resume_fakes()


def _overlay(tmp_path: Path) -> Path:
    path = tmp_path / "configs" / "bm25.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text("")
    return path


async def _sweep(results_dir: Path, overlay: Path, **overrides: object) -> None:
    await run_sweep_detailed(
        systems=(COUNTING_SYSTEM,),
        config_paths=(overlay,),
        dataset_name=FAKE_DATASET,
        tracker_kwargs={"jsonl": {"output_dir": results_dir}},
        metric_specs=(TASK_SCORE_METRIC,),
        bench_cache=False,
        **overrides,
    )


async def _stopped_then_resumed(results_dir: Path, overlay: Path, run_id: str) -> None:
    """The run side: interrupted on task 3, then relaunched under the same run id."""
    CountingFakeSystem.fail_on_query = fake_query(3)
    with pytest.raises(InjectedTaskFailure):
        await _sweep(results_dir, overlay, run_id=run_id)
    reset_resume_fakes()
    await _sweep(results_dir, overlay, run_id=run_id)


async def test_a_resumed_run_matches_an_uninterrupted_local_run_and_exits_zero(
    tmp_path: Path, capsys: Capsys
) -> None:
    spec = write_spec(tmp_path / "runs", "cpu-resume-smoke", retrieval_spec())
    run_id = load_run_spec(spec).run_id
    pulled, local, overlay = tmp_path / "pulled", tmp_path / "local", _overlay(tmp_path)
    await _stopped_then_resumed(pulled / run_id / "state" / "results", overlay, run_id)
    await _sweep(local / run_id / "results", overlay)
    capsys.readouterr()

    exit_code = main(
        [
            "compare",
            str(spec),
            "--pulled-results-dir",
            str(pulled),
            "--local-results-dir",
            str(local),
        ]
    )

    out = capsys.readouterr()
    assert exit_code == 0, out.err
    assert out.out.splitlines()[-1] == (
        "identical: 3 aggregates bit-identical across 1 leg; "
        "6 wall-clock aggregates reported, not compared"
    )


def _spec(tmp_path: Path) -> tuple[Path, str]:
    path = write_spec(tmp_path / "runs", "cpu-resume-smoke", retrieval_spec())
    return path, load_run_spec(path).run_id


def _compare(tmp_path: Path, spec: Path, capsys: Capsys) -> CompareResult:
    capsys.readouterr()
    exit_code = main(
        [
            "compare",
            str(spec),
            "--pulled-results-dir",
            str(tmp_path / "pulled"),
            "--local-results-dir",
            str(tmp_path / "local"),
        ]
    )
    captured = capsys.readouterr()
    return CompareResult(exit_code=exit_code, out=captured.out, err=captured.err)


def test_a_differing_aggregate_fails_with_a_got_expected_line(
    tmp_path: Path, capsys: Capsys
) -> None:
    spec, run_id = _spec(tmp_path)
    write_leg(run_leg_path(tmp_path / "pulled", run_id), {"mrr_mean": 0.5, "mrr_ci_low": 0.1})
    write_leg(local_leg_path(tmp_path / "local", run_id), {"mrr_mean": 0.25, "mrr_ci_low": 0.1})

    result = _compare(tmp_path, spec, capsys)

    assert result.exit_code == 1
    assert result.err.splitlines() == [
        f"{spec}: compare: leg {LEG_NAME}: aggregate mrr_mean: "
        "got 0x1.0000000000000p-1 (0.5), expected 0x1.0000000000000p-2 (0.25)"
    ]


def test_wall_clock_aggregates_are_reported_with_both_values_but_not_compared(
    tmp_path: Path, capsys: Capsys
) -> None:
    spec, run_id = _spec(tmp_path)
    write_leg(
        run_leg_path(tmp_path / "pulled", run_id), {"mrr_mean": 0.5, "search_seconds_p50": 0.1}
    )
    write_leg(
        local_leg_path(tmp_path / "local", run_id), {"mrr_mean": 0.5, "search_seconds_p50": 0.25}
    )

    result = _compare(tmp_path, spec, capsys)

    assert result.exit_code == 0, result.err
    assert "  wall-clock, not compared: search_seconds_p50: run 0.1, local 0.25" in result.out
    assert "  identical: mrr_mean = 0.5" in result.out


def test_a_run_stopped_before_its_final_block_is_reported_as_partial(
    tmp_path: Path, capsys: Capsys
) -> None:
    spec, run_id = _spec(tmp_path)
    run_leg = write_leg(run_leg_path(tmp_path / "pulled", run_id), None, tasks=3, task_records=True)
    write_leg(local_leg_path(tmp_path / "local", run_id), {"mrr_mean": 0.5})

    result = _compare(tmp_path, spec, capsys)

    assert result.exit_code == 1
    assert result.err.splitlines() == [
        f"{spec}: compare: leg {LEG_NAME}: got no final aggregate block in the run's "
        f"{run_leg} (3 task records), expected a finished leg; relaunch the run with "
        "make bench-vm, then pull it again"
    ]


def test_a_local_reference_that_did_not_finish_is_reported(tmp_path: Path, capsys: Capsys) -> None:
    spec, run_id = _spec(tmp_path)
    write_leg(run_leg_path(tmp_path / "pulled", run_id), {"mrr_mean": 0.5}, task_records=True)
    local_leg = write_leg(local_leg_path(tmp_path / "local", run_id), None)

    result = _compare(tmp_path, spec, capsys)

    assert result.exit_code == 1
    assert result.err.splitlines() == [
        f"{spec}: compare: leg {LEG_NAME}: got no final aggregate block in the local "
        f"reference's {local_leg}, expected a finished leg; run the command local-command "
        "prints to the end, into an empty results directory"
    ]


def test_no_leg_files_on_either_side_never_passes(tmp_path: Path, capsys: Capsys) -> None:
    spec, run_id = _spec(tmp_path)
    run_results = tmp_path / "pulled" / run_id / "state" / "results"
    local_results = tmp_path / "local" / run_id / "results"

    result = _compare(tmp_path, spec, capsys)

    assert result.exit_code == 1
    assert result.err.splitlines() == [
        f"{spec}: compare: got no leg files under {run_results}, expected the run's; "
        "pull it first with make bench-vm-pull",
        f"{spec}: compare: got no leg files under {local_results}, expected the local "
        "reference's; run the command local-command prints first",
    ]


def test_a_leg_on_one_side_only_is_named_with_where_it_was_found(
    tmp_path: Path, capsys: Capsys
) -> None:
    spec, run_id = _spec(tmp_path)
    run_leg = write_leg(run_leg_path(tmp_path / "pulled", run_id), {"mrr_mean": 0.5})
    local_leg = write_leg(
        local_leg_path(tmp_path / "local", run_id, config_name="dense"),
        {"mrr_mean": 0.5},
        config_name="dense",
    )

    result = _compare(tmp_path, spec, capsys)

    run_results = tmp_path / "pulled" / run_id / "state" / "results"
    local_results = tmp_path / "local" / run_id / "results"
    assert result.exit_code == 1
    assert result.err.splitlines() == [
        f"{spec}: compare: leg {LEG_NAME}: got no leg file under {local_results}, "
        f"expected one to match the run's {run_leg}",
        f"{spec}: compare: leg {leg_name('dense')}: got no leg file under {run_results}, "
        f"expected one to match the local reference's {local_leg}",
    ]


def test_a_run_not_pulled_yet_is_one_problem_not_one_per_local_leg(
    tmp_path: Path, capsys: Capsys
) -> None:
    spec, run_id = _spec(tmp_path)
    for config in ("bm25", "dense"):
        path = local_leg_path(tmp_path / "local", run_id, config_name=config)
        write_leg(path, {"mrr_mean": 0.5}, config_name=config)

    result = _compare(tmp_path, spec, capsys)

    run_results = tmp_path / "pulled" / run_id / "state" / "results"
    assert result.exit_code == 1
    assert result.err.splitlines() == [
        f"{spec}: compare: got no leg files under {run_results}, expected the run's; "
        "pull it first with make bench-vm-pull"
    ]


def test_two_files_for_one_leg_on_a_side_are_refused_naming_both(
    tmp_path: Path, capsys: Capsys
) -> None:
    spec, run_id = _spec(tmp_path)
    write_leg(run_leg_path(tmp_path / "pulled", run_id), {"mrr_mean": 0.5})
    first, second = (
        write_leg(local_leg_path(tmp_path / "local", run_id, stamp=stamp), {"mrr_mean": 0.5})
        for stamp in ("20261117T000000Z", "20261117T010000Z")
    )

    result = _compare(tmp_path, spec, capsys)

    local_results = tmp_path / "local" / run_id / "results"
    assert result.exit_code == 1
    assert result.err.splitlines() == [
        f"{spec}: compare: leg {LEG_NAME}: got 2 leg files under {local_results} "
        f"({first}, {second}), expected one; move the extra aside"
    ]


def test_a_jsonl_without_a_run_start_header_is_named_not_skipped(
    tmp_path: Path, capsys: Capsys
) -> None:
    spec, run_id = _spec(tmp_path)
    write_leg(run_leg_path(tmp_path / "pulled", run_id), {"mrr_mean": 0.5})
    empty = run_leg_path(tmp_path / "pulled", run_id, config_name="dense")
    empty.write_text("", encoding="utf-8")
    write_leg(local_leg_path(tmp_path / "local", run_id), {"mrr_mean": 0.5})

    result = _compare(tmp_path, spec, capsys)

    assert result.exit_code == 1
    assert result.err.splitlines() == [
        f"{spec}: compare: got no run_start header in {empty}, expected a leg file the runner "
        "wrote; a runner stopped before its first line leaves one empty"
    ]


def test_each_leg_names_both_sides_run_tags_and_notes_differing_commits(
    tmp_path: Path, capsys: Capsys
) -> None:
    spec, run_id = _spec(tmp_path)
    run_tags = {"git_sha": "a" * 40, "platform": "Linux-x86_64", "python_version": "3.12.9"}
    local_tags = {"git_sha": "b" * 40, "platform": "Linux-x86_64", "python_version": "3.12.9"}
    write_leg(run_leg_path(tmp_path / "pulled", run_id), {"mrr_mean": 0.5}, tags=run_tags)
    write_leg(local_leg_path(tmp_path / "local", run_id), {"mrr_mean": 0.5}, tags=local_tags)

    result = _compare(tmp_path, spec, capsys)

    assert result.exit_code == 0, result.err
    lines = result.out.splitlines()
    assert f"  run tags:   git_sha={'a' * 40} platform=Linux-x86_64 python_version=3.12.9" in lines
    assert f"  local tags: git_sha={'b' * 40} platform=Linux-x86_64 python_version=3.12.9" in lines
    assert (
        "  note: the two sides ran different commits; the local reference should run "
        "the run's commit" in lines
    )


def test_a_paid_kind_is_refused_like_local_command(tmp_path: Path, capsys: Capsys) -> None:
    spec = write_spec(tmp_path / "runs", "agents", agent_track_spec())

    result = _compare(tmp_path, spec, capsys)

    assert result.exit_code == 1
    assert result.out == ""
    assert result.err.splitlines() == [
        f"{spec}: compare refused: got kind agent_track, expected kind retrieval: only the "
        "retrieval runner's leg files have a local reference to compare with"
    ]


def test_compare_reaches_no_cloud_binary_and_imports_nothing_from_the_product(
    tmp_path: Path,
) -> None:
    # An empty PATH: a gcloud or terraform call would fail the verb; the leaked
    # modules check keeps the laptop-side floor (the eval base install).
    spec, run_id = _spec(tmp_path)
    write_leg(run_leg_path(tmp_path / "pulled", run_id), {"mrr_mean": 0.5})
    write_leg(local_leg_path(tmp_path / "local", run_id), {"mrr_mean": 0.5})
    script = (
        "import sys\n"
        "from pydocs_eval.bench_vm.__main__ import main\n"
        "code = main(sys.argv[1:])\n"
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] == 'pydocs_mcp')\n"
        "assert not leaked, leaked\n"
        "sys.exit(code)\n"
    )
    argv = [
        "compare",
        str(spec),
        "--pulled-results-dir",
        str(tmp_path / "pulled"),
        "--local-results-dir",
        str(tmp_path / "local"),
    ]
    env = {"PATH": "", "HOME": str(tmp_path), "PYTHONPATH": str(REPO_ROOT / "benchmarks" / "src")}

    result = subprocess.run(
        [sys.executable, "-c", script, *argv],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1].startswith("identical: 1 aggregates")


def test_it_lists_each_sides_leg_files_with_progress_even_with_nothing_to_compare_yet(
    tmp_path: Path, capsys: Capsys
) -> None:
    # A mid-run pull, before the local reference exists: the owner's progress check.
    spec, run_id = _spec(tmp_path)
    write_leg(run_leg_path(tmp_path / "pulled", run_id), None, tasks=8, task_records=True)

    result = _compare(tmp_path, spec, capsys)

    run_results = tmp_path / "pulled" / run_id / "state" / "results"
    local_results = tmp_path / "local" / run_id / "results"
    assert result.exit_code == 1
    assert result.out.splitlines()[:4] == [
        f"run:   {run_results}",
        f"  {run_id}/pydocs-mcp_bm25_repoqa_at_2024-06-23.jsonl: leg {LEG_NAME}, "
        "8 task records, no final aggregate block yet",
        f"local: {local_results}",
        "  (no leg files)",
    ]
