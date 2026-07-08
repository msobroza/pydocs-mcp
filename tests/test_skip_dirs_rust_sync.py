"""Pin the src/lib.rs <-> _fallback.py SKIP_DIRS sync contract.

walk_py_files is a fallback-contract function (CLAUDE.md's "Fallback
contract": same inputs must produce same outputs on both engines). Rust's
SKIP_DIRS (src/lib.rs) and the Python fallback's SKIP_DIRS
(_fallback.py) are two independently maintained directory-exclusion
lists that must stay identical or the two engines silently disagree on
which files get indexed.

test_parity.py's TestWalkPyFilesParity only exercises a couple of names
(.venv, __pycache__, ...) and is skipped whenever the native extension
isn't compiled (the common case for contributors without a Rust
toolchain, and for CI lanes that run the Python suite only) — so a name
present in one list but not the other (e.g. "egg-info", which src/lib.rs
has and _fallback.py lacked) can drift for a long time without any red
test. This module regex-extracts the Rust SKIP_DIRS array straight out
of src/lib.rs and diffs it against _fallback.SKIP_DIRS so the tripwire
runs unconditionally under plain `pytest -q`, plus exercises the concrete
filesystem behavior for the "egg-info" name specifically.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydocs_mcp._fallback import SKIP_DIRS, walk_py_files

_LIB_RS = Path(__file__).parent.parent / "src" / "lib.rs"


def _extract_rust_skip_dirs(rust_src: str) -> set[str]:
    """Parse the `const SKIP_DIRS: &[&str] = &[...];` array out of Rust source."""
    match = re.search(r"const\s+SKIP_DIRS\s*:\s*&\[&str\]\s*=\s*&\[(.*?)\];", rust_src, re.DOTALL)
    assert match is not None, "SKIP_DIRS array not found in src/lib.rs"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_fallback_skip_dirs_matches_rust_skip_dirs() -> None:
    """Source-level tripwire: runs even where Rust isn't compiled.

    Fails today (pre-fix) because _fallback.SKIP_DIRS is missing
    "egg-info", which src/lib.rs's SKIP_DIRS carries.
    """
    rust_skip_dirs = _extract_rust_skip_dirs(_LIB_RS.read_text(encoding="utf-8"))

    assert rust_skip_dirs == SKIP_DIRS, (
        f"_fallback.SKIP_DIRS drifted out of lockstep with src/lib.rs's SKIP_DIRS: "
        f"only in _fallback.py={SKIP_DIRS - rust_skip_dirs}, "
        f"only in src/lib.rs={rust_skip_dirs - SKIP_DIRS}. "
        "walk_py_files is a fallback-contract function (CLAUDE.md) — both "
        "engines must exclude the same directory names."
    )


def test_walk_py_files_skips_egg_info(tmp_path: Path) -> None:
    """Concrete edge case: a directory literally named 'egg-info' (not
    '<pkg>.egg-info') containing .py files must be pruned by the fallback
    walker exactly as src/lib.rs's walk_py_files prunes it.
    """
    egg_info = tmp_path / "egg-info"
    egg_info.mkdir()
    (egg_info / "hidden.py").touch()
    (tmp_path / "visible.py").touch()

    result = walk_py_files(str(tmp_path))

    assert len(result) == 1
    assert all("egg-info" not in Path(f).parts for f in result)
