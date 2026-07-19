"""Rollout driver — offline via the ``_spawn`` monkeypatch seam (ADR 0009 §4-5).

Every test here runs WITHOUT a real ``claude``: the driver's spawn seam is
replaced with a canned-stdout double, so the persist / patch / lockfile /
prediction path is exercised deterministically. Covers: the env-map + session-id
correlation channels, raw-stream-before-fold (R1), lockfile determinism, patch
blob capture, and both golden-pinned prediction formats.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.agent_track._command import render_mcp_config
from pydocs_eval.agent_track._types import ArmConfig
from pydocs_eval.trajectory.rollout import (
    RolloutRequest,
    RolloutTimeoutError,
    build_rollout_command,
    build_run_config,
    capture_git_diff,
    live_predictions_dict,
    mainline_prediction,
    render_predictions_jsonl,
    run_config_hash,
    run_rollout,
    trace_env_map,
    write_trace_mcp_config,
)

_TRAJ_ID = "12345678-1234-5678-1234-567812345678"
_CANNED_STDOUT = '{"type":"assistant"}\n{"type":"result","total_cost_usd":0.1}\n'


@dataclass
class _RecordingRunner:
    """Scripted ``_spawn`` seam: records the argv + cwd, returns canned stdout."""

    task_timeout_seconds: float = 900.0
    canned: str = _CANNED_STDOUT
    calls: list[tuple[list[str], Path]] = field(default_factory=list)

    async def _spawn(self, cmd: list[str], *, cwd: Path) -> str:
        self.calls.append((cmd, cwd))
        return self.canned


def _git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(root), check=True)
    (root / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(root), check=True)
    return root


def _request(tmp_path: Path, *, arm: ArmConfig, workspace: Path | None = None) -> RolloutRequest:
    return RolloutRequest(
        arm=arm,
        prompt="q?",
        workspace=workspace or tmp_path / "ws",
        corpus_dir=tmp_path / "corpus",
        python=Path("/venv/bin/python"),
        trace_dir=tmp_path / "trace",
        trajectory_id=_TRAJ_ID,
        instance_id="repo__pkg-1",
        claude_cli_version="2.1.205",
        dataset_revision="rev-abc",
        versions={"pydocs_mcp": "0.6.0", "harness": "0.2.0"},
    )


# --- correlation channels: env map + session-id -----------------------------


def test_render_mcp_config_env_none_is_byte_neutral(tmp_path) -> None:
    corpus, python = tmp_path / "c", Path("/venv/bin/python")
    assert render_mcp_config(corpus_dir=corpus, python=python) == render_mcp_config(
        corpus_dir=corpus, python=python, env=None
    )


def test_render_mcp_config_env_adds_block(tmp_path) -> None:
    rendered = render_mcp_config(
        corpus_dir=tmp_path / "c", python=Path("/p"), env={"PYDOCS_TRACE__DIR": "/d"}
    )
    server = json.loads(rendered)["mcpServers"]["pydocs-mcp"]
    assert server["env"] == {"PYDOCS_TRACE__DIR": "/d"}


def test_trace_env_map_keys() -> None:
    assert trace_env_map(trajectory_id=_TRAJ_ID, trace_dir=Path("/d")) == {
        "PYDOCS_TRACE__TRAJECTORY_ID": _TRAJ_ID,
        "PYDOCS_TRACE__DIR": "/d",
    }


def test_build_rollout_command_appends_session_id(tmp_path) -> None:
    cmd = build_rollout_command(
        ArmConfig(name="bare"), prompt="q?", cwd=tmp_path, mcp_config=None, trajectory_id=_TRAJ_ID
    )
    assert cmd[-2:] == ["--session-id", _TRAJ_ID]


def test_build_rollout_command_rejects_non_uuid(tmp_path) -> None:
    with pytest.raises(ValueError, match="must be a UUID"):
        build_rollout_command(
            ArmConfig(name="bare"), prompt="q?", cwd=tmp_path, mcp_config=None, trajectory_id="nope"
        )


def test_write_trace_mcp_config_indexed_carries_env(tmp_path) -> None:
    path = write_trace_mcp_config(_request(tmp_path, arm=ArmConfig(name="indexed", mcp=True)))
    assert path is not None
    server = json.loads(path.read_text())["mcpServers"]["pydocs-mcp"]
    assert server["env"]["PYDOCS_TRACE__TRAJECTORY_ID"] == _TRAJ_ID
    assert server["env"]["PYDOCS_TRACE__DIR"] == str(tmp_path / "trace")


def test_write_trace_mcp_config_bare_returns_none(tmp_path) -> None:
    assert write_trace_mcp_config(_request(tmp_path, arm=ArmConfig(name="bare"))) is None


# --- lockfile determinism ----------------------------------------------------


def test_run_config_records_unrecorded_sampling(tmp_path) -> None:
    cfg = build_run_config(_request(tmp_path, arm=ArmConfig(name="bare")))
    assert cfg["sampling"] == {
        "temperature": None,
        "top_p": None,
        "seed": None,
        "unrecorded_by_client": ["temperature", "top_p", "seed"],
    }
    assert cfg["model"] == ArmConfig(name="bare").model
    assert cfg["instance_id"] == "repo__pkg-1"


def test_run_config_hash_is_key_order_independent() -> None:
    assert run_config_hash({"a": 1, "b": 2}) == run_config_hash({"b": 2, "a": 1})


def test_run_config_hash_deterministic(tmp_path) -> None:
    cfg = build_run_config(_request(tmp_path, arm=ArmConfig(name="bare")))
    assert run_config_hash(cfg) == run_config_hash(cfg)
    assert len(run_config_hash(cfg)) == 64


# --- git diff patch capture --------------------------------------------------


def test_capture_git_diff_includes_edit_and_new_file(tmp_path) -> None:
    repo = _git_repo(tmp_path / "repo")
    (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "added.py").write_text("y = 3\n", encoding="utf-8")
    diff = capture_git_diff(repo)
    assert "tracked.py" in diff and "-x = 1" in diff and "+x = 2" in diff
    assert "added.py" in diff and "+y = 3" in diff


# --- prediction formats (golden-pinned) -------------------------------------


def test_mainline_prediction_shape() -> None:
    assert mainline_prediction(instance_id="i", model_name_or_path="m", model_patch="d") == {
        "instance_id": "i",
        "model_name_or_path": "m",
        "model_patch": "d",
    }


def test_render_predictions_jsonl_golden() -> None:
    preds = [
        mainline_prediction(instance_id="a", model_name_or_path="m", model_patch="pa"),
        mainline_prediction(instance_id="b", model_name_or_path="m", model_patch="pb"),
    ]
    assert render_predictions_jsonl(preds) == (
        '{"instance_id":"a","model_name_or_path":"m","model_patch":"pa"}\n'
        '{"instance_id":"b","model_name_or_path":"m","model_patch":"pb"}\n'
    )


def test_live_predictions_dict_golden() -> None:
    preds = [mainline_prediction(instance_id="a", model_name_or_path="m", model_patch="pa")]
    assert live_predictions_dict(preds) == {
        "a": {"instance_id": "a", "model_name_or_path": "m", "model_patch": "pa"}
    }


def test_live_predictions_dict_rejects_duplicate_instance_id() -> None:
    dup = mainline_prediction(instance_id="a", model_name_or_path="m", model_patch="p")
    with pytest.raises(ValueError, match="duplicate instance_id"):
        live_predictions_dict([dup, dup])


# --- end-to-end offline driver ----------------------------------------------


async def test_run_rollout_persists_raw_before_fold_and_captures(tmp_path) -> None:
    repo = _git_repo(tmp_path / "ws")
    (repo / "added.py").write_text("z = 9\n", encoding="utf-8")
    request = _request(tmp_path, arm=ArmConfig(name="indexed", mcp=True), workspace=repo)
    runner = _RecordingRunner()

    result = await run_rollout(runner, request)

    # session-id threaded into the built command
    cmd, _cwd = runner.calls[0]
    assert cmd[-2:] == ["--session-id", _TRAJ_ID]
    # raw stream persisted verbatim (R1: byte-identical to canned stdout)
    assert result.stream_path.read_text() == _CANNED_STDOUT
    # patch blob captured + dereferenceable
    blob = tmp_path / "trace" / "blobs" / result.patch_blob
    assert blob.exists() and "added.py" in blob.read_text()
    # run record carries the lockfile hash + cli version
    record = json.loads(result.run_record_path.read_text())
    assert record["run_config_hash"] == result.run_config_hash
    assert record["claude_cli_version"] == "2.1.205"
    # trailer references the same patch blob + session id
    trailer = json.loads(result.trailer_path.read_text())
    assert trailer["patch_blob"] == result.patch_blob
    assert trailer["session_id"] == _TRAJ_ID
    # prediction defaults model_name_or_path to the arm name
    assert result.prediction["model_name_or_path"] == "indexed"
    assert "added.py" in result.prediction["model_patch"]


async def test_run_rollout_bare_arm_writes_no_mcp_config(tmp_path) -> None:
    repo = _git_repo(tmp_path / "ws")
    request = _request(tmp_path, arm=ArmConfig(name="bare"), workspace=repo)
    result = await run_rollout(_RecordingRunner(), request)
    assert not (tmp_path / "trace" / _TRAJ_ID / ".mcp.json").exists()
    assert result.prediction["model_name_or_path"] == "bare"


async def test_run_rollout_timeout_raises(tmp_path) -> None:
    repo = _git_repo(tmp_path / "ws")

    @dataclass
    class _HangingRunner:
        task_timeout_seconds: float = 0.01

        async def _spawn(self, cmd: list[str], *, cwd: Path) -> str:
            await asyncio.sleep(1.0)
            return ""

    request = _request(tmp_path, arm=ArmConfig(name="bare"), workspace=repo)
    with pytest.raises(RolloutTimeoutError, match="wall budget"):
        await run_rollout(_HangingRunner(), request)
