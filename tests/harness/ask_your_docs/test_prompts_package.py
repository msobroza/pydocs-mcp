"""ask_your_docs/prompts — per-architecture prompt namespaces.

Convention over configuration: an architecture's prompts live in
``prompts/<registry-name>/`` and fall back to ``prompts/shared/``; the
``@register_architecture`` decorator binds the namespace to the registry
name, so no architecture hardcodes a template path."""

from __future__ import annotations

import pytest

pytest.importorskip("jinja2")

from pydocs_mcp.ask_your_docs import prompts


def test_shared_pool_renders() -> None:
    assert "You are a documentation and code assistant" in prompts.render_shared("system_v1")
    rewrite = prompts.render_shared("rewrite_v1", history="H-LINES", question="Q-TEXT")
    assert "H-LINES" in rewrite and "Q-TEXT" in rewrite and "{" not in rewrite
    assert "ERROR:" in prompts.render_shared("vision_extraction_v1", question="q")
    assert "EXPENSIVE" in prompts.REINSPECT_DESCRIPTION
    assert "budget" in prompts.BUDGET_MESSAGE


def test_architecture_namespace_resolves_own_then_shared() -> None:
    """The core convention: prompts/<name>/ wins, shared/ is the fallback."""
    inline_ns = prompts.prompts_for("inline")
    assert "Image handling:" in inline_ns.render("system_suffix_v1")
    assert inline_ns.resolve_source("system_suffix_v1") == "inline"
    # No override for system_v1 → shared serves it.
    assert inline_ns.resolve_source("system_v1") == "shared"
    assert inline_ns.render("system_v1") == prompts.render_shared("system_v1")
    # An architecture with no directory at all is pure fallback.
    tr = prompts.prompts_for("text_react")
    assert tr.resolve_source("system_v1") == "shared"
    assert tr.render("vision_extraction_v1", question="q")


def test_unknown_template_raises_listing_both_locations() -> None:
    with pytest.raises(FileNotFoundError) as excinfo:
        prompts.prompts_for("inline").render("nope_v1")
    msg = str(excinfo.value)
    assert "inline" in msg and "shared" in msg


def test_namespace_names_are_the_union() -> None:
    names = prompts.prompts_for("inline").names()
    assert "system_suffix_v1" in names  # own
    assert "system_v1" in names and "rewrite_v1" in names  # shared


def test_decorator_binds_namespace_to_registry_name() -> None:
    """@register_architecture registers AND wires prompts() by name."""
    pytest.importorskip("langgraph")
    from pydocs_mcp.ask_your_docs.architectures import agent_registry

    for name in agent_registry.names():
        cls = agent_registry.get(name)
        assert cls.architecture_name == name
        assert cls.prompts().architecture == name


def test_system_prompt_identical_across_architectures_today() -> None:
    """No architecture overrides system_v1 yet — the base system prompt is
    deliberately identical everywhere (the AC3 anchor); an override is one
    dropped-in prompts/<name>/system_v1.j2 away."""
    pytest.importorskip("langgraph")
    from pydocs_mcp.ask_your_docs.architectures import agent_registry

    rendered = {prompts.prompts_for(n).render("system_v1") for n in agent_registry.names()}
    assert len(rendered) == 1
    assert rendered.pop() == prompts.SYSTEM_PROMPT  # back-compat constant


def test_backcompat_exports_still_work() -> None:
    pytest.importorskip("langgraph")
    from pydocs_mcp.ask_your_docs import agent
    from pydocs_mcp.ask_your_docs.architectures import inline

    assert agent.SYSTEM_PROMPT is prompts.SYSTEM_PROMPT
    assert "Image handling:" in inline._IMAGE_ANALYSIS_PROMPT_SECTION
