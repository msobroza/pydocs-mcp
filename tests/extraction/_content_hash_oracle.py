"""Independent oracle for ``ContentHashStage``'s package-hash framing.

Suites that pin an EXACT package content hash derive the expectation here,
from the documented formula, instead of calling the stage's private fold — so
a regression in the stage cannot silently move the expectation along with it.
One copy, because several suites pin it.

Framing (``stages/content_hash.py``), innermost first: ``hash_files(paths)``
normalized to a str, then the conditional exclusion fold, then the
project-only ``MODULE_ID_RULE_VERSION`` fold, then the conditional
decision-capture fold (a project folds it when ``decision_capture`` digests
to something other than the stock baseline the stage pins; the structuring
LLM's identity rides inside that token, never as a fold of its own; a
dependency folds the plain token only while ``enabled`` and ``include_deps``
mine it — issue #346), then the
conditional, dependency-only member-extraction fold (only when the composition
root's member-extraction token is non-empty and differs from the stock token
the stage pins — issue #347), then the conditional reference-capture fold on
every package (only when
``reference_graph.capture`` normalizes to something other than the stock
token the stage pins — issue #347), then the unconditional loadable-grammar
salt, then the unconditional chunk-tree salt, then the identity salt
(pipeline hash + embed tier), which a stage built without a pipeline hash
omits. Each fold is exposed separately rather than as one composed helper so
every pin spells the ORDER it depends on out loud.
"""

from __future__ import annotations

import hashlib

from pydocs_mcp.extraction.config import ChunkingConfig
from pydocs_mcp.extraction.strategies.chunkers.chunk_tree_rules import (
    chunk_tree_fingerprint,
)
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    loadable_grammar_fingerprint,
)
from pydocs_mcp.extraction.strategies.python_module_id import MODULE_ID_RULE_VERSION
from pydocs_mcp.retrieval.config import DecisionCaptureConfig, LlmConfig, ReferenceCaptureConfig


def raw_hash_files(paths: list[str]) -> str:
    """``hash_files`` output normalized exactly as the stage normalizes it
    (str passthrough / bytes → hex) — the base digest every fold wraps."""
    # Deferred, like the stage's own import, so disable_rust() swaps apply.
    from pydocs_mcp._fast import hash_files

    result = hash_files(paths)
    return result if isinstance(result, str) else result.hex()


def digest_fold(base: str, salt: str) -> str:
    """The digest-of-digest fold every salt uses: ``md5(base NUL salt)[:16]``."""
    salted = f"{base}\x00{salt}".encode()
    return hashlib.md5(salted, usedforsecurity=False).hexdigest()[:16]


def rule_folded(base: str) -> str:
    """``base`` wrapped in the PROJECT-only ``MODULE_ID_RULE_VERSION`` token.

    Example: ``rule_folded(raw_hash_files(paths))`` is the pre-grammar-salt
    digest of a project bundle with no user excludes.
    """
    return digest_fold(base, MODULE_ID_RULE_VERSION)


def decision_capture_token(config: DecisionCaptureConfig, llm: LlmConfig | None = None) -> str:
    """The decision-capture token for ``config`` (issue #263):
    ``decisions:`` + ``md5(config.model_dump_json())[:16]``, then — only when
    ``llm`` is given — ``|llm:`` + ``md5("provider|model_name|temperature|
    max_tokens")[:16]`` for the structuring LLM (issue #347).

    Re-derived here from the documented recipe rather than read from the stage,
    so a stage that digested a hand-picked field subset (or a nonce) — or
    hashed ``api_key`` into the LLM part — would not match. The stage folds the
    token ONLY for a project target whose settings digest to something other
    than its pinned stock baseline — i.e. for any config but today's
    ``DecisionCaptureConfig()`` — and appends the LLM part only where
    structuring runs: ``decision_capture.enabled`` and
    ``llm_structuring.enabled``. The caller passes ``llm`` exactly then.

    Example: ``decision_capture_token(DecisionCaptureConfig(merge_jaccard=0.5))``
    returns ``'decisions:'`` followed by 16 lowercase hex characters.
    """
    digest = hashlib.md5(config.model_dump_json().encode(), usedforsecurity=False).hexdigest()
    token = f"decisions:{digest[:16]}"
    if llm is None:
        return token
    identity = f"{llm.provider}|{llm.model_name}|{llm.temperature}|{llm.max_tokens}"
    llm_digest = hashlib.md5(identity.encode(), usedforsecurity=False).hexdigest()
    return f"{token}|llm:{llm_digest[:16]}"


