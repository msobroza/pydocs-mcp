"""Rollout driver — the loop-side capture half of dual capture (ADR 0009 §4-5).

One rollout is one headless ``claude -p`` run against one instance. This module
threads a runner-chosen ``trajectory_id`` (UUID) three ways so every artifact of
the rollout shares one correlation key (ADR 0009 decision):

- ``--session-id <trajectory_id>`` on the CLI (stream stdout / result envelope /
  transcript filename all carry the session id),
- the ``.mcp.json`` server ``env`` map (``PYDOCS_TRACE__TRAJECTORY_ID`` +
  ``PYDOCS_TRACE__DIR``) so the product recorder writes to the same trajectory,
- the runner's own run record + run-config lockfile.

It reuses the ``agent_track`` seams verbatim — ``build_claude_command`` and
``render_mcp_config`` (the latter gained an additive ``env`` param) — and only
ADDS the ``--session-id`` flag + the trace env map; the base command builder is
untouched. The one expensive step (``ClaudeAgentRunner._spawn``) is the seam the
offline tests monkeypatch, so the whole persist/fold/lockfile path is exercised
with canned stdout and no real ``claude``.

Persistence order is load-bearing (R1): the raw ``stream.jsonl`` is written
BEFORE any parse/fold so every metric stays recomputable from the verbatim
capture. The post-run ``git diff`` patch is stored as a content-addressed blob
(the ADR 0010 ``blobs/<sha256>`` convention, reused from ``blob_store``) plus a
trailer record. The run-config lockfile hashes the canonical run config (the
``rubric_config_hash`` precedent) with sampling params recorded ``null`` +
``unrecorded_by_client`` because headless ``claude`` exposes no temperature /
top_p / seed knob (ADR 0009 R2, verified gap).
"""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import uuid
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydocs_eval.agent_track._command import build_claude_command, render_mcp_config
from pydocs_eval.agent_track._types import ArmConfig
from pydocs_eval.trajectory.blob_store import canonical_json, write_result_blob
from pydocs_eval.trajectory.merge import RunRecord

# ADR 0009 correlation channels — the flag + the two env var names threaded so
# the server recorder and the loop share one trajectory_id. Kept local to the
# driver (NOT in ``_command._CLI_FLAGS``) because they are trajectory-capture
# concerns, not part of the base two-arm command contract.
_SESSION_ID_FLAG = "--session-id"
_TRACE_ENV_TRAJECTORY_ID = "PYDOCS_TRACE__TRAJECTORY_ID"
_TRACE_ENV_DIR = "PYDOCS_TRACE__DIR"
# Route A candidate injection (ADR 0017 §Decision 5): the product's descriptions
# override env var. Re-declared here (not imported) so this core rollout module
# stays importable in a base install WITHOUT the [retrieval] extra — the same
# public-re-declaration + parity-test idiom candidate.py uses for the artifact
# hash. Pinned byte-for-byte against
# ``pydocs_mcp.application.description_override.DESCRIPTIONS_PATH_ENV_VAR`` by a
# parity test so a product rename fails loudly rather than silently mis-injecting.
_SERVE_DESCRIPTIONS_PATH_ENV = "PYDOCS_SERVE__DESCRIPTIONS_PATH"

# Per-trajectory + run-level artifact layout under the run's ``trace_dir``.
_STREAM_FILENAME = "stream.jsonl"
_RUN_RECORD_FILENAME = "run_record.json"
_TRAILER_FILENAME = "trailer.json"
_MCP_CONFIG_FILENAME = ".mcp.json"
_BLOBS_DIRNAME = "blobs"

# Headless ``claude`` exposes only ``--model`` / ``--max-turns`` (ADR 0009 R2,
# verified): these sampling knobs have no CLI surface, so they are stamped null
# and named in ``unrecorded_by_client`` rather than silently omitted.
_UNRECORDED_SAMPLING = ("temperature", "top_p", "seed")

# The provider the headless arm runs against — Anthropic's Claude Code CLI.
_DEFAULT_PROVIDER = "anthropic"


class RolloutError(Exception):
    """Root of every rollout-driver failure."""


class RolloutTimeoutError(RolloutError):
    """The rollout spawn exceeded the runner's wall budget — no capturable
    trajectory (raised with the budget + command for context)."""


class SpawnSeam(Protocol):
    """The subset of ``ClaudeAgentRunner`` the driver depends on.

    ``_spawn`` is the process-creation seam the offline tests monkeypatch with
    canned stdout; ``task_timeout_seconds`` bounds it. Depending on the Protocol
    (not the concrete adapter) keeps the driver testable with a scripted double.
    """

    task_timeout_seconds: float

    def _spawn(self, cmd: list[str], *, cwd: Path) -> Awaitable[str]: ...


