"""Subprocess adapter + corpus prep for the agent track (spec §D15).

Subprocess-free tests: ``ClaudeAgentRunner``'s orchestration is exercised with a
monkeypatched ``_spawn`` returning canned stdout (the committed stream fixture),
so the merge of ``parse_stream_events`` + ``parse_result_json`` into a
``RunMetrics`` is verified without ever launching ``claude``. A hanging
``_spawn`` proves the wall-timeout returns ``None`` (a half-pair the orchestrator
discards) rather than raising. ``CorpusPrep`` is tested with ``_run_index``
patched to a recorder so the marker-file skip is asserted without invoking the
real ``pydocs_mcp index``. ``FakeAgentRunner`` is the scripted double downstream
tasks build their orchestrator tests on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from benchmarks.eval.agent_track._runner import (
    ClaudeAgentRunner,
    CorpusPrep,
    FakeAgentRunner,
)
from benchmarks.eval.agent_track._types import ArmConfig, RunMetrics

_FIXTURES = Path(__file__).parent / "fixtures"
# The committed stream fixture ends with a ``result`` line carrying cost/turns —
# exactly what the CLI emits under ``--output-format stream-json``, so it feeds
# BOTH parsers (per-event stats + the final result JSON) the runner merges.
FIXTURE_STREAM = (_FIXTURES / "claude_stream.jsonl").read_text(encoding="utf-8")


def _arm(*, mcp: bool) -> ArmConfig:
    return ArmConfig(name="indexed" if mcp else "bare", mcp=mcp)


def _canned_spawn(stdout: str) -> Callable[..., object]:
    async def _spawn(cmd, *, cwd):  # test double: ignores argv + cwd
        return stdout

    return _spawn


def _hanging_spawn() -> Callable[..., object]:
    async def _spawn(cmd, *, cwd):  # test double: never returns before timeout
        await asyncio.sleep(10)
        return ""

    return _spawn


async def test_runner_combines_parsers_into_run_metrics(monkeypatch, tmp_path) -> None:
    runner = ClaudeAgentRunner(task_timeout_seconds=5.0)
    monkeypatch.setattr(runner, "_spawn", _canned_spawn(FIXTURE_STREAM))
    metrics = await runner.run(_arm(mcp=False), prompt="q", cwd=tmp_path, mcp_config=None)
    assert metrics is not None
    assert metrics.tool_calls == 5 and metrics.cost_usd > 0
    assert metrics.distinct_files_read == 2
    assert metrics.answer.startswith("The synchronization")


async def test_timeout_returns_none_not_raise(monkeypatch, tmp_path) -> None:
    runner = ClaudeAgentRunner(task_timeout_seconds=0.01)
    monkeypatch.setattr(runner, "_spawn", _hanging_spawn())
    assert await runner.run(_arm(mcp=False), prompt="q", cwd=tmp_path, mcp_config=None) is None


async def test_prepare_corpus_indexes_once(monkeypatch, tmp_path) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(
        "benchmarks.eval.agent_track._runner._run_index",
        lambda p: calls.append(p),
    )
    corpus = tmp_path / "co"
    corpus.mkdir()
    prep = CorpusPrep(cache_dir=tmp_path)
    d1 = await prep.ensure_indexed(corpus)
    d2 = await prep.ensure_indexed(corpus)
    assert len(calls) == 1  # marker file skips the second index
    assert d1 == corpus and d2 == corpus


async def test_fake_agent_runner_scripts_per_arm(tmp_path) -> None:
    bare = RunMetrics(
        cost_usd=0.10,
        wall_seconds=5.0,
        turns=3,
        tool_calls=4,
        distinct_files_read=2,
        cache_read_tokens=1000,
        cache_write_tokens=200,
        answer="bare answer",
    )
    runner = FakeAgentRunner(by_arm={"bare": bare})
    out = await runner.run(_arm(mcp=False), prompt="q", cwd=tmp_path, mcp_config=None)
    assert out is bare


async def test_fake_agent_runner_can_fail_a_named_arm(tmp_path) -> None:
    indexed = RunMetrics(
        cost_usd=0.20,
        wall_seconds=6.0,
        turns=4,
        tool_calls=5,
        distinct_files_read=3,
        cache_read_tokens=2000,
        cache_write_tokens=300,
        answer="indexed answer",
    )
    runner = FakeAgentRunner(by_arm={"indexed": indexed}, fail_arms={"indexed"})
    assert (
        await runner.run(_arm(mcp=True), prompt="q", cwd=tmp_path, mcp_config=tmp_path / "m.json")
        is None
    )
