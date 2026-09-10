"""ContentHashStage — fills ``state.files.content_hash``, the package-level hash.

The package hash drives whole-package cache invalidation. Per-node
``DocumentNode.content_hash`` values are computed inside each chunker
and ride on the trees instead — they don't flow through state.

Framing: ``hash_files(paths)``, then the CONDITIONAL exclusion fold (only
when user excludes are in effect), then the UNCONDITIONAL loadable-grammar
salt (analyzers spec §8.2) wrapping whatever the first two produced.
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
        # exclusion fold alone never moves an exclude-less hash; the grammar
        # salt in ``_hash`` is a separate, deliberate move).
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
        if fingerprint is not None:
            # Conditional exclusion fold: no user excludes → no fold (the
            # exclude-dirs design, spec §9.2), so adding that feature alone
            # never invalidated an exclude-less deployment's stored hashes.
            base = _fold(base, fingerprint)
        # Loadable-grammar salt (analyzers spec §8.2, D9): UNCONDITIONAL —
        # unlike the exclusion fold, an empty fingerprint must stay
        # distinguishable from "not folded", and the hash must flip on BOTH
        # transitions (grammars appear AND disappear). Costs one full
        # re-extract on upgrade, subsumed by the §8.1 scope-fold re-embed.
        return _fold(base, f"grammars:{_grammar_fingerprint()}")

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> ContentHashStage:
        return cls()

    def to_dict(self) -> dict:
        return {"type": "content_hash"}


def _grammar_fingerprint() -> str:
    # Deferred: a stage module must not pull the chunker stack at import time.
    from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
        loadable_grammar_fingerprint,
    )

    return loadable_grammar_fingerprint()


def _fold(base: str, salt: str) -> str:
    """Digest-of-digest fold shared by both salts: ``md5(base NUL salt)[:16]``.

    hash_files' input framing is owned by the Rust/fallback parity pair and
    cannot grow a parameter (exclude-dirs spec D7: no Rust change), so a salt
    wraps the base digest instead of entering it. md5 matches the fallback's
    non-cryptographic cache-fingerprint posture; [:16] matches the base
    digest width.
    """
    folded = hashlib.md5(f"{base}\x00{salt}".encode(), usedforsecurity=False)
    return folded.hexdigest()[:16]


__all__ = ("ContentHashStage",)
