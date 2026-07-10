"""The ``skillopt`` adapter tests (plan Task 10, spec §D4/§D8).

Every test is subprocess-free and network-free (slice-6 contract): the real
``skillopt`` library is NEVER imported. ``generate_env_plugin`` is exercised as a
pure file-writer; ``_invoke_train`` (the ONE subprocess wrapper in the layer) is
monkeypatched to a fake that just writes ``best_skill.md``; the missing-module
path stubs ``skillopt`` as ``None`` in ``sys.modules`` so ``find_spec`` reports it
absent. The consumed-surface tuple is pinned so a SkillOpt version bump that
moves any consumed symbol/CLI is caught here first (the version-pin canary).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from pydocs_eval.optimize._types import OptimizationBudget
from pydocs_eval.optimize.artifacts.usage_skill import UsageSkillArtifact
from pydocs_eval.optimize.ladder import FitnessLadder, Rung
from pydocs_eval.optimize.optimizers.skillopt import (
    _CONSUMED_SKILLOPT_SURFACE,
    SkillOptConfig,
    SkillOptOptimizer,
    generate_env_plugin,
)
from pydocs_eval.optimize.registries import optimizer_registry

# The committed seed already satisfies the §D6 firewall, so it doubles as the
# "valid best" a fake SkillOpt run emits — a parsed best that ``validate()``s clean.
_VALID_SKILL_TEXT = UsageSkillArtifact().render()


def _train_tasks(n: int) -> tuple[tuple[str, str, str], ...]:
    """``n`` synthetic train-split rows: ``(task_id, question, gold)``."""
    return tuple((f"swe-qa-pro:{i:04d}", f"question {i}", f"gold {i}") for i in range(n))


def _skillopt_cfg(*, max_trials: int = 2, max_usd: float = 40.0) -> SkillOptConfig:
    """The mapped SkillOpt config: our budget → SkillOpt's own rollout/budget fields."""
    return SkillOptConfig(max_trials=max_trials, max_usd=max_usd)


def _usage_skill_seed() -> UsageSkillArtifact:
    return UsageSkillArtifact()


def _ladder() -> FitnessLadder:
    return FitnessLadder(rungs=(Rung("paired_agent", max_tasks=6, survivors=1),))


def test_env_plugin_layout_generated(tmp_path) -> None:
    plugin = generate_env_plugin(tmp_path, tasks=_train_tasks(4), config=_skillopt_cfg())
    for rel in ("dataloader.py", "rollout.py", "evaluator.py", "configs/pydocs_usage_skill.yaml"):
        assert (plugin / rel).is_file()


def test_budget_mapping_asserted(tmp_path) -> None:
    # OptimizationBudget(max_trials=20, max_usd=40.0) must land in SkillOpt's own config fields —
    # our --max-usd does NOT bound SkillOpt's internal rollouts (spec §D4 spend asymmetry).
    cfg_text = (
        generate_env_plugin(
            tmp_path, tasks=_train_tasks(2), config=_skillopt_cfg(max_trials=20, max_usd=40.0)
        )
        / "configs/pydocs_usage_skill.yaml"
    ).read_text()
    cfg = yaml.safe_load(cfg_text)
    assert cfg["budget"]["max_usd"] == 40.0 and cfg["rollouts"]["total"] == 20


def test_consumed_surface_is_enumerated_and_stable() -> None:
    # The version-pin canary: everything we assume about SkillOpt in ONE tuple.
    assert _CONSUMED_SKILLOPT_SURFACE == (
        "python -m skillopt.train --config <yaml>",
        "env-plugin: dataloader.py / rollout.py / evaluator.py / configs/<name>.yaml",
        "output: <run_dir>/best_skill.md",
    )


async def test_best_skill_parsed_and_validated(monkeypatch, tmp_path) -> None:
    async def fake_invoke(cmd, run_dir):  # mirrors the real _invoke_train hook it replaces
        (run_dir / "best_skill.md").write_text(_VALID_SKILL_TEXT)
        return 0

    monkeypatch.setattr("pydocs_eval.optimize.optimizers.skillopt._invoke_train", fake_invoke)
    opt = SkillOptOptimizer(python=Path("/venv/bin/python"))
    result = await opt.optimize(_usage_skill_seed(), _ladder(), OptimizationBudget(max_trials=2))
    assert result.best is not None and result.best.validate() == ()


async def test_missing_skillopt_module_raises_actionable(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "skillopt", None)
    with pytest.raises(RuntimeError, match=r"git\+https://github\.com/microsoft/SkillOpt@"):
        SkillOptOptimizer(python=Path("/venv/bin/python")).ensure_available()


def test_registered_as_skillopt() -> None:
    built = optimizer_registry.build("skillopt", python=Path("/venv/bin/python"))
    assert isinstance(built, SkillOptOptimizer)
