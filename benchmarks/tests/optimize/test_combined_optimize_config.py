"""Combined-corpus optimize config + the per-prefix reporting seam (design
§6.4). At ≤33 ccv vs ~260 swe-qa-pro tasks the ccv slice is ~8-11% of a
blended score and can silently regress — metrics must be groupable by the
task_id prefix (``task_id.split("/", 1)[0]``). Hermetic: config load + pure
helpers + captured CLI echo; no network, no paid calls."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from pydocs_eval.datasets.combined import CombinedDataset
from pydocs_eval.optimize._prefix_report import (
    count_by_task_prefix,
    mean_score_by_task_prefix,
    task_id_prefix,
)
from pydocs_eval.optimize.run_config import load_run_config
from pydocs_eval.registries import dataset_registry


def _shipped(name: str) -> Path:
    """Resolve a shipped ``optimize/configs/<name>`` YAML to a filesystem path."""
    return Path(str(files("pydocs_eval.optimize.configs").joinpath(name)))


def test_combined_config_resolves_and_builds_the_combined_dataset() -> None:
    cfg = load_run_config(_shipped("optimize_ask_prompt_combined.yaml"))
    # The "+" is a valid YAML plain scalar — it survives parsing unquoted.
    assert cfg.dataset.name == "swe-qa-pro+crosscommitvuln"
    assert cfg.dataset.fixture_path is None
    ds = dataset_registry.build(cfg.dataset.name, fixture_path=cfg.dataset.fixture_path)
    assert isinstance(ds, CombinedDataset)


def test_combined_config_gates_include_exactness_and_grounding() -> None:
    # load_run_config already ran validate_rubric_config against
    # gate_registry.names() — a successful load PROVES gold_substring_all is
    # a registered gate kind (the AC-7 KeyError fires otherwise).
    cfg = load_run_config(_shipped("optimize_ask_prompt_combined.yaml"))
    assert cfg.ask_rubric is not None
    kinds = [g.kind for g in cfg.ask_rubric.gates]
    assert "gold_substring_all" in kinds
    assert "used_indexed_tools" in kinds
    exact = next(g for g in cfg.ask_rubric.gates if g.kind == "gold_substring_all")
    assert exact.name == "exact_id"
    assert exact.params == {}


def test_task_id_prefix_is_the_leading_slash_component() -> None:
    assert task_id_prefix("ccv/cve-2099-0001") == "ccv"
    assert task_id_prefix("sweqapro/swe_qa_pro/0001") == "sweqapro"
    # Un-prefixed ids group under themselves (single-dataset runs degrade).
    assert task_id_prefix("swe-qa-pro:0001") == "swe-qa-pro:0001"


def test_count_by_task_prefix_groups_counts() -> None:
    counts = count_by_task_prefix(["sweqapro/a", "sweqapro/b", "ccv/x"])
    assert counts == {"sweqapro": 2, "ccv": 1}


def test_mean_score_by_task_prefix_reports_each_dataset_separately() -> None:
    means = mean_score_by_task_prefix({"sweqapro/a": 1.0, "sweqapro/b": 0.0, "ccv/x": 0.25})
    assert means == {"sweqapro": 0.5, "ccv": 0.25}


def test_split_echo_reports_per_prefix_counts(capsys) -> None:
    from pydocs_eval.optimize.__main__ import _print_per_prefix_split

    _print_per_prefix_split(
        train=("sweqapro/a", "sweqapro/b", "ccv/x"), holdout=("sweqapro/c", "ccv/y")
    )
    out = capsys.readouterr().out
    assert "sweqapro" in out and "ccv" in out
    assert "'sweqapro': 2" in out  # train-side count is broken down per prefix


def test_split_echo_is_silent_for_a_single_prefix(capsys) -> None:
    from pydocs_eval.optimize.__main__ import _print_per_prefix_split

    _print_per_prefix_split(train=("swe-qa-pro:0001",), holdout=("swe-qa-pro:0002",))
    assert capsys.readouterr().out == ""
