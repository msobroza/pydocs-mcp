"""The shared prompt pool — harness-agnostic fallback templates (flat ``*.j2``).

Owner decision (2026-07-26): shared prompts live in ``harness/core``, not in
any single harness. A harness architecture namespace overrides a template by
shipping its own file under ``<harness>/prompts/<architecture>/``; this pool
serves everything it does not override.

Versioning rule (retrieval/prompts precedent): never edit a shipped ``_vN``
in place — ship ``_vN+1``. jinja2 is a core dep — importing this is light.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

from pydocs_mcp.retrieval.prompts._loader import render_prompt_from

CORE_PROMPTS_PACKAGE = "pydocs_mcp.harness.core.prompts"


def has_core_prompt(prompt_name: str) -> bool:
    """Whether ``prompt_name`` exists in the shared pool."""
    return resources.files(CORE_PROMPTS_PACKAGE).joinpath(f"{prompt_name}.j2").is_file()


def core_prompt_names() -> tuple[str, ...]:
    """Every template name in the shared pool (without the ``.j2`` suffix)."""
    pool = resources.files(CORE_PROMPTS_PACKAGE)
    return tuple(sorted(entry.name[:-3] for entry in pool.iterdir() if entry.name.endswith(".j2")))


def render_core_prompt(prompt_name: str, **variables: Any) -> str:
    """Render a template from the shared pool.

    Example: ``render_core_prompt("rewrite_v1", history=h, question=q)``.
    """
    return render_prompt_from(CORE_PROMPTS_PACKAGE, prompt_name, **variables)
