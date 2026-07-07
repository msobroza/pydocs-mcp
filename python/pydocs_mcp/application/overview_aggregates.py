"""Index-time overview aggregates — git-activity summary (§D17 block 9).

Two halves, both index-time only:

- :func:`compute_activity` is a PURE aggregator over the framed ``git log`` dump
  that :func:`pydocs_mcp.extraction.decisions._git.read_git_log` produces (same
  ``commit``/``author-date``/``files``/``==END==`` line frame the
  ``commit_messages`` source parses). It groups commits by the touched files'
  containing directory (the "module" grouping = path prefix), counts commits per
  module inside the window, and computes a 30d-vs-prior-30d trend ratio. No I/O.

- :class:`ActivitySummary` serialises to/from the JSON stored in the
  ``index_metadata.activity_summary`` column; :class:`OverviewAggregates` is the
  read-side bundle ``OverviewService`` gets from its injected reader closure.

The subprocess spawn (``read_git_log``) lives in the composition-root writer
closure (``storage.factories``), NOT here — this module stays pure so the
aggregator is unit-testable with a framed-string fixture and no git repo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# A commit's author-date is an epoch float; the window / trend split are in days.
_SECONDS_PER_DAY = 86_400.0
# Trend compares the most-recent 30d against the immediately-prior 30d.
_TREND_WINDOW_DAYS = 30.0
# The activity card lists the busiest modules; more than this floods the block.
_MAX_ACTIVITY_MODULES = 5
_END_MARKER = "==END=="
_COMMIT_PREFIX = "commit "
_AUTHOR_DATE_PREFIX = "author-date "
_FILES_PREFIX = "files "


@dataclass(frozen=True, slots=True)
class ActivitySummary:
    """Aggregated git activity for the overview's ``## Recent activity`` block.

    ``top_modules`` is ``(module_path_prefix, commit_count)`` pairs sorted by
    count desc (name tie-break), capped at five. ``trend_ratio`` is the
    most-recent-30d commit count over the prior-30d count (``1.0`` when there is
    no prior activity — flat, never a divide-by-zero). ``total_commits`` counts
    the commits inside ``window_days``.
    """

    top_modules: tuple[tuple[str, int], ...]
    trend_ratio: float
    window_days: int
    total_commits: int


@dataclass(frozen=True, slots=True)
class OverviewAggregates:
    """Read-side bundle of the persisted overview aggregates (block 9 today)."""

    activity: ActivitySummary | None = None


@dataclass(frozen=True, slots=True)
class _Commit:
    """The two fields the aggregator needs off a framed record."""

    author_date: float
    module_prefixes: frozenset[str]


def compute_activity(
    log_text: str,
    *,
    window_days: int,
    now: float,
) -> ActivitySummary | None:
    """Aggregate a framed ``git log`` dump into an :class:`ActivitySummary`.

    Pure. ``log_text`` is the ``read_git_log`` frame (``commit``/``author-date``/
    ``files``/``==END==`` records); ``now`` is the reference instant (epoch
    seconds) the windows are measured back from. Returns ``None`` when no commit
    falls inside ``window_days`` (the caller omits the block on ``None``).

    Example::

        summary = compute_activity(read_git_log(root, ...), window_days=90, now=time.time())
    """
    window_cutoff = now - window_days * _SECONDS_PER_DAY
    commits = [c for c in _parse_commits(log_text) if c.author_date >= window_cutoff]
    if not commits:
        return None
    counts = _module_counts(commits)
    top = tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_MAX_ACTIVITY_MODULES])
    return ActivitySummary(
        top_modules=top,
        trend_ratio=_trend_ratio(commits, now=now),
        window_days=window_days,
        total_commits=len(commits),
    )


def _module_counts(commits: list[_Commit]) -> dict[str, int]:
    """Per-module commit counts — one increment per commit that touches a module.

    A commit touching several files in the SAME module counts once for that
    module (``frozenset`` of prefixes), so a wide sweep doesn't inflate its own
    directory's count.
    """
    counts: dict[str, int] = {}
    for commit in commits:
        for prefix in commit.module_prefixes:
            counts[prefix] = counts.get(prefix, 0) + 1
    return counts


def _trend_ratio(commits: list[_Commit], *, now: float) -> float:
    """Most-recent-30d commit count / prior-30d count (``1.0`` when no prior)."""
    recent_cutoff = now - _TREND_WINDOW_DAYS * _SECONDS_PER_DAY
    prior_cutoff = now - 2 * _TREND_WINDOW_DAYS * _SECONDS_PER_DAY
    recent = sum(1 for c in commits if c.author_date >= recent_cutoff)
    prior = sum(1 for c in commits if prior_cutoff <= c.author_date < recent_cutoff)
    if prior == 0:
        return 1.0
    return recent / prior


def _parse_commits(log_text: str) -> list[_Commit]:
    """Split the framed dump on ``==END==`` and parse each record's date+files."""
    commits: list[_Commit] = []
    for chunk in log_text.split(_END_MARKER):
        commit = _parse_record(chunk)
        if commit is not None:
            commits.append(commit)
    return commits


