"""ReferenceKind enum for the cross-node reference graph (spec §4.1).

Five kinds. Three are AST-precise — CALLS, IMPORTS, INHERITS — captured during
Python file ingestion. MENTIONS is regex-fuzzy: backtick-quoted dotted names in
markdown, lower-precision than AST capture and therefore opt-in via YAML.
SIMILAR is index-time *synthetic*: embedding-kNN edges between a node and its
top-m nearest neighbours, densifying the otherwise-sparse AST graph so graph
expansion can reach semantically-related code that has no call/inherit edge.
Opt-in (off by default).

StrEnum so the on-disk ``kind`` column stays plain text — readable in
SQLite shell, no enum-import-needed for ad-hoc queries.
"""

from __future__ import annotations

from enum import StrEnum


class ReferenceKind(StrEnum):
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    MENTIONS = "mentions"
    SIMILAR = "similar"
