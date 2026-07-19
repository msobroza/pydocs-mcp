"""Attribution tier + provenance tests (ADR 0011 action items 1, 5, 6).

Drives the six committed synthetic attribution fixtures through the real
attributor and asserts each declared tier / first-touch outcome, plus the two
provenance rules (INJECTED_CONTEXT_MARKER + initiator=injected exclusion;
fired_rules never evidence) and the fidelity-stamp honesty rule (no hunk metric
from a span-less source).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_eval.trajectory.attribution import (
    ContentClass,
    Fidelity,
    attribute_trajectory,
    classify,
    load_events,
)
from pydocs_eval.trajectory.metrics import hunk_overlap_report
from pydocs_eval.trajectory.schema import ToolEvent

_ATTR = Path(__file__).parent / "fixtures" / "trajectories" / "attribution"
_CASES = (
    "search_surfaces_gold",
    "grep_hitlist_surfacing",
    "wasted_read",
    "injected_context_excluded",
    "fired_rules_not_evidence",
    "budget_elided_items",
)


def _load(case: str):
    meta = json.loads((_ATTR / case / "meta.json").read_text(encoding="utf-8"))
    events = load_events(_ATTR / case / "events.jsonl")
    attribution = attribute_trajectory(
        events,
        final_patch_files=frozenset(meta["final_patch_files"]),
        workspace_root=meta["workspace_root"],
    )
    return attribution, meta


@pytest.mark.parametrize("case", _CASES)
def test_tiers_match_declared_expectation(case: str) -> None:
    attribution, meta = _load(case)
    exp = meta["expected"]
    assert attribution.surfaced_files == frozenset(exp["surfaced"])
    assert attribution.inspected_files == frozenset(exp["inspected"])
    assert attribution.used_files == frozenset(exp["used"])
    assert attribution.wasted_reads == frozenset(exp["wasted"])


@pytest.mark.parametrize("case", _CASES)
def test_first_touch_credit_matches(case: str) -> None:
    attribution, meta = _load(case)
    assert attribution.first_touch == meta["expected"]["first_touch"]


def test_injected_content_excluded_from_all_tiers() -> None:
    # Both an INJECTED_CONTEXT_MARKER first line and an initiator=injected event
    # surface files that must never reach any tier (ADR 0011 R7).
    attribution, _ = _load("injected_context_excluded")
    assert "widgetlib/pricing.py" not in attribution.surfaced_files
    assert "widgetlib/textutil.py" not in attribution.surfaced_files
    assert attribution.surfaced_files == frozenset({"widgetlib/calculator.py"})


def test_fired_rules_never_add_evidence() -> None:
    # A fired-rule annotation + suggestion echo name pricing.py; neither may add
    # it to any tier (machinery annotations are never evidence, ADR 0011).
    attribution, _ = _load("fired_rules_not_evidence")
    assert "widgetlib/pricing.py" not in attribution.surfaced_files
    assert attribution.surfaced_files == frozenset({"widgetlib/calculator.py"})


def test_grep_files_with_matches_is_surfaced_not_inspected() -> None:
    # The per-file grep mode leaks one line in items but is a hit list — surfaced
    # only. The read_file that follows is what makes the file inspected.
    attribution, _ = _load("grep_hitlist_surfacing")
    grep_edges = [s for s in attribution.surfacings if s.tool == "grep"]
    assert grep_edges and all(not s.inspected for s in grep_edges)
    assert "widgetlib/inventory.py" in attribution.inspected_files  # via read_file


def test_dependency_absolute_path_excluded() -> None:
    # A site-packages absolute read is gold_matchable=False → never surfaced.
    attribution, _ = _load("wasted_read")
    assert not any("site-packages" in s.path for s in attribution.surfacings)


def test_fidelity_stamp_honesty_no_hunk_from_span_less_source() -> None:
    # A used gold file surfaced ONLY through a file-level source (glob: path, no
    # span) must land in file_level_only, never in the hunk-scored by_file map —
    # no fabricated line precision (ADR 0011).
    glob_event = ToolEvent(
        event_id="e",
        trajectory_id="t",
        seq=1,
        ts=0.0,
        turn=1,
        tool="glob",
        args={},
        latency_ms=1.0,
        result_ids=({"path": "widgetlib/pricing.py"},),
    )
    attribution = attribute_trajectory(
        [glob_event],
        final_patch_files=frozenset({"widgetlib/pricing.py"}),
        workspace_root="/ws",
    )
    report = hunk_overlap_report(attribution, {"widgetlib/pricing.py": frozenset({10})})
    assert "widgetlib/pricing.py" in report.file_level_only
    assert "widgetlib/pricing.py" not in report.by_file
    assert report.mean() == 1.0  # no hunk evidence → vacuous


def test_injected_marker_matches_product_contract() -> None:
    # The eval copy is a byte-for-byte contract mirror (zero product-import
    # floor); this pins it against the product constant so a drift fails loudly.
    product = pytest.importorskip("pydocs_mcp.application.session_start_context")
    from pydocs_eval.trajectory.attribution import INJECTED_CONTEXT_MARKER

    assert INJECTED_CONTEXT_MARKER == product.INJECTED_CONTEXT_MARKER


def test_classify_table_key_rows() -> None:
    # Spot-check the per-tool/mode classification the tiers rest on.
    assert classify("read_file", {}).content_class is ContentClass.CONTENT
    assert classify("glob", {}).content_class is ContentClass.HIT_LIST
    assert classify("grep", {"output_mode": "files_with_matches"}).content_class is (
        ContentClass.HIT_LIST
    )
    assert classify("grep", {"output_mode": "content"}).content_class is ContentClass.CONTENT
    assert classify("search_codebase", {"kind": "api"}).fidelity is Fidelity.FILE
    assert classify("search_codebase", {"kind": "docs"}).fidelity is Fidelity.HUNK
    assert classify("get_references", {}).fidelity is Fidelity.FILE
