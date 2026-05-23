"""Tests for benchmarks.eval.plotting.

Headless matplotlib — backend forced to ``Agg`` before pyplot imports so the
tests don't spin up a GUI toolkit on developer machines or in CI.
"""
from __future__ import annotations

import json
import os

# Force the non-GUI backend BEFORE matplotlib.pyplot loads anywhere.
os.environ.setdefault("MPLBACKEND", "Agg")

from pathlib import Path  # noqa: E402

import matplotlib  # noqa: E402

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402
from matplotlib.container import BarContainer  # noqa: E402


def _bar_containers(ax) -> list[BarContainer]:
    return [c for c in ax.containers if isinstance(c, BarContainer)]

from benchmarks.eval.plotting import (  # noqa: E402
    BaselineRecord,
    _format_seconds,
    plot_baselines,
    plot_metric_vs_latency,
    plot_timings,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


def _baseline_payload(**overrides: object) -> dict[str, object]:
    """Default baseline-JSON shape matching benchmarks/baselines/*.json.

    Includes both score metrics (mean + 95%% CI) and timing metrics
    (p50 / p95 / p99) so plot_baselines, plot_timings, and
    plot_metric_vs_latency all have data to work with.
    """
    base: dict[str, object] = {
        "dataset": "repoqa-2024-06-23-python",
        "system": "pydocs-mcp",
        "config": "baseline",
        "label": "real-100-needles",
        "tasks_ran": 100,
        "metrics": {
            "recall@1":  {"mean": 0.14,  "ci_low": 0.07,  "ci_high": 0.21},
            "recall@5":  {"mean": 0.17,  "ci_low": 0.10,  "ci_high": 0.24},
            "recall@10": {"mean": 0.18,  "ci_low": 0.11,  "ci_high": 0.26},
            "mrr":       {"mean": 0.152, "ci_low": 0.088, "ci_high": 0.218},
            "indexing_seconds": {"p50": 7.45,  "p95": 53.70, "p99": 62.23},
            "search_seconds":   {"p50": 0.021, "p95": 0.098, "p99": 0.279},
        },
        "captured_at": "2026-05-23T20:45:29+00:00",
        "git_sha": "0123456789abcdef0123",
        "source_jsonl": "ignored",
    }
    base.update(overrides)
    return base


def _write_baseline(tmp_path: Path, name: str, **overrides: object) -> Path:
    payload = _baseline_payload(**overrides)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload))
    return path


# ── BaselineRecord ────────────────────────────────────────────────────────


def test_baseline_record_from_path_parses_full_shape(tmp_path: Path) -> None:
    path = _write_baseline(tmp_path, "snf")
    rec = BaselineRecord.from_path(path)
    assert rec.system == "pydocs-mcp"
    assert rec.config == "baseline"
    assert rec.label == "real-100-needles"
    assert rec.dataset == "repoqa-2024-06-23-python"
    assert rec.tasks_ran == 100
    assert rec.git_sha == "0123456789abcdef0123"
    assert rec.metrics["recall@10"]["mean"] == pytest.approx(0.18)


def test_baseline_record_display_label() -> None:
    rec = BaselineRecord(
        system="pydocs-mcp",
        config="baseline",
        label="real-100-needles",
        dataset="repoqa-2024-06-23-python",
        tasks_ran=100,
        metrics={},
        captured_at=None,
        git_sha=None,
    )
    assert rec.display_label == "pydocs-mcp / baseline (real-100-needles)"


def test_baseline_record_legend_suffix_includes_sha_and_n() -> None:
    rec = BaselineRecord(
        system="pydocs-mcp",
        config="baseline",
        label="real-100-needles",
        dataset="repoqa-2024-06-23-python",
        tasks_ran=100,
        metrics={},
        captured_at=None,
        git_sha="0123456789abcdef0123",
    )
    suffix = rec.legend_suffix
    # label is in display_label, NOT duplicated in the suffix.
    assert "0123456" in suffix  # first 7 chars of git sha
    assert "n=100" in suffix


# ── plot_baselines — happy paths ──────────────────────────────────────────


def test_plot_baselines_returns_figure_for_single_record(tmp_path: Path) -> None:
    rec = BaselineRecord.from_path(_write_baseline(tmp_path, "single"))
    fig = plot_baselines([rec], metrics=("recall@1", "recall@10"))
    try:
        assert fig is not None
        ax = fig.axes[0]
        # Two metrics → two X-axis tick labels.
        tick_labels = [t.get_text() for t in ax.get_xticklabels()]
        assert tick_labels == ["recall@1", "recall@10"]
    finally:
        plt.close(fig)


