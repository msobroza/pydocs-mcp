"""Decorator registry mapping an engine NAME to its adapter class.

The ``ProviderRegistry`` conventions (loud collision, loud unknown naming the
supported set, a ``names()`` pinned against its config vocabulary by test),
storing CLASSES like the architecture registry rather than builder functions —
an adapter takes no config, so its class IS its factory.

Extension seam contract: a new engine costs (1) one new module here, (2) one
``@cli_agent_registry.register("<name>")`` decorator, (3) one side-effect
import in ``__init__``, (4) the name in an arm's ``settings.engine``. No call
site changes — the composed harness resolves the adapter through this registry.
"""

from __future__ import annotations

from collections.abc import Callable

from pydocs_mcp.harness.platform.engines.base import CliAgentAdapter


class CliAgentRegistry:
    """Registered CLI-agent engines, by name."""

    def __init__(self) -> None:
        self._adapters: dict[str, type[CliAgentAdapter]] = {}

    def register(self, name: str) -> Callable[[type[CliAgentAdapter]], type[CliAgentAdapter]]:
        """Register an adapter class under ``name`` (duplicates raise at import)."""

        def decorator(adapter: type[CliAgentAdapter]) -> type[CliAgentAdapter]:
            if name in self._adapters:
                raise ValueError(f"CLI agent engine {name!r} already registered")
            self._adapters[name] = adapter
            return adapter

        return decorator

    def get(self, name: str) -> type[CliAgentAdapter]:
        """The adapter CLASS for ``name`` — class-level access (its
        ``guidance_channel``) needs no instance."""
        try:
            return self._adapters[name]
        except KeyError:
            supported = ", ".join(repr(known) for known in sorted(self._adapters))
            raise ValueError(
                f"Unknown CLI agent engine: {name!r}. Supported: {supported}."
            ) from None

    def build(self, name: str) -> CliAgentAdapter:
        """Construct the adapter registered under ``name``."""
        return self.get(name)()

    def names(self) -> frozenset[str]:
        """Registered engine names — pinned against the settings vocabulary by test."""
        return frozenset(self._adapters)


cli_agent_registry = CliAgentRegistry()
