"""Named fakes for the ``harness-ask-your-docs`` launcher (core deps only)."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence


class FakeStreamlitRun:
    """Stands in for ``subprocess.run`` in ``cli.main``: records ``cmd`` and ``env``, spawns nothing."""

    def __init__(self) -> None:
        self.cmd: list[str] = []
        self.env: dict[str, str] = {}

    def __call__(
        self, cmd: Sequence[str], *, env: Mapping[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        self.cmd = list(cmd)
        self.env = dict(env)
        return subprocess.CompletedProcess(self.cmd, 0)
