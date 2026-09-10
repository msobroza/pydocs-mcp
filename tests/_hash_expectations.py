"""Independent re-computations of the package-hash framing, for test pins.

WHY a separate helper: tests pin ``ContentHashStage`` output against values
computed HERE, from the documented framing, not by calling the stage's
private ``_fold_digest``. A pin that imported the helper under test would
agree with any change to it and prove nothing.

Framing (``stages/content_hash.py``): ``hash_files(paths)`` normalised to a
str, then an optional md5 digest-of-digest fold of the exclusion
fingerprint, then (project targets only) the same fold of
``MODULE_ID_RULE_VERSION``.
"""

from __future__ import annotations

import hashlib

from pydocs_mcp.extraction.strategies.python_module_id import MODULE_ID_RULE_VERSION


def raw_hash_files(paths: list[str]) -> str:
    """The unfolded framing: ``hash_files`` output as the stage normalises it.

    This is the value every ``packages.content_hash`` was written with before
    any fold existed (str passthrough, bytes → hex).

    Example: ``raw_hash_files([str(tmp_path / "a.py")])``.
    """
    from pydocs_mcp._fast import hash_files

    result = hash_files(paths)
    return result if isinstance(result, str) else result.hex()


def digest_folded(base: str, token: str) -> str:
    """``md5(f"{base}\\x00{token}")[:16]``, the digest-of-digest fold.

    Example: ``digest_folded("abc", "fp")`` is 16 lowercase hex characters.
    """
    folded = hashlib.md5(f"{base}\x00{token}".encode(), usedforsecurity=False)
    return folded.hexdigest()[:16]


def rule_folded(base: str) -> str:
    """``base`` with ``MODULE_ID_RULE_VERSION`` folded in (project targets).

    Example: ``rule_folded(raw_hash_files(paths))`` is a project hash with no
    user excludes.
    """
    return digest_folded(base, MODULE_ID_RULE_VERSION)


__all__ = ("digest_folded", "raw_hash_files", "rule_folded")
