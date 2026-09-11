"""Independent oracle for ``ContentHashStage``'s package-hash framing.

Suites that pin an EXACT package content hash derive the expectation here,
from the documented formula, instead of calling the stage's private fold — so
a regression in the stage cannot silently move the expectation along with it.
One copy, because several suites pin it.

Framing (``stages/content_hash.py``), innermost first: ``hash_files(paths)``
normalized to a str, then the conditional exclusion fold, then the
project-only ``MODULE_ID_RULE_VERSION`` fold, then the unconditional
loadable-grammar salt, then the identity salt (pipeline hash + embed tier),
which a stage built without a pipeline hash omits. Each fold is exposed
separately rather than as one composed helper so every pin spells the ORDER
it depends on out loud.
"""

from __future__ import annotations

import hashlib

from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    loadable_grammar_fingerprint,
)
from pydocs_mcp.extraction.strategies.python_module_id import MODULE_ID_RULE_VERSION


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


def grammar_folded(base: str) -> str:
    """``base`` wrapped in the UNCONDITIONAL loadable-grammar salt, under the
    calling process's CURRENT grammar state.

    Example: ``grammar_folded(rule_folded(raw_hash_files([str(f)])))`` is the
    stage's hash for a project bundle with no user excludes, before the
    identity salt; ``grammar_folded(raw_hash_files([str(f)]))`` is the same
    for a dependency bundle, which never carries the rule token.
    """
    return digest_fold(base, f"grammars:{loadable_grammar_fingerprint()}")


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
    """The full no-user-excludes package hash, all four folds in order.

    For a PROJECT target: base → rule token → grammar salt → identity salt.
    Pass ``project=False`` for a dependency bundle, which never carries the
    project-only rule token (member-module-ids spec §4).
    """
    base = raw_hash_files(paths)
    if project:
        base = rule_folded(base)
    return pipeline_folded(grammar_folded(base), pipeline_hash, tier)


__all__ = (
    "digest_fold",
    "grammar_folded",
    "package_hash_oracle",
    "pipeline_folded",
    "raw_hash_files",
    "rule_folded",
)
