"""The zero-spend ``--dry-run`` preflight walk (spec §D5/§D7).

Walks the WHOLE optimize pipeline spending nothing: it validates the seed
against its §D13 firewall, echoes the wired ladder, checks the split predicate
is deterministic and both-sided, lists every configured arm with its identity,
reports each optimizer adapter's availability (``skillopt`` SKIPPED when its
extra is absent — a dry run must never require it), and runs one full
orchestrator pass PER ARM on scripted doubles. Every step prints; ``$0.00`` is
spent, and no arm's harness runtime is ever imported.

Lives beside the CLI rather than inside it so ``__main__`` stays argparse plus
the paid-run gate, and so the dry walk is importable by tests without going
through argv.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from pydocs_eval.datasets.task_ids import record_id_of
from pydocs_eval.optimize import ask_binding
from pydocs_eval.optimize._agent_track_binding import FakeAgentRunner, FakeJudge
from pydocs_eval.optimize._prefix_report import count_by_task_prefix
from pydocs_eval.optimize._split import partition_task_ids
from pydocs_eval.optimize._types import OptimizationBudget, OptimizationResult, Provenance
from pydocs_eval.optimize.arm_runtime import (
    ResolvedArm,
    arm_runner_factory,
    build_arm_fitness,
    resolve_arm_dataset,
    resolve_arms,
)
from pydocs_eval.optimize.arm_scoring import objective_fitness_name
from pydocs_eval.optimize.artifacts import ToolDocsArtifact, UsageSkillArtifact  # noqa: F401
from pydocs_eval.optimize.ask_binding import FakeAskRunner
from pydocs_eval.optimize.dry_doubles import (
    SPLIT_PROBE_IDS,
    ProbeDataset,
    SeedEchoOptimizer,
    ZeroCostFitness,
    dry_ask_doubles,
)
from pydocs_eval.optimize.fitness.ask_rubric import (
    AskRubricFitness,
    JudgeCallCounter,
    ask_objective_hash,
    enumerated_task_names,
)
from pydocs_eval.optimize.ladder import FitnessLadder
from pydocs_eval.optimize.optimizers.critique_refine import FakeCritiqueClient
from pydocs_eval.optimize.optimizers.skillopt import SkillOptOptimizer
from pydocs_eval.optimize.orchestrator import BudgetGuard, run_optimization
from pydocs_eval.optimize.protocols import OptimizableArtifact
from pydocs_eval.optimize.registries import artifact_registry, optimizer_registry
from pydocs_eval.optimize.rubric.judge import FakeRubricJudge
from pydocs_eval.optimize.rubric.sample_ledger import SampleRubricLedger
from pydocs_eval.optimize.run_config import (
    OptimizeRunConfig,
    build_config_search_optimizer,
)
from pydocs_eval.optimize.trials_ledger import TrialsLedger
from pydocs_eval.registries import dataset_registry

# The runbook a landed proposal + a paid run are documented in (spec §D7). Named
# in the CLI output so an operator knows where the preflight-first rule + spend
# model live. Single source of truth for the pointer string.
RUNBOOK_PATH = "benchmarks/AGENT_TRACK.md"

# How many hash characters identify an arm in human-facing output.
_HASH_ECHO_CHARS = 12


def seed_artifact(cfg: OptimizeRunConfig) -> OptimizableArtifact:
    """Build the run's seed artifact from the registry (its unseeded render)."""
    return artifact_registry.build(cfg.artifact)


async def dry_run(cfg: OptimizeRunConfig, *, ledger_path: Path) -> int:
    """Walk the whole pipeline offline and report; return exit code 0 (spent $0.00).

    Order mirrors spec §D5's dry-run contract: seed validation, ladder wiring,
    split determinism, the configured arms, optimizer availability, then one
    full orchestrator pass PER ARM on a zero-cost fake fitness with
    ``FakeAgentRunner`` / ``FakeJudge``. Nothing is measured against a live
    agent, so the run spends exactly ``$0.00``.
    """
    print("DRY RUN — walking the optimize pipeline offline (no money is spent):")
    seed = seed_artifact(cfg)
    _print_seed_report(seed)
    _print_ladder(cfg.ladder)
    _print_split_determinism(cfg)
    arms = resolve_arms(cfg)
    print_arm_plan(arms)
    _print_optimizer_availability(cfg)
    results = await _dry_orchestrator_passes(cfg, seed, arms, ledger_path=ledger_path)
    total = results[-1].total_usd if results else 0.0
    print(f"total spend: ${total:.2f}")
    print(f"DRY RUN — no money was spent. A paid run needs an explicit go (see {RUNBOOK_PATH}).")
    return 0


