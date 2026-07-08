"""Regression test pinning CHAR-based (not byte-based) docstring truncation
as the canonical cross-engine contract.

Gap: src/lib.rs's `safe_truncate` was previously byte-based while the Python
fallback (_fallback.py) has always sliced with plain `[:N]`, which is
char-based (Python strings are code-point sequences). For a def whose
non-ASCII (CJK/emoji) docstring closes its triple quotes between byte-500
and char-500 of the post-def text, the fallback finds the docstring while a
byte-truncating Rust build stores "" — `module_members` content silently
diverges between native and pure-Python deployments of the same input,
violating the fallback substitution contract (CLAUDE.md "Fallback contract").

`src/lib.rs::safe_truncate` is now char-based (see its own `#[cfg(test)]`
regressions), so this file pins the FALLBACK side (the always-available,
canonical reference implementation per CLAUDE.md) and cross-checks the
native module when it happens to be compiled. It intentionally does NOT
reproduce the byte-based "both engines identically fail" framing that
`tests/test_parity.py` previously documented — that framing describes
pre-fix Rust behavior and is no longer true of the current source.
"""

import pytest

from pydocs_mcp._fallback import parse_py_file
from pydocs_mcp.constants import DOCSTRING_LOOKAHEAD

try:
    from pydocs_mcp import _native as rust

    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False


def test_fallback_finds_cjk_docstring_closing_between_byte_500_and_char_500():
    """190 leading chars of 3-byte CJK inside the docstring window: the
    closing triple-quotes land well within Python's 500-char lookahead
    (190 chars plus the surrounding quote/whitespace overhead is
    comfortably under 500) but past a 500-byte lookahead (190 chars times
    3 bytes per char is 570 bytes). This is the exact edge case from the
    gap record; pins char-based semantics as canonical on the fallback
    (always-available) side."""
    cjk_doc = "中" * 190
    assert len(cjk_doc) < DOCSTRING_LOOKAHEAD  # within char window
    assert len(cjk_doc.encode("utf-8")) > DOCSTRING_LOOKAHEAD  # beyond byte window

    src = f'def foo(x):\n    """{cjk_doc}"""\n    pass\n'
    members = parse_py_file(src)

    assert len(members) == 1
    assert members[0].docstring == cjk_doc


@pytest.mark.skipif(
    not RUST_AVAILABLE, reason="Rust native module not compiled in this environment"
)
def test_native_matches_fallback_for_cjk_docstring_straddling_lookahead():
    """When the native module IS compiled, it must return the identical
    docstring as the fallback for the same straddling-window input — a
    build-dependent difference here means module_members content (and thus
    chunks.content_hash) silently diverges by which wheel is installed."""
    cjk_doc = "中" * 190
    src = f'def foo(x):\n    """{cjk_doc}"""\n    pass\n'

    rust_members = rust.parse_py_file(src)
    py_members = parse_py_file(src)

    rust_doc = rust_members[0].docstring if rust_members else ""
    py_doc = py_members[0].docstring

    assert rust_doc == py_doc == cjk_doc
