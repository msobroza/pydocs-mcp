"""The COMPOSED harness: one external CLI agent, run against the run contract.

Two harness kinds exist (spec amendment 2026-07-28). A SELF-CONTAINED harness
owns its whole loop (``harness/builtin/ask_your_docs``). A COMPOSED harness
HAS-A CLI agent adapter: it owns the corpus, the trace, the guidance policy and
the :class:`~pydocs_mcp.harness.platform.contract.Trajectory`, and delegates
only "what is the argv" and "what does the transcript say" to the ENGINE. That
is why swapping ``claude`` for another CLI is one adapter subclass and one
registry line, and never a second harness.

Guidance policy is a HARNESS property, so ``harness_name`` — not the engine
name — keys the ``HARNESS_TASK_HEAD:`` tier every engine under this harness
shares. The engine is a separately RECORDED identity dimension: it rides the
arm's ``settings``, which the arm fingerprint folds whole.

Trace lifecycle: the ADR 0009 correlation identity rides the ``.mcp.json``
server ``env`` block (the CLI child starts from its own environment, so
parent-environ mutation would neither reach it nor survive concurrent runs).
An MCP-attached run that comes back traceless is a hard error; a BARE run
legitimately produces no server trace, and every tool call it made surfaces as
a CLIENT observation instead.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.platform.contract import (
    ToolCallObservation,
    ToolCallRecord,
    Trajectory,
    TurnBudgetExceededError,
    missing_sample_keys,
)
from pydocs_mcp.harness.platform.engines.base import CliAgentAdapter, CliRunRequest, CliToolCall
from pydocs_mcp.harness.platform.guidance_fold import fold_guidance_sections
from pydocs_mcp.harness.platform.serve_config import MCP_SERVER_NAME
from pydocs_mcp.observability.trace_env import trace_subprocess_env
from pydocs_mcp.observability.trace_reader import read_tool_call_records, tool_args_digest
from pydocs_mcp.observability.trace_writer import SERVER_EVENTS_FILENAME


class ComposedHarnessSettings(Protocol):
    """The settings SHAPE a composed harness reads — the platform-side port.

    Structural by construction: naming a concrete ``builtin`` settings model
    here (even under ``TYPE_CHECKING``) would make the reusable machinery
    depend on one harness INSTANCE, which is exactly what the one-way
    dependency rule in ``platform/__init__.py`` forbids. Each harness validates
    its own model — ``extra="forbid"``, so a typo fails loud at its own
    ``make_harness_runner`` — and hands the result in; this Protocol states
    only the eight attributes :class:`CliAgentHarness` actually reads.

    Members are read-only properties because every implementation is frozen; a
    read-only member is satisfied by a plain attribute too, so a pydantic model
    conforms without inheriting anything.
    """

    @property
    def workspace(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def trace_root(self) -> str: ...

    @property
    def mcp(self) -> bool: ...

    @property
    def pydocs_config(self) -> str | None: ...

    @property
    def tool_names(self) -> tuple[str, ...] | None: ...

    @property
    def max_agent_turns(self) -> int: ...

    @property
    def task_timeout_seconds(self) -> float: ...


class ExternalSampleContractError(PydocsMCPError, ValueError):
    """A sample row missing the run contract's required keys (rule 6)."""

    def __init__(self, *, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__(
            f"sample is missing required key(s) {list(missing)} — the run "
            "contract requires record_id, task_name, rendered_prompt, gold"
        )


class ExternalTraceMissingError(PydocsMCPError, RuntimeError):
    """An MCP-attached run came back traceless (contract rule 4)."""

    def __init__(self, *, trace_dir: Path) -> None:
        self.trace_dir = trace_dir
        super().__init__(
            f"no server trace at {trace_dir} after an MCP-attached run — "
            "refusing to return a scoreable trajectory (ADR 0009 correlation "
            "contract; check the .mcp.json env block reached the serve child)"
        )


@dataclass(frozen=True, slots=True)
class CliAgentHarness:
    """One CLI-agent engine, composed into a ``HarnessRunner``.

    Conforms to the port structurally AND nominally (``isinstance`` via the
    runtime-checkable Protocol).
    """

    harness_name: str
    adapter: CliAgentAdapter
    settings: ComposedHarnessSettings

    async def run(
        self, sample: Mapping[str, object], guidance_sections: Mapping[str, str]
    ) -> Trajectory:
        """One sample through this harness, returning its trajectory.

        Ordered so nothing can spend before the two cheap contract checks: a
        nonconformant row and an undeliverable guidance section both fail
        before the subprocess is built.
        """
        missing = missing_sample_keys(sample)
        if missing:
            raise ExternalSampleContractError(missing=missing)
        suffix = fold_guidance_sections(
            guidance_sections,
            harness_name=self.harness_name,
            task_name=str(sample["task_name"]),
        )

        trajectory_id = str(uuid.uuid4())
        trace_root = Path(self.settings.trace_root).expanduser()
        trace_dir = trace_root / trajectory_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        workspace = Path(self.settings.workspace).expanduser()
        request = self._run_request(
            sample,
            suffix=suffix,
            workspace=workspace,
            trace_dir=trace_dir,
            trace_root=trace_root,
            trajectory_id=trajectory_id,
        )

        started = time.monotonic()
        stdout = await _run_cli_process(
            self.adapter.build_command(request),
            cwd=workspace,
            timeout_seconds=self.settings.task_timeout_seconds,
        )
        wall_seconds = time.monotonic() - started

        result = self.adapter.parse_transcript(stdout)
        if result.turn_budget_exhausted:
            # The engine METERS this run, so the spend it already reported
            # travels with the failure — a wrapper that scores the cap as a
            # failed trajectory still bills the campaign what it cost.
            raise TurnBudgetExceededError(
                turn_limit=self.settings.max_agent_turns, cost_usd=result.cost_usd
            )
        return Trajectory(
            trajectory_id=trajectory_id,
            trace_dir=trace_dir,
            answer=result.answer,
            tool_calls=self._joined_tool_calls(trace_dir, result.tool_calls),
            turns=result.turns,
            cost_usd=result.cost_usd,
            wall_seconds=wall_seconds,
        )

    def _run_request(
        self,
        sample: Mapping[str, object],
        *,
        suffix: str,
        workspace: Path,
        trace_dir: Path,
        trace_root: Path,
        trajectory_id: str,
    ) -> CliRunRequest:
        """Bind one run's settings + sample into the engine-neutral request."""
        mcp_config = self._write_mcp_config(
            workspace=workspace,
            trace_dir=trace_dir,
            trace_root=trace_root,
            trajectory_id=trajectory_id,
        )
        return CliRunRequest(
            prompt=str(sample["rendered_prompt"]),
            cwd=workspace,
            model=self.settings.model,
            max_turns=self.settings.max_agent_turns,
            allowed_tools=self._allowed_tools(),
            mcp_config=mcp_config,
            system_prompt_suffix=suffix,
            # The correlation key is also the CLI session id, so stdout, the
            # result envelope and the on-disk transcript all name this
            # trajectory. It is a dashed UUID because the CLI validates it.
            session_id=trajectory_id,
        )

    def _allowed_tools(self) -> tuple[str, ...]:
        """This arm's resolved tool grant, in the ENGINE's own spelling.

        Three profiles. A BARE arm grants the engine's built-ins only. An
        MCP-attached arm adds the whole server. An explicit ``tool_names``
        REPLACES the profile grant with exactly that MCP surface — the
        established drop-one shape (the standalone track's ``ArmConfig.tools``
        semantics), which is also what §6's arm C wants: dropping ``Bash`` with
        the rest of the built-ins is what makes the arm compare retrieval
        surfaces rather than shell-vs-retrieval.

        The names arrive in the SERVER's vocabulary (the frozen nine, which is
        what the arms platform's ``tool_names`` narrows within) and leave in
        the engine's, because only the engine knows how it namespaces them.
        """
        if self.settings.tool_names is not None:
            return self.adapter.mcp_tool_grant(MCP_SERVER_NAME, self.settings.tool_names)
        if self.settings.mcp:
            return (*self.adapter.file_tools, *self.adapter.mcp_tool_grant(MCP_SERVER_NAME, None))
        return self.adapter.file_tools

    def _write_mcp_config(
        self, *, workspace: Path, trace_dir: Path, trace_root: Path, trajectory_id: str
    ) -> Path | None:
        """Write this trajectory's MCP config, or ``None`` for a bare arm.

        Both the document and its filename come from the ENGINE: the schema is
        a spelling of one binary's config reader, so rendering one engine's
        would hand another a config it silently ignores. The harness supplies
        only the run-shaped inputs and owns the per-trajectory LOCATION —
        ``trace_dir``, never the workspace, which concurrent trajectories share.
        """
        if not self.settings.mcp:
            return None
        overlay = (
            Path(self.settings.pydocs_config).expanduser() if self.settings.pydocs_config else None
        )
        rendered = self.adapter.render_mcp_config(
            corpus_dir=workspace,
            python=Path(sys.executable),
            env=trace_subprocess_env(trace_root, trajectory_id),
            overlay=overlay,
        )
        path = trace_dir / self.adapter.mcp_config_filename
        path.write_text(rendered, encoding="utf-8")
        return path

    def _joined_tool_calls(
        self, trace_dir: Path, transcript_calls: tuple[CliToolCall, ...]
    ) -> tuple[ToolCallRecord, ...]:
        """The SERVER slice from the trace, then the CLIENT-only remainder.

        A bare arm attaches no server, so its trace is legitimately absent and
        every transcript call is a CLIENT observation; an MCP-attached run that
        left no trace is the hard error rule 4 demands.
        """
        if self.settings.mcp and not (trace_dir / SERVER_EVENTS_FILENAME).exists():
            raise ExternalTraceMissingError(trace_dir=trace_dir)
        server_records = read_tool_call_records(trace_dir)
        remaining: dict[str, int] = {}
        for record in server_records:
            remaining[record.tool_name] = remaining.get(record.tool_name, 0) + 1
        client_only = _client_only_records(
            transcript_calls, remaining, to_server_name=self.adapter.server_tool_name
        )
        return (*server_records, *client_only)


def _client_only_records(
    transcript_calls: tuple[CliToolCall, ...],
    remaining: dict[str, int],
    *,
    to_server_name: Callable[[str], str],
) -> tuple[ToolCallRecord, ...]:
    """Transcript calls the server never saw, matched by name multiset.

    The two observation points name the SAME call differently — the CLI reports
    its namespaced ``mcp__<server>__<tool>``, the server records its bare
    registration name — so the match is made in the SERVER's vocabulary via the
    engine's own translation. Matching raw names would emit every MCP call
    twice, once per observation point. The record a client-only call keeps is
    the name the CLIENT saw, because that is what was observed there.
    """
    records: list[ToolCallRecord] = []
    for call in transcript_calls:
        server_name = to_server_name(call.tool_name)
        if remaining.get(server_name, 0) > 0:
            remaining[server_name] -= 1
            continue
        records.append(
            ToolCallRecord(
                tool_name=call.tool_name,
                args_digest=tool_args_digest(dict(call.args)),
                observed_by=ToolCallObservation.CLIENT,
            )
        )
    return tuple(records)


async def _run_cli_process(cmd: list[str], *, cwd: Path, timeout_seconds: float) -> str:
    """Spawn ``cmd`` in ``cwd`` and return its decoded stdout.

    The monkeypatch seam for tests — the one place this harness spends. The
    timeout is enforced AROUND the spawn await so it bounds any implementation,
    and the child runs in its own session so a timeout kills the whole tree
    (the CLI spawns MCP-server and tool subprocesses of its own). The timeout
    propagates as ``TimeoutError``: the campaign's runner wrapper is what turns
    it into a scoreable failed trajectory, so one hung sample never silently
    becomes a passing one here.
    """
    return await asyncio.wait_for(_spawn(cmd, cwd=cwd), timeout=timeout_seconds)


async def _spawn(cmd: list[str], *, cwd: Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        # stdin is CLOSED, not inherited: a CLI agent that finds a non-TTY fd 0
        # reads it and folds it into the prompt (``opencode run`` does exactly
        # this — ``process.stdin.isTTY ? undefined : await Bun.stdin.text()``
        # then ``value + "\n" + piped``), so an inherited pipe either blocks the
        # whole rollout until the task timeout or silently mutates the measured
        # input. No engine here delivers its prompt on stdin.
        stdin=asyncio.subprocess.DEVNULL,
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


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Best-effort SIGKILL of the child's process group after a cancellation.

    ``killpg`` (the child ran under ``start_new_session=True``) so the whole
    tree dies, not just the top process. Errors are swallowed: the child may
    already be dead, and a timeout must resolve cleanly rather than raise a
    second, less informative failure.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return
