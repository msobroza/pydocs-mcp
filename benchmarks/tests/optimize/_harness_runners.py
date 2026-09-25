"""Named harness-runner doubles for the tests of the eval's timeout wrapper.

Each stands in for the product harness at the one seam the wrapper wraps —
``run(sample, guidance_sections)`` — and fails the way a real run fails.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

# Far past any timeout a test sets: the run never finishes on its own.
_HANG_SECONDS = 10.0


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
        await asyncio.sleep(_HANG_SECONDS)
        raise AssertionError("the per-task timeout should have cancelled this run")
