"""Campaign lockfile — the immutable campaign identity (ADR 0016 §Campaign mechanics).

Extends the Phase 2 run-config lockfile (``trajectory/rollout.py``
``build_run_config`` / ``run_config_hash``) with the campaign-scoped fields ADR
0016 and ADR 0014 add:

- dataset snapshot pins + split-file sha256s (ADR 0013),
- cell definitions (ADR 0016 §Stage 1),
- host fingerprint captured at launch (ADR 0014 item 6),
- provider + billing mode + the Claude-direct provider pin (auth / base_url /
  router / fallbacks / quantization / anthropic-version / pricing snapshot) (ADR 0015),
- per-rollout caps (turns, wall) + campaign cost ceiling (R6),
- metric / score / taxonomy versions + artifact hash (ADR 0009–0012).

The canonical-JSON sha256 over the whole block IS the campaign ID: any field
change yields a new ID (R5 — a changed campaign is a new campaign). The hash
reuses the ``run_config_hash`` idiom (sorted-key canonical JSON) so it is
order-independent and byte-stable on re-serialization.
"""

from __future__ import annotations

import hashlib
import platform
import socket
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_eval.campaign.cells import CellConfig
from pydocs_eval.trajectory.blob_store import canonical_json

# The lockfile filename the runner writes into the campaign root.
LOCKFILE_FILENAME = "campaign.lock.json"

# The ONLY billing mode a campaign may run under (ADR 0014/0015, R6): API-key
# auth is metered per token, so per-rollout ``total_cost_usd`` sums to a
# provider-billable number. A subscription-quota login cannot be reconciled per
# rollout, so it is rejected here rather than silently accepted into a lockfile
# whose spend numbers would then be unverifiable.
_VALID_BILLING_MODES = ("api_key_metered",)


@dataclass(frozen=True, slots=True)
class HostFingerprint:
    """The host identity captured at campaign launch (ADR 0014 item 6).

    ``hostname`` / ``arch`` / ``os`` pin WHERE a campaign ran; recorded so a
    resumed or re-analyzed campaign can prove it ran on the provisioned x86_64
    host, not the arm64 dev machine (ADR 0014 §Evidence).
    """

    hostname: str
    arch: str
    os: str

    def to_dict(self) -> dict[str, object]:
        return {"hostname": self.hostname, "arch": self.arch, "os": self.os}


def capture_host_fingerprint() -> HostFingerprint:
    """Snapshot the current host's identity (hostname / arch / os).

    Called once at launch by the runner; the result is frozen into the lockfile
    so it is part of the campaign ID.
    """
    return HostFingerprint(
        hostname=socket.gethostname(),
        arch=platform.machine(),
        os=f"{platform.system()} {platform.release()}",
    )


@dataclass(frozen=True, slots=True)
class RolloutCaps:
    """Per-rollout budget caps (R6). ``max_turns`` is the live cap (headless
    ``claude`` records token/wall caps as ``null``); ``wall_seconds`` bounds one
    rollout's spawn. Both feed the taxonomy ``budget_exhausted`` predicate."""

    max_turns: int
    wall_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {"max_turns": self.max_turns, "wall_seconds": self.wall_seconds}


# The Claude-direct plumbing R5/R7 pin to. These are VERIFIED FACTS, not knobs
# (ADR 0015 §Evidence, §Decision): the headless loop drives only Claude, exposes
# no router/fallback/quantization/base-url lever for the target arm, so any other
# value would be a fiction — rejected at construction so R7 holds structurally,
# not by config discipline.
_PINNED_PROVIDER_FACTS = {
    "auth": "api_key",
    "base_url": "default",
    "router": "none",
    "fallbacks": "structurally_absent",
    "quantization": "n/a",
}


@dataclass(frozen=True, slots=True)
class ProviderPin:
    """The Claude-direct provider pinning block (ADR 0015 §Decision).

    Records the model-plumbing facts R5/R7 fold into the campaign identity. Every
    field except ``pricing_snapshot`` is a static verified fact (no router, no
    fallback path — so a provider change is impossible without a new lockfile,
    i.e. a new campaign ID). ``pricing_snapshot`` is the one measured slot: the
    probe-confirmed price table (standard vs the Sonnet intro window) the budget
    was computed against. ``anthropic_version`` pins the API version header.

    Raises:
        ValueError: if any static fact is set to a value other than the single
            pinned option (e.g. ``router="openrouter"``) — that would reopen R7
            as config discipline instead of structure.
    """

    anthropic_version: str
    pricing_snapshot: Mapping[str, object]
    auth: str = _PINNED_PROVIDER_FACTS["auth"]
    base_url: str = _PINNED_PROVIDER_FACTS["base_url"]
    router: str = _PINNED_PROVIDER_FACTS["router"]
    fallbacks: str = _PINNED_PROVIDER_FACTS["fallbacks"]
    quantization: str = _PINNED_PROVIDER_FACTS["quantization"]

    def __post_init__(self) -> None:
        for name, pinned in _PINNED_PROVIDER_FACTS.items():
            got = getattr(self, name)
            if got != pinned:
                raise ValueError(
                    f"ProviderPin.{name}={got!r} is not the pinned Claude-direct value "
                    f"{pinned!r}; R7 is satisfied structurally (no router/fallback) — "
                    "a non-Claude-direct value is a Phase 4 reopener, not a lockfile field"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "auth": self.auth,
            "base_url": self.base_url,
            "router": self.router,
            "fallbacks": self.fallbacks,
            "quantization": self.quantization,
            "anthropic_version": self.anthropic_version,
            "pricing_snapshot": dict(self.pricing_snapshot),
        }


