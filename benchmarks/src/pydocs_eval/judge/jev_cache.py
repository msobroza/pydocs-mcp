"""Jev's answers on disk, by input hash: a question asked twice is paid for once.

The key is the hash of the whole request body, model pin included
(:func:`~pydocs_eval.judge.jev_wire.jev_cache_key`), so a pin change can never
be served an answer another model gave. Each entry is one JSON file, written to
a temporary name and renamed into place, so a crash mid-write leaves no
half-entry; an entry that cannot be read is a miss, never a failure.

Example:
    >>> cache = JevResponseCache(jev_cache_dir(JevConfig()))  # doctest: +SKIP
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval._bench_cache import cache_root
from pydocs_eval.atomic_text import write_text_atomically
from pydocs_eval.judge.config import JevConfig

_CACHE_SUBDIR = "jev"
_ENTRY_SUFFIX = ".json"


def jev_cache_dir(config: JevConfig) -> Path:
    """``judge.jev.cache_dir``, or the derived default beside the bench index cache.

    Example:
        >>> jev_cache_dir(JevConfig(cache_dir="/tmp/jev"))
        PosixPath('/tmp/jev')
    """
    if config.cache_dir:
        return Path(config.cache_dir).expanduser()
    return cache_root() / _CACHE_SUBDIR


@dataclass(frozen=True, slots=True)
class JevResponseCache:
    """Raw ``systemone`` response bodies under ``root``, one file per input hash."""

    root: Path

    def get(self, key: str) -> object | None:
        """The body stored under ``key``, or ``None`` when absent or unreadable."""
        try:
            payload: object = json.loads(self._entry(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload

    def put(self, key: str, payload: object) -> None:
        """Store ``payload`` under ``key``: a reader sees the old entry or the whole new one."""
        write_text_atomically(self._entry(key), json.dumps(payload, ensure_ascii=False))

    def _entry(self, key: str) -> Path:
        return self.root / f"{key}{_ENTRY_SUFFIX}"


__all__ = ("JevResponseCache", "jev_cache_dir")
