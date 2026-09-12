"""What the server advertises for scope arguments (UI spec §6.12).

Read once per agent build from each loaded tool's ``args_schema`` — the
adapter sets it to the raw MCP ``inputSchema`` dict. The page hides every
control whose capability is false, and the interceptor never sends an
argument the capability does not cover, so a P0 server never sees ``branch``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScopeCapabilities:
    branch_selector: bool  # "branch" in every tool's inputSchema properties
    changed_slice: bool  # "changed" in search_codebase's scope enum
    diff_slice: bool  # "diff" in search_codebase's AND grep's scope enum


NO_SCOPE_CAPABILITIES = ScopeCapabilities(
    branch_selector=False, changed_slice=False, diff_slice=False
)


@dataclass(frozen=True, slots=True)
class BuiltAgent:
    """``build_agent_with_scope_capabilities``'s result — the graph, the llm,
    and the capability record the page and the interceptor read."""

    graph: object
    llm: object
    scope_capabilities: ScopeCapabilities


def _properties(schema: object) -> Mapping[str, object]:
    props = schema.get("properties") if isinstance(schema, Mapping) else None
    return props if isinstance(props, Mapping) else {}


def _enum_values(schema: Mapping[str, object], name: str) -> tuple[str, ...]:
    """The enum of property ``name``: inline, hoisted into ``$defs``, or under ``anyOf``."""
    prop = _properties(schema).get(name)
    if not isinstance(prop, Mapping):
        return ()
    if isinstance(prop.get("enum"), list):
        return tuple(str(v) for v in prop["enum"])
    ref = prop.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        defs = schema.get("$defs")
        definition = defs.get(ref.rsplit("/", 1)[-1], {}) if isinstance(defs, Mapping) else {}
        return tuple(str(v) for v in definition.get("enum", ()))
    options = prop.get("anyOf", ())
    return tuple(
        str(v) for option in options if isinstance(option, Mapping) for v in option.get("enum", ())
    )


def inspect_scope_capabilities(tools: Sequence[object]) -> ScopeCapabilities:
    """The capability record for a loaded tool list (non-dict schemas are ignored)."""
    schemas = {str(getattr(tool, "name", "")): getattr(tool, "args_schema", None) for tool in tools}
    dict_schemas = {name: s for name, s in schemas.items() if isinstance(s, Mapping)}
    if not dict_schemas:
        return NO_SCOPE_CAPABILITIES
    search_scope = _enum_values(dict_schemas.get("search_codebase", {}), "scope")
    grep_scope = _enum_values(dict_schemas.get("grep", {}), "scope")
    return ScopeCapabilities(
        branch_selector=all("branch" in _properties(s) for s in dict_schemas.values()),
        changed_slice="changed" in search_scope,
        diff_slice="diff" in search_scope and "diff" in grep_scope,
    )


__all__ = (
    "NO_SCOPE_CAPABILITIES",
    "BuiltAgent",
    "ScopeCapabilities",
    "inspect_scope_capabilities",
)
