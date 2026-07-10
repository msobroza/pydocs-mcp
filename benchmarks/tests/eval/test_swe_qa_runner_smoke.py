"""End-to-end smoke for ``run_sweep`` on the SWE-QA-Pro track.

Mirrors ``test_runner_smoke.py::test_runner_smoke_pydocs_jsonl_fixture`` in
shape, swapping the dataset for ``swe-qa-pro`` (file-level pseudo-qrels) and
the metric set for ``recall@5 / ndcg@10 / mrr``. Hermetic: the dataset is
driven from the checked-in fixture JSONL + a fake ``RepoCache`` pointing every
checkout at the shared fixture corpus dir (no git, no network); the embedder /
LLM are mocked by the autouse fixtures in ``benchmarks/tests/conftest.py``.

Pins two things the four SWE-QA configs must satisfy end-to-end:
  1. the 5-row fixture yields exactly 4 scorable tasks (1 citation-free row is
     excluded), so ``tasks_ran == 4``; and
  2. each returned result row carries the three requested metric keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.report import format_report
from pydocs_eval.runner import _task_rows_from_legs, run_sweep, run_sweep_detailed

_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"
_FIXTURES = Path(__file__).parent / "fixtures"
_PRO_FIXTURE = _FIXTURES / "swe_qa_pro_mini.jsonl"
_CORPUS_DIR = _FIXTURES / "swe_qa_corpus"

# Every path the fixture cites, as the fake repo's tracked-file listing.
_CORPUS_TREE = (
    "src/qibo/models/variational.py",
    "src/pkg/mod.py",
)

# The four SWE-QA-Pro overlays under test, one per retrieval config. Each is a
# two-line file selecting one of the RepoQA experiment blueprints.
_CONFIG_STEMS = (
    "swe_qa_pro_bm25",
    "swe_qa_pro_dense",
    "swe_qa_pro_hybrid_rrf_k60",
    "swe_qa_pro_graph",
)

_METRIC_SPECS = ("recall@5", "ndcg@10", "mrr")


@dataclass
class _FakeRepoCache:
    """Stand-in for ``RepoCache`` — no git, no network. ``file_tree`` returns a
    fixed listing; ``checkout`` returns the shared fixture corpus dir."""

    tree: tuple[str, ...] = _CORPUS_TREE
    corpus_dir: Path = field(default=_CORPUS_DIR)

    def checkout(self, url: str, sha: str) -> Path:
        return self.corpus_dir

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return self.tree


@pytest.mark.parametrize("config_stem", _CONFIG_STEMS)
async def test_swe_qa_pro_runner_smoke(config_stem: str, tmp_path: Path) -> None:
    overlay = _CONFIGS_DIR / f"{config_stem}.yaml"
    jsonl_dir = tmp_path / "jsonl"

    results, tasks_ran = await run_sweep(
        systems=("pydocs-mcp",),
        config_paths=(overlay,),
        dataset_name="swe-qa-pro",
        dataset_kwargs={"fixture_path": _PRO_FIXTURE, "repo_cache": _FakeRepoCache()},
        metric_specs=_METRIC_SPECS,
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": jsonl_dir}},
        limit=None,
    )

    # WHY: the 5-row fixture drops the 1 citation-free row, leaving 4 scorable
    # tasks — pins the file-level pseudo-qrel exclusion path end-to-end.
    assert tasks_ran == 4
    assert set(results.keys()) == {("pydocs-mcp", config_stem)}
    metrics = results[("pydocs-mcp", config_stem)]
    # WHY: each requested metric spec maps to exactly its own key in the
    # returned aggregate dict (``recall@5`` / ``ndcg@10`` / ``mrr``).
    assert set(_METRIC_SPECS).issubset(metrics.keys())
    for spec in _METRIC_SPECS:
        triple = metrics[spec]
        assert len(triple) == 3, f"{spec} aggregate shape changed"
        for v in triple:
            assert 0.0 <= v <= 1.0, f"{spec} value out of bounds: {v}"


async def test_swe_qa_pro_runner_emits_qa_type_breakout(tmp_path: Path) -> None:
    # WHY: this is the PRODUCTION path ``runner.main`` now takes — run the
    # detailed sweep, project the legs into ``task_rows``, and render. The
    # fixture's four scorable rows span three distinct ``qa_type`` first-words
    # (How / Where / What / Why), so the ``## By qa_type`` breakout the README
    # documents must actually appear in the CLI's report (previously the
    # feature was exercised only by report.py unit tests — dead in the runner).
    overlay = _CONFIGS_DIR / "swe_qa_pro_bm25.yaml"
    jsonl_dir = tmp_path / "jsonl"

    outcome = await run_sweep_detailed(
        systems=("pydocs-mcp",),
        config_paths=(overlay,),
        dataset_name="swe-qa-pro",
        dataset_kwargs={"fixture_path": _PRO_FIXTURE, "repo_cache": _FakeRepoCache()},
        metric_specs=_METRIC_SPECS,
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": jsonl_dir}},
        limit=None,
    )

    task_rows = _task_rows_from_legs(outcome.legs)
    report = format_report(
        sweep_results=outcome.results,
        dataset_name="swe-qa-pro",
        n_tasks=outcome.tasks_ran,
        task_rows=task_rows,
    )

    assert "## By qa_type" in report
    # Every scorable row's qa_type first-word must label a breakout row.
    section = report.split("## By qa_type", 1)[1]
    for category in ("How", "Where", "What", "Why"):
        assert f"| {category} |" in section
