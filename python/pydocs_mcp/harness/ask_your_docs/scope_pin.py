"""The corpus pin forced onto every MCP tool call — a pure, langchain-free function.

Pulled out of ``agent._intercept`` so the arguments a tool call ACTUALLY carries (the
model's proposal plus the page's pin) can be computed without an interceptor — the
activity trace shows both.

Example:
    >>> pinned_args("search_codebase", {"query": "q"}, {"project": "backend", "code": "deps"})
    {'query': 'q', 'project': 'backend', 'scope': 'deps'}
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Which corpus filters each tool actually accepts (see pydocs_mcp.server):
# ``project`` — all six tools; ``package`` — search_codebase + get_overview;
# ``scope`` (own vs deps) — search_codebase only. The interceptor forces a pin
# only where the tool can honor it.
_PACKAGE_TOOLS = frozenset({"search_codebase", "get_overview"})


def pinned_args(
    tool_name: str, proposed: Mapping[str, Any], scope: Mapping[str, str]
) -> dict[str, Any]:
    """``proposed`` with the pin forced on; the pin wins over what the model chose.

    ``scope`` keys: ``project``, ``package`` and ``code`` (``"all"`` | ``"project"`` |
    ``"deps"`` — sent as search_codebase's ``scope`` argument). Never mutates ``proposed``.
    """
    args = dict(proposed)
    if scope.get("project"):
        args["project"] = scope["project"]
    if tool_name in _PACKAGE_TOOLS and scope.get("package"):
        args["package"] = scope["package"]
    if tool_name == "search_codebase" and scope.get("code", "all") != "all":
        args["scope"] = scope["code"]
    return args
