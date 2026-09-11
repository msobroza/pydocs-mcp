"""ContentHashStage — fills ``state.files.content_hash``, the package-level hash.

The package hash drives whole-package cache invalidation. Per-node
``DocumentNode.content_hash`` values are computed inside each chunker
and ride on the trees instead — they don't flow through state.

Framing, innermost first: ``hash_files(paths)``, then the CONDITIONAL
exclusion fold (only under user excludes), then the PROJECT-ONLY
``MODULE_ID_RULE_VERSION`` fold, then the UNCONDITIONAL loadable-grammar
salt (analyzers spec §8.2) wrapping whatever the earlier folds produced.
Every fold is the same md5 digest-of-digest step, :func:`_fold_digest`; the
ORDER is load-bearing and pinned by
tests/extraction/test_content_hash_fold_composition.py.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.extraction.strategies.python_module_id import MODULE_ID_RULE_VERSION
from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, exclusion_fingerprint


@stage_registry.register("content_hash")
@dataclass(frozen=True, slots=True)
class ContentHashStage:
    name: str = "content_hash"

    async def run(self, state: IngestionState) -> IngestionState:
        files = state.files
        package_hash = await asyncio.to_thread(
            self._hash, list(files.paths), _exclusion_fingerprint(files), files.target_kind
        )
        return replace(state, files=replace(files, content_hash=package_hash))

    def _hash(self, paths: list[str], exclusion_salt: str | None, target_kind: TargetKind) -> str:
        # Deferred so _fast's native/fallback choice is resolved lazily.
        from pydocs_mcp._fast import hash_files

        result = hash_files(paths)
        # hash_files may return str (fallback) or bytes (some native builds).
        # Normalize so downstream consumers see a stable str regardless.
        digest = result if isinstance(result, str) else result.hex()
        # Fold ORDER is part of the hash: each fold wraps the previous digest,
        # so a permutation yields different values. Ordered narrowest scope
        # first — excludes (some deployments) → project targets (one package
        # per index) → every package — which is the only order that keeps both
        # folds' own framings literally true at once: the grammar salt "wraps
        # whatever the earlier folds produced" (analyzers spec §8.2) and the
        # rule token folds "after the exclusion fingerprint"
        # (member-module-ids spec §4).
        if exclusion_salt is not None:
            # Conditional exclusion fold: no user excludes → no fold (the
            # exclude-dirs design, spec §9.2), so adding that feature alone
            # never invalidated an exclude-less deployment's stored hashes.
            digest = _fold_digest(digest, exclusion_salt)
        if target_kind is TargetKind.PROJECT:
            # Member module ids are computed after the project cache skip, so
            # a module-id rule change reaches an existing index only through
            # this hash: the token makes every stored __project__ hash miss
            # once (one re-extraction, no re-embed; dependencies never fold).
            # Not a SCHEMA_VERSION bump: v17 is reserved by the multi-branch
            # P1 plan, and an older running process that met an unknown
            # version would wipe the index (member-module-ids spec §4).
            digest = _fold_digest(digest, MODULE_ID_RULE_VERSION)
        # Loadable-grammar salt (analyzers spec §8.2, D9): UNCONDITIONAL —
        # unlike the exclusion fold, an empty fingerprint must stay
        # distinguishable from "not folded", and the hash must flip on BOTH
        # transitions (grammars appear AND disappear). Costs one full
        # re-extract on upgrade, subsumed by the §8.1 scope-fold re-embed.
        return _fold_digest(digest, f"grammars:{_grammar_fingerprint()}")

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> ContentHashStage:
        return cls()

    def to_dict(self) -> dict:
        return {"type": "content_hash"}


def _exclusion_fingerprint(files: FileBundle) -> str | None:
    """Fingerprint of the exclude set this run's discovery walk pruned against.

    State-carried, never re-derived (spec D10): a mid-``--watch`` pyproject
    save landing between the two stages must not fold a set the walk didn't
    use. EMPTY_PROJECT_EXCLUDES is the "discovery never supplied a set"
    sentinel (directly constructed states in tests / legacy callers): folding
    its empty fingerprint would silently change every such hash, so it is
    no-fold like the floor-only case, which exclusion_fingerprint itself
    collapses to None (spec §9.2: the exclusion fold alone never moves an
    exclude-less hash; the grammar salt in ``_hash`` is a separate,
    deliberate move).
    """
    excludes = files.effective_excludes
    if excludes == EMPTY_PROJECT_EXCLUDES:
        return None
    return exclusion_fingerprint(excludes, _EXCLUDED_DIRS)


def _grammar_fingerprint() -> str:
    # Deferred: a stage module must not pull the chunker stack at import time.
    from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
        loadable_grammar_fingerprint,
    )

    # Performance: the first package hash in a process imports tree_sitter and
    # every grammar wheel (~3 ms for all seven, measured), even for pure-Python
    # projects and dependency packages; every later hash is a memo lookup
    # (microseconds).
    return loadable_grammar_fingerprint()


def _fold_digest(base: str, token: str) -> str:
    """Wrap ``base`` with ``token``: ``md5(f"{base}\\x00{token}")[:16]``.

    Digest-of-digest because hash_files' input framing is owned by the
    Rust/fallback parity pair and cannot grow a parameter (exclude-dirs spec
    D7 — no Rust change), so a token wraps the base digest instead of entering
    it. md5 matches the fallback's non-cryptographic cache-fingerprint
    posture; ``[:16]`` matches the base digest width.

    Example: ``_fold_digest("0123456789abcdef", "package-root/1")`` returns
    16 lowercase hex characters.
    """
    folded = hashlib.md5(f"{base}\x00{token}".encode(), usedforsecurity=False)
    return folded.hexdigest()[:16]


__all__ = ("ContentHashStage",)
