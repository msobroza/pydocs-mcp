"""Agent-architecture registry for ask-your-docs (spec §3.3).

The fifth ``ComponentRegistry`` instance (after step / formatter / stage /
decision_source; #186's ``ProviderRegistry`` under
``extraction/strategies/embedders/`` is the *function*-registry sibling
family — architectures are classes with ``from_dict``, so ComponentRegistry
is the right base). Populated by side-effect import of the entry modules
below — the same import-time population pattern as extraction.pipeline.stages.
Duplicate registration raises ValueError at import time (wiring bugs surface
at import time, not first use).

Extension seam contract (binding for future specs): an architecture is
addable by (1) one new file here, (2) one ``@agent_registry.register(name)``
decorator, (3) one side-effect import below, (4) selecting the name in YAML.
No call-site edits in app.py / agent.py.
"""

from pydocs_mcp.ask_your_docs.architectures.base import (
    AgentArchitecture,
    AgentArchitectureError,
    AgentBuildContext,
)
from pydocs_mcp.retrieval.serialization import ComponentRegistry

agent_registry: ComponentRegistry[AgentArchitecture] = ComponentRegistry()


def register_architecture(name: str):
    """Register an architecture AND bind its prompt namespace in one decorator.

    ``name`` becomes both the registry key and the prompt directory
    (``prompts/<name>/`` with ``prompts/shared/`` fallback) — convention over
    configuration, so a new architecture never hardcodes template paths.
    Duplicate names raise ValueError at import time (ComponentRegistry).
    """
    registry_decorator = agent_registry.register(name)

    def decorator(cls: type[AgentArchitecture]) -> type[AgentArchitecture]:
        cls.architecture_name = name
        return registry_decorator(cls)

    return decorator


# Side-effect imports populate the registry. Heavy langgraph imports live
# INSIDE these modules' build() methods, which only run when the extra is
# installed and an agent is actually built.
from pydocs_mcp.ask_your_docs.architectures import (  # noqa: E402,F401
    auto,
    inline,
    text_react,
    vision_subagent,
)

__all__ = [
    "AgentArchitecture",
    "AgentArchitectureError",
    "AgentBuildContext",
    "agent_registry",
    "register_architecture",
]