def print_arm_plan(arms: Sequence[ResolvedArm]) -> None:
    """List every configured arm with the identity a paid run would key on.

    Silent for a config with no ``arms:`` block — that run has the single
    implicit arm and there is nothing to disambiguate. Computing this costs
    nothing paid and imports no harness RUNTIME: the arm hash folds the
    harness's delivery-map digest (a pure product constant) but never the
    agent graph, which is why a dry run can describe an arm on a machine
    without ``langgraph``.
    """
    if not arms:
        return
    print(f"  arms: {len(arms)} configured (each runs its own orchestrator pass)")
    for arm in arms:
        print(f"    - {arm.label} {arm.arm_hash[:_HASH_ECHO_CHARS]}: {_arm_summary(arm)}")


def _arm_summary(arm: ResolvedArm) -> str:
    """One arm's human-facing line: corpus, framing, surface, objective, watched."""
    dataset = resolve_arm_dataset(arm.cell)
    tools = "all nine" if arm.cell.tool_names is None else ",".join(arm.cell.tool_names)
    tracked = ",".join(arm.cell.scoring.tracked) or "none"
    return (
        f"dataset={dataset.name}@{dataset.revision}, task_name={arm.cell.task_name}, "
        f"tools=[{tools}], objective={arm.cell.scoring.objective}"
        f"/{arm.objective_hash[:_HASH_ECHO_CHARS]}, tracked=[{tracked}]"
    )


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
    """Check the split predicate is deterministic + both-sided over probe keys.

    Uses the config's fixture RECORD ids when one is named — the same unit
    ``AskRubricFitness._split_tasks`` partitions on, so the guard covers the
    partition the paid run will actually produce; otherwise a synthetic id
    sample (offline — a real run resolves the dataset's own rows). Prints the
    (train, holdout) sizes; ``partition_task_ids`` raises loudly if either side
    is empty (a tiny/skewed pool is a config error, spec §D3).
    """
    ids = _probe_split_keys(cfg)
    train, holdout = partition_task_ids(ids)
    print(
        f"  split: deterministic sha256 % 2 over {len(ids)} record(s) -> "
        f"train={len(train)}, holdout={len(holdout)}"
    )
    _print_per_prefix_split(train, holdout)


def _print_per_prefix_split(train: Sequence[str], holdout: Sequence[str]) -> None:
    """Echo per-dataset-prefix split counts (design §6.4, small-N safety).

    Combined datasets prefix every task_id (``sweqapro/…``, ``ccv/…``); the
    ccv slice is small enough to skew silently, so both sides are broken
    down by prefix whenever any key is actually prefixed. Silent for
    single-dataset runs (no key carries a ``"/"``) — the plain counts above
    already cover them.

    Groups the SPLIT KEYS, i.e. records. For a combined dataset the record IS
    the prefixed task id, so this is the §6.4 report unchanged; for a framing
    dataset the record is the source row's id, so the grouping names the
    wrapped corpus rather than the wrapper — which is the honest answer to
    "what is actually being partitioned".
    """
    if not any("/" in task_id for task_id in (*train, *holdout)):
        return
    train_counts = count_by_task_prefix(train)
    holdout_counts = count_by_task_prefix(holdout)
    print(f"  split by prefix: train={train_counts}, holdout={holdout_counts}")


def _probe_split_keys(cfg: OptimizeRunConfig) -> Sequence[str]:
    """The keys the split-determinism check partitions on (offline)."""
    if cfg.dataset.fixture_path is None:
        return SPLIT_PROBE_IDS
    dataset = dataset_registry.build(cfg.dataset.name, fixture_path=cfg.dataset.fixture_path)
    return _collect_fixture_record_ids(dataset)


