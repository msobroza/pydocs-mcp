"""Ask-your-docs — a LangGraph ReAct agent over pydocs-mcp, with a Streamlit UI.

Re-exports are lazy (PEP 562 ``__getattr__``) so importing a light submodule
like ``graph`` or ``catalog`` never drags in the heavy agent stack (langgraph /
streamlit), which only ships with the ``[ask-your-docs]`` extra.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ask",
    "build_agent",
    "reformulate",
    "render_catalog",
    "scope_prefix",
    "workspace_catalog",
]

_LAZY = {
    "ask": "agent",
    "build_agent": "agent",
    "reformulate": "agent",
    "scope_prefix": "agent",
    "render_catalog": "catalog",
    "workspace_catalog": "catalog",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(f"{__name__}.{module}")
    return getattr(mod, name)