def decision_capture_folded(
    base: str, config: DecisionCaptureConfig, llm: LlmConfig | None = None
) -> str:
    """``base`` wrapped in :func:`decision_capture_token` for ``config`` (and
    ``llm``, when the structuring LLM's identity applies).

    The caller decides when the fold applies (a project target with non-stock
    settings) and when the LLM part rides along, so each pin states those
    conditions itself.

    Example: ``grammar_folded(decision_capture_folded(rule_folded(base), cfg))``
    is the pre-chunk-tree-salt digest of a project bundle tuned with ``cfg``.
    """
    return digest_fold(base, decision_capture_token(config, llm))


def dependency_decision_capture_folded(base: str, config: DecisionCaptureConfig) -> str:
    """``base`` wrapped in the decision token a DEPENDENCY carries (issue #346).

    Unlike :func:`decision_capture_folded`, this helper states the condition
    itself, because it is the whole point of the dependency fold: it applies
    only while ``decision_capture`` mines dependencies — ``enabled`` AND
    ``include_deps`` — and returns ``base`` unchanged otherwise. Re-derived here
    from those two switches rather than read from the stage's gate. Where it
    applies, the token is the plain one, the same digest a project gets. It
    never carries the ``|llm:`` part: dependencies are never structured.

    Example: ``grammar_folded(dependency_decision_capture_folded(base,
    DecisionCaptureConfig(include_deps=True)))`` is the pre-chunk-tree-salt
    digest of a dependency bundle whose inline markers are mined.
    """
    if not (config.enabled and config.include_deps):
        return base
    return digest_fold(base, decision_capture_token(config))


def member_extraction_folded(base: str, token: str) -> str:
    """``base`` wrapped in the member-extraction salt ``members:<token>``.

    ``token`` is the string the composition root derives from ``--no-inspect``,
    ``--depth`` and ``extraction.members.*`` (issue #347) — each pin spells it
    out as a literal, so a stage that re-derived or re-framed it would not
    match. The caller decides when the fold applies (a DEPENDENCY target whose
    token is non-empty and differs from the stock
    ``inspect|depth=1|cap=120|sig=200|doc=1024``), so each pin states that
    condition itself.

    Example: ``grammar_folded(member_extraction_folded(base, "static"))`` is the
    pre-chunk-tree-salt digest of a dependency indexed in static mode.
    """
    return digest_fold(base, f"members:{token}")


def reference_capture_token(config: ReferenceCaptureConfig) -> str:
    """The reference-capture token for ``config`` (issue #347):
    ``refs:disabled`` when capture is off, whatever the kinds; otherwise
    ``refs:`` + the DISTINCT kinds, sorted, comma-joined.

    Normalized because capture itself reads ``frozenset(kinds)``: order and
    duplicates change no edge, so they must change no hash either. Re-derived
    here rather than read from the stage. The stage folds it on EVERY target
    kind, and only when it differs from the stock token
    ``refs:calls,imports,inherits``.

    Example: ``reference_capture_token(ReferenceCaptureConfig(kinds=("mentions",
    "calls", "calls")))`` returns ``'refs:calls,mentions'``.
    """
    if not config.enabled:
        return "refs:disabled"
    return "refs:" + ",".join(sorted(set(config.kinds)))


