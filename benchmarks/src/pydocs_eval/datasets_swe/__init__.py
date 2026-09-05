"""Phase 3 SWE-bench dataset plumbing: pins, R2 overlap, splits, discriminative subset.

Separate from the retrieval-track ``datasets`` subpackage on purpose (ADR 0013): this one
owns the pinned SWE-bench-Live dev/val corpus and the frozen SWE-bench Pro test manifest
for the baseline campaigns, not the retrieval QA loaders. Heavy deps (huggingface_hub,
pyarrow) are function-local in :mod:`download` so importing this package stays stdlib-cheap.
"""

from __future__ import annotations

from .discriminative import (
    DiscriminativeSubset,
    SubsetRuleConfig,
    build_discriminative_subset,
)
from .overlap import OverlapReport, compute_overlap, excluded_instance_ids
from .pins import LIVE_PIN, PRO_PIN, pin_metadata
from .records import LiveRecord, dedupe_live_records, org_of
from .splits import SplitConfig, SplitResult, build_splits
from .touch_log import TouchLogEntry, append_entry, read_entries

__all__ = (
    "LIVE_PIN",
    "PRO_PIN",
    "DiscriminativeSubset",
    "LiveRecord",
    "OverlapReport",
    "SplitConfig",
    "SplitResult",
    "SubsetRuleConfig",
    "TouchLogEntry",
    "append_entry",
    "build_discriminative_subset",
    "build_splits",
    "compute_overlap",
    "dedupe_live_records",
    "excluded_instance_ids",
    "org_of",
    "pin_metadata",
    "read_entries",
)
