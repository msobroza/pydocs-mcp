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
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".tox", ".eggs", "build", "dist", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "htmlcov", ".nox",
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
        h.update(p.encode())
        with contextlib.suppress(OSError):
            h.update(str(Path(p).stat().st_mtime_ns).encode())
    return h.hexdigest()[:16]


# plain @dataclass (not frozen+slots) to mirror Rust #[pyclass] ParsedMember,
# which exposes read-only getters but isn't truly frozen on the Python side.
@dataclass
class ParsedMember:
    name: str
    kind: str
    signature: str
    docstring: str


def parse_py_file(source: str) -> list[ParsedMember]:
    """Extract top-level functions and classes using regex."""
    def_re = re.compile(
        r'^(async\s+def|def|class)\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(?:->[\s\w\[\],.|]*)?:',
        re.MULTILINE,
    )
    doc_re = re.compile(r'(?s)^(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')')

    members = []
    for m in def_re.finditer(source):
        kind, name, sig = m.group(1), m.group(2), m.group(3)
        if name.startswith("_"):
            continue

        # Look for docstring immediately after the definition (colon consumed by regex).
        rest = source[m.end():][:DOCSTRING_LOOKAHEAD].lstrip()
        docstring = ""
        doc_match = doc_re.match(rest)
        if doc_match:
            docstring = (doc_match.group(1) or doc_match.group(2) or "").strip()[:FUNC_DOCSTRING_MAX]

        members.append(ParsedMember(name, kind, f"({sig.strip()})", docstring))
    return members


def extract_module_doc(source: str) -> str:
    """Extract module-level docstring."""
    doc_re = re.compile(r'(?s)^(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')')
    m = doc_re.match(source.lstrip())
    if m:
        return (m.group(1) or m.group(2) or "").strip()[:MODULE_DOCSTRING_MAX]
    return ""


def read_file(path: str) -> str:
    """Read a file, return empty string on error."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def read_files_parallel(paths: list[str]) -> list[tuple[str, str]]:
    """Read files (no parallelism in pure Python fallback)."""
    return [(p, read_file(p)) for p in paths]
