"""Index-time overview aggregates — git-activity (§D17 block 9) + LLM summary (block 2).

Both halves are index-time only:

- :func:`compute_activity` is a PURE aggregator over the framed ``git log`` dump
  that :func:`pydocs_mcp.extraction.decisions._git.read_git_log` produces (same
  ``commit``/``author-date``/``files``/``==END==`` line frame the
  ``commit_messages`` source parses). It groups commits by the touched files'
  containing directory (the "module" grouping = path prefix), counts commits per
  module inside the window, and computes a 30d-vs-prior-30d trend ratio. No I/O.

- :func:`generate_overview_summary` is the opt-in LLM architecture summary (block
  2). It is fingerprint-cached: the fingerprint is the sha256 of the SORTED module
  qnames, so the (expensive) LLM call fires ONLY when the module set changes —
  same fingerprint returns the cached record with zero LLM calls. A malformed
  (blank) reply degrades to the old cache (never overwrites, never raises).

- :class:`ActivitySummary` / :class:`OverviewSummary` serialise to/from the JSON
  stored in the ``index_metadata.activity_summary`` / ``overview_summary`` columns;
  :class:`OverviewAggregates` is the read-side bundle ``OverviewService`` gets from
  its injected reader closure.

The subprocess spawn (``read_git_log``) and the LLM call both live in the
composition-root writer closure (``storage.factories``); only the pure
aggregation + the LLM orchestration (which takes the client as a param) live
here, so both are unit-testable with a framed-string fixture / a fake client and
no git repo, no network.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.retrieval.protocols import LlmClient

log = logging.getLogger("pydocs-mcp")

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
class OverviewSummary:
    """Cached LLM architecture summary for the overview's ``## Architecture`` block.

    ``text`` is the 2–4 sentence prose the LLM produced; ``fingerprint`` is the
    sha256 of the sorted module qnames the summary was generated for (so a later
    index run can skip the LLM call when the module set is unchanged);
    ``generated_at`` is the epoch instant it was produced.
    """

    text: str
    fingerprint: str
    generated_at: float


@dataclass(frozen=True, slots=True)
class OverviewAggregates:
    """Read-side bundle of the persisted overview aggregates (blocks 9 + 2)."""

    activity: ActivitySummary | None = None
    summary: OverviewSummary | None = None


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


# ── LLM architecture summary (§D17 block 2, index_metadata.overview_summary) ──

# The LLM prompt caps the reply at a fixed sentence range so the block stays a
# terse orientation blurb, not a wall of prose. Stated in-prompt (below) so the
# model self-limits; the code never truncates a valid reply.
_SUMMARY_MIN_SENTENCES = 2
_SUMMARY_MAX_SENTENCES = 4
# The prompt lists at most this many central symbols so a large graph doesn't
# blow the prompt budget — the top-N by centrality is plenty of grounding.
_SUMMARY_MAX_CENTRAL = 15
# LLM temperature for the summary — deterministic (matches LlmClient default 0.0)
# so the same module set yields a stable summary across runs.
_SUMMARY_TEMPERATURE = 0.0

_SUMMARY_SYSTEM_PROMPT = (
    "You are a senior engineer writing a one-paragraph architecture orientation "
    "for a Python codebase. Given its module map and most-central symbols, "
    f"write {_SUMMARY_MIN_SENTENCES}–{_SUMMARY_MAX_SENTENCES} plain sentences "
    "naming the main layers and how they relate. No preamble, no bullet points, "
    "no markdown — just the prose."
)


def summary_fingerprint(module_qnames: Sequence[str]) -> str:
    """sha256 (hex) of the SORTED module qnames — the cache key for the summary.

    Sorted before hashing so the module map's iteration order is irrelevant: the
    same set of modules always yields the same fingerprint, and the LLM call
    fires only when the set genuinely changes (a module added / removed).
    """
    joined = "\n".join(sorted(module_qnames))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def build_architecture_prompt(
    module_qnames: Sequence[str],
    central_symbols: Sequence[str],
) -> str:
    """Render the user prompt: the module map + the top central symbols.

    Pure — the sentence cap lives in the system prompt, this just grounds the
    model in the corpus's shape. Central symbols are capped so a large graph
    doesn't blow the prompt budget.
    """
    modules_block = "\n".join(f"- {q}" for q in sorted(module_qnames))
    central_block = "\n".join(f"- {q}" for q in central_symbols[:_SUMMARY_MAX_CENTRAL])
    return (
        f"Modules ({len(module_qnames)}):\n{modules_block}\n\n"
        f"Most-central symbols:\n{central_block}"
    )


async def generate_overview_summary(
    *,
    module_qnames: Sequence[str],
    central_symbols: Sequence[str],
    llm_client: LlmClient,
    cached: OverviewSummary | None,
    now: float,
) -> OverviewSummary | None:
    """Produce (or reuse) the cached LLM architecture summary.

    Fingerprint-cached: when ``cached`` already covers the current module set
    (``cached.fingerprint == summary_fingerprint(module_qnames)``), the cached
    record is returned VERBATIM and NO LLM call is made. Otherwise one
    ``llm_client.chat`` call generates a fresh summary.

    Degradation: a blank / whitespace-only reply is treated as malformed — it is
    logged and the OLD cache is kept (``None`` when there was none). This never
    raises: a flaky LLM must not fail an indexing run.

    Example::

        summary = await generate_overview_summary(
            module_qnames=("proj.api", "proj.core"),
            central_symbols=("proj.core.Engine",),
            llm_client=client,
            cached=previous,
            now=time.time(),
        )
    """
    fingerprint = summary_fingerprint(module_qnames)
    if cached is not None and cached.fingerprint == fingerprint:
        return cached
    reply = await llm_client.chat(
        [
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": build_architecture_prompt(module_qnames, central_symbols)},
        ],
        temperature=_SUMMARY_TEMPERATURE,
    )
    text = reply.strip()
    if not text:
        # Malformed reply — keep the prior cache rather than overwrite it with
        # an empty summary; the block just stays on the last good text.
        log.warning("Overview LLM summary skipped: empty reply; keeping cached summary")
        return cached
    return OverviewSummary(text=text, fingerprint=fingerprint, generated_at=now)


def summary_to_json(summary: OverviewSummary) -> str:
    """Serialise an :class:`OverviewSummary` to the stored JSON string."""
    return json.dumps(
        {
            "text": summary.text,
            "fingerprint": summary.fingerprint,
            "generated_at": summary.generated_at,
        }
    )


def summary_from_json(text: str) -> OverviewSummary | None:
    """Deserialise the stored JSON; ``None`` on empty / malformed input.

    Never raises: a corrupt or absent column degrades the block to omitted
    rather than failing the whole overview render (same contract as
    :func:`activity_from_json`).
    """
    if not text:
        return None
    try:
        data = json.loads(text)
        return OverviewSummary(
            text=str(data["text"]),
            fingerprint=str(data["fingerprint"]),
            generated_at=float(data["generated_at"]),
        )
    except (ValueError, TypeError, KeyError):
        return None


__all__ = (
    "ActivitySummary",
    "OverviewAggregates",
    "OverviewSummary",
    "activity_from_json",
    "activity_to_json",
    "build_architecture_prompt",
    "compute_activity",
    "generate_overview_summary",
    "summary_fingerprint",
    "summary_from_json",
    "summary_to_json",
)