def test_plot_baselines_accepts_paths_directly(tmp_path: Path) -> None:
    path = _write_baseline(tmp_path, "from_path")
    fig = plot_baselines([path])
    try:
        # Default metrics should produce 4 ticks.
        ax = fig.axes[0]
        assert len(ax.get_xticks()) == 4
    finally:
        plt.close(fig)


def test_plot_baselines_groups_two_systems_side_by_side(tmp_path: Path) -> None:
    bm25 = BaselineRecord.from_path(
        _write_baseline(tmp_path, "bm25", config="baseline"),
    )
    dense = BaselineRecord.from_path(
        _write_baseline(
            tmp_path, "dense",
            config="dense_v1",
            metrics={
                "recall@1": {"mean": 0.30, "ci_low": 0.22, "ci_high": 0.38},
                "recall@5": {"mean": 0.42, "ci_low": 0.33, "ci_high": 0.51},
                "recall@10": {"mean": 0.48, "ci_low": 0.39, "ci_high": 0.57},
                "mrr": {"mean": 0.35, "ci_low": 0.27, "ci_high": 0.43},
            },
        ),
    )
    fig = plot_baselines([bm25, dense], metrics=("recall@10",))
    try:
        ax = fig.axes[0]
        # Two systems × 1 metric = 2 bar containers, one bar each.
        bars = _bar_containers(ax)
        assert len(bars) == 2
        for container in bars:
            assert len(container) == 1
        # Legend has two entries.
        legend = ax.get_legend()
        labels = [t.get_text() for t in legend.get_texts()]
        assert len(labels) == 2
        # Each legend label carries the system / config base + suffix.
        assert any("pydocs-mcp / baseline (" in lbl for lbl in labels)
        assert any("pydocs-mcp / dense_v1 (" in lbl for lbl in labels)
    finally:
        plt.close(fig)


def test_plot_baselines_saves_to_output_creates_parent(tmp_path: Path) -> None:
    rec = BaselineRecord.from_path(_write_baseline(tmp_path, "saved"))
    output = tmp_path / "nested" / "subdir" / "out.png"
    fig = plot_baselines([rec], output=output)
    try:
        assert output.exists()
        assert output.stat().st_size > 100  # non-empty PNG
    finally:
        plt.close(fig)


def test_plot_baselines_skips_missing_metric_for_subset_baseline(
    tmp_path: Path,
) -> None:
    """When one baseline lacks a metric the others report, the plot still
    renders the other baselines' bars for that metric (silent skip)."""
    full = BaselineRecord.from_path(_write_baseline(tmp_path, "full"))
    partial_payload = _baseline_payload(config="partial_v1")
    # Strip recall@10 from the partial baseline.
    del partial_payload["metrics"]["recall@10"]  # type: ignore[arg-type]
    partial_path = tmp_path / "partial.json"
    partial_path.write_text(json.dumps(partial_payload))
    partial = BaselineRecord.from_path(partial_path)

    fig = plot_baselines([full, partial], metrics=("recall@1", "recall@10"))
    try:
        ax = fig.axes[0]
        # 2 systems × 2 metrics → 2 BarContainers. The partial baseline's
        # missing recall@10 slot renders as no bar but the system still
        # contributes its own container.
        assert len(_bar_containers(ax)) == 2
    finally:
        plt.close(fig)


# ── plot_baselines — error paths ──────────────────────────────────────────


def test_plot_baselines_empty_baselines_raises() -> None:
    with pytest.raises(ValueError, match="at least one baseline"):
        plot_baselines([])


def test_plot_baselines_empty_metrics_raises(tmp_path: Path) -> None:
    rec = BaselineRecord.from_path(_write_baseline(tmp_path, "x"))
    with pytest.raises(ValueError, match="at least one metric"):
        plot_baselines([rec], metrics=())


def test_plot_baselines_rejects_mixed_datasets(tmp_path: Path) -> None:
    """Apples-to-apples: a single plot must compare baselines from the
    same dataset slice. Mixing a 5-needle fixture with a 100-needle
    real sweep silently misleads — raise a clear error instead."""
    real = BaselineRecord.from_path(_write_baseline(tmp_path, "real"))
    fixture = BaselineRecord.from_path(
        _write_baseline(
            tmp_path, "fixture",
            dataset="repoqa-fixture-python",
            tasks_ran=5,
        ),
    )
    with pytest.raises(ValueError, match="same dataset"):
        plot_baselines([real, fixture])


