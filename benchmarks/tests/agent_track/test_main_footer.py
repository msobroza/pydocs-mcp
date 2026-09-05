"""Honest-footer accounting for the CLI entry (spec §D15, no-silent-caps).

``run_agent_track`` returns only the *admitted* pairs, so the CLI cannot read the
discard count or the money burned on discarded arms from its return value alone —
it must read them back from the ledger, which the orchestrator writes for every
task (admitted AND discarded). This suite pins that ledger-back accounting:
``_footer_stats_from_ledger`` counts ``discarded``-keyed lines and sums every
per-arm cost field over the rows of THIS run's arm. Offline: it only reads a
JSONL file.

Arm scoping is load-bearing since the resume key widened (run-contract design
§8): a different arm re-runs and APPENDS to the same file, so one ledger holds
several arms' rows and an unfiltered sum would report this arm's pairs beside
every arm's money.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_eval.agent_track.__main__ import _footer_stats_from_ledger

_ARM = "arm-hash-a"
_OTHER_ARM = "arm-hash-b"


def _write_ledger(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_missing_ledger_yields_zeroes(tmp_path: Path) -> None:
    # No run yet → no ledger → an all-zero footer, not a crash.
    assert _footer_stats_from_ledger(tmp_path / "absent.jsonl", arm_hash=_ARM) == (0, 0.0)


def test_counts_discards_and_sums_admitted_cost(tmp_path: Path) -> None:
    ledger = tmp_path / "pairs.jsonl"
    _write_ledger(
        ledger,
        [
            {
                "task_id": "a",
                "arm_hash": _ARM,
                "qa_type": "How",
                "bare_cost": 4.0,
                "indexed_cost": 3.5,
            },
            {"task_id": "b", "arm_hash": _ARM, "discarded": "arm-timeout:indexed"},
            {
                "task_id": "c",
                "arm_hash": _ARM,
                "qa_type": "What",
                "bare_cost": 2.0,
                "indexed_cost": 3.0,
            },
            {"task_id": "d", "arm_hash": _ARM, "discarded": "judge-failed"},
        ],
    )
    discarded, spend = _footer_stats_from_ledger(ledger, arm_hash=_ARM)
    # Two discard lines → the footer no longer reads 'discarded: 0'.
    assert discarded == 2
    # Spend is the ledger's recorded per-arm cost, summed across this arm's lines.
    assert spend == 4.0 + 3.5 + 2.0 + 3.0


def test_another_arms_rows_are_not_this_runs_spend(tmp_path: Path) -> None:
    # The multi-arm file the widened resume key makes reachable: reporting B's
    # pairs beside A+B's money would roughly double the money actually burned
    # on the arm the report describes.
    ledger = tmp_path / "pairs.jsonl"
    _write_ledger(
        ledger,
        [
            {"task_id": "a", "arm_hash": _ARM, "bare_cost": 1.0, "indexed_cost": 2.0},
            {"task_id": "b", "arm_hash": _ARM, "discarded": "judge-failed"},
            {"task_id": "a", "arm_hash": _OTHER_ARM, "bare_cost": 1.0, "indexed_cost": 2.0},
            {"task_id": "b", "arm_hash": _OTHER_ARM, "discarded": "judge-failed"},
        ],
    )
    assert _footer_stats_from_ledger(ledger, arm_hash=_ARM) == (1, 3.0)
    assert _footer_stats_from_ledger(ledger, arm_hash=_OTHER_ARM) == (1, 3.0)


def test_legacy_rows_without_an_arm_hash_are_not_counted(tmp_path: Path) -> None:
    # A row written before the key widened belongs to no arm — its task re-runs
    # (the orchestrator's rule), so its money is not this run's either.
    ledger = tmp_path / "pairs.jsonl"
    _write_ledger(
        ledger,
        [
            {"task_id": "old", "bare_cost": 9.0, "indexed_cost": 9.0},
            {"task_id": "old2", "discarded": "arm-timeout:bare"},
            {"task_id": "new", "arm_hash": _ARM, "bare_cost": 1.0, "indexed_cost": 1.0},
        ],
    )
    assert _footer_stats_from_ledger(ledger, arm_hash=_ARM) == (0, 2.0)


def test_discard_line_with_recorded_arm_cost_is_counted(tmp_path: Path) -> None:
    # Future-proofing the honest-spend contract: if the orchestrator later records
    # a discarded task's per-arm cost on its discard line, the total must include
    # it WITHOUT changing this caller — the sum keys on cost fields, not line kind.
    ledger = tmp_path / "pairs.jsonl"
    _write_ledger(
        ledger,
        [
            {"task_id": "a", "arm_hash": _ARM, "bare_cost": 5.0, "indexed_cost": 5.0},
            {
                "task_id": "b",
                "arm_hash": _ARM,
                "discarded": "arm-timeout:indexed",
                "bare_cost": 4.0,
            },
        ],
    )
    discarded, spend = _footer_stats_from_ledger(ledger, arm_hash=_ARM)
    assert discarded == 1
    # The discarded arm's $4 is real money — it must land in 'total spend'.
    assert spend == 5.0 + 5.0 + 4.0


def test_blank_and_corrupt_lines_are_skipped(tmp_path: Path) -> None:
    # One corrupt-line policy shared with the orchestrator's resume read: a
    # truncated line from a killed run is logged and skipped, never a crash
    # mid-report.
    ledger = tmp_path / "pairs.jsonl"
    ledger.write_text(
        '{"task_id": "a", "arm_hash": "arm-hash-a", "bare_cost": 1.0, "indexed_cost": 1.0}\n'
        "\n  \n"
        '{"task_id": "b", "arm_hash": "arm-h\n',
        encoding="utf-8",
    )
    assert _footer_stats_from_ledger(ledger, arm_hash=_ARM) == (0, 2.0)
