"""CampaignFitness bridge (ADR 0017 §3): render → per-candidate lockfile →
run_campaign → PROJECT (never re-derive) the Phase 2 outputs.

The bridge imports ``pydocs_mcp`` transitively (via the GEPA-view candidate), so
these are [retrieval]-extra tests — same as the sibling candidate tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pydocs_eval
import pytest

from pydocs_eval.campaign.budget import BudgetGuard, HaltReason
from pydocs_eval.campaign.cells import screening_cells
from pydocs_eval.campaign.lockfile import (
    LOCKFILE_FILENAME,
    CampaignLockfile,
    HostFingerprint,
    RolloutCaps,
    claude_direct_pin,
)
from pydocs_eval.campaign.ledger import WorkItem
from pydocs_eval.campaign.runner import RolloutOutcome
from pydocs_eval.optimize._types import FitnessReport
from pydocs_eval.optimize.candidates.candidate import Candidate
from pydocs_eval.optimize.fitness.campaign import (
    CANDIDATE_DOCUMENT_FILENAME,
    CampaignFitness,
)
from pydocs_eval.trajectory.consumers import DerivedRecord


def _lockfile_for(artifact_hash: str) -> CampaignLockfile:
    """A minimal per-candidate lockfile with the candidate hash folded in."""
    return CampaignLockfile(
        dataset_pins={"dev_val": {"revision": "abc"}},
        split_hashes={"dev.txt": "d" * 64},
        cells=screening_cells(),
        host=HostFingerprint(hostname="h", arch="x86_64", os="Linux 6"),
        provider="anthropic",
        billing_mode="api_key_metered",
        provider_pin=claude_direct_pin(anthropic_version="2023-06-01", pricing_snapshot={}),
        caps=RolloutCaps(max_turns=40, wall_seconds=900.0),
        cost_ceiling_usd=100.0,
        assumed_cost_on_raise=0.5,
        schema_version=1,
        score_version=2,
        taxonomy_version=3,
        artifact_hash=artifact_hash,
    )


def _derived(
    instance_id: str, *, soft: float, excluded: bool = False, cost: float = 1.0
) -> DerivedRecord:
    return DerivedRecord(
        trajectory_id=f"t-{instance_id}",
        instance_id=instance_id,
        hard=1,
        soft=soft,
        components={"localization": soft},
        label="resolved",
        feedback=f"feedback-{instance_id}",
        fail_reason="",
        cost_usd=cost,
        score_version=2,
        taxonomy_version=3,
        schema_version=1,
        artifact_hash="a" * 64,
        run_config_ref="rc",
        excluded_from_aggregates=excluded,
    )


def _fitness(
    tmp_path: Path,
    *,
    instances: tuple[str, ...],
    records: dict[str, DerivedRecord],
    guard: BudgetGuard | None = None,
    rollout_cost: float = 1.0,
) -> CampaignFitness:
    async def rollout_fn(item: WorkItem) -> RolloutOutcome:
        return RolloutOutcome(
            trajectory_id=f"t-{item.instance_id}", cost_usd=rollout_cost, is_infra=False
        )

    def derive_fn(item: WorkItem, _outcome: RolloutOutcome) -> DerivedRecord | None:
        return records.get(item.instance_id)

    return CampaignFitness(
        build_lockfile=_lockfile_for,
        guard=guard or BudgetGuard(cost_ceiling_usd=100.0, assumed_cost_on_raise=0.5),
        instances=instances,
        cell="indexed_sugg-on_inj-off",
        rollout_fn=rollout_fn,
        derive_fn=derive_fn,
        workspace=tmp_path,
        concurrency=1,
    )


async def test_evaluate_candidate_projects_aggregate_and_per_instance(tmp_path: Path) -> None:
    """The bridge projects run_aggregate → FitnessReport + gepa_pair per instance."""
    records = {"i1": _derived("i1", soft=0.4), "i2": _derived("i2", soft=0.8)}
    fitness = _fitness(tmp_path, instances=("i1", "i2"), records=records)
    result = await fitness.evaluate_candidate(Candidate.seed())
    assert isinstance(result.report, FitnessReport)
    assert result.report.score == pytest.approx(0.6)  # mean of graded soft scores
    assert result.report.n_samples == 2
    scores = {s.instance_id: (s.score, s.feedback) for s in result.per_instance}
    assert scores == {"i1": (0.4, "feedback-i1"), "i2": (0.8, "feedback-i2")}
    assert result.halt_reason is HaltReason.COMPLETED


async def test_candidate_document_and_lockfile_written_to_campaign_dir(tmp_path: Path) -> None:
    """Route A document + lockfile land in the per-campaign_id directory."""
    records = {"i1": _derived("i1", soft=0.5)}
    fitness = _fitness(tmp_path, instances=("i1",), records=records)
    result = await fitness.evaluate_candidate(Candidate.seed())
    campaign_dir = tmp_path / result.campaign_id
    assert (campaign_dir / CANDIDATE_DOCUMENT_FILENAME).read_text() == Candidate.seed().render()
    assert (campaign_dir / LOCKFILE_FILENAME).is_file()


async def test_distinct_candidates_get_distinct_campaign_ids(tmp_path: Path) -> None:
    """Candidate artifact_hash folded into the lockfile ⇒ distinct campaign_id (R5)."""
    seed = Candidate.seed()
    mutated = Candidate.from_gepa(
        {**seed.sections, "SERVER_INSTRUCTIONS": seed.sections["SERVER_INSTRUCTIONS"] + "\nextra"}
    )
    records = {"i1": _derived("i1", soft=0.5)}
    fitness = _fitness(tmp_path, instances=("i1",), records=records)
    id_seed = (await fitness.evaluate_candidate(seed)).campaign_id
    id_mut = (await fitness.evaluate_candidate(mutated)).campaign_id
    assert seed.candidate_hash != mutated.candidate_hash
    assert id_seed != id_mut


async def test_infra_excluded_records_drop_from_aggregate(tmp_path: Path) -> None:
    """An infra-excluded DerivedRecord is carved out of the scored aggregate (ADR 0012)."""
    records = {"i1": _derived("i1", soft=0.4), "i2": _derived("i2", soft=0.0, excluded=True)}
    fitness = _fitness(tmp_path, instances=("i1", "i2"), records=records)
    result = await fitness.evaluate_candidate(Candidate.seed())
    assert result.report.n_samples == 1  # only the graded record scores
    assert result.report.score == pytest.approx(0.4)


async def test_budget_halt_is_surfaced(tmp_path: Path) -> None:
    """A ceiling reached mid-run surfaces HALTED_BY_GUARD, not a silent complete."""
    records = {i: _derived(i, soft=0.5) for i in ("i1", "i2", "i3")}
    guard = BudgetGuard(cost_ceiling_usd=1.0, assumed_cost_on_raise=0.5)
    fitness = _fitness(
        tmp_path, instances=("i1", "i2", "i3"), records=records, guard=guard, rollout_cost=1.0
    )
    result = await fitness.evaluate_candidate(Candidate.seed())
    assert result.halt_reason is HaltReason.HALTED_BY_GUARD
    assert result.report.n_samples < 3  # not every instance ran


# --------------------------------------------------------------------------
# The "projects, never re-derives" import lock (mirrors test_gate.py's walker)
# --------------------------------------------------------------------------

_PKG_ROOT = Path(pydocs_eval.__file__).parent
_PKG_PREFIX = "pydocs_eval."
# The re-derivation primitives the bridge must never reach EXCEPT through the
# sanctioned single-source ``consumers`` projection layer (ADR 0012).
_FORBIDDEN = {
    "pydocs_eval.trajectory.shaped_score",
    "pydocs_eval.trajectory.metrics",
    "pydocs_eval.trajectory.taxonomy",
    "pydocs_eval.trajectory.feedback",
    "pydocs_eval.trajectory.attribution",
}
# Sanctioned boundaries: the bridge routes scoring THROUGH ``consumers`` (the one
# projection layer), so it is recorded but not expanded — expanding it would
# defeat the single-source design it is meant to enforce.
_SANCTIONED_BOUNDARIES = {"pydocs_eval.trajectory.consumers", "pydocs_eval.trajectory.gate"}


def _module_source(dotted: str) -> Path | None:
    rel = dotted.removeprefix(_PKG_PREFIX).replace(".", "/")
    for candidate in (_PKG_ROOT / f"{rel}.py", _PKG_ROOT / rel / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _pydocs_imports_in(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return {name for name in names if name.startswith(_PKG_PREFIX)}


def _reachable(root: Path) -> set[str]:
    seen: set[str] = set()
    frontier = _pydocs_imports_in(root)
    while frontier:
        module = frontier.pop()
        if module in seen:
            continue
        seen.add(module)
        if module in _SANCTIONED_BOUNDARIES:
            continue  # route through the single-source projection; do not expand
        source = _module_source(module)
        if source is not None:
            frontier |= _pydocs_imports_in(source) - seen
    return seen


def test_bridge_reaches_scoring_only_through_consumers() -> None:
    """No re-derivation primitive is reachable except through the consumers seam."""
    from pydocs_eval.optimize.fitness import campaign as bridge_mod

    reachable = _reachable(Path(bridge_mod.__file__))
    assert reachable.isdisjoint(_FORBIDDEN), reachable & _FORBIDDEN
    # Routes through the sanctioned single-source projection...
    assert "pydocs_eval.trajectory.consumers" in reachable
    # ...and the walker is not vacuous — it follows edges >1 hop deep (the bridge
    # does not directly import _retrieval_extra; candidates.candidate does).
    assert "pydocs_eval._retrieval_extra" in reachable


def test_bridge_never_directly_imports_a_scoring_primitive() -> None:
    """The bridge's own source imports no re-derivation primitive directly."""
    from pydocs_eval.optimize.fitness import campaign as bridge_mod

    assert _pydocs_imports_in(Path(bridge_mod.__file__)).isdisjoint(_FORBIDDEN)
