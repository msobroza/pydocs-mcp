"""The ``tool_docs`` optimizable artifact (spec §D2, §D2a, §D13).

Renders the product's live ``TOOL_DOCS`` + ``SERVER_INSTRUCTIONS`` as the
shared delimited document, parses a candidate back with ``with_content``, and
runs the §D13 rules in ``validate()`` against the SAME importable constants the
product lint uses (``pydocs_mcp.application.tool_docs``) so the firewall and the
lint can never disagree. ``landing_note()`` points a human at the product file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from pydocs_mcp.application.tool_docs import (
    CHARS_PER_TOKEN,
    PER_TOOL_TOKEN_BUDGET,
    REQUIRED_MARKERS,
    SERVER_INSTRUCTIONS,
    TOOL_DOCS,
    TOTAL_TOKEN_BUDGET,
)

from pydocs_eval.optimize.artifacts._delimited import (
    find_header_collisions,
    parse_delimited,
    render_delimited,
)
from pydocs_eval.optimize.registries import artifact_registry

# WHY: the section key for a tool is its delimited-format header group
# (``TOOL: <name>``); building it in one place keeps render/validate aligned.
_TOOL_KEY = "TOOL: {name}"
_SERVER_KEY = "SERVER_INSTRUCTIONS"

# The product file a landed proposal edits; named in ``landing_note`` so the
# human knows exactly where the diff applies and to rerun the §D13 lint after.
_PRODUCT_PATH = "python/pydocs_mcp/application/tool_docs.py"


@artifact_registry.register("tool_docs")
@dataclass(frozen=True, slots=True)
class ToolDocsArtifact:
    """A candidate ``TOOL_DOCS`` + ``SERVER_INSTRUCTIONS`` surface (spec §D2)."""

    name: str = "tool_docs"
    content: str | None = None

    def render(self) -> str:
        """Return the candidate text, or the live product surface when unseeded."""
        if self.content is not None:
            return self.content
        sections = {_SERVER_KEY: SERVER_INSTRUCTIONS}
        for tool_name, doc in TOOL_DOCS.items():
            sections[_TOOL_KEY.format(name=tool_name)] = doc
        return render_delimited(sections)

    def with_content(self, content: str) -> ToolDocsArtifact:
        """Return a copy carrying ``content`` as the candidate surface."""
        return replace(self, content=content)

    def validate(self) -> tuple[str, ...]:
        """Return §D2a + §D13 constraint violations; empty tuple == valid.

        Runs the firewall the orchestrator checks before spending any fitness:
        the document must round-trip, carry exactly the six live tools in order,
        keep every required §D13 marker, and stay under the token budgets — all
        against the same constants the product lint imports (zero drift).
        """
        sections = parse_delimited(self.render())
        expected = [_TOOL_KEY.format(name=n) for n in TOOL_DOCS]
        allowed = (_SERVER_KEY, *expected)
        return (
            *find_header_collisions(sections, allowed=allowed),
            *_structure_violations(sections, expected),
            *_marker_violations(sections, expected),
            *_budget_violations(sections),
        )

    def landing_note(self) -> str:
        """Explain how a human lands a proposal from this artifact."""
        return (
            f"Apply the diff to {_PRODUCT_PATH} by hand, then rerun the §D13 "
            "lint (tests/application/test_tool_docs_lint.py) to confirm the "
            "edited surface still passes."
        )

    @property
    def fingerprint(self) -> str:
        """SHA-256 hex digest of the rendered surface (64 chars)."""
        return hashlib.sha256(self.render().encode()).hexdigest()


def _structure_violations(sections: dict[str, str], expected: list[str]) -> tuple[str, ...]:
    # Unexpected/phantom headers are reported by ``find_header_collisions``; here
    # we only flag missing sections and out-of-order tools among the ones present.
    violations = [] if _SERVER_KEY in sections else [f"missing section {_SERVER_KEY}"]
    violations += [f"missing tool section {key!r}" for key in expected if key not in sections]
    present = [key for key in sections if key in expected]
    if not violations and present != expected:
        violations.append(f"tool order {present} != expected {expected}")
    return tuple(violations)


def _marker_violations(sections: dict[str, str], expected: list[str]) -> tuple[str, ...]:
    return tuple(
        f"{key!r} missing §D13 marker {marker!r}"
        for key in expected
        if key in sections
        for marker in REQUIRED_MARKERS
        if marker not in sections[key]
    )


def _budget_violations(sections: dict[str, str]) -> tuple[str, ...]:
    violations: list[str] = []
    total = 0
    for key, content in sections.items():
        tokens = len(content) // CHARS_PER_TOKEN
        total += tokens
        if tokens > PER_TOOL_TOKEN_BUDGET:
            violations.append(f"{key!r}: {tokens} tokens > {PER_TOOL_TOKEN_BUDGET}")
    if total > TOTAL_TOKEN_BUDGET:
        violations.append(f"surface total {total} tokens > {TOTAL_TOKEN_BUDGET}")
    return tuple(violations)