def test_plot_baselines_accepts_two_baselines_on_same_dataset(
    tmp_path: Path,
) -> None:
    """Different configs on the SAME dataset is the supported case
    (e.g., BM25 vs dense embeddings, both on repoqa-2024-06-23-python)."""
    bm25 = BaselineRecord.from_path(
        _write_baseline(tmp_path, "bm25", config="baseline"),
    )
    dense = BaselineRecord.from_path(
        _write_baseline(tmp_path, "dense", config="dense_v1"),
    )
    assert bm25.dataset == dense.dataset
    fig = plot_baselines([bm25, dense])
    try:
        assert len(_bar_containers(fig.axes[0])) == 2
    finally:
        plt.close(fig)


# ── CLI smoke test ────────────────────────────────────────────────────────


def test_cli_main_writes_output(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from benchmarks.eval.plotting import _cli_main

    path = _write_baseline(tmp_path, "cli")
    output = tmp_path / "cli.png"
    rc = _cli_main([
        str(path),
        "--output", str(output),
        "--metrics", "recall@1,recall@10",
    ])
    assert rc == 0
    assert output.exists()
    captured = capsys.readouterr()
    assert "Saved" in captured.out
    assert str(output) in captured.out


# ── plot_timings ──────────────────────────────────────────────────────────


def test_plot_timings_returns_figure_for_single_record(tmp_path: Path) -> None:
    rec = BaselineRecord.from_path(_write_baseline(tmp_path, "single_timings"))
    fig = plot_timings([rec])
    try:
        assert fig is not None
        # Default = 2 timing metrics → 2 stacked subplots.
        assert len(fig.axes) == 2
    finally:
        plt.close(fig)


def test_plot_timings_renders_one_panel_per_metric(tmp_path: Path) -> None:
    rec = BaselineRecord.from_path(_write_baseline(tmp_path, "one_metric"))
    fig = plot_timings([rec], metrics=("indexing_seconds",))
    try:
        assert len(fig.axes) == 1
    finally:
        plt.close(fig)


def test_plot_timings_groups_two_systems_same_dataset(tmp_path: Path) -> None:
    bm25 = BaselineRecord.from_path(
        _write_baseline(tmp_path, "bm25_t", config="baseline"),
    )
    dense = BaselineRecord.from_path(
        _write_baseline(tmp_path, "dense_t", config="dense_v1"),
    )
    fig = plot_timings([bm25, dense])
    try:
        # 2 metrics × 1 BarContainer per axis = 2 bar containers total.
        bars_per_axis = [len(_bar_containers(ax)) for ax in fig.axes]
        # One BarContainer per axis carrying both bars.
        assert all(n == 1 for n in bars_per_axis)
        # Each container holds two bars (one per system).
        for ax in fig.axes:
            (container,) = _bar_containers(ax)
            assert len(container) == 2
    finally:
        plt.close(fig)


def test_plot_timings_saves_to_output(tmp_path: Path) -> None:
    rec = BaselineRecord.from_path(_write_baseline(tmp_path, "save_t"))
    output = tmp_path / "nested" / "timings.png"
    fig = plot_timings([rec], output=output)
    try:
        assert output.exists()
        assert output.stat().st_size > 100
    finally:
        plt.close(fig)


def test_plot_timings_rejects_mixed_datasets(tmp_path: Path) -> None:
    real = BaselineRecord.from_path(_write_baseline(tmp_path, "real_t"))
    fixture = BaselineRecord.from_path(
        _write_baseline(
            tmp_path, "fixture_t",
            dataset="repoqa-fixture-python",
            tasks_ran=5,
        ),
    )
    with pytest.raises(ValueError, match="same dataset"):
        plot_timings([real, fixture])


def test_plot_timings_empty_baselines_raises() -> None:
    with pytest.raises(ValueError, match="at least one baseline"):
        plot_timings([])


def test_plot_timings_empty_metrics_raises(tmp_path: Path) -> None:
    rec = BaselineRecord.from_path(_write_baseline(tmp_path, "x_t"))
    with pytest.raises(ValueError, match="at least one metric"):
        plot_timings([rec], metrics=())


def test_format_seconds_picks_unit_by_magnitude() -> None:
    assert _format_seconds(0.0) == "0s"
    assert _format_seconds(123e-6) == "123µs"
    assert _format_seconds(0.05) == "50.0ms"
    assert _format_seconds(7.45) == "7.45s"


def test_cli_main_timings_mode_writes_output(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    from benchmarks.eval.plotting import _cli_main

    path = _write_baseline(tmp_path, "cli_t")
    output = tmp_path / "cli_t.png"
    rc = _cli_main([
        str(path),
        "--output", str(output),
        "--timings",
    ])
    assert rc == 0
    assert output.exists()
    assert "Saved" in capsys.readouterr().out


# ── plot_metric_vs_latency ────────────────────────────────────────────────


def test_scatter_returns_figure_for_single_point(tmp_path: Path) -> None:
    rec = BaselineRecord.from_path(_write_baseline(tmp_path, "single_scatter"))
    fig = plot_metric_vs_latency([rec])
    try:
        assert fig is not None
        ax = fig.axes[0]
        # X-axis label mentions latency + percentile.
        assert "p50" in ax.get_xlabel()
        assert "ms" in ax.get_xlabel()
        # Y-axis label is the default metric.
        assert "recall@10" in ax.get_ylabel()
    finally:
        plt.close(fig)


def test_scatter_two_points_same_dataset(tmp_path: Path) -> None:
    bm25 = BaselineRecord.from_path(
        _write_baseline(tmp_path, "bm25_s", config="baseline"),
    )
    dense = BaselineRecord.from_path(
        _write_baseline(tmp_path, "dense_s", config="dense_v1"),
    )
    fig = plot_metric_vs_latency([bm25, dense])
    try:
        ax = fig.axes[0]
        # Two ErrorbarContainers (one per baseline), each carrying an
        # errorbar line collection.
        from matplotlib.container import ErrorbarContainer

        eb = [c for c in ax.containers if isinstance(c, ErrorbarContainer)]
        assert len(eb) == 2
    finally:
        plt.close(fig)


def test_scatter_rejects_mixed_datasets(tmp_path: Path) -> None:
    real = BaselineRecord.from_path(_write_baseline(tmp_path, "real_s"))
    fixture = BaselineRecord.from_path(
        _write_baseline(
            tmp_path, "fixture_s",
            dataset="repoqa-fixture-python",
            tasks_ran=5,
        ),
    )
    with pytest.raises(ValueError, match="same dataset"):
        plot_metric_vs_latency([real, fixture])


def test_scatter_saves_to_output(tmp_path: Path) -> None:
    rec = BaselineRecord.from_path(_write_baseline(tmp_path, "save_s"))
    output = tmp_path / "nested" / "scatter.png"
    fig = plot_metric_vs_latency([rec], output=output)
    try:
        assert output.exists()
        assert output.stat().st_size > 100
    finally:
        plt.close(fig)


def test_scatter_respects_custom_metric_and_latency(tmp_path: Path) -> None:
    rec = BaselineRecord.from_path(_write_baseline(tmp_path, "custom_s"))
    fig = plot_metric_vs_latency(
        [rec],
        metric="recall@5",
        latency_metric="indexing_seconds",
        latency_percentile="p95",
    )
    try:
        ax = fig.axes[0]
        assert "recall@5" in ax.get_ylabel()
        assert "p95" in ax.get_xlabel()
    finally:
        plt.close(fig)


def test_scatter_raises_when_no_baseline_has_metric(tmp_path: Path) -> None:
    payload = _baseline_payload()
    # Strip the requested metric to force the no-data path.
    del payload["metrics"]["recall@10"]  # type: ignore[arg-type]
    path = tmp_path / "no_metric.json"
    path.write_text(json.dumps(payload))
    rec = BaselineRecord.from_path(path)
    with pytest.raises(ValueError, match="no baseline had both"):
        plot_metric_vs_latency([rec], metric="recall@10")


def test_cli_main_scatter_mode_writes_output(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    from benchmarks.eval.plotting import _cli_main

    path = _write_baseline(tmp_path, "cli_s")
    output = tmp_path / "cli_s.png"
    rc = _cli_main([
        str(path),
        "--output", str(output),
        "--scatter",
        "--scatter-metric", "recall@5",
    ])
    assert rc == 0
    assert output.exists()
    assert "Saved" in capsys.readouterr().out


def test_cli_main_timings_and_scatter_mutually_exclusive(tmp_path: Path) -> None:
    from benchmarks.eval.plotting import _cli_main

    path = _write_baseline(tmp_path, "cli_excl")
    with pytest.raises(SystemExit):
        _cli_main([
            str(path),
            "--output", str(tmp_path / "out.png"),
            "--timings", "--scatter",
        ])
