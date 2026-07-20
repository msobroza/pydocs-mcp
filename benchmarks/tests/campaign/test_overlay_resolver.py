"""Overlay-name → serve-config resolver (ADR 0021 eval hook).

Pins the registry mapping a cell's overlay NAME to its shipped serve-YAML path,
the typed error on an unknown name, and the packaged existence of every
registered overlay (a name with no file is a packaging defect, not a silent
stock serve).
"""

from __future__ import annotations

import pytest

from pydocs_eval.campaign.overlay_resolver import (
    UnknownOverlayError,
    known_overlays,
    resolve_overlay,
)


def test_resolve_suggestions_off_overlay() -> None:
    path = resolve_overlay("suggestions_off")
    assert path.is_file()
    assert path.name == "suggestions_off.yaml"


def test_unknown_overlay_is_typed_error() -> None:
    with pytest.raises(UnknownOverlayError, match="unknown overlay 'nope'"):
        resolve_overlay("nope")


def test_every_registered_overlay_file_exists() -> None:
    # A registered name whose file is missing would surface as FileNotFoundError
    # at rollout time; assert the whole registry resolves at import-adjacent time.
    for name in known_overlays():
        assert resolve_overlay(name).is_file()


def test_suggestions_off_overlay_is_registered() -> None:
    # cells.py's _SUGGESTIONS_OFF_OVERLAY value must be resolvable — the consumer
    # the evidence found missing.
    assert "suggestions_off" in known_overlays()
