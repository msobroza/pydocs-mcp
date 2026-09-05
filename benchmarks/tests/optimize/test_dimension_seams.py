"""Every searched dimension must have a seam the product can express.

The WHY comment in ``artifacts/ask_architecture.py`` ("only behaviors the
product can already express may be searched") as an executable rule
(run-contract design §9 stage 1, 2026-07-27): a dimension with no consuming seam
sweeps a no-op and silently multiplies rollout spend — ``rewrite_enabled``
did exactly that (the headless runner never calls ``reformulate``), doubling
the ask-architecture grid before it was removed.
"""

from __future__ import annotations

import inspect

import pytest

from pydocs_eval.optimize.artifacts.ask_architecture import _DIMENSION_FIELDS

# Dimensions consumed by the RUNNER rather than build_agent, each with the
# consuming site named — a new entry here must cite its seam or it is drift.
_RUNNER_CONSUMED_DIMENSIONS = {
    "retrieval_config",  # serve --config overlay (pydocs_config runner setting)
    "max_agent_turns",  # AskYourDocsRunnerSettings.max_agent_turns → recursion_limit
}


def test_every_searched_dimension_has_a_product_seam() -> None:
    pytest.importorskip("langgraph")
    from pydocs_mcp.harness.ask_your_docs.agent import build_agent

    buildable = set(inspect.signature(build_agent).parameters)
    for dimension in _DIMENSION_FIELDS:
        assert dimension in buildable | _RUNNER_CONSUMED_DIMENSIONS, (
            f"dimension {dimension!r} is searched but no seam accepts it — "
            "wire it into build_agent (or document its runner seam above) "
            "or delete it; sweeping a no-op multiplies rollout spend"
        )


def test_dimension_fields_are_the_committed_tuple() -> None:
    # Non-skipping twin of the seam check above: the seam test needs the
    # [ask] extra, so this pin holds the rule in environments without it —
    # widening the tuple REQUIRES updating both tests deliberately.
    assert _DIMENSION_FIELDS == ("architecture", "retrieval_config", "max_agent_turns")


def test_the_two_dead_dimensions_stay_deleted() -> None:
    # rewrite_enabled: no seam exists (reformulate is never called headless).
    # scope_pin: returns ONLY together with its build_agent seam (stage 2 of
    # the run-contract design) — never as a searched no-op.
    assert "rewrite_enabled" not in _DIMENSION_FIELDS
    assert "scope_pin" not in _DIMENSION_FIELDS
