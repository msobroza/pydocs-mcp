"""The ``skillopt`` optimizer — an adapter to microsoft/SkillOpt (spec §D4).

SkillOpt (MIT; ``github.com/microsoft/SkillOpt``; PyPI ``skillopt`` 0.2.x)
trains a skill document over an environment adapter. Its custom-benchmark
contract is a ``skillopt.envs.base.EnvAdapter`` subclass registered under
``scripts.train._ENV_REGISTRY`` and driven by the ``skillopt-train`` console
entry (``scripts.train:main``). This adapter generates that plugin at run time —
an env-adapter module with the train rows inlined, a ``run.py`` that injects the
registry key and defers to ``scripts.train.main()``, the seed skill, and a
structured config YAML — invokes ``run.py`` as a subprocess (the ONLY subprocess
in the optimize layer, and this module is the ONLY place that knows SkillOpt),
parses the emitted ``best_skill.md``, converts it back through the seed's
``with_content`` / ``validate()`` firewall, and hands the proposal to the SAME
D4 holdout gate as any other optimizer. A candidate that fails ``validate()`` is
recorded and the result carries ``best=None`` — the firewall is never bypassed.

**Spend asymmetry (spec §D4/§D5), documented not hidden.** SkillOpt runs its own
rollouts on its own harness, so the orchestrator's outer ``--max-usd`` CANNOT
interrupt a training run mid-flight — and skillopt 0.2.x has NO spend knob at
all (a package-wide grep finds no budget/USD config key; the only USD symbol is
read-only codex telemetry). ``OptimizationBudget.max_trials`` therefore maps to
SkillOpt's rollout counts (``_rollout_plan`` → epochs / batch size / selection
eval size — the only native bound), while ``OptimizationBudget.max_usd`` is
recorded in the generated YAML as an explanatory comment ONLY: nothing inside
``skillopt-train`` enforces it. The outer cap still bounds the D4 holdout-gate
runs, which DO go through our harness — ``test_budget_mapping_asserted`` pins
both halves of this mapping.

Offline-test contract (slice-6): the real ``skillopt`` library is NEVER imported
by the test suite. ``generate_env_plugin`` is a pure file-writer (the generated
sources import SkillOpt only when the SUBPROCESS runs them, inside the venv the
extra installed); ``ensure_available`` only asks ``importlib.util.find_spec``;
the subprocess lives behind the module-level ``_invoke_train`` so tests
monkeypatch it and spend nothing. ``_CONSUMED_SKILLOPT_SURFACE`` is the single
tuple enumerating every assumption this adapter makes about SkillOpt — the
version-pin canary: a SkillOpt bump that moves any consumed symbol/CLI trips
this constant's test first.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template

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
# A version bump that renames the CLI, moves the env registry, reshapes the
# EnvAdapter ABC, or changes the output path breaks a line here and
# ``test_consumed_surface_is_enumerated_and_stable`` fails FIRST — before any
# real run silently produces garbage (spec §D8 canary).
_CONSUMED_SKILLOPT_SURFACE = (
    "scripts.train:main (the skillopt-train console entry) --config <yaml>",
    "scripts.train._ENV_REGISTRY[<env name>] = <EnvAdapter subclass> (run.py injection)",
    "skillopt.envs.base.EnvAdapter: build_train_env / build_eval_env /"
    " rollout -> [{id, hard, soft}] / get_task_types",
    "config YAML sections: model / train / gradient / optimizer / evaluation / env"
    " (no spend key exists)",
    "output: <out_root>/best_skill.md",
)

# The importable module SkillOpt installs; the extra that installs it. WHY a
# PyPI version range and not a git SHA: the air-gapped deployment installs from
# a PyPI mirror only (direct git URLs are unreachable there, and PyPI bans
# direct-URL Requires-Dist anyway), and the released 0.2.x wheel ships
# everything this adapter consumes. The range itself lives in the
# [optimizers-skillopt] extra (benchmarks/pyproject.toml) — the single install
# source of truth; bumping it is a deliberate edit gated by the
# _CONSUMED_SKILLOPT_SURFACE canary test.
_SKILLOPT_MODULE = "skillopt"
_SKILLOPT_EXTRA = "optimizers-skillopt"

# The generated env-plugin's fixed config basename (spec §D4), doubling as the
# ``_ENV_REGISTRY`` key ``run.py`` injects. The usage_skill artifact is the v1
# SkillOpt target; the name is stable so ``optimize`` and the plugin writer
# agree without threading it around.
_DEFAULT_CONFIG_NAME = "pydocs_usage_skill"

# The optimizer name recorded in synthesized provenance when handed a bare seed.
_OPTIMIZER_NAME = "skillopt"

# SkillOpt writes its winning skill here inside out_root (consumed surface);
# ``env.out_root: "."`` + subprocess cwd=run_dir pins it to <run_dir>/best_skill.md.
_BEST_SKILL_FILE = "best_skill.md"

# The generated files: the EnvAdapter module, the launcher that injects it into
# scripts.train._ENV_REGISTRY, and the seed skill ``env.skill_init`` points at.
_PLUGIN_MODULE_NAME = "pydocs_env_plugin"
_ENV_ADAPTER_CLASS = "PydocsEnvAdapter"
_RUN_FILE = "run.py"
_SEED_SKILL_FILE = "seed_skill.md"

# Both backend halves are chat backends: the generated rollout drives
# ``skillopt.model.chat_target`` (exec backends are rejected by it), and the
# optimizer side must be a chat backend per SkillOpt's own backend matrix.
# ``claude_chat`` shells out to the local ``claude`` CLI — no API key needed.
_CHAT_BACKEND = "claude_chat"
# Mirrors SkillOpt's own default_model_for_backend("claude") deployment default,
# so the generated config never races ahead of what the backend accepts.
_DEFAULT_MODEL = "claude-sonnet-4-6"

# Rollout-plan floor + reflect defaults surfaced in the generated YAML. The
# gradient/optimizer values mirror SkillOpt's documented example configs.
_MIN_SEL_ENV_NUM = 1
_DEFAULT_SEED = 42
_DEFAULT_EDIT_BUDGET = 4
_DEFAULT_ANALYST_WORKERS = 4
_DEFAULT_MERGE_BATCH_SIZE = 4
_DEFAULT_MINIBATCH_SIZE = 4


@dataclass(frozen=True, slots=True)
class SkillOptConfig:
    """The budget mapping onto SkillOpt's OWN config fields (spec §D4).

    ``max_trials`` maps to SkillOpt's rollout counts via ``_rollout_plan`` — the
    only native bound, since skillopt 0.2.x has no spend key. ``max_usd`` has NO
    native sink; it is recorded in the generated YAML as an explanatory comment
    and enforced only by the orchestrator's outer holdout-gate cap. Our outer
    ``--max-usd`` cannot interrupt ``skillopt-train`` mid-run — this mapping is
    the only lever we have over the SkillOpt search's spend, so it is asserted
    in a test (``test_budget_mapping_asserted``).
    """

    max_trials: int
    max_usd: float
    config_name: str = _DEFAULT_CONFIG_NAME

    @classmethod
    def from_budget(cls, budget: OptimizationBudget) -> SkillOptConfig:
        """Map an ``OptimizationBudget`` onto SkillOpt's own rollout-count fields."""
        return cls(max_trials=budget.max_trials, max_usd=budget.max_usd)


