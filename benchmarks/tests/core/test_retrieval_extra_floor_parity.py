"""The eval extras' pydocs-mcp floor and the guard's constant stay equal.

``_REQUIRED_PYDOCS_MCP`` is what the version-skew hint tells users to install;
``benchmarks/pyproject.toml`` is what pip actually enforces. A bump that edits
one and not the other would print a floor pip does not honor, or enforce one
the hint never names.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

from pydocs_eval._retrieval_extra import _REQUIRED_PYDOCS_MCP

_EVAL_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _pydocs_mcp_requirement(extra: str) -> Requirement:
    """Return the ``pydocs-mcp`` requirement declared by one eval extra."""
    project = tomllib.loads(_EVAL_PYPROJECT.read_text(encoding="utf-8"))["project"]
    declared = project["optional-dependencies"][extra]
    return next(Requirement(r) for r in declared if Requirement(r).name == "pydocs-mcp")


@pytest.mark.parametrize("extra", ["retrieval", "ask", "all"])
def test_extra_floor_matches_guard_constant(extra: str) -> None:
    req = _pydocs_mcp_requirement(extra)
    assert req.specifier == SpecifierSet(f">={_REQUIRED_PYDOCS_MCP}"), (
        f"[{extra}] declares {req}, but the guard names pydocs-mcp>={_REQUIRED_PYDOCS_MCP}"
    )


def test_floor_is_the_first_release_with_the_harness_modules() -> None:
    # The optimize layer imports pydocs_mcp.harness.* and
    # application.description_source, first shipped in 0.6.0; a lower floor
    # lets pip keep an installed product those imports fail against.
    assert tuple(int(part) for part in _REQUIRED_PYDOCS_MCP.split(".")) >= (0, 6, 0)
