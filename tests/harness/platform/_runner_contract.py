"""The shared HarnessRunner contract suite (run-contract design §2 rules).

Every harness's test module subclasses :class:`HarnessRunnerContract` and
implements the three hooks; the suite then enforces the rules no adapter may
weaken: bad settings fail at construction before any spend; an unknown
guidance section raises ``UndeliverableGuidanceError`` (never silent
omission); a conformant run returns a ``Trajectory`` whose SERVER slice
equals the trace events and whose ``trajectory_id`` is non-empty.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.platform.contract import (
    REQUIRED_SAMPLE_KEYS,
    HarnessRunner,
    Trajectory,
    UndeliverableGuidanceError,
)


def conformant_sample() -> dict[str, object]:
    return {key: f"value-{key}" for key in REQUIRED_SAMPLE_KEYS}


class HarnessRunnerContract:
    """Subclass per harness; implement the three hooks."""

    def make_runner(self):
        """A ready runner built from valid settings (fakes welcome)."""
        raise NotImplementedError

    def invalid_settings(self) -> Mapping[str, object]:
        """A settings mapping the harness must reject at construction."""
        raise NotImplementedError

    def build_runner_from(self, settings: Mapping[str, object]):
        """The harness's factory (``make_harness_runner`` equivalent)."""
        raise NotImplementedError

    async def test_the_factory_returns_the_port(self) -> None:
        # The runtime-checkable Protocol IS the cross-package contract: a
        # generic caller writes runner.run(...), so a bare callable is a
        # conformance failure even if it "works" in this repo's tests.
        assert isinstance(self.make_runner(), HarnessRunner)

    async def test_invalid_settings_fail_at_construction(self) -> None:
        with pytest.raises(Exception):
            self.build_runner_from(self.invalid_settings())

    async def test_unknown_guidance_section_is_undeliverable(self) -> None:
        runner = self.make_runner()
        with pytest.raises(UndeliverableGuidanceError):
            await runner.run(conformant_sample(), {"NOT: a.section": "text"})

    async def test_nonconformant_sample_rows_fail_loudly(self) -> None:
        # Contract rule 6, harness-agnostic: a row without the required
        # keys must raise a typed product error before any spend.
        runner = self.make_runner()
        with pytest.raises(PydocsMCPError):
            await runner.run({"record_id": "only"}, {})

    async def test_conformant_run_returns_a_traced_trajectory(self) -> None:
        runner = self.make_runner()
        trajectory = await runner.run(conformant_sample(), {})
        assert isinstance(trajectory, Trajectory)
        assert trajectory.trajectory_id
        # The SERVER slice must equal the trace the run left behind — the
        # derived-view pin (contract rule 5).
        from pydocs_mcp.observability.trace_reader import read_tool_call_records

        assert trajectory.server_tool_calls() == read_tool_call_records(trajectory.trace_dir)
