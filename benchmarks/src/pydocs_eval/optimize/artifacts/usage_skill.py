"""The ``usage_skill`` optimizable artifact (spec §D6).

A free-form skill document that teaches an agent how to operate pydocs-mcp:
which tool answers which question shape, how to decompose a repository question
into retrieval queries, and when to stop searching and read. Unlike
``tool_docs`` it has no internal delimited structure — it is one prose document
that reaches the evaluated agent through ``task_prompt(skill=...)``.

``render()`` returns a candidate's ``content`` or the packaged
``usage_skill_seed.md``. ``validate()`` runs the §D6 firewall: a size cap
(``_SKILL_TOKEN_BUDGET`` tokens via the shared ``CHARS_PER_TOKEN`` rule) plus a
check that all six live tool names appear — the tool list comes from the
product's ``TOOL_DOCS`` keys so a renamed/removed tool can never drift out of
sync. ``landing_note()`` points a human at the seed file and its consumer.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from importlib.resources import files

from pydocs_mcp.application.tool_docs import CHARS_PER_TOKEN, TOOL_DOCS

from pydocs_eval.optimize.registries import artifact_registry

# WHY: §D6 hard cap. Encoded once here as the single Python source; the run
# config YAML restates it for user-facing clarity (YAML files are exempt from
# the no-duplicate-literal rule). Applied via the shared CHARS_PER_TOKEN rule so
# the token accounting matches the tool_docs firewall and the product lint.
_SKILL_TOKEN_BUDGET = 1500

# The committed seed shipped as package data; named in ``landing_note`` so a
# human knows which file a landed proposal edits.
_SEED_RESOURCE = "usage_skill_seed.md"
_SEED_PATH = "benchmarks/src/pydocs_eval/optimize/artifacts/usage_skill_seed.md"


@artifact_registry.register("usage_skill")
@dataclass(frozen=True, slots=True)
class UsageSkillArtifact:
    """A candidate skill document teaching pydocs-mcp operation (spec §D6)."""

    name: str = "usage_skill"
    content: str | None = None

    def render(self) -> str:
        """Return the candidate text, or the committed seed when unseeded."""
        if self.content is not None:
            return self.content
        return files("pydocs_eval.optimize.artifacts").joinpath(_SEED_RESOURCE).read_text()

    def with_content(self, content: str) -> UsageSkillArtifact:
        """Return a copy carrying ``content`` as the candidate skill text."""
        return replace(self, content=content)

    def validate(self) -> tuple[str, ...]:
        """Return §D6 constraint violations; empty tuple == valid.

        The firewall the orchestrator checks before spending any fitness: the
        document must stay under the token cap and must still name every live
        tool (iterated from ``TOOL_DOCS`` keys — the single source of truth for
        the tool list, so this can never disagree with the MCP surface).
        """
        text = self.render()
        return (*_budget_violations(text), *_missing_tool_names(text))

    def landing_note(self) -> str:
        """Explain how a human lands a proposal from this artifact."""
        return (
            f"Apply the diff to {_SEED_PATH} by hand. This skill text feeds the "
            "evaluated agent through task_prompt(skill=...), so rerun the "
            "optimization preflight (--dry-run) to confirm the edited seed still "
            "validates before landing."
        )

    @property
    def fingerprint(self) -> str:
        """SHA-256 hex digest of the rendered skill text (64 chars)."""
        return hashlib.sha256(self.render().encode()).hexdigest()


def _budget_violations(text: str) -> tuple[str, ...]:
    tokens = len(text) // CHARS_PER_TOKEN
    if tokens > _SKILL_TOKEN_BUDGET:
        return (f"skill document {tokens} tokens > {_SKILL_TOKEN_BUDGET}",)
    return ()


def _missing_tool_names(text: str) -> tuple[str, ...]:
    return tuple(f"missing tool name {name!r}" for name in TOOL_DOCS if name not in text)