def reference_capture_folded(base: str, config: ReferenceCaptureConfig) -> str:
    """``base`` wrapped in :func:`reference_capture_token` for ``config``.

    The caller decides when the fold applies (non-stock capture settings, any
    target kind), so each pin states that condition itself.

    Example: ``grammar_folded(reference_capture_folded(rule_folded(base), cfg))``
    is the pre-chunk-tree-salt digest of a project bundle captured with ``cfg``
    under stock decision settings.
    """
    return digest_fold(base, reference_capture_token(config))


def grammar_folded(base: str) -> str:
    """``base`` wrapped in the UNCONDITIONAL loadable-grammar salt, under the
    calling process's CURRENT grammar state.

    Example: ``grammar_folded(rule_folded(raw_hash_files([str(f)])))`` is the
    stage's hash for a project bundle with no user excludes, before the
    identity salt; ``grammar_folded(raw_hash_files([str(f)]))`` is the same
    for a dependency bundle, which never carries the rule token.
    """
    return digest_fold(base, f"grammars:{loadable_grammar_fingerprint()}")


def chunk_tree_folded(base: str, chunking: ChunkingConfig | None = None) -> str:
    """``base`` wrapped in the UNCONDITIONAL chunk-tree salt, under the calling
    process's CURRENT chunker rules (issue #246 close-out).

    ``chunking`` defaults to a stock ``ChunkingConfig()``, which is what both a
    bare ``ContentHashStage()`` and a default ``AppConfig.load()`` produce — pass
    one only when the suite under test varies a chunker tunable.

    Example: ``chunk_tree_folded(grammar_folded(rule_folded(base)))`` is the
    stage's hash for a project bundle with no user excludes, before the identity
    salt.
    """
    return digest_fold(base, f"chunks:{chunk_tree_fingerprint(chunking or ChunkingConfig())}")


def pipeline_folded(base: str, pipeline_hash: str, tier: str = "full") -> str:
    """``base`` wrapped in the identity salt — the OUTERMOST fold.

    Carries the same two components the CHUNK hashes fold: the pipeline hash
    and the package's embed tier (``full`` for any project target).
    Applied only when the stage was given a pipeline hash, which is always
    true through a real composition root and never true for a bare
    ``ContentHashStage()`` in a stage-isolation test. A suite that indexes
    through ``build_project_indexer`` must therefore wrap its expectation in
    this, using ``config.compute_ingestion_pipeline_hash()`` for the same
    config the run used.
    """
    return digest_fold(base, f"pipeline:{pipeline_hash}|tier:{tier}")


def package_hash_oracle(
    paths: list[str],
    pipeline_hash: str,
    tier: str = "full",
    *,
    project: bool = True,
) -> str:
    """The full no-user-excludes package hash of a STOCK deployment, in order.

    For a PROJECT target: base → rule token → grammar salt → chunk-tree salt →
    identity salt. Pass ``project=False`` for a dependency bundle, which never
    carries the project-only rule token (member-module-ids spec §4). A stock
    ``decision_capture``, a stock inspect-mode member extraction and a stock
    ``reference_graph.capture`` fold nothing, so this oracle has none of those
    folds; a suite that tunes one — or indexes a dependency in static mode, or
    mines dependencies — composes :func:`decision_capture_folded`,
    :func:`dependency_decision_capture_folded`, :func:`member_extraction_folded`
    or :func:`reference_capture_folded` itself.
    """
    base = raw_hash_files(paths)
    if project:
        base = rule_folded(base)
    return pipeline_folded(chunk_tree_folded(grammar_folded(base)), pipeline_hash, tier)


__all__ = (
    "chunk_tree_folded",
    "decision_capture_folded",
    "decision_capture_token",
    "dependency_decision_capture_folded",
    "digest_fold",
    "grammar_folded",
    "member_extraction_folded",
    "package_hash_oracle",
    "pipeline_folded",
    "raw_hash_files",
    "reference_capture_folded",
    "reference_capture_token",
    "rule_folded",
)
