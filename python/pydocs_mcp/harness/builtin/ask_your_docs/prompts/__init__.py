"""EVERY model-facing prompt for the ask-your-docs agent — per-architecture.

Convention over configuration: an architecture registered as ``<name>`` gets
its prompts from ``prompts/<name>/*.j2``, falling back to the harness-local
``freeze/`` pool, then to the cross-harness core pool in
``pydocs_mcp/harness/assets/prompts/`` — so adding or customizing an
architecture's prompt is one dropped-in file named after the registry
entry, never a code edit. The ``@register_architecture`` decorator
(architectures/base.py) binds the namespace to the registry name.

Layout (owner rules 2026-07-26: the CORE pool carries only prompts plausibly
shared across harnesses/tasks; every harness-local template is FROZEN — an
experimental control consumed by optimizable tasks, byte-pinned by the
freeze manifest in tests/fixtures/goldens/, see platform/prompt_freeze.py):

- ``harness/assets/prompts/`` — cross-harness pool: ``system_v1`` (base ReAct
                  system prompt) and ``rewrite_v1`` (follow-up → standalone) —
                  the retriever-guidance surface the optimizer seeds from.
- ``freeze/``   — harness-local FROZEN pool (architecture-independent
                  ask-your-docs machinery): ``vision_extraction_v1``
                  (image-fact extraction — vision node + reinspect tool),
                  ``reinspect_description_v1`` / ``reinspect_budget_message_v1``.
- ``inline/``   — ``system_suffix_v1`` (image-analysis section appended to the
                  system prompt) — architecture namespace, frozen too.
- ``<name>/``   — any future architecture's overrides/additions (frozen too).

Versioning rule (retrieval/prompts precedent): never edit a shipped ``_vN``
in place — ship ``_vN+1``. Variant SELECTION via YAML is deferred to the
agent auto-optimization work. jinja2 is a core dep — importing this is light.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

from pydocs_mcp.harness.platform.prompt_namespace import FREEZE_POOL_LABEL, HarnessPromptNamespace
from pydocs_mcp.harness.platform.prompt_pool import render_core_prompt
from pydocs_mcp.retrieval.prompts._loader import render_prompt_from

_PACKAGE = "pydocs_mcp.harness.builtin.ask_your_docs.prompts"

# Back-compat export: the resolver class is the harness-generic core seam.
ArchitecturePrompts = HarnessPromptNamespace


def prompts_for(architecture: str) -> HarnessPromptNamespace:
    """The namespace for ``architecture`` (a registry name); a missing
    directory simply means pure shared-pool fallback."""
    return HarnessPromptNamespace(_PACKAGE, architecture)


def render_shared(prompt_name: str, **variables: Any) -> str:
    """Render an architecture-independent prompt: the harness-local
    ``freeze/`` pool wins; anything it does not carry falls back to the
    cross-harness core pool."""
    local = resources.files(_PACKAGE).joinpath(FREEZE_POOL_LABEL, f"{prompt_name}.j2")
    if local.is_file():
        return render_prompt_from(_PACKAGE, f"{FREEZE_POOL_LABEL}/{prompt_name}", **variables)
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
