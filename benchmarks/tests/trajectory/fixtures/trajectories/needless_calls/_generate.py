"""Generator for the needless-call metric fixtures.

Built through the real ``schema.py`` classes (header first, tool events by seq),
so every emitted line is schema-v1 conformant by construction. Committed as
IMMUTABLE raw fixtures; this script is regeneration provenance only and is NOT
run by the test suite (the tests read + validate the committed files).

One case per component of the needless-call rate, one case for the pointer
companion, and one trajectory where none of the four conditions fire:

1. ``resurfacing_repeat_search``  — a second search whose every row was already seen.
2. ``zero_yield_empty_search``    — an empty result with no error (an errored call does NOT count).
3. ``fan_out_where_batch``        — three single-target symbol calls in one turn.
4. ``tool_mismatch_dotted_query`` — a search whose query is shaped like a dotted path.
5. ``pointer_followed``           — three pointers offered, two of them issued.
6. ``clean_trajectory``           — none of the four conditions fire; one batched follow-up.

Each ``meta.json`` carries the FULL expected metric block, so the test compares
one computed dict against one committed dict per case.

Run: ``PYTHONPATH=benchmarks/src python .../needless_calls/_generate.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydocs_eval.trajectory.schema import ToolEvent, TrajectoryHeader

ROOT = Path(__file__).parent

_IDS = {
    "resurfacing_repeat_search": "20000000-0000-4000-8000-000000000001",
    "zero_yield_empty_search": "20000000-0000-4000-8000-000000000002",
    "fan_out_where_batch": "20000000-0000-4000-8000-000000000003",
    "tool_mismatch_dotted_query": "20000000-0000-4000-8000-000000000004",
    "pointer_followed": "20000000-0000-4000-8000-000000000005",
    "clean_trajectory": "20000000-0000-4000-8000-000000000006",
}

_DISCOUNT = {"path": "widgetlib/pricing.py", "start_line": 8, "end_line": 14}
_CART = {"path": "widgetlib/cart.py", "start_line": 1, "end_line": 20}
_TAX = {"path": "widgetlib/tax.py", "start_line": 3, "end_line": 9}
_DISCOUNT_BODY = {"path": "widgetlib/pricing.py", "start_line": 15, "end_line": 40}
_CART_BODY = {"path": "widgetlib/cart.py", "start_line": 21, "end_line": 60}

_DISCOUNT_NAME = "widgetlib.pricing.apply_discount"
_CART_NAME = "widgetlib.cart.Cart"
_TAX_NAME = "widgetlib.tax.rate"


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
    *,
    preview: str | None = None,
    error: dict[str, str] | None = None,
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
        error=error,
        hit_count=None if ids is None else len(ids),
        result_ids=None if ids is None else tuple(ids),
        result_preview=preview or f"<{name} result>",
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


def expected(
    *,
    rate: float,
    needless: int,
    total: int,
    resurfacing: int = 0,
    zero_yield: int = 0,
    fan_out: int = 0,
    mismatch: int = 0,
    pointer_rate: float | None,
    parallel: float,
    batch_ratio: float | None,
    batch_calls: int = 0,
    fan_out_calls: int = 0,
) -> dict[str, Any]:
    return {
        "needless_call_rate": rate,
        "needless_calls": needless,
        "total_calls": total,
        "resurfacing": resurfacing,
        "zero_yield": zero_yield,
        "fan_out_where_batch": fan_out,
        "tool_mismatch": mismatch,
        "pointer_followed_rate": pointer_rate,
        "parallel_calls_per_turn": parallel,
        "batch_vs_fanout_ratio": batch_ratio,
        "batch_calls": batch_calls,
        "fan_out_calls": fan_out_calls,
    }


def resurfacing_repeat_search() -> None:
    name = "resurfacing_repeat_search"
    tid = _IDS[name]
    write_case(
        name,
        events=[
            header(tid),
            tool(
                tid, 1, 1, "search_codebase", {"query": "how discounts apply"}, [_DISCOUNT, _CART]
            ),
            tool(tid, 2, 2, "search_codebase", {"query": "discount rounding"}, [_DISCOUNT]),
        ],
        meta={
            "case": name,
            "description": (
                "The second search returns only a row the first search already returned, so "
                "every identifier it surfaced was seen earlier in the trajectory."
            ),
            "expected": expected(
                rate=0.5,
                needless=1,
                total=2,
                resurfacing=1,
                pointer_rate=None,
                parallel=1.0,
                batch_ratio=None,
            ),
        },
    )


def zero_yield_empty_search() -> None:
    name = "zero_yield_empty_search"
    tid = _IDS[name]
    write_case(
        name,
        events=[
            header(tid),
            tool(tid, 1, 1, "search_codebase", {"query": "widget audit trail"}, []),
            tool(
                tid,
                2,
                2,
                "get_symbol",
                {"target": "widgetlib.audit.Trail"},
                None,
                error={"type": "SymbolNotFoundError", "message": "no such target"},
            ),
        ],
        meta={
            "case": name,
            "description": (
                "An empty search result with no error is zero-yield; the failing symbol call "
                "that follows is NOT — it reported its own failure. The failing call is still a "
                "single-target call of a batchable tool, so it counts as fan-out for the ratio."
            ),
            "expected": expected(
                rate=0.5,
                needless=1,
                total=2,
                zero_yield=1,
                pointer_rate=None,
                parallel=1.0,
                batch_ratio=0.0,
                fan_out_calls=1,
            ),
        },
    )


def fan_out_where_batch() -> None:
    name = "fan_out_where_batch"
    tid = _IDS[name]
    write_case(
        name,
        events=[
            header(tid),
            tool(tid, 1, 1, "get_symbol", {"target": _DISCOUNT_NAME}, [_DISCOUNT]),
            tool(tid, 2, 1, "get_symbol", {"target": _CART_NAME}, [_CART]),
            tool(tid, 3, 1, "get_symbol", {"target": _TAX_NAME}, [_TAX]),
            tool(
                tid,
                4,
                2,
                "get_context",
                {"targets": [_DISCOUNT_NAME, _CART_NAME]},
                [_DISCOUNT_BODY, _CART],
            ),
        ],
        meta={
            "case": name,
            "description": (
                "Three single-target symbol calls in one turn, where one batched context call "
                "would have fetched all three. Every call in the group is charged, because one "
                "batch call replaces the whole group. The batched call in the next turn is not "
                "charged, and its rows are not all previously seen."
            ),
            "expected": expected(
                rate=0.75,
                needless=3,
                total=4,
                fan_out=3,
                pointer_rate=None,
                parallel=2.0,
                batch_ratio=0.25,
                batch_calls=1,
                fan_out_calls=3,
            ),
        },
    )


def tool_mismatch_dotted_query() -> None:
    name = "tool_mismatch_dotted_query"
    tid = _IDS[name]
    write_case(
        name,
        events=[
            header(tid),
            tool(tid, 1, 1, "search_codebase", {"query": _DISCOUNT_NAME}, [_DISCOUNT]),
            tool(tid, 2, 1, "search_codebase", {"query": "how discounts apply"}, [_CART]),
        ],
        meta={
            "case": name,
            "description": (
                "A search whose query is a dotted path is a tool mismatch — that name is what "
                "the symbol tool resolves directly. The prose query beside it is not."
            ),
            "expected": expected(
                rate=0.5,
                needless=1,
                total=2,
                mismatch=1,
                pointer_rate=None,
                parallel=2.0,
                batch_ratio=None,
            ),
        },
    )


_POINTER_RESPONSE = (
    "## apply_discount\n"
    "widgetlib/pricing.py:8-14\n"
    f'together: → get_symbol(target="{_DISCOUNT_NAME}")  '
    f'→ get_references(target="{_DISCOUNT_NAME}", direction="callers")\n'
    f'then: → get_symbol(target="{_DISCOUNT_NAME}", depth="source")\n'
)


def pointer_followed() -> None:
    name = "pointer_followed"
    tid = _IDS[name]
    write_case(
        name,
        events=[
            header(tid),
            tool(
                tid,
                1,
                1,
                "search_codebase",
                {"query": "how discounts apply"},
                [_DISCOUNT],
                preview=_POINTER_RESPONSE,
            ),
            tool(tid, 2, 2, "get_symbol", {"target": _DISCOUNT_NAME}, [_DISCOUNT_BODY]),
            tool(
                tid,
                3,
                2,
                "get_references",
                {"target": _DISCOUNT_NAME, "direction": "callers"},
                [_CART],
            ),
        ],
        meta={
            "case": name,
            "description": (
                "One response offers three pointers; the next turn issues two of them. The "
                "source-depth pointer is never issued. Two single-target calls in one turn are "
                "below the fan-out threshold, and the reference call has no batch counterpart."
            ),
            "expected": expected(
                rate=0.0,
                needless=0,
                total=3,
                pointer_rate=2 / 3,
                parallel=1.5,
                batch_ratio=0.0,
                fan_out_calls=1,
            ),
        },
    )


_BATCH_RESPONSE = (
    "## apply_discount\n"
    "widgetlib/pricing.py:8-14\n"
    f'together: → get_context(targets=["{_DISCOUNT_NAME}", "{_CART_NAME}"])\n'
)


def clean_trajectory() -> None:
    name = "clean_trajectory"
    tid = _IDS[name]
    write_case(
        name,
        events=[
            header(tid),
            tool(
                tid,
                1,
                1,
                "search_codebase",
                {"query": "how discounts apply"},
                [_DISCOUNT],
                preview=_BATCH_RESPONSE,
            ),
            tool(
                tid,
                2,
                2,
                "get_context",
                {"targets": [_DISCOUNT_NAME, _CART_NAME]},
                [_DISCOUNT_BODY, _CART_BODY],
            ),
        ],
        meta={
            "case": name,
            "description": (
                "None of the four conditions fire: every call yields new rows, nothing fans "
                "out, no query is a dotted path. The offered batch pointer is followed, so the "
                "pointer rate is 1.0 and every target-fetching call is a batch call."
            ),
            "expected": expected(
                rate=0.0,
                needless=0,
                total=2,
                pointer_rate=1.0,
                parallel=1.0,
                batch_ratio=1.0,
                batch_calls=1,
            ),
        },
    )


def main() -> None:
    resurfacing_repeat_search()
    zero_yield_empty_search()
    fan_out_where_batch()
    tool_mismatch_dotted_query()
    pointer_followed()
    clean_trajectory()


if __name__ == "__main__":
    main()
