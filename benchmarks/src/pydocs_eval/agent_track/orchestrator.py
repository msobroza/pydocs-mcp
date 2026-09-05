"""Paired run orchestrator — ledger, resume, spend guardrails (spec §D15).

``run_agent_track`` drives the two-arm harness over a dataset: per task it
materializes the corpus once, indexes it for the indexed arm, runs both arms
SEQUENTIALLY, and — only when BOTH arms returned metrics — has the blind judge
score them. The paired aggregation the report consumes is only sound at
answer-quality parity, so the orchestrator enforces the spec's guardrails:

- **No half-pairs.** A task counts only when both arms AND the judge produced a
  result inside budget. A timed-out arm (runner returns ``None``) or a failed
  judge discards the whole task; the discard is logged to the ledger, never
  silently dropped (no-silent-caps).
- **Resumable, per ARM.** Every task_id — admitted OR discarded — is written to
  a JSONL ledger alongside the ``arm_hash`` it ran under, and a rerun skips any
  task_id already present FOR THAT ARM. A resumed run therefore neither re-pays
  for a completed pair nor re-attempts a task it already gave up on, while a run
  under a different scaffold, guidance, or arm surface re-runs instead of
  silently reusing an answer the old arm produced (run-contract design §8). A
  legacy line written before the key widened carries no ``arm_hash``, so it
  matches no arm and its task re-runs — the deliberate, one-time cost of the
  widening.
- **Bounded spend.** ``max_usd`` is checked against the ACTUAL cost accrued this
  invocation before each new pair (conservative: stop if the spend so far plus
  the worst pair observed so far would exceed the cap). ``max_tasks`` caps the
  number of admitted pairs.

Everything expensive is injected behind a Protocol: ``runner`` is an
``AgentRunner``, ``judge`` is a ``Judge``, and ``CorpusPrep`` does the one-time
index — so the whole loop is exercised offline with scripted doubles, no
subprocess, no network (the tests do exactly this).
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.agent_track._command import render_mcp_config, task_prompt
from pydocs_eval.agent_track._judge import Judge
from pydocs_eval.agent_track._runner import AgentRunner, CorpusPrep
from pydocs_eval.agent_track._types import (
    AgentTrackConfig,
    PairResult,
    RunMetrics,
)
from pydocs_eval.datasets.base_dataset import Dataset, EvalTask, GoldAnswer

log = logging.getLogger(__name__)

# The per-arm mcp config filename the indexed arm attaches. Written into the
# materialized corpus dir (which the orchestrator owns + rmtrees per task), so it
# never leaks between tasks. Single source of truth for a rename.
_MCP_CONFIG_NAME = ".mcp.json"


async def run_agent_track(
    cfg: AgentTrackConfig,
    *,
    dataset: Dataset,
    runner: AgentRunner,
    judge: Judge,
    ledger_path: Path,
    arm_hash: str,
) -> tuple[PairResult, ...]:
    """Run the paired harness over ``dataset``; return the admitted pairs.

    Iterates ``dataset.tasks()`` (a plain ``def`` returning an ``AsyncIterator`` —
    consumed with ``async for``, no intermediate await), skipping any task_id
    already in ``ledger_path`` UNDER ``arm_hash`` (resume). For each remaining
    task it runs one pair; a pair that completes inside budget is admitted,
    appended to the ledger, and returned; a half-pair (either arm timed out,
    judge failed) is discarded and the discard is logged. Stops when
    ``max_tasks`` admitted pairs is reached or when the next pair could exceed
    ``max_usd`` (checked against actual spend).

    ``arm_hash`` is REQUIRED and opaque (``_arm.external_arm_hash`` mints it):
    an optional one defaulting to ``None`` would match the legacy rows the
    widened key exists to invalidate.
    """
    ledger = _ArmLedger(path=ledger_path, arm_hash=arm_hash)
    done = ledger.done_task_ids()
    prep = CorpusPrep(cache_dir=cfg.output_dir)
    results: list[PairResult] = []
    spent = 0.0
    worst_pair = 0.0
    async for task in dataset.tasks():
        if len(results) >= cfg.max_tasks:
            break
        if task.task_id in done:
            continue
        if _would_overspend(spent=spent, worst_pair=worst_pair, cap=cfg.max_usd):
            log.info("agent-track: stop before %s — max_usd=%.2f", task.task_id, cfg.max_usd)
            break
        pair, pair_cost = await _run_one_pair(
            task, cfg=cfg, runner=runner, judge=judge, prep=prep, ledger=ledger
        )
        spent += pair_cost
        if pair is None:
            continue
        worst_pair = max(worst_pair, pair_cost)
        ledger.append_admitted(pair)
        results.append(pair)
    return tuple(results)


def _would_overspend(*, spent: float, worst_pair: float, cap: float) -> bool:
    """True if starting another pair could push total spend past ``cap``.

    Conservative: uses the WORST pair cost observed so far as the estimate for
    the next pair. Zero worst_pair (no pair completed yet) never blocks — the
    first pair always runs so the harness makes progress even with a tight cap.
    """
    return spent + worst_pair > cap


async def _run_one_pair(
    task: EvalTask,
    *,
    cfg: AgentTrackConfig,
    runner: AgentRunner,
    judge: Judge,
    prep: CorpusPrep,
    ledger: _ArmLedger,
) -> tuple[PairResult | None, float]:
    """Run both arms + judge for one task; return (pair-or-None, cost).

    Owns the materialized corpus dir for this task's lifetime and rmtrees it in a
    ``finally`` (the harness cleanup convention) so a crash mid-task can't leak it
    into the next. A discard (half-pair / judge failure) is logged to the ledger
    here; the returned cost is always the ACTUAL spend so far so the caller's
    ``max_usd`` guard sees a truthful running total even for a discarded pair.
    """
    corpus_dir = task.corpus_source()
    try:
        return await _run_arms_and_judge(
            task,
            corpus_dir,
            cfg=cfg,
            runner=runner,
            judge=judge,
            prep=prep,
            ledger=ledger,
        )
    finally:
        # WHY: the runner owns the materialized corpus (see corpus.py lifetime
        # contract) — rmtree between tasks so one task can't leak into the next.
        shutil.rmtree(corpus_dir, ignore_errors=True)


async def _run_arms_and_judge(
    task: EvalTask,
    corpus_dir: Path,
    *,
    cfg: AgentTrackConfig,
    runner: AgentRunner,
    judge: Judge,
    prep: CorpusPrep,
    ledger: _ArmLedger,
) -> tuple[PairResult | None, float]:
    prompt = task_prompt(task.query)
    bare_arm, indexed_arm = cfg.arms
    # Arm A (bare) first, then prep + arm B (indexed). SEQUENTIALLY on purpose:
    # WHY not parallel — the arms would contend for CPU during indexing/inference
    # and skew the wall-clock the report aggregates; a clean per-arm latency is
    # worth the extra wall time on a manual, never-CI run.
    bare = await runner.run(bare_arm, prompt=prompt, cwd=corpus_dir, mcp_config=None)
    mcp_config = await _prepare_indexed(corpus_dir, prep=prep)
    indexed = await runner.run(indexed_arm, prompt=prompt, cwd=corpus_dir, mcp_config=mcp_config)
    cost = _arm_cost(bare) + _arm_cost(indexed)
    if bare is None or indexed is None:
        ledger.append_discard(task, reason=_half_pair_reason(bare, indexed))
        return None, cost
    scores = await judge.score(
        question=task.query,
        gold=_gold_text(task.gold),
        answers={bare_arm.name: bare.answer, indexed_arm.name: indexed.answer},
    )
    if scores is None:
        ledger.append_discard(task, reason="judge-failed")
        return None, cost
    pair = PairResult(
        task_id=task.task_id,
        qa_type=task.metadata.get("qa_type", ""),
        bare=bare,
        indexed=indexed,
        judge=scores.get(indexed_arm.name),
    )
    return pair, cost


async def _prepare_indexed(corpus_dir: Path, *, prep: CorpusPrep) -> Path:
    """Index the corpus once for arm B and render its one-server mcp config.

    The config file is written INTO the corpus dir so it's cleaned up with the
    dir. Uses the harness's own interpreter so the served ``pydocs_mcp`` matches
    the installed one.
    """
    await prep.ensure_indexed(corpus_dir)
    mcp_config = corpus_dir / _MCP_CONFIG_NAME
    rendered = render_mcp_config(corpus_dir=corpus_dir, python=Path(sys.executable))
    mcp_config.write_text(rendered, encoding="utf-8")
    return mcp_config


def _half_pair_reason(bare: RunMetrics | None, indexed: RunMetrics | None) -> str:
    """Name which arm(s) timed out, for the discard ledger line."""
    failed = [name for name, m in (("bare", bare), ("indexed", indexed)) if m is None]
    return f"arm-timeout:{','.join(failed)}"


def _arm_cost(metrics: RunMetrics | None) -> float:
    """An arm's spend counts even when it timed out to zero cost (``None`` → 0)."""
    return metrics.cost_usd if metrics is not None else 0.0


