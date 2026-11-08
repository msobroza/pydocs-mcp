#!/usr/bin/env python
"""Offline smoke: a REAL ``python -m pydocs_eval.runner`` run, SIGKILLed mid-leg, resumes.

WHY: the ``--run-id`` resume tests (issue #420) drive the sweep in-process
or stop a child at a ``--limit`` boundary; neither kills a live process at
an arbitrary point. This gate does (issue #416): it starts the real runner
CLI on a tiny RepoQA fixture under a run id, SIGKILLs it once the leg file
holds some but not all ``task`` records, re-runs the identical command and
proves from the leg file that only the unrecorded tasks ran — then checks the
resumed leg's final aggregate block against an uninterrupted control run,
bit for bit.

Offline by construction: BM25-only config (``benchmarks/configs/bm25.yaml``),
the bundled ``repoqa_mini.json`` fixture (copied to more tasks so the kill
lands mid-leg), ``HF_HUB_OFFLINE=1`` and every HTTP(S) proxy pointed at a
closed local port, so any network attempt fails loudly instead of
downloading; ``PYDOCS_*`` overlays are dropped from the children's
environment so only the checkout decides the config. Indexing still embeds
chunks, so the default FastEmbed model must already be in the local model
cache (``FASTEMBED_CACHE_PATH``, else ``<tempdir>/fastembed_cache``).

Latency aggregates (``*_seconds_p50`` …) are wall-clock and differ between
any two runs; they must exist under the same names, and every other
aggregate must match bit for bit.

Usage::

    PYTHONPATH=python:benchmarks/src python benchmarks/scripts/runner_resume_smoke.py \\
        [--copies 2] [--kill-after 3] [--timeout 180]

Exit 0 with an evidence summary on success, 1 with ``RUNNER RESUME SMOKE
FAILED`` lines otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydocs_eval.sweep import LATENCY_KEYS

# WHY: this script runs from its file path, not as an installed module, and the
# unit tests load it the same way; its pass/fail helpers live beside it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runner_resume_smoke_checks import (
    EVENT_RESUME,
    LegFile,
    SmokeFailure,
    check_kill,
    check_resume,
    compare_aggregate_blocks,
    count_fixture_tasks,
    count_task_records,
    derive_fixture,
    events_of,
    final_aggregate_block,
    is_latency_name,
    kill_landed_in_window,
    parse_leg_file,
    records_after_last_resume,
    task_ids,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MINI_FIXTURE = REPO_ROOT / "benchmarks" / "tests" / "fixtures" / "repoqa_mini.json"
BM25_CONFIG = REPO_ROOT / "benchmarks" / "configs" / "bm25.yaml"
SMOKE_RUN_ID = "resume-smoke"
CONTROL_RUN_ID = "resume-control"
DEFAULT_COPIES = 2
DEFAULT_KILL_AFTER = 3
DEFAULT_TIMEOUT_S = 180.0
POLL_INTERVAL_S = 0.01
# SIGKILL cannot be caught, so the child is reaped at once; this only bounds
# a pathological wait without doubling the stage's --timeout.
KILL_REAP_TIMEOUT_S = 10.0
# WHY port 9 (discard): nothing listens on it, so a proxied request is refused
# at once — a download attempt fails fast instead of reaching the network.
DEAD_PROXY = "http://127.0.0.1:9"
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
# The AppConfig env layer: an operator's PYDOCS_* overlay (an OpenAI embedder,
# say) would silently replace the BM25-only config under test.
_APP_CONFIG_ENV_PREFIX = "PYDOCS_"
FASTEMBED_CACHE_VAR = "FASTEMBED_CACHE_PATH"


# ── child process ───────────────────────────────────────────────────────


def build_runner_argv(
    python: str, *, config: Path, fixture: Path, results_dir: Path, run_id: str
) -> list[str]:
    """The runner command; the three invocations differ only in ``run_id``."""
    return [
        python, "-m", "pydocs_eval.runner",
        "--configs", str(config),
        "--dataset", "repoqa",
        "--fixture", str(fixture),
        "--bench-cache", "off",
        "--results-dir", str(results_dir),
        "--run-id", run_id,
    ]  # fmt: skip


def build_child_env(
    base: Mapping[str, str], *, repo_root: Path, workdir: Path, model_cache: Path
) -> dict[str, str]:
    """``base`` minus PYDOCS_* overlays, plus an offline guard, this checkout on
    PYTHONPATH, and every scratch path (index cache, TMPDIR) under ``workdir``.

    WHY TMPDIR: a SIGKILLed task never runs its own cleanup, so its corpus and
    tmp index would outlive the smoke in the system temp dir. Moving TMPDIR
    moves FastEmbed's default model cache too, hence the explicit
    ``model_cache`` (an operator's own FASTEMBED_CACHE_PATH wins).
    """
    env = {k: v for k, v in base.items() if not k.startswith(_APP_CONFIG_ENV_PREFIX)}
    paths = [str(repo_root / "python"), str(repo_root / "benchmarks" / "src")]
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [*paths, base.get("PYTHONPATH")]))
    env["HF_HUB_OFFLINE"] = "1"
    for var in _PROXY_VARS:
        env[var] = env[var.lower()] = DEAD_PROXY
    env["NO_PROXY"] = env["no_proxy"] = ""
    env["PYDOCS_CACHE_DIR"] = str(workdir / "index-cache")
    env["TMPDIR"] = str(workdir / "tmp")
    env.setdefault(FASTEMBED_CACHE_VAR, str(model_cache))
    return env


def default_model_cache() -> Path:
    """FastEmbed's default model cache as the parent process resolves it."""
    return Path(tempfile.gettempdir()) / "fastembed_cache"


@dataclass(frozen=True, slots=True)
class Workspace:
    """Every path one smoke run touches, under one temporary directory."""

    root: Path
    fixture: Path
    results: Path
    logs: Path
    env: dict[str, str]

    def argv(self, run_id: str) -> list[str]:
        return build_runner_argv(
            sys.executable, config=BM25_CONFIG, fixture=self.fixture,
            results_dir=self.results, run_id=run_id,
        )  # fmt: skip

    def leg_path(self, run_id: str) -> Path | None:
        found = sorted((self.results / run_id).glob("*.jsonl"))
        if len(found) > 1:
            raise SmokeFailure(f"run {run_id!r} has {len(found)} leg files, expected one: {found}")
        return found[0] if found else None

    def read_leg(self, run_id: str) -> LegFile:
        path = self.leg_path(run_id)
        if path is None:
            raise SmokeFailure(f"run {run_id!r} wrote no leg file under {self.results}")
        return parse_leg_file(path.read_text(encoding="utf-8"))


def make_workspace(root: Path, derived_fixture: Mapping[str, Any]) -> Workspace:
    fixture = root / "repoqa_resume_smoke.json"
    fixture.write_text(json.dumps(derived_fixture), encoding="utf-8")
    logs = root / "logs"
    logs.mkdir()
    (root / "tmp").mkdir()
    env = build_child_env(
        os.environ, repo_root=REPO_ROOT, workdir=root, model_cache=default_model_cache()
    )
    return Workspace(root=root, fixture=fixture, results=root / "results", logs=logs, env=env)


def load_derived_fixture(copies: int, kill_after: int) -> dict[str, Any]:
    """The smoke's fixture; refuses a ``kill_after`` that leaves no mid-leg window."""
    source = json.loads(MINI_FIXTURE.read_text(encoding="utf-8"))
    derived = derive_fixture(source, copies=copies)
    total = count_fixture_tasks(derived)
    if not kill_landed_in_window(kill_after, total=total):
        raise SmokeFailure(f"--kill-after {kill_after} must be 1..{total - 1} for {total} tasks")
    return derived


def run_to_completion(ws: Workspace, run_id: str, label: str, timeout: float) -> None:
    """Run the runner CLI to the end; raise unless it exits 0."""
    log = ws.logs / f"{label}.log"
    with log.open("w", encoding="utf-8") as out:
        # S603: argv is our own constants and temp paths, never user input.
        proc = subprocess.run(  # noqa: S603
            ws.argv(run_id), cwd=REPO_ROOT, env=ws.env, stdout=out,
            stderr=subprocess.STDOUT, timeout=timeout, check=False,
        )  # fmt: skip
    if proc.returncode != 0:
        tail = log.read_text(encoding="utf-8")[-2000:]
        raise SmokeFailure(f"{label} runner exited {proc.returncode}:\n{tail}")


def run_and_kill_mid_leg(ws: Workspace, run_id: str, kill_after: int, timeout: float) -> int:
    """Start the runner, SIGKILL it once ``kill_after`` task records are on disk.

    Polls the leg file — never a fixed sleep. Returns the exit code; raises if
    the runner exits on its own first or the deadline passes. The runner keeps
    working between the poll and the signal, so ``check_kill`` judges the file
    as the kill left it, not the threshold.
    """
    with (ws.logs / "killed.log").open("w", encoding="utf-8") as out:
        # S603: argv is our own constants and temp paths, never user input.
        proc = subprocess.Popen(  # noqa: S603
            ws.argv(run_id), cwd=REPO_ROOT, env=ws.env, stdout=out, stderr=subprocess.STDOUT
        )
        try:
            _poll_until_recorded(ws, run_id, proc, kill_after, timeout)
            proc.send_signal(signal.SIGKILL)
            return proc.wait(timeout=KILL_REAP_TIMEOUT_S)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def _poll_until_recorded(
    ws: Workspace, run_id: str, proc: subprocess.Popen[bytes], kill_after: int, timeout: float
) -> None:
    deadline = time.monotonic() + timeout
    while _recorded_now(ws, run_id) < kill_after:
        if proc.poll() is not None:
            raise SmokeFailure(
                f"runner exited ({proc.returncode}) before {kill_after} task records; "
                "nothing left to kill mid-leg — raise --copies"
            )
        if time.monotonic() > deadline:
            raise SmokeFailure(f"no {kill_after} task records within {timeout}s")
        time.sleep(POLL_INTERVAL_S)


def _recorded_now(ws: Workspace, run_id: str) -> int:
    path = ws.leg_path(run_id)
    return count_task_records(path.read_text(encoding="utf-8")) if path is not None else 0


# ── orchestration ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Evidence:
    """What the summary prints; every number comes from a leg file on disk."""

    total: int
    kill_returncode: int
    killed_ids: tuple[str, ...]
    truncated_lines: int
    resume_recorded: object
    second_ids: tuple[str, ...]
    final_task_records: int
    compared: int
    latency_skipped: int


def format_evidence(ev: Evidence, elapsed: float) -> list[str]:
    """The evidence summary, one line per claim."""
    return [
        f"[resume-smoke] control leg: {ev.total} tasks, uninterrupted (run id {CONTROL_RUN_ID!r})",
        f"[resume-smoke] kill: returncode {ev.kill_returncode} with {len(ev.killed_ids)}/{ev.total} "
        f"task records on disk ({ev.truncated_lines} truncated line(s))",
        f"[resume-smoke] recorded before kill: {list(ev.killed_ids)}",
        f"[resume-smoke] run_resume.recorded_tasks = {ev.resume_recorded!r}",
        f"[resume-smoke] second invocation ran {len(ev.second_ids)} task(s): {list(ev.second_ids)}",
        f"[resume-smoke] task records: {len(ev.killed_ids)} before -> {ev.final_task_records} after",
        f"[resume-smoke] final aggregates: {ev.compared} bit-identical to control "
        f"({ev.latency_skipped} wall-clock latency values name-checked only)",
        f"[resume-smoke] passed in {elapsed:.1f}s",
    ]


def run_smoke(args: argparse.Namespace, ws: Workspace) -> Evidence:
    """Control → killed run → resume; raises ``SmokeFailure`` listing every mismatch."""
    run_to_completion(ws, CONTROL_RUN_ID, "control", args.timeout)
    control = ws.read_leg(CONTROL_RUN_ID)
    control_ids = task_ids(control.records)
    returncode = run_and_kill_mid_leg(ws, SMOKE_RUN_ID, args.kill_after, args.timeout)
    killed = ws.read_leg(SMOKE_RUN_ID)
    _raise_if(check_kill(returncode, killed, total=len(control_ids)))
    run_to_completion(ws, SMOKE_RUN_ID, "resumed", args.timeout)
    resumed = ws.read_leg(SMOKE_RUN_ID)
    blocks = final_aggregate_block(resumed.records), final_aggregate_block(control.records)
    _raise_if(
        check_resume(killed, resumed, control_ids) + compare_aggregate_blocks(*blocks, LATENCY_KEYS)
    )
    return _evidence(control_ids, returncode, killed, resumed, blocks[1])


def _raise_if(failures: list[str]) -> None:
    if failures:
        raise SmokeFailure("\n".join(failures))


def _evidence(
    control_ids: list[str],
    returncode: int,
    killed: LegFile,
    resumed: LegFile,
    control_block: list[tuple[str, float]],
) -> Evidence:
    latency = sum(is_latency_name(name, LATENCY_KEYS) for name, _ in control_block)
    return Evidence(
        total=len(control_ids),
        kill_returncode=returncode,
        killed_ids=tuple(task_ids(killed.records)),
        truncated_lines=killed.corrupt_lines,
        resume_recorded=events_of(resumed.records, EVENT_RESUME)[0].get("recorded_tasks"),
        second_ids=tuple(task_ids(records_after_last_resume(list(resumed.records)))),
        final_task_records=len(task_ids(resumed.records)),
        compared=len(control_block) - latency,
        latency_skipped=latency,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse ``--copies``, ``--kill-after`` and ``--timeout`` (seconds, per runner invocation)."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--copies", type=int, default=DEFAULT_COPIES,
                        help="repeat the mini fixture's repos this many times (more tasks)")  # fmt: skip
    parser.add_argument("--kill-after", type=int, default=DEFAULT_KILL_AFTER,
                        help="SIGKILL once this many task records are on disk")  # fmt: skip
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: 0 with the evidence summary, 1 with every mismatch otherwise."""
    args = parse_args(argv)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="pydocs-resume-smoke-") as tmp:
        try:
            derived = load_derived_fixture(args.copies, args.kill_after)
            evidence = run_smoke(args, make_workspace(Path(tmp), derived))
        except (SmokeFailure, subprocess.TimeoutExpired, ValueError) as exc:
            for line in str(exc).splitlines() or [type(exc).__name__]:
                print(f"RUNNER RESUME SMOKE FAILED: {line}", file=sys.stderr)
            return 1
    for line in format_evidence(evidence, time.monotonic() - started):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
