"""Offline fakes for the GEPA-harness loop tests, exposed via the ``harness`` fixture.

Shared as a dir-level conftest (not a sibling module import) to sidestep the
documented ``tests`` package-name clash (repo-root vs ``benchmarks/tests``, see
``benchmarks/tests/conftest.py``). A ``CampaignFitness`` wired to scripted
``rollout_fn`` + ``derive_fn`` (the test-campaign_fitness idiom), a candidate
ledger factory, a distinct-text fake reflection callable, and gepa-shaped
stopper / selector doubles — no live LLM, no gepa spend.
"""

from __future__ import annotations

import types
from collections.abc import Sequence
from pathlib import Path

import pytest

from pydocs_eval.campaign.budget import BudgetGuard
from pydocs_eval.campaign.cells import screening_cells
from pydocs_eval.campaign.ledger import LEDGER_FILENAME as CAMPAIGN_LEDGER_FILENAME
from pydocs_eval.campaign.ledger import WorkItem
from pydocs_eval.campaign.lockfile import (
    CampaignLockfile,
    HostFingerprint,
    RolloutCaps,
    claude_direct_pin,
)
from pydocs_eval.campaign.runner import RolloutOutcome
from pydocs_eval.optimize.candidates.ledger import LEDGER_FILENAME, CandidateLedger
from pydocs_eval.optimize.fitness.campaign import CampaignFitness
from pydocs_eval.trajectory.consumers import DerivedRecord

_ = CAMPAIGN_LEDGER_FILENAME  # (import kept explicit for symmetry; unused directly)
_CELL = "indexed_sugg-on_inj-off"


def _lockfile_for(artifact_hash: str) -> CampaignLockfile:
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


def _derived(instance_id: str, *, soft: float = 0.5) -> DerivedRecord:
    """One scripted Phase 2 record; soft < 1.0 so gepa never skips it as 'perfect'."""
    return DerivedRecord(
        trajectory_id=f"t-{instance_id}",
        instance_id=instance_id,
        hard=0,
        soft=soft,
        components={"localization": soft},
        label="wrong_fix",
        feedback=f"feedback-{instance_id}",
        fail_reason="wrong_fix",
        cost_usd=1.0,
        score_version=2,
        taxonomy_version=3,
        schema_version=1,
        artifact_hash="a" * 64,
        run_config_ref="rc",
        excluded_from_aggregates=False,
    )


def _make_fitness(
    tmp_path: Path,
    instances: Sequence[str],
    *,
    soft: float = 0.5,
    guard: BudgetGuard | None = None,
    rollout_raises: bool = False,
) -> CampaignFitness:
    async def rollout_fn(item: WorkItem) -> RolloutOutcome:
        if rollout_raises:
            raise AssertionError(f"rollout_fn ran for {item.instance_id!r} — R3 firewall breached")
        return RolloutOutcome(trajectory_id=f"t-{item.instance_id}", cost_usd=1.0, is_infra=False)

    def derive_fn(item: WorkItem, _outcome: RolloutOutcome) -> DerivedRecord | None:
        return _derived(item.instance_id, soft=soft)

    return CampaignFitness(
        build_lockfile=_lockfile_for,
        guard=guard or BudgetGuard(cost_ceiling_usd=1000.0, assumed_cost_on_raise=0.5),
        instances=tuple(instances),
        cell=_CELL,
        rollout_fn=rollout_fn,
        derive_fn=derive_fn,
        workspace=tmp_path,
        concurrency=1,
    )


def _make_ledger(tmp_path: Path) -> CandidateLedger:
    return CandidateLedger(tmp_path / LEDGER_FILENAME)


class FakeReflection:
    """Scripted reflection callable — DISTINCT fenced rewrites, no real LM.

    gepa's ``StatelessReflectionLM`` extracts the text between the first and last
    ``` fences (``InstructionProposalSignature.output_extractor``); the counter
    keeps successive candidate hashes distinct.
    """

    def __init__(self, prefix: str = "Refined server guidance revision") -> None:
        self.prefix = prefix
        self.calls = 0

    def __call__(self, prompt: str | list) -> str:
        self.calls += 1
        return f"```\n{self.prefix} number {self.calls} with ample instructional prose.\n```"


class CountingStopper:
    """A gepa ``StopperProtocol`` that halts after ``max_iters`` engine checks."""

    def __init__(self, max_iters: int) -> None:
        self.max_iters = max_iters
        self.calls = 0

    def __call__(self, *_args: object, **_kwargs: object) -> bool:
        self.calls += 1
        return self.calls > self.max_iters


def _always_server(*_args: object, **_kwargs: object) -> list[str]:
    """Deterministic module selector: always mutate SERVER_INSTRUCTIONS (stays valid)."""
    return ["SERVER_INSTRUCTIONS"]


@pytest.fixture
def harness() -> types.SimpleNamespace:
    """Bundle the offline fakes so a test pulls exactly what it needs by name."""
    return types.SimpleNamespace(
        make_fitness=_make_fitness,
        make_ledger=_make_ledger,
        lockfile_for=_lockfile_for,
        derived=_derived,
        FakeReflection=FakeReflection,
        CountingStopper=CountingStopper,
        always_server=_always_server,
    )
