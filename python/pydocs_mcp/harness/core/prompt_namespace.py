"""Per-architecture prompt namespaces over a harness prompt package.

Generalized from the ask-your-docs resolver: any harness ships a prompt
package whose ``<architecture>/`` directories override by name, with the
harness-agnostic shared pool (``harness/core/prompts``) serving everything
not overridden. ``"shared"`` is the public source LABEL for the pool.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any

from pydocs_mcp.harness.core.prompts import (
    core_prompt_names,
    has_core_prompt,
    render_core_prompt,
)
from pydocs_mcp.retrieval.prompts._loader import render_prompt_from

SHARED_SOURCE_LABEL = "shared"


@dataclass(frozen=True, slots=True)
class HarnessPromptNamespace:
    """The prompt namespace of one registered architecture in one harness.

    Example: ``HarnessPromptNamespace("pydocs_mcp.harness.ask_your_docs.prompts",
    "inline").render("system_suffix_v1")``.
    """

    package: str
    architecture: str

    def resolve_source(self, prompt_name: str) -> str:
        """Which source serves ``prompt_name`` — the architecture's own
        directory or the ``shared`` core pool — raising with both searched
        locations when neither has it."""
        pkg = resources.files(self.package)
        if pkg.joinpath(self.architecture, f"{prompt_name}.j2").is_file():
            return self.architecture
        if has_core_prompt(prompt_name):
            return SHARED_SOURCE_LABEL
        raise FileNotFoundError(
            f"prompt {prompt_name!r} not found for architecture "
            f"{self.architecture!r} — searched "
            f"{self.package.replace('.', '/')}/{self.architecture}/ "
            f"and the shared pool harness/core/prompts/."
        )

    def render(self, prompt_name: str, **variables: Any) -> str:
        source = self.resolve_source(prompt_name)
        if source == SHARED_SOURCE_LABEL:
            return render_core_prompt(prompt_name, **variables)
        return render_prompt_from(self.package, f"{source}/{prompt_name}", **variables)

    def names(self) -> tuple[str, ...]:
        """Every prompt this architecture can render (own ∪ shared pool)."""
        pkg = resources.files(self.package)
        found: set[str] = set(core_prompt_names())
        directory = pkg.joinpath(self.architecture)
        if directory.is_dir():
            found |= {
                entry.name[:-3] for entry in directory.iterdir() if entry.name.endswith(".j2")
            }
        return tuple(sorted(found))