def _rollout_plan(max_trials: int, train_size: int) -> tuple[int, int, int]:
    """Map ``max_trials`` onto ``(num_epochs, batch_size, sel_env_num)`` counts.

    Rollout count is SkillOpt's ONLY native bound (no spend key exists) and the
    trainer derives steps_per_epoch itself, so the levers are epochs, batch size
    and the selection-eval size. With one full-pool step per epoch (batch_size =
    train pool, accumulation 1, eval_test off), total item-rollouts for a run =
    ``sel + num_epochs * (batch + sel)``. The selection eval is capped at the
    pool size AND a quarter of the budget (an eval bigger than either wastes
    search rollouts); epochs then grow while the total stays within
    ``max_trials``. Floor ``(1, pool, 1)``: a minimum viable run — baseline eval
    + one train batch + one candidate eval — even when ``max_trials`` is tighter
    than that.
    """
    batch = max(train_size, 1)
    sel = max(min(batch, max_trials // 4), _MIN_SEL_ENV_NUM)
    epochs = max((max_trials - sel) // (batch + sel), 1)
    return epochs, batch, sel


def generate_env_plugin(
    root: Path,
    *,
    tasks: Sequence[tuple[str, str, str]],
    config: SkillOptConfig,
    seed_content: str = "",
) -> Path:
    """Write the SkillOpt env plugin under ``root`` and return its directory.

    The plugin is SkillOpt 0.2.x's custom-benchmark contract (spec §D4): an
    ``EnvAdapter`` subclass module with our train-split ``(task_id, question,
    gold)`` rows inlined as JSON (so the subprocess re-imports it standalone —
    no closure over our process), a ``run.py`` that injects the adapter into
    ``scripts.train._ENV_REGISTRY`` and defers to ``scripts.train.main()``, the
    seed skill ``env.skill_init`` starts the search from, and the structured
    ``configs/<name>.yaml`` carrying the MAPPED budget (``max_trials`` →
    rollout counts; ``max_usd`` → an explanatory comment, no native sink).
    Pure file I/O — imports nothing from SkillOpt.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "configs").mkdir(exist_ok=True)
    (root / f"{_PLUGIN_MODULE_NAME}.py").write_text(_plugin_module_source(tasks))
    (root / _RUN_FILE).write_text(_run_py_source(config))
    (root / _SEED_SKILL_FILE).write_text(seed_content)
    (root / "configs" / f"{config.config_name}.yaml").write_text(
        _config_yaml(config, train_size=len(tasks))
    )
    return root


def _config_yaml(config: SkillOptConfig, *, train_size: int) -> str:
    """Render ``configs/<name>.yaml`` in the 0.2.0 structured schema.

    Every section/key the trainer hard-indexes is stated explicitly; the
    ``max_usd`` header comment is the honest record of the unenforceable half
    of the budget mapping (spec §D4 spend asymmetry).
    """
    epochs, batch, sel = _rollout_plan(config.max_trials, train_size)
    body = yaml.safe_dump(
        {
            "model": {
                # WHY both halves explicitly: a YAML-only shared `backend:` does
                # not propagate to target_backend in scripts/train.py.
                "optimizer_backend": _CHAT_BACKEND,
                "target_backend": _CHAT_BACKEND,
                "optimizer": _DEFAULT_MODEL,
                "target": _DEFAULT_MODEL,
            },
            "train": {
                "num_epochs": epochs,
                "batch_size": batch,
                "accumulation": 1,
                "seed": _DEFAULT_SEED,
                # Required: the generated adapter keeps get_dataloader() -> None,
                # so the trainer needs the pool size stated here.
                "train_size": batch,
            },
            "gradient": {
                "merge_batch_size": _DEFAULT_MERGE_BATCH_SIZE,
                "analyst_workers": _DEFAULT_ANALYST_WORKERS,
                "minibatch_size": _DEFAULT_MINIBATCH_SIZE,
                "failure_only": False,
            },
            # learning_rate aliases to edit_budget (edits per patch) — an LR
            # analog, NOT a spend knob.
            "optimizer": {"learning_rate": _DEFAULT_EDIT_BUDGET},
            "evaluation": {
                "sel_env_num": sel,
                # The D4 holdout gate is OURS — SkillOpt's own test phase would
                # spend extra rollouts outside the max_trials mapping.
                "eval_test": False,
                "test_env_num": 0,
            },
            "env": {
                "name": config.config_name,
                "skill_init": f"./{_SEED_SKILL_FILE}",
                # abspath'ed against the subprocess cwd (run_dir), pinning
                # best_skill.md to <run_dir>/best_skill.md (consumed surface).
                "out_root": ".",
            },
        },
        sort_keys=True,
    )
    return _max_usd_comment(config) + body


def _max_usd_comment(config: SkillOptConfig) -> str:
    """The honest record of the unenforceable half of the budget mapping."""
    return (
        f"# max_usd={config.max_usd}: recorded, NOT enforced. skillopt 0.2.x has no\n"
        "# spend key (the only USD symbol package-wide is read-only codex telemetry),\n"
        "# so this bound holds only indirectly: via the rollout-count mapping below\n"
        f"# (max_trials={config.max_trials}) and the orchestrator's outer holdout-gate\n"
        "# cap, which DOES go through our harness.\n"
    )


# The static halves of the generated EnvAdapter module. Kept as string.Template
# constants ($-placeholders — the generated code is full of literal braces, so
# str.format would mangle it); the only other dynamic line is the tasks JSON.
_PLUGIN_HEADER = Template('''\
"""SkillOpt env plugin — pydocs usage-skill rollouts (generated; do not edit).

Imported only inside the skillopt venv subprocess: run.py injects
$cls into scripts.train._ENV_REGISTRY before scripts.train.main().
"""
import json
import os
import random

from skillopt.envs.base import EnvAdapter
from skillopt.model import chat_target

''')

_PLUGIN_BODY = Template('''

def _normalize(text):
    """Lowercase + collapse whitespace so grading ignores formatting noise."""
    return " ".join(str(text).lower().split())


def _soft_score(answer, gold):
    """Token-overlap F1 between the answer and the gold string (0.0-1.0)."""
    answer_tokens = _normalize(answer).split()
    gold_tokens = _normalize(gold).split()
    common = 0
    remaining = list(gold_tokens)
    for token in answer_tokens:
        if token in remaining:
            remaining.remove(token)
            common += 1
    if common == 0:
        return 0.0
    precision = common / len(answer_tokens)
    recall = common / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def _sample(count, seed):
    """A deterministic, seed-shuffled slice of the inlined task pool."""
    pool = list(_TASKS)
    random.Random(seed).shuffle(pool)
    return pool[: max(min(count, len(pool)), 0)]


def _rollout_one(item, skill_content, out_dir, max_completion_tokens):
    """One graded QA rollout; hard = gold containment, soft = token F1."""
    system = (
        "You answer questions about the pydocs-mcp codebase. Apply the skill "
        "document below.\\n\\n## Skill\\n" + skill_content
    )
    result = {
        "id": str(item["task_id"]),
        "hard": 0,
        "soft": 0.0,
        "question": item["question"],
        "predicted_answer": "",
        "fail_reason": "",
    }
    try:
        answer, _raw = chat_target(
            system=system,
            user=item["question"],
            max_completion_tokens=max_completion_tokens,
        )
    except Exception as exc:  # noqa: BLE001 -- one failed item must not sink the batch
        result["fail_reason"] = "error: %s" % exc
        return result
    result["predicted_answer"] = answer
    result["soft"] = _soft_score(answer, item["gold"])
    result["hard"] = int(_normalize(item["gold"]) in _normalize(answer))
    if not result["hard"]:
        result["fail_reason"] = "gold %r not found in the answer" % item["gold"]
    _write_conversation(out_dir, result, system, item)
    return result


def _write_conversation(out_dir, result, system, item):
    """The reflect analyst reads predictions/<id>/conversation.json trajectories."""
    pred_dir = os.path.join(out_dir, "predictions", result["id"])
    os.makedirs(pred_dir, exist_ok=True)
    verdict = "[EVALUATION] hard=%s soft=%.4f gold=%r fail_reason=%s" % (
        result["hard"],
        result["soft"],
        item["gold"],
        result["fail_reason"] or "none",
    )
    conversation = [
        {"role": "system", "content": system},
        {"role": "user", "content": item["question"]},
        {"role": "assistant", "content": result["predicted_answer"]},
        {"role": "system", "content": verdict},
    ]
    with open(os.path.join(pred_dir, "conversation.json"), "w") as f:
        json.dump(conversation, f, ensure_ascii=False, indent=2)


class $cls(EnvAdapter):
    """Serves the inlined train rows and grades chat_target rollouts."""

    def __init__(
        self,
        analyst_workers=$analyst_workers,
        failure_only=False,
        minibatch_size=$minibatch_size,
        edit_budget=$edit_budget,
        seed=$seed,
        max_completion_tokens=16384,
    ):
        # The base EnvAdapter.reflect() default reads these four attributes but
        # the base class never sets them — the ctor MUST (SkillOpt contract).
        # Params are named after flat cfg keys so get_adapter() auto-fills them.
        self.analyst_workers = analyst_workers
        self.failure_only = failure_only
        self.minibatch_size = minibatch_size
        self.edit_budget = edit_budget
        self.seed = seed
        self.max_completion_tokens = int(max_completion_tokens)

    def build_train_env(self, batch_size, seed, **kwargs):
        return _sample(batch_size, seed)

    def build_eval_env(self, env_num, split, seed, **kwargs):
        return _sample(env_num, seed)

    def rollout(self, env_manager, skill_content, out_dir, **kwargs):
        return [
            _rollout_one(item, skill_content, out_dir, self.max_completion_tokens)
            for item in env_manager
        ]

    def get_task_types(self):
        return ["usage"]
''')


def _plugin_module_source(tasks: Sequence[tuple[str, str, str]]) -> str:
    """Emit the EnvAdapter module with the train rows inlined as JSON."""
    rows = [{"task_id": t, "question": q, "gold": g} for t, q, g in tasks]
    serialized = json.dumps(rows)
    header = _PLUGIN_HEADER.substitute(cls=_ENV_ADAPTER_CLASS)
    body = _PLUGIN_BODY.substitute(
        cls=_ENV_ADAPTER_CLASS,
        analyst_workers=_DEFAULT_ANALYST_WORKERS,
        minibatch_size=_DEFAULT_MINIBATCH_SIZE,
        edit_budget=_DEFAULT_EDIT_BUDGET,
        seed=_DEFAULT_SEED,
    )
    return header + f"_TASKS = json.loads({serialized!r})\n" + body


def _run_py_source(config: SkillOptConfig) -> str:
    """Emit ``run.py``: registry injection, then defer to ``scripts.train.main()``."""
    return (
        '"""SkillOpt launcher — registers the generated env adapter, then defers to\n'
        'scripts.train:main, the skillopt-train entry (generated; do not edit)."""\n'
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
        "\n"
        "import scripts.train as _train\n"
        "\n"
        f"from {_PLUGIN_MODULE_NAME} import {_ENV_ADAPTER_CLASS}\n"
        "\n"
        "# get_adapter() resolves env names ONLY from this module-global registry;\n"
        "# _register_builtins() never touches custom keys, so pre-injection is safe.\n"
        f"_train._ENV_REGISTRY[{config.config_name!r}] = {_ENV_ADAPTER_CLASS}\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    _train.main()\n"
    )


async def _invoke_train(cmd: Sequence[str], run_dir: Path) -> int:
    """Run the generated ``run.py --config ...`` and return its exit code.

    The ONE subprocess in the optimize layer, isolated here at module level so
    the whole adapter is exercised offline (tests monkeypatch this). ``run_dir``
    is the subprocess cwd, where SkillOpt writes ``best_skill.md``
    (``env.out_root: "."`` is abspath'ed against it).
    """
    import asyncio

    process = await asyncio.create_subprocess_exec(*cmd, cwd=str(run_dir))
    return await process.wait()


@optimizer_registry.register("skillopt")
@dataclass(frozen=True, slots=True)
class SkillOptOptimizer:
    """SkillOpt env-plugin adapter with the ``validate()`` firewall (spec §D4).

    ``python`` is the interpreter that runs the generated ``run.py`` (the
    ``[optimizers-skillopt]`` extra installs SkillOpt into that env). ``tasks``
    are the train-split rows the generated adapter serves — empty is legal (the
    standalone optimizer tests drive it that way); the orchestrator supplies
    real rows.
    """

    python: Path
    tasks: tuple[tuple[str, str, str], ...] = ()
    name: str = _OPTIMIZER_NAME

    def ensure_available(self) -> None:
        """Raise an actionable ``RuntimeError`` when SkillOpt is not importable.

        Preflight check (never called by ``optimize`` — the CLI/orchestrator calls
        it before a paid run). Uses ``find_spec`` so no import side effects fire;
        the error names the extra that installs the PyPI-pinned range.
        """
        if importlib.util.find_spec(_SKILLOPT_MODULE) is None:
            raise RuntimeError(
                f"{_SKILLOPT_MODULE!r} is not installed — run "
                f'pip install "pydocs-mcp-eval[{_SKILLOPT_EXTRA}]" to run the '
                f"skillopt optimizer (the PyPI version range is pinned in "
                f"benchmarks/pyproject.toml)"
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
        ``.seed``). Steps: write the env plugin + seed skill into a run dir,
        ``await _invoke_train`` (the ONE subprocess), parse ``best_skill.md``,
        convert it via ``with_content``, and run the artifact's OWN
        ``validate()``. A valid best becomes the proposal; a violating best is
        recorded as a ``Trial`` with its violations and the result carries
        ``best=None``. Acceptance stays ``False`` — the orchestrator owns the
        held-out D4 gate.
        """
        _ = ladder  # SkillOpt walks its own internal search; the ladder governs the D4 gate
        artifact = _seed_artifact(seed)
        run_dir = _prepare_run_dir(seed)
        config = SkillOptConfig.from_budget(budget)
        generate_env_plugin(
            run_dir, tasks=self.tasks, config=config, seed_content=artifact.render()
        )
        cmd = self._train_command(run_dir, config)
        await _invoke_train(cmd, run_dir)
        return _result_from_best(artifact, run_dir, _provenance(seed, artifact))

    def _train_command(self, run_dir: Path, config: SkillOptConfig) -> tuple[str, ...]:
        """Build the ``python run.py --config <yaml>`` argv (consumed surface)."""
        config_path = run_dir / "configs" / f"{config.config_name}.yaml"
        return (str(self.python), str(run_dir / _RUN_FILE), "--config", str(config_path))


def _prepare_run_dir(seed: object) -> Path:
    """A per-run scratch dir the plugin + SkillOpt output live in.

    Uses a temp dir so a standalone ``optimize`` call (no orchestrator ledger)
    still has a concrete cwd for the subprocess to write ``best_skill.md`` into.
    A FRESH dir per call also defeats SkillOpt's own resume-from-history logic —
    stale runtime state must never leak between optimize() calls.
    """
    import tempfile

    _ = seed
    return Path(tempfile.mkdtemp(prefix="skillopt-run-"))


def _result_from_best(
    seed: OptimizableArtifact, run_dir: Path, provenance: Provenance
) -> OptimizationResult:
    """Parse ``best_skill.md``, firewall it, and assemble the proposal.

    A missing output (SkillOpt produced nothing — e.g. a crash before the first
    training step completes) OR a candidate that fails ``validate()`` yields
    ``best=None`` with a recorded trial carrying the violations — the firewall
    is never bypassed. A clean best becomes the proposal (it can equal the seed
    verbatim: no-improvement runs still emit ``best_skill.md``).
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
