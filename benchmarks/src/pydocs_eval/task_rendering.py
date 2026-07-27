"""How a dataset question becomes the text an agent actually reads.

The run-contract design (§8, `docs/superpowers/specs/2026-07-27-harness-run-contract-design.md`)
makes task rendering a first-class step owned by ONE module instead of a
detail of whichever execution path happens to run. Before this module the
external CLI track applied a citation-demanding scaffold while the in-process
ask path handed the bare question to the agent — so the two tracks were not
measuring the same task, and the gold gates rewarded the scaffolded arm for
instructions the other arm never received.

``render_task_prompt`` is that single source. Both execution paths render
through it, so a scaffold edit moves both tracks together and shows up as one
recorded objective change (the ``binding_identity`` fold in
``rubric_config_hash``), never as a silent divergence.

BASE install by contract: stdlib only, no ``pydocs_mcp`` import — the
black-box agent track's console scripts import this module (ADR 0009's
2026-07-27 amendment keeps base-install eval modules library-free).
"""

from __future__ import annotations

# ONE scaffold every execution path runs (run-contract design §8; originally
# the agent-track-private ``_command._SCAFFOLD``). Keeping the bare scaffold in
# its own constant is what makes ``task_prompt(q, skill="")`` byte-identical to
# it — the skill section is appended only when non-empty, so no trailing
# whitespace leaks in.
_SCAFFOLD = (
    "Your working directory is the repository. Answer the question about the "
    "repository directly, citing the file and line where the answer lives. Do "
    "not edit any files; this is a read-only analysis task.\n\n"
    "Question: {question}"
)

# The scaffold's recorded version. It folds into the objective identity
# (``rubric_config_hash(..., binding_identity=...)``), so editing ``_SCAFFOLD``
# without bumping this string would silently resume verdicts produced under
# different instructions.
TASK_SCAFFOLD_VERSION = "task_scaffold_v1"


def render_task_prompt(question: str) -> str:
    """Render ``question`` into the shared task scaffold.

    The ONLY place the scaffold text is applied. Callers that additionally
    append candidate guidance (the agent track's ``skill`` section) build on
    top of this return value rather than re-formatting the scaffold.

    Example:
        >>> render_task_prompt("What does X do?").endswith("Question: What does X do?")
        True
    """
    return _SCAFFOLD.format(question=question)
