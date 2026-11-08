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
from pydocs_eval.task_rendering import (
    DEFAULT_TASK_SCAFFOLD,
    TASK_SCAFFOLD_VERSION,
    TaskScaffold,
    render_task_prompt,
    task_scaffold_version,
)


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


def test_the_citing_scaffold_carries_the_unchanged_version() -> None:
    """The per-scaffold map keeps the citing version byte-identical, so no ledger moves."""
    assert task_scaffold_version(TaskScaffold.CITING) == TASK_SCAFFOLD_VERSION


def test_the_bare_question_has_its_own_version() -> None:
    """The chat page sends the bare question; its arms must never hash like citing ones."""
    assert task_scaffold_version(TaskScaffold.BARE) == "task_scaffold_bare_v1"
    assert task_scaffold_version(TaskScaffold.BARE) != TASK_SCAFFOLD_VERSION


def test_the_bare_scaffold_renders_the_question_unchanged() -> None:
    """What the chat page sends: the question alone, no instructions around it."""
    question = "  Where is the pipeline built?\n"
    assert render_task_prompt(question, scaffold=TaskScaffold.BARE) == question


def test_the_citing_scaffold_is_the_default_and_renders_byte_identically() -> None:
    explicit = render_task_prompt("q", scaffold=TaskScaffold.CITING)
    assert render_task_prompt("q") == explicit
    assert explicit == (
        "Your working directory is the repository. Answer the question about the "
        "repository directly, citing the file and line where the answer lives. Do "
        "not edit any files; this is a read-only analysis task.\n\nQuestion: q"
    )


def test_every_scaffold_renders_its_own_text_and_carries_its_own_version() -> None:
    """A scaffold whose text fell back to another's would run under a digest that lies."""
    rendered = {render_task_prompt("q", scaffold=scaffold) for scaffold in TaskScaffold}
    versions = {task_scaffold_version(scaffold) for scaffold in TaskScaffold}
    assert len(rendered) == len(versions) == len(TaskScaffold)


def test_a_scaffold_spelled_as_its_value_renders_as_the_member() -> None:
    """An ``arm_settings.json`` read without ``from_dict`` holds the string, not the enum."""
    assert render_task_prompt("q", scaffold="bare") == "q"  # type: ignore[arg-type]


def test_the_default_scaffold_is_citing() -> None:
    assert DEFAULT_TASK_SCAFFOLD is TaskScaffold.CITING


def test_module_imports_only_the_standard_library() -> None:
    """The stdlib-only pin: the black-box track's console scripts import this module."""
    import sys

    import pydocs_eval.task_rendering as module

    tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
    roots = {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        # A relative import stays inside pydocs_eval, which the other pin covers.
        if isinstance(node, ast.ImportFrom) and node.level == 0
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert roots <= set(sys.stdlib_module_names) | {"__future__"}, sorted(roots)


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
