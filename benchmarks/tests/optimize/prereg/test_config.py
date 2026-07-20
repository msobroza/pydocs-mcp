"""Pre-registration loader, launch-refusal, hash, and panel-disjointness pins."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

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

_PACKAGED = (
    Path(__file__).parents[3] / "src/pydocs_eval/optimize/configs/campaign_preregistration.yaml"
)
_VAL_TXT = Path(__file__).parents[3] / "data/swe/splits/val.txt"


def _packaged() -> PreRegistration:
    return load_preregistration(_PACKAGED)


def _filled() -> PreRegistration:
    """A fully-measured registration (every slot filled) for the launchable path."""
    return dataclasses.replace(
        _packaged(),
        pi_d=0.20,
        cost_rollout=0.40,
        m_mb=0.02,
        c_sel=1.50,
        confirmed_target=0.05,
        g_gate_evals=10,
        minibatch_panel_instance_ids=("widgetlib__panel-1",),
    )


def test_n_val_equals_live_val_txt_distinct_line_count() -> None:
    """The pre-registered ``n_val`` is not a stale literal: it equals the CURRENT
    distinct-line count of the committed val gate list (ADR 0018 §Evidence — the
    559-instance gate). A split re-derivation that changes val.txt fails here
    until the registration is re-frozen against it."""
    distinct = {line.strip() for line in _VAL_TXT.read_text().splitlines() if line.strip()}
    assert _packaged().n_val == len(distinct)


def test_packaged_loads_with_fixed_slots_set() -> None:
    """The shipped registration parses; fixed decision slots are pinned."""
    prereg = _packaged()
    assert prereg.alpha == 0.05
    assert prereg.delta_min == 0.05
    assert prereg.k_plateau == 5
    assert prereg.n_val == 559
    assert prereg.gate_rule == "paired_exact_mcnemar_one_sided"
    assert prereg.stopping_rules == (
        "budget_ceiling",
        "plateau_k_consecutive_rejections",
        "target_reached",
    )


def test_packaged_measured_slots_all_null() -> None:
    """Every measured slot ships [TO BE MEASURED] (null) — nothing pre-filled."""
    prereg = _packaged()
    assert prereg.unfilled_slots() == MEASURED_SLOTS
    assert prereg.is_launchable() is False


def test_authorize_launch_refuses_and_names_empty_slots() -> None:
    """The standing refusal: launch blocked, error names every null slot (ADR 0018)."""
    with pytest.raises(UnfilledSlotsError) as excinfo:
        authorize_launch(_packaged())
    assert excinfo.value.empty_slots == MEASURED_SLOTS
    for slot in MEASURED_SLOTS:
        assert slot in str(excinfo.value)


def test_authorize_launch_passes_when_filled() -> None:
    """A fully-measured registration authorizes without raising."""
    prereg = _filled()
    assert prereg.is_launchable() is True
    authorize_launch(prereg)  # no raise


def test_empty_panel_still_blocks_launch() -> None:
    """An empty panel tuple counts as unfilled — a campaign needs a real panel."""
    prereg = dataclasses.replace(_filled(), minibatch_panel_instance_ids=())
    assert "minibatch_panel_instance_ids" in prereg.unfilled_slots()
    with pytest.raises(UnfilledSlotsError):
        authorize_launch(prereg)


def test_marker_string_parses_to_none(tmp_path: Path) -> None:
    """The literal [TO BE MEASURED] marker parses to None, not a real value."""
    text = _PACKAGED.read_text(encoding="utf-8").replace("pi_d: null", 'pi_d: "[TO BE MEASURED]"')
    path = tmp_path / "prereg.yaml"
    path.write_text(text, encoding="utf-8")
    assert load_preregistration(path).pi_d is None


def test_registration_hash_is_content_stable() -> None:
    """Same content -> same hash; a slot edit changes it loudly."""
    assert registration_hash(_packaged()) == registration_hash(_packaged())
    bumped = dataclasses.replace(_packaged(), alpha=0.01)
    assert registration_hash(bumped) != registration_hash(_packaged())


def test_registration_hash_ignores_field_insertion_order() -> None:
    """canonical_json sorts keys — construction order cannot change the hash."""
    a = _filled()
    b = dataclasses.replace(_filled())
    assert registration_hash(a) == registration_hash(b)


def test_missing_fixed_slot_raises(tmp_path: Path) -> None:
    """A dropped required fixed slot is a typed error naming the key."""
    text = "\n".join(
        line
        for line in _PACKAGED.read_text(encoding="utf-8").splitlines()
        if not line.startswith("alpha:")
    )
    path = tmp_path / "prereg.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="alpha"):
        load_preregistration(path)


def test_panel_disjoint_no_op_while_unfilled() -> None:
    """Disjointness is a no-op while the panel slot is null (nothing to overlap)."""
    assert_panel_disjoint_from_val(_packaged(), frozenset({"a", "b"}))  # no raise


def test_panel_overlap_raises() -> None:
    """A panel sharing a val gate id is a PanelOverlapError naming the overlap."""
    prereg = dataclasses.replace(_filled(), minibatch_panel_instance_ids=("shared", "panel-only"))
    with pytest.raises(PanelOverlapError, match="shared"):
        assert_panel_disjoint_from_val(prereg, frozenset({"shared", "gate-only"}))


def test_panel_disjoint_ok() -> None:
    """A val-disjoint panel passes."""
    prereg = dataclasses.replace(_filled(), minibatch_panel_instance_ids=("p1", "p2"))
    assert_panel_disjoint_from_val(prereg, frozenset({"g1", "g2"}))  # no raise
