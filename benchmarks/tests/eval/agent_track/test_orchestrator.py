"""Pair orchestrator — ledger, resume, guardrails (spec §D15).

Fully offline: the orchestrator is driven with scripted ``AgentRunner`` /
``Judge`` doubles and the slice-4 ``SweQaProDataset`` in its ``fixture_path``
mode (no git, no network — a ``_FakeRepoCache`` returns a fixed file tree and a
fixture corpus dir). ``CorpusPrep`` is patched to a no-op so no real
``pydocs_mcp index`` runs. The four load-bearing behaviors are asserted here:

- both arms run and every admitted task carries a judge verdict;
- a timed-out arm (runner returns ``None``) discards the whole pair — no
  half-pairs admitted — and the discard is logged to the ledger;
- a resumed run skips task_ids already in the ledger (no rerun);
- the ``max_usd`` guard stops BEFORE starting a pair that could overspend, and
  ``max_tasks`` caps the number of admitted pairs.

The as-landed ``FakeAgentRunner`` keys metrics by ``arm.name`` and cannot script
per-task failure / per-run cost / call counting, so this module ships a richer
``_ScriptRunner`` that satisfies the same ``AgentRunner`` Protocol — the plan's
test intent (fail one arm on one task, fixed cost per run, count calls per task)
mapped onto the as-landed seams.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from benchmarks.eval.agent_track._judge import Judge
from benchmarks.eval.agent_track._runner import CorpusPrep
from benchmarks.eval.agent_track._types import (
    AgentTrackConfig,
    ArmConfig,
    JudgeScore,
    PairResult,
    RunMetrics,
)
from benchmarks.eval.agent_track.orchestrator import run_agent_track
from benchmarks.eval.datasets._repo_cache import RepoCacheLike
from benchmarks.eval.datasets.base_dataset import Dataset, EvalTask
from benchmarks.eval.datasets.swe_qa_pro import SweQaProDataset

import pytest

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "swe_qa_pro_mini.jsonl"
_CORPUS = Path(__file__).parents[1] / "fixtures" / "swe_qa_corpus"

# Pin the exported Protocol so a rename fails this module, not just at runtime.
_ = (Judge, PairResult)


class _FakeRepoCache:
    """No-git/no-network ``RepoCacheLike``: fixed tree + a fixture corpus dir.

    Every citation in the fixture jsonl resolves against this tree so each row
    yields a task with a non-empty ``file_set`` (no rows are dropped), giving the
    orchestrator tests a stable, ordered task stream.
    """

    _TREE = (
        "src/qibo/models/variational.py",
        "src/pkg/mod.py",
    )

    def checkout(self, url: str, sha: str) -> Path:
        return _CORPUS

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return self._TREE


@dataclass
class _LimitedDataset:
    """Yield only the first ``limit`` tasks of an inner dataset.

    Lets a test pin the task stream to exactly N rows so a discard assertion
    ("no half-pairs admitted") isn't masked by a later fixture row backfilling
    the freed ``max_tasks`` slot.
    """

    inner: Dataset
    limit: int
    name: str = "swe-qa-pro"
    revision: str = "test"

    async def tasks(self) -> AsyncIterator[EvalTask]:
        count = 0
        async for task in self.inner.tasks():
            if count >= self.limit:
                return
            count += 1
            yield task


def _dataset(repo_cache: RepoCacheLike | None = None) -> SweQaProDataset:
    return SweQaProDataset(
        fixture_path=_FIXTURE,
        repo_cache=repo_cache if repo_cache is not None else _FakeRepoCache(),
    )


def _metrics(*, arm: str, cost: float) -> RunMetrics:
    return RunMetrics(
        cost_usd=cost,
        wall_seconds=5.0,
        turns=3,
        tool_calls=4,
        distinct_files_read=2,
        cache_read_tokens=1000,
        cache_write_tokens=200,
        answer=f"{arm} answer",
    )


def _task_token(text: str) -> str:
    # Each fixture row's question is distinctive; map a prompt (or task_id) to a
    # stable per-row token the runner scripting / resume assertions key on.
    lowered = text.lower()
    for token in ("qaoa", "vqe", "load", "store", "module"):
        if token in lowered:
            return token
    return "unknown"


@dataclass
class _ScriptRunner:
    """Scripted ``AgentRunner`` for the orchestrator tests.

    ``cost_per_run`` is the per-arm cost every run reports (so a pair's spend is
    predictable for the ``max_usd`` guard). ``fail_on`` is a set of
    ``(arm_name, task_token)`` pairs: a run whose arm+prompt matches returns
    ``None`` (a wall-timeout half-pair). ``calls`` records every (arm, token) so a
    resume test can assert a completed task is never re-run.
    """

    cost_per_run: float = 0.10
    fail_on: frozenset[tuple[str, str]] = frozenset()
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def run(
        self,
        arm: ArmConfig,
        *,
        prompt: str,
        cwd: Path,
        mcp_config: Path | None,
    ) -> RunMetrics | None:
        token = _task_token(prompt)
        self.calls.append((arm.name, token))
        if (arm.name, token) in self.fail_on:
            return None
        return _metrics(arm=arm.name, cost=self.cost_per_run)

    def calls_for(self, token: str) -> int:
        return sum(1 for _arm, tok in self.calls if tok == token)


@dataclass
class _ScriptJudge:
    """Scripted ``Judge``: a fixed spread favoring the indexed arm."""

    async def score(
        self,
        *,
        question: str,
        gold: str,
        answers: dict[str, str],
    ) -> dict[str, JudgeScore] | None:
        return {
            "bare": JudgeScore(
                correctness=4,
                completeness=3,
                relevance=5,
                clarity=6,
                reasoning="",
                mean=4.4,
            ),
            "indexed": JudgeScore(
                correctness=9,
                completeness=8,
                relevance=9,
                clarity=9,
                reasoning="",
                mean=8.6,
            ),
        }


@pytest.fixture(autouse=True)
def _no_real_index(monkeypatch) -> None:
    # CorpusPrep.ensure_indexed must never shell out to ``pydocs_mcp index`` in
    # tests — patch it to return the dir unchanged so arm B's prep is a no-op.
    async def _noop(self, corpus_dir: Path) -> Path:
        return corpus_dir

    monkeypatch.setattr(CorpusPrep, "ensure_indexed", _noop)


def _cfg(*, max_tasks: int = 48, max_usd: float = 25.0) -> AgentTrackConfig:
    return AgentTrackConfig(max_tasks=max_tasks, max_usd=max_usd)


def _ledger_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def test_runs_both_arms_and_judges_each_task(tmp_path) -> None:
    results = await run_agent_track(
        _cfg(max_tasks=2),
        dataset=_dataset(),
        runner=_ScriptRunner(),
        judge=_ScriptJudge(),
        ledger_path=tmp_path / "pairs.jsonl",
    )
    assert len(results) == 2
    assert all(r.bare and r.indexed and r.judge for r in results)


async def test_half_pair_discarded_and_logged(tmp_path) -> None:
    # Two-row slice: arm B (indexed) times out on the second row ("vqe") — that
    # whole pair is discarded, leaving only the first ("qaoa") admitted.
    runner = _ScriptRunner(fail_on=frozenset({("indexed", "vqe")}))
    ledger = tmp_path / "pairs.jsonl"
    results = await run_agent_track(
        _cfg(max_tasks=2),
        dataset=_LimitedDataset(inner=_dataset(), limit=2),
        runner=runner,
        judge=_ScriptJudge(),
        ledger_path=ledger,
    )
    # Row 0 (task_id .../0, the "qaoa" question) survives; row 1 (.../1, the
    # "vqe" question whose indexed arm timed out) is a half-pair, never admitted.
    admitted_ids = {r.task_id for r in results}
    assert len(admitted_ids) == 1
    assert next(iter(admitted_ids)).endswith("/0")
    # The discard is logged to the ledger (no-silent-caps): a discard line names
    # the timed-out task_id and a reason, alongside the one completed pair.
    discards = [line for line in _ledger_lines(ledger) if "discarded" in line]
    assert len(discards) == 1 and discards[0]["discarded"]
    assert discards[0]["task_id"].endswith("/1")


async def test_resume_skips_completed_pairs(tmp_path) -> None:
    ledger = tmp_path / "pairs.jsonl"
    # First run admits exactly the first task (max_tasks=1 → the "qaoa" row).
    first = await run_agent_track(
        _cfg(max_tasks=1),
        dataset=_dataset(),
        runner=_ScriptRunner(),
        judge=_ScriptJudge(),
        ledger_path=ledger,
    )
    assert len(first) == 1 and first[0].task_id.endswith("/0")
    # The first admitted task is row 0, whose question asks about "QAOA".
    done_token = "qaoa"
    # Second run: the already-completed task must NOT be re-run.
    counting = _ScriptRunner()
    await run_agent_track(
        _cfg(max_tasks=2),
        dataset=_dataset(),
        runner=counting,
        judge=_ScriptJudge(),
        ledger_path=ledger,
    )
    assert counting.calls_for(done_token) == 0  # ledger short-circuits


async def test_max_usd_aborts_before_next_pair(tmp_path) -> None:
    # 2 arms @ $10 = $20 for pair 1; the guard estimates the next pair at the
    # worst observed pair cost ($20), so $20 + $20 > $25 → stop before pair 2.
    runner = _ScriptRunner(cost_per_run=10.0)
    results = await run_agent_track(
        _cfg(max_tasks=48, max_usd=25.0),
        dataset=_dataset(),
        runner=runner,
        judge=_ScriptJudge(),
        ledger_path=tmp_path / "pairs.jsonl",
    )
    assert len(results) == 1  # stopped, not overspent


async def test_max_tasks_cap(tmp_path) -> None:
    # The fixture yields 5 tasks; max_tasks=3 admits exactly three pairs.
    results = await run_agent_track(
        _cfg(max_tasks=3),
        dataset=_dataset(),
        runner=_ScriptRunner(),
        judge=_ScriptJudge(),
        ledger_path=tmp_path / "pairs.jsonl",
    )
    assert len(results) == 3
