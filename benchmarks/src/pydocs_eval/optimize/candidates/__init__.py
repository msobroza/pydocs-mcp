"""The R3 candidate layer: GEPA-view candidate + validity firewall + ledger.

Three cohesive pieces of ADR 0017/0019's candidate contract:

- ``candidate`` — the GEPA ``dict[str, str]`` candidate <-> Phase 1 delimited
  document bridge (render / parse / serve-truthful artifact hash).
- ``firewall`` — the v2 zero-cost validity firewall over the FULL eleven-header
  product grammar, at least as strict as the product loader on every shared
  dimension (firewall-accepts ⇒ product-accepts).
- ``ledger`` — the append-only JSONL candidate super-ledger with the three new
  lineage fields; a validity-rejected candidate is provably zero-rollout.
"""

from __future__ import annotations

from pydocs_eval.optimize.candidates.candidate import (
    CANONICAL_SECTION_KEYS,
    Candidate,
)
from pydocs_eval.optimize.candidates.firewall import (
    ValidityVerdict,
    firewall_violations,
    screen_candidate,
)
from pydocs_eval.optimize.candidates.ledger import (
    CandidateLedger,
    CandidateRecord,
    GateOutcome,
    MutationRecord,
)

__all__ = [
    "CANONICAL_SECTION_KEYS",
    "Candidate",
    "CandidateLedger",
    "CandidateRecord",
    "GateOutcome",
    "MutationRecord",
    "ValidityVerdict",
    "firewall_violations",
    "screen_candidate",
]
