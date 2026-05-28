"""Ceiling test for ``# noqa: BLE001`` occurrences (spec S14 / AC-3).

A failing test here means a new broad ``except Exception:`` was added
without first being narrowed. Either tighten the except (preferred) or,
if the broad catch is genuinely required (e.g., a third-party
``inspect.getmembers`` that can raise arbitrary descriptor errors),
bump the threshold AND document the addition in the same PR's commit
message so the increment is reviewed.

The post-PR target per spec AC-3 is 14, but the current tree carries a
small buffer above that to accommodate the existing per-file containment
catches in extraction/strategies/* that pre-date this audit. The
threshold is a CEILING — it must never silently grow.
"""

from __future__ import annotations

from pathlib import Path

# WHY: ceiling, not exact match. The audit's eventual target is 14; the
# current tree carries 20 catches:
#   - 16 across extraction strategies + existing CLI boundaries (each
#     documented with an inline rationale, pre-watcher).
#   - 3 added by the ``serve --watch`` wiring in ``__main__.py``:
#     (a) ``_on_change`` reindex-failure boundary — watcher must keep
#         draining events even if a reindex throws (spec §4.1 + Risk R4).
#     (b) ``_run_watch_loop`` finally fallback — logs unexpected watcher
#         exits without propagating, so the server shutdown path still
#         completes deterministically.
#     (c) ``_cmd_serve`` ``--watch`` top-level CLI boundary — structural
#         mirror of the existing no-watch boundary; uniform verbose /
#         traceback policy across both branches.
#   - 1 added by the standalone ``watch`` subcommand wiring:
#     (d) ``_cmd_watch`` top-level CLI boundary — structural mirror of
#         ``_cmd_serve`` so the standalone watch path enjoys the same
#         verbose / traceback policy as every other ``_cmd_*`` entry.
# Tightening below 20 belongs to a follow-up that actually narrows the
# catches, not to a cosmetic test.
NOQA_BLE001_THRESHOLD = 20

# Marker we search for. The exact spelling (single space between ``noqa:``
# and ``BLE001``) is the form ruff emits and the form the repo uses; a
# future refactor that drops the space would need to update this constant
# in lock-step with the source change.
_NOQA_MARKER = "# noqa: BLE001"

_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "python" / "pydocs_mcp"


def _iter_python_files(root: Path):
    yield from (p for p in root.rglob("*.py") if p.is_file())


def test_noqa_ble001_count_below_threshold() -> None:
    """Fails CI if ``# noqa: BLE001`` count rises above the ceiling.

    Counts the marker inside actual source lines (NOT docstrings or
    comments that merely *mention* the marker for documentation
    purposes). The heuristic: a line that contains both ``except`` AND
    the noqa marker is a real catch; anything else is descriptive.
    """
    matches: list[tuple[Path, int, str]] = []
    for path in _iter_python_files(_PACKAGE_ROOT):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if _NOQA_MARKER in line and "except" in line:
                matches.append((path, lineno, line.strip()))

    assert len(matches) <= NOQA_BLE001_THRESHOLD, (
        f"# noqa: BLE001 count {len(matches)} exceeds ceiling "
        f"{NOQA_BLE001_THRESHOLD}. New broad excepts need an explicit "
        f"narrowing pass before the threshold can grow. Current matches:\n"
        + "\n".join(f"  {p}:{ln}: {src}" for p, ln, src in matches)
    )
