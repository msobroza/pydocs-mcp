"""One-off generator for the four degenerate synthetic trajectory fixtures.

Built through the real schema classes so every emitted line is schema-v1
conformant by construction. Outputs are committed as IMMUTABLE raw fixtures
(Task 5); this script is kept only as regeneration provenance and is NOT run by
the test suite (the tests read + validate the committed files, never rewrite).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_eval.trajectory.schema import (
    LoopEvent,
    ToolEvent,
    TrajectoryHeader,
)

ROOT = Path(
    "/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/"
    "phase-2-instrumentation-spec-498def/benchmarks/tests/trajectory/"
    "fixtures/trajectories/synthetic"
)

# Stable synthetic UUIDs (v4 shape) — synthetic, never from a real rollout.
IDS = {
    "empty_trajectory": "00000000-0000-4000-8000-000000000001",
    "crash_before_first_tool": "00000000-0000-4000-8000-000000000002",
    "patch_apply_failed": "00000000-0000-4000-8000-000000000003",
    "infra_error": "00000000-0000-4000-8000-000000000004",
}


def header(tid: str, instance_id: str) -> TrajectoryHeader:
    return TrajectoryHeader(
        trajectory_id=tid,
        artifact_hash="0" * 64,
        pydocs_mcp_version="0.6.0",
        mcp_version="1.28.1",
        claude_cli_version="2.1.76",
        dataset_revision="widgetlib-fixture@1",
        run_config={
            "model": "claude-haiku-4-5-20251001",
            "provider": "anthropic",
            "max_turns": 15,
            "instance_id": instance_id,
        },
    )


def write_case(name: str, *, lines: list, meta: dict, extra_files: dict[str, str]) -> None:
    d = ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    with (d / "events.jsonl").open("w", encoding="utf-8") as fh:
        for obj in lines:
            fh.write(json.dumps(obj.to_dict(), sort_keys=True) + "\n")
    (d / "meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for fname, text in extra_files.items():
        (d / fname).write_text(text, encoding="utf-8")


# 1. empty_trajectory — the model returned immediately: no assistant text, no
#    tool call, an empty (non-error) result, and an empty patch.
tid = IDS["empty_trajectory"]
write_case(
    "empty_trajectory",
    lines=[
        header(tid, "widgetlib__calculator-average"),
        LoopEvent(
            event_id=f"{tid}:result",
            trajectory_id=tid,
            kind="result",
            turn=0,
            message_id="msg_empty",
            usage={"input_tokens": 12, "output_tokens": 0},
            text="",
            is_error=False,
        ),
    ],
    meta={
        "case": "empty_trajectory",
        "expected_taxonomy": "empty_trajectory",
        "taxonomy_version_note": "ADR 0012 first-match order, label #2",
        "description": (
            "Header + a single empty, non-error result. No tool calls, no "
            "assistant content, empty patch. The rollout produced nothing."
        ),
        "taxonomy_inputs": ["events.jsonl", "trailer.json"],
        "instance_id": "widgetlib__calculator-average",
        "patch_bytes": 0,
    },
    extra_files={
        "trailer.json": json.dumps(
            {
                "trajectory_id": tid,
                "patch_blob": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "patch_bytes": 0,
                "session_id": tid,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    },
)

# 2. crash_before_first_tool — the model emitted one assistant turn, then the
#    run errored (is_error result) before ANY tool call. Distinguished from
#    empty by the presence of pre-crash loop activity + is_error.
tid = IDS["crash_before_first_tool"]
write_case(
    "crash_before_first_tool",
    lines=[
        header(tid, "widgetlib__inventory-value"),
        LoopEvent(
            event_id=f"{tid}:asst0",
            trajectory_id=tid,
            kind="assistant",
            turn=0,
            message_id="msg_crash",
            usage={"input_tokens": 4200, "output_tokens": 37},
            text="Let me look at how total_value is computed.",
        ),
        LoopEvent(
            event_id=f"{tid}:result",
            trajectory_id=tid,
            kind="result",
            turn=0,
            message_id="msg_crash_result",
            text="Error: process exited before completing a tool call",
            is_error=True,
        ),
    ],
    meta={
        "case": "crash_before_first_tool",
        "expected_taxonomy": "crash_before_first_tool",
        "taxonomy_version_note": "ADR 0012 first-match order, label #3",
        "description": (
            "Header + one assistant turn, then an is_error result before any "
            "tool_call event. Pre-crash activity exists, so it is NOT empty; the "
            "crash lands before the first tool, so it is not budget_exhausted."
        ),
        "taxonomy_inputs": ["events.jsonl"],
        "instance_id": "widgetlib__inventory-value",
        "patch_bytes": 0,
    },
    extra_files={},
)

# 3. patch_apply_failed — a real attempt: MCP tool calls + a non-empty patch,
#    but the eval harness could not apply it (the ">>>>> Patch Apply Failed"
#    marker). A MODEL failure (hard=0), included in aggregates (ADR 0012).
tid = IDS["patch_apply_failed"]
write_case(
    "patch_apply_failed",
    lines=[
        header(tid, "widgetlib__pricing-discount"),
        ToolEvent(
            event_id=f"{tid}:1",
            trajectory_id=tid,
            seq=1,
            ts=1000.0,
            turn=1,
            tool="get_symbol",
            args={"target": "widgetlib.pricing.apply_discount", "depth": "source"},
            latency_ms=41.2,
            hit_count=1,
            result_ids=[{"path": "widgetlib/pricing.py", "start_line": 4, "end_line": 12}],
            result_preview="def apply_discount(price, pct): ...",
            result_blob="a" * 64,
            result_bytes=512,
        ),
        LoopEvent(
            event_id=f"{tid}:asst1",
            trajectory_id=tid,
            kind="assistant",
            turn=1,
            message_id="msg_apply",
            usage={"input_tokens": 5100, "output_tokens": 220},
            text="I'll change the return to price * (1 - pct).",
        ),
        LoopEvent(
            event_id=f"{tid}:result",
            trajectory_id=tid,
            kind="result",
            turn=2,
            message_id="msg_apply_result",
            text="Done — updated apply_discount.",
            is_error=False,
        ),
    ],
    meta={
        "case": "patch_apply_failed",
        "expected_taxonomy": "patch_apply_failed",
        "taxonomy_version_note": "ADR 0012 first-match order, label #4 (model fault, in aggregates)",
        "description": (
            "A non-empty patch was produced but the eval harness rejected it: "
            "run_log.txt carries the '>>>>> Patch Apply Failed' marker that "
            "eval_report.classify_infra_marker routes to 'patch_apply_failed'."
        ),
        "taxonomy_inputs": ["events.jsonl", "trailer.json", "run_log.txt"],
        "instance_id": "widgetlib__pricing-discount",
        "patch_bytes": 512,
        "marker": ">>>>> Patch Apply Failed",
    },
    extra_files={
        "trailer.json": json.dumps(
            {
                "trajectory_id": tid,
                "patch_blob": "b" * 64,
                "patch_bytes": 512,
                "session_id": tid,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "run_log.txt": (
            "Applying patch for widgetlib__pricing-discount...\n"
            ">>>>> Patch Apply Failed\n"
            "error: patch does not apply\n"
        ),
    },
)

# 4. infra_error — the eval harness itself failed (tests errored under a broken
#    environment). Labeled + EXCLUDED from score aggregates (ADR 0012).
tid = IDS["infra_error"]
write_case(
    "infra_error",
    lines=[
        header(tid, "widgetlib__textutil-slugify"),
        ToolEvent(
            event_id=f"{tid}:1",
            trajectory_id=tid,
            seq=1,
            ts=1000.0,
            turn=1,
            tool="search_codebase",
            args={"query": "slugify"},
            latency_ms=58.0,
            hit_count=2,
            result_ids=[{"path": "widgetlib/textutil.py", "start_line": 8, "end_line": 16}],
            result_preview="def slugify(title): ...",
            result_blob="c" * 64,
            result_bytes=640,
        ),
        LoopEvent(
            event_id=f"{tid}:result",
            trajectory_id=tid,
            kind="result",
            turn=2,
            message_id="msg_infra",
            text="Applied the lowercase fix.",
            is_error=False,
        ),
    ],
    meta={
        "case": "infra_error",
        "expected_taxonomy": "infra_error",
        "taxonomy_version_note": "ADR 0012 first-match order, label #1 (excluded from aggregates)",
        "description": (
            "The rollout ran fine but the eval harness crashed: run_log.txt "
            "carries the '>>>>> Tests Errored' infra marker. eval_report routes "
            "this to infra_error, which is excluded from score aggregates."
        ),
        "taxonomy_inputs": ["events.jsonl", "run_log.txt"],
        "instance_id": "widgetlib__textutil-slugify",
        "patch_bytes": 380,
        "marker": ">>>>> Tests Errored",
    },
    extra_files={
        "run_log.txt": (
            "Running tests for widgetlib__textutil-slugify...\n"
            ">>>>> Tests Errored\n"
            "ImportError: cannot import name 'pytest' (broken test environment)\n"
        ),
    },
)

print("wrote synthetic fixtures under", ROOT)