@dataclass(frozen=True, slots=True)
class RolloutRequest:
    """Everything one rollout needs beyond the runner. ``trajectory_id`` is the
    runner-chosen UUID (correlation authority); ``model_name_or_path`` defaults to
    the arm name and names the prediction's runner id (SWE-bench convention)."""

    arm: ArmConfig
    prompt: str
    workspace: Path
    corpus_dir: Path
    python: Path
    trace_dir: Path
    trajectory_id: str
    instance_id: str
    claude_cli_version: str
    provider: str = _DEFAULT_PROVIDER
    dataset_revision: str | None = None
    versions: Mapping[str, str] = field(default_factory=dict)
    model_name_or_path: str | None = None
    # Route A candidate injection (ADR 0017): the rendered candidate description
    # document this rollout serves. ``None`` = the packaged product surface.
    descriptions_path: Path | None = None


@dataclass(frozen=True, slots=True)
class RolloutTrailer:
    """Trajectory-level trailer: the runner-captured patch as a blob ref plus the
    session id (ADR 0010 trailer record). ``patch_bytes`` makes an empty patch
    (no edits) detectable without dereferencing the blob."""

    trajectory_id: str
    patch_blob: str
    patch_bytes: int
    session_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "patch_blob": self.patch_blob,
            "patch_bytes": self.patch_bytes,
            "session_id": self.session_id,
        }


@dataclass(frozen=True, slots=True)
class RolloutResult:
    """What one rollout produced: the raw capture paths + the derived artifacts."""

    trajectory_id: str
    stream_path: Path
    run_record_path: Path
    trailer_path: Path
    patch_blob: str
    run_config_hash: str
    prediction: dict[str, Any]
    stdout: str


def _validate_trajectory_id(trajectory_id: str) -> str:
    """Return ``trajectory_id`` if it is a valid UUID string, else raise.

    ``claude --session-id`` requires a valid UUID (verified in v2.1.76 help), so
    a malformed id is rejected at the boundary rather than failing deep in spawn.
    """
    try:
        uuid.UUID(trajectory_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"trajectory_id must be a UUID string, got {trajectory_id!r}") from exc
    return trajectory_id


def trace_env_map(
    *, trajectory_id: str, trace_dir: Path, descriptions_path: Path | None = None
) -> dict[str, str]:
    """Build the ``.mcp.json`` ``env`` block that correlates the server recorder.

    ``descriptions_path`` (optional, ADR 0017 Route A) additively injects the
    product's ``PYDOCS_SERVE__DESCRIPTIONS_PATH`` override so the served
    ``pydocs_mcp`` binds this rollout's candidate description surface through
    ``apply_source`` — reaching the product before tool registration and making
    the trace header's ``artifact_hash`` self-identify the candidate. Omitting it
    is byte-identical to the pre-Route-A env, so non-candidate rollouts are
    untouched.

    Example:
        >>> trace_env_map(trajectory_id="t", trace_dir=Path("/d"))
        {'PYDOCS_TRACE__TRAJECTORY_ID': 't', 'PYDOCS_TRACE__DIR': '/d'}
    """
    env = {_TRACE_ENV_TRAJECTORY_ID: trajectory_id, _TRACE_ENV_DIR: str(trace_dir)}
    if descriptions_path is not None:
        env[_SERVE_DESCRIPTIONS_PATH_ENV] = str(descriptions_path)
    return env


def build_rollout_command(
    arm: ArmConfig,
    *,
    prompt: str,
    cwd: Path,
    mcp_config: Path | None,
    trajectory_id: str,
) -> list[str]:
    """Reuse ``build_claude_command`` and append ``--session-id <trajectory_id>``.

    The base command builder is untouched (frozen two-arm contract); the driver
    only adds the correlation flag so the session id ties stdout / result / the
    on-disk transcript to this trajectory (ADR 0009).
    """
    _validate_trajectory_id(trajectory_id)
    cmd = build_claude_command(arm, prompt=prompt, cwd=cwd, mcp_config=mcp_config)
    return [*cmd, _SESSION_ID_FLAG, trajectory_id]


def _trajectory_dir(request: RolloutRequest) -> Path:
    traj_dir = request.trace_dir / request.trajectory_id
    traj_dir.mkdir(parents=True, exist_ok=True)
    return traj_dir


