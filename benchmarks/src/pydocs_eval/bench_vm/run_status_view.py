"""How ``status`` prints a run, and the live bench VMs when no run is named.

Plain text for the owner's terminal: the run's identity and frozen inputs, its
live VM, the full status history, then the tail of the current attempt's log,
last, so the newest output sits right above the prompt.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydocs_eval.bench_vm.bench_cloud import LabelledInstance
from pydocs_eval.bench_vm.run_control import RunStatusReport
from pydocs_eval.bench_vm.run_record import RunRecord, RunTransition
from pydocs_eval.bench_vm.vm_request import LABEL_ATTEMPT, LABEL_RUN_ID

LOG_TAIL_LINES = 40
_NONE = "-"


def render_run_status(report: RunStatusReport) -> str:
    record = report.record
    lines = [
        f"run: {record.run_id}",
        f"status: {record.status} at attempt {record.attempt}",
        f"kind: {record.kind}",
        *_commit_lines(record),
        f"image: {record.image} (digest {record.image_digest})",
        f"machine: {_latest_machine(record)}",
        f"live VM: {_live_text(report)}",
        f"last sync: {_or_never(record.last_sync_at)}",
        *([] if record.exit_code is None else [f"exit code: {record.exit_code}"]),
        "history:",
        *(_history_row(transition) for transition in record.history),
        *_log_lines(report),
    ]
    return "\n".join(lines)


def _commit_lines(record: RunRecord) -> list[str]:
    lines = [f"commit: {record.commit} (git_ref {record.git_ref})"]
    if record.baseline_commit is not None:
        lines.append(f"baseline: {record.baseline_commit} (baseline_ref {record.baseline_ref})")
    if record.candidate_commit is not None:
        lines.append(f"candidate: {record.candidate_commit} (candidate_ref {record.candidate_ref})")
    return lines


def _latest_machine(record: RunRecord) -> str:
    machines = [t.machine for t in record.history if t.machine is not None]
    return machines[-1] if machines else "unknown (no transition recorded one)"


def _live_text(report: RunStatusReport) -> str:
    if not report.live:
        return "none"
    return "; ".join(f"{vm.name} in {vm.zone} ({vm.status})" for vm in report.live)


def _or_never(value: object | None) -> str:
    return "never" if value is None else str(value)


def _history_row(transition: RunTransition) -> str:
    return "  ".join(
        (
            f"  {transition.at.isoformat()}",
            f"{transition.status:<9}",
            f"attempt {transition.attempt}",
            f"instance {transition.instance or _NONE}",
            f"zone {transition.zone or _NONE}",
            f"machine {transition.machine or _NONE}",
            transition.detail,
        )
    ).rstrip()


def _log_lines(report: RunStatusReport) -> list[str]:
    if report.log_tail is None:
        return [f"log: {report.log_uri}: no log synced yet for attempt {report.record.attempt}"]
    lines = report.log_tail.decode("utf-8", errors="replace").splitlines()
    if report.log_truncated and lines:
        lines = lines[1:]  # the read began mid-line
    return [f"log: {report.log_uri} (last {LOG_TAIL_LINES} lines)", *lines[-LOG_TAIL_LINES:]]


def render_live_bench_vms(zones: Sequence[str], live: Sequence[LabelledInstance]) -> str:
    if not live:
        return f"no live bench VM in {', '.join(zones)}"
    header = ("run id", "attempt", "zone", "status", "instance")
    rows = [
        (
            vm.labels.get(LABEL_RUN_ID, _NONE),
            vm.labels.get(LABEL_ATTEMPT, _NONE),
            vm.zone,
            vm.status,
            vm.name,
        )
        for vm in live
    ]
    widths = [max(len(row[column]) for row in (header, *rows)) for column in range(len(header))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in (header, *rows)
    )
