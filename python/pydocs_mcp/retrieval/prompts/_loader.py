"""Render versioned Jinja2 prompts shipped under this package.

Templates are versioned via filename suffix (``_vN``); to ship a new
variant add a new file. Never edit a shipped version in place —
existing deployments depend on stable prompt behavior keyed by name.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

from jinja2 import Environment, StrictUndefined

_env = Environment(
    autoescape=False,  # noqa: S701 — prompt text is not HTML; LLM prompts shouldn't be HTML-escaped
    undefined=StrictUndefined,  # missing vars in templates raise loudly
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_prompt_from(package: str, template_name: str, **variables: Any) -> str:
    """Load ``<template_name>.j2`` from ``package`` and render it.

    One Jinja environment repo-wide (DRY): the retrieval templates and the
    ask-your-docs agent prompts both render through here — ``package`` is a
    positional parameter (not keyword-only) so a template variable named
    ``package`` can never collide with it.
    """
    pkg = resources.files(package)
    template_file = pkg.joinpath(f"{template_name}.j2")
    if not template_file.is_file():
        raise FileNotFoundError(
            f"Prompt template {template_name!r} not found under {package.replace('.', '/')}/.",
        )
    template = _env.from_string(template_file.read_text(encoding="utf-8"))
    return template.render(**variables)


def render_prompt(template_name: str, **variables: Any) -> str:
    """Load ``<template_name>.j2`` from this package and render it.

    Variables passed via keyword args become template context. ``trees``
    is serialized via Jinja2's ``tojson`` filter; the template is
    responsible for wrapping it appropriately.
    """
    return render_prompt_from("pydocs_mcp.retrieval.prompts", template_name, **variables)
