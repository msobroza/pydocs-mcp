"""Phase 3 baseline-campaign runner (ADR 0014 execution + ADR 0016 campaign design).

The orchestration layer above Phase 2's per-rollout capture: cell definitions and
the immutable campaign lockfile (campaign ID = its canonical-JSON hash), a
resumable JSONL work queue, runner-enforced budget guards (R6) and infra
retry/exclusion (R8), the canonical-checkout project-index cache, the cross-cell
paired aggregator, and the host-gated 3-instance smoke.

Heavy ``pydocs_mcp`` imports stay function-local (index cache only), so importing
this package keeps the eval base-install dependency floor intact.
"""

from __future__ import annotations

from pydocs_eval.campaign.aggregator import (
    CellAggregate,
    ContrastResult,
    NamedContrast,
    campaign_report,
    load_cell_aggregate,
    paired_contrast,
    strata_contrasts,
)
from pydocs_eval.campaign.budget import BudgetGuard, HaltReason
from pydocs_eval.campaign.cells import CellConfig, screening_cells
from pydocs_eval.campaign.ledger import (
    CampaignLedger,
    LedgerRecord,
    WorkItem,
    WorkState,
    build_work,
)
from pydocs_eval.campaign.lockfile import (
    CampaignLockfile,
    HostFingerprint,
    ProviderPin,
    RolloutCaps,
    capture_host_fingerprint,
    claude_direct_pin,
    write_lockfile,
)
from pydocs_eval.campaign.prebuild import InstanceSpec, load_instance_manifest, prebuild_index
from pydocs_eval.campaign.runner import CampaignRunResult, RolloutOutcome, run_campaign
from pydocs_eval.campaign.smoke import (
    HostProbe,
    SmokePreconditionError,
    check_preconditions,
    ensure_preconditions,
    run_smoke,
)

__all__ = (
    "BudgetGuard",
    "CampaignLedger",
    "CampaignLockfile",
    "CampaignRunResult",
    "CellAggregate",
    "CellConfig",
    "ContrastResult",
    "HaltReason",
    "HostFingerprint",
    "HostProbe",
    "InstanceSpec",
    "LedgerRecord",
    "NamedContrast",
    "ProviderPin",
    "RolloutCaps",
    "RolloutOutcome",
    "SmokePreconditionError",
    "WorkItem",
    "WorkState",
    "build_work",
    "campaign_report",
    "capture_host_fingerprint",
    "check_preconditions",
    "claude_direct_pin",
    "ensure_preconditions",
    "load_cell_aggregate",
    "load_instance_manifest",
    "paired_contrast",
    "prebuild_index",
    "run_campaign",
    "run_smoke",
    "screening_cells",
    "strata_contrasts",
    "write_lockfile",
)
