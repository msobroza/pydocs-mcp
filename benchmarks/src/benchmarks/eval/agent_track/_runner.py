"""Subprocess adapter + corpus prep for the agent track (spec §D15).

The one expensive path in the harness: ``ClaudeAgentRunner`` spawns a headless
``claude -p`` per arm and folds its stdout into a ``RunMetrics``; ``CorpusPrep``
indexes each materialized corpus once with ``pydocs_mcp index`` before the
indexed arm can query it. Everything spawn-shaped lives behind two seams so the
rest of the harness stays hermetically testable:

- ``_spawn`` (process creation) is an instance method the orchestration tests
  monkeypatch with canned stdout — ``run``'s parse-merge logic is exercised
  without ever launching ``claude``.
- ``_run_index`` (module-level) is the single call site the corpus-prep test
  patches to a recorder, so the marker-file skip is asserted offline.

``AgentRunner`` is the Protocol both the real adapter and the scripted
``FakeAgentRunner`` satisfy; downstream (the pair orchestrator) depends only on
the Protocol, never on ``ClaudeAgentRunner`` directly.

Timeout discipline: the wall timeout is enforced in ``run`` (``asyncio.wait_for``
around the ``_spawn`` await) so it bounds ANY ``_spawn`` implementation —
including a fake that hangs. On timeout ``run`` returns ``None`` (a half-pair the
orchestrator discards, never a raise); the real ``_spawn`` kills the child's
process group on cancellation so no orphaned ``claude`` survives.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from benchmarks.eval.agent_track._command import build_claude_command
from benchmarks.eval.agent_track._parse import parse_result_json, parse_stream_events
from benchmarks.eval.agent_track._types import ArmConfig, RunMetrics

# The marker touched inside an indexed corpus dir so a rerun skips reindexing.
# WHY a marker (not an in-memory set): corpus prep must survive process restarts
# — a resumed run re-materializes the same dir and must skip the ~minutes-long
# index. The actual index cache lives in ``~/.pydocs-mcp`` keyed by dirname +
# path-hash; the marker only records "we already ran index over this dir".
_INDEXED_MARKER = ".pydocs-indexed"

# The stream event kind carrying the final answer + run-level cost/turns. Under
# ``--output-format stream-json`` the CLI emits this as the LAST line, so the
# adapter parses it as the result JSON without a separate ``--output-format json``
# invocation. Single source of truth so a CLI rename is a one-line fix here.
_RESULT_EVENT_TYPE = "result"


@runtime_checkable
class AgentRunner(Protocol):
    """One arm's run on one task → its ``RunMetrics``, or ``None`` on timeout.

    ``None`` is the half-pair signal: the arm did not finish inside the wall
    budget, so the orchestrator discards the whole task (no half-pairs admitted).
    The real adapter and the scripted ``FakeAgentRunner`` both satisfy this.
    """

    async def run(
        self,
        arm: ArmConfig,
        *,
        prompt: str,
        cwd: Path,
        mcp_config: Path | None,
    ) -> RunMetrics | None: ...


@dataclass
class ClaudeAgentRunner:
    """Spawn a headless ``claude -p`` for one arm and merge its stdout.

    ``task_timeout_seconds`` bounds each arm's wall clock; on overrun ``run``
    returns ``None`` and the child's process group is killed. The parse-merge is
    pure (``parse_stream_events`` for efficiency stats + the final ``result``
    line via ``parse_result_json`` for cost / turns / answer), so the only
    non-deterministic part is the spawn itself — isolated in ``_spawn``.

    WHY not ``frozen=True, slots=True`` like the value objects: this is an I/O
    adapter, not a value object, and the orchestration tests replace ``_spawn``
    with a canned-stdout double via ``monkeypatch.setattr(runner, "_spawn", ...)``
    — which a slotted frozen instance forbids. The frozen-slotted convention is
    scoped to value objects / steps, not adapters seamed for test substitution.
    """

    task_timeout_seconds: float

    async def run(
        self,
        arm: ArmConfig,
        *,
        prompt: str,
        cwd: Path,
        mcp_config: Path | None,
    ) -> RunMetrics | None:
        """Run one arm; return its metrics, or ``None`` if it times out.

        The wall clock is measured around the ``_spawn`` await so it reflects the
        real end-to-end latency the report aggregates. A timeout is a controlled
        half-pair (``None``), never an exception — the orchestrator drops the
        task and moves on.
        """
        cmd = build_claude_command(arm, prompt=prompt, cwd=cwd, mcp_config=mcp_config)
        started = time.monotonic()
        try:
            stdout = await asyncio.wait_for(
                self._spawn(cmd, cwd=cwd),
                timeout=self.task_timeout_seconds,
            )
        except TimeoutError:
            return None
        wall_seconds = time.monotonic() - started
        return _merge_metrics(stdout, wall_seconds=wall_seconds)

    async def _spawn(self, cmd: list[str], *, cwd: Path) -> str:
        """Launch ``cmd`` in ``cwd`` and return its decoded stdout.

        ``start_new_session=True`` puts the child in its own process group so a
        wall-timeout cancellation can ``killpg`` the whole tree (``claude`` spawns
        MCP-server and tool subprocesses). On cancellation the group is killed and
        the exception re-raised so ``run``'s ``wait_for`` sees the timeout.
        """
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            out, _err = await proc.communicate()
        except asyncio.CancelledError:
            _kill_process_group(proc)
            raise
        return out.decode("utf-8", errors="replace")


def _merge_metrics(stdout: str, *, wall_seconds: float) -> RunMetrics:
    """Fold one run's stdout into a ``RunMetrics``.

    ``parse_stream_events`` supplies the efficiency stats; the final ``result``
    stream line — parsed via ``parse_result_json`` — supplies cost / turns /
    answer. Both parsers are total, so a truncated or answerless run degrades to
    zeros / empty text rather than raising.
    """
    stats = parse_stream_events(stdout)
    result = parse_result_json(_result_line(stdout))
    return RunMetrics(
        cost_usd=result.cost_usd,
        wall_seconds=wall_seconds,
        turns=result.turns,
        tool_calls=stats.tool_calls,
        distinct_files_read=stats.distinct_files_read,
        cache_read_tokens=stats.cache_read_tokens,
        cache_write_tokens=stats.cache_write_tokens,
        answer=result.answer,
    )


def _result_line(stdout: str) -> str:
    """Return the last ``{"type": "result", ...}`` stream line, else ``"{}"``.

    Scans from the end because the result event is emitted last. A missing result
    line (truncated run) yields ``"{}"`` so ``parse_result_json`` degrades to a
    zero-cost, empty-answer ``ParsedResult`` rather than raising.
    """
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if f'"type":"{_RESULT_EVENT_TYPE}"' in stripped.replace(" ", ""):
            return stripped
    return "{}"


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Best-effort SIGKILL of the child's process group after a timeout.

    Uses ``killpg`` (the child ran under ``start_new_session=True``) so the whole
    ``claude`` tree dies, not just the top process. Any error here is swallowed:
    the child may already be dead, and a timeout must always resolve to a clean
    ``None``, never a secondary raise.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return


def _run_index(corpus_dir: Path) -> None:
    """Index ``corpus_dir`` with ``<python> -m pydocs_mcp index <corpus_dir>``.

    Sync + blocking by design — ``CorpusPrep.ensure_indexed`` calls it through
    ``asyncio.to_thread`` so the event loop is not blocked. Runs with the SAME
    interpreter as the harness (``sys.executable``) so the arm indexes against the
    same installed ``pydocs_mcp``. ``check=True`` surfaces a failed index as a
    ``CalledProcessError`` carrying the corpus dir — a broken corpus fails loud
    before any paid arm runs against a stale / empty index.
    """
    subprocess.run(
        [sys.executable, "-m", "pydocs_mcp", "index", str(corpus_dir)],
        check=True,
        capture_output=True,
    )


@dataclass(frozen=True, slots=True)
class CorpusPrep:
    """Index each materialized corpus once, guarded by an in-dir marker file.

    ``cache_dir`` is retained for symmetry with the rest of the harness config
    (and future per-corpus bookkeeping); the authoritative skip signal is the
    ``.pydocs-indexed`` marker touched inside the corpus dir, so a resumed run
    that re-materializes the same dir skips the expensive reindex.
    """

    cache_dir: Path

    async def ensure_indexed(self, corpus_dir: Path) -> Path:
        """Index ``corpus_dir`` once; return it. Reruns skip via the marker.

        The first call runs ``pydocs_mcp index`` (off-thread) then touches the
        marker; every later call for the same dir sees the marker and returns
        immediately. Idempotent so the orchestrator can call it before each arm
        without tracking state itself.
        """
        marker = corpus_dir / _INDEXED_MARKER
        if marker.exists():
            return corpus_dir
        await asyncio.to_thread(_run_index, corpus_dir)
        marker.touch()
        return corpus_dir


@dataclass(frozen=True, slots=True)
class FakeAgentRunner:
    """Scripted ``AgentRunner`` double for offline orchestrator tests.

    Returns the ``RunMetrics`` mapped to ``arm.name`` in ``by_arm``; an arm whose
    name is in ``fail_arms`` returns ``None`` (simulating a wall-timeout half-pair)
    so tests can drive the no-half-pairs discard path without a real subprocess.
    """

    by_arm: dict[str, RunMetrics] = field(default_factory=dict)
    fail_arms: frozenset[str] = frozenset()

    async def run(
        self,
        arm: ArmConfig,
        *,
        prompt: str,
        cwd: Path,
        mcp_config: Path | None,
    ) -> RunMetrics | None:
        """Return the scripted metrics for ``arm`` (or ``None`` if it's failed)."""
        _ = (prompt, cwd, mcp_config)  # scripted double ignores the run inputs
        if arm.name in self.fail_arms:
            return None
        return self.by_arm.get(arm.name)
