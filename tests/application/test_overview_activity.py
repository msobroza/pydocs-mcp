"""Git activity aggregates (§D17 block 9) — pure aggregator + writer + block render.

Five concerns:
1. ``compute_activity`` — pure over the framed git-log dump: per-module commit
   counts within the window (file→module mapping from path prefixes), the
   30d-vs-prior-30d trend ratio, top-5 modules.
2. JSON round-trip of :class:`ActivitySummary`.
3. Renderer golden — ``## Recent activity`` with the trend arrow (↑/→/↓).
4. Empty log → no summary (block omitted).
5. ``run_index_pass`` writes the activity JSON via the mapper when enabled.
6. ``OverviewService`` renders block 9 from the injected ``aggregates_reader``.
"""

from __future__ import annotations

from pydocs_mcp.application.formatting import format_overview_card
from pydocs_mcp.application.overview_aggregates import (
    ActivitySummary,
    OverviewAggregates,
    activity_from_json,
    activity_to_json,
    compute_activity,
)
from pydocs_mcp.application.overview_service import OverviewCard

_DAY = 86_400.0
# A stable "now" so the window math is deterministic. Commits are framed at
# author-date offsets before this instant.
_NOW = 1_000_000_000.0


def _frame(sha: str, *, days_ago: float, files: tuple[str, ...]) -> str:
    """One framed git-log record in the ``_git.read_git_log`` line format."""
    epoch = _NOW - days_ago * _DAY
    file_line = "files " + " ".join(files)
    return f"commit {sha}\nauthor-date {epoch}\nsubject touch {sha}\nbody \n{file_line}\n==END==\n"


def _log(*records: str) -> str:
    return "".join(records)


# ── 1. Pure aggregator ───────────────────────────────────────────────────


def test_compute_activity_counts_per_module_within_window() -> None:
    log = _log(
        _frame("a", days_ago=1, files=("python/pydocs_mcp/storage/sqlite.py",)),
        _frame("b", days_ago=2, files=("python/pydocs_mcp/storage/factories.py",)),
        _frame("c", days_ago=3, files=("python/pydocs_mcp/retrieval/pipeline.py",)),
        # Outside the 90d window — must be ignored.
        _frame("d", days_ago=200, files=("python/pydocs_mcp/storage/db.py",)),
    )
    summary = compute_activity(log, window_days=90, now=_NOW)

    counts = dict(summary.top_modules)
    assert counts["python/pydocs_mcp/storage"] == 2
    assert counts["python/pydocs_mcp/retrieval"] == 1
    assert summary.total_commits == 3  # the 200-day-old commit is excluded


def test_compute_activity_top_five_only() -> None:
    log = _log(*(_frame(f"c{i}", days_ago=1, files=(f"pkg/mod{i}/file.py",)) for i in range(8)))
    summary = compute_activity(log, window_days=90, now=_NOW)
    assert len(summary.top_modules) == 5


def test_trend_ratio_recent_vs_prior_thirty_days() -> None:
    log = _log(
        # 3 commits in the last 30d.
        _frame("r1", days_ago=1, files=("pkg/a/x.py",)),
        _frame("r2", days_ago=5, files=("pkg/a/x.py",)),
        _frame("r3", days_ago=10, files=("pkg/a/x.py",)),
        # 2 commits in the prior 30d (days 30-60).
        _frame("p1", days_ago=35, files=("pkg/a/x.py",)),
        _frame("p2", days_ago=50, files=("pkg/a/x.py",)),
    )
    summary = compute_activity(log, window_days=90, now=_NOW)
    assert summary.trend_ratio == 1.5  # 3 recent / 2 prior


def test_trend_ratio_flat_when_no_prior_commits() -> None:
    log = _log(_frame("r1", days_ago=1, files=("pkg/a/x.py",)))
    summary = compute_activity(log, window_days=90, now=_NOW)
    # No prior-30d commits → flat (no division by zero, treated as 1.0).
    assert summary.trend_ratio == 1.0


def test_compute_activity_empty_log_is_none() -> None:
    assert compute_activity("", window_days=90, now=_NOW) is None


# ── 2. JSON round-trip ───────────────────────────────────────────────────


