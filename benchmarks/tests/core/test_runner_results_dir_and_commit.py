"""``--results-dir`` and the run header's commit fallback (issue #418).

Two default-preserving runner changes the bench VM builds on:

- ``--results-dir <dir>`` moves each leg's JSONL file into ``<dir>`` —
  same name pattern, same events. Without it the tracker's own field
  default applies, as before.
- The ``run_start`` header's ``tags["git_sha"]`` names the commit even with
  no git checkout: when ``git rev-parse`` fails, it falls back to the
  commit SHA in ``COMMIT_SHA_ENV_VAR`` (the bench image is built from a
  ``git archive`` and sets it). With git available git wins; with neither
  the tag stays ``""``.

The CLI cases drive ``python -m pydocs_eval.runner`` in a real child
process from a cwd outside any git checkout and read the files back; the
sweep cases drive ``run_sweep`` in-process. Git runs for real — against a
tiny fixture repository when a checkout is wanted, and with ``GIT_DIR``
pointed at a missing directory when it is not, so the outcome never
depends on where the test suite happens to live.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydocs_eval.runner import run_sweep
from pydocs_eval.sweep_support import COMMIT_SHA_ENV_VAR

_TESTS_DIR = Path(__file__).parents[1]
_FIXTURE = _TESTS_DIR / "fixtures" / "repoqa_mini.json"
_EVAL_SRC = _TESTS_DIR.parent / "src"
# WHY a 40-hex literal that no fixture repository can produce: a match proves
# the tag came from the environment variable, not from a git answer.
_IMAGE_SHA = "1234567890abcdef1234567890abcdef12345678"
# The JSONL tracker's file name: ``{system}_{config}_{dataset slug}_{utc_ts}.jsonl``
# (the RepoQA slug carries its pinned revision, e.g. ``repoqa_at_2024-06-23``).
_LEG_FILE_NAME = re.compile(r"^pydocs-mcp_baseline_repoqa[\w.-]*_\d{8}T\d{6}Z\.jsonl$")


def _empty_overlay(tmp_path: Path) -> Path:
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")
    return overlay


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _only_jsonl_file(directory: Path) -> Path:
    files = sorted(directory.glob("*.jsonl"))
    assert len(files) == 1, f"expected exactly one JSONL file in {directory}, got {files}"
    return files[0]


def _header_git_sha(path: Path) -> object:
    header = _records(path)[0]
    assert header["_event"] == "run_start"
    tags = header["tags"]
    assert isinstance(tags, dict)
    return tags["git_sha"]


def _no_git_child_env(tmp_path: Path, *, commit_sha: str | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != COMMIT_SHA_ENV_VAR}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(_EVAL_SRC), env.get("PYTHONPATH"))))
    env["GIT_DIR"] = str(tmp_path / "no-such-git-dir")
    if commit_sha is not None:
        env[COMMIT_SHA_ENV_VAR] = commit_sha
    return env


def _run_cli(
    tmp_path: Path, *extra_args: str, commit_sha: str | None = None
) -> subprocess.CompletedProcess[str]:
    cwd = tmp_path / "launch-cwd"
    cwd.mkdir(exist_ok=True)
    argv = [
        sys.executable,
        "-m",
        "pydocs_eval.runner",
        "--configs",
        str(_empty_overlay(tmp_path)),
        "--dataset",
        "repoqa",
        "--fixture",
        str(_FIXTURE),
        "--limit",
        "1",
        "--bench-cache",
        "off",
        *extra_args,
    ]
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=_no_git_child_env(tmp_path, commit_sha=commit_sha),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def _event_shape(path: Path) -> list[tuple[object, object]]:
    # Event kind + metric name, in order — the shape a moved file must keep.
    return [(r["_event"], r.get("name")) for r in _records(path)]


# ── --results-dir (CLI) ─────────────────────────────────────────────────


def test_cli_results_dir_lands_the_leg_file_in_the_given_directory(tmp_path: Path) -> None:
    results_dir = tmp_path / "state" / "results"

    _run_cli(tmp_path, "--results-dir", str(results_dir))

    moved = _only_jsonl_file(results_dir)
    assert _LEG_FILE_NAME.match(moved.name), moved.name
    # Nothing falls back to the default location under the launch cwd.
    assert not (tmp_path / "launch-cwd" / "benchmarks").exists()

    _run_cli(tmp_path)
    default = _only_jsonl_file(tmp_path / "launch-cwd" / "benchmarks" / "results" / "jsonl")
    assert _event_shape(moved) == _event_shape(default)


def test_cli_without_results_dir_writes_where_the_tracker_default_points(
    tmp_path: Path,
) -> None:
    _run_cli(tmp_path)

    default = _only_jsonl_file(tmp_path / "launch-cwd" / "benchmarks" / "results" / "jsonl")
    assert _LEG_FILE_NAME.match(default.name), default.name


def test_arg_parser_results_dir_defaults_to_none() -> None:
    from pydocs_eval.runner import _build_arg_parser

    args = _build_arg_parser().parse_args(["--configs", "x.yaml"])
    assert args.results_dir is None


# ── commit fallback ─────────────────────────────────────────────────────


def test_cli_without_git_tags_the_commit_from_the_environment(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"

    _run_cli(tmp_path, "--results-dir", str(results_dir), commit_sha=_IMAGE_SHA)

    assert _header_git_sha(_only_jsonl_file(results_dir)) == _IMAGE_SHA


def test_cli_without_git_or_variable_keeps_the_empty_tag(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"

    _run_cli(tmp_path, "--results-dir", str(results_dir))

    assert _header_git_sha(_only_jsonl_file(results_dir)) == ""


def _init_fixture_repo(root: Path) -> str:
    root.mkdir()
    git_env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_AUTHOR_NAME": "fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    for argv in (
        ["git", "init", "-q"],
        ["git", "commit", "-q", "--allow-empty", "-m", "fixture"],
    ):
        subprocess.run(argv, cwd=root, env=git_env, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, env=git_env, check=True, capture_output=True
    )
    return head.stdout.decode().strip()


async def _sweep_header_git_sha(tmp_path: Path) -> object:
    results_dir = tmp_path / "results"
    await run_sweep(
        systems=("pydocs-mcp",),
        config_paths=(_empty_overlay(tmp_path),),
        dataset_name="repoqa",
        dataset_kwargs={"fixture_path": _FIXTURE},
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": results_dir}},
        limit=1,
    )
    return _header_git_sha(_only_jsonl_file(results_dir))


@pytest.mark.skipif(shutil.which("git") is None, reason="needs a git binary")
async def test_sweep_with_git_available_git_wins_over_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_head = _init_fixture_repo(tmp_path / "repo")
    for name in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path / "repo")
    monkeypatch.setenv(COMMIT_SHA_ENV_VAR, _IMAGE_SHA)

    assert await _sweep_header_git_sha(tmp_path) == repo_head


async def test_sweep_without_git_falls_back_to_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "no-such-git-dir"))
    monkeypatch.setenv(COMMIT_SHA_ENV_VAR, _IMAGE_SHA)

    assert await _sweep_header_git_sha(tmp_path) == _IMAGE_SHA


async def test_sweep_without_git_or_variable_keeps_the_empty_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "no-such-git-dir"))
    monkeypatch.delenv(COMMIT_SHA_ENV_VAR, raising=False)

    assert await _sweep_header_git_sha(tmp_path) == ""
