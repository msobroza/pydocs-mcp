"""Always-available tracker: one JSONL file per run, one JSON line per
event (spec §4.5). Zero deps — the run file is self-describing via an
``_event`` discriminator on every record."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import TextIOBase
from pathlib import Path
from typing import IO, Literal

from ..protocols import RunHandle
from ..serialization import tracker_registry


def _utc_ts() -> str:
    # WHY: filename-safe ISO8601 — colons in the time portion are illegal
    # on Windows filesystems and awkward in shells, so we collapse them.
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(dataset: str) -> str:
    # WHY: ``@`` in ``repoqa@v1`` is unfriendly in filenames; ``_at_``
    # round-trips back to the dataset name visually without escaping.
    return dataset.replace("@", "_at_").replace("/", "_")


@tracker_registry.register("jsonl")
@dataclass
class JsonlExperimentTracker:
    """One file per run under ``output_dir``. Stateless w.r.t. the run —
    the open file handle rides on ``RunHandle.raw`` so concurrent runs do
    not contend on tracker state."""

    name: str = "jsonl"
    output_dir: Path = field(default_factory=lambda: Path("benchmarks/results/jsonl"))

    def open_run(
        self,
        *,
        system: str,
        config_name: str,
        dataset: str,
        params: Mapping[str, str],
        tags: Mapping[str, str],
    ) -> RunHandle:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / (
            f"{system}_{config_name}_{_slug(dataset)}_{_utc_ts()}.jsonl"
        )
        fh = path.open("w", encoding="utf-8")
        _write(
            fh,
            {
                "_event": "run_start",
                "system": system,
                "config_name": config_name,
                "dataset": dataset,
                "params": dict(params),
                "tags": dict(tags),
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )
        return RunHandle(tracker_name=self.name, raw=fh)

    def log_metric(
        self,
        handle: RunHandle,
        name: str,
        value: float,
        step: int | None = None,
    ) -> None:
        _write(
            _file(handle),
            {"_event": "metric", "name": name, "value": value, "step": step},
        )

    def log_artifact(
        self,
        handle: RunHandle,
        path: Path,
        name: str | None = None,
    ) -> None:
        _write(
            _file(handle),
            {
                "_event": "artifact",
                "name": name or path.name,
                "path": str(path),
            },
        )

    def close_run(
        self,
        handle: RunHandle,
        status: Literal["finished", "failed"],
    ) -> None:
        fh = _file(handle)
        # WHY: the runner's try/finally path may call close twice on
        # failure; a second close on an already-closed handle would raise
        # ValueError, masking the original exception.
        if fh.closed:
            return
        _write(fh, {"_event": "run_end", "status": status})
        fh.close()


def _file(handle: RunHandle) -> IO[str]:
    raw = handle.raw
    if not isinstance(raw, TextIOBase):
        raise TypeError(
            f"JsonlExperimentTracker expected a text file handle, got {type(raw).__name__}"
        )
    return raw


def _write(fh: IO[str], record: Mapping[str, object]) -> None:
    fh.write(json.dumps(record) + "\n")
    fh.flush()  # Performance: tail-followability matters more than throughput here.
