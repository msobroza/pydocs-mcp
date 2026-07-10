"""The ``skillopt`` adapter tests (plan Task 10, spec §D4/§D8).

Every test is subprocess-free and network-free (slice-6 contract): the real
``skillopt`` library is NEVER imported. ``generate_env_plugin`` is exercised as
a pure file-writer (the generated env-adapter module is ``compile()``d, never
imported — importing it would pull the real ``skillopt``); ``_invoke_train``
(the ONE subprocess wrapper in the layer) is monkeypatched to a fake that just
writes ``best_skill.md``; the missing-module path stubs ``skillopt`` as ``None``
in ``sys.modules`` so ``find_spec`` reports it absent. The consumed-surface
tuple is pinned so a SkillOpt version bump that moves any consumed symbol/CLI
is caught here first (the version-pin canary).
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
    """The mapped SkillOpt config: our budget → SkillOpt's own rollout-count fields."""
    return SkillOptConfig(max_trials=max_trials, max_usd=max_usd)


def _usage_skill_seed() -> UsageSkillArtifact:
    return UsageSkillArtifact()


def _ladder() -> FitnessLadder:
    return FitnessLadder(rungs=(Rung("paired_agent", max_tasks=6, survivors=1),))


def test_env_plugin_layout_generated(tmp_path) -> None:
    plugin = generate_env_plugin(tmp_path, tasks=_train_tasks(4), config=_skillopt_cfg())
    files = ("pydocs_env_plugin.py", "run.py", "seed_skill.md", "configs/pydocs_usage_skill.yaml")
    for rel in files:
        assert (plugin / rel).is_file()


def test_budget_mapping_asserted(tmp_path) -> None:
    # OptimizationBudget(max_trials=20) must land in SkillOpt's rollout-count fields —
    # skillopt 0.2.x has NO spend key, so rollout counts are the only native bound and
    # our --max-usd does NOT reach inside skillopt-train (spec §D4 spend asymmetry).
    cfg_text = (
        generate_env_plugin(
            tmp_path, tasks=_train_tasks(2), config=_skillopt_cfg(max_trials=20, max_usd=40.0)
        )
        / "configs/pydocs_usage_skill.yaml"
    ).read_text()
    cfg = yaml.safe_load(cfg_text)
    epochs = cfg["train"]["num_epochs"]
    batch = cfg["train"]["batch_size"]
    sel = cfg["evaluation"]["sel_env_num"]
    # Total item-rollouts (eval_test off, accumulation 1) stay within max_trials.
    assert cfg["evaluation"]["eval_test"] is False and cfg["train"]["accumulation"] == 1
    assert sel + epochs * (batch + sel) <= 20
    assert (epochs, batch, sel) == (4, 2, 2)  # the pinned deterministic mapping for N=2
    # max_usd has no native sink: recorded as an explanatory comment, never a config key.
    assert "max_usd=40.0" in cfg_text and "NOT enforced" in cfg_text
    assert "max_usd" not in _flat_keys(cfg)


def _flat_keys(tree: object) -> set[str]:
    """Every mapping key in a nested YAML tree (comments are not keys)."""
    if not isinstance(tree, dict):
        return set()
    keys = set(tree)
    for value in tree.values():
        keys |= _flat_keys(value)
    return keys


def test_consumed_surface_is_enumerated_and_stable() -> None:
    # The version-pin canary: everything we assume about SkillOpt 0.2.x in ONE tuple.
    assert _CONSUMED_SKILLOPT_SURFACE == (
        "scripts.train:main (the skillopt-train console entry) --config <yaml>",
        "scripts.train._ENV_REGISTRY[<env name>] = <EnvAdapter subclass> (run.py injection)",
        "skillopt.envs.base.EnvAdapter: build_train_env / build_eval_env /"
        " rollout -> [{id, hard, soft}] / get_task_types",
        "config YAML sections: model / train / gradient / optimizer / evaluation / env"
        " (no spend key exists)",
        "output: <out_root>/best_skill.md",
    )


