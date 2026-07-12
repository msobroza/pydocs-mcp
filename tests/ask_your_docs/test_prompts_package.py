"""ask_your_docs/prompts — the single home of every model-facing prompt string
(option c: centralized, versioned .j2 files; no YAML selector yet — variant
selection is the ask-auto-optimization spec's surface)."""

from __future__ import annotations

import pytest

pytest.importorskip("jinja2")

from pydocs_mcp.ask_your_docs import prompts


def test_all_templates_exist_and_render() -> None:
    """One directory answers 'where are the prompts?' — every shipped name
    renders non-empty."""
    for name in prompts.TEMPLATE_NAMES:
        text = prompts.render_agent_prompt(name, question="q", history="h")
        assert text.strip(), name


def test_static_constants_come_from_templates() -> None:
    """The importable constants are rendered from the package's .j2 files —
    editing a template (by shipping a _v2) is the only way to change them."""
    assert "You are a documentation and code assistant" in prompts.SYSTEM_PROMPT
    assert prompts.SYSTEM_PROMPT.endswith("widening it.\n")
    assert prompts.IMAGE_ANALYSIS_PROMPT_SECTION.startswith("\nImage handling:")
    assert "EXPENSIVE" in prompts.REINSPECT_DESCRIPTION
    assert "budget" in prompts.BUDGET_MESSAGE


def test_rewrite_and_vision_render_their_variables() -> None:
    rewrite = prompts.render_agent_prompt("rewrite_v1", history="H-LINES", question="Q-TEXT")
    assert "H-LINES" in rewrite and "Q-TEXT" in rewrite
    assert "{" not in rewrite  # no unrendered placeholders
    vision = prompts.render_agent_prompt("vision_extraction_v1", question="Q-TEXT")
    assert "Q-TEXT" in vision and "ERROR:" in vision


def test_consumers_import_from_the_central_package() -> None:
    """The old definition sites re-export the central values (back-compat),
    so greps and existing imports keep working while the text lives in ONE
    place."""
    pytest.importorskip("langgraph")
    from pydocs_mcp.ask_your_docs import agent
    from pydocs_mcp.ask_your_docs.architectures import inline, vision_subagent

    assert agent.SYSTEM_PROMPT is prompts.SYSTEM_PROMPT
    assert inline._IMAGE_ANALYSIS_PROMPT_SECTION is prompts.IMAGE_ANALYSIS_PROMPT_SECTION
    # vision_subagent + reinspect render via prompts.vision_extraction_prompt —
    # their behavior tests pin the rendered content reaching the model.
    assert vision_subagent  # imported without error; no stale local constant
    assert not hasattr(vision_subagent, "_VISION_EXTRACTION_PROMPT")
