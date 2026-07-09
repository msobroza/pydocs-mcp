"""The optimize CLI + zero-spend ``--dry-run`` preflight (spec §D5/§D7).

``python -m pydocs_eval.optimize --config <cfg>.yaml [--dry-run] [--resume LEDGER]``
drives one optimization run, or — with ``--dry-run`` — walks the WHOLE pipeline
spending nothing: it validates the seed against its §D13 firewall, echoes the
wired ladder, checks the split predicate is deterministic and both-sided,
reports each optimizer adapter's availability (``skillopt`` SKIPPED when its
extra is absent — a dry run must never require it), and runs one full
orchestrator pass on a zero-cost fake fitness with ``FakeAgentRunner`` /
``FakeJudge``. Every step prints; ``$0.00`` is spent.

A real (non-dry) run is manual and preflight-gated (spec §D5): the CLI prints
the spend expectations + the runbook pointer and proceeds only under the
budget's ``max_usd`` cap. No test drives the real path — the whole suite runs
``--dry-run`` and spends nothing.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydocs_eval.optimize._agent_track_binding import FakeAgentRunner, FakeJudge
from pydocs_eval.optimize._split import partition_task_ids
from pydocs_eval.optimize._types import (
    FitnessReport,
    OptimizationBudget,
    OptimizationResult,
    Provenance,
)
from pydocs_eval.optimize.artifacts import ToolDocsArtifact, UsageSkillArtifact  # noqa: F401
from pydocs_eval.optimize.ladder import FitnessLadder
from pydocs_eval.optimize.optimizers.critique_refine import FakeCritiqueClient
from pydocs_eval.optimize.optimizers.skillopt import SkillOptOptimizer
from pydocs_eval.optimize.orchestrator import SeedView, run_optimization
from pydocs_eval.optimize.protocols import OptimizableArtifact
from pydocs_eval.optimize.registries import (
    artifact_registry,
    optimizer_registry,
)
from pydocs_eval.optimize.run_config import OptimizeRunConfig, load_run_config
from pydocs_eval.optimize.trials_ledger import TrialsLedger
from pydocs_eval.serialization import dataset_registry

# The runbook a landed proposal + a paid run are documented in (spec §D7). Named
# in the CLI output so an operator knows where the preflight-first rule + spend
# model live. Single source of truth for the pointer string.
_RUNBOOK_PATH = "benchmarks/AGENT_TRACK.md"

# A tiny synthetic id sample the split-determinism check runs over when the
# config names no fixture — enough distinct ids that the sha256 % 2 predicate
# lands on BOTH sides so ``partition_task_ids`` proves it is deterministic and
# non-empty without any network (a real run resolves the dataset's own ids).
_SPLIT_PROBE_IDS = tuple(f"swe-qa-pro:{i:04d}" for i in range(12))


@dataclass(frozen=True, slots=True)
class _ZeroCostFitness:
    """A free, zero-spend fitness for the dry-run orchestrator pass.

    Scores every candidate 0.0 on both splits at no cost, so the full
    orchestrator pass (seed validate → train firewall → holdout gate) runs
    end-to-end spending nothing. Never used on a real run — the real ladder
    resolves the paid ``paired_agent`` fitness.
    """

    name: str = "paired_agent"
    cost_tier: Literal["free", "paid"] = "free"

    async def evaluate(
        self,
        artifact: OptimizableArtifact,
        *,
        split: Literal["train", "holdout"],
    ) -> FitnessReport:
        _ = (artifact, split)  # dry-run: nothing is measured, nothing is spent
        return FitnessReport(score=0.0, components={}, cost_usd=0.0, n_samples=0)


@dataclass(frozen=True, slots=True)
class _SeedEchoOptimizer:
    """A no-op optimizer for the dry-run pass: returns the seed, proposes nothing.

    Drives the orchestrator's gate + train-firewall wiring without an LLM or a
    subprocess (the real optimizers reach a client / ``train.py``). ``best=None``
    means "nothing beat the seed", so the pass exercises the whole control loop
    at zero spend.
    """

    name: str = "dry-run-echo"

    async def optimize(
        self,
        seed: SeedView,
        ladder: FitnessLadder,
        budget: OptimizationBudget,
    ) -> OptimizationResult:
        _ = (ladder, budget)  # the orchestrator owns the gate; this proposes nothing
        return OptimizationResult(
            best=None,
            accepted=False,
            trials=(),
            total_usd=0.0,
            provenance=seed.provenance,
        )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pydocs_eval.optimize",
        description="Offline harness-artifact optimizer. Manual, preflight-gated, never CI.",
    )
    parser.add_argument("--config", type=Path, required=True, help="run config YAML (spec §D7)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="walk the whole pipeline with a fake runner/fitness; spend $0.00",
    )
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="resume from this trials ledger (already-scored candidates are skipped)",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("optimize_trials.jsonl"),
        help="JSONL trials ledger path (resume key: (fingerprint, split))",
    )
    return parser


def _seed_artifact(cfg: OptimizeRunConfig) -> OptimizableArtifact:
    """Build the run's seed artifact from the registry (its unseeded render)."""
    return artifact_registry.build(cfg.artifact)


