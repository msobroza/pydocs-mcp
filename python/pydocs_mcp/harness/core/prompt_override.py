"""Prompt-override seam + the one system-prompt assembly function.

The override type is what evaluation harnesses inject through
``build_agent(prompts=...)`` — ``None`` fields mean "use the shipped
templates". Assembly lives here so there is exactly ONE place that decides
the assembled shape (single source of truth); harnesses resolve their own
components (system text, catalog block, session-start pack) and pass them in.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PromptOverrides:
    """Prompt overrides for evaluation harnesses. ``None`` → shipped templates.

    ``system_prompt`` substitutes the system *component* at the assembly site
    (catalog listing and architecture-appended sections stay outside the
    override). ``rewrite_prompt`` is a ``str.format`` template with
    ``{history}`` / ``{question}`` placeholders consumed by the harness's own
    reformulate call; assembly never reads it.
    """

    system_prompt: str | None = None
    rewrite_prompt: str | None = None


def assemble_system_prompt(
    system: str,
    catalog_block: str,
    session_start_context: str | None = None,
    skill_block: str | None = None,
) -> str:
    """Compose the final system prompt: system + catalog (+ session pack)
    (+ skill guidance).

    ``session_start_context`` (ADR 0008) appends the harness-injected
    session-start pack after the catalog. ``skill_block`` (run-contract
    design §9 stage 2) appends the skill-artifact guidance — the harness's
    ``system_prompt_suffix`` delivery channel — after the session pack.
    ``None`` for either — the shipped defaults — keeps the assembled prompt
    byte-identical to the pre-existing shape (each optional component's
    off-state is the ablation control arm).
    """
    assembled = f"{system}\nIndexed projects and packages:\n{catalog_block}"
    if session_start_context is not None:
        assembled = f"{assembled}\n{session_start_context}"
    if skill_block is not None:
        assembled = f"{assembled}\n{skill_block}"
    return assembled
