"""ContentHashStage — fills ``state.files.content_hash``, the package-level hash.

The package hash drives whole-package cache invalidation. Per-node
``DocumentNode.content_hash`` values are computed inside each chunker
and ride on the trees instead — they don't flow through state.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, exclusion_fingerprint


@stage_registry.register("content_hash")
@dataclass(frozen=True, slots=True)
class ContentHashStage:
    name: str = "content_hash"

    async def run(self, state: IngestionState) -> IngestionState:
        excludes = state.files.effective_excludes
        # Fold the fingerprint of the set the SAME run's discovery walk
        # actually pruned against (state-carried, never re-derived — spec
        # D10: a mid-``--watch`` pyproject save landing between the two
        # stages must not fold a set the walk didn't use).
        # EMPTY_PROJECT_EXCLUDES is the "discovery never supplied a set"
        # sentinel (directly constructed states in tests / legacy callers):
        # folding its empty fingerprint would silently change every such
        # hash, so it is no-fold like the floor-only case — which
        # exclusion_fingerprint itself collapses to None (spec §9.2: the
        # conditional fold keeps every pre-upgrade stored hash valid).
        fingerprint = (
            None
            if excludes == EMPTY_PROJECT_EXCLUDES
            else exclusion_fingerprint(excludes, _EXCLUDED_DIRS)
        )
        h = await asyncio.to_thread(self._hash, list(state.files.paths), fingerprint)
        new_files = replace(state.files, content_hash=h)
        return replace(state, files=new_files)

    def _hash(self, paths: list[str], fingerprint: str | None) -> str:
        # Deferred so _fast's native/fallback choice is resolved lazily.
        from pydocs_mcp._fast import hash_files

        result = hash_files(paths)
        # hash_files may return str (fallback) or bytes (some native builds).
        # Normalize so downstream consumers see a stable str regardless.
        base = result if isinstance(result, str) else result.hex()
        if fingerprint is None:
            # No user excludes → byte-identical to the historical framing
            # (spec §9.2 — upgrade is free for exclude-less deployments).
            return base
        # Fold via digest-of-digest: hash_files' input framing is owned by
        # the Rust/fallback parity pair and cannot grow a parameter (spec
        # D7 — no Rust change), so the fingerprint wraps the base digest
        # instead of entering it. md5 matches the fallback's
        # non-cryptographic cache-fingerprint posture; [:16] matches the
        # base digest width.
        folded = hashlib.md5(
            f"{base}\x00{fingerprint}".encode(),
            usedforsecurity=False,
        )
        return folded.hexdigest()[:16]

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> ContentHashStage:
        return cls()

    def to_dict(self) -> dict:
        return {"type": "content_hash"}


__all__ = ("ContentHashStage",)
