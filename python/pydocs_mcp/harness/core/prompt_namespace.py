"""Per-architecture prompt namespaces over a harness prompt package.

Resolution is three-tier, most specific wins: the architecture's own
``<architecture>/`` directory, then the harness-local ``shared/`` pool
(harness-specific but architecture-independent machinery), then the
cross-harness core pool (``harness/core/prompts``). Owner rule
(2026-07-26): the core pool carries ONLY prompts plausibly shared across
harnesses/tasks — a single harness's feature machinery lives in that
harness's own ``shared/``.

``"shared"`` labels the harness-local pool (it is also the directory
name); ``"core"`` labels the cross-harness pool. Both label strings are
therefore RESERVED — never register an architecture named ``shared`` or
``core``.
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

HARNESS_POOL_LABEL = "shared"
CORE_POOL_LABEL = "core"


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
        directory, the harness-local ``shared`` pool, or the ``core``
        pool — raising with all three searched locations when none has
        it."""
        pkg = resources.files(self.package)
        if pkg.joinpath(self.architecture, f"{prompt_name}.j2").is_file():
            return self.architecture
        if pkg.joinpath(HARNESS_POOL_LABEL, f"{prompt_name}.j2").is_file():
            return HARNESS_POOL_LABEL
        if has_core_prompt(prompt_name):
            return CORE_POOL_LABEL
        package_dir = self.package.replace(".", "/")
        raise FileNotFoundError(
            f"prompt {prompt_name!r} not found for architecture "
            f"{self.architecture!r} — searched "
            f"{package_dir}/{self.architecture}/, "
            f"{package_dir}/{HARNESS_POOL_LABEL}/, "
            "and the core pool harness/core/prompts/."
        )

    def render(self, prompt_name: str, **variables: Any) -> str:
        source = self.resolve_source(prompt_name)
        if source == CORE_POOL_LABEL:
            return render_core_prompt(prompt_name, **variables)
        return render_prompt_from(self.package, f"{source}/{prompt_name}", **variables)

    def names(self) -> tuple[str, ...]:
        """Every prompt this architecture can render (own ∪ shared ∪ core)."""
        pkg = resources.files(self.package)
        found: set[str] = set(core_prompt_names())
        for directory_name in (HARNESS_POOL_LABEL, self.architecture):
            directory = pkg.joinpath(directory_name)
            if directory.is_dir():
                found |= {
                    entry.name[:-3] for entry in directory.iterdir() if entry.name.endswith(".j2")
                }
        return tuple(sorted(found))
