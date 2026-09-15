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

from pydocs_mcp.harness.ask_your_docs.question_scope import (
    CODE_LABELS,
    CODE_SERVER_VALUES,
    QuestionScope,
    ScopeKind,
)

# Which corpus filters each tool actually accepts (see pydocs_mcp.server):
# ``project`` — all six tools; ``package`` — search_codebase + get_overview;
# ``scope`` (own vs deps) — search_codebase only. The interceptor forces a pin
# only where the tool can honor it.
_PACKAGE_TOOLS = frozenset({"search_codebase", "get_overview"})
# The ``code`` value that narrows nothing. ScopeCode.ALL has no server spelling — a
# search with no ``scope`` argument already covers everything — so the page spells the
# absence itself, and every reader of a scope mapping compares against this one name.
NO_CODE_PIN = "all"
# The words for a non-"all" ``code`` pin, keyed by the SERVER spelling. On screen (the
# activity panel's scope line), so they are the picker's Code labels, never the frozen
# words of the model-facing note — that one reads MODEL_NOTE_CODE_WORDS (UI spec §6.7).
CODE_SCOPE_WORDS = {
    server_value: CODE_LABELS[code] for code, server_value in CODE_SERVER_VALUES.items()
}


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
    if tool_name == "search_codebase" and scope.get("code", NO_CODE_PIN) != NO_CODE_PIN:
        args["scope"] = scope["code"]
    return args


def activity_scope_words(scope: QuestionScope | None) -> dict[str, str]:
    """A hard pin as the ``{project, package, code}`` words the activity panel reads.

    Only a PIN pins; the DEFAULT scope fills what the model omitted, which is not
    "pinned by you" — it yields ``{}``, the panel's no-pin shape. A pin over several
    projects has no single project word: the footer (from the interceptor's own
    observations) names every cell, this preview shows the package / code pin only.
    """
    if scope is None or scope.kind is ScopeKind.DEFAULT:
        return {}
    projects = scope.projects()
    return {
        "project": projects[0] if len(projects) == 1 else "",
        "package": scope.package,
        "code": CODE_SERVER_VALUES.get(scope.code, NO_CODE_PIN),
    }
