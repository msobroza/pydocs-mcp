"""Baseline JSON data model — stdlib-only on purpose.

``BaselineRecord`` is the read model for ``benchmarks/baselines/*.json``.
It lives apart from ``plotting.py`` so consumers that only READ baselines
(``ci_compare``, programmatic method comparison) import it in milliseconds
without paying the matplotlib + seaborn + pandas import cost that
``plotting.py`` front-loads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BaselineRecord:
    """A loaded baseline JSON ready for plotting.

    Mirrors the shape written by Task 6's heredoc in
    ``benchmarks/baselines/*.json``: ``dataset``, ``system``, ``config``,
    ``label``, ``tasks_ran``, ``metrics``, ``captured_at``, ``git_sha``,
    ``source_jsonl``.
    """

    system: str
    config: str
    label: str
    dataset: str
    tasks_ran: int
    metrics: dict[str, dict[str, float]]
    captured_at: str | None
    git_sha: str | None

    @classmethod
    def from_path(cls, path: Path) -> BaselineRecord:
        """Load a baseline JSON from disk."""
        data = json.loads(path.read_text())
        return cls(
            system=data["system"],
            config=data["config"],
            label=data.get("label", "<unlabeled>"),
            dataset=data["dataset"],
            tasks_ran=int(data["tasks_ran"]),
            metrics=data["metrics"],
            captured_at=data.get("captured_at"),
            git_sha=data.get("git_sha"),
        )

    @property
    def display_label(self) -> str:
        """Legend label disambiguating the baseline.

        Format: ``"<system> / <config> (<label>)"``. The ``label`` field
        is included so two baselines that share ``system`` + ``config``
        but report from different sweeps (e.g. fixture-5-needles vs
        real-100-needles) don't collide on the X-axis hue.
        """
        return f"{self.system} / {self.config} ({self.label})"

    @property
    def legend_suffix(self) -> str:
        """Compact provenance string ``[<git_sha[:7]>, n=<tasks>]``.

        Sized to fit comfortably in a matplotlib legend without clipping.
        ``label`` is intentionally NOT duplicated here — it's already in
        :attr:`display_label`.
        """
        parts: list[str] = []
        if self.git_sha:
            parts.append(self.git_sha[:7])
        parts.append(f"n={self.tasks_ran}")
        return f" [{', '.join(parts)}]"


__all__ = ("BaselineRecord",)
