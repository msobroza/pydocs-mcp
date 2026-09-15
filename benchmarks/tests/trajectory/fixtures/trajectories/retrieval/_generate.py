"""Generator for the retrieval + tool-usage metric fixtures.

Built through the real ``schema.py`` classes (header first, tool events by seq),
so every emitted line is schema-v1 conformant by construction. Committed as
IMMUTABLE raw fixtures; this script is regeneration provenance only and is NOT
run by the test suite (the tests read + validate the committed files).

One case per question the metrics answer:

1. ``union_of_reformulations``  — two reformulated searches, each reaching ONE of
   the two gold files; only their union covers both.
2. ``needle_through_read_file`` — the search misses the gold entirely and a later
   read reaches it, so the needle is reached by a tool that ranks nothing.
3. ``used_calls_attributed``    — a final patch exists, so the used calls are the
   ones whose rows became attributed evidence.
4. ``used_calls_not_needless``  — the SAME calls with no patch to attribute
   against, so the used count falls back to "not needless".
5. ``no_search_calls``          — the trajectory never searched: the retrieval
   numbers are undefined, not zero, while the usage counts stay defined.

Each ``meta.json`` carries the gold + workspace facts the metrics need and the
FULL expected metric block, so a case is one computed dict against one committed
dict. ``final_patch_files`` is present ONLY in the attributed case — its absence
is what selects the fallback definition of a used call.

Run: ``PYTHONPATH=benchmarks/src python .../retrieval/_generate.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydocs_eval.trajectory.schema import ToolEvent, TrajectoryHeader

ROOT = Path(__file__).parent

_IDS = {
    "union_of_reformulations": "30000000-0000-4000-8000-000000000001",
    "needle_through_read_file": "30000000-0000-4000-8000-000000000002",
    "used_calls_attributed": "30000000-0000-4000-8000-000000000003",
    "used_calls_not_needless": "30000000-0000-4000-8000-000000000004",
    "no_search_calls": "30000000-0000-4000-8000-000000000005",
}

WORKSPACE_ROOT = "/ws"
_K_VALUES = (1, 5, 10)

_PRICING = {"path": "widgetlib/pricing.py", "start_line": 8, "end_line": 14}
_CART = {"path": "widgetlib/cart.py", "start_line": 1, "end_line": 20}
_UTIL = {"path": "widgetlib/util.py", "start_line": 3, "end_line": 9}
_HELPERS = {"path": "widgetlib/helpers.py", "start_line": 1, "end_line": 6}
_PRICING_BODY = {"path": "widgetlib/pricing.py", "start_line": 1, "end_line": 40}


def header(tid: str) -> TrajectoryHeader:
    return TrajectoryHeader(
        trajectory_id=tid,
        artifact_hash="0" * 64,
        pydocs_mcp_version="0.7.0",
        mcp_version="1.28.1",
        claude_cli_version="2.1.76",
        dataset_revision="widgetlib-fixture@1",
        run_config={"model": "claude-haiku-4-5-20251001"},
    )


def tool(
    tid: str,
    seq: int,
    turn: int,
    name: str,
    args: dict[str, Any],
    ids: list[dict[str, Any]] | None,
) -> ToolEvent:
    return ToolEvent(
        event_id=f"{tid}:tool:{seq:06d}",
        trajectory_id=tid,
        seq=seq,
        ts=1000.0 + seq,
        turn=turn,
        tool=name,
        args=args,
        latency_ms=40.0 + seq,
        hit_count=None if ids is None else len(ids),
        result_ids=None if ids is None else tuple(ids),
        result_preview=f"<{name} result>",
        result_blob="a" * 64,
        result_bytes=256,
    )


def write_case(name: str, *, events: list[Any], meta: dict[str, Any]) -> None:
    case_dir = ROOT / name
    case_dir.mkdir(parents=True, exist_ok=True)
    with (case_dir / "events.jsonl").open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
    (case_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def call_scores(*, seq: int, query: str, results: int, rank: int | None) -> dict[str, Any]:
    """One search call's expected block, derived from the rank of its first gold row."""
    reached = {k: 1.0 if rank is not None and rank <= k else 0.0 for k in _K_VALUES}
    return {
        "seq": seq,
        "query": query,
        "results": results,
        **{f"recall@{k}": value for k, value in reached.items()},
        **{f"hit@{k}": value for k, value in reached.items()},
        "mrr": 0.0 if rank is None else 1.0 / rank,
    }


