"""The ``skillopt`` optimizer — an adapter to microsoft/SkillOpt (spec §D4).

SkillOpt is a research repo (MIT; ``github.com/microsoft/SkillOpt``) whose
custom-benchmark contract is an **env-plugin package** — ``dataloader.py`` +
``rollout.py`` + ``evaluator.py`` + a ``configs/<name>.yaml`` — driven by its
``python -m skillopt.train`` CLI. This adapter generates that plugin at run time,
invokes ``train.py`` as a subprocess (the ONLY subprocess in the optimize layer,
and this module is the ONLY place that knows SkillOpt), parses the emitted
``best_skill.md``, converts it back through the seed's ``with_content`` /
``validate()`` firewall, and hands the proposal to the SAME D4 holdout gate as
any other optimizer. A candidate that fails ``validate()`` is recorded and the
result carries ``best=None`` — the firewall is never bypassed.

**Spend asymmetry (spec §D4/§D5), documented not hidden.** SkillOpt runs its own
rollouts on its own harness, so the orchestrator's outer ``--max-usd`` CANNOT
interrupt ``train.py`` mid-run. Instead this adapter maps
``OptimizationBudget.max_trials`` → SkillOpt's rollout count and
``OptimizationBudget.max_usd`` → SkillOpt's own budget field (``SkillOptConfig``
→ the generated YAML). The outer cap still bounds the D4 holdout-gate runs, which
DO go through our harness — but the SkillOpt search itself is bounded only by the
mapped config, which is why ``test_budget_mapping_asserted`` pins the mapping.

Offline-test contract (slice-6): the real ``skillopt`` library is NEVER imported
by the test suite. ``generate_env_plugin`` is a pure file-writer;
``ensure_available`` only asks ``importlib.util.find_spec``; the subprocess lives
behind the module-level ``_invoke_train`` so tests monkeypatch it and spend
nothing. ``_CONSUMED_SKILLOPT_SURFACE`` is the single tuple enumerating every
assumption this adapter makes about SkillOpt — the version-pin canary: a SkillOpt
bump that moves any consumed symbol/CLI trips this constant's test first.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from pydocs_eval.optimize._types import (
    OptimizationBudget,
    OptimizationResult,
    Provenance,
    Trial,
)
from pydocs_eval.optimize.ladder import FitnessLadder
from pydocs_eval.optimize.protocols import OptimizableArtifact
from pydocs_eval.optimize.registries import optimizer_registry

# WHY: the ONE place every assumption this adapter makes about SkillOpt lives.
# A version bump that renames the CLI, moves a plugin file, or changes the output
# path breaks a line here and ``test_consumed_surface_is_enumerated_and_stable``
# fails FIRST — before any real run silently produces garbage (spec §D8 canary).
_CONSUMED_SKILLOPT_SURFACE = (
    "python -m skillopt.train --config <yaml>",
    "env-plugin: dataloader.py / rollout.py / evaluator.py / configs/<name>.yaml",
    "output: <run_dir>/best_skill.md",
)

# The importable module SkillOpt installs; the extra that provides it. Both named
# in ``ensure_available``'s actionable error so a human knows the one fix.
_SKILLOPT_MODULE = "skillopt"
_SKILLOPT_EXTRA = "optimizers-skillopt"

# The generated env-plugin's fixed config basename (spec §D4). The usage_skill
# artifact is the v1 SkillOpt target; the name is stable so ``optimize`` and the
# plugin writer agree without threading it around.
_DEFAULT_CONFIG_NAME = "pydocs_usage_skill"

# The optimizer name recorded in synthesized provenance when handed a bare seed.
_OPTIMIZER_NAME = "skillopt"

# SkillOpt writes its winning skill here inside the run dir (consumed surface).
_BEST_SKILL_FILE = "best_skill.md"


@dataclass(frozen=True, slots=True)
class SkillOptConfig:
    """The budget mapping onto SkillOpt's OWN config fields (spec §D4).

    ``max_trials`` maps to SkillOpt's rollout count and ``max_usd`` to its budget
    field. Our outer ``--max-usd`` cannot interrupt ``train.py`` mid-run — this
    mapping is the only lever we have over the SkillOpt search's spend, so it is
    asserted in a test (``test_budget_mapping_asserted``).
    """

    max_trials: int
    max_usd: float
    config_name: str = _DEFAULT_CONFIG_NAME

    @classmethod
    def from_budget(cls, budget: OptimizationBudget) -> SkillOptConfig:
        """Map an ``OptimizationBudget`` onto SkillOpt's own rollout/budget fields."""
        return cls(max_trials=budget.max_trials, max_usd=budget.max_usd)


