"""Campaign pre-registration (ADR 0018) — frozen registration + power tooling.

No-spend Phase 4 deliverable: the versioned pre-registration config
(``optimize/configs/campaign_preregistration.yaml``), a loader that REFUSES to
authorize a launch while any measured slot is null (:func:`authorize_launch`), the
:func:`registration_hash` the candidate super-ledger references, and the
code-computed power/false-accept report the owner budget checkpoint reads
(:func:`render_power_report`).
"""

from pydocs_eval.optimize.prereg.config import (
    MEASURED_SLOTS,
    PanelOverlapError,
    PreRegistration,
    UnfilledSlotsError,
    assert_panel_disjoint_from_val,
    authorize_launch,
    load_preregistration,
    registration_hash,
)
from pydocs_eval.optimize.prereg.power import (
    PowerRow,
    false_accept_rate,
    gate_accept_prob,
    power_at,
    power_rows,
)
from pydocs_eval.optimize.prereg.report import family_wise_false_accept, render_power_report

__all__ = [
    "MEASURED_SLOTS",
    "PanelOverlapError",
    "PowerRow",
    "PreRegistration",
    "UnfilledSlotsError",
    "assert_panel_disjoint_from_val",
    "authorize_launch",
    "false_accept_rate",
    "family_wise_false_accept",
    "gate_accept_prob",
    "load_preregistration",
    "power_at",
    "power_rows",
    "registration_hash",
    "render_power_report",
]
