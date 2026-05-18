"""ReferenceKind enum for the cross-node reference graph (spec §4.1).

Three AST-precise kinds — CALLS, IMPORTS, INHERITS — captured during
ingestion. MENTIONS (regex-fuzzy backtick-quoted dotted names in
markdown) is deferred to sub-PR #5c per spec Decision 1.

StrEnum so the on-disk ``kind`` column stays plain text — readable in
SQLite shell, no enum-import-needed for ad-hoc queries.
"""
from __future__ import annotations

from enum import StrEnum


class ReferenceKind(StrEnum):
    CALLS    = "calls"
    IMPORTS  = "imports"
    INHERITS = "inherits"
