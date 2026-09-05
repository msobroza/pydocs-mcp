"""Flat, stdlib-only row models for the pinned SWE-bench snapshots (ADR 0013).

The parquet/pyarrow read edge (:mod:`download`) normalizes each snapshot's nested
columns into these flat records, so every downstream pure function — overlap
(:mod:`overlap`), split construction (:mod:`splits`) — operates on plain dataclasses
and is testable offline with synthetic records (no network, no parquet engine).
"""

from __future__ import annotations

from dataclasses import dataclass

from .pins import LIVE_DUPLICATE_INSTANCE_ID


@dataclass(frozen=True, slots=True)
class LiveRecord:
    """One SWE-bench-Live `full` instance, reduced to the fields Phase 3 needs.

    ``difficulty_files`` is the ``difficulty.files`` int (the gold-patch file count —
    the struct is ``{files, hunks, lines}``, datasets-overlap §2); ``created_at_year``
    is the 4-digit year. Both are stratification axes in :mod:`splits`.
    """

    instance_id: str
    repo: str
    difficulty_files: int
    created_at_year: int

    @property
    def org(self) -> str:
        """GitHub org — the leading ``owner`` of ``owner/name`` (R2 exclusion key)."""
        return org_of(self.repo)


def org_of(repo: str) -> str:
    """Normalize a ``owner/name`` repo slug to its GitHub org.

    Raises on a slug with no ``/`` — a malformed repo string is a data error, not a
    silently-empty org (which would wrongly collide every unslashed repo under "").
    """
    if "/" not in repo:
        raise ValueError(f"invalid repo slug: got {repo!r}, expected 'owner/name'")
    return repo.split("/", 1)[0]


def dedupe_live_records(records: list[LiveRecord]) -> list[LiveRecord]:
    """Drop the SECOND occurrence of the known conan duplicate (ADR 0013 dedupe rule).

    Order-preserving: the first occurrence of every instance_id is kept, and only the
    pinned duplicate ``conan-io__conan-18153`` is de-duplicated — an UNEXPECTED
    duplicate raises, because a new dup means the snapshot drifted under the pin and the
    ADR's 1888→1887 invariant no longer holds.
    """
    seen: set[str] = set()
    out: list[LiveRecord] = []
    for record in records:
        if record.instance_id not in seen:
            seen.add(record.instance_id)
            out.append(record)
            continue
        if record.instance_id != LIVE_DUPLICATE_INSTANCE_ID:
            raise ValueError(
                f"unexpected duplicate instance_id {record.instance_id!r}; "
                f"only {LIVE_DUPLICATE_INSTANCE_ID!r} is a known dup — snapshot drifted?"
            )
    return out
