"""SqliteChunkMultiVectorRepository — the ``chunk_multi_vector_ids`` mapping table.

Bridges ``chunks.id`` ↔ fast-plaid ``plaid_doc_id`` for the
late-interaction backend; every method rides the ambient UoW
transaction via ``_maybe_acquire`` and never commits itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire


@dataclass(frozen=True, slots=True)
class SqliteChunkMultiVectorRepository:
    """Repository over the ``chunk_multi_vector_ids`` SQLite table.

    Bridges ``chunks.id`` ↔ fast-plaid ``plaid_doc_id`` for the
    late-interaction backend. Structurally a sibling of
    :class:`SqliteChunkRepository`: a frozen/slots dataclass holding a
    :class:`ConnectionProvider`, every method routing through
    :func:`_maybe_acquire` so it reuses the ambient
    :class:`SqliteUnitOfWork` transaction when one is open — and NEVER
    calling ``conn.commit()`` itself. The owning UoW drives commit.

    Extracted from :class:`FastPlaidUnitOfWork`, which previously opened
    its own ``sqlite3.connect`` + eager ``conn.commit()`` against this
    table — that deadlocked against the composite UoW's already-open
    write transaction on the same DB file and broke cross-store
    atomicity. Routing the mapping SQL through this repo (and the shared
    provider) keeps the mapping rows on the same ambient transaction as
    the ``chunks`` writes.
    """

    provider: ConnectionProvider

    async def next_plaid_offset(self) -> int:
        """Next free ``plaid_doc_id`` = ``MAX(plaid_doc_id)+1`` (0 on empty).

        ``COALESCE(MAX(...)+1, 0)`` returns 0 for the empty-table path so
        the first append always starts at offset 0 — matching fast-plaid's
        ``.create`` (offset 0) vs ``.update`` (offset > 0) branch.
        """
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT COALESCE(MAX(plaid_doc_id) + 1, 0) FROM chunk_multi_vector_ids"
                ).fetchone()
            )
        return int(row[0])

    async def upsert(self, rows: Sequence[tuple[int, int, str, str]]) -> None:
        """Insert/replace ``(chunk_id, plaid_doc_id, package, pipeline_hash)`` rows.

        ``INSERT OR REPLACE`` keyed on the ``chunk_id`` PRIMARY KEY — a
        reindex of the same chunk overwrites its mapping row in place.
        """
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT OR REPLACE INTO chunk_multi_vector_ids "
                "(chunk_id, plaid_doc_id, package, pipeline_hash) VALUES (?,?,?,?)",
                list(rows),
            )

    async def delete_by_chunk_ids(self, ids: Sequence[int]) -> tuple[int, ...]:
        """Delete mapping rows for ``ids``; return the freed ``plaid_doc_id``s.

        The caller (fast-plaid soft-delete) needs the ``plaid_doc_id``s to
        ``delete(subset=...)`` the index slots, so we SELECT them before
        the DELETE. Both statements run on the ambient connection so the
        SELECT-then-DELETE is consistent within the surrounding
        transaction.
        """
        ids_list = list(ids)
        if not ids_list:
            return ()
        # ``placeholders`` is literal ``?`` chars (one per id, not user input),
        # so the IN-clause SQL is safe; the values bind via parameters.
        placeholders = ",".join("?" for _ in ids_list)
        async with _maybe_acquire(self.provider) as conn:

            def _select_then_delete() -> tuple[int, ...]:
                plaid_ids = tuple(
                    row[0]
                    for row in conn.execute(
                        "SELECT plaid_doc_id FROM chunk_multi_vector_ids "
                        f"WHERE chunk_id IN ({placeholders})",
                        ids_list,
                    )
                )
                conn.execute(
                    f"DELETE FROM chunk_multi_vector_ids WHERE chunk_id IN ({placeholders})",
                    ids_list,
                )
                return plaid_ids

            return await asyncio.to_thread(_select_then_delete)

    async def clear(self) -> tuple[int, ...]:
        """Delete every mapping row; return all freed ``plaid_doc_id``s."""
        async with _maybe_acquire(self.provider) as conn:

            def _select_then_delete() -> tuple[int, ...]:
                plaid_ids = tuple(
                    row[0]
                    for row in conn.execute("SELECT plaid_doc_id FROM chunk_multi_vector_ids")
                )
                conn.execute("DELETE FROM chunk_multi_vector_ids")
                return plaid_ids

            return await asyncio.to_thread(_select_then_delete)

    async def packages_for_chunks(self, ids: Sequence[int]) -> dict[int, str]:
        """Map ``chunk_id -> package`` from the ``chunks`` table for ``ids``."""
        ids_list = list(ids)
        if not ids_list:
            return {}
        # ``placeholders`` is literal ``?`` chars (one per id); values bind via parameters.
        placeholders = ",".join("?" for _ in ids_list)
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    f"SELECT id, package FROM chunks WHERE id IN ({placeholders})",
                    ids_list,
                ).fetchall()
            )
        return {row[0]: row[1] for row in rows}

    async def plaid_ids_for_chunks(self, ids: Sequence[int]) -> tuple[tuple[int, int], ...]:
        """Return ``(plaid_doc_id, chunk_id)`` pairs for the given ``chunk_id``s.

        The score path reverse-maps fast-plaid hits back to ``chunk_id``s,
        so it wants the mapping keyed by ``plaid_doc_id``. Returning pairs
        (not a dict) keeps the repository free of caller-specific shaping.
        """
        ids_list = list(ids)
        if not ids_list:
            return ()
        # ``placeholders`` is literal ``?`` chars (one per id); values bind via parameters.
        placeholders = ",".join("?" for _ in ids_list)
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT plaid_doc_id, chunk_id FROM chunk_multi_vector_ids "
                    f"WHERE chunk_id IN ({placeholders})",
                    ids_list,
                ).fetchall()
            )
        return tuple((row[0], row[1]) for row in rows)
