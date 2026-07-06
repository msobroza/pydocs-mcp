"""split_symbol_qname — the ONE site encoding the uppercase-segment heuristic.

Pins the unified semantics so a chunker qname-scheme change (nested
classes, PEP 695) is a one-site edit, per the lockstep TODO on the helper.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.extraction.strategies.reference_resolver import (
    _enclosing_class_qname,
    _module_part_of,
    split_symbol_qname,
)


@pytest.mark.parametrize(
    ("node_id", "expected"),
    [
        # Method under a class: strip two → module, strip one → class.
        ("pkg.mod.Cls.method", ("pkg.mod", "pkg.mod.Cls")),
        # Free function: second-to-last is a lowercase module segment.
        ("pkg.mod.fn", ("pkg.mod", None)),
        # Module-level capture: whole id is the module qname; split still
        # strips one — the resolver tolerates this as a silent alias miss.
        ("pkg.mod", ("pkg", None)),
        # 2-part id with uppercase first segment: the strict >=3 guard means
        # NO class — the deliberate delta vs the old _module_part_of, which
        # stripped two segments and returned "" (never matching the alias
        # table anyway).
        ("HTTP.client", ("HTTP", None)),
        # Single segment: nothing to strip.
        ("solo", ("", None)),
    ],
)
def test_split_symbol_qname(node_id: str, expected: tuple[str, str | None]) -> None:
    assert split_symbol_qname(node_id) == expected


def test_wrappers_delegate_to_the_single_split() -> None:
    assert _enclosing_class_qname("pkg.mod.Cls.method") == "pkg.mod.Cls"
    assert _enclosing_class_qname("pkg.mod.fn") is None
    assert _module_part_of("pkg.mod.Cls.method") == "pkg.mod"
    assert _module_part_of("pkg.mod.fn") == "pkg.mod"
