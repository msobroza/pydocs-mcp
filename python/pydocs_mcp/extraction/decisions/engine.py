"""Pure merge / staleness / reconcile engine for mined decisions (spec §D8-§D10).

Three deterministic, side-effect-free steps sit between the mining sources and
the ``decision_records`` table:

* :func:`merge_raw_decisions` collapses per-source :class:`RawDecision`\\s whose
  normalized titles are token-Jaccard-similar into one merged decision —
  evidence and affected-* accrete (stable order), the merged confidence is the
  group max plus a small per-corroborator bump (never LOWERED by corroboration,
  §D8), status/source come from the highest-confidence member.
* :func:`staleness_score` scores a decision's freshness (spec §D10 formula
  verbatim): a churn term over affected-file mtimes plus an age term over the
  latest evidence date. ``os.stat`` is the only I/O; missing files count as
  changed.
* :func:`reconcile` matches merged incoming decisions against persisted
  :class:`DecisionRecord`\\s by normalized title — matched rows keep
  ``id`` / ``created_at`` / ``superseded_by`` and take the incoming
  evidence/status/confidence/affected, bumping ``updated_at`` ONLY when the
  evidence content-hash changed; unmatched incoming become new records;
  unmatched existing rows are deleted (their sources vanished).

Everything here is pure so it unit-tests without a DB, a filesystem (except the
bounded ``os.stat`` batch in :func:`staleness_score`), or the network.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path

from pydocs_mcp.extraction.decisions._types import RawDecision
from pydocs_mcp.storage.decision_record import DecisionEvidence, DecisionRecord

# Staleness weights (spec §D10) — single source of truth for the churn / age
# split so a re-weighting touches exactly these two constants.
_STALENESS_CHURN_WEIGHT = 0.7
_STALENESS_AGE_WEIGHT = 0.3
# One year in seconds — the age term caps at 1.0 here (a decision older than a
# year contributes the full age weight, no more).
_ONE_YEAR_SECONDS = 365 * 86400.0

# Small stopword set dropped from titles before Jaccard comparison so
# near-identical decisions ("use sidecar for vectors" vs "use the sidecar for
# vectors") still merge. Deliberately tiny — over-stemming would collapse
# genuinely distinct decisions.
_STOPWORDS = frozenset(
    {"a", "an", "the", "for", "to", "of", "in", "on", "and", "or", "with", "is", "be"}
)

# Everything that is not a word char or whitespace is punctuation to strip.
_PUNCT_RE = re.compile(r"[^\w\s]+")


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """Output of :func:`reconcile` — the write plan for one package's decisions.

    ``upserts`` carries every record to write (matched rows with preserved
    identity + new records with ``id is None``); ``delete_ids`` carries the ids
    of existing rows whose mining sources have all vanished.
    """

    upserts: tuple[DecisionRecord, ...]
    delete_ids: tuple[int, ...]


def _normalize_title(title: str) -> tuple[str, ...]:
    """Casefold, strip punctuation, drop stopwords → a stable token tuple.

    The token tuple is the merge/reconcile identity: two titles that normalize
    to token sets with Jaccard ≥ threshold are the same decision. Order is
    preserved for readability but similarity compares SETS.
    """
    stripped = _PUNCT_RE.sub(" ", title.casefold())
    return tuple(tok for tok in stripped.split() if tok and tok not in _STOPWORDS)


def decision_key(title: str) -> str:
    """Stable per-decision key = normalized title tokens joined by ``-``.

    The capture stage stamps this on each decision chunk's metadata; the
    persistence layer recomputes it from each reconciled record's title to map
    ``decision_key`` → assigned ``id`` and backlink the chunk. Single source of
    truth so the two sides can never drift (a decision that merges to the same
    normalized title collapses to one key, matching its one persisted row).
    """
    return "-".join(_normalize_title(title))


def _jaccard(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    """Token-set Jaccard similarity of two normalized titles (0.0 when both empty)."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _dedupe_evidence(evidence: list[DecisionEvidence]) -> tuple[DecisionEvidence, ...]:
    """Union evidence spans in first-seen order, dropping exact duplicates."""
    seen: set[tuple[str, str, str]] = set()
    out: list[DecisionEvidence] = []
    for ev in evidence:
        key = (ev.source, ev.locator, ev.text)
        if key not in seen:
            seen.add(key)
            out.append(ev)
    return tuple(out)


