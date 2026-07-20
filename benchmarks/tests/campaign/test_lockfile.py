"""Campaign lockfile: campaign ID = canonical-JSON hash; any field change ⇒ new ID."""

from __future__ import annotations

import dataclasses
import json

import pytest

from pydocs_eval.campaign.cells import screening_cells
from pydocs_eval.campaign.lockfile import (
    LOCKFILE_FILENAME,
    CampaignLockfile,
    HostFingerprint,
    ProviderPin,
    RolloutCaps,
    capture_host_fingerprint,
    claude_direct_pin,
    split_file_hashes,
    write_lockfile,
)


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
            pricing_snapshot={"claude-haiku-4-5": {"input": 1.0, "output": 5.0}},
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


def test_campaign_id_is_64_hex_and_stable() -> None:
    lf = _lockfile()
    assert len(lf.campaign_id) == 64
    assert lf.campaign_id == _lockfile().campaign_id  # deterministic


def test_field_change_yields_new_campaign_id() -> None:
    base = _lockfile()
    bumped = _lockfile(cost_ceiling_usd=200.0)
    assert bumped.campaign_id != base.campaign_id


def test_cell_change_yields_new_campaign_id() -> None:
    base = _lockfile()
    fewer = _lockfile(cells=screening_cells()[:3])
    assert fewer.campaign_id != base.campaign_id


def test_assumed_cost_on_raise_is_part_of_campaign_id(tmp_path) -> None:
    # Finding 1(b): assumed_cost_on_raise is a required budget field that hashes
    # into the campaign identity — changing the raise-backstop is a new campaign,
    # and it must appear in the written lockfile document.
    base = _lockfile()
    bumped = _lockfile(assumed_cost_on_raise=1.5)
    assert bumped.campaign_id != base.campaign_id
    doc = json.loads(write_lockfile(tmp_path, base).read_text())
    assert doc["assumed_cost_on_raise"] == 0.5


def test_subscription_billing_rejected() -> None:
    with pytest.raises(ValueError, match="api_key_metered"):
        _lockfile(billing_mode="subscription")


def test_empty_cells_rejected() -> None:
    with pytest.raises(ValueError, match="at least one cell"):
        _lockfile(cells=())


def test_write_lockfile_embeds_matching_campaign_id(tmp_path) -> None:
    lf = _lockfile()
    path = write_lockfile(tmp_path, lf)
    assert path.name == LOCKFILE_FILENAME
    doc = json.loads(path.read_text())
    assert doc["campaign_id"] == lf.campaign_id


def test_split_file_hashes_match_sha256(tmp_path) -> None:
    (tmp_path / "dev.txt").write_text("i1\ni2\n")
    hashes = split_file_hashes({"dev.txt": tmp_path / "dev.txt"})
    assert len(hashes["dev.txt"]) == 64


def test_split_file_hash_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="split file to hash is missing"):
        split_file_hashes({"dev.txt": tmp_path / "nope.txt"})


def test_capture_host_fingerprint_populates_fields() -> None:
    fp = capture_host_fingerprint()
    assert fp.hostname and fp.arch and fp.os


def test_host_change_yields_new_campaign_id() -> None:
    base = _lockfile()
    other = _lockfile(host=dataclasses.replace(base.host, arch="aarch64"))
    assert other.campaign_id != base.campaign_id


def test_claude_direct_pin_records_static_verified_facts() -> None:
    # ADR 0015 §Decision: with no router and no fallback path, provider change is
    # impossible without a new lockfile — these are facts, not tunable knobs.
    pin = claude_direct_pin(anthropic_version="2023-06-01", pricing_snapshot={})
    d = pin.to_dict()
    assert d["router"] == "none"
    assert d["fallbacks"] == "structurally_absent"
    assert d["quantization"] == "n/a"  # verified undisclosed on every Claude endpoint
    assert d["auth"] == "api_key"
    assert d["base_url"] == "default"
    assert d["anthropic_version"] == "2023-06-01"


def test_provider_pin_is_part_of_campaign_id() -> None:
    base = _lockfile()
    repinned = _lockfile(
        provider_pin=claude_direct_pin(
            anthropic_version="2023-06-01",
            pricing_snapshot={"claude-sonnet-5": {"input": 3.0, "output": 15.0}},
        )
    )
    assert repinned.campaign_id != base.campaign_id  # a repricing is a new campaign


def test_provider_pin_appears_in_written_lockfile(tmp_path) -> None:
    lf = _lockfile()
    doc = json.loads(write_lockfile(tmp_path, lf).read_text())
    assert doc["provider_pin"]["router"] == "none"
    assert "pricing_snapshot" in doc["provider_pin"]


def test_non_default_pin_facts_are_rejected() -> None:
    # A router or fallback path would make R7 a config discipline, not structural.
    with pytest.raises(ValueError, match="router"):
        ProviderPin(anthropic_version="2023-06-01", pricing_snapshot={}, router="openrouter")
