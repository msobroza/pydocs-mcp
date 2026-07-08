"""Regression tests for multibyte-character docstring truncation.

The Rust engine truncates by BYTES (`safe_truncate` in src/lib.rs applied to
DOCSTRING_LOOKAHEAD / FUNC_DOCSTRING_MAX / MODULE_DOCSTRING_MAX), while the
Python fallback slices by CHARACTERS (_fallback.py). For multi-byte content
(CJK, emoji, accented text) this makes the two engines diverge:

  - a docstring within Python's 500-*char* lookahead window can still fall
    outside Rust's 500-*byte* window, so Rust fails to find the docstring at
    all (returns "") while Python extracts it in full
  - MODULE_DOCSTRING_MAX truncates to 5000 chars in Python vs ~5000 bytes
    (far fewer chars) in Rust

This divergence means `chunks.content_hash` (which hashes the extracted
docstring text) differs by *which engine indexed the package*, causing
spurious re-embedding churn on an engine switch, and silently shorter
(or entirely missing) docs for CJK-documented packages under Rust.
"""

import pytest

from pydocs_mcp._fallback import extract_module_doc, parse_py_file
from pydocs_mcp.constants import DOCSTRING_LOOKAHEAD, MODULE_DOCSTRING_MAX

try:
    from pydocs_mcp import _native as rust

    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False


# ── Fallback (Python reference implementation) — char-based semantics ─────


class TestFallbackMultibyteTruncation:
    def test_module_docstring_truncates_by_char_count_not_byte_count(self):
        """5000+ CJK chars: Python's char-based slice keeps exactly
        MODULE_DOCSTRING_MAX *characters*, not bytes — each retained char is
        3 bytes in UTF-8, so a byte-based truncation (Rust's current
        behavior) would keep far fewer than 5000 characters here."""
        cjk_doc = "中" * 5500
        src = f'"""{cjk_doc}"""\n'
        doc = extract_module_doc(src)
        assert len(doc) == MODULE_DOCSTRING_MAX
        assert doc == cjk_doc[:MODULE_DOCSTRING_MAX]
        # Sanity: this is a genuinely multi-byte-heavy payload, not ASCII.
        assert len(doc.encode("utf-8")) == MODULE_DOCSTRING_MAX * 3

    def test_module_docstring_under_max_not_truncated_when_multibyte(self):
        cjk_doc = "日本語のドキュメント文字列です。" * 50  # well under 5000 chars
        assert len(cjk_doc) < MODULE_DOCSTRING_MAX
        src = f'"""{cjk_doc}"""\n'
        doc = extract_module_doc(src)
        assert doc == cjk_doc

    def test_func_docstring_within_char_lookahead_found_when_multibyte(self):
        """A CJK docstring whose closing quotes land within
        DOCSTRING_LOOKAHEAD *characters* of the def, but well beyond
        DOCSTRING_LOOKAHEAD *bytes* (each CJK char is 3 bytes) — this is
        exactly the byte-vs-char lookahead-window boundary the Rust engine
        gets wrong (src/lib.rs safe_truncate operates on bytes)."""
        # 200 chars => 600 bytes in UTF-8: over the 500-byte window Rust
        # uses, comfortably under the 500-char window Python uses.
        cjk_doc = "あ" * 200
        assert len(cjk_doc) < DOCSTRING_LOOKAHEAD
        assert len(cjk_doc.encode("utf-8")) > DOCSTRING_LOOKAHEAD
        src = f'def foo(x):\n    """{cjk_doc}"""\n    pass\n'
        syms = parse_py_file(src)
        assert syms[0].docstring == cjk_doc


# ── Parity: Rust native module vs Python fallback ──────────────────────────
#
# Skipped when the compiled native module isn't available in this
# environment (it is not rebuilt here — see project test-running rules).
# When it IS available, these tests pin/document the byte-vs-char
# divergence rather than asserting silent equality, so a genuine fix on the
# Rust side (making safe_truncate operate on char counts, matching Python)
# is what turns them green.

pytestmark_native = pytest.mark.skipif(
    not RUST_AVAILABLE, reason="Rust native module not compiled in this environment"
)


class TestModuleDocstringMultibyteParity:
    @pytestmark_native
    def test_5000_plus_cjk_chars_matches_python(self):
        cjk_doc = "中" * 5500
        src = f'"""{cjk_doc}"""\n'
        rust_doc = rust.extract_module_doc(src)
        py_doc = extract_module_doc(src)
        assert rust_doc == py_doc, (
            f"Rust truncates module docstrings by BYTES not chars: "
            f"rust={len(rust_doc)} chars, py={len(py_doc)} chars "
            "(src/lib.rs safe_truncate must operate on char boundaries "
            "to match the Python fallback)"
        )


class TestFuncDocstringMultibyteLookaheadParity:
    @pytestmark_native
    def test_doc_closing_within_char_window_but_past_byte_window(self):
        """Case (c) from the gap record: docstring closes within 500 chars
        of the def but beyond 500 bytes. Python's char-based lookahead finds
        it; Rust's byte-based lookahead truncates the search window before
        reaching the closing triple-quote, so DOC_RE never matches and Rust
        returns "" for a docstring Python extracts in full."""
        cjk_doc = "あ" * 200  # 200 chars, 600 bytes
        src = f'def foo(x):\n    """{cjk_doc}"""\n    pass\n'
        rust_syms = rust.parse_py_file(src)
        py_syms = parse_py_file(src)
        rust_doc = rust_syms[0].docstring if rust_syms else ""
        py_doc = py_syms[0].docstring
        assert rust_doc == py_doc, (
            f"Rust lookahead window is byte-based (500 bytes) vs Python's "
            f"char-based (500 chars): rust={rust_doc!r}, py={py_doc!r} "
            "(src/lib.rs DOCSTRING_LOOKAHEAD truncation must operate on "
            "char boundaries to match the Python fallback)"
        )
