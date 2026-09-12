"""The ONE system-prompt assembly site for an ask-your-docs build.

Pulled out of ``agent.py`` for its line budget (AC-29, one tool-call read);
``agent.py`` re-exports every name here, so the existing import paths
survive. The candidate-or-shipped system section, the catalog block, the
session-start pack and the skill block compose here and nowhere else — a
second assembly site is the one forbidden shape (single source of truth).
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.harness.ask_your_docs.catalog import render_catalog
from pydocs_mcp.harness.ask_your_docs.prompts import prompts_for
from pydocs_mcp.harness.core.prompt_override import PromptOverrides, assemble_system_prompt

# Back-compat name: the override type is the harness-generic core seam
# (consumed by the eval binding and the UI through this import site).
AskPrompts = PromptOverrides


def _assemble_prompt(
    name: str,
    catalog: dict[str, list[str]],
    prompts: AskPrompts | None,
    session_start_context: str | None = None,
    skill_block: str | None = None,
) -> str:
    """The ONE prompt-assembly site: candidate-or-shipped system + catalog.

    The fallback is the per-architecture render (``prompts_for(name)``), never
    the ``SYSTEM_PROMPT`` constant — a ``prompts/<name>/system_v1.j2``
    override must apply whenever that architecture is selected (an architecture
    without that template gets ``shared/``; ``auto`` composes with its own
    shared prompt even when it delegates the graph). A second assembly site is
    the one forbidden shape (single source of truth).

    ``session_start_context`` (ADR 0008) appends the harness-injected
    session-start pack after the catalog; ``skill_block`` (run-contract
    design §9 stage 2) appends the skill-artifact guidance after it.
    ``None`` for either — the shipped defaults — keeps the assembled prompt
    byte-identical to the pre-existing shape.
    """
    resolved_system = (
        prompts.system_prompt
        if prompts and prompts.system_prompt
        else prompts_for(name).render("system_v1")
    )
    return assemble_system_prompt(
        resolved_system, render_catalog(catalog), session_start_context, skill_block
    )


def _resolved_skill_block(skill_override: Path | None, task_name: str | None) -> str | None:
    """The skill guidance for this build, or ``None`` — the byte-identity default.

    The backbone folds whenever skill guidance is requested at all
    (``skill_override`` or ``task_name`` given); the harness-invariant task
    head and this harness's harness task head fold only when ``task_name``
    names the arm's task. An unknown task name fails loudly in
    ``task_head_section_header`` (the enumerated v1 set); an invalid override
    document fails loudly in the loader — never a silent fallback.
    """
    if skill_override is None and task_name is None:
        return None
    # WHY function-local: the loader pulls in the description grammar; the
    # default build path (no skill) must not pay that import.
    from pydocs_mcp.harness.core.skill_artifact_loader import load_skill_artifact

    artifact = load_skill_artifact(skill_override)
    if task_name is None:
        return artifact.backbone
    task_head = artifact.task_head(task_name)
    harness_task_head = artifact.harness_task_head("ask_your_docs", task_name)
    return f"{artifact.backbone}\n{task_head}\n{harness_task_head}"
