"""trajectory/ask_events — reading a trajectory whose product recorded no turns.

The strict reader refuses a trajectory with no model-turn sidecar, and that
refusal is right for a metric that claims to be per-turn (``test_ask_events.py``
pins it). It is wrong for exactly one case: a before/after run whose baseline
commit predates the sidecar, whose trajectories are already recorded and already
paid for. These tests pin the tolerant reader that case needs — one turn per
call, said out loud in ``turns_recorded``, and never a defaulted turn.

The discriminating test is the fan-out one: the same three calls read through the
two readers charge three needless calls with real turns and none without, which
is the whole reason the rate computed without turns is a LOWER BOUND.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from pydocs_eval.trajectory.ask_events import (
    ASK_MODEL_TURNS_FILENAME,
    MissingModelTurnsError,
    load_ask_tool_events,
    load_ask_trajectory_events,
)
from pydocs_eval.trajectory.call_efficiency import compute_call_efficiency

from ._ask_traces import ScriptedCall, write_ask_trajectory

# Three single-target calls the model issued AT ONCE — the fan-out one batch
# call replaces, and the only needless component that needs a turn to see it.
_THREE_CALLS_IN_ONE_TURN: list[ScriptedCall] = [
    ("get_symbol", {"target": "a.B"}, 1),
    ("get_symbol", {"target": "c.D"}, 1),
    ("get_symbol", {"target": "e.F"}, 1),
]


def _trajectory_missing_its_sidecar(tmp_path: Path, calls: Sequence[ScriptedCall]) -> Path:
    """A trace exactly as a product that predates the model-turn sidecar leaves it."""
    trace_dir = write_ask_trajectory(tmp_path / "traces", calls=calls)
    (trace_dir / ASK_MODEL_TURNS_FILENAME).unlink()
    return trace_dir


def test_without_the_sidecar_every_call_gets_a_turn_of_its_own(tmp_path: Path) -> None:
    trace_dir = _trajectory_missing_its_sidecar(tmp_path, _THREE_CALLS_IN_ONE_TURN)

    trajectory = load_ask_trajectory_events(trace_dir)

    assert trajectory.turns_recorded is False
    assert [event.turn for event in trajectory.events] == [1, 2, 3]
    assert [event.seq for event in trajectory.events] == [1, 2, 3]


def test_without_the_sidecar_the_fan_out_component_charges_nothing(tmp_path: Path) -> None:
    """One call per turn can never reach the fan-out threshold — so the rate is a floor.

    With the real turns the same three calls ARE a fan-out and the rate is 1.0.
    Stamping each call with its own seq can only UNDER-count needless calls,
    which is what makes the number safe to pair with the other arm.
    """
    with_turns = write_ask_trajectory(tmp_path / "with", calls=_THREE_CALLS_IN_ONE_TURN)
    without_turns = _trajectory_missing_its_sidecar(tmp_path / "without", _THREE_CALLS_IN_ONE_TURN)

    charged = compute_call_efficiency(load_ask_tool_events(with_turns)).needless
    floor = compute_call_efficiency(load_ask_trajectory_events(without_turns).events).needless

    assert sorted(charged.fan_out_where_batch) == [1, 2, 3]
    assert charged.rate == 1.0
    assert floor.fan_out_where_batch == frozenset()
    assert floor.rate < charged.rate


def test_with_the_sidecar_it_reads_exactly_what_the_strict_reader_reads(tmp_path: Path) -> None:
    trace_dir = write_ask_trajectory(tmp_path / "traces", calls=_THREE_CALLS_IN_ONE_TURN)

    trajectory = load_ask_trajectory_events(trace_dir)

    assert trajectory.turns_recorded is True
    assert trajectory.events == load_ask_tool_events(trace_dir)


def test_a_sidecar_that_stamps_no_turn_for_a_call_still_refuses(tmp_path: Path) -> None:
    """A sidecar that IS there and disagrees with the capture is a defect, not an old product."""
    trace_dir = write_ask_trajectory(tmp_path / "traces", calls=[("grep", {"pattern": "x"}, 1)])
    (trace_dir / ASK_MODEL_TURNS_FILENAME).write_text(
        json.dumps({"schema_version": 1, "turns": {"99": 1}}), encoding="utf-8"
    )

    with pytest.raises(MissingModelTurnsError, match="seq 1"):
        load_ask_trajectory_events(trace_dir)
