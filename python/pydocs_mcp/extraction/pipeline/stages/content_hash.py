"""ContentHashStage — fills ``state.files.content_hash``, the package-level hash.

The package hash drives whole-package cache invalidation. Per-node
``DocumentNode.content_hash`` values are computed inside each chunker
and ride on the trees instead — they don't flow through state.

Framing, innermost first: ``hash_files(paths)``, then the CONDITIONAL
exclusion fold (only under user excludes), then the PROJECT-ONLY
``MODULE_ID_RULE_VERSION`` fold, then the CONDITIONAL, PROJECT-ONLY
decision-capture fold (only when ``decision_capture`` digests to something
other than the pinned stock baseline — issue #263), then the CONDITIONAL,
DEPENDENCY-ONLY member-extraction fold (only when the composition root's
member token is non-empty and differs from the pinned stock token — issue
#347), then the CONDITIONAL reference-capture fold on EVERY package (only when
``reference_graph.capture`` normalizes to something other than the pinned stock
token — issue #347), then the UNCONDITIONAL loadable-grammar salt (analyzers
spec §8.2), then the UNCONDITIONAL chunk-tree salt (issue #246 close-out —
``chunkers/chunk_tree_rules.py`` explains what it carries), then the identity
salt (pipeline hash + embed tier) wrapping whatever the first seven produced.
:meth:`ContentHashStage._ordered_salts` lists them in that order, None for a
fold that does not apply, and every fold is the same md5 digest-of-digest
step, :func:`_fold_digest`; the ORDER is load-bearing and pinned by
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
from pydocs_mcp.extraction.pipeline.stages.reference_capture import (
    capture_config_from_build_context,
)
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.extraction.strategies.python_module_id import MODULE_ID_RULE_VERSION
from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, exclusion_fingerprint
from pydocs_mcp.retrieval.config import DecisionCaptureConfig, ReferenceCaptureConfig

# ``md5(DecisionCaptureConfig().model_dump_json())[:16]`` as it stood when the
# decision fold shipped (issue #263): the one settings value that folds nothing,
# which is what keeps every stored hash of a stock deployment byte-identical.
# Pinned rather than compared against a live ``DecisionCaptureConfig()``: that
# would make the salt depend on the model's CURRENT defaults, so a release that
# moved a default (and so what stock mining emits) would still fold nothing for
# a stock deployment and re-open the #263 loop. Against a pin the salt depends
# only on the effective settings, so such a release folds by itself — one
# re-extract, then it settles. Re-pin ONLY for a change that leaves stock mining
# output unchanged (say, a new knob whose default keeps the old behaviour).
_STOCK_DECISION_CAPTURE_DIGEST = "67e6c428e8c34ab7"

# ``member_extraction_token`` for the CLI default — inspect mode at the
# ``MembersConfig()`` values — as it stood when the member-extraction fold
# shipped (issue #347): the one token that folds nothing. A readable literal
# because the token is a short ``key=value`` string. Pinned rather than rebuilt
# from a live ``MembersConfig()`` for the #263 reason above: a release that
# moved a member default (and so what stock extraction emits) must fold by
# itself. Ruff's S105 reads "TOKEN" as a credential; this is a cache token.
_STOCK_MEMBER_EXTRACTION_TOKEN = "inspect|depth=1|cap=120|sig=200|doc=1024"  # noqa: S105

# ``refs:`` + the sorted, distinct ``ReferenceCaptureConfig()`` kinds as they
# stood when the reference-capture fold shipped (issue #347) — the one settings
# value that folds nothing. A readable literal rather than a digest because the
# input is a small fixed tuple. Pinned rather than derived from a live
# ``ReferenceCaptureConfig()`` for the #263 reason above: a release that moved a
# capture default (and so what stock capture emits) must fold by itself. Ruff's
# S105 reads "TOKEN" as a credential; this is a cache token, hence the suppression.
_STOCK_REFERENCE_CAPTURE_TOKEN = "refs:calls,imports,inherits"  # noqa: S105


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
    # The same settings ``CaptureDecisionsPipeline`` mines with. Read here so a
    # YAML knob that changes the emitted decision chunks also moves the package
    # gate (issue #263); the stock default folds nothing, which is also what a
    # stage-isolation test should hash as.
    decision_capture: DecisionCaptureConfig = field(default_factory=DecisionCaptureConfig)
    # The settings the member extractor was built with, as the token
    # ``build_project_indexer`` derives (``extraction/strategies/members/
    # extraction_token.py``) — wiring from the composition root, like
    # ``pipeline_hash``, because only it knows ``--no-inspect`` and the resolved
    # ``--depth`` (issue #347). Empty in stage-isolation tests and hand-wired
    # roots, which is the documented no-fold path.
    member_extraction_token: str = ""
    # The same settings ``ReferenceCaptureStage`` captures with — both decode
    # them through ``capture_config_from_build_context`` — so a YAML knob that
    # changes the captured edges also moves the package gate (issue #347); the
    # stock default folds nothing, which is also what a stage-isolation test
    # should hash as.
    reference_capture: ReferenceCaptureConfig = field(default_factory=ReferenceCaptureConfig)
    name: str = "content_hash"

    async def run(self, state: IngestionState) -> IngestionState:
        package_hash = await asyncio.to_thread(self._hash, state)
        return replace(state, files=replace(state.files, content_hash=package_hash))

    def _hash(self, state: IngestionState) -> str:
        """The package hash: the base digest wrapped in every applicable salt.

        Runs off the event loop, salts included: the first grammar salt in a
        process imports tree_sitter and every grammar wheel.
        """
        return _fold_ordered_salts(_base_digest(state.files.paths), self._ordered_salts(state))

    def _ordered_salts(self, state: IngestionState) -> tuple[str | None, ...]:
        """Every salt, innermost first; None marks a fold that does not apply.

        Fold ORDER is part of the hash: each fold wraps the previous digest, so
        a permutation yields different values. Ordered narrowest scope first —
        excludes (some deployments) → project targets (one package per index:
        the rule token, then decision capture, which is ALSO conditional) →
        dependency targets, conditionally (member extraction, issue #347) →
        every package, conditionally (reference capture, issue #347) → every
        package (grammars, then chunk rules) → every package under a pipeline
        identity — which is the only order that keeps every fold's own framing
        literally true at once: the identity salt "wraps whatever the first
        three produced" (ingestion-cache-gates fix, written when it wrapped
        three; it is seven now and still outermost), the grammar salt "wraps
        whatever the earlier folds produced" (analyzers spec §8.2) and the rule
        token folds "after the exclusion fingerprint" (member-module-ids spec
        §4). A new conditional fold goes before the grammar salt, so every
        conditional fold stays inside the unconditional ones.
        """
        kind = state.files.target_kind
        return (
            _exclusion_fingerprint(state.files),
            _module_id_rule_salt(kind),
            # Issue #263 — see _decision_capture_salt for why it is conditional
            # and project-only.
            _decision_capture_salt(self.decision_capture, kind),
            _member_extraction_salt(self.member_extraction_token, kind),
            _reference_capture_salt(self.reference_capture),
            _grammar_salt(),
            _chunk_tree_salt(self.chunking),
            self._pipeline_salt(state),
        )

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

        The CHUNK hashes already fold these, but the PACKAGE hash is the gate
        ProjectIndexer checks FIRST — and it used to short-circuit before the
        chunk diff ever ran. So a pipeline or tier change re-embedded every
        chunk and then discarded the result as "cached", on every pass forever,
        healed only by ``index --force``. Folding them in the hash itself covers
        every indexing entry point rather than one orchestrator.
        """
        if not self.pipeline_hash:
            return None
        tier = self.embed_policy.tier(state.files.target_kind, state.files.package_name)
        return f"pipeline:{self.pipeline_hash}|tier:{tier}"

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> ContentHashStage:
        app_config = getattr(context, "app_config", None)
        chunking = getattr(getattr(app_config, "extraction", None), "chunking", None)
        # Wired exactly as CaptureDecisionsPipeline.from_dict wires it, so the
        # stage that hashes sees the settings the stage that mines used.
        decision_capture = getattr(app_config, "decision_capture", None) or DecisionCaptureConfig()
        reference_capture = capture_config_from_build_context(context) or ReferenceCaptureConfig()
        return cls(
            pipeline_hash=getattr(context, "pipeline_hash", ""),
            embed_policy=EmbedPolicy.from_config(getattr(app_config, "embedding", None)),
            chunking=chunking if chunking is not None else ChunkingConfig(),
            decision_capture=decision_capture,
            member_extraction_token=getattr(context, "member_extraction_token", ""),
            reference_capture=reference_capture,
        )

    def to_dict(self) -> dict:
        return {"type": "content_hash"}


def _base_digest(paths: tuple[str, ...]) -> str:
    """``hash_files(paths)`` as a str — the digest every salt wraps."""
    # Deferred so _fast's native/fallback choice is resolved lazily.
    from pydocs_mcp._fast import hash_files

    result = hash_files(list(paths))
    # hash_files may return str (fallback) or bytes (some native builds).
    # Normalize so downstream consumers see a stable str regardless.
    return result if isinstance(result, str) else result.hex()


def _fold_ordered_salts(digest: str, salts: tuple[str | None, ...]) -> str:
    """Wrap ``digest`` in each salt in turn, skipping the ones that are None."""
    for salt in salts:
        if salt is not None:
            digest = _fold_digest(digest, salt)
    return digest


def _exclusion_fingerprint(files: FileBundle) -> str | None:
    """Fingerprint of the exclude set this run's discovery walk pruned against.

    Conditional exclusion fold: no user excludes → no fold (the exclude-dirs
    design, spec §9.2), so adding that feature alone never invalidated an
    exclude-less deployment's stored hashes.

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


def _module_id_rule_salt(target_kind: TargetKind) -> str | None:
    """``MODULE_ID_RULE_VERSION`` for a project target, None for a dependency.

    Member module ids are computed after the project cache skip, so a
    module-id rule change reaches an existing index only through this hash: the
    token makes every stored __project__ hash miss once (one re-extraction, no
    re-embed; dependencies never fold). Not a SCHEMA_VERSION bump: v17 is
    reserved by the multi-branch P1 plan, and an older running process that met
    an unknown version would wipe the index (member-module-ids spec §4).
    """
    return MODULE_ID_RULE_VERSION if target_kind is TargetKind.PROJECT else None


def _decision_capture_salt(config: DecisionCaptureConfig, target_kind: TargetKind) -> str | None:
    """The decision-capture token, or None when there is nothing to fold.

    WHY a fold at all (issue #263): ``CaptureDecisionsPipeline`` mines with these
    settings and emits every decision AS A CHUNK, yet they reached no cache key —
    and this stage runs after ``capture_decisions`` and ``embed_chunks`` in
    ``pipelines/ingestion.yaml`` (mining runs on every pass regardless). So a
    changed knob re-embedded the changed decision chunks on every pass, then
    discarded them as a package cache hit, forever, healed only by
    ``index --force``.

    None for a dependency: ``CaptureDecisionsPipeline.run`` short-circuits every
    dependency target (and ``include_deps`` is consumed nowhere), so no knob can
    change what a dependency extracts — folding there would re-extract every
    dependency for nothing. None for the stock settings too — those whose digest
    is :data:`_STOCK_DECISION_CAPTURE_DIGEST` — so every stored hash of a stock
    deployment stays byte-identical and upgrading costs no re-extraction (the
    conditional-exclusion-fold precedent).

    Digested from ``model_dump_json`` rather than a hand-picked field list:
    pydantic emits fields in declaration order, so it is stable across
    processes, and a knob added later folds itself. md5 and ``[:16]`` match the
    non-cryptographic cache-fingerprint posture of :func:`_fold_digest`.

    Example: a project tuned with ``merge_jaccard: 0.5`` returns
    ``'decisions:'`` followed by 16 lowercase hex characters.
    """
    if target_kind is not TargetKind.PROJECT:
        return None
    blob = config.model_dump_json().encode()
    digest = hashlib.md5(blob, usedforsecurity=False).hexdigest()[:16]
    return None if digest == _STOCK_DECISION_CAPTURE_DIGEST else f"decisions:{digest}"


def _member_extraction_salt(token: str, target_kind: TargetKind) -> str | None:
    """The member-extraction token, or None when there is nothing to fold.

    WHY a fold at all (issue #347): ``ProjectIndexer`` extracts a dependency's
    members AFTER its package cache check, with the extractor built from
    ``--no-inspect``, ``--depth`` and ``extraction.members.*`` — none of which
    reached a cache key. So changing one left every already-indexed dependency a
    cache hit, its members frozen at the first settings, healed only by
    ``index --force``.

    DEPENDENCY targets only: project members always come from the AST extractor
    (``InspectMemberExtractor`` hands the project to its static fallback), which
    reads no member setting, so a project fold would only re-extract — every
    static-mode project on upgrade. tests/storage/test_build_project_indexer.py
    guards that invariant. None for an empty token (a root that never supplies
    one) and for :data:`_STOCK_MEMBER_EXTRACTION_TOKEN`, so every stored hash of
    a stock deployment stays byte-identical; a static-mode deployment
    re-extracts its dependencies once on upgrade, which is the fix working.

    Example: ``_member_extraction_salt("static", TargetKind.DEPENDENCY)``
    returns ``'members:static'``; any token for a project target returns None.
    """
    if target_kind is not TargetKind.DEPENDENCY or not token:
        return None
    return None if token == _STOCK_MEMBER_EXTRACTION_TOKEN else f"members:{token}"


def _reference_capture_salt(config: ReferenceCaptureConfig) -> str | None:
    """The reference-capture token, or None when there is nothing to fold.

    WHY a fold at all (issue #347): ``ReferenceCaptureStage`` captures with these
    settings and runs BEFORE this stage in ``pipelines/ingestion.yaml``, yet they
    reached no cache key. So turning ``mentions`` on, or capture off, captured
    the new edge set on every pass and discarded it as a package cache hit,
    forever, healed only by ``index --force``.

    EVERY target kind: capture does not gate on target kind, so a dependency's
    edges depend on these settings as much as the project's do. None for the
    stock settings — those that normalize to
    :data:`_STOCK_REFERENCE_CAPTURE_TOKEN` — so every stored hash of a stock
    deployment stays byte-identical and upgrading costs no re-extraction.

    Normalized rather than digested from ``model_dump_json``: capture reads
    ``frozenset(kinds)``, so kind order and duplicates change no edge and must
    re-extract nothing; with capture off the kinds are moot, so every disabled
    config is one token. Sorting also keeps the token stable across processes.

    Example: ``kinds=("mentions", "calls", "calls")`` returns
    ``'refs:calls,mentions'``; ``enabled=False`` returns ``'refs:disabled'``.
    """
    token = "refs:" + ",".join(sorted(set(config.kinds))) if config.enabled else "refs:disabled"
    return None if token == _STOCK_REFERENCE_CAPTURE_TOKEN else token


def _grammar_salt() -> str:
    """Loadable-grammar salt (analyzers spec §8.2, D9): UNCONDITIONAL.

    Unlike the exclusion fold, an empty fingerprint must stay distinguishable
    from "not folded", and the hash must flip on BOTH transitions (grammars
    appear AND disappear). Costs one full re-extract on upgrade, subsumed by
    the §8.1 scope-fold re-embed.
    """
    return f"grammars:{_grammar_fingerprint()}"


def _chunk_tree_salt(chunking: ChunkingConfig) -> str:
    """Chunk-tree salt (issue #246 close-out): UNCONDITIONAL.

    Outside the grammar salt because it is about what the chunkers DO with a
    grammar rather than which ones load. Without it a chunker change could not
    reach a cached package at all — #257 and #258 both changed chunk trees and
    both had to tell operators to touch the files or --force.
    """
    return f"chunks:{_chunk_tree_fingerprint(chunking)}"


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