def test_activity_json_round_trip() -> None:
    summary = ActivitySummary(
        top_modules=(("pkg/a", 3), ("pkg/b", 1)),
        trend_ratio=1.6,
        window_days=90,
        total_commits=4,
    )
    restored = activity_from_json(activity_to_json(summary))
    assert restored == summary


def test_activity_from_json_none_on_garbage() -> None:
    assert activity_from_json("not json") is None
    assert activity_from_json("") is None


# ── 3. Renderer golden ───────────────────────────────────────────────────


def _card_with_activity(summary: ActivitySummary | None) -> OverviewCard:
    return OverviewCard(
        package="__project__",
        package_count=1,
        module_count=1,
        symbol_count=1,
        doc_coverage=1.0,
        modules=(),
        entry_points=(),
        communities=(),
        dependency_profile=(),
        node_scores_available=True,
        activity=summary,
    )


def test_recent_activity_block_up_arrow() -> None:
    summary = ActivitySummary(
        top_modules=(("pkg/a", 3), ("pkg/b", 1)),
        trend_ratio=1.6,
        window_days=90,
        total_commits=4,
    )
    out = format_overview_card(_card_with_activity(summary))
    assert "## Recent activity" in out
    assert "↑1.6x" in out
    assert "`pkg/a` — 3 commits" in out


def test_recent_activity_block_flat_and_down_arrows() -> None:
    flat = ActivitySummary(
        top_modules=(("pkg/a", 1),), trend_ratio=1.0, window_days=90, total_commits=1
    )
    down = ActivitySummary(
        top_modules=(("pkg/a", 1),), trend_ratio=0.5, window_days=90, total_commits=1
    )
    assert "→" in format_overview_card(_card_with_activity(flat))
    assert "↓" in format_overview_card(_card_with_activity(down))


def test_recent_activity_block_omitted_when_absent() -> None:
    out = format_overview_card(_card_with_activity(None))
    assert "## Recent activity" not in out


# ── 4. Writer via run_index_pass (fake mapper) ───────────────────────────


async def test_run_index_pass_writes_activity_json_when_enabled() -> None:
    from pathlib import Path

    from pydocs_mcp.application.index_project import run_index_pass
    from pydocs_mcp.application.indexing_service import IndexingStats
    from pydocs_mcp.storage.index_metadata import IndexMetadata

    written: list[str | None] = []

    class _Orch:
        async def index_project(self, project: Path, **_kw: object) -> IndexingStats:
            return IndexingStats(indexed=1, cached=0)

    class _Svc:
        async def invalidate_stale_embeddings(self, *, current_model: str) -> list[str]:
            return []

    async def _ci() -> list[str]:
        return []

    async def _rf() -> None:
        return None

    def _sm(_meta: IndexMetadata) -> None:
        return None

    async def _write_activity(_project: Path) -> None:
        written.append("activity.json")

    await run_index_pass(
        orchestrator=_Orch(),
        indexing_service=_Svc(),
        pipeline_hash="h",
        project=Path("/tmp/p"),
        embedding_provider="fastembed",
        embedding_model="m",
        embedding_dim=384,
        force=False,
        include_project_source=True,
        include_dependencies=False,
        workers=1,
        check_integrity=_ci,
        rebuild_fts=_rf,
        stamp_metadata=_sm,
        write_aggregates=_write_activity,
    )
    assert written == ["activity.json"]


# ── 5. OverviewService renders block 9 from the injected reader ──────────


async def test_overview_service_renders_activity_from_reader() -> None:
    from tests._fakes import make_fake_uow_factory

    from pydocs_mcp.application.overview_service import OverviewService

    summary = ActivitySummary(
        top_modules=(("pkg/a", 2),), trend_ratio=1.6, window_days=90, total_commits=2
    )

    def _reader() -> OverviewAggregates:
        return OverviewAggregates(activity=summary)

    svc = OverviewService(
        uow_factory=make_fake_uow_factory(),
        scripts={},
        aggregates_reader=_reader,
    )
    card = await svc.build()
    assert card.activity == summary
    assert "## Recent activity" in format_overview_card(card)


async def test_overview_service_activity_none_without_reader() -> None:
    from tests._fakes import make_fake_uow_factory

    from pydocs_mcp.application.overview_service import OverviewService

    svc = OverviewService(uow_factory=make_fake_uow_factory(), scripts={})
    card = await svc.build()
    assert card.activity is None
