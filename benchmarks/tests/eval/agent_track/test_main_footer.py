"""Honest-footer accounting for the CLI entry (spec §D15, no-silent-caps).

``run_agent_track`` returns only the *admitted* pairs, so the CLI cannot read the
discard count or the money burned on discarded arms from its return value alone —
it must read them back from the ledger, which the orchestrator writes for every
task (admitted AND discarded). This suite pins that ledger-back accounting:
``_footer_stats_from_ledger`` counts ``discarded``-keyed lines and sums every
per-arm cost field across the whole ledger. Offline: it only reads a JSONL file.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.eval.agent_track.__main__ import _footer_stats_from_ledger


def _write_ledger(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_missing_ledger_yields_zeroes(tmp_path: Path) -> None:
    # No run yet → no ledger → an all-zero footer, not a crash.
    assert _footer_stats_from_ledger(tmp_path / "absent.jsonl") == (0, 0.0)


def test_counts_discards_and_sums_admitted_cost(tmp_path: Path) -> None:
    ledger = tmp_path / "pairs.jsonl"
    _write_ledger(
        ledger,
        [
            {"task_id": "a", "qa_type": "How", "bare_cost": 4.0, "indexed_cost": 3.5},
            {"task_id": "b", "discarded": "arm-timeout:indexed"},
            {"task_id": "c", "qa_type": "What", "bare_cost": 2.0, "indexed_cost": 3.0},
            {"task_id": "d", "discarded": "judge-failed"},
        ],
    )
    discarded, spend = _footer_stats_from_ledger(ledger)
    # Two discard lines → the footer no longer reads 'discarded: 0'.
    assert discarded == 2
    # Spend is the ledger's recorded per-arm cost, summed across every line.
    assert spend == 4.0 + 3.5 + 2.0 + 3.0


def test_discard_line_with_recorded_arm_cost_is_counted(tmp_path: Path) -> None:
    # Future-proofing the honest-spend contract: if the orchestrator later records
    # a discarded task's per-arm cost on its discard line, the total must include
    # it WITHOUT changing this caller — the sum keys on cost fields, not line kind.
    ledger = tmp_path / "pairs.jsonl"
    _write_ledger(
        ledger,
        [
            {"task_id": "a", "bare_cost": 5.0, "indexed_cost": 5.0},
            {"task_id": "b", "discarded": "arm-timeout:indexed", "bare_cost": 4.0},
        ],
    )
    discarded, spend = _footer_stats_from_ledger(ledger)
    assert discarded == 1
    # The discarded arm's $4 is real money — it must land in 'total spend'.
    assert spend == 5.0 + 5.0 + 4.0


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    ledger = tmp_path / "pairs.jsonl"
    ledger.write_text(
        '{"task_id": "a", "bare_cost": 1.0, "indexed_cost": 1.0}\n\n  \n',
        encoding="utf-8",
    )
    assert _footer_stats_from_ledger(ledger) == (0, 2.0)