def claude_direct_pin(
    *, anthropic_version: str, pricing_snapshot: Mapping[str, object]
) -> ProviderPin:
    """Build the canonical Anthropic-direct pin — static facts + the two slots.

    Example:
        >>> claude_direct_pin(anthropic_version="2023-06-01", pricing_snapshot={}).router
        'none'
    """
    return ProviderPin(anthropic_version=anthropic_version, pricing_snapshot=pricing_snapshot)


def sha256_of_file(path: Path) -> str:
    """Return the hex sha256 of ``path`` (split-file pin, ADR 0013).

    Raises:
        FileNotFoundError: if ``path`` is absent — a missing split file means the
            campaign cannot pin its corpus, a launch-blocking defect, not a
            silently-empty hash.
    """
    if not path.is_file():
        raise FileNotFoundError(f"split file to hash is missing: {path} (expected a readable file)")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def split_file_hashes(split_files: Mapping[str, Path]) -> dict[str, str]:
    """Map each named split file to its sha256 (deterministic key order)."""
    return {name: sha256_of_file(split_files[name]) for name in sorted(split_files)}


@dataclass(frozen=True, slots=True)
class CampaignLockfile:
    """The full campaign identity block (ADR 0016 §Campaign mechanics).

    ``dataset_pins`` is :func:`pydocs_eval.datasets_swe.pin_metadata`;
    ``split_hashes`` the per-split-file sha256s; ``cells`` the grid;
    ``host`` the launch fingerprint; ``provider`` / ``billing_mode`` the model
    plumbing; ``caps`` / ``cost_ceiling_usd`` / ``assumed_cost_on_raise`` the R6
    guards (the last being the conservative spend booked per raised rollout, so
    changing it changes the campaign identity — money-review finding 1); the
    ``*_version`` fields + ``artifact_hash`` the metric identity. :attr:`campaign_id`
    is the canonical-JSON sha256 over :meth:`to_dict` — the R5 identity.
    """

    dataset_pins: Mapping[str, object]
    split_hashes: Mapping[str, str]
    cells: Sequence[CellConfig]
    host: HostFingerprint
    provider: str
    billing_mode: str
    provider_pin: ProviderPin
    caps: RolloutCaps
    cost_ceiling_usd: float
    assumed_cost_on_raise: float
    schema_version: int
    score_version: int
    taxonomy_version: int
    artifact_hash: str
    metric_version: int = 1
    extra: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.billing_mode not in _VALID_BILLING_MODES:
            raise ValueError(
                f"billing_mode={self.billing_mode!r} is not one of {_VALID_BILLING_MODES!r}; "
                "R6 rollout-level reconciliation needs api_key_metered"
            )
        if not self.cells:
            raise ValueError("campaign lockfile requires at least one cell, got none")

    def to_dict(self) -> dict[str, object]:
        """Canonical lockfile document — the pre-image of the campaign ID."""
        return {
            "dataset_pins": dict(self.dataset_pins),
            "split_hashes": dict(self.split_hashes),
            "cells": [c.to_dict() for c in self.cells],
            "host": self.host.to_dict(),
            "provider": self.provider,
            "billing_mode": self.billing_mode,
            "provider_pin": self.provider_pin.to_dict(),
            "caps": self.caps.to_dict(),
            "cost_ceiling_usd": self.cost_ceiling_usd,
            "assumed_cost_on_raise": self.assumed_cost_on_raise,
            "versions": {
                "schema": self.schema_version,
                "score": self.score_version,
                "taxonomy": self.taxonomy_version,
                "metric": self.metric_version,
            },
            "artifact_hash": self.artifact_hash,
            "extra": dict(self.extra),
        }

    @property
    def campaign_id(self) -> str:
        """sha256 of the canonical-JSON lockfile — any field change ⇒ new ID (R5)."""
        return hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


def write_lockfile(campaign_root: Path, lockfile: CampaignLockfile) -> Path:
    """Write the lockfile (with its stamped ``campaign_id``) into ``campaign_root``.

    The written document embeds ``campaign_id`` alongside the block so a reader
    never has to recompute it to know the identity; a re-hash of the block still
    reproduces it (byte-stable), which the resume path asserts.
    """
    campaign_root.mkdir(parents=True, exist_ok=True)
    payload = {"campaign_id": lockfile.campaign_id, **lockfile.to_dict()}
    path = campaign_root / LOCKFILE_FILENAME
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    return path
