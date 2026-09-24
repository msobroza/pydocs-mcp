"""Typed exceptions for the storage layer, and the one classifier of the
storage errors a request path may degrade on."""

from __future__ import annotations

import sqlite3

from pydocs_mcp.exceptions import PydocsMCPError

# What SQLite says when a served bundle's file is unreadable or is the empty
# file a plain ``sqlite3.connect`` (the freshness probe's) leaves where a
# removed bundle was — the signals ``multirepo.current_metadata`` falls back on.
_BUNDLE_GONE_MESSAGES = ("no such table", "unable to open database file")


def is_bundle_gone_error(exc: BaseException) -> bool:
    """True when a read failed because the served bundle is no longer an index.

    Removed from under a running server (``CacheNotIndexedError`` is a
    ``FileNotFoundError``), emptied, or unreadable. A request path that must
    keep answering degrades on exactly these (#311); any other error is a
    defect and propagates.
    """
    if isinstance(exc, FileNotFoundError):
        return True
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    return any(gone in str(exc) for gone in _BUNDLE_GONE_MESSAGES)


class UnitOfWorkNotEnteredError(PydocsMCPError, RuntimeError):
    """Raised when a UoW attribute is accessed outside ``async with``.

    Repository attributes on :class:`UnitOfWork` are only valid inside
    the active transaction context. Loud failure beats silent None.
    """

    def __init__(self, attr_name: str) -> None:
        super().__init__(
            f"UnitOfWork attribute {attr_name!r} accessed outside "
            f"`async with uow:` — repositories are valid only inside "
            f"the transaction context.",
        )
        self.attr_name = attr_name


class EmbeddingDimMismatchError(PydocsMCPError, ValueError):
    """Raised when a loaded ``.tq`` index's on-disk dim disagrees with the
    configured ``embedding.dim``.

    ``TurboQuantUnitOfWork._open_index`` only applies ``dim`` on the
    CONSTRUCT branch — a LOADED index keeps its stored dim. Without this
    gate, a config-only ``embedding.dim``/model change (no reindex) either
    crashes indexing on turbovec's raw ``add_with_ids`` ValueError, or —
    worse — lets ``IdMapIndex.search`` run to completion with a wrong-dim
    query and silently return meaningless similarity scores. Raised eagerly
    in ``TurboQuantUnitOfWork.__aenter__`` right after load, before any
    add/search call can reach turbovec.
    """

    def __init__(self, *, index_path: object, index_dim: int, configured_dim: int) -> None:
        super().__init__(
            f"{index_path} was built with dim={index_dim}, but the configured "
            f"embedding.dim={configured_dim} disagrees. Reindex the project "
            f"(pydocs-mcp index . --force) after changing embedding.dim or "
            f"the embedding model in YAML.",
        )
        self.index_path = index_path
        self.index_dim = index_dim
        self.configured_dim = configured_dim
