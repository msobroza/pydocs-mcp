"""The ``ask_prompt`` optimizable artifact (spec §3.2.1).

A delimited two-section document — the ask agent's system prompt and the
follow-up-reformulation (rewrite) template — in the SAME shared delimited
grammar every text artifact speaks (``_delimited``), so the existing
optimizers drive it unchanged. The rewrite section is a ``str.format``
template with literal ``{history}`` / ``{question}`` placeholders — exactly
what ``reformulate(rewrite_template=…)`` consumes.

Unseeded, it renders the LIVE product prompts (zero drift by construction,
the ``tool_docs`` precedent); the committed ``ask_prompt_seed.md`` package
data is the reviewable copy, pinned byte-for-byte by the AC-4 regeneration
test.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra

# Module-level ``pydocs_mcp`` boundary: the six-tool-name check iterates the
# product TOOL_DOCS keys and the seed IS the product prompt templates — there
# is no library-free way to define this artifact. A base install without the
# [retrieval] extra gets the actionable install hint.
try:
    from pydocs_mcp.application.tool_docs import CHARS_PER_TOKEN, TOOL_DOCS
    from pydocs_mcp.ask_your_docs.prompts import SYSTEM_PROMPT, render_shared
except ImportError as exc:
    raise_missing_retrieval_extra(exc)

from pydocs_eval.optimize.artifacts._delimited import (
    find_header_collisions,
    parse_delimited,
    render_delimited,
)
from pydocs_eval.optimize.registries import artifact_registry

_SYSTEM_KEY = "SYSTEM_PROMPT"
_REWRITE_KEY = "REWRITE_PROMPT"
_SECTION_ORDER = (_SYSTEM_KEY, _REWRITE_KEY)

# Section token budgets (single sources; sized with headroom over today's
# prompts — ≈650 and ≈60 tokens at the shared CHARS_PER_TOKEN rule). Their job
# is to stop the optimizer inflating the searchable region; the catalog and
# architecture-appended layers are outside the artifact and need no budget.
_ASK_SYSTEM_TOKEN_BUDGET = 1200
_ASK_REWRITE_TOKEN_BUDGET = 300
_BUDGETS = {_SYSTEM_KEY: _ASK_SYSTEM_TOKEN_BUDGET, _REWRITE_KEY: _ASK_REWRITE_TOKEN_BUDGET}

# Where a landed proposal applies; named in ``landing_note``.
_PRODUCT_PROMPTS_DIR = "python/pydocs_mcp/ask_your_docs/prompts/shared/"


def _seed_sections() -> dict[str, str]:
    """The live product prompts as artifact sections.

    The rewrite seed renders the shipped Jinja template with LITERAL
    ``{history}`` / ``{question}`` placeholders, converting it to the
    ``str.format`` shape the candidate axis edits and ``reformulate``
    consumes.
    """
    return {
        _SYSTEM_KEY: SYSTEM_PROMPT,
        _REWRITE_KEY: render_shared("rewrite_v1", history="{history}", question="{question}"),
    }


@artifact_registry.register("ask_prompt")
@dataclass(frozen=True, slots=True)
class AskPromptArtifact:
    """A candidate (system prompt, rewrite template) pair (spec §3.2.1)."""

    name: str = "ask_prompt"
    content: str | None = None

    def render(self) -> str:
        """Return the candidate text, or the live product prompts when unseeded."""
        if self.content is not None:
            return self.content
        return render_delimited(_seed_sections())

    def with_content(self, content: str) -> AskPromptArtifact:
        """Return a copy carrying ``content`` as the candidate document."""
        return replace(self, content=content)

    def validate(self) -> tuple[str, ...]:
        """Return constraint violations; empty tuple == valid (never raises).

        Both sections present exactly once and in order, non-empty, inside
        their token budgets, and the system section names all live tools —
        iterated from ``TOOL_DOCS`` keys so a surface change breaks loudly.
        """
        text = self.render()
        sections = parse_delimited(text)
        return (
            *find_header_collisions(sections, allowed=_SECTION_ORDER),
            *_structure_violations(text, sections),
            *_content_violations(sections),
        )

    def landing_note(self) -> str:
        """Explain how a human lands a proposal from this artifact."""
        return (
            f"Ship the system section as a new _vN+1 template under "
            f"{_PRODUCT_PROMPTS_DIR} (never edit a shipped _vN) and the "
            "rewrite section as the matching rewrite template, then rerun "
            "tests/ask_your_docs/test_prompts_package.py."
        )

    @property
    def fingerprint(self) -> str:
        """SHA-256 hex digest of the rendered document (64 chars)."""
        return hashlib.sha256(self.render().encode()).hexdigest()

    def system_prompt(self) -> str:
        """The candidate system section (feeds ``AskPrompts.system_prompt``)."""
        return parse_delimited(self.render()).get(_SYSTEM_KEY, "")

    def rewrite_template(self) -> str:
        """The candidate rewrite section (feeds ``reformulate(rewrite_template=…)``)."""
        return parse_delimited(self.render()).get(_REWRITE_KEY, "")


def _structure_violations(text: str, sections: dict[str, str]) -> tuple[str, ...]:
    violations: list[str] = []
    for key in _SECTION_ORDER:
        occurrences = text.count(f"=== {key} ===")
        if occurrences == 0:
            violations.append(f"missing section {key!r}")
        elif occurrences > 1:
            violations.append(f"section {key!r} must appear exactly once, found {occurrences}")
    present = [key for key in sections if key in _SECTION_ORDER]
    if not violations and present != list(_SECTION_ORDER):
        violations.append(f"section order {present} != expected {list(_SECTION_ORDER)}")
    return tuple(violations)


def _content_violations(sections: dict[str, str]) -> tuple[str, ...]:
    violations: list[str] = []
    for key in _SECTION_ORDER:
        content = sections.get(key)
        if content is None:
            continue
        if not content.strip():
            violations.append(f"section {key!r} is empty")
        tokens = len(content) // CHARS_PER_TOKEN
        if tokens > _BUDGETS[key]:
            violations.append(f"section {key!r}: {tokens} tokens > {_BUDGETS[key]}")
    system = sections.get(_SYSTEM_KEY, "")
    violations += [
        f"system section does not name tool {tool_name!r}"
        for tool_name in TOOL_DOCS
        if tool_name not in system
    ]
    return tuple(violations)
