"""One task's measured row, and how a single number is read off it.

The row the measurement fills in (``before_after_measure``) and the report's row
catalogue reads (``before_after_rows``). It lives apart from the measurement
because the catalogue is imported at runtime by the plan module
(``before_after.REPORTED_METRICS``), and the measurement module imports the plan:
a catalogue that imported the measurement would close that cycle. Nothing here
reads a trace.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydocs_eval.trajectory.ask_outcome import UNMEASURED_ENDING, TaskEnding
from pydocs_eval.trajectory.search_retrieval import SearchRetrieval
from pydocs_eval.trajectory.tool_usage import ToolUsage


@dataclass(frozen=True, slots=True)
class TaskMeasurement:
    """One trajectory's metric blocks, kept under its task id so two arms can pair.

    ``None`` means undefined, never zero — the trajectory layer's own convention:
    a rate over opportunities the server created is undefined when there were
    none, ``tool_calls_to_first_gold`` is undefined when no call ever surfaced a
    gold file, and a retrieval number is undefined when the trajectory never
    searched.

    The spend fields follow that rule twice over: they are all ``None`` for a
    trajectory that recorded no usage at all, ``reasoning_tokens`` is ``None``
    when the endpoint never reported a thinking count, and ``reported_usd`` is
    ``None`` when it quoted no price. They are defaulted so a measurement built
    without them stays valid and simply reports nothing.

    The two per-turn numbers follow it a third time: they are ``None`` for a
    trajectory whose product wrote no model-turn sidecar (``turns_recorded``
    False), because a turn is exactly what they are computed over.
    """

    task_id: str
    needless_call_rate: float
    resurfacing: int
    zero_yield: int
    #: ``None`` when the trajectory recorded no turns — the component groups
    #: calls per (turn, tool), so without turns it charges nothing measurable.
    fan_out_where_batch: int | None
    tool_mismatch: int
    pointer_followed_rate: float | None
    #: ``None`` when the trajectory recorded no turns — this divides BY turns.
    parallel_calls_per_turn: float | None
    batch_vs_fanout_ratio: float | None
    tool_calls_to_first_gold: int | None
    retrieval: SearchRetrieval
    usage: ToolUsage
    #: False when the product that ran this trajectory wrote no model-turn
    #: sidecar; every other number on this row is still measured.
    turns_recorded: bool = True
    # The same three questions as above, asked of the rows the response TEXT
    # rendered — the only rows the model could read. All ``None`` when the
    # capture cannot say (a search recorded before ``rendered_rows``, schema
    # 2); ``tool_calls_to_first_visible_gold`` is ``None`` when gold never
    # became visible too, exactly like its surfaced sibling. Defaulted so a
    # measurement built without them stays valid and simply reports nothing.
    visible_hit_rate: float | None = None
    gold_visible: bool | None = None
    tool_calls_to_first_visible_gold: int | None = None
    # What the trajectory spent. ``reasoning_tokens`` is the thinking slice OF
    # ``output_tokens`` and ``cached_tokens`` the reused slice OF
    # ``input_tokens`` — diagnostics beside their parents, never addends to
    # them, or the same token would be billed twice.
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    estimated_usd: float | None = None
    reported_usd: float | None = None
    # How the task ended — its outcome, turns and budget, a legacy row's outcome
    # back-filled — and how long it ran, off the arm record. Defaulted like the
    # spend fields: a measurement built without them is UNRECORDED, which every
    # turn row drops rather than counts as zero.
    ending: TaskEnding = UNMEASURED_ENDING
    wall_seconds: float | None = None
    # What the trajectory did after the Needle reached the model
    # (``trajectory.gold_reach``); ``None`` when it never did.
    turns_after_first_gold: int | None = None
    calls_after_first_gold: int | None = None
    tool_calls_to_first_gold_read: int | None = None
    calls_after_first_gold_read: int | None = None
    #: Reserved for finalizing an exhausted run (#375): undefined until a product does.
    finalize_format_failures: int | None = None

    @property
    def uncached_input_tokens(self) -> int | None:
        """Tokens in the endpoint did NOT serve from its cache; ``None`` without usage."""
        if self.input_tokens is None or self.cached_tokens is None:
            return None
        return self.input_tokens - self.cached_tokens

    @property
    def uncached_input_tokens_per_turn(self) -> float | None:
        """The uncached tokens in, per model turn; undefined without usage or turns."""
        uncached, turns = self.uncached_input_tokens, self.ending.turns
        if uncached is None or not turns:
            return None
        return uncached / turns

    @property
    def reached_gold(self) -> int:
        """1 when some call surfaced a gold file — the binary outcome McNemar pairs.

        Always defined, unlike :attr:`tool_calls_to_first_gold`: "never reached
        gold" is a measured failure, not a missing measurement, and dropping it
        would hide exactly the trajectories a change is meant to fix. It is that
        field's ``is not None`` by construction, so the two can never disagree.
        """
        return int(self.tool_calls_to_first_gold is not None)

    @property
    def visible_gold_reached(self) -> float | None:
        """1 / 0 when the capture can say whether gold became visible, else None.

        Undefined rather than zero for a pre-schema-2 capture: "the text showed
        no gold" and "nobody recorded what the text showed" are different
        findings, and reporting the second as the first would invent a failure.
        """
        return None if self.gold_visible is None else float(self.gold_visible)


#: How one number is read off one task's measurement; ``None`` where undefined.
#: Every arm-level rollup (``before_after_measure.ArmMetrics``) takes one, so a
#: caller can ask for a value nested inside a block (``task.retrieval``,
#: ``task.usage``) without the measurement having to flatten every block into a
#: field of its own.
TaskValue = Callable[[TaskMeasurement], float | None]
