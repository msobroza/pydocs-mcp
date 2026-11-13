"""How many alignment items the sub-budget buys, from what the pilot measured (spec 9d, Q51).

The 10-item dual pilot measures the real cost of one item labelled by both
labellers. What is left of ``judge.alignment.sub_budget_usd`` after it buys
``remaining // per_item`` more items; when that is short of what both passes
aim at (``n_items``: 100 for pass 1 and 60 for pass 2, each beside the pilot's
items, as the spec's budget counts them), it is split between them in
proportion to their aims. The owner is told the counts; the item count is what
flexes, never the budget.

Example:
    >>> size_alignment_passes(LabellingCost(0.5, 10, 0), AlignmentConfig()).pass_2_items
    60
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pydocs_eval.judge.alignment_labelling import LabellingCost
from pydocs_eval.judge.role_config import AlignmentConfig

#: What the $15 sub-budget was sized on: $0.09-0.20 per dual-labelled item (the
#: spec's estimate, 9d). The pilot is compared with it, since Astra's batch price
#: turned out 2.5x Opus's (the #381 note from implementing #377).
ASSUMED_ITEM_USD = (0.09, 0.20)


@dataclass(frozen=True, slots=True)
class AlignmentSizing:
    """The pilot's measured price and what the rest of the sub-budget buys, per pass.

    ``pass_1_items`` and ``pass_2_items`` are each pass's own items, the pilot's
    not among them. Every count is ``None`` when the pilot has no complete
    price: a batch OpenRouter did not price leaves the cost per item unknown.
    """

    per_item_usd: float | None
    pilot_items: int
    pilot_cost_usd: float
    remaining_usd: float
    affordable_items: int | None
    pass_1_items: int | None
    pass_2_items: int | None

    @property
    def within_assumed_band(self) -> bool | None:
        """Whether the measured price per item is inside the $0.09-0.20 the budget assumed."""
        if self.per_item_usd is None:
            return None
        low, high = ASSUMED_ITEM_USD
        return low <= self.per_item_usd <= high


def size_alignment_passes(cost: LabellingCost, config: AlignmentConfig) -> AlignmentSizing:
    """What the sub-budget left after the pilot buys, split between pass 1 and pass 2."""
    remaining = config.sub_budget_usd - cost.total_usd
    per_item = cost.per_item_usd if cost.unpriced_batches == 0 and cost.total_usd > 0 else None
    if per_item is None:
        return AlignmentSizing(None, cost.items, cost.total_usd, remaining, None, None, None)
    affordable = max(0, math.floor(remaining / per_item))
    pass_1, pass_2 = _split(affordable, config.n_items.pass_1, config.n_items.pass_2)
    return AlignmentSizing(
        per_item, cost.items, cost.total_usd, remaining, affordable, pass_1, pass_2
    )


def _split(affordable: int, pass_1_aim: int, pass_2_aim: int) -> tuple[int, int]:
    """Both aims when they fit; else ``affordable`` shared in proportion to them."""
    if affordable >= pass_1_aim + pass_2_aim:
        return pass_1_aim, pass_2_aim
    pass_1 = math.floor(affordable * pass_1_aim / (pass_1_aim + pass_2_aim))
    return pass_1, affordable - pass_1


__all__ = ("ASSUMED_ITEM_USD", "AlignmentSizing", "size_alignment_passes")
