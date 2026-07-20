"""Pre-freeze serving-field divergence comparator (ADR 0020 §Pre-test re-validation):
a serving change triggers re-validation; a non-serving change does not."""

from __future__ import annotations

from pydocs_eval.agent_track._types import ArmConfig
from pydocs_eval.campaign.cells import CellConfig, screening_cells
from pydocs_eval.campaign.lockfile import (
    CampaignLockfile,
    HostFingerprint,
    RolloutCaps,
    claude_direct_pin,
)
from pydocs_eval.optimize.lockfile_divergence import compare_serving_fields


def _lockfile(**overrides) -> CampaignLockfile:
    base = dict(
        dataset_pins={"dev_val": {"revision": "abc"}},
        split_hashes={"dev.txt": "d" * 64, "val.txt": "v" * 64},
        cells=screening_cells(),
        host=HostFingerprint(hostname="h", arch="x86_64", os="Linux 6"),
        provider="anthropic",
        billing_mode="api_key_metered",
        provider_pin=claude_direct_pin(
            anthropic_version="2023-06-01",
            pricing_snapshot={"claude-sonnet-5": {"input": 3.0, "output": 15.0}},
        ),
        caps=RolloutCaps(max_turns=40, wall_seconds=900.0),
        cost_ceiling_usd=100.0,
        assumed_cost_on_raise=0.5,
        schema_version=1,
        score_version=2,
        taxonomy_version=3,
        artifact_hash="a" * 64,
    )
    base.update(overrides)
    return CampaignLockfile(**base)


def _dict(**overrides) -> dict:
    return _lockfile(**overrides).to_dict()


def test_identical_lockfiles_do_not_diverge() -> None:
    result = compare_serving_fields(_dict(), _dict())
    assert result.diverged is False
    assert result.fields == ()


def test_artifact_hash_change_triggers_revalidation() -> None:
    result = compare_serving_fields(_dict(), _dict(artifact_hash="b" * 64))
    assert result.diverged is True
    assert result.fields == ("artifact_hash",)


def test_provider_pin_change_triggers_revalidation() -> None:
    other_pin = claude_direct_pin(
        anthropic_version="2024-10-01",  # version bump = serving divergence
        pricing_snapshot={"claude-sonnet-5": {"input": 3.0, "output": 15.0}},
    )
    result = compare_serving_fields(_dict(), _dict(provider_pin=other_pin))
    assert result.fields == ("provider_pin",)


def test_model_swap_on_a_cell_triggers_revalidation() -> None:
    swapped = (CellConfig(name="bare", arm=ArmConfig(name="bare", model="claude-opus-4-8")),)
    result = compare_serving_fields(_dict(), _dict(cells=swapped))
    assert "model_ids" in result.fields


def test_non_serving_change_does_not_trigger_revalidation() -> None:
    """A cost-ceiling / dataset-pin change is a new campaign ID but NOT a serving
    divergence — the val numbers are still valid, no re-run needed."""
    result = compare_serving_fields(
        _dict(), _dict(cost_ceiling_usd=250.0, dataset_pins={"dev_val": {"revision": "zzz"}})
    )
    assert result.diverged is False


def test_multiple_divergences_are_reported_sorted() -> None:
    result = compare_serving_fields(
        _dict(), _dict(artifact_hash="b" * 64, provider="other-provider")
    )
    assert result.fields == ("artifact_hash", "provider")
