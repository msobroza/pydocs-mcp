"""``commit_messages`` source — mine architectural decisions from git log (spec §D8).

Parses the framed ``git log`` dump the capture stage passes in via
``ctx.git_log_text`` (this source NEVER spawns a subprocess — the seam is
``_git.read_git_log``, called index-time by the stage). Each framed record is
keyword-scored against :data:`_DECISION_KEYWORDS`; a record qualifies at ≥2
keyword hits, or 1 hit backed by a body of ≥3 non-empty lines (a lone keyword in
a one-line commit is too weak a signal). Qualifying records mine at confidence
0.70 / status ``proposed`` with the subject+body as verbatim evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.decisions._types import (
    CaptureContext,
    DecisionEvidence,
    RawDecision,
    decision_source_registry,
)

# Substring-matched, case-folded architectural-change verbs. A phrase like
# "switch to" is matched as a substring so "switched to" also hits. Module
# constant per the single-source-of-truth rule; ``changelog`` imports it so the
# two prose sources share one scorer.
_DECISION_KEYWORDS = frozenset(
    {
        "migrate",
        "switch to",
        "replace",
        "adopt",
        "deprecate",
        "rewrite",
        "introduce",
        "remove",
        "extract",
        "split",
        "convert",
        "transition",
        "revert",
    }
)

# Mined git history is a moderate signal — below inline markers (0.95) and ADRs
# (1.0). Status is always ``proposed``: a commit records that a change happened,
# not that the decision is the current active one.
_CONFIDENCE = 0.70
_TITLE_MAX = 80
_NAME = "commit_messages"
_END_MARKER = "==END=="
# 1 keyword qualifies only when the body carries at least this many non-blank
# lines; ≥2 keywords qualify regardless of body length.
_MIN_BODY_LINES_FOR_SINGLE = 3


@dataclass(frozen=True, slots=True)
class _Commit:
    sha: str
    author_date: float | None
    subject: str
    body: str
    files: tuple[str, ...]


@decision_source_registry.register(_NAME)
@dataclass(frozen=True, slots=True)
class CommitMessagesSource:
    """Mines decision-shaped commits from the framed ``ctx.git_log_text`` dump."""

    name: str = _NAME

    async def mine(self, ctx: CaptureContext) -> tuple[RawDecision, ...]:
        raws: list[RawDecision] = []
        for commit in _parse_log(ctx.git_log_text):
            raw = _commit_to_raw(commit, ctx.project_root)
            if raw is not None:
                raws.append(raw)
        return tuple(raws)


def score_keywords(text: str) -> int:
    """Count distinct :data:`_DECISION_KEYWORDS` present in ``text`` (case-fold)."""
    lowered = text.lower()
    return sum(1 for kw in _DECISION_KEYWORDS if kw in lowered)


def qualifies(text: str, body: str) -> bool:
    """Keyword gate shared by the git/prose sources (single source of truth).

    Qualifies at ≥2 keyword hits, or exactly 1 hit backed by a body of ≥3
    non-empty lines. ``body`` is the multi-line region under the title/subject.
    """
    hits = score_keywords(text)
    if hits >= 2:
        return True
    if hits == 1:
        return _non_empty_line_count(body) >= _MIN_BODY_LINES_FOR_SINGLE
    return False


def _non_empty_line_count(body: str) -> int:
    return sum(1 for line in body.splitlines() if line.strip())


def _parse_log(log_text: str) -> list[_Commit]:
    """Split the framed dump on ``==END==`` and parse each record."""
    commits: list[_Commit] = []
    for chunk in log_text.split(_END_MARKER):
        commit = _parse_record(chunk)
        if commit is not None:
            commits.append(commit)
    return commits


def _parse_record(chunk: str) -> _Commit | None:
    """Parse one framed record; ``None`` when it carries no ``commit`` line."""
    sha: str | None = None
    author_date: float | None = None
    subject = ""
    body_lines: list[str] = []
    files: tuple[str, ...] = ()
    in_body = False
    for line in chunk.splitlines():
        if line.startswith("commit "):
            sha, in_body = line[len("commit ") :].strip(), False
        elif line.startswith("author-date "):
            author_date, in_body = _parse_epoch(line[len("author-date ") :]), False
        elif line.startswith("subject "):
            subject, in_body = line[len("subject ") :], False
        elif line.startswith("body "):
            body_lines, in_body = [line[len("body ") :]], True
        elif line.startswith("files "):
            files, in_body = tuple(line[len("files ") :].split()), False
        elif in_body:
            body_lines.append(line)
    if sha is None:
        return None
    return _Commit(sha, author_date, subject, "\n".join(body_lines), files)


def _parse_epoch(raw: str) -> float | None:
    try:
        return float(raw.strip())
    except ValueError:
        return None


def _commit_to_raw(commit: _Commit, project_root: Path) -> RawDecision | None:
    """Keyword-gate a commit and, if it qualifies, build its :class:`RawDecision`."""
    scored_text = f"{commit.subject}\n{commit.body}"
    if not qualifies(scored_text, commit.body):
        return None
    files = _existing_files(commit.files, project_root)
    evidence = DecisionEvidence(source=_NAME, locator=commit.sha, text=scored_text.strip())
    return RawDecision(
        title=commit.subject[:_TITLE_MAX],
        status="proposed",
        source=_NAME,
        confidence=_CONFIDENCE,
        evidence=(evidence,),
        affected_files=files,
        affected_qnames=(),
        evidence_date=commit.author_date,
    )


def _existing_files(files: tuple[str, ...], project_root: Path) -> tuple[str, ...]:
    """Commit files filtered to paths that still exist under the project root."""
    return tuple(rel for rel in files if (project_root / rel).is_file())