def generate_env_plugin(
    root: Path,
    *,
    tasks: Sequence[tuple[str, str, str]],
    config: SkillOptConfig,
) -> Path:
    """Write a SkillOpt env-plugin package under ``root`` and return its directory.

    The package is SkillOpt's custom-benchmark contract (spec §D4): ``dataloader``
    yields our train-split ``(task_id, question, gold)`` rows (serialized into the
    plugin as JSON so SkillOpt re-imports it standalone — no closure over our
    process); ``rollout`` selects SkillOpt's built-in Claude-Code backend pointed
    at our indexed corpora; ``evaluator`` maps our rung fitness onto the reward
    hook by reading the per-rollout components JSON; ``configs/<name>.yaml`` carries
    the MAPPED budget (``rollouts.total`` = ``max_trials``, ``budget.max_usd`` =
    ``max_usd``). Pure file I/O — imports nothing from SkillOpt.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "configs").mkdir(exist_ok=True)
    (root / "dataloader.py").write_text(_dataloader_source(tasks))
    (root / "rollout.py").write_text(_ROLLOUT_SOURCE)
    (root / "evaluator.py").write_text(_EVALUATOR_SOURCE)
    (root / "configs" / f"{config.config_name}.yaml").write_text(_config_yaml(config))
    return root


def _config_yaml(config: SkillOptConfig) -> str:
    """Render the plugin's ``configs/<name>.yaml`` with the mapped budget."""
    return yaml.safe_dump(
        {
            # The asymmetry (spec §D4): SkillOpt bounds its OWN search with these
            # fields; our outer --max-usd never reaches inside train.py.
            "rollouts": {"total": config.max_trials},
            "budget": {"max_usd": config.max_usd},
            "backend": "claude_code",  # SkillOpt's built-in execution backend
            "dataloader": "dataloader:load",
            "rollout": "rollout:run",
            "evaluator": "evaluator:score",
        },
        sort_keys=True,
    )


def _dataloader_source(tasks: Sequence[tuple[str, str, str]]) -> str:
    """Emit ``dataloader.py`` with the train rows inlined as JSON (standalone import)."""
    rows = [{"task_id": t, "question": q, "gold": g} for t, q, g in tasks]
    serialized = json.dumps(rows)
    return (
        '"""SkillOpt dataloader — yields the harness train-split rows (generated)."""\n'
        "import json\n\n"
        f"_TASKS = json.loads({serialized!r})\n\n\n"
        "def load():\n"
        '    """Return the train-split (task_id, question, gold) rows."""\n'
        "    return list(_TASKS)\n"
    )


# ``rollout.py`` selects SkillOpt's built-in Claude-Code backend with cwd pointed
# at our indexed corpora; the concrete wiring is SkillOpt's, so the plugin stub
# just names the entry-point SkillOpt calls (backend selection lives in the YAML).
_ROLLOUT_SOURCE = (
    '"""SkillOpt rollout hook — uses the config-selected Claude-Code backend (generated)."""\n\n\n'
    "def run(task, skill, backend):\n"
    '    """Run one rollout for ``task`` under ``skill`` on ``backend`` (SkillOpt-driven)."""\n'
    "    return backend.run(task=task, skill=skill)\n"
)


# ``evaluator.py`` maps our rung fitness onto SkillOpt's reward hook by reading the
# per-rollout components JSON the backend emits; the reward is the harness score.
_EVALUATOR_SOURCE = (
    '"""SkillOpt evaluator hook — reward = the harness rung fitness score (generated)."""\n'
    "import json\n\n\n"
    "def score(rollout):\n"
    '    """Return the scalar reward for ``rollout`` from its components JSON."""\n'
    "    components = json.loads(rollout.components_json)\n"
    '    return float(components["score"])\n'
)


async def _invoke_train(cmd: Sequence[str], run_dir: Path) -> int:
    """Run ``python -m skillopt.train --config ...`` and return its exit code.

    The ONE subprocess in the optimize layer, isolated here at module level so
    the whole adapter is exercised offline (tests monkeypatch this). ``run_dir``
    is the subprocess cwd, where SkillOpt writes ``best_skill.md``.
    """
    import asyncio

    process = await asyncio.create_subprocess_exec(*cmd, cwd=str(run_dir))
    return await process.wait()


