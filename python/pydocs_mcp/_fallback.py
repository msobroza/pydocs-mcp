"""
Pure Python fallbacks for Rust functions.
Used when the Rust extension is not installed (pip install without Rust toolchain).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.constants import (
    DOCSTRING_LOOKAHEAD,
    FUNC_DOCSTRING_MAX,
    MODULE_DOCSTRING_MAX,
)

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".tox",
    ".eggs",
    "build",
    "dist",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "htmlcov",
    ".nox",
    # Fallback-contract parity with src/lib.rs's SKIP_DIRS (see
    # tests/test_skip_dirs_rust_sync.py) — a bare "egg-info" directory
    # (distinct from "<pkg>.egg-info", handled separately by
    # extraction/config.py's path_under_excluded) must be pruned by both
    # engines identically.
    "egg-info",
}


def walk_py_files(root: str) -> list[str]:
    """Find all .py files, skipping excluded directories."""
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        # os.walk yields string dirpath/filenames; build with str(Path(...) / ...)
        # so the output matches the Rust counterpart byte-for-byte while staying
        # PTH118-clean.
        result.extend(str(Path(dirpath) / f) for f in filenames if f.endswith(".py"))
    result.sort()
    return result


def hash_files(paths: list[str]) -> str:
    """Hash file paths + mtimes to detect changes."""
    # md5 used as a non-cryptographic content fingerprint for cache invalidation;
    # usedforsecurity=False signals intent to ruff/bandit.
    h = hashlib.md5(usedforsecurity=False)
    for p in paths:
        # Separators mirror src/lib.rs's hash_files (':' after the path, '\n'
        # after each entry) so distinct path lists can't collide by having
        # their concatenated path+mtime bytes read as the same byte stream
        # (e.g. ["a", "1234"] vs ["a1234"] for two missing paths).
        h.update(p.encode())
        h.update(b":")
        with contextlib.suppress(OSError):
            h.update(str(Path(p).stat().st_mtime_ns).encode())
        h.update(b"\n")
    return h.hexdigest()[:16]


# plain @dataclass (not frozen+slots) to mirror Rust #[pyclass] ParsedMember,
# which exposes read-only getters but isn't truly frozen on the Python side.
@dataclass
class ParsedMember:
    name: str
    kind: str
    signature: str
    docstring: str


# Bound the paren-balance scan (_scan_matching_paren) so a pathological
# unclosed '(' can't walk the rest of a huge source file char-by-char.
_PAREN_SCAN_LIMIT = 4000


def _scan_matching_paren(source: str, open_idx: int) -> int | None:
    """Return the index just past the ``)`` matching the ``(`` at ``open_idx``.

    Plain depth counting (not a `[^)]*` regex charclass) so a nested ``)``
    inside a default value (tuple literal, call, dict) doesn't terminate the
    scan early — see ``def resize(size=(640, 480)):`` in vision/ML libs,
    which the old `\\(([^)]*)\\)` pattern could never match at all because
    the inner ``)`` closed the regex's paren group before the real one.
    Returns None if unclosed within `_PAREN_SCAN_LIMIT` chars (caller treats
    the definition as unparseable, same as a regex non-match today).
    """
    depth = 0
    end = min(len(source), open_idx + _PAREN_SCAN_LIMIT)
    for i in range(open_idx, end):
        ch = source[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def parse_py_file(source: str) -> list[ParsedMember]:
    """Extract top-level functions and classes using regex + a paren-balance scan."""
    # Paren group is optional: paren-less `class Config:` is the idiomatic,
    # most common class form in modern Python (no base classes). `def`/`async def`
    # always carry parens in valid syntax, so making the group optional here
    # cannot introduce false positives for functions.
    #
    # The '(' is matched but NOT captured with `[^)]*)` here — that charclass
    # can't span a nested ')' inside a tuple/call default, so the whole
    # definition would fail to match. Instead we only anchor on the opening
    # paren and hand off to _scan_matching_paren for depth-aware balancing.
    header_re = re.compile(
        r"^(async\s+def|def|class)\s+([A-Za-z_]\w*)\s*(\()?",
        re.MULTILINE,
    )
    tail_re = re.compile(r"\s*(?:->[\s\w\[\],.|\"']*)?:")
    doc_re = re.compile(r'(?s)^(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')')

    members = []
    for m in header_re.finditer(source):
        kind, name, has_paren = m.group(1), m.group(2), m.group(3)
        if name.startswith("_"):
            continue

        if has_paren:
            close_idx = _scan_matching_paren(source, m.end() - 1)
            if close_idx is None:
                continue  # unclosed paren within scan bound — skip, don't misparse.
            sig = source[m.end() : close_idx - 1]
            after_paren = close_idx
        else:
            sig = None
            after_paren = m.end()

        # `-> ...:` (or paren-less `:`) must immediately follow, else this
        # wasn't actually a def/class header (e.g. a call that happens to
        # start with "def"-like text is already excluded by \b via \s+).
        tail_match = tail_re.match(source, after_paren)
        if tail_match is None:
            continue
        end = tail_match.end()

        # Look for docstring immediately after the definition (colon consumed by regex).
        rest = source[end:][:DOCSTRING_LOOKAHEAD].lstrip()
        docstring = ""
        doc_match = doc_re.match(rest)
        if doc_match:
            docstring = (doc_match.group(1) or doc_match.group(2) or "").strip()[
                :FUNC_DOCSTRING_MAX
            ]

        # sig is None for paren-less classes (has_paren is falsy; see above).
        members.append(ParsedMember(name, kind, f"({sig.strip() if sig else ''})", docstring))
    return members


def extract_module_doc(source: str) -> str:
    """Extract module-level docstring."""
    doc_re = re.compile(r'(?s)^(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')')
    m = doc_re.match(source.lstrip())
    if m:
        return (m.group(1) or m.group(2) or "").strip()[:MODULE_DOCSTRING_MAX]
    return ""


def read_file(path: str) -> str:
    """Read a file, return empty string on error.

    Uses errors="replace" (not "ignore") so invalid UTF-8 bytes become
    U+FFFD instead of being silently dropped — this must match the Rust
    reader's String::from_utf8_lossy semantics byte-for-byte (see
    CLAUDE.md "Fallback contract"; src/lib.rs read_file/read_files_parallel).
    """
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def read_files_parallel(paths: list[str]) -> list[tuple[str, str]]:
    """Read files (no parallelism in pure Python fallback)."""
    return [(p, read_file(p)) for p in paths]
