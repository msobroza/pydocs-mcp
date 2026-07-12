"""EVERY model-facing prompt string for the ask-your-docs agent lives here.

One directory answers "where are the prompts?": versioned Jinja2 templates
(``*_vN.j2``) following the ``retrieval/prompts`` precedent — never edit a
shipped version in place; ship a ``_v2`` file instead, because deployments
depend on stable prompt behavior keyed by name. Variant *selection* (a YAML
knob) is deliberately not built here — that surface belongs to the agent
auto-optimization work.

Template inventory:

- ``system_react_v1``            — the main ReAct system prompt (all architectures)
- ``rewrite_v1``                 — follow-up → standalone question rewrite
- ``image_analysis_section_v1``  — appended to the system prompt by ``inline``
- ``vision_extraction_v1``       — question-guided image-fact extraction
                                   (``vision_subagent`` node + ``reinspect_images``)
- ``reinspect_description_v1``   — the reinspect tool's model-facing description
- ``reinspect_budget_message_v1``— the per-turn-budget refusal text

Light by contract: jinja2 is a core runtime dep, so importing this pulls no
extra weight and the subpackage's lazy-import guarantee holds.
"""

from __future__ import annotations

from typing import Any

from pydocs_mcp.retrieval.prompts._loader import render_prompt_from

_PACKAGE = "pydocs_mcp.ask_your_docs.prompts"

TEMPLATE_NAMES: tuple[str, ...] = (
    "system_react_v1",
    "rewrite_v1",
    "image_analysis_section_v1",
    "vision_extraction_v1",
    "reinspect_description_v1",
    "reinspect_budget_message_v1",
)


def render_agent_prompt(template_name: str, **variables: Any) -> str:
    """Render one of this package's templates (see ``TEMPLATE_NAMES``)."""
    return render_prompt_from(_PACKAGE, template_name, **variables)


def rewrite_prompt(*, history: str, question: str) -> str:
    """The reformulation prompt with its two variables rendered."""
    return render_agent_prompt("rewrite_v1", history=history, question=question)


def vision_extraction_prompt(*, question: str) -> str:
    """The question-guided image-fact extraction prompt."""
    return render_agent_prompt("vision_extraction_v1", question=question)


# Static prompts, rendered once at import (no variables).
SYSTEM_PROMPT = render_agent_prompt("system_react_v1")
IMAGE_ANALYSIS_PROMPT_SECTION = render_agent_prompt("image_analysis_section_v1")
REINSPECT_DESCRIPTION = render_agent_prompt("reinspect_description_v1")
BUDGET_MESSAGE = render_agent_prompt("reinspect_budget_message_v1")

__all__ = (
    "BUDGET_MESSAGE",
    "IMAGE_ANALYSIS_PROMPT_SECTION",
    "REINSPECT_DESCRIPTION",
    "SYSTEM_PROMPT",
    "TEMPLATE_NAMES",
    "render_agent_prompt",
    "rewrite_prompt",
    "vision_extraction_prompt",
)