def write_trace_mcp_config(request: RolloutRequest) -> Path | None:
    """Render the trace-aware ``.mcp.json`` for the indexed arm, else ``None``.

    Only the indexed arm attaches an MCP server; the bare / tool-less arms get no
    config (matching ``build_claude_command``). The env map carries the two
    ``PYDOCS_TRACE__*`` correlation vars so the server recorder writes into this
    trajectory's trace file.
    """
    if not request.arm.mcp:
        return None
    rendered = render_mcp_config(
        corpus_dir=request.corpus_dir,
        python=request.python,
        env=trace_env_map(
            trajectory_id=request.trajectory_id,
            trace_dir=request.trace_dir,
            descriptions_path=request.descriptions_path,
        ),
    )
    path = _trajectory_dir(request) / _MCP_CONFIG_FILENAME
    path.write_text(rendered, encoding="utf-8")
    return path


def persist_raw_stream(*, trace_dir: Path, trajectory_id: str, stdout: str) -> Path:
    """Write the verbatim stream-json stdout BEFORE any fold (R1).

    This is the raw substrate every downstream metric recomputes from, so it is
    persisted exactly as ``claude`` emitted it — no parsing, no dedupe, no fold.
    """
    traj_dir = trace_dir / trajectory_id
    traj_dir.mkdir(parents=True, exist_ok=True)
    path = traj_dir / _STREAM_FILENAME
    path.write_text(stdout, encoding="utf-8")
    return path


def capture_git_diff(workspace: Path) -> str:
    """Return the post-rollout unified diff of ``workspace`` (edits + new files).

    ``git add -N .`` records intent-to-add for untracked files so ``git diff``
    includes newly-created files' content (SWE-bench gold patches routinely add
    files — 21.1% of instances). A non-git workspace is out of scope (ADR 0009);
    a git failure surfaces as ``CalledProcessError`` carrying the workspace.
    """
    subprocess.run(["git", "add", "-N", "."], cwd=str(workspace), check=True, capture_output=True)
    completed = subprocess.run(
        ["git", "diff"], cwd=str(workspace), check=True, capture_output=True, text=True
    )
    return completed.stdout


def store_patch(*, trace_dir: Path, patch: str) -> str:
    """Store the patch in the run-level ``blobs/`` store; return its sha256 hex.

    Reuses the ADR 0010 content-addressed convention (write-once by hash) so a
    re-run with an identical patch is idempotent and blobs dedupe across the run.
    """
    return write_result_blob(trace_dir / _BLOBS_DIRNAME, patch.encode("utf-8"))


def build_run_config(request: RolloutRequest) -> dict[str, Any]:
    """Assemble the run-config lockfile block (ADR 0009 R2).

    Sampling params are stamped ``null`` and named in ``unrecorded_by_client``
    (headless ``claude`` exposes no such knob — the verified gap is recorded, not
    papered over). Model / provider / caps / arm config / versions / instance
    revision pin the run identity.
    """
    sampling: dict[str, Any] = {name: None for name in _UNRECORDED_SAMPLING}
    sampling["unrecorded_by_client"] = list(_UNRECORDED_SAMPLING)
    return {
        "model": request.arm.model,
        "provider": request.provider,
        "max_turns": request.arm.max_turns,
        "arm": {"name": request.arm.name, "mcp": request.arm.mcp, "no_tools": request.arm.no_tools},
        "sampling": sampling,
        "versions": dict(request.versions),
        "instance_id": request.instance_id,
    }


def run_config_hash(run_config: Mapping[str, Any]) -> str:
    """sha256 of the canonical-JSON run config (the ``rubric_config_hash`` idiom).

    Canonical JSON (sorted keys, no spaces) makes the hash order-independent so
    two logically-identical configs pin to the same lockfile hash (R6).

    Example:
        >>> len(run_config_hash({"b": 1, "a": 2}))
        64
    """
    return hashlib.sha256(canonical_json(run_config).encode("utf-8")).hexdigest()


def write_run_record(*, trace_dir: Path, run_record: RunRecord, run_config_digest: str) -> Path:
    """Write the run record + its lockfile hash to ``<traj>/run_record.json``.

    The record is what ``merge_trajectory`` folds into the trajectory header;
    ``run_config_hash`` is stamped alongside so a re-pinned run cannot silently
    resume samples measured under a different config.
    """
    traj_dir = trace_dir / run_record.trajectory_id
    traj_dir.mkdir(parents=True, exist_ok=True)
    payload = run_record.to_dict()
    payload["run_config_hash"] = run_config_digest
    path = traj_dir / _RUN_RECORD_FILENAME
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    return path


def write_trailer(*, trace_dir: Path, trailer: RolloutTrailer) -> Path:
    """Write the trajectory trailer (patch blob ref + session id) to disk."""
    traj_dir = trace_dir / trailer.trajectory_id
    traj_dir.mkdir(parents=True, exist_ok=True)
    path = traj_dir / _TRAILER_FILENAME
    path.write_text(canonical_json(trailer.to_dict()) + "\n", encoding="utf-8")
    return path