def _print_seed_report(seed: OptimizableArtifact) -> None:
    """Echo the seed's §D13 ``validate()`` report (empty violations == clean)."""
    violations = seed.validate()
    status = "clean" if not violations else f"{len(violations)} violation(s): {list(violations)}"
    print(f"  seed: {seed.name!r} validate() -> {status}")


def _print_ladder(ladder: FitnessLadder) -> None:
    """Echo the wired ladder rungs (fitness, max_tasks, survivors)."""
    rungs = ", ".join(f"[{r.fitness_name}, {r.max_tasks}, {r.survivors}]" for r in ladder.rungs)
    print(f"  ladder: {len(ladder.rungs)} rung(s): {rungs}")


def _print_split_determinism(cfg: OptimizeRunConfig) -> None:
    """Check the split predicate is deterministic + both-sided over probe ids.

    Uses the config's fixture task ids when one is named; otherwise a synthetic
    id sample (offline — a real run resolves the dataset's own ids). Prints the
    (train, holdout) sizes; ``partition_task_ids`` raises loudly if either side
    is empty (a tiny/skewed pool is a config error, spec §D3).
    """
    ids = _probe_task_ids(cfg)
    train, holdout = partition_task_ids(ids)
    print(
        f"  split: deterministic sha256 % 2 over {len(ids)} id(s) -> "
        f"train={len(train)}, holdout={len(holdout)}"
    )


def _probe_task_ids(cfg: OptimizeRunConfig) -> Sequence[str]:
    """The task ids the split-determinism check runs over (offline)."""
    if cfg.dataset.fixture_path is None:
        return _SPLIT_PROBE_IDS
    dataset = dataset_registry.build(cfg.dataset.name, fixture_path=cfg.dataset.fixture_path)
    return _collect_fixture_ids(dataset)


def _collect_fixture_ids(dataset: object) -> tuple[str, ...]:
    """Drain a fixture-backed dataset's task ids synchronously (offline read)."""

    async def _drain() -> tuple[str, ...]:
        return tuple([task.task_id async for task in dataset.tasks()])  # type: ignore[attr-defined]

    return asyncio.run(_drain())


def _print_optimizer_availability(cfg: OptimizeRunConfig) -> None:
    """Report each v1 optimizer adapter's availability (SKIPPED never fails a dry run).

    ``critique_refine`` is proven importable by constructing it with an empty
    ``FakeCritiqueClient`` (it never completes). ``skillopt`` calls
    ``ensure_available()``; a missing ``[optimizers-skillopt]`` extra is reported
    SKIPPED — a dry run must not require the extra (spec §D7).
    """
    print(f"  optimizer: run config selects {cfg.optimizer!r}")
    # critique_refine constructs offline with the scripted fake client.
    optimizer_registry.build(
        "critique_refine", client=FakeCritiqueClient(replies=[]), fitness=_ZeroCostFitness()
    )
    print("    - critique_refine: importable (constructs with FakeCritiqueClient)")
    _report_skillopt_availability()


def _report_skillopt_availability() -> None:
    """Probe ``skillopt`` availability; SKIPPED when the extra is absent."""
    try:
        SkillOptOptimizer(python=Path("python")).ensure_available()
    except RuntimeError as exc:
        print(f"    - skillopt: SKIPPED (extra not installed): {exc}")
        return
    print("    - skillopt: available")