def expected(
    *,
    search_calls: int,
    reformulations: int,
    trajectory_recall: dict[int, float | None],
    first_call: dict[str, Any] | None,
    best_call: dict[str, Any] | None,
    tool_calls_total: int,
    calls_by_tool: dict[str, int],
    tool_calls_used: int,
    used_call_definition: str,
    used_call_ratio: float,
    needle_reached: bool,
    tool_calls_to_first_gold: int | None,
) -> dict[str, Any]:
    """The whole expected block: retrieval, then usage, then gold reach."""
    return {
        "search_calls": search_calls,
        "reformulations": reformulations,
        **{f"trajectory_recall@{k}": v for k, v in trajectory_recall.items()},
        "first_call": first_call,
        "best_call": best_call,
        "tool_calls_total": tool_calls_total,
        "distinct_tools_used": len(calls_by_tool),
        "calls_by_tool": calls_by_tool,
        "tool_calls_used": tool_calls_used,
        "used_call_definition": used_call_definition,
        "used_call_ratio": used_call_ratio,
        "needle_reached": needle_reached,
        "tool_calls_to_first_gold": tool_calls_to_first_gold,
    }


def union_of_reformulations() -> None:
    name = "union_of_reformulations"
    tid = _IDS[name]
    first = call_scores(seq=1, query="how discounts apply", results=3, rank=3)
    best = call_scores(seq=2, query="where cart totals are summed", results=2, rank=1)
    write_case(
        name,
        events=[
            header(tid),
            tool(
                tid, 1, 1, "search_codebase", {"query": first["query"]}, [_UTIL, _HELPERS, _PRICING]
            ),
            tool(tid, 2, 2, "search_codebase", {"query": best["query"]}, [_CART, _UTIL]),
        ],
        meta={
            "case": name,
            "description": (
                "Two reformulated searches over a two-file gold. The first reaches the pricing "
                "file at rank 3, the second the cart file at rank 1, so neither call covers more "
                "than half the gold at any k; the union of their top-5 rows covers all of it."
            ),
            "gold_files": ["widgetlib/cart.py", "widgetlib/pricing.py"],
            "workspace_root": WORKSPACE_ROOT,
            "expected": expected(
                search_calls=2,
                reformulations=2,
                # The top-1 union is {util, cart} — half the gold; top-5 adds pricing.
                trajectory_recall={1: 0.5, 5: 1.0, 10: 1.0},
                first_call=first,
                best_call=best,
                tool_calls_total=2,
                calls_by_tool={"search_codebase": 2},
                tool_calls_used=2,
                used_call_definition="not_needless",
                used_call_ratio=1.0,
                needle_reached=True,
                tool_calls_to_first_gold=1,
            ),
        },
    )


def needle_through_read_file() -> None:
    name = "needle_through_read_file"
    tid = _IDS[name]
    only = call_scores(seq=1, query="where rounding happens", results=2, rank=None)
    write_case(
        name,
        events=[
            header(tid),
            tool(tid, 1, 1, "search_codebase", {"query": only["query"]}, [_UTIL, _HELPERS]),
            tool(tid, 2, 2, "read_file", {"path": "widgetlib/pricing.py"}, [_PRICING_BODY]),
        ],
        meta={
            "case": name,
            "description": (
                "The search never returns the gold file, so every retrieval number is a measured "
                "zero; the read that follows puts the gold in front of the model, so the needle "
                "IS reached — by a tool that ranks nothing."
            ),
            "gold_files": ["widgetlib/pricing.py"],
            "workspace_root": WORKSPACE_ROOT,
            "expected": expected(
                search_calls=1,
                reformulations=1,
                trajectory_recall={1: 0.0, 5: 0.0, 10: 0.0},
                first_call=only,
                best_call=only,
                tool_calls_total=2,
                calls_by_tool={"read_file": 1, "search_codebase": 1},
                tool_calls_used=2,
                used_call_definition="not_needless",
                used_call_ratio=1.0,
                needle_reached=True,
                tool_calls_to_first_gold=2,
            ),
        },
    )


