"""One shared task scaffold, owned by ``pydocs_eval.task_rendering``.

Run-contract design §8: the external CLI track and the in-process ask path
must render the SAME instructions, or the gold gates reward one arm for a
citation demand the other never received. These tests pin (a) the agent
track's ``task_prompt`` as a thin delegate — so its byte-identity contract is
unchanged — and (b) the module's base-install floor, since the black-box
track's console scripts import it.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from pydocs_eval.agent_track._command import task_prompt
from pydocs_eval.task_rendering import TASK_SCAFFOLD_VERSION, render_task_prompt


def test_agent_track_prompt_is_the_shared_scaffold_verbatim() -> None:
    # The delegate contract: with no skill, task_prompt IS render_task_prompt.
    assert task_prompt("What does X do?") == render_task_prompt("What does X do?")
    assert task_prompt("q", skill="") == render_task_prompt("q")


def test_scaffold_demands_a_citation_and_ends_with_the_question() -> None:
    rendered = render_task_prompt("How does routing work?")
    assert "citing the file and line where the answer lives" in rendered
    assert "read-only analysis task" in rendered
    assert rendered.endswith("Question: How does routing work?")


def test_skill_section_builds_on_top_of_the_shared_scaffold() -> None:
    skill = "USE get_symbol FIRST"
    assert task_prompt("q", skill=skill) == f"{render_task_prompt('q')}\n\n{skill}"


def test_scaffold_version_is_recorded_for_the_objective_hash() -> None:
    # Editing the scaffold without bumping this string would silently resume
    # verdicts produced under different instructions (design §8).
    assert TASK_SCAFFOLD_VERSION == "task_scaffold_v1"


def test_module_stays_base_install_library_free() -> None:
    # ADR 0009's 2026-07-27 floor: the black-box track's base modules must not
    # import pydocs_mcp. Parsed, not merely grepped, so a comment cannot pass.
    import pydocs_eval.task_rendering as module

    source = Path(inspect.getfile(module)).read_text(encoding="utf-8")
    imported = {
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(name.startswith("pydocs_mcp") for name in imported), sorted(imported)
