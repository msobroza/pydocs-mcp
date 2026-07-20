"""CampaignFitness — the ``run_campaign`` → ``FitnessReport`` bridge (ADR 0017 §3).

The core Phase 4 build item: a :class:`~pydocs_eval.optimize.protocols.FitnessFunction`
that turns ONE GEPA-view candidate into a paired campaign result. The loop is:

1. render the candidate to a campaign-owned directory (the Route A document);
2. build a per-candidate campaign lockfile with the candidate's serve-truthful
   ``artifact_hash`` folded in, so a distinct candidate ⇒ a distinct
   ``campaign_id`` (ADR 0017 §Decision 6 = R5 verbatim: one lockfile per candidate);
3. run :func:`~pydocs_eval.campaign.runner.run_campaign` over the configured
   instance list through the INJECTED ``rollout_fn`` seam (fake offline, real in
   the paid arc);
4. PROJECT the Phase 2 ``DerivedRecord`` outputs — ``gepa_pair`` per instance
   (score, feedback) for the reflective dataset + ``run_aggregate`` for the
   :class:`~pydocs_eval._types.FitnessReport`.

**Projects, never re-derives (ADR 0017 §Evidence).** The single-source metric
rule (``trajectory/consumers.py``) is the whole contract of this bridge: every
score/feedback/infra-carve-out comes from the ONE ``compute_derived_record``
computation, reached ONLY through the sanctioned ``consumers`` projection layer.
The bridge imports NONE of the re-derivation primitives (``shaped_score`` /
``metrics`` / ``taxonomy`` / ``feedback`` / ``attribution``) — pinned by an
import-graph test mirroring ``test_gate.py``'s walker, with ``consumers`` as the
sanctioned single-source boundary the bridge routes through.

The Phase 2 boundary itself is an injected ``derive_fn`` seam: given a completed
rollout it returns that rollout's ``DerivedRecord`` (in the paid arc it reads the
trajectory and calls ``compute_derived_record``; offline it is a scripted
double). Keeping it injected is what keeps the bridge free of the scoring
primitives — the bridge only ever calls the projections.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydocs_eval.campaign.budget import BudgetGuard, HaltReason
from pydocs_eval.campaign.ledger import LEDGER_FILENAME, CampaignLedger, WorkItem, build_work
from pydocs_eval.campaign.lockfile import CampaignLockfile, write_lockfile
from pydocs_eval.campaign.runner import RolloutOutcome, run_campaign
from pydocs_eval.optimize._types import FitnessReport
from pydocs_eval.optimize.candidates.candidate import Candidate

# The single-source PROJECTION layer (ADR 0012). The bridge reaches scoring ONLY
# through these three symbols — it never imports shaped_score / metrics /
# taxonomy / feedback / attribution, so it cannot re-derive a score.
from pydocs_eval.trajectory.consumers import DerivedRecord, gepa_pair, run_aggregate

# The rendered candidate document Route A points ``PYDOCS_SERVE__DESCRIPTIONS_PATH``
# at (written into the campaign root beside the lockfile).
CANDIDATE_DOCUMENT_FILENAME = "descriptions.md"

# Injected Phase 2 boundary: map a completed rollout to its DerivedRecord, or
# ``None`` when the rollout produced no gradeable trajectory (a spawn crash).
DeriveFn = Callable[[WorkItem, RolloutOutcome], DerivedRecord | None]
RolloutFn = Callable[[WorkItem], Awaitable[RolloutOutcome]]
# Given a candidate's serve-truthful artifact_hash → its per-candidate lockfile
# (the hash folded into the block ⇒ a distinct campaign_id). Injected so the
# bridge stays ignorant of the campaign's fixed plumbing fields (DI idiom).
BuildLockfile = Callable[[str], CampaignLockfile]


@dataclass(frozen=True, slots=True)
class InstanceScore:
    """One instance's projected ``(score, feedback)`` pair (``consumers.gepa_pair``).

    This is the per-exemplar material the GEPA reflective dataset consumes
    (ADR 0019): ``score`` is the shaped soft score, ``feedback`` the bounded
    Phase 2 fact string.
    """

    instance_id: str
    score: float
    feedback: str


@dataclass(frozen=True, slots=True)
class CampaignFitnessResult:
    """A candidate's full campaign evaluation — the aggregate + the per-instance view.

    ``report`` is the ``FitnessReport`` the ladder ranks on; ``per_instance`` is
    the ``gepa_pair`` projection for the reflective dataset; ``campaign_id`` binds
    the result to its one-per-candidate lockfile; ``halt_reason`` surfaces a
    budget halt so a truncated run is not read as a complete one.
    """

    report: FitnessReport
    per_instance: tuple[InstanceScore, ...]
    campaign_id: str
    halt_reason: HaltReason


@dataclass(frozen=True, slots=True)
class CampaignFitness:
    """Bridge one candidate onto the campaign runner and project the outputs.

    Every expensive dependency is injected: ``rollout_fn`` is the campaign
    runner's rollout seam (fake offline), ``derive_fn`` the Phase 2 boundary,
    ``build_lockfile`` the per-candidate lockfile factory. ``workspace`` roots the
    per-candidate campaign directories (one subdir per ``campaign_id``).
    """

    build_lockfile: BuildLockfile
    guard: BudgetGuard
    instances: Sequence[str]
    cell: str
    rollout_fn: RolloutFn
    derive_fn: DeriveFn
    workspace: Path
    concurrency: int = 4
    name: str = "campaign"
    cost_tier: Literal["free", "paid"] = "paid"

    async def evaluate(
        self, artifact: object, *, split: Literal["train", "holdout"]
    ) -> FitnessReport:
        """``FitnessFunction`` entry: score an artifact's rendered document (ADR 0017 §3).

        Parses the artifact's rendered document into the GEPA-view candidate so
        the lockfile's ``artifact_hash`` is the SERVE-truthful ``candidate_hash``
        (matching the trace header), then delegates to :meth:`evaluate_candidate`.
        ``split`` is accepted for Protocol parity; the campaign's split is fixed
        by its instance list, not chosen per call.
        """
        candidate = Candidate.from_document(artifact.render())  # type: ignore[attr-defined]
        return (await self.evaluate_candidate(candidate)).report

    async def evaluate_candidate(self, candidate: Candidate) -> CampaignFitnessResult:
        """Run the candidate's campaign and project the Phase 2 outputs.

        Renders the candidate to its campaign directory, writes the per-candidate
        lockfile, drives ``run_campaign`` over the instance list, and projects the
        collected ``DerivedRecord``s. NEVER re-derives a score — projection only.
        """
        lockfile = self.build_lockfile(candidate.candidate_hash)
        root = self._prepare_campaign_dir(lockfile, candidate)
        run = await self._run(root)
        return _project(run, campaign_id=lockfile.campaign_id)

    def _prepare_campaign_dir(self, lockfile: CampaignLockfile, candidate: Candidate) -> Path:
        """Create the per-``campaign_id`` root; write the Route A document + lockfile."""
        root = self.workspace / lockfile.campaign_id
        root.mkdir(parents=True, exist_ok=True)
        (root / CANDIDATE_DOCUMENT_FILENAME).write_text(candidate.render(), encoding="utf-8")
        write_lockfile(root, lockfile)
        return root

    async def _run(self, root: Path) -> _CollectedRun:
        """Drive ``run_campaign`` over the instances, collecting DerivedRecords."""
        ledger = CampaignLedger(root / LEDGER_FILENAME)
        work = build_work([self.cell], list(self.instances))
        collector = _RecordCollector(rollout_fn=self.rollout_fn, derive_fn=self.derive_fn)
        result = await run_campaign(
            work,
            ledger=ledger,
            guard=self.guard,
            rollout_fn=collector,
            concurrency=self.concurrency,
        )
        return _CollectedRun(records=collector.records, halt_reason=result.halt_reason)


@dataclass(slots=True)
class _RecordCollector:
    """Wrap the injected ``rollout_fn`` to collect each rollout's DerivedRecord.

    The runner only returns per-state tallies, not outcomes, so this captures the
    ``DerivedRecord`` on the way through: run the real rollout, derive its record
    via the Phase 2 seam, store it keyed by ``instance_id`` (last attempt wins),
    and return the outcome unchanged to the runner. A ``None`` derive (a spawn
    crash with no trajectory) is simply not stored.
    """

    rollout_fn: RolloutFn
    derive_fn: DeriveFn
    records: dict[str, DerivedRecord] = field(default_factory=dict, init=False)

    async def __call__(self, item: WorkItem) -> RolloutOutcome:
        outcome = await self.rollout_fn(item)
        derived = self.derive_fn(item, outcome)
        if derived is not None:
            self.records[item.instance_id] = derived
        return outcome


@dataclass(frozen=True, slots=True)
class _CollectedRun:
    """The collected records + why the run stopped (kept off the return signature)."""

    records: dict[str, DerivedRecord]
    halt_reason: HaltReason


def _project(run: _CollectedRun, *, campaign_id: str) -> CampaignFitnessResult:
    """Project the collected DerivedRecords onto the aggregate + per-instance view.

    Uses ``consumers.run_aggregate`` (the infra-carve-out-aware mean) for the
    ``FitnessReport`` and ``consumers.gepa_pair`` for each instance's
    ``(score, feedback)``. Pure projection — no score is computed here.
    """
    records = list(run.records.values())
    aggregate = run_aggregate(records)
    report = FitnessReport(**aggregate.to_fitness_report_dict())
    per_instance = tuple(_instance_score(record) for record in records)
    return CampaignFitnessResult(
        report=report,
        per_instance=per_instance,
        campaign_id=campaign_id,
        halt_reason=run.halt_reason,
    )


def _instance_score(record: DerivedRecord) -> InstanceScore:
    """One record → its ``(score, feedback)`` projection (``consumers.gepa_pair``)."""
    score, feedback = gepa_pair(record)
    return InstanceScore(instance_id=record.instance_id, score=score, feedback=feedback)
