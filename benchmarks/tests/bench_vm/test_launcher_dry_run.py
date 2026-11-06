"""``launch --dry-run`` (``DRY_RUN=1``): the laptop-only checks and the plan; no cloud, no writes."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.__main__ import main
from pydocs_eval.bench_vm.run_spec_file import load_run_spec
from pydocs_eval.bench_vm.vm_request import (
    METADATA_IMAGE,
    METADATA_RUN_ID,
    METADATA_SHUTDOWN_SCRIPT,
    METADATA_STARTUP_SCRIPT,
)

from ._git_fixture import FIXTURE_CONFIG, FIXTURE_CORPUS_DIR, make_fixture_repo
from ._spec_dicts import REPO_ROOT, retrieval_spec, write_spec

Capsys = pytest.CaptureFixture[str]


def _spec(**overrides: object) -> dict[str, object]:
    return retrieval_spec(
        **{"configs": [FIXTURE_CONFIG], "corpus_dir": FIXTURE_CORPUS_DIR, **overrides}
    )


def _dry_run(capsys: Capsys, spec_path: Path, repo: Path) -> tuple[int, str, str]:
    exit_code = main(["launch", str(spec_path), "--repo", str(repo), "--dry-run"])
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def test_a_dry_run_of_a_valid_spec_prints_the_plan_and_exits_zero(
    tmp_path: Path, capsys: Capsys
) -> None:
    repo = make_fixture_repo(tmp_path / "git")
    spec_path = write_spec(tmp_path / "runs", "cpu-smoke", _spec(max_runtime="1h"))
    run_id = load_run_spec(spec_path).run_id

    exit_code, out, err = _dry_run(capsys, spec_path, repo.path)

    assert exit_code == 0, err
    commit = repo.head()
    assert "DRY RUN" in out
    assert f"run id: {run_id}" in out
    assert f"git_ref: main -> {commit}" in out
    assert f"/bench-cpu:{commit}" in out
    assert f"/{run_id}/run.json" in out
    assert f"/{run_id}/spec.yaml" in out
    assert f"name: bench-{run_id}-1" in out
    assert "provisioning model: SPOT" in out
    assert "max run duration: 3600s, then DELETE" in out
    assert "external IP: none" in out
    assert f"bench-run-id={run_id}" in out
    for key in (METADATA_RUN_ID, METADATA_IMAGE, METADATA_STARTUP_SCRIPT, METADATA_SHUTDOWN_SCRIPT):
        assert key in out
    assert '"status": "launching"' in out


def test_a_dry_run_marks_deployment_names_as_placeholders_and_lists_unchecked_cloud_facts(
    tmp_path: Path, capsys: Capsys
) -> None:
    repo = make_fixture_repo(tmp_path / "git")
    spec_path = write_spec(tmp_path / "runs", "cpu-smoke", _spec(zone="europe-west4-b"))

    exit_code, out, _ = _dry_run(capsys, spec_path, repo.path)

    assert exit_code == 0
    assert "<runs_bucket: terraform output, not read in a dry run>" in out
    assert "zone order: europe-west4-b, <zones: terraform output, not read in a dry run>" in out
    not_checked = out.split("not checked (dry run):", 1)[1]
    for fact in ("machine-type facts", "image tag", "existing run record", "live VM"):
        assert fact in not_checked


def test_a_dry_run_still_refuses_what_the_laptop_can_check(tmp_path: Path, capsys: Capsys) -> None:
    repo = make_fixture_repo(tmp_path / "git")
    unpushed = repo.commit_unpushed("local only")
    spec_path = write_spec(tmp_path / "runs", "cpu-smoke", _spec())

    exit_code, _, err = _dry_run(capsys, spec_path, repo.path)

    assert exit_code == 1
    assert unpushed in err
    assert "remote-tracking branch" in err


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _stub_cloud_clis(stub_dir: Path, log: Path) -> None:
    stub_dir.mkdir()
    for name in ("gcloud", "terraform"):
        stub = stub_dir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 97\n', encoding="utf-8")
        stub.chmod(0o755)


def test_a_dry_run_spawns_no_gcloud_or_terraform_and_writes_nothing(tmp_path: Path) -> None:
    repo = make_fixture_repo(tmp_path / "work" / "git")
    spec_path = write_spec(tmp_path / "work" / "runs", "cpu-smoke", _spec())
    log = tmp_path / "cloud-cli-calls.log"
    _stub_cloud_clis(tmp_path / "stubs", log)
    before = _snapshot(tmp_path / "work")
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'stubs'}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": f"{REPO_ROOT / 'python'}{os.pathsep}{REPO_ROOT / 'benchmarks' / 'src'}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    completed = subprocess.run(
        [sys.executable, "-m", "pydocs_eval.bench_vm", "launch", str(spec_path), "--dry-run"],
        cwd=repo.path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "DRY RUN" in completed.stdout
    assert not log.exists(), log.read_text()
    assert _snapshot(tmp_path / "work") == before


def test_a_dry_run_shows_the_build_a_missing_tag_would_submit_without_checking_the_tag(
    tmp_path: Path, capsys: Capsys
) -> None:
    repo = make_fixture_repo(tmp_path / "git")
    spec_path = write_spec(
        tmp_path / "runs", "gpu-smoke", retrieval_spec(configs=[FIXTURE_CONFIG], gpu={})
    )

    exit_code, out, err = _dry_run(capsys, spec_path, repo.path)

    assert exit_code == 0, err
    commit = repo.head()
    build = out.split("image build:", 1)[1].split("\n\n", 1)[0]
    assert "only if the tag is missing: not checked (dry run)" in build
    assert f"git archive of {commit}" in build
    assert f"infra/bench-vm/cloudbuild.yaml at {commit}" in build
    assert f"_COMMIT={commit}" in build
    assert "_FLAVOUR=cuda" in build
    assert "<region: terraform output, not read in a dry run>" in build


def test_a_dry_run_says_a_relaunch_was_not_checked(tmp_path: Path, capsys: Capsys) -> None:
    repo = make_fixture_repo(tmp_path / "git")
    spec_path = write_spec(tmp_path / "runs", "cpu-smoke", _spec())

    exit_code, out, _ = _dry_run(capsys, spec_path, repo.path)

    assert exit_code == 0
    relaunch = out.split("first launch or relaunch:", 1)[1].split("\n\n", 1)[0]
    assert "not checked (dry run)" in relaunch
    assert "attempt 1" in relaunch
    assert "recorded commits" in relaunch
