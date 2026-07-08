"""Pin the src/lib.rs <-> constants.py truncation-limit sync contract.

constants.py documents that DOCSTRING_LOOKAHEAD / FUNC_DOCSTRING_MAX /
MODULE_DOCSTRING_MAX "are mirrored in src/lib.rs and MUST be kept in sync
when changed" — but nothing enforced that claim. test_parity.py only
compares *behavior* and is skipped whenever the native extension isn't
compiled (the common case for contributors without a Rust toolchain and
for this repo's CI lane that runs the Python suite without maturin).

This test regex-extracts the `const NAME: usize = N;` declarations
straight out of src/lib.rs and diffs them against the Python constants,
so a value drifting out of lockstep fails in plain `pytest -q` with no
Rust build required.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydocs_mcp import constants

_LIB_RS = Path(__file__).parent.parent / "src" / "lib.rs"

# Constants documented as "mirrored in src/lib.rs" in constants.py — the
# exact three names this contract covers (see constants.py's "SYNC:" note).
_RUST_MIRRORED_NAMES = (
    "DOCSTRING_LOOKAHEAD",
    "FUNC_DOCSTRING_MAX",
    "MODULE_DOCSTRING_MAX",
)


def _extract_rust_usize_consts(rust_src: str) -> dict[str, int]:
    """Parse `const NAME: usize = N;` declarations out of Rust source."""
    pattern = re.compile(r"const\s+([A-Z_]+)\s*:\s*usize\s*=\s*(\d+)\s*;")
    return {name: int(value) for name, value in pattern.findall(rust_src)}


def test_lib_rs_constants_match_python_constants() -> None:
    rust_consts = _extract_rust_usize_consts(_LIB_RS.read_text(encoding="utf-8"))

    for name in _RUST_MIRRORED_NAMES:
        assert name in rust_consts, f"{name} not found as a `const ...: usize` in src/lib.rs"
        rust_value = rust_consts[name]
        python_value = getattr(constants, name)
        assert rust_value == python_value, (
            f"{name} drifted out of lockstep: "
            f"src/lib.rs={rust_value} vs constants.py={python_value}. "
            "constants.py documents these three limits as mirrored in "
            "src/lib.rs and required to change in lockstep."
        )
