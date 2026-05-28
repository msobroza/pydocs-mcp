"""Whole-runner sanity: an oracle retriever must score perfectly.

If aggregate ``recall@10``, ``mrr``, and ``pass@1-needle`` don't all
collapse to 1.0 when the system literally hands back the gold function
body as the top hit, the bug is in the harness itself — the matcher,
the scorer, or the runner orchestration. The actual systems' scores are
validated by real benchmark runs; this test guards the SCAFFOLDING.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from benchmarks.eval.datasets.repoqa import RepoQADataset
from benchmarks.eval.runner import run_sweep
from benchmarks.eval.serialization import system_registry
from benchmarks.eval.systems.base_system import RetrievedItem

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

_FIXTURE = Path(__file__).parent / "fixtures" / "repoqa_mini.json"

# WHY: the runner calls ``system_registry.build(system_name)`` with no
# kwargs, so the oracle's per-task gold cannot be passed via constructor.
# A module-level lookup that the test populates before ``run_sweep`` runs
# is the cleanest channel — the system reads from it inside ``search``.
# Keyed by ``query`` because ``System.search(query, limit)`` is the only
# signal the system gets per task.
_ORACLE_LOOKUP: dict[str, str] = {}


@system_registry.register("oracle-integration-test")
@dataclass
class _OracleTestSystem:
    """Returns the gold body for the queried task as ``retrieved[0]``.

    Trivially satisfies every metric: gold lives at rank 1 ⇒ recall@k = 1
    for any k ≥ 1, mrr = 1/1 = 1, and pass@1-needle = 1.
    """

    name: str = "oracle-integration-test"

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        # WHY: oracle bypasses indexing entirely — the lookup table is the
        # entire "index". Nothing to do here.
        return None

    async def search(self, query: str, limit: int) -> tuple[RetrievedItem, ...]:
        gold = _ORACLE_LOOKUP.get(query)
        if gold is None:
            # WHY: an unknown query means the test forgot to populate the
            # lookup — return empty rather than fabricating output. The
            # zero-score assertion in test_integration_empty.py also pins
            # this branch indirectly.
            return ()
        return (
            RetrievedItem(
                rank=1,
                text=gold,
                source_path="<oracle>",
                qualified_name="oracle.gold",
                relevance=1.0,
            ),
        )

    async def teardown(self) -> None:
        return None


async def _populate_oracle_from_fixture() -> None:
    """Seed ``_ORACLE_LOOKUP`` by walking the Dataset Protocol — the same
    path the runner walks. Decouples this test from the on-disk JSON shape
    so future schema changes only touch the loader."""
    dataset = RepoQADataset(fixture_path=_FIXTURE)
    _ORACLE_LOOKUP.clear()
    async for task in dataset.tasks():
        _ORACLE_LOOKUP[task.query] = task.gold.ast_body or ""


async def test_oracle_system_scores_perfectly(tmp_path: Path) -> None:
    await _populate_oracle_from_fixture()
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")

    results, tasks_ran = await run_sweep(
        systems=("oracle-integration-test",),
        config_paths=(overlay,),
        dataset_name="repoqa",
        dataset_kwargs={"fixture_path": _FIXTURE},
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": tmp_path / "jsonl"}},
    )

    assert tasks_ran == 5
    aggregates = results[("oracle-integration-test", "baseline")]
    # WHY: three perfect-score assertions on the same concept — every
    # metric that depends on gold-in-retrieved must collapse to 1.0 when
    # gold *is* retrieved[0]. If any one fails, the bug is in that metric;
    # if all three fail, the bug is in the matcher or the runner.
    recall_at_10_mean, _, _ = aggregates["recall@10"]
    pass_at_1_mean, _, _ = aggregates["pass@1-needle"]
    mrr_mean, _, _ = aggregates["mrr"]
    assert recall_at_10_mean == 1.0
    assert pass_at_1_mean == 1.0
    assert mrr_mean == 1.0


async def test_oracle_system_scores_recall_at_1_and_5_perfectly(tmp_path: Path) -> None:
    # WHY: gold at rank 1 ⇒ every recall@k for k ≥ 1 scores 1.0. Pinning
    # recall@1 + recall@5 separately catches a k-off-by-one bug that
    # recall@10 alone would mask (a wrong slice that still happens to
    # include rank 1).
    await _populate_oracle_from_fixture()
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")

    results, _ = await run_sweep(
        systems=("oracle-integration-test",),
        config_paths=(overlay,),
        dataset_name="repoqa",
        dataset_kwargs={"fixture_path": _FIXTURE},
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": tmp_path / "jsonl"}},
    )

    aggregates = results[("oracle-integration-test", "baseline")]
    assert aggregates["recall@1"][0] == 1.0
    assert aggregates["recall@5"][0] == 1.0
