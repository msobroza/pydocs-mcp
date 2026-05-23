"""Indexer class with self.X.Y patterns. Exercises Rule 0."""
from __future__ import annotations

from ac15_pkg.pipeline import Pipeline
from ac15_pkg.types_and_helpers import normalize


class Indexer:
    """Test class for self.X.Y rewrite (Rule 0)."""

    def __init__(self, pipeline: Pipeline) -> None:
        self.pipeline = pipeline      # self.pipeline: Pipeline
        self.cache: dict[str, int] = {}

    def index_pair(self, name: str, a: int, b: int) -> int:
        """Calls self.pipeline.process — Rule 0 rewrites to Pipeline.process,
        then Rule B resolves Pipeline.process to ac15_pkg.pipeline.Pipeline.process."""
        clean = self.normalize_name(name)
        result = self.pipeline.process(a, b)
        self.cache[clean] = result
        return result

    def normalize_name(self, name: str) -> str:
        """Calls normalize (cross-module, Rule B)."""
        return normalize(name)
