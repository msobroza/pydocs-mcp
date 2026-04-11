"""Utility helpers for the fake analytics project."""
import hashlib
import statistics
from typing import Iterable


def batch(iterable: Iterable, size: int):
    """Yield successive chunks of *size* from *iterable*."""
    buf = []
    for item in iterable:
        buf.append(item)
        if len(buf) == size:
            yield buf
            buf = []
    if buf:
        yield buf


def mean_and_std(values: list[float]) -> tuple[float, float]:
    """Return (mean, stdev) or (0.0, 0.0) for empty input."""
    if not values:
        return 0.0, 0.0
    m = statistics.mean(values)
    s = statistics.pstdev(values) if len(values) > 1 else 0.0
    return m, s


def fingerprint(text: str) -> str:
    """Return a short SHA-256 hex digest of *text*."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]
