"""Typed run-config loading + registry-key validation (plan Task 11, spec §D7).

Both shipped YAMLs must load into the typed ``OptimizeRunConfig`` with the
spec's canonical shape (artifact, ladder rungs, accept margin, judge-parity
floor), and an unknown registry key in the YAML must fail loud at load time —
byte-identical names are the §D7 contract, so a typo like ``gradient_descent``
is a config error, not a silent no-op. No subprocess, no network, no live LLM.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import pytest

from benchmarks.optimize.run_config import load_run_config


def _shipped(name: str) -> Path:
    """Resolve a shipped ``optimize/configs/<name>`` YAML to a real filesystem path."""
    return Path(str(files("benchmarks.optimize.configs").joinpath(name)))


def test_both_shipped_configs_load_typed() -> None:
    for name in ("optimize_tool_docs.yaml", "optimize_usage_skill.yaml"):
        cfg = load_run_config(_shipped(name))
        assert cfg.artifact in ("tool_docs", "usage_skill")
        assert cfg.ladder.rungs[0].fitness_name == "paired_agent"
        assert cfg.accept_margin == pytest.approx(0.02)
        assert cfg.fitness.judge_parity_floor == pytest.approx(-0.25)


def test_unknown_registry_key_is_a_clear_error(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        _shipped("optimize_tool_docs.yaml")
        .read_text()
        .replace("critique_refine", "gradient_descent")
    )
    with pytest.raises(KeyError, match="gradient_descent"):
        load_run_config(bad)
