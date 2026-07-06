"""Import-surface pin for pydocs_mcp.constants after the dead-code sweep.

The 15 constants in _DEAD had ZERO usages in python/, tests/, benchmarks/
and src/ (verified by word-boundary grep) — they described truncation
behavior of pre-refactor indexing/formatting code paths that no longer
exist. Pinning both lists means re-introducing a constant (or dropping a
live one) fails a test instead of drifting silently.
"""

from __future__ import annotations

import pydocs_mcp.constants as constants

# The nine survivors: three Rust-mirrored indexing limits (_fallback.py),
# four MCP display limits (application/formatting.py, module_inspector.py),
# two collection caps (application/formatting.py).
_LIVE = (
    "DOCSTRING_LOOKAHEAD",
    "FUNC_DOCSTRING_MAX",
    "MODULE_DOCSTRING_MAX",
    "PACKAGE_DOC_MAX",
    "PACKAGE_DOC_LINE_MAX",
    "LIVE_SIGNATURE_MAX",
    "LIVE_DOC_MAX",
    "REQUIREMENTS_DISPLAY",
    "LIST_PACKAGES_MAX",
)

_DEAD = (
    "SIGNATURE_MAX",
    "RETURN_TYPE_MAX",
    "PARAMS_JSON_MAX",
    "PARAM_DEFAULT_MAX",
    "CLASS_DOCSTRING_MAX",
    "CLASS_FULL_DOC_MAX",
    "METHOD_SUMMARY_MAX",
    "SEARCH_BODY_DISPLAY",
    "SEARCH_DOC_DISPLAY",
    "SEARCH_BODY_CLI",
    "SEARCH_DOC_CLI",
    "SEARCH_RESULTS_MAX",
    "CONTEXT_TOKEN_BUDGET",
    "REQUIREMENTS_PARSE_MAX",
    "CLASS_METHODS_MAX",
)


def test_live_constants_survive_the_sweep() -> None:
    for name in _LIVE:
        assert isinstance(getattr(constants, name), int), name


def test_dead_constants_removed() -> None:
    for name in _DEAD:
        assert not hasattr(constants, name), f"{name} should have been deleted"
