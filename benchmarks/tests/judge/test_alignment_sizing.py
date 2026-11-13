"""Sizing the alignment passes to the sub-budget from the pilot's measured cost per item."""

from __future__ import annotations

import pytest

from pydocs_eval.judge.alignment_labelling import LabellingCost
from pydocs_eval.judge.alignment_sizing import size_alignment_passes
from pydocs_eval.judge.role_config import AlignmentConfig

_CONFIG = AlignmentConfig()


def test_a_pilot_at_15_cents_an_item_leaves_room_for_fewer_items_than_both_passes_aim_at() -> None:
    sizing = size_alignment_passes(LabellingCost(1.5, 10, 0), _CONFIG)

    assert sizing.per_item_usd == pytest.approx(0.15)
    assert sizing.remaining_usd == pytest.approx(13.5)
    assert sizing.affordable_items == 90
    # The pilot is its own draw: 90 items split 100:60 between pass 1 and pass 2.
    assert (sizing.pass_1_items, sizing.pass_2_items) == (56, 34)
    assert sizing.within_assumed_band


def test_a_cheap_pilot_buys_both_passes_at_full_size() -> None:
    sizing = size_alignment_passes(LabellingCost(0.5, 10, 0), _CONFIG)

    assert sizing.affordable_items == 290
    assert (sizing.pass_1_items, sizing.pass_2_items) == (100, 60)
    assert not sizing.within_assumed_band


def test_without_a_measured_price_nothing_is_sized() -> None:
    sizing = size_alignment_passes(LabellingCost(0.0, 10, 2), _CONFIG)

    assert sizing.per_item_usd is None
    assert (sizing.affordable_items, sizing.pass_1_items, sizing.pass_2_items) == (None, None, None)
    assert sizing.within_assumed_band is None


def test_a_pilot_that_spent_the_whole_sub_budget_buys_nothing_more() -> None:
    sizing = size_alignment_passes(LabellingCost(16.0, 10, 0), _CONFIG)

    assert sizing.affordable_items == 0
    assert (sizing.pass_1_items, sizing.pass_2_items) == (0, 0)
