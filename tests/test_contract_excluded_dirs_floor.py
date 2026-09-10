"""docs/tool-contracts.md §4.1 names exactly the excluded-directory floor the code enforces.

The contract is the frozen, user-facing statement of the corpus scope. 0.6.0 grew
``_EXCLUDED_DIRS`` from 21 to 26 names while §4.1 kept listing 21, so this pins the
documented count and names to the code: a floor change now fails here until the
contract (an owner-ratified amendment) is updated with it.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS

_CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "tool-contracts.md"
_FLOOR_SENTENCE = re.compile(
    r"floor of (?P<count>\d+) excluded directory names\*\*: (?P<names>.*?)\n\s*\(`_EXCLUDED_DIRS`",
    re.S,
)


def _documented_floor() -> tuple[int, frozenset[str]]:
    """Return the count and the backticked names stated in §4.1 item 1."""
    match = _FLOOR_SENTENCE.search(_CONTRACT.read_text(encoding="utf-8"))
    assert match, f"§4.1 floor sentence not found in {_CONTRACT}"
    return int(match.group("count")), frozenset(re.findall(r"`([^`]+)`", match.group("names")))


def test_contract_floor_names_match_the_code() -> None:
    _, names = _documented_floor()
    assert names == _EXCLUDED_DIRS, (
        f"missing from §4.1: {sorted(_EXCLUDED_DIRS - names)}; "
        f"listed in §4.1 but not in code: {sorted(names - _EXCLUDED_DIRS)}"
    )


def test_contract_floor_count_matches_its_names() -> None:
    count, names = _documented_floor()
    assert count == len(names) == len(_EXCLUDED_DIRS), (
        f"§4.1 says {count} names, lists {len(names)}, code has {len(_EXCLUDED_DIRS)}"
    )
