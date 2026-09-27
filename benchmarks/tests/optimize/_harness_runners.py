"""Named harness-runner doubles for the tests of the eval's timeout wrapper.

Each stands in for the product harness at the one seam the wrapper wraps —
``run(sample, guidance_sections)`` — and fails the way a real run fails.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

# Far past any timeout a test sets: the run never finishes on its own.
_HANG_SECONDS = 10.0


async def hang_until_the_timeout_cancels() -> NoReturn:
    """How every hanging double ends: only the per-task timeout can stop the run."""
    await asyncio.sleep(_HANG_SECONDS)
    raise AssertionError("the per-task timeout should have cancelled this run")


@dataclass(slots=True)
class RaisingHarnessRunner:
    """A harness whose every run raises ``error`` — a runaway candidate, a dead serve child."""

    error: BaseException

    async def run(
        self, sample: Mapping[str, object], guidance_sections: Mapping[str, str]
    ) -> object:
        raise self.error


@dataclass(slots=True)
class HangingHarnessRunner:
    """A harness whose run hangs, so only the per-task timeout can end it."""

    async def run(
        self, sample: Mapping[str, object], guidance_sections: Mapping[str, str]
    ) -> object:
        await hang_until_the_timeout_cancels()


@dataclass(slots=True)
class TraceHandleFillingHangingRunner:
    """A product run that says where it writes and how far it got, then hangs.

    The ask binding's shape from the trace-handle seam on: it records its trajectory
    id, trace directory and messages into the handle the caller made active, and only
    the per-task timeout ends it.
    """

    trace_dir: Path
    messages: Sequence[object] = ()

    async def run(
        self, sample: Mapping[str, object], guidance_sections: Mapping[str, str]
    ) -> object:
        from pydocs_mcp.harness.ask_your_docs.run_trace_handle import ACTIVE_RUN_TRACE_HANDLE

        handle = ACTIVE_RUN_TRACE_HANDLE.get()
        handle.record_identity(self.trace_dir.name, self.trace_dir)
        handle.record_messages(self.messages)
        await hang_until_the_timeout_cancels()


@dataclass(slots=True)
class ActiveHandleRecordingHangingRunner:
    """Hangs like :class:`HangingHarnessRunner`, first noting the trace handle that was active.

    ``handle_var`` is the product's ContextVar, captured by the test before it hides the
    module — so the run can report what the wrapper made active without importing it.
    """

    handle_var: Any
    active_handles: list[object]

    async def run(
        self, sample: Mapping[str, object], guidance_sections: Mapping[str, str]
    ) -> object:
        self.active_handles.append(self.handle_var.get())
        await hang_until_the_timeout_cancels()
