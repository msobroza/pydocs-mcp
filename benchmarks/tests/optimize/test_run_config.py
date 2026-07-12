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

from pydocs_eval.optimize.run_config import load_run_config


def _shipped(name: str) -> Path:
    """Resolve a shipped ``optimize/configs/<name>`` YAML to a real filesystem path."""
    return Path(str(files("pydocs_eval.optimize.configs").joinpath(name)))


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


_ASK_RUBRIC_YAML = """
artifact: tool_docs
optimizer: critique_refine
ladder:
  - [paired_agent, 12, 1]
ask_rubric:
  runner:
    model: claude-sonnet-5
    architecture: text_react
  gates:
    - {name: non_empty, kind: min_answer_chars, params: {n: 40}}
  criteria:
    - {name: correctness, weight: 0.6, description: "Factually correct."}
    - {name: grounding, weight: 0.4, description: "Traceable."}
  fail_fast: true
  gate_weight: 0.3
  rubric_weight: 0.7
budget:
  max_trials: 5
  max_judge_calls: 50
rng_seed: 7
"""


def _write(tmp_path, text: str) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_ask_rubric_section_loads_typed(tmp_path) -> None:
    cfg = load_run_config(_write(tmp_path, _ASK_RUBRIC_YAML))
    assert cfg.ask_rubric is not None
    assert cfg.ask_rubric.runner.architecture == "text_react"
    assert cfg.ask_rubric.rubric_config.criteria[0].name == "correctness"
    assert cfg.budget.max_judge_calls == 50
    assert cfg.rng_seed == 7


def test_rng_seed_and_judge_calls_default(tmp_path) -> None:
    cfg = load_run_config(
        _write(
            tmp_path,
            "artifact: tool_docs\noptimizer: critique_refine\nladder: [[paired_agent, 12, 1]]\n",
        )
    )
    assert cfg.rng_seed == 0
    assert cfg.budget.max_judge_calls == 200
    assert cfg.ask_rubric is None


def test_bad_criterion_weights_raise_at_load(tmp_path) -> None:
    # AC-8: weights summing to 1.02 are a config error at load, never trial 14.
    bad = _ASK_RUBRIC_YAML.replace("weight: 0.4", "weight: 0.42")
    with pytest.raises(ValueError, match="criterion weights"):
        load_run_config(_write(tmp_path, bad))


def test_unregistered_gate_kind_raises_at_load(tmp_path) -> None:
    # AC-7: a gate-kind typo names the registered kinds at load time.
    bad = _ASK_RUBRIC_YAML.replace("kind: min_answer_chars", "kind: min_chars")
    with pytest.raises(KeyError, match="min_answer_chars"):
        load_run_config(_write(tmp_path, bad))
