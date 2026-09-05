"""The ``tool_docs`` optimizable artifact (spec §D2, §D2a, §D13).

Renders the product's live ``TOOL_DOCS`` + ``SERVER_INSTRUCTIONS`` as the
shared delimited document, parses a candidate back with ``with_content``, and
screens it through the ONE view-parameterized validity firewall
(``candidates/firewall.py``) under :data:`~pydocs_eval.optimize.candidates.
firewall.OVERLAY_UNIVERSE` — the ten-header subset this overlay optimizes
(``SERVER_INSTRUCTIONS`` + nine ``TOOL: <name>``; ``SESSION_START_PREAMBLE`` is
injected by the overlay bridge downstream, ADR 0019 §Amendment 2026-07-20). The
firewall runs the SAME product ``validate_sections`` the §D13 lint imports, so
the firewall and the lint can never disagree. ``landing_note()`` points a human
at the product file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra

# Module-level ``pydocs_mcp`` boundary: ``render()`` seeds from the live product
# surface, so this artifact is inherently library-coupled. A base install without
# the [retrieval] extra gets the actionable install hint instead of a bare
# ModuleNotFoundError.
try:
    from pydocs_mcp.application.tool_docs import SERVER_INSTRUCTIONS, TOOL_DOCS
except ImportError as exc:
    raise_missing_retrieval_extra(exc)

from pydocs_eval.optimize.artifacts._delimited import render_delimited
from pydocs_eval.optimize.candidates.firewall import OVERLAY_UNIVERSE, firewall_violations
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

        Delegates to the ONE view-parameterized firewall under the overlay
        universe (``OVERLAY_UNIVERSE``): the document must round-trip, carry
        exactly the nine live tools in canonical order, keep every required §D13
        marker, and stay under the product token budgets (the nine TOOL sections
        only — ``SERVER_INSTRUCTIONS`` is budget-exempt, EXACT product parity).
        Never raises; the firewall catches the product's strict-parse errors into
        the violations tuple.
        """
        return firewall_violations(self.render(), universe=OVERLAY_UNIVERSE)

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