def test_generated_env_adapter_module_is_standalone(tmp_path) -> None:
    # The plugin module must be valid standalone Python for the skillopt venv:
    # compiled here (never imported — that would pull the real skillopt) and
    # carrying the tasks inline, the EnvAdapter subclass, and the four reflect
    # attributes SkillOpt's default reflect() reads but never sets.
    plugin = generate_env_plugin(tmp_path, tasks=_train_tasks(3), config=_skillopt_cfg())
    source = (plugin / "pydocs_env_plugin.py").read_text()
    compile(source, "pydocs_env_plugin.py", "exec")
    assert "from skillopt.envs.base import EnvAdapter" in source
    assert "class PydocsEnvAdapter(EnvAdapter):" in source
    for attr in ("analyst_workers", "failure_only", "minibatch_size", "edit_budget"):
        assert f"self.{attr} = {attr}" in source
    assert "question 2" in source  # the train rows travel inside the module


def test_generated_run_py_injects_registry_and_defers(tmp_path) -> None:
    # run.py is the 0.2.x invocation seam: pre-inject the adapter into
    # scripts.train._ENV_REGISTRY, then hand argv to scripts.train.main().
    plugin = generate_env_plugin(tmp_path, tasks=_train_tasks(1), config=_skillopt_cfg())
    source = (plugin / "run.py").read_text()
    compile(source, "run.py", "exec")
    assert "import scripts.train as _train" in source
    assert "_train._ENV_REGISTRY['pydocs_usage_skill'] = PydocsEnvAdapter" in source
    assert "_train.main()" in source
    assert "skillopt.train" not in source  # the dead pre-0.2 `-m skillopt.train` CLI


def test_seed_skill_written_and_wired_as_skill_init(tmp_path) -> None:
    plugin = generate_env_plugin(
        tmp_path, tasks=_train_tasks(1), config=_skillopt_cfg(), seed_content=_VALID_SKILL_TEXT
    )
    assert (plugin / "seed_skill.md").read_text() == _VALID_SKILL_TEXT
    cfg = yaml.safe_load((plugin / "configs/pydocs_usage_skill.yaml").read_text())
    assert cfg["env"]["skill_init"] == "./seed_skill.md"
    assert cfg["env"]["out_root"] == "."  # pins best_skill.md to the subprocess cwd


async def test_best_skill_parsed_and_validated(monkeypatch, tmp_path) -> None:
    async def fake_invoke(cmd, run_dir):  # mirrors the real _invoke_train hook it replaces
        (run_dir / "best_skill.md").write_text(_VALID_SKILL_TEXT)
        return 0

    monkeypatch.setattr("pydocs_eval.optimize.optimizers.skillopt._invoke_train", fake_invoke)
    opt = SkillOptOptimizer(python=Path("/venv/bin/python"))
    result = await opt.optimize(_usage_skill_seed(), _ladder(), OptimizationBudget(max_trials=2))
    assert result.best is not None and result.best.validate() == ()


async def test_invoke_seam_runs_generated_run_py(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_invoke(cmd, run_dir):
        captured["cmd"] = tuple(cmd)
        captured["run_dir"] = run_dir
        (run_dir / "best_skill.md").write_text(_VALID_SKILL_TEXT)
        return 0

    monkeypatch.setattr("pydocs_eval.optimize.optimizers.skillopt._invoke_train", fake_invoke)
    opt = SkillOptOptimizer(python=Path("/venv/bin/python"), tasks=_train_tasks(2))
    await opt.optimize(_usage_skill_seed(), _ladder(), OptimizationBudget(max_trials=4))
    run_dir = captured["run_dir"]
    assert isinstance(run_dir, Path)
    assert captured["cmd"] == (
        "/venv/bin/python",
        str(run_dir / "run.py"),
        "--config",
        str(run_dir / "configs" / "pydocs_usage_skill.yaml"),
    )
    # The plugin is fully materialized before the subprocess would start.
    assert (run_dir / "pydocs_env_plugin.py").is_file() and (run_dir / "run.py").is_file()


async def test_missing_skillopt_module_raises_actionable(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "skillopt", None)
    with pytest.raises(RuntimeError, match=r"pydocs-mcp-eval\[optimizers-skillopt\]"):
        SkillOptOptimizer(python=Path("/venv/bin/python")).ensure_available()


def test_registered_as_skillopt() -> None:
    built = optimizer_registry.build("skillopt", python=Path("/venv/bin/python"))
    assert isinstance(built, SkillOptOptimizer)
