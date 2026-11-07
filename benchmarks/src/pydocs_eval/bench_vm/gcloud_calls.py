"""What ``GcloudBenchCloud``'s mixins call through it: ``_gcloud`` and ``_raise_unless_ok``.

``GcloudCacheTransfers`` and ``GcloudImageBuilds`` both shell out through the
adapter's bounded ``gcloud`` call; this base declares that call once, so its
signature changes in one place, and holds the timeout of a small read every
module shares.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydocs_eval.bench_vm.bounded_command import CommandRunner, CompletedCommand

# One small API read (describe, cat, list): an answer within seconds.
GCLOUD_READ_TIMEOUT_S = 60.0


class GcloudCalls:
    """The two calls a ``GcloudBenchCloud`` mixin makes; the adapter provides both."""

    __slots__ = ()

    def _gcloud(
        self,
        args: Sequence[str],
        timeout_s: float,
        *,
        stdin: bytes | None = None,
        runner: CommandRunner | None = None,
    ) -> CompletedCommand:
        raise NotImplementedError  # GcloudBenchCloud provides it

    @staticmethod
    def _raise_unless_ok(completed: CompletedCommand, what: str) -> None:
        raise NotImplementedError  # GcloudBenchCloud provides it