@optimizer_registry.register("skillopt")
@dataclass(frozen=True, slots=True)
class SkillOptOptimizer:
    """SkillOpt env-plugin adapter with the ``validate()`` firewall (spec §D4).

    ``python`` is the interpreter that runs SkillOpt's ``train`` module (the extra
    installs SkillOpt into that env). ``tasks`` are the train-split rows the
    generated ``dataloader`` serves — empty is legal (the standalone optimizer
    tests drive it that way); the orchestrator supplies real rows.
    """

    python: Path
    tasks: tuple[tuple[str, str, str], ...] = ()
    name: str = _OPTIMIZER_NAME

    def ensure_available(self) -> None:
        """Raise an actionable ``RuntimeError`` when SkillOpt is not importable.

        Preflight check (never called by ``optimize`` — the CLI/orchestrator calls
        it before a paid run). Uses ``find_spec`` so no import side effects fire;
        the error names the ``[optimizers-skillopt]`` extra that provides it.
        """
        if importlib.util.find_spec(_SKILLOPT_MODULE) is None:
            raise RuntimeError(
                f"{_SKILLOPT_MODULE!r} is not installed — install the "
                f"[{_SKILLOPT_EXTRA}] benchmarks extra to run the skillopt optimizer"
            )

    async def optimize(
        self,
        seed: object,
        ladder: FitnessLadder,
        budget: OptimizationBudget,
    ) -> OptimizationResult:
        """Generate the plugin, run SkillOpt, and firewall the emitted best.

        ``seed`` is either a bare ``OptimizableArtifact`` (the standalone path the
        adapter tests drive) or the orchestrator's ``SeedView`` (artifact on
        ``.seed``). Steps: write the env-plugin into a run dir, ``await
        _invoke_train`` (the ONE subprocess), parse ``best_skill.md``, convert it
        via ``with_content``, and run the artifact's OWN ``validate()``. A valid
        best becomes the proposal; a violating best is recorded as a ``Trial`` with
        its violations and the result carries ``best=None``. Acceptance stays
        ``False`` — the orchestrator owns the held-out D4 gate.
        """
        _ = ladder  # SkillOpt walks its own internal search; the ladder governs the D4 gate
        artifact = _seed_artifact(seed)
        run_dir = _prepare_run_dir(seed)
        config = SkillOptConfig.from_budget(budget)
        generate_env_plugin(run_dir, tasks=self.tasks, config=config)
        cmd = self._train_command(run_dir, config)
        await _invoke_train(cmd, run_dir)
        return _result_from_best(artifact, run_dir, _provenance(seed, artifact))

    def _train_command(self, run_dir: Path, config: SkillOptConfig) -> tuple[str, ...]:
        """Build the ``python -m skillopt.train --config <yaml>`` argv (consumed surface)."""
        config_path = run_dir / "configs" / f"{config.config_name}.yaml"
        return (str(self.python), "-m", "skillopt.train", "--config", str(config_path))


def _prepare_run_dir(seed: object) -> Path:
    """A per-run scratch dir the plugin + SkillOpt output live in.

    Uses a temp dir so a standalone ``optimize`` call (no orchestrator ledger)
    still has a concrete cwd for the subprocess to write ``best_skill.md`` into.
    """
    import tempfile

    _ = seed
    return Path(tempfile.mkdtemp(prefix="skillopt-run-"))


def _result_from_best(
    seed: OptimizableArtifact, run_dir: Path, provenance: Provenance
) -> OptimizationResult:
    """Parse ``best_skill.md``, firewall it, and assemble the proposal.

    A missing output (SkillOpt produced nothing) OR a candidate that fails
    ``validate()`` yields ``best=None`` with a recorded trial carrying the
    violations — the firewall is never bypassed. A clean best becomes the proposal.
    """
    best_path = run_dir / _BEST_SKILL_FILE
    if not best_path.is_file():
        return _empty_result(provenance, ("skillopt produced no best_skill.md",))
    candidate = seed.with_content(best_path.read_text())
    violations = candidate.validate()
    if violations:
        return _empty_result(provenance, violations)
    trial = Trial(fingerprint=candidate.fingerprint, rung_scores=(), cost_usd=0.0, violations=())
    return OptimizationResult(
        best=candidate,
        accepted=False,  # the orchestrator owns the held-out D4 acceptance gate
        trials=(trial,),
        total_usd=0.0,  # SkillOpt's internal spend is not observable through our harness
        provenance=provenance,
    )


def _empty_result(provenance: Provenance, violations: tuple[str, ...]) -> OptimizationResult:
    """A no-best result recording why (missing output or a firewalled candidate)."""
    trial = Trial(fingerprint="", rung_scores=(), cost_usd=0.0, violations=violations)
    return OptimizationResult(
        best=None,
        accepted=False,
        trials=(trial,),
        total_usd=0.0,
        provenance=provenance,
    )


def _seed_artifact(seed: object) -> OptimizableArtifact:
    """Unwrap the artifact whether ``seed`` is bare or an orchestrator ``SeedView``."""
    # The orchestrator hands a ``SeedView`` (``.seed`` is the artifact); the
    # standalone adapter tests hand the artifact directly. ``getattr`` bridges
    # both without importing the orchestrator (which would be a forward cycle).
    return getattr(seed, "seed", seed)  # type: ignore[return-value]


def _provenance(seed: object, best: OptimizableArtifact) -> Provenance:
    """Reuse the orchestrator's provenance, or synthesize one for a bare seed."""
    existing = getattr(seed, "provenance", None)
    if existing is not None:
        return existing
    return Provenance(
        seed_fingerprint=best.fingerprint,
        dataset_revision="",
        model_ids=(),
        optimizer=_OPTIMIZER_NAME,
    )
