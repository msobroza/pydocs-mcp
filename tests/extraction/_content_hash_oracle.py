"""Independent oracle for ``ContentHashStage``'s package-hash framing.

Suites that pin an EXACT package content hash derive the expectation here,
from the documented formula (analyzers spec §8.2), instead of calling the
stage's private fold — so a regression in the stage cannot silently move
the expectation along with it. One copy, because four suites pin it.
"""

from __future__ import annotations

import hashlib

from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    loadable_grammar_fingerprint,
)


def raw_hash_files(paths: list[str]) -> str:
    """``hash_files`` output normalized exactly as the stage normalizes it
    (str passthrough / bytes → hex) — the base digest every fold wraps."""
    # Deferred, like the stage's own import, so disable_rust() swaps apply.
    from pydocs_mcp._fast import hash_files

    result = hash_files(paths)
    return result if isinstance(result, str) else result.hex()


def digest_fold(base: str, salt: str) -> str:
    """The digest-of-digest fold both salts use: ``md5(base NUL salt)[:16]``."""
    salted = f"{base}\x00{salt}".encode()
    return hashlib.md5(salted, usedforsecurity=False).hexdigest()[:16]


def grammar_folded(base: str) -> str:
    """``base`` wrapped in the UNCONDITIONAL loadable-grammar salt, under the
    calling process's CURRENT grammar state.

    Example: ``grammar_folded(raw_hash_files([str(f)]))`` is the stage's
    hash for a bundle with no user excludes, before the pipeline salt.
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


def package_hash_oracle(paths: list[str], pipeline_hash: str, tier: str = "full") -> str:
    """The full no-user-excludes package hash: base → grammar salt → identity salt."""
    return pipeline_folded(grammar_folded(raw_hash_files(paths)), pipeline_hash, tier)
