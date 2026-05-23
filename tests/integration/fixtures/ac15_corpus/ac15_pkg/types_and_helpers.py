"""Type aliases + simple helpers. Exercises Rule B (exact qname match)."""
from __future__ import annotations

from typing import TypeAlias

# Type alias usable across modules
ChunkId: TypeAlias = int


def compute_sum(a: int, b: int) -> int:
    """Pure helper. Called from `pipeline.process` (cross-module call,
    Rule B: exact qname match resolves)."""
    return a + b


def compute_product(a: int, b: int) -> int:
    return a * b


def normalize(name: str) -> str:
    """Used by `Indexer.normalize_name` (self-attribute pattern)."""
    return name.strip().lower()