def _collect_fixture_record_ids(dataset: object) -> tuple[str, ...]:
    """Drain a fixture-backed dataset's RECORD ids synchronously (offline read).

    Keyed on the record, never the row's task id, because that is what
    ``AskRubricFitness._split_tasks`` partitions on: a probe reading a
    different key would report — and guard — a partition the paid run will not
    use. Identical for every pre-framing corpus, whose record IS its task id.
    """

    async def _drain() -> tuple[str, ...]:
        names = enumerated_task_names()
        return tuple(
            [
                record_id_of(task, task_names=names)
                async for task in dataset.tasks()  # type: ignore[attr-defined]
            ]
        )

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
        "critique_refine", client=FakeCritiqueClient(replies=[]), fitness=ZeroCostFitness()
    )
    print("    - critique_refine: importable (constructs with FakeCritiqueClient)")
    if cfg.config_search is not None:
        optimizer = build_config_search_optimizer(cfg)
        print(
            f"    - config_search: constructed from run config "
            f"(strategy={optimizer.strategy}, seed={optimizer.seed}, "
            f"{len(optimizer.dimensions)} dimension(s))"
        )
    else:
        optimizer_registry.build("config_search")
        print("    - config_search: importable (dependency-free)")
    _report_skillopt_availability()
    _report_ask_binding_availability()


def _report_skillopt_availability() -> None:
    """Probe ``skillopt`` availability; SKIPPED when the extra is absent."""
    try:
        SkillOptOptimizer(python=Path("python")).ensure_available()
    except RuntimeError as exc:
        print(f"    - skillopt: SKIPPED (extra not installed): {exc}")
        return
    print("    - skillopt: available")


def _report_ask_binding_availability() -> None:
    """Probe the ``[ask]`` extra; SKIPPED never fails a dry run (AC-17)."""
    try:
        ask_binding._require_ask_extra()
    except RuntimeError as exc:
        print(f"    - ask binding: SKIPPED (extra not installed): {exc}")
        return
    print("    - ask binding: available (harness runner constructible)")


def dry_ask_rubric_fitness(
    cfg: OptimizeRunConfig, *, ledger_path: Path
) -> tuple[FakeAskRunner, FakeRubricJudge, AskRubricFitness]:
    """The REAL ask_rubric fitness for the SINGLE IMPLICIT ARM, on the doubles.

    The no-``arms:`` path: one fitness, the top-level ``ask_rubric:`` objective,
    an empty tracked list and the empty arm hash — byte-identical to what every
    pre-``arms:`` config ran.
    """
    assert cfg.ask_rubric is not None
    runner, judge = dry_ask_doubles(cfg.ask_rubric)
    fitness = AskRubricFitness(
        dataset=ProbeDataset(),
        runner_factory=lambda artifact: runner,
        judge=judge,
        rubric=cfg.ask_rubric.rubric_config,
        architecture=cfg.ask_rubric.runner.architecture,
        sample_ledger=SampleRubricLedger(ledger_path.with_suffix(".samples.jsonl")),
        output_dir=ledger_path.parent / "dry-run-samples",
        max_judge_calls=cfg.budget.max_judge_calls,
        rng_seed=cfg.rng_seed,
    )
    return runner, judge, fitness


async def _dry_orchestrator_passes(
    cfg: OptimizeRunConfig,
    seed: OptimizableArtifact,
    arms: Sequence[ResolvedArm],
    *,
    ledger_path: Path,
) -> tuple[OptimizationResult, ...]:
    """One ``run_optimization`` pass per arm (or one implicit pass), spending $0.00.

    The agent-track doubles are constructed to prove they import. Every pass
    shares ONE ``TrialsLedger``, ONE ``BudgetGuard``, ONE ``SampleRubricLedger``
    and ONE ``JudgeCallCounter``, and each gets its own SLICE of
    ``budget.max_trials`` — the four halves of "an arm is a measurement axis,
    never a second budget".
    """
    _ = (FakeAgentRunner(), FakeJudge())
    ledger = TrialsLedger(ledger_path)
    if not arms:
        return (await _implicit_arm_pass(cfg, seed, ledger=ledger, ledger_path=ledger_path),)
    guard = BudgetGuard(ledger=ledger, max_usd=cfg.budget.max_usd)
    sample_ledger = SampleRubricLedger(ledger_path.with_suffix(".samples.jsonl"))
    judge_calls = JudgeCallCounter()
    budget = per_arm_budget(cfg.budget, arm_count=len(arms))
    results = []
    for arm in arms:
        results.append(
            await _one_arm_pass(
                cfg,
                seed,
                arm,
                budget=budget,
                ledger=ledger,
                guard=guard,
                sample_ledger=sample_ledger,
                judge_calls=judge_calls,
                output_dir=ledger_path.parent / "dry-run-samples",
            )
        )
    return tuple(results)