async def _dry_run(cfg: OptimizeRunConfig, *, ledger_path: Path) -> int:
    """Walk the whole pipeline offline and report; return exit code 0 (spent $0.00).

    Order mirrors spec §D5's dry-run contract: seed validation, ladder wiring,
    split determinism, optimizer availability, then one full orchestrator pass on
    a zero-cost fake fitness with ``FakeAgentRunner`` / ``FakeJudge``. Nothing is
    measured against a live agent, so the run spends exactly ``$0.00``.
    """
    print("DRY RUN — walking the optimize pipeline offline (no money is spent):")
    seed = _seed_artifact(cfg)
    _print_seed_report(seed)
    _print_ladder(cfg.ladder)
    _print_split_determinism(cfg)
    _print_optimizer_availability(cfg)
    result = await _dry_orchestrator_pass(cfg, seed, ledger_path=ledger_path)
    print(f"  orchestrator pass: accepted={result.accepted}, total spend: ${result.total_usd:.2f}")
    print(f"total spend: ${result.total_usd:.2f}")
    print(f"DRY RUN — no money was spent. A paid run needs an explicit go (see {_RUNBOOK_PATH}).")
    return 0


async def _dry_orchestrator_pass(
    cfg: OptimizeRunConfig, seed: OptimizableArtifact, *, ledger_path: Path
) -> OptimizationResult:
    """One full ``run_optimization`` pass on a zero-cost fake fitness (spends $0.00)."""
    # FakeAgentRunner / FakeJudge are constructed to prove the agent-track doubles
    # import; the zero-cost fitness stands in for the paid paired-agent run, so the
    # whole control loop (train firewall + holdout gate) runs without a live agent.
    _ = (FakeAgentRunner(), FakeJudge())
    fitness = _ZeroCostFitness()
    fitness_by_name: Mapping[str, object] = {
        rung.fitness_name: fitness for rung in cfg.ladder.rungs
    }
    return await run_optimization(
        seed,
        _SeedEchoOptimizer(),
        cfg.ladder,
        cfg.budget,
        fitness_by_name=fitness_by_name,  # type: ignore[arg-type]
        ledger=TrialsLedger(ledger_path),
        provenance=_dry_provenance(cfg, seed),
    )


def _dry_provenance(cfg: OptimizeRunConfig, seed: OptimizableArtifact) -> Provenance:
    """Synthesize provenance for the dry-run pass (audit shape, no real models)."""
    return Provenance(
        seed_fingerprint=seed.fingerprint,
        dataset_revision=cfg.dataset.name,
        model_ids=(),
        optimizer=cfg.optimizer,
    )


def _print_real_run_expectations(cfg: OptimizeRunConfig) -> None:
    """Print the spend expectations + runbook pointer before a paid run (spec §D5)."""
    print("REAL RUN — this spends real money.")
    print(f"  budget cap: max_usd=${cfg.budget.max_usd:.2f}, max_trials={cfg.budget.max_trials}")
    print(f"  preflight-first + spend model: see {_RUNBOOK_PATH}")


async def _main_async(args: argparse.Namespace) -> int:
    """Load the config, then dry-run or (real) print spend expectations + proceed."""
    cfg = load_run_config(args.config)
    ledger_path = args.resume if args.resume is not None else args.ledger
    if args.dry_run:
        return await _dry_run(cfg, ledger_path=ledger_path)
    # Real path: machinery only in this slice — a paid run needs an explicit go
    # (spec spend gate). Print the expectations + runbook pointer and stop short
    # of spending; a later, user-authorized change wires the real optimizer.
    _print_real_run_expectations(cfg)
    print("Machinery is ready. Re-run with --dry-run to preflight, or authorize a paid run.")
    return 0


async def cli_main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run the optimize CLI; return the process exit code.

    Async so the whole pipeline (dataset drain, orchestrator pass) runs on one
    event loop. ``main()`` wraps this in ``asyncio.run`` for the console entry.
    """
    args = _build_arg_parser().parse_args(argv)
    return await _main_async(args)


def main() -> None:
    """Console entry: run the async CLI and exit with its code."""
    raise SystemExit(asyncio.run(cli_main()))


if __name__ == "__main__":  # pragma: no cover -- CLI entry, not unit-tested
    main()
