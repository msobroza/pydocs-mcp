"""SqliteDecisionRepository — mined decisions in ``decision_records`` (schema v14)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.decision_record import DecisionEvidence, DecisionRecord
from pydocs_mcp.storage.protocols import UnitOfWork
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire

# Column order shared by INSERT / UPDATE so the two statements can't drift.
_WRITE_COLUMNS = (
    "package",
    "title",
    "status",
    "source",
    "confidence",
    "evidence",
    "affected_files",
    "affected_qnames",
    "staleness_score",
    "superseded_by",
    "verification",
    "structured",
    "created_at",
    "updated_at",
)
_SELECT_COLUMNS = ("id", *_WRITE_COLUMNS)


@dataclass(frozen=True, slots=True)
class SqliteDecisionRepository:
    """DecisionStore backed by the ``decision_records`` SQLite table (v14).

    Holds mined architectural decisions (spec §D8-§D10). ``upsert`` is
    insert-or-update-by-id: a record with ``id is None`` INSERTs and its new
    rowid is returned; a record with a concrete ``id`` UPDATEs that row and the
    same id is returned (so ``created_at`` is preserved by NOT re-writing it).
    Evidence / affected-* / structured serialise via ``json.dumps`` at the row
    boundary and deserialise on read. Mirrors :class:`SqliteNodeScoreRepository`:
    every method rides the ambient transaction via ``_maybe_acquire`` and never
    calls ``conn.commit()``.
    """

    provider: ConnectionProvider

    async def upsert(
        self,
        records: Sequence[DecisionRecord],
        *,
        uow: UnitOfWork | None = None,
    ) -> tuple[int, ...]:
        materialised = tuple(records)
        if not materialised:
            return ()
        async with _maybe_acquire(self.provider) as conn:
            return await asyncio.to_thread(self._write, conn, materialised)

    def _write(self, conn, records: tuple[DecisionRecord, ...]) -> tuple[int, ...]:
        # Row-by-row (not executemany) because we need each INSERT's lastrowid
        # to return the caller the assigned ids. The whole loop runs inside the
        # ambient UoW transaction, so it's still atomic.
        placeholders = ", ".join("?" * len(_WRITE_COLUMNS))
        insert_sql = (
            f"INSERT INTO decision_records ({', '.join(_WRITE_COLUMNS)}) VALUES ({placeholders})"
        )
        # UPDATE preserves created_at by excluding it from the SET list.
        update_cols = [c for c in _WRITE_COLUMNS if c != "created_at"]
        update_sql = (
            "UPDATE decision_records SET "
            + ", ".join(f"{c} = ?" for c in update_cols)
            + " WHERE id = ?"
        )
        out: list[int] = []
        for record in records:
            values = _record_to_values(record)
            if record.id is None:
                cursor = conn.execute(insert_sql, values)
                out.append(cursor.lastrowid)
            else:
                update_values = [
                    v for c, v in zip(_WRITE_COLUMNS, values, strict=True) if c != "created_at"
                ]
                conn.execute(update_sql, (*update_values, record.id))
                out.append(record.id)
        return tuple(out)

    async def list_for_package(self, package: str) -> tuple[DecisionRecord, ...]:
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    f"SELECT {', '.join(_SELECT_COLUMNS)} FROM decision_records "
                    "WHERE package = ? ORDER BY id",
                    (package,),
                ).fetchall()
            )
        return tuple(_row_to_decision_record(r) for r in rows)

    async def delete_for_package(
        self,
        package: str,
        *,
        uow: UnitOfWork | None = None,
    ) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "DELETE FROM decision_records WHERE package = ?",
                (package,),
            )

    async def delete_all(self, *, uow: UnitOfWork | None = None) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM decision_records")


def _record_to_values(record: DecisionRecord) -> tuple[object, ...]:
    """Serialise a DecisionRecord to the positional VALUES tuple (write side)."""
    evidence_json = json.dumps(
        [{"source": e.source, "locator": e.locator, "text": e.text} for e in record.evidence]
    )
    structured_json = None if record.structured is None else json.dumps(dict(record.structured))
    return (
        record.package,
        record.title,
        record.status,
        record.source,
        record.confidence,
        evidence_json,
        json.dumps(list(record.affected_files)),
        json.dumps(list(record.affected_qnames)),
        record.staleness_score,
        record.superseded_by,
        record.verification,
        structured_json,
        record.created_at,
        record.updated_at,
    )


def _row_to_decision_record(row) -> DecisionRecord:
    """Deserialise a ``sqlite3.Row`` back to a DecisionRecord (read side)."""
    evidence = tuple(
        DecisionEvidence(source=e["source"], locator=e["locator"], text=e["text"])
        for e in json.loads(row["evidence"])
    )
    structured_raw = row["structured"]
    return DecisionRecord(
        id=row["id"],
        package=row["package"],
        title=row["title"],
        status=row["status"],
        source=row["source"],
        confidence=row["confidence"],
        evidence=evidence,
        affected_files=tuple(json.loads(row["affected_files"])),
        affected_qnames=tuple(json.loads(row["affected_qnames"])),
        staleness_score=row["staleness_score"],
        superseded_by=row["superseded_by"],
        verification=row["verification"],
        structured=None if structured_raw is None else json.loads(structured_raw),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
