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
    plot_baselines,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


def _baseline_payload(**overrides: object) -> dict[str, object]:
    """Default baseline-JSON shape matching benchmarks/baselines/*.json."""
    base: dict[str, object] = {
        "dataset": "repoqa-2024-06-23-python",
        "system": "pydocs-mcp",
        "config": "baseline",
        "label": "real-100-needles",
        "tasks_ran": 100,
        "metrics": {
            "recall@1": {"mean": 0.14, "ci_low": 0.07, "ci_high": 0.21},
            "recall@5": {"mean": 0.17, "ci_low": 0.10, "ci_high": 0.24},
            "recall@10": {"mean": 0.18, "ci_low": 0.11, "ci_high": 0.26},
            "mrr": {"mean": 0.152, "ci_low": 0.088, "ci_high": 0.218},
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
