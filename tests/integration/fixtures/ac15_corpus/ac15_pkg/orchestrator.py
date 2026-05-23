"""Cross-module composition. Most CALLS edges in the corpus live here."""
from __future__ import annotations

from ac15_pkg.indexer import Indexer
from ac15_pkg.inheritance import Leaf
from ac15_pkg.pipeline import Pipeline
from ac15_pkg.stdlib_user import hash_text, parse_json
from ac15_pkg.types_and_helpers import compute_sum


def build_indexer(multiplier: int = 1) -> Indexer:
    """Constructs Pipeline + Indexer. Calls Indexer.__init__ (Rule B) +
    Pipeline.__init__ (Rule B)."""
    return Indexer(pipeline=Pipeline(multiplier=multiplier))


def run_demo(name: str) -> str:
    """End-to-end demo. Many cross-module calls."""
    indexer = build_indexer()
    result = indexer.index_pair(name, 1, 2)
    leaf = Leaf()
    hashed = hash_text(f"{name}:{result}")
    parsed = parse_json("{}")
    total = compute_sum(result, len(parsed))
    return f"{leaf.announce()}:{hashed}:{total}"
