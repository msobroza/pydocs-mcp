"""ContentHashStage — fills ``state.files.content_hash``, the package-level hash.

The package hash drives whole-package cache invalidation. Per-node
``DocumentNode.content_hash`` values are computed inside each chunker
and ride on the trees instead — they don't flow through state.

Framing, innermost first: ``hash_files(paths)``, then the CONDITIONAL
exclusion fold (only under user excludes), then the PROJECT-ONLY
``MODULE_ID_RULE_VERSION`` fold, then the UNCONDITIONAL loadable-grammar
salt (analyzers spec §8.2), then the UNCONDITIONAL chunk-tree salt (issue
#246 close-out — ``chunkers/chunk_tree_rules.py`` explains what it carries),
then the identity salt (pipeline hash + embed tier) wrapping whatever the
first four produced. Every fold is the same md5 digest-of-digest step,
:func:`_fold_digest`; the ORDER is load-bearing and pinned by
tests/extraction/test_content_hash_fold_composition.py.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field, replace
from typing import Any

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, ChunkingConfig
from pydocs_mcp.extraction.embed_policy import EmbedPolicy
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.extraction.strategies.python_module_id import MODULE_ID_RULE_VERSION
from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, exclusion_fingerprint


@stage_registry.register("content_hash")
@dataclass(frozen=True, slots=True)
class ContentHashStage:
    # Identity of the whole ingestion pipeline — embedder, search backend,
    # effective extension scope and the ingestion YAML's raw bytes. Supplied by
    # the composition root, so it is wiring rather than a tunable and never
    # round-trips through ``to_dict``. Empty in stage-isolation tests, which is
    # the documented no-fold path (mirrors AssignChunkContentHashStage).
    pipeline_hash: str = ""
    # Decides this package's embed tier, the second half of the identity salt.
    embed_policy: EmbedPolicy = field(default_factory=EmbedPolicy)
    # The same tunables ``ChunkingStage`` hands the chunkers. Read here so a YAML
    # knob that changes emitted trees also moves the package gate; defaults match
    # a stock deployment, which is what a stage-isolation test should hash as.
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    name: str = "content_hash"

    async def run(self, state: IngestionState) -> IngestionState:
        files = state.files
        package_hash = await asyncio.to_thread(
            self._hash,
            list(files.paths),
            _exclusion_fingerprint(files),
            files.target_kind,
            self._pipeline_salt(state),
            self.chunking,
        )
        return replace(state, files=replace(files, content_hash=package_hash))

    def _pipeline_salt(self, state: IngestionState) -> str | None:
        """The identity salt, or None when this stage was built without one.

        Deliberately the SAME two components ``AssignChunkContentHashStage``
        folds into every chunk hash: the pipeline hash and the package's embed
        tier. The tier is a separate component because it is per-package and is
        deliberately kept OUT of ``ingestion_pipeline_hash`` (see
        ``EmbeddingConfig.dependency_policy``), so that promoting one dependency
        re-embeds only that package. Folding both keeps the package-level gate
        and the chunk-level diff invalidated by exactly the same events — the
        package gate runs first, so anything it misses can never reach the diff.
        """
        if not self.pipeline_hash:
            return None
        tier = self.embed_policy.tier(state.files.target_kind, state.files.package_name)
        return f"pipeline:{self.pipeline_hash}|tier:{tier}"

    def _hash(
        self,
        paths: list[str],
        exclusion_salt: str | None,
        target_kind: TargetKind,
        pipeline_salt: str | None,
        chunking: ChunkingConfig,
    ) -> str:
        # Deferred so _fast's native/fallback choice is resolved lazily.
        from pydocs_mcp._fast import hash_files

        result = hash_files(paths)
        # hash_files may return str (fallback) or bytes (some native builds).
        # Normalize so downstream consumers see a stable str regardless.
        digest = result if isinstance(result, str) else result.hex()
        # Fold ORDER is part of the hash: each fold wraps the previous digest,
        # so a permutation yields different values. Ordered narrowest scope
        # first — excludes (some deployments) → project targets (one package
        # per index) → every package (grammars, then chunk rules) → every
        # package under a pipeline identity — which is the only order that keeps
        # every fold's own framing literally true at once: the identity salt
        # "wraps whatever the first three produced" (ingestion-cache-gates fix,
        # written when it wrapped three; it is four now and still outermost),
        # the grammar salt "wraps whatever the earlier folds produced"
        # (analyzers spec §8.2) and the rule token folds "after the exclusion
        # fingerprint" (member-module-ids spec §4).
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
        digest = _fold_digest(digest, f"grammars:{_grammar_fingerprint()}")
        # Chunk-tree salt (issue #246 close-out): UNCONDITIONAL, and outside the
        # grammar salt because it is about what the chunkers DO with a grammar
        # rather than which ones load. Without it a chunker change could not
        # reach a cached package at all — #257 and #258 both changed chunk trees
        # and both had to tell operators to touch the files or --force.
        digest = _fold_digest(digest, f"chunks:{_chunk_tree_fingerprint(chunking)}")
        if pipeline_salt is None:
            return digest
        # Identity salt (see _pipeline_salt for what goes in it). The CHUNK
        # hashes already fold these, but the PACKAGE hash is the gate
        # ProjectIndexer checks FIRST — and it used to short-circuit before the
        # chunk diff ever ran. So a pipeline or tier change re-embedded every
        # chunk and then discarded the result as "cached", on every pass
        # forever, healed only by ``index --force``. Folding the same inputs
        # here keeps both cache levels invalidated by the same events, in the
        # hash itself, so every indexing entry point is covered rather than one
        # orchestrator.
        return _fold_digest(digest, pipeline_salt)

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> ContentHashStage:
        app_config = getattr(context, "app_config", None)
        chunking = getattr(getattr(app_config, "extraction", None), "chunking", None)
        return cls(
            pipeline_hash=getattr(context, "pipeline_hash", ""),
            embed_policy=EmbedPolicy.from_config(getattr(app_config, "embedding", None)),
            chunking=chunking if chunking is not None else ChunkingConfig(),
        )

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


def _chunk_tree_fingerprint(chunking: ChunkingConfig) -> str:
    # Deferred for the same reason as _grammar_fingerprint: a stage module must
    # not pull the chunker stack at import time.
    from pydocs_mcp.extraction.strategies.chunkers.chunk_tree_rules import (
        chunk_tree_fingerprint,
    )

    return chunk_tree_fingerprint(chunking)


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