def _used_call_events(tid: str) -> list[Any]:
    """Three calls: one reaching the gold, one aside, one yielding nothing."""
    return [
        header(tid),
        tool(tid, 1, 1, "search_codebase", {"query": "discount rounding"}, [_PRICING, _UTIL]),
        tool(tid, 2, 2, "get_symbol", {"target": "widgetlib.helpers.render"}, [_HELPERS]),
        tool(tid, 3, 3, "search_codebase", {"query": "audit trail"}, []),
    ]


def _used_call_meta(
    name: str,
    *,
    description: str,
    tool_calls_used: int,
    used_call_definition: str,
    used_call_ratio: float,
) -> dict[str, Any]:
    """The expected block shared by the two used-call cases, minus what differs."""
    reaching = call_scores(seq=1, query="discount rounding", results=2, rank=1)
    return {
        "case": name,
        "description": description,
        "gold_files": ["widgetlib/pricing.py"],
        "workspace_root": WORKSPACE_ROOT,
        "expected": expected(
            search_calls=2,
            reformulations=2,
            trajectory_recall={1: 1.0, 5: 1.0, 10: 1.0},
            first_call=reaching,
            best_call=reaching,
            tool_calls_total=3,
            calls_by_tool={"get_symbol": 1, "search_codebase": 2},
            tool_calls_used=tool_calls_used,
            used_call_definition=used_call_definition,
            used_call_ratio=used_call_ratio,
            needle_reached=True,
            tool_calls_to_first_gold=1,
        ),
    }


def used_calls_attributed() -> None:
    name = "used_calls_attributed"
    write_case(
        name,
        events=_used_call_events(_IDS[name]),
        meta={
            **_used_call_meta(
                name,
                description=(
                    "The final patch touches the pricing file, so exactly the call whose rows "
                    "became attributed evidence counts as used — the aside does not, even "
                    "though it returned rows nothing charged as needless."
                ),
                tool_calls_used=1,
                used_call_definition="attributed_evidence",
                used_call_ratio=1 / 3,
            ),
            "final_patch_files": ["widgetlib/pricing.py"],
        },
    )


def used_calls_not_needless() -> None:
    name = "used_calls_not_needless"
    write_case(
        name,
        events=_used_call_events(_IDS[name]),
        meta=_used_call_meta(
            name,
            description=(
                "The same three calls with no patch to attribute against: the used count falls "
                "back to the calls no needless-call component charged, which spares the aside "
                "and charges only the empty search."
            ),
            tool_calls_used=2,
            used_call_definition="not_needless",
            used_call_ratio=2 / 3,
        ),
    )


def no_search_calls() -> None:
    name = "no_search_calls"
    tid = _IDS[name]
    write_case(
        name,
        events=[
            header(tid),
            tool(tid, 1, 1, "get_overview", {"package": "widgetlib"}, [_UTIL]),
            tool(tid, 2, 2, "read_file", {"path": "widgetlib/cart.py"}, [_CART]),
        ],
        meta={
            "case": name,
            "description": (
                "The agent never searched, so there is no ranking to score: every retrieval "
                "number is undefined rather than zero. The usage counts are unaffected — two "
                "calls were made and neither was needless."
            ),
            "gold_files": ["widgetlib/pricing.py"],
            "workspace_root": WORKSPACE_ROOT,
            "expected": expected(
                search_calls=0,
                reformulations=0,
                trajectory_recall=dict.fromkeys(_K_VALUES),
                first_call=None,
                best_call=None,
                tool_calls_total=2,
                calls_by_tool={"get_overview": 1, "read_file": 1},
                tool_calls_used=2,
                used_call_definition="not_needless",
                used_call_ratio=1.0,
                needle_reached=False,
                tool_calls_to_first_gold=None,
            ),
        },
    )


def main() -> None:
    union_of_reformulations()
    needle_through_read_file()
    used_calls_attributed()
    used_calls_not_needless()
    no_search_calls()


if __name__ == "__main__":
    main()
