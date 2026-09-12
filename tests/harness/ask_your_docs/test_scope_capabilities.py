"""inspect_scope_capabilities over the registration golden — AC-15."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    BuiltAgent,
    ScopeCapabilities,
    inspect_scope_capabilities,
)

_GOLDEN = (
    Path(__file__).resolve().parents[2] / "fixtures" / "goldens" / "mcp_registration_surface.json"
)


@dataclass(frozen=True)
class _SchemaTool:
    name: str
    args_schema: object


def _golden_tools() -> list[_SchemaTool]:
    surface = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    return [
        _SchemaTool(name, copy.deepcopy(entry["inputSchema"])) for name, entry in surface.items()
    ]


def test_todays_surface_advertises_nothing():
    assert inspect_scope_capabilities(_golden_tools()) == NO_SCOPE_CAPABILITIES
    assert inspect_scope_capabilities([]) == NO_SCOPE_CAPABILITIES


def test_branch_on_every_tool_enables_the_selector():
    tools = _golden_tools()
    for tool in tools:
        tool.args_schema["properties"]["branch"] = {"type": "string", "default": ""}
    assert inspect_scope_capabilities(tools).branch_selector is True
    tools[0].args_schema["properties"].pop("branch")
    assert inspect_scope_capabilities(tools).branch_selector is False


def test_slice_values_are_read_from_search_and_grep_scope_enums():
    tools = {t.name: t for t in _golden_tools()}
    tools["search_codebase"].args_schema["properties"]["scope"]["enum"] = [
        "project",
        "deps",
        "all",
        "changed",
    ]
    caps = inspect_scope_capabilities(list(tools.values()))
    assert caps == ScopeCapabilities(branch_selector=False, changed_slice=True, diff_slice=False)
    tools["search_codebase"].args_schema["properties"]["scope"]["enum"].append("diff")
    assert inspect_scope_capabilities(list(tools.values())).diff_slice is False  # grep lacks it
    tools["grep"].args_schema["properties"]["scope"]["enum"] = ["project", "deps", "all", "diff"]
    assert inspect_scope_capabilities(list(tools.values())).diff_slice is True


def test_non_mapping_schemas_are_ignored():
    class _PydanticLike:  # a StructuredTool.from_function tool carries a model class
        pass

    tools = [*_golden_tools(), _SchemaTool("reinspect_images", _PydanticLike)]
    for tool in tools[:-1]:
        tool.args_schema["properties"]["branch"] = {"type": "string"}
    assert inspect_scope_capabilities(tools).branch_selector is True


def test_built_agent_is_a_frozen_record():
    built = BuiltAgent(graph="G", llm="L", scope_capabilities=NO_SCOPE_CAPABILITIES)
    assert (built.graph, built.llm) == ("G", "L")
