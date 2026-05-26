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
    autoescape=False,           # prompt text is not HTML; don't escape
    undefined=StrictUndefined,  # missing vars in templates raise loudly
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_prompt(template_name: str, **variables: Any) -> str:
    """Load ``<template_name>.j2`` from this package and render it.

    Variables passed via keyword args become template context. ``trees``
    is serialized via Jinja2's ``tojson`` filter; the template is
    responsible for wrapping it appropriately.
    """
    pkg = resources.files("pydocs_mcp.retrieval.prompts")
    template_file = pkg.joinpath(f"{template_name}.j2")
    if not template_file.is_file():
        raise FileNotFoundError(
            f"Prompt template {template_name!r} not found "
            f"under pydocs_mcp/retrieval/prompts/.",
        )
    template = _env.from_string(template_file.read_text(encoding="utf-8"))
    return template.render(**variables)