def _gold_text(gold: GoldAnswer) -> str:
    """Render a ``GoldAnswer`` into the reference text the blind judge scores.

    The as-landed SweQaPro adapter stores only ``file_set`` (resolved citation
    paths), so the judge's reference is those paths plus any ``ast_body`` /
    ``extra`` when present. WHY not the raw answer prose: the dataset drops it
    into pseudo-qrels; the file-level citations are the stable, reproducible gold
    the judge grounds correctness against.
    """
    if gold.ast_body:
        return gold.ast_body
    parts = list(gold.file_set)
    if gold.extra:
        parts.append(json.dumps(dict(gold.extra), sort_keys=True))
    return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class _ArmLedger:
    """The JSONL ledger seen through ONE arm — reads and writes the resume key.

    Both line shapes (admitted and discarded) carry ``arm_hash`` as a sibling
    field in the ``.get``-tolerant pattern: legacy lines parse fine and simply
    belong to no arm, so they suppress nothing. Because a different arm now
    re-runs and APPENDS instead of being skipped, one file legitimately holds
    several arms' rows — so every consumer that reports a total (the CLI
    footer's spend/discard counts, ``__main__._footer_stats_from_ledger``)
    filters on this same ``arm_hash`` rather than summing the whole file.
    """

    path: Path
    arm_hash: str

    def done_task_ids(self) -> set[str]:
        """Task_ids already recorded UNDER THIS ARM (admitted OR discarded).

        A discarded task is 'done' too: rerunning must not re-attempt a task
        the previous run already gave up on, so both line shapes contribute
        their ``task_id``. A row whose ``arm_hash`` differs — including a
        legacy row that has none — is a DIFFERENT arm's answer and must not
        suppress this arm's run. A missing ledger yields an empty set.
        """
        if not self.path.exists():
            return set()
        ids: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            record = _parse_ledger_line(line)
            if record is None or record.get("arm_hash") != self.arm_hash:
                continue
            task_id = record.get("task_id")
            if task_id:
                ids.add(str(task_id))
        return ids

    def append_admitted(self, pair: PairResult) -> None:
        """Append one admitted pair to the JSONL ledger (one line, ``json.dumps``)."""
        self._append(
            {
                "task_id": pair.task_id,
                "arm_hash": self.arm_hash,
                "qa_type": pair.qa_type,
                **_pair_metrics(pair),
            }
        )

    def append_discard(self, task: EvalTask, *, reason: str) -> None:
        """Append a discard line — no-silent-caps: an operator sees every drop."""
        log.info("agent-track: discard %s — %s", task.task_id, reason)
        self._append({"task_id": task.task_id, "arm_hash": self.arm_hash, "discarded": reason})

    def _append(self, record: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")


def _parse_ledger_line(line: str) -> dict[str, object] | None:
    """Decode one ledger line; ``None`` for blank or corrupt (logged, skipped).

    The corrupt-line policy every sibling ledger already applies: a truncated
    line from a killed run must not crash a resume, and swallowing it silently
    would hide real damage.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        log.warning("agent-track: skipping corrupt ledger line: %.80s", stripped)
        return None
    if not isinstance(decoded, dict):
        log.warning("agent-track: skipping non-object ledger line: %.80s", stripped)
        return None
    return decoded


def _pair_metrics(pair: PairResult) -> dict[str, object]:
    """The scoring-relevant fields of an admitted pair, flattened for the ledger."""
    # PairResult.__post_init__ guarantees both arms; narrow for the type checker.
    assert pair.bare is not None
    assert pair.indexed is not None
    judge_mean = pair.judge.mean if pair.judge is not None else None
    return {
        "bare_cost": pair.bare.cost_usd,
        "indexed_cost": pair.indexed.cost_usd,
        "judge_mean_indexed": judge_mean,
    }