def _dedupe_strings(values: list[str]) -> tuple[str, ...]:
    """Union strings in first-seen order, dropping exact duplicates."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)


def _merge_group(group: list[RawDecision]) -> RawDecision:
    """Collapse one similar-title group into a single merged :class:`RawDecision`.

    Confidence = ``min(1.0, max(conf) + 0.05 * (len(group) - 1))`` (corroboration
    RAISES, never lowers). Title / status / source come from the highest-
    confidence member (ties keep the first, i.e. mining order). Evidence and
    affected-* accrete as first-seen-order unions. ``evidence_date`` = the
    latest present date.
    """
    primary = max(group, key=lambda r: r.confidence)
    max_conf = max(r.confidence for r in group)
    confidence = min(1.0, max_conf + 0.05 * (len(group) - 1))
    evidence: list[DecisionEvidence] = []
    files: list[str] = []
    qnames: list[str] = []
    dates: list[float] = []
    for r in group:
        evidence.extend(r.evidence)
        files.extend(r.affected_files)
        qnames.extend(r.affected_qnames)
        if r.evidence_date is not None:
            dates.append(r.evidence_date)
    return RawDecision(
        title=primary.title,
        status=primary.status,
        source=primary.source,
        confidence=confidence,
        evidence=_dedupe_evidence(evidence),
        affected_files=_dedupe_strings(files),
        affected_qnames=_dedupe_strings(qnames),
        evidence_date=max(dates) if dates else None,
    )


def merge_raw_decisions(
    raws: tuple[RawDecision, ...],
    *,
    jaccard_threshold: float,
) -> tuple[RawDecision, ...]:
    """Greedily group ``raws`` on normalized-title Jaccard, then merge each group.

    A new raw joins the FIRST existing group whose representative title clears
    ``jaccard_threshold``; otherwise it seeds a new group. Greedy (not
    clustering) — deterministic and cheap; the source set is small enough that
    optimal clustering earns nothing. Returns one merged decision per group, in
    first-seen group order.
    """
    groups: list[list[RawDecision]] = []
    keys: list[tuple[str, ...]] = []
    for raw in raws:
        key = _normalize_title(raw.title)
        placed = False
        for idx, existing_key in enumerate(keys):
            if _jaccard(key, existing_key) >= jaccard_threshold:
                groups[idx].append(raw)
                placed = True
                break
        if not placed:
            groups.append([raw])
            keys.append(key)
    return tuple(_merge_group(group) for group in groups)


def staleness_score(
    *,
    affected_files: tuple[str, ...],
    updated_at: float,
    now: float,
    root: Path,
) -> float:
    """Spec §D10 staleness in [0, 1] — churn over mtimes + age over evidence date.

    ``changed_ratio`` is the fraction of ``affected_files`` whose mtime (via
    ``os.stat`` under ``root``) is newer than ``updated_at`` — a MISSING file
    counts as changed (a decision pointing at a deleted file is suspect). When
    ``affected_files`` is empty the churn term is 0 and only the age term
    contributes. ``age_years`` is ``(now - updated_at)`` in years, the age term
    capped at 1.0.
    """
    changed_ratio = _changed_ratio(affected_files, updated_at=updated_at, root=root)
    age_years = (now - updated_at) / _ONE_YEAR_SECONDS
    age_term = min(1.0, age_years)
    return min(
        1.0,
        _STALENESS_CHURN_WEIGHT * changed_ratio + _STALENESS_AGE_WEIGHT * age_term,
    )


def _changed_ratio(
    affected_files: tuple[str, ...],
    *,
    updated_at: float,
    root: Path,
) -> float:
    """Fraction of affected files touched after ``updated_at`` (missing = changed)."""
    if not affected_files:
        return 0.0
    changed = sum(1 for rel in affected_files if _is_changed(rel, updated_at=updated_at, root=root))
    return changed / len(affected_files)


def _is_changed(rel: str, *, updated_at: float, root: Path) -> bool:
    """True if ``rel`` (under ``root``) was modified after ``updated_at`` or is gone."""
    try:
        mtime = (root / rel).stat().st_mtime
    except OSError:
        return True  # missing file counts as changed
    return mtime > updated_at


def _evidence_hash(evidence: tuple[DecisionEvidence, ...]) -> str:
    """SHA-256 of the sorted evidence texts — the "did evidence change?" key (§D9)."""
    joined = "\n".join(sorted(ev.text for ev in evidence))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _apply_incoming(
    existing: DecisionRecord, incoming: RawDecision, *, now: float
) -> DecisionRecord:
    """Fold an incoming merged decision onto its matched existing record.

    Keeps identity (``id`` / ``created_at`` / ``superseded_by``), takes the
    incoming evidence/status/confidence/affected/source, and bumps ``updated_at``
    to ``now`` ONLY when the evidence content-hash changed (§D9) — otherwise the
    existing ``updated_at`` (the latest-evidence anchor) survives so decisions
    can still age.
    """
    evidence_changed = _evidence_hash(existing.evidence) != _evidence_hash(incoming.evidence)
    updated_at = now if evidence_changed else existing.updated_at
    return replace(
        existing,
        status=incoming.status,
        source=incoming.source,
        confidence=incoming.confidence,
        evidence=incoming.evidence,
        affected_files=incoming.affected_files,
        affected_qnames=incoming.affected_qnames,
        updated_at=updated_at,
    )


def _to_new_record(incoming: RawDecision, *, package: str, now: float) -> DecisionRecord:
    """Build a fresh :class:`DecisionRecord` for an unmatched incoming decision.

    ``created_at == updated_at == evidence_date or now`` (the latest-evidence
    anchor, §D10). ``staleness_score`` is left at 0.0 here — the persistence
    layer recomputes it with the real ``project_root`` before writing.
    """
    stamp = incoming.evidence_date if incoming.evidence_date is not None else now
    return DecisionRecord(
        id=None,
        package=package,
        title=incoming.title,
        status=incoming.status,
        source=incoming.source,
        confidence=incoming.confidence,
        evidence=incoming.evidence,
        affected_files=incoming.affected_files,
        affected_qnames=incoming.affected_qnames,
        staleness_score=0.0,
        superseded_by=None,
        verification="verbatim",
        structured=None,
        created_at=stamp,
        updated_at=stamp,
    )


def reconcile(
    *,
    existing: tuple[DecisionRecord, ...],
    incoming: tuple[RawDecision, ...],
    now: float,
    package: str = "__project__",
) -> ReconcileResult:
    """Match incoming↔existing by normalized title → the write plan (spec §D9).

    Matched pairs keep identity and take the incoming content (bumping
    ``updated_at`` on evidence change); unmatched incoming become new records;
    unmatched existing rows are deleted (their sources vanished). A first-match
    wins on the normalized-title key so a re-mined decision reuses exactly one
    persisted row.
    """
    existing_by_key: dict[tuple[str, ...], DecisionRecord] = {}
    for record in existing:
        existing_by_key.setdefault(_normalize_title(record.title), record)

    upserts: list[DecisionRecord] = []
    matched_keys: set[tuple[str, ...]] = set()
    for inc in incoming:
        key = _normalize_title(inc.title)
        matched = existing_by_key.get(key)
        if matched is not None and key not in matched_keys:
            matched_keys.add(key)
            upserts.append(_apply_incoming(matched, inc, now=now))
        else:
            upserts.append(_to_new_record(inc, package=package, now=now))

    delete_ids = tuple(
        record.id
        for key, record in existing_by_key.items()
        if key not in matched_keys and record.id is not None
    )
    return ReconcileResult(upserts=tuple(upserts), delete_ids=delete_ids)


__all__ = [
    "ReconcileResult",
    "decision_key",
    "merge_raw_decisions",
    "reconcile",
    "staleness_score",
]
