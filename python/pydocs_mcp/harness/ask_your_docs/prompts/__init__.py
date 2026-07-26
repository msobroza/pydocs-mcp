"""EVERY model-facing prompt for the ask-your-docs agent — per-architecture.

Convention over configuration: an architecture registered as ``<name>`` gets
its prompts from ``prompts/<name>/*.j2``, FALLING BACK to the harness-agnostic
shared pool in ``pydocs_mcp/harness/core/prompts/`` — so adding or customizing
an architecture's prompt is one dropped-in file named after the registry
entry, never a code edit. The ``@register_architecture`` decorator
(architectures/base.py) binds the namespace to the registry name.

Layout:

- ``harness/core/prompts/`` — the shared pool (owner decision 2026-07-26:
                  shared prompts live in ``harness/core``, not in any single
                  harness): ``system_v1`` (base ReAct system prompt),
                  ``rewrite_v1`` (follow-up → standalone), ``vision_extraction_v1``
                  (image-fact extraction — vision node + reinspect tool),
                  ``reinspect_description_v1`` / ``reinspect_budget_message_v1``.
- ``inline/``   — ``system_suffix_v1`` (image-analysis section appended to the
                  system prompt) — harness-local architecture namespace.
- ``<name>/``   — any future architecture's overrides/additions (harness-local).

Versioning rule (retrieval/prompts precedent): never edit a shipped ``_vN``
in place — ship ``_vN+1``. Variant SELECTION via YAML is deferred to the
agent auto-optimization work. jinja2 is a core dep — importing this is light.
"""

from __future__ import annotations

from typing import Any

from pydocs_mcp.harness.core.prompt_namespace import HarnessPromptNamespace
from pydocs_mcp.harness.core.prompts import render_core_prompt

_PACKAGE = "pydocs_mcp.harness.ask_your_docs.prompts"

# Back-compat export: the resolver class is the harness-generic core seam.
ArchitecturePrompts = HarnessPromptNamespace


def prompts_for(architecture: str) -> HarnessPromptNamespace:
    """The namespace for ``architecture`` (a registry name); a missing
    directory simply means pure shared-pool fallback."""
    return HarnessPromptNamespace(_PACKAGE, architecture)


def render_shared(prompt_name: str, **variables: Any) -> str:
    """Render an architecture-independent prompt from the shared core pool."""
    return render_core_prompt(prompt_name, **variables)


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
