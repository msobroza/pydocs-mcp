"""ReferenceKind enum for the cross-node reference graph (spec §4.1).

Four kinds total. Three are AST-precise — CALLS, IMPORTS, INHERITS —
captured during Python file ingestion. MENTIONS is regex-fuzzy:
backtick-quoted dotted names in markdown, lower-precision than AST
capture and therefore opt-in via YAML (sub-PR #5c, §5.3).

StrEnum so the on-disk ``kind`` column stays plain text — readable in
SQLite shell, no enum-import-needed for ad-hoc queries.
"""
from __future__ import annotations

from enum import StrEnum


class ReferenceKind(StrEnum):
    CALLS    = "calls"
    IMPORTS  = "imports"
    INHERITS = "inherits"
    MENTIONS = "mentions"
