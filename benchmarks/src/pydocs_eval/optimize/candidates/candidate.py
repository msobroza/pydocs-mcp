"""GEPA-view candidate <-> Phase 1 delimited-document bridge (ADR 0017 R3).

gepa 0.1.4 pins ``Candidate = dict[str, str]`` (``core/adapter.py``) — a flat
mapping of named components to text. Phase 1's eleven canonical description
sections (``SERVER_INSTRUCTIONS`` + nine ``TOOL: <name>`` +
``SESSION_START_PREAMBLE``, ``description_source.CANONICAL_HEADERS``) map
BIJECTIVELY onto it: each section key is a GEPA component, and the dict view
buys per-component mutation + component-wise merge for free (ADR 0017
§Decision 1). This module is the bridge — render a candidate's section dict to
the product delimited document, parse one back, and compute the serve-truthful
artifact hash — all through the product's PUBLIC ``description_source`` grammar
so a candidate and a real serve can never drift.

``candidate_hash`` re-implements the product artifact-hash payload PUBLICLY
(``renderer:v{RENDERER_VERSION}`` + the normalized surface), the same
byte-for-byte re-implementation idiom the eval blob store uses over the
recorder's ``blobs/<sha256>`` convention (``trajectory/blob_store.py``): the
FORMAT is the contract, pinned by a parity test against the product's own
``current_artifact_hash`` / ``apply_source`` return value — NOT by importing
the private ``_artifact_hash`` here, so drift fails a test loudly instead of
silently binding to an internal symbol.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra

# Module-level ``pydocs_mcp`` boundary (the grammar IS the product's
# description-source grammar — there is no library-free way to bridge onto it),
# mirroring the sibling ``optimize/artifacts/_delimited.py`` import shape. A base
# install without the [retrieval] extra gets the actionable install hint instead
# of a bare ModuleNotFoundError.
try:
    from pydocs_mcp.application.description_source import (
        CANONICAL_HEADERS,
        RENDERER_VERSION,
        load_packaged,
        normalize,
        parse_sections,
        render_sections,
    )
except ImportError as exc:
    raise_missing_retrieval_extra(exc)

__all__ = ["CANONICAL_SECTION_KEYS", "Candidate"]

# The eleven GEPA component keys, in canonical document order. Re-exported from
# the product's single source so a section rename can never drift the bridge.
CANONICAL_SECTION_KEYS: tuple[str, ...] = CANONICAL_HEADERS


@dataclass(frozen=True, slots=True)
class Candidate:
    """A GEPA-view candidate: the eleven canonical description sections.

    ``sections`` is GEPA's native ``dict[str, str]`` view — each key is one of
    :data:`CANONICAL_SECTION_KEYS`, each value that section's text. Validity is
    NOT enforced here (the firewall owns that, ADR 0019): a malformed candidate
    still needs a section view so its violations can be recorded (R3).

    Example:
        >>> Candidate.seed().candidate_hash[:8]  # doctest: +SKIP
        'eeb66ef5'
    """

    sections: Mapping[str, str]

    @classmethod
    def from_gepa(cls, components: Mapping[str, str]) -> Candidate:
        """Build from GEPA's ``dict[str, str]`` component mapping (identity view)."""
        return cls(sections=dict(components))

    def to_gepa(self) -> dict[str, str]:
        """Return the GEPA-native component mapping (a fresh dict copy)."""
        return dict(self.sections)

    @classmethod
    def from_document(cls, text: str) -> Candidate:
        """Parse a delimited product document into the section-dict view.

        Uses the product's PERMISSIVE parse (no ``allowed`` set) — validity is
        the firewall's job, so an out-of-set header surfaces there as a
        collision rather than raising in the bridge.
        """
        return cls(sections=parse_sections(text))

    @classmethod
    def seed(cls) -> Candidate:
        """The Phase 1 seed candidate: the packaged description document (R8)."""
        return cls(sections=load_packaged())

    def render(self) -> str:
        """Render to the delimited product document (what Route A writes to disk)."""
        return render_sections(self.sections)

    def normalized(self) -> str:
        """The canonical byte surface — one product normalization pass."""
        return normalize(self.render())

    @property
    def candidate_hash(self) -> str:
        """Serve-truthful SHA-256 identity — equals a real serve's ``current_artifact_hash``.

        Re-implements the product artifact-hash payload publicly:
        ``renderer:v{RENDERER_VERSION}`` prefix + the normalized surface, which
        matches ``description_source._artifact_hash`` (and thus the trace
        header + campaign lockfile ``artifact_hash``) byte for byte. A parity
        test pins that equality against the product's ``apply_source`` return
        value, so a payload-format change fails loudly here.
        """
        payload = f"renderer:v{RENDERER_VERSION}\n{self.normalized()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
