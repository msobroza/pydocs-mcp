"""Loader for the cross-harness prompt pool packaged under ``harness/assets/prompts``.

The templates are ASSETS (optimizable text); this loader is MACHINERY, which is
why the two live in different trees: a diff under ``harness/assets/`` is a
guidance change by definition, so no code may sit there to blur that reading.

Naming, deliberately: the pool's RESERVED resolution label stays ``"core"``
(``prompt_namespace.CORE_POOL_LABEL``) even though its directory is now
``assets/prompts/``. The label is public resolution vocabulary — it is what
``HarnessPromptNamespace.resolve_source`` returns — so renaming it would change
behavior, not layout.

Versioning rule (retrieval/prompts precedent): never edit a shipped ``_vN`` in
place — ship ``_vN+1``. jinja2 is a core dep — importing this is light.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

from pydocs_mcp.retrieval.prompts._loader import render_prompt_from

CORE_PROMPTS_PACKAGE = "pydocs_mcp.harness.assets.prompts"


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
