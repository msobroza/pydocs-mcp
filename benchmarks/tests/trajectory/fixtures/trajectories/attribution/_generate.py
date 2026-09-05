"""Generator for the six synthetic attribution trajectory fixtures (Task 6).

Built through the real ``schema.py`` classes and the merge conventions (header
first, tool events by seq, loop events interleaved), so every emitted line is
schema-v1 conformant by construction. Committed as IMMUTABLE raw fixtures; this
script is regeneration provenance only and is NOT run by the test suite (the
tests read + validate the committed files).

Twelve real hand-labeled trajectories now live in ``../real/`` (captured
2026-07-21; see ``../../README.md`` §"Real trajectories"). These six
synthetic-but-realistic merged trajectories remain the deterministic per-path
coverage: they exercise every attribution path so the attributor, every metric,
and the ``compare_labels`` agreement tooling stay validated end-to-end:

1. ``search_surfaces_gold``      — chunk rows (spans) surface + inspect gold → used, hunk overlap.
2. ``grep_hitlist_surfacing``    — grep files_with_matches surfaces (not inspected); read_file inspects; first-touch → grep.
3. ``wasted_read``               — a read of a never-edited file (+ a dependency-absolute exclusion).
4. ``injected_context_excluded`` — INJECTED_CONTEXT_MARKER first line + initiator=injected → excluded from all tiers.
5. ``fired_rules_not_evidence``  — a fired-rule annotation naming a path adds NO evidence.
6. ``budget_elided_items``       — search items enumerate a text-elided gold row → budget-elided first-touch credit.

Run: ``PYTHONPATH=benchmarks/src python .../attribution/_generate.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_eval.trajectory.attribution import INJECTED_CONTEXT_MARKER
from pydocs_eval.trajectory.schema import FiredRule, LoopEvent, ToolEvent, TrajectoryHeader

ROOT = Path(__file__).parent
WS = "/ws"

_IDS = {
    "search_surfaces_gold": "10000000-0000-4000-8000-000000000001",
    "grep_hitlist_surfacing": "10000000-0000-4000-8000-000000000002",
    "wasted_read": "10000000-0000-4000-8000-000000000003",
    "injected_context_excluded": "10000000-0000-4000-8000-000000000004",
    "fired_rules_not_evidence": "10000000-0000-4000-8000-000000000005",
    "budget_elided_items": "10000000-0000-4000-8000-000000000006",
}


def header(tid: str, instance_id: str) -> TrajectoryHeader:
    return TrajectoryHeader(
        trajectory_id=tid,
        artifact_hash="0" * 64,
        pydocs_mcp_version="0.6.0",
        mcp_version="1.28.1",
        claude_cli_version="2.1.76",
        dataset_revision="widgetlib-fixture@1",
        run_config={"model": "claude-haiku-4-5-20251001", "instance_id": instance_id},
    )


def tool(tid: str, seq: int, name: str, args: dict, ids, **kw) -> ToolEvent:
    return ToolEvent(
        event_id=f"{tid}:tool:{seq:06d}",
        trajectory_id=tid,
        seq=seq,
        ts=1000.0 + seq,
        turn=seq,
        tool=name,
        args=args,
        latency_ms=40.0 + seq,
        hit_count=None if ids is None else len(ids),
        result_ids=ids,
        result_preview=kw.get("preview", f"<{name} result>"),
        result_blob=kw.get("blob", "a" * 64),
        result_bytes=kw.get("bytes", 256),
        initiator=kw.get("initiator", "model"),
        suggestion=kw.get("suggestion"),
        fired_rules=kw.get("fired_rules", ()),
    )


def result_loop(tid: str, turn: int, text: str) -> LoopEvent:
    return LoopEvent(
        event_id=f"{tid}:loop:{turn:06d}",
        trajectory_id=tid,
        kind="result",
        turn=turn,
        message_id=f"msg_{tid[-1]}_result",
        text=text,
        is_error=False,
    )


def write_case(name: str, *, lines: list, meta: dict, label: dict) -> None:
    d = ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    with (d / "events.jsonl").open("w", encoding="utf-8") as fh:
        for obj in lines:
            fh.write(json.dumps(obj.to_dict(), sort_keys=True) + "\n")
    (d / "meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (d / "labels.json").write_text(
        json.dumps(label, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _base_meta(name: str, **kw) -> dict:
    meta = {"case": name, "workspace_root": WS}
    meta.update(kw)
    return meta


# 1. search_surfaces_gold ----------------------------------------------------
tid = _IDS["search_surfaces_gold"]
write_case(
    "search_surfaces_gold",
    lines=[
        header(tid, "widgetlib__pricing-discount"),
        tool(
            tid,
            1,
            "search_codebase",
            {"query": "discount", "kind": "docs"},
            [{"path": "widgetlib/pricing.py", "start_line": 8, "end_line": 14}],
            preview="## apply_discount\ndef apply_discount(price, pct): ...",
        ),
        result_loop(tid, 2, "Fixed apply_discount."),
    ],
    meta=_base_meta(
        "search_surfaces_gold",
        description="A docs chunk row (real span) surfaces AND inspects the gold "
        "file; the model edits it. Exercises CONTENT classification, hunk overlap "
        "(seen 8-14 covers gold 10-11), and first-touch credit to search_codebase.",
        gold_files=["widgetlib/pricing.py"],
        gold_line_map={"widgetlib/pricing.py": [10, 11]},
        final_patch_files=["widgetlib/pricing.py"],
        expected={
            "surfaced": ["widgetlib/pricing.py"],
            "inspected": ["widgetlib/pricing.py"],
            "used": ["widgetlib/pricing.py"],
            "wasted": [],
            "first_touch": {"widgetlib/pricing.py": "search_codebase"},
            "mean_hunk_overlap": 1.0,
        },
    ),
    label={
        "trajectory_id": tid,
        "used_files": ["widgetlib/pricing.py"],
        "first_surface": {"widgetlib/pricing.py": "search_codebase"},
    },
)

# 2. grep_hitlist_surfacing --------------------------------------------------
tid = _IDS["grep_hitlist_surfacing"]
write_case(
    "grep_hitlist_surfacing",
    lines=[
        header(tid, "widgetlib__inventory-value"),
        tool(
            tid,
            1,
            "grep",
            {"pattern": "total_value", "output_mode": "files_with_matches"},
            [{"path": "widgetlib/inventory.py", "start_line": 5, "end_line": 5}],
            preview="widgetlib/inventory.py",
        ),
        tool(
            tid,
            2,
            "read_file",
            {"path": "widgetlib/inventory.py"},
            [{"path": "widgetlib/inventory.py", "start_line": 1, "end_line": 40}],
            preview="  1\tdef total_value(items): ...",
        ),
        result_loop(tid, 3, "Fixed total_value."),
    ],
    meta=_base_meta(
        "grep_hitlist_surfacing",
        description="grep files_with_matches surfaces the gold file but is a hit "
        "list (items leak one line — NOT inspected); a later read_file inspects "
        "it. First-touch credit goes to grep (earliest surfacer), not read_file.",
        gold_files=["widgetlib/inventory.py"],
        gold_line_map={"widgetlib/inventory.py": [12, 13]},
        final_patch_files=["widgetlib/inventory.py"],
        expected={
            "surfaced": ["widgetlib/inventory.py"],
            "inspected": ["widgetlib/inventory.py"],
            "used": ["widgetlib/inventory.py"],
            "wasted": [],
            "first_touch": {"widgetlib/inventory.py": "grep"},
        },
    ),
    label={
        "trajectory_id": tid,
        "used_files": ["widgetlib/inventory.py"],
        "first_surface": {"widgetlib/inventory.py": "grep"},
    },
)

# 3. wasted_read (+ dependency-absolute exclusion) ---------------------------
tid = _IDS["wasted_read"]
write_case(
    "wasted_read",
    lines=[
        header(tid, "widgetlib__calculator-average"),
        tool(
            tid,
            1,
            "read_file",
            {"path": "widgetlib/textutil.py"},
            [{"path": "widgetlib/textutil.py", "start_line": 1, "end_line": 30}],
            preview="  1\tdef slugify(t): ...",
        ),
        tool(
            tid,
            2,
            "read_file",
            {"path": "widgetlib/calculator.py"},
            [{"path": "widgetlib/calculator.py", "start_line": 20, "end_line": 26}],
            preview="  20\tdef average(values): ...",
        ),
        tool(
            tid,
            3,
            "read_file",
            {"path": "/venv/lib/python3.11/site-packages/dep/mod.py"},
            [
                {
                    "path": "/venv/lib/python3.11/site-packages/dep/mod.py",
                    "start_line": 1,
                    "end_line": 5,
                }
            ],
            preview="  1\t# a dependency file",
        ),
        result_loop(tid, 4, "Fixed average."),
    ],
    meta=_base_meta(
        "wasted_read",
        description="Two reads: textutil.py is never edited (wasted-read), "
        "calculator.py is edited (used). A third read of a site-packages absolute "
        "path is a dependency (gold_matchable=False) and is excluded from surfaced.",
        gold_files=["widgetlib/calculator.py"],
        gold_line_map={"widgetlib/calculator.py": [25]},
        final_patch_files=["widgetlib/calculator.py"],
        expected={
            "surfaced": ["widgetlib/calculator.py", "widgetlib/textutil.py"],
            "inspected": ["widgetlib/calculator.py", "widgetlib/textutil.py"],
            "used": ["widgetlib/calculator.py"],
            "wasted": ["widgetlib/textutil.py"],
            "first_touch": {
                "widgetlib/calculator.py": "read_file",
                "widgetlib/textutil.py": "read_file",
            },
            "wasted_read_ratio": 0.5,
        },
    ),
    label={
        "trajectory_id": tid,
        "used_files": ["widgetlib/calculator.py"],
        "first_surface": {"widgetlib/calculator.py": "read_file"},
    },
)

# 4. injected_context_excluded ----------------------------------------------
tid = _IDS["injected_context_excluded"]
write_case(
    "injected_context_excluded",
    lines=[
        header(tid, "widgetlib__pricing-discount"),
        tool(
            tid,
            1,
            "get_overview",
            {},
            [{"path": "widgetlib/pricing.py"}],
            preview=f"{INJECTED_CONTEXT_MARKER}\nwidgetlib.pricing — discount helpers",
        ),
        tool(
            tid,
            2,
            "get_overview",
            {},
            [{"path": "widgetlib/textutil.py"}],
            preview="widgetlib.textutil — string helpers",
            initiator="injected",
        ),
        tool(
            tid,
            3,
            "read_file",
            {"path": "widgetlib/calculator.py"},
            [{"path": "widgetlib/calculator.py", "start_line": 20, "end_line": 26}],
            preview="  20\tdef average(values): ...",
        ),
        result_loop(tid, 4, "Fixed average."),
    ],
    meta=_base_meta(
        "injected_context_excluded",
        description="Two injected events (one via INJECTED_CONTEXT_MARKER first "
        "line, one via initiator=injected) surface pricing.py and textutil.py — "
        "both must be excluded from ALL tiers. Only the model-initiated read of "
        "calculator.py counts.",
        gold_files=["widgetlib/calculator.py", "widgetlib/pricing.py"],
        gold_line_map={"widgetlib/calculator.py": [25]},
        final_patch_files=["widgetlib/calculator.py"],
        expected={
            "surfaced": ["widgetlib/calculator.py"],
            "inspected": ["widgetlib/calculator.py"],
            "used": ["widgetlib/calculator.py"],
            "wasted": [],
            "first_touch": {"widgetlib/calculator.py": "read_file"},
            "gold_file_recall": 0.5,
        },
    ),
    label={
        "trajectory_id": tid,
        "used_files": ["widgetlib/calculator.py"],
        "first_surface": {"widgetlib/calculator.py": "read_file"},
    },
)

# 5. fired_rules_not_evidence ------------------------------------------------
tid = _IDS["fired_rules_not_evidence"]
write_case(
    "fired_rules_not_evidence",
    lines=[
        header(tid, "widgetlib__calculator-average"),
        tool(
            tid,
            1,
            "search_codebase",
            {"query": "average", "kind": "docs"},
            [{"path": "widgetlib/calculator.py", "start_line": 20, "end_line": 26}],
            preview="## average\ndef average(values): ...",
            suggestion="Consider get_symbol for widgetlib/pricing.py",
            fired_rules=(
                FiredRule(seq=1, tool="search_codebase", rule="mentions widgetlib/pricing.py"),
            ),
        ),
        result_loop(tid, 2, "Fixed average."),
    ],
    meta=_base_meta(
        "fired_rules_not_evidence",
        description="A fired-rule annotation (and suggestion echo) names "
        "widgetlib/pricing.py, but machinery annotations are NEVER evidence: "
        "pricing.py must not be surfaced. Only the search chunk row for "
        "calculator.py counts.",
        gold_files=["widgetlib/calculator.py"],
        gold_line_map={"widgetlib/calculator.py": [25]},
        final_patch_files=["widgetlib/calculator.py"],
        expected={
            "surfaced": ["widgetlib/calculator.py"],
            "inspected": ["widgetlib/calculator.py"],
            "used": ["widgetlib/calculator.py"],
            "wasted": [],
            "first_touch": {"widgetlib/calculator.py": "search_codebase"},
        },
    ),
    label={
        "trajectory_id": tid,
        "used_files": ["widgetlib/calculator.py"],
        "first_surface": {"widgetlib/calculator.py": "search_codebase"},
    },
)

# 6. budget_elided_items -----------------------------------------------------
tid = _IDS["budget_elided_items"]
write_case(
    "budget_elided_items",
    lines=[
        header(tid, "widgetlib__pricing-discount"),
        tool(
            tid,
            1,
            "search_codebase",
            {"query": "value", "kind": "any"},
            [
                {"path": "widgetlib/pricing.py", "start_line": 8, "end_line": 14},
                {"path": "widgetlib/inventory.py", "start_line": 5, "end_line": 12},
            ],
            preview="## apply_discount\ndef apply_discount(price, pct): ...",
        ),
        tool(
            tid,
            2,
            "read_file",
            {"path": "widgetlib/inventory.py"},
            [{"path": "widgetlib/inventory.py", "start_line": 1, "end_line": 40}],
            preview="  1\tdef total_value(items): ...",
        ),
        result_loop(tid, 3, "Fixed both."),
    ],
    meta=_base_meta(
        "budget_elided_items",
        description="search items[] enumerate two gold rows, but only pricing.py "
        "rendered in the text body (preview) — inventory.py was elided by the "
        "token budget. First-touch credits inventory to search (items-inclusive "
        "surfaced tier), while the model-visible label credits read_file: the "
        "budget-elided over-count the compare_labels tally measures.",
        gold_files=["widgetlib/pricing.py", "widgetlib/inventory.py"],
        gold_line_map={"widgetlib/pricing.py": [10], "widgetlib/inventory.py": [12]},
        final_patch_files=["widgetlib/pricing.py", "widgetlib/inventory.py"],
        expected={
            "surfaced": ["widgetlib/inventory.py", "widgetlib/pricing.py"],
            "inspected": ["widgetlib/inventory.py", "widgetlib/pricing.py"],
            "used": ["widgetlib/inventory.py", "widgetlib/pricing.py"],
            "wasted": [],
            "first_touch": {
                "widgetlib/pricing.py": "search_codebase",
                "widgetlib/inventory.py": "search_codebase",
            },
        },
    ),
    label={
        "trajectory_id": tid,
        "used_files": ["widgetlib/pricing.py", "widgetlib/inventory.py"],
        "first_surface": {
            "widgetlib/pricing.py": "search_codebase",
            "widgetlib/inventory.py": "read_file",
        },
    },
)

print("wrote 6 attribution fixtures under", ROOT)
