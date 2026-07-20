"""Facts-only reflective dataset + flagged feedback selector (ADR 0019 §1-2).

Pure offline — no gepa, no pydocs_mcp: the reflective builder and the selector
operate on plain section dicts + ``InstanceTrajectory`` values.
"""

from __future__ import annotations

import pytest

from pydocs_eval.optimize.gepa_harness.module_selector import FeedbackImplicatedSelector
from pydocs_eval.optimize.gepa_harness.reflective import (
    InstanceTrajectory,
    ReflectiveConfig,
    build_reflective_dataset,
)

_SECTIONS = {
    "SERVER_INSTRUCTIONS": "server text",
    "TOOL: grep": "grep text",
    "TOOL: get_symbol": "symbol text",
}
_TRAJ = (
    InstanceTrajectory(instance_id="i1", score=0.4, feedback="grep never ran; grep missed"),
    InstanceTrajectory(instance_id="i2", score=0.8, feedback="all good"),
)


def test_facts_only_records_use_gepa_schema() -> None:
    """Each record is GEPA's {Inputs, Generated Outputs, Feedback} + score/trace_id."""
    dataset = build_reflective_dataset(_SECTIONS, _TRAJ, ("TOOL: grep",), ReflectiveConfig())
    records = dataset["TOOL: grep"]
    assert [r["Feedback"] for r in records] == ["grep never ran; grep missed", "all good"]
    assert records[0]["Generated Outputs"] == "grep text"  # the section being rewritten
    assert records[0]["Inputs"] == {"instance": "i1"}
    assert records[0]["score"] == 0.4 and records[0]["trace_id"] == "i1"
    assert "Result Excerpt" not in records[0]  # excerpts OFF by default (ADR 0019 §ii)


def test_result_excerpt_flag_pulls_verbatim_only() -> None:
    """The excerpt flag appends the injected VERBATIM excerpt, never a summary."""
    config = ReflectiveConfig(include_result_excerpts=True, excerpt_fn=lambda iid: f"BLOB<{iid}>")
    records = build_reflective_dataset(_SECTIONS, _TRAJ, ("TOOL: grep",), config)["TOOL: grep"]
    assert records[0]["Result Excerpt"] == "BLOB<i1>"


def test_excerpt_flag_without_provider_is_a_construction_error() -> None:
    """include_result_excerpts with no excerpt_fn has no verbatim source to slice."""
    with pytest.raises(ValueError, match="requires excerpt_fn"):
        ReflectiveConfig(include_result_excerpts=True)


def test_subsample_is_seeded_and_bounded() -> None:
    """max_records caps records per component and subsamples deterministically."""
    many = tuple(InstanceTrajectory(f"i{n}", 0.5, "fb") for n in range(10))
    config = ReflectiveConfig(max_records=3, rng_seed=7)
    first = build_reflective_dataset(_SECTIONS, many, ("TOOL: grep",), config)["TOOL: grep"]
    second = build_reflective_dataset(_SECTIONS, many, ("TOOL: grep",), config)["TOOL: grep"]
    assert len(first) == 3
    assert [r["trace_id"] for r in first] == [r["trace_id"] for r in second]  # seeded


def test_feedback_selector_picks_most_implicated_tool() -> None:
    """The flagged selector returns the TOOL section its feedback most mentions."""
    selector = FeedbackImplicatedSelector()
    candidate = dict(_SECTIONS)
    picked = selector(None, _TRAJ, [0.4, 0.8], 0, candidate)
    assert picked == ["TOOL: grep"]  # 'grep' appears twice in the feedback, 'get_symbol' zero


def test_feedback_selector_falls_back_when_nothing_implicated() -> None:
    """No implicated tool → the first mutable section, so the loop never stalls."""
    quiet = (InstanceTrajectory("i1", 0.5, "no tool named here"),)
    picked = FeedbackImplicatedSelector()(None, quiet, [0.5], 0, dict(_SECTIONS))
    assert picked == ["SERVER_INSTRUCTIONS"]  # first key
