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
    hash for a bundle with no user excludes.
    """
    return digest_fold(base, f"grammars:{loadable_grammar_fingerprint()}")
