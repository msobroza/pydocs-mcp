"""The pass/fail helpers of ``runner_resume_smoke.py``: parse a leg file, judge the kill and the resume.

Pure functions over leg-file text and records, with no process or file
handling, so the unit tests can pin them (``benchmarks/tests/tools/
test_runner_resume_smoke.py``) and a refactor cannot make the gate vacuous.
The script imports every name it uses from here.
"""

from __future__ import annotations

import copy
import json
import signal
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

FIXTURE_LANGUAGE = "python"

# The JSONL tracker's event discriminators (trackers/jsonl_tracker.py).
EVENT_TASK = "task"
EVENT_METRIC = "metric"
EVENT_RESUME = "run_resume"
EVENT_END = "run_end"


class SmokeFailure(Exception):
    """A smoke stage failed; the message is what the operator sees."""


@dataclass(frozen=True, slots=True)
class LegFile:
    """One leg file's parsed records, plus how many lines were not JSON."""

    records: tuple[dict[str, Any], ...]
    corrupt_lines: int


# ── fixture ─────────────────────────────────────────────────────────────


def derive_fixture(source: Mapping[str, Any], *, copies: int) -> dict[str, Any]:
    """``source`` with its repos repeated ``copies`` times under distinct names.

    Copy 0 is the original; copy ``k`` renames each repo ``<repo>-copy<k>``,
    which gives every needle a distinct task id. ``source`` is not mutated.
    """
    if copies < 1:
        raise ValueError(f"copies must be >= 1, got {copies!r}")
    repos = list(source.get(FIXTURE_LANGUAGE, []))
    derived = dict(source)
    derived[FIXTURE_LANGUAGE] = [_renamed_repo(entry, k) for k in range(copies) for entry in repos]
    return derived


def _renamed_repo(entry: Mapping[str, Any], k: int) -> dict[str, Any]:
    renamed = copy.deepcopy(dict(entry))
    if k:
        renamed["repo"] = f"{entry['repo']}-copy{k}"
    return renamed


def count_fixture_tasks(fixture: Mapping[str, Any]) -> int:
    """Needles across the fixture's repos — one RepoQA task each."""
    repos = fixture.get(FIXTURE_LANGUAGE, [])
    return sum(len(entry["needles"]) for entry in repos)


# ── leg file ────────────────────────────────────────────────────────────


def parse_leg_file(text: str) -> LegFile:
    """Every JSON-object line; a non-JSON line (a SIGKILL's truncated tail) is counted."""
    records: list[dict[str, Any]] = []
    corrupt = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            corrupt += 1
            continue
        if isinstance(record, dict):
            records.append(record)
    return LegFile(records=tuple(records), corrupt_lines=corrupt)


def count_task_records(text: str) -> int:
    """Complete ``task`` records in a leg file's text."""
    return len(task_ids(parse_leg_file(text).records))


def task_ids(records: Iterable[Mapping[str, Any]]) -> list[str]:
    """The ``task_id`` of every ``task`` record, in file order."""
    return [str(r["task_id"]) for r in records if r.get("_event") == EVENT_TASK]


def events_of(records: Iterable[Mapping[str, Any]], kind: str) -> list[Mapping[str, Any]]:
    return [r for r in records if r.get("_event") == kind]