def per_arm_budget(budget: OptimizationBudget, *, arm_count: int) -> OptimizationBudget:
    """Divide the RUN's trial ceiling evenly across ``arm_count`` arms.

    ``max_usd`` and ``max_judge_calls`` stay whole here because they are
    enforced against shared objects (one ledger + guard, one judge counter), so
    handing each arm the run-level number still spends the run-level pool once.
    ``max_trials`` has no shared enforcer — each optimizer consumes it per
    ``optimize()`` call — so passing it whole would make an N-arm run search N
    times the authorized trials. Floored at 1 so a config with more arms than
    trials still runs every arm rather than silently measuring none.
    """
    return replace(budget, max_trials=max(1, budget.max_trials // max(1, arm_count)))


async def _one_arm_pass(
    cfg: OptimizeRunConfig,
    seed: OptimizableArtifact,
    arm: ResolvedArm,
    *,
    budget: OptimizationBudget,
    ledger: TrialsLedger,
    guard: BudgetGuard,
    sample_ledger: SampleRubricLedger,
    judge_calls: JudgeCallCounter,
    output_dir: Path,
) -> OptimizationResult:
    """Score ONE arm end to end on the scripted doubles and report it."""
    runner, judge = dry_ask_doubles(arm.rubric_settings)
    fitness = build_arm_fitness(
        arm,
        cfg=cfg,
        dataset=ProbeDataset(),
        # The dry run deliberately does NOT call ``arm_runner_factory``: that
        # is the seam that imports the harness runtime, and a preflight must
        # stay green on a machine without it.
        runner_factory=lambda artifact: runner,
        judge=judge,
        sample_ledger=sample_ledger,
        output_dir=output_dir,
        judge_calls=judge_calls,
    )
    fitness_by_name: dict[str, object] = {
        r.fitness_name: ZeroCostFitness() for r in cfg.ladder.rungs
    }
    # The name the arm's OBJECTIVE binds — the load-time firewall already
    # proved the ladder's final rung ranks on it, so this install is never a
    # fitness nothing resolves.
    fitness_by_name[objective_fitness_name(arm.cell.scoring.objective)] = fitness
    result = await run_optimization(
        seed,
        SeedEchoOptimizer(),
        cfg.ladder,
        budget,
        fitness_by_name=fitness_by_name,  # type: ignore[arg-type]
        ledger=ledger,
        provenance=dry_provenance(cfg, seed, objective_hash=arm.objective_hash),
        arm_hash=arm.arm_hash,
        guard=guard,
    )
    print(
        f"  orchestrator pass [{arm.label} {arm.arm_hash[:_HASH_ECHO_CHARS]}]: "
        f"{_measured_state(result)}, accepted={result.accepted}, "
        f"runner calls={runner.calls}, judge calls={judge.calls} (scripted fakes, $0.00)"
    )
    return result


def _measured_state(result: OptimizationResult) -> str:
    """Whether this arm was actually measured — never conflate it with rejection.

    Arms share one USD/judge pool first-come-first-served, so a leading arm can
    exhaust it and leave a trailing arm with zero rollouts. That arm returns
    ``accepted=False`` exactly like a measured-and-rejected one; printing the
    distinction is what stops an operator reading "the narrowed arm did not
    beat its seed" off an arm that was never measured at all.
    """
    return "measured" if result.seed_holdout is not None else "NOT MEASURED (budget exhausted)"


async def _implicit_arm_pass(
    cfg: OptimizeRunConfig,
    seed: OptimizableArtifact,
    *,
    ledger: TrialsLedger,
    ledger_path: Path,
) -> OptimizationResult:
    """The no-``arms:`` pass — exactly what every pre-``arms:`` config ran."""
    fitness_by_name: dict[str, object] = {
        r.fitness_name: ZeroCostFitness() for r in cfg.ladder.rungs
    }
    doubles: tuple[FakeAskRunner, FakeRubricJudge] | None = None
    if cfg.ask_rubric is not None and "ask_rubric" in fitness_by_name:
        runner, judge, ask_fitness = dry_ask_rubric_fitness(cfg, ledger_path=ledger_path)
        fitness_by_name["ask_rubric"] = ask_fitness
        doubles = (runner, judge)
    result = await run_optimization(
        seed,
        SeedEchoOptimizer(),
        cfg.ladder,
        cfg.budget,
        fitness_by_name=fitness_by_name,  # type: ignore[arg-type]
        ledger=ledger,
        provenance=dry_provenance(cfg, seed),
    )
    if doubles is not None:
        runner, judge = doubles
        print(
            f"  ask_rubric dry pass: runner calls={runner.calls}, "
            f"judge calls={judge.calls} (scripted fakes, $0.00)"
        )
    print(f"  orchestrator pass: accepted={result.accepted}, total spend: ${result.total_usd:.2f}")
    return result


def dry_provenance(
    cfg: OptimizeRunConfig, seed: OptimizableArtifact, *, objective_hash: str | None = None
) -> Provenance:
    """Synthesize provenance for the dry-run pass (audit shape, no real models).

    ``rubric_hash`` pins the exact objective (spec §3.6/AC-19) whenever the
    config carries one — the same expression every real-run provenance builder
    must use. An arm passes its OWN ``objective_hash``: the arm's ledger rows
    key on the section its ``scoring.rubric`` resolved to, so provenance
    recomputed from the top-level section would name a different objective the
    moment a config carries a second one. ``run_optimization`` stamps
    ``arm_hash``.
    """
    if objective_hash is not None:
        return _provenance(cfg, seed, rubric_hash=objective_hash)
    rubric_hash = None
    if cfg.ask_rubric is not None:
        # The one objective-identity helper the sample ledger and arm identity
        # also fold — provenance computed any other way would name a DIFFERENT
        # objective than the one the ledger keys on.
        rubric_hash = ask_objective_hash(
            cfg.ask_rubric.rubric_config,
            architecture=cfg.ask_rubric.runner.architecture,
        )
    return _provenance(cfg, seed, rubric_hash=rubric_hash)


def _provenance(
    cfg: OptimizeRunConfig, seed: OptimizableArtifact, *, rubric_hash: str | None
) -> Provenance:
    """The dry-run provenance shape, built once for both objective sources."""
    return Provenance(
        seed_fingerprint=seed.fingerprint,
        dataset_revision=cfg.dataset.name,
        model_ids=(),
        optimizer=cfg.optimizer,
        rubric_hash=rubric_hash,
    )


def print_real_run_expectations(cfg: OptimizeRunConfig) -> None:
    """Print the spend expectations + the per-arm plan before a paid run (spec §D5).

    Resolving the arms and BUILDING each runner settings mapping here is
    deliberate: it is the validation a paid run owes before it spends (an arm
    whose ``settings`` re-spell an identity-bearing key, or omit one the
    harness requires, fails NOW rather than at that arm's first rollout — after
    earlier arms already spent). It costs nothing: ``arm_runner_settings`` only
    merges mappings, and the factory it feeds is a closure, so the harness
    runtime is still imported only when a candidate is actually scored.
    """
    print("REAL RUN — this spends real money.")
    print(f"  budget cap: max_usd=${cfg.budget.max_usd:.2f}, max_trials={cfg.budget.max_trials}")
    arms = resolve_arms(cfg)
    print_arm_plan(arms)
    if arms:
        sliced = per_arm_budget(cfg.budget, arm_count=len(arms))
        print(
            f"  per arm: max_trials={sliced.max_trials} (the run's ceiling divided "
            f"{len(arms)} ways); max_usd + max_judge_calls stay ONE shared pool"
        )
    for arm in arms:
        arm_runner_factory(arm)
    print(f"  preflight-first + spend model: see {RUNBOOK_PATH}")
