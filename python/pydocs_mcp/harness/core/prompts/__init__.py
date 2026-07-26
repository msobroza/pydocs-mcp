"""The cross-harness prompt pool — ONLY prompts plausibly shared across harnesses.

Owner rule (2026-07-26): a template lives here only if a second harness or
task could plausibly reuse it — today the retriever-guidance surface the
optimizer seeds from (``system_v1``, ``rewrite_v1``). A single harness's
feature machinery (e.g. ask-your-docs' vision/reinspect templates) lives in
that harness's own ``prompts/freeze/`` pool instead — FROZEN, because
optimizable tasks consume it as an experimental control (byte-pinned per
harness via ``core/prompt_freeze.py``). Resolution order per architecture
namespace: ``<harness>/prompts/<architecture>/`` →
``<harness>/prompts/freeze/`` → this pool (``prompt_namespace.py``).

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