def _parse_record(chunk: str) -> _Commit | None:
    """Parse one framed record; ``None`` when it carries no ``commit`` line.

    Only the ``author-date`` and ``files`` lines matter here; the subject/body
    lines are ignored (this is the activity aggregator, not decision mining).
    """
    has_commit = False
    author_date: float | None = None
    files: tuple[str, ...] = ()
    for line in chunk.splitlines():
        if line.startswith(_COMMIT_PREFIX):
            has_commit = True
        elif line.startswith(_AUTHOR_DATE_PREFIX):
            author_date = _parse_epoch(line[len(_AUTHOR_DATE_PREFIX) :])
        elif line.startswith(_FILES_PREFIX):
            files = tuple(line[len(_FILES_PREFIX) :].split())
    if not has_commit or author_date is None:
        return None
    return _Commit(author_date=author_date, module_prefixes=_module_prefixes(files))


def _module_prefixes(files: tuple[str, ...]) -> frozenset[str]:
    """The distinct containing directories of the touched files (the modules).

    A file's "module" is its parent directory path (``a/b/c.py`` → ``a/b``); a
    top-level file (no ``/``) maps to itself so it still counts. Path prefixes
    are the coarse-but-robust module proxy §D17 uses — no import graph needed at
    index-end.
    """
    return frozenset(_module_of(path) for path in files if path)


def _module_of(path: str) -> str:
    if "/" in path:
        return path.rsplit("/", 1)[0]
    return path


def _parse_epoch(raw: str) -> float | None:
    try:
        return float(raw.strip())
    except ValueError:
        return None


# ── JSON serialisation (index_metadata.activity_summary column) ──────────


def activity_to_json(summary: ActivitySummary) -> str:
    """Serialise an :class:`ActivitySummary` to the stored JSON string."""
    return json.dumps(
        {
            "top_modules": [[name, count] for name, count in summary.top_modules],
            "trend_ratio": summary.trend_ratio,
            "window_days": summary.window_days,
            "total_commits": summary.total_commits,
        }
    )


def activity_from_json(text: str) -> ActivitySummary | None:
    """Deserialise the stored JSON; ``None`` on empty / malformed input.

    Never raises: a corrupt or absent column degrades the block to omitted
    rather than failing the whole overview render.
    """
    if not text:
        return None
    try:
        data = json.loads(text)
        return ActivitySummary(
            top_modules=tuple((str(name), int(count)) for name, count in data["top_modules"]),
            trend_ratio=float(data["trend_ratio"]),
            window_days=int(data["window_days"]),
            total_commits=int(data["total_commits"]),
        )
    except (ValueError, TypeError, KeyError):
        return None


__all__ = (
    "ActivitySummary",
    "OverviewAggregates",
    "activity_from_json",
    "activity_to_json",
    "compute_activity",
)
