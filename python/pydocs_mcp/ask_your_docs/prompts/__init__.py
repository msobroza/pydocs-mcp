"""EVERY model-facing prompt for the ask-your-docs agent — per-architecture.

Convention over configuration: an architecture registered as ``<name>`` gets
its prompts from ``prompts/<name>/*.j2``, FALLING BACK to ``prompts/shared/``
for anything it doesn't override — so adding or customizing an architecture's
prompt is one dropped-in file named after the registry entry, never a code
edit. The ``@register_architecture`` decorator (architectures/base.py) binds
the namespace to the registry name.

Layout:

- ``shared/``   — the fallback pool: ``system_v1`` (base ReAct system prompt),
                  ``rewrite_v1`` (follow-up → standalone), ``vision_extraction_v1``
                  (image-fact extraction — vision node + reinspect tool),
                  ``reinspect_description_v1`` / ``reinspect_budget_message_v1``.
- ``inline/``   — ``system_suffix_v1`` (image-analysis section appended to the
                  system prompt).
- ``<name>/``   — any future architecture's overrides/additions.

Versioning rule (retrieval/prompts precedent): never edit a shipped ``_vN``
in place — ship ``_vN+1``. Variant SELECTION via YAML is deferred to the
agent auto-optimization work. jinja2 is a core dep — importing this is light.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any

from pydocs_mcp.retrieval.prompts._loader import render_prompt_from

_PACKAGE = "pydocs_mcp.ask_your_docs.prompts"
_SHARED = "shared"


@dataclass(frozen=True, slots=True)
class ArchitecturePrompts:
    """The prompt namespace of one registered architecture."""

    architecture: str

    def resolve_source(self, prompt_name: str) -> str:
        """Which directory serves ``prompt_name`` — the architecture's own or
        ``shared`` — raising with both searched locations when neither has it."""
        pkg = resources.files(_PACKAGE)
        for source in (self.architecture, _SHARED):
            if pkg.joinpath(source, f"{prompt_name}.j2").is_file():
                return source
        raise FileNotFoundError(
            f"prompt {prompt_name!r} not found for architecture "
            f"{self.architecture!r} — searched prompts/{self.architecture}/ "
            f"and prompts/{_SHARED}/."
        )

    def render(self, prompt_name: str, **variables: Any) -> str:
        source = self.resolve_source(prompt_name)
        return render_prompt_from(_PACKAGE, f"{source}/{prompt_name}", **variables)

    def names(self) -> tuple[str, ...]:
        """Every prompt this architecture can render (own ∪ shared)."""
        pkg = resources.files(_PACKAGE)
        found: set[str] = set()
        for source in (self.architecture, _SHARED):
            directory = pkg.joinpath(source)
            if directory.is_dir():
                found |= {
                    entry.name[:-3] for entry in directory.iterdir() if entry.name.endswith(".j2")
                }
        return tuple(sorted(found))


def prompts_for(architecture: str) -> ArchitecturePrompts:
    """The namespace for ``architecture`` (a registry name); a missing
    directory simply means pure shared fallback."""
    return ArchitecturePrompts(architecture)


def render_shared(prompt_name: str, **variables: Any) -> str:
    """Render an architecture-independent prompt from the shared pool."""
    return render_prompt_from(_PACKAGE, f"{_SHARED}/{prompt_name}", **variables)


def rewrite_prompt(*, history: str, question: str) -> str:
    """The reformulation prompt (architecture-independent — it runs before
    any graph is involved)."""
    return render_shared("rewrite_v1", history=history, question=question)


# Back-compat / convenience constants (rendered once at import; no variables).
SYSTEM_PROMPT = render_shared("system_v1")
REINSPECT_DESCRIPTION = render_shared("reinspect_description_v1")
BUDGET_MESSAGE = render_shared("reinspect_budget_message_v1")

__all__ = (
    "BUDGET_MESSAGE",
    "REINSPECT_DESCRIPTION",
    "SYSTEM_PROMPT",
    "ArchitecturePrompts",
    "prompts_for",
    "render_shared",
    "rewrite_prompt",
)