def records_after_last_resume(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the last resuming invocation wrote: the records after its ``run_resume``."""
    marks = [i for i, r in enumerate(records) if r.get("_event") == EVENT_RESUME]
    return list(records[marks[-1] + 1 :]) if marks else []


def final_aggregate_block(records: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
    """The trailing step-less ``metric`` records (before ``run_end``), in file order."""
    tail = list(records)
    if tail and tail[-1].get("_event") == EVENT_END:
        tail.pop()
    block: list[tuple[str, float]] = []
    while tail and tail[-1].get("_event") == EVENT_METRIC and tail[-1].get("step") is None:
        record = tail.pop()
        block.append((str(record["name"]), float(record["value"])))
    return block[::-1]


# ── decisions ───────────────────────────────────────────────────────────


def kill_landed_in_window(recorded: int, *, total: int) -> bool:
    """A mid-leg kill leaves at least one task recorded and at least one not."""
    return 1 <= recorded < total


def check_kill(returncode: int | None, leg: LegFile, *, total: int) -> list[str]:
    """Failures of the killed run: not SIGKILLed, outside the window, or finished."""
    failures: list[str] = []
    if returncode != -signal.SIGKILL:
        failures.append(
            f"expected the runner killed by SIGKILL (returncode {-signal.SIGKILL}), got {returncode!r}"
        )
    recorded = len(task_ids(leg.records))
    if not kill_landed_in_window(recorded, total=total):
        failures.append(
            f"kill landed outside the window: {recorded} task records, expected 1..{total - 1}"
        )
    if events_of(leg.records, EVENT_END):
        failures.append("killed leg already holds a run_end record; the leg was not cut mid-run")
    return failures


def check_resume(killed: LegFile, resumed: LegFile, control_ids: Sequence[str]) -> list[str]:
    """Failures of the resumed leg against the leg as the kill left it.

    ``control_ids`` (the uninterrupted run's task order) is the dataset order.
    """
    killed_ids = task_ids(killed.records)
    failures = _check_killed_prefix(killed_ids, control_ids)
    failures += _check_resume_event(resumed.records, len(killed_ids))
    if resumed.corrupt_lines > killed.corrupt_lines:
        failures.append(
            f"resumed leg has {resumed.corrupt_lines} non-JSON line(s), the killed leg "
            f"{killed.corrupt_lines}; expected the resume to append only whole records"
        )
    second = records_after_last_resume(list(resumed.records))
    already = set(killed_ids)
    expected = [t for t in control_ids if t not in already]
    if task_ids(second) != expected:
        failures.append(f"second invocation ran {task_ids(second)!r}, expected {expected!r}")
    failures += _check_no_rescored_steps(second, len(killed_ids))
    if sorted(task_ids(resumed.records)) != sorted(control_ids):
        failures.append(
            f"resumed leg holds {len(task_ids(resumed.records))} task records, "
            f"expected each of the {len(control_ids)} control tasks exactly once"
        )
    return failures + _check_finished(resumed.records)


def _check_killed_prefix(killed_ids: Sequence[str], control_ids: Sequence[str]) -> list[str]:
    if list(killed_ids) == list(control_ids[: len(killed_ids)]):
        return []
    return [f"tasks recorded before the kill {list(killed_ids)!r} are not a dataset-order prefix"]


def _check_resume_event(records: Sequence[Mapping[str, Any]], recorded: int) -> list[str]:
    resumes = events_of(records, EVENT_RESUME)
    if len(resumes) != 1:
        return [f"expected exactly one {EVENT_RESUME} record, got {len(resumes)}"]
    got = resumes[0].get("recorded_tasks")
    if got != recorded:
        return [f"{EVENT_RESUME} recorded_tasks is {got!r}, expected {recorded}"]
    return []


def _check_no_rescored_steps(second: Sequence[Mapping[str, Any]], recorded: int) -> list[str]:
    """Recorded tasks hold leg positions 0..recorded-1; none may be scored again."""
    steps = sorted(
        {s for r in events_of(second, EVENT_METRIC) if isinstance(s := r.get("step"), int)}
    )
    rescored = [s for s in steps if s < recorded]
    if rescored:
        return [f"second invocation re-scored recorded leg positions {rescored!r}"]
    return []


def _check_finished(records: Sequence[Mapping[str, Any]]) -> list[str]:
    last = records[-1] if records else {}
    if last.get("_event") == EVENT_END and last.get("status") == "finished":
        return []
    return [f"expected the leg to end with a finished {EVENT_END} record, got {dict(last)!r}"]


def is_latency_name(name: str, latency_keys: Iterable[str]) -> bool:
    return any(name.startswith(f"{key}_") for key in latency_keys)


def compare_aggregate_blocks(
    resumed: Sequence[tuple[str, float]],
    control: Sequence[tuple[str, float]],
    latency_keys: Iterable[str],
) -> list[str]:
    """Failures unless both blocks name the same metrics and agree bit for bit
    on every non-latency value (``float.hex`` equality, so ``-0.0``/NaN count)."""
    if not control:
        return ["control leg has no final aggregate block"]
    names = [n for n, _ in resumed]
    if names != [n for n, _ in control]:
        return [f"aggregate names differ: resumed {names!r}, control {[n for n, _ in control]!r}"]
    keys = tuple(latency_keys)
    return [
        f"aggregate {name} differs: resumed {r.hex()} ({r!r}), control {c.hex()} ({c!r})"
        for (name, r), (_, c) in zip(resumed, control, strict=True)
        if not is_latency_name(name, keys) and r.hex() != c.hex()
    ]