def mainline_prediction(
    *, instance_id: str, model_name_or_path: str, model_patch: str
) -> dict[str, str]:
    """One SWE-bench mainline prediction dict (§4.1 canonical keys).

    Example:
        >>> mainline_prediction(instance_id="i", model_name_or_path="m", model_patch="d")
        {'instance_id': 'i', 'model_name_or_path': 'm', 'model_patch': 'd'}
    """
    return {
        "instance_id": instance_id,
        "model_name_or_path": model_name_or_path,
        "model_patch": model_patch,
    }


def render_predictions_jsonl(predictions: Sequence[Mapping[str, Any]]) -> str:
    """Render predictions as mainline swebench JSONL — one canonical object/line.

    JSONL of ``{instance_id, model_name_or_path, model_patch}`` is the most
    interoperable of the accepted encodings (§4.1); the Live harness dict form is
    derived by ``live_predictions_dict``.
    """
    return "".join(canonical_json(dict(p)) + "\n" for p in predictions)


def live_predictions_dict(
    predictions: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Re-key predictions into the SWE-bench-Live dict form (§4.2).

    ``{instance_id: prediction}`` is what the Live harness ``json.load``s; a
    duplicate ``instance_id`` would silently drop a prediction, so it raises.
    """
    out: dict[str, dict[str, Any]] = {}
    for pred in predictions:
        instance_id = str(pred["instance_id"])
        if instance_id in out:
            raise ValueError(
                f"duplicate instance_id {instance_id!r} in predictions; "
                "the Live dict form requires unique instance ids"
            )
        out[instance_id] = dict(pred)
    return out


async def _spawn_with_timeout(runner: SpawnSeam, cmd: list[str], *, cwd: Path) -> str:
    """Await the runner's spawn seam under its wall budget, or raise on timeout."""
    try:
        return await asyncio.wait_for(
            runner._spawn(cmd, cwd=cwd), timeout=runner.task_timeout_seconds
        )
    except TimeoutError as exc:
        raise RolloutTimeoutError(
            f"rollout spawn exceeded {runner.task_timeout_seconds}s wall budget "
            f"for trajectory command {cmd!r}"
        ) from exc


async def run_rollout(runner: SpawnSeam, request: RolloutRequest) -> RolloutResult:
    """Drive one rollout end-to-end: spawn, persist raw (R1), capture, lockfile.

    Order is load-bearing: the raw ``stream.jsonl`` lands BEFORE any fold, then
    the post-run ``git diff`` becomes a blob + trailer, then the run-config
    lockfile pins the run. Returns the derived prediction + artifact paths; the
    caller aggregates predictions across rollouts.
    """
    _validate_trajectory_id(request.trajectory_id)
    mcp_config = write_trace_mcp_config(request)
    cmd = build_rollout_command(
        request.arm,
        prompt=request.prompt,
        cwd=request.workspace,
        mcp_config=mcp_config,
        trajectory_id=request.trajectory_id,
    )
    stdout = await _spawn_with_timeout(runner, cmd, cwd=request.workspace)
    stream_path = persist_raw_stream(
        trace_dir=request.trace_dir, trajectory_id=request.trajectory_id, stdout=stdout
    )
    return _capture_artifacts(request, stream_path=stream_path, stdout=stdout)


def _capture_artifacts(request: RolloutRequest, *, stream_path: Path, stdout: str) -> RolloutResult:
    """Post-spawn capture: patch blob, run-config lockfile, trailer, prediction."""
    patch = capture_git_diff(request.workspace)
    patch_blob = store_patch(trace_dir=request.trace_dir, patch=patch)
    run_config = build_run_config(request)
    digest = run_config_hash(run_config)
    run_record = RunRecord(
        trajectory_id=request.trajectory_id,
        claude_cli_version=request.claude_cli_version,
        dataset_revision=request.dataset_revision,
        run_config=run_config,
    )
    run_record_path = write_run_record(
        trace_dir=request.trace_dir, run_record=run_record, run_config_digest=digest
    )
    trailer = RolloutTrailer(
        trajectory_id=request.trajectory_id,
        patch_blob=patch_blob,
        patch_bytes=len(patch.encode("utf-8")),
        session_id=request.trajectory_id,
    )
    trailer_path = write_trailer(trace_dir=request.trace_dir, trailer=trailer)
    prediction = mainline_prediction(
        instance_id=request.instance_id,
        model_name_or_path=request.model_name_or_path or request.arm.name,
        model_patch=patch,
    )
    return RolloutResult(
        trajectory_id=request.trajectory_id,
        stream_path=stream_path,
        run_record_path=run_record_path,
        trailer_path=trailer_path,
        patch_blob=patch_blob,
        run_config_hash=digest,
        prediction=prediction,
        stdout=stdout,
    )
