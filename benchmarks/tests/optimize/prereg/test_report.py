"""Power-report generator + family-wise disclosure pins (ADR 0018 item 6)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from pydocs_eval.optimize.prereg.config import PreRegistration, load_preregistration
from pydocs_eval.optimize.prereg.report import family_wise_false_accept, render_power_report

_PACKAGED = (
    Path(__file__).parents[3] / "src/pydocs_eval/optimize/configs/campaign_preregistration.yaml"
)


def _prereg() -> PreRegistration:
    return load_preregistration(_PACKAGED)


def test_report_contains_adr_headline_power_numbers() -> None:
    """The rendered table carries the 0.96/0.73/0.55 headline power at N=559."""
    text = render_power_report(_prereg(), (0.10, 0.20, 0.30))
    assert "0.9628" in text
    assert "0.7279" in text
    assert "0.5504" in text
    assert "300" in text and "624" in text and "936" in text


def test_report_uses_registration_alpha_and_n_val() -> None:
    """The report reflects the frozen alpha / delta_min / N_val, never a drift."""
    text = render_power_report(_prereg(), (0.20,))
    assert "alpha=0.050" in text
    assert "delta_min=0.050" in text
    assert "N_val=559" in text


def test_report_notes_unmeasured_g() -> None:
    """With G unfilled the family-wise line says [TO BE MEASURED], not a number."""
    text = render_power_report(_prereg(), (0.20,))
    assert "G [TO BE MEASURED]" in text


def test_report_renders_family_wise_when_g_filled() -> None:
    """A filled G yields 1-(1-alpha/2)^G in the disclosure line."""
    prereg = dataclasses.replace(_prereg(), g_gate_evals=10)
    text = render_power_report(prereg, (0.20,))
    assert "G=10" in text
    assert f"{family_wise_false_accept(0.05, 10):.4f}" in text


def test_family_wise_formula() -> None:
    """1-(1-alpha/2)^G exactly (0 gates -> 0; monotone up in G)."""
    assert family_wise_false_accept(0.05, 0) == 0.0
    assert family_wise_false_accept(0.05, 10) == pytest.approx(1 - 0.975**10)
    assert family_wise_false_accept(0.05, 10) > family_wise_false_accept(0.05, 5)


def test_family_wise_rejects_negative_g() -> None:
    with pytest.raises(ValueError, match="g_gate_evals"):
        family_wise_false_accept(0.05, -1)


def test_report_requires_at_least_one_row() -> None:
    with pytest.raises(ValueError, match="pi_ds"):
        render_power_report(_prereg(), ())


def test_report_is_deterministic() -> None:
    """Byte-stable render for the owner checkpoint (recomputable)."""
    a = render_power_report(_prereg(), (0.10, 0.20, 0.30))
    b = render_power_report(_prereg(), (0.10, 0.20, 0.30))
    assert a == b
