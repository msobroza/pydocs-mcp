"""Each kind's child argv: its CLI, its typed inputs as flags, the owned flags, then the passthrough.

The flags the supervisor owns point into the state directory (results,
reports, ledgers) and the cache directory (before/after's workspaces and its
product clone); ``--gpu`` appears on a GPU run only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.bench_vm.kind_flags import KindInvocation
from pydocs_eval.bench_vm.run_kinds import RUN_KIND_TABLE, RunKind

from ._git_fixture import FIXTURE_CONFIG, FIXTURE_CORPUS_DIR, run_git
from ._spec_dicts import agent_track_spec, before_after_spec, retrieval_spec
from ._supervise_harness import SuperviseHarness


def test_the_retrieval_runner_gets_its_inputs_and_the_run_files_flags(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = retrieval_spec(
        dataset="ds1000",
        split="small_test",
        corpus_dir=FIXTURE_CORPUS_DIR,
        args=["--limit", "5", "--fixture", "benchmarks/tests/fixtures/ds1000_mini.json"],
    )
    run_id = supervise_harness.launch_run(capsys, "ds1000", spec)

    started = supervise_harness.supervise(capsys, run_id).started

    state = supervise_harness.state_dir
    assert started["argv"] == [
        "-m",
        "pydocs_eval.runner",
        "--configs",
        FIXTURE_CONFIG,
        "--dataset",
        "ds1000",
        "--split",
        "small_test",
        "--corpus-dir",
        FIXTURE_CORPUS_DIR,
        "--run-id",
        run_id,
        "--results-dir",
        str(state / "results"),
        "--report",
        str(state / "report.md"),
        "--limit",
        "5",
        "--fixture",
        "benchmarks/tests/fixtures/ds1000_mini.json",
    ]


def test_a_retrieval_run_without_optional_inputs_passes_only_the_configs_and_owned_flags(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    argv = supervise_harness.supervise(capsys, run_id).started["argv"]

    assert argv[:4] == ["-m", "pydocs_eval.runner", "--configs", FIXTURE_CONFIG]
    assert argv[4] == "--run-id"
    assert "--gpu" not in argv


def test_a_gpu_retrieval_run_also_gets_gpu_before_the_passthrough(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(
        capsys, "gpu-smoke", retrieval_spec(gpu={}, args=["--limit", "2"])
    )

    argv = supervise_harness.supervise(capsys, run_id).started["argv"]

    assert argv[-3:] == ["--gpu", "--limit", "2"]
    assert argv.count("--gpu") == 1


def test_the_agent_track_gets_its_dataset_spend_cap_ledger_and_report(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = agent_track_spec(dataset="swe-qa", max_usd=20.0, args=["--max-tasks", "3"])
    run_id = supervise_harness.launch_run(capsys, "agents", spec)

    started = supervise_harness.supervise(capsys, run_id).started

    state = supervise_harness.state_dir
    assert started["argv"] == [
        "-m",
        "pydocs_eval.agent_track",
        "--dataset",
        "swe-qa",
        "--max-usd",
        "20.0",
        "--ledger",
        str(state / "agent_track_pairs.jsonl"),
        "--report",
        str(state / "report.md"),
        "--max-tasks",
        "3",
    ]


def test_before_after_gets_the_frozen_commits_and_its_directories(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = supervise_harness.launch.repo
    baseline = repo.head()
    run_git(repo.path, "tag", "v-old", baseline)
    repo.write("notes/candidate.txt", "candidate\n")
    candidate = repo.commit("candidate commit")
    repo.push_main()
    supervise_harness.launch.publish_images(candidate)
    spec = before_after_spec(
        configs=[FIXTURE_CONFIG],
        baseline_ref="v-old",
        candidate_ref="main",
        max_usd=5,
        args=["--model", "qwen/qwen3-4b"],
    )
    run_id = supervise_harness.launch_run(capsys, "before-after", spec)

    started = supervise_harness.supervise(capsys, run_id).started

    state, cache = supervise_harness.state_dir, supervise_harness.cache_dir
    assert started["argv"] == [
        "-m",
        "pydocs_eval.campaign",
        "before-after",
        "--baseline",
        baseline,
        "--candidate",
        candidate,
        "--config",
        FIXTURE_CONFIG,
        "--split",
        "repoqa/small_test",
        "--max-usd",
        "5.0",
        "--confirm-spend",
        "--workspace",
        str(cache / "before-after" / "workspace"),
        "--out",
        str(state / "before-after"),
        "--repo",
        str(cache / "product-repo"),
        "--model",
        "qwen/qwen3-4b",
    ]


@pytest.mark.parametrize("kind", list(RunKind))
@pytest.mark.parametrize("gpu", [False, True])
def test_every_flag_a_row_fills_is_one_it_owns(kind: RunKind, gpu: bool, tmp_path: Path) -> None:
    row = RUN_KIND_TABLE[kind]
    invocation = KindInvocation(
        run_id="r-0123abcd",
        dataset="repoqa",
        split="small_test",
        configs=(FIXTURE_CONFIG,),
        corpus_dir=FIXTURE_CORPUS_DIR,
        baseline_commit="a" * 40,
        candidate_commit="b" * 40,
        max_usd=5.0,
        gpu=gpu,
        state_dir=tmp_path / "state",
        cache_dir=tmp_path / "cache",
    )

    flags = [arg for arg in row.owned_flag_values(invocation) if arg.startswith("--")]

    assert flags
    assert set(flags) <= row.owned_flags
