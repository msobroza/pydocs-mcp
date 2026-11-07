"""``--run-id``: a leg recorded under another identity is refused before any task runs (issue #420)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydocs_eval.leg_identity import LegIdentityMismatch
from pydocs_eval.sweep_support import COMMIT_SHA_ENV_VAR

from ._resume_fakes import TASK_SCORE_METRIC, CountingFakeSystem
from ._resume_sweep import COMMIT_B, SHA256, interrupted, leg_file, read_records, sweep

# ── Identity ─────────────────────────────────────────────────────────────


def _change_config(overlay: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    overlay.write_text("log_level: warning\n")
    return {}


def _change_metric_specs(overlay: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    return {"metric_specs": (TASK_SCORE_METRIC, "mrr")}


def _change_dataset_revision(overlay: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    return {"dataset_kwargs": {"revision": "r2"}}


def _change_dataset_args(overlay: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    return {"dataset_kwargs": {"split": "dev"}}


def _change_commit(overlay: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setenv(COMMIT_SHA_ENV_VAR, COMMIT_B)
    return {}


@pytest.mark.parametrize(
    ("change", "component"),
    [
        (_change_config, "config_params"),
        (_change_metric_specs, "metric_specs"),
        (_change_dataset_revision, "dataset_revision"),
        (_change_dataset_args, "dataset_args"),
        (_change_commit, "commit"),
    ],
    ids=["config", "metric-specs", "dataset-revision", "dataset-args", "commit"],
)
async def test_identity_change_fails_before_any_task_runs(
    tmp_path: Path,
    overlay: Path,
    monkeypatch: pytest.MonkeyPatch,
    change,
    component: str,
) -> None:
    await interrupted(tmp_path, overlay, fail_at=2, run_id="pinned")
    path = leg_file(tmp_path, "pinned")
    before = path.read_bytes()
    recorded = read_records(path)[0]["identity"]["fingerprint"]
    overrides = change(overlay, monkeypatch)

    with pytest.raises(LegIdentityMismatch) as excinfo:
        await sweep(tmp_path, overlay, run_id="pinned", **overrides)

    message = str(excinfo.value)
    assert "'pinned'" in message
    fingerprints = SHA256.findall(message)
    assert len(set(fingerprints)) == 2 and recorded in fingerprints
    assert f"first differing component: {component!r}" in message
    assert CountingFakeSystem.searched == []
    assert path.read_bytes() == before
