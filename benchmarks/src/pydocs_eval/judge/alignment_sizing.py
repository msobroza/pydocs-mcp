"""How many alignment items the sub-budget buys, from what the pilot measured (spec 9d, Q51).

The 10-item dual pilot measures the real cost of one item labelled by both
labellers. What is left of ``judge.alignment.sub_budget_usd`` after it buys
``remaining // per_item`` more items; when that is short of what both passes
aim at (``n_items``: pass 1 of 100, the pilot's items included, and pass 2 of
60), it is split between them in proportion to what each still needs. The
owner is told the counts; the item count is what flexes, never the budget.

Example:
    >>> size_alignment_passes(LabellingCost(0.5, 10, 0), AlignmentConfig()).pass_2_items
    60
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pydocs_eval.judge.alignment_labelling import LabellingCost
from pydocs_eval.judge.role_config import AlignmentConfig

# What the $15 sub-budget was sized on: $0.09-0.20 per dual-labelled item (the
# spec's estimate, 9d). The pilot is compared with it, since Astra's batch price
# turned out 2.5x Opus's (the #381 note from implementing #377).
_ASSUMED_ITEM_USD = (0.09, 0.20)


@dataclass(frozen=True, slots=True)
class AlignmentSizing:
    """The pilot's measured price and what the rest of the sub-budget buys, per pass.

    Every count is ``None`` when the pilot has no complete price: a batch
    OpenRouter did not price leaves the cost per item unknown.
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
        low, high = _ASSUMED_ITEM_USD
        return low <= self.per_item_usd <= high


def size_alignment_passes(cost: LabellingCost, config: AlignmentConfig) -> AlignmentSizing:
    """What the sub-budget left after the pilot buys, split between pass 1 and pass 2."""
    remaining = config.sub_budget_usd - cost.total_usd
    per_item = cost.per_item_usd if cost.unpriced_batches == 0 and cost.total_usd > 0 else None
    if per_item is None:
        return AlignmentSizing(None, cost.items, cost.total_usd, remaining, None, None, None)
    affordable = max(0, math.floor(remaining / per_item))
    more_1 = max(0, config.n_items.pass_1 - cost.items)
    more_2 = config.n_items.pass_2
    if affordable < more_1 + more_2:
        more_1 = math.floor(affordable * more_1 / (more_1 + more_2))
        more_2 = affordable - more_1
    return AlignmentSizing(
        per_item, cost.items, cost.total_usd, remaining, affordable, cost.items + more_1, more_2
    )


__all__ = ("AlignmentSizing", "size_alignment_passes")
