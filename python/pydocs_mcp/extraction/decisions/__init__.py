"""Decision mining — deterministic sources feeding the capture stage (spec §D8).

Public surface: the :class:`DecisionSource` Protocol, its
:class:`CaptureContext` input and :class:`RawDecision` output value objects, and
the :data:`decision_source_registry` that names each source. Importing this
package eagerly imports ``.sources`` so the concrete sources register
themselves (mirrors how importing ``extraction.pipeline.stages`` populates
``stage_registry``).
"""

from __future__ import annotations

# Side-effect import: registers every concrete source on the registry above.
from pydocs_mcp.extraction.decisions import sources as _sources  # noqa: F401
from pydocs_mcp.extraction.decisions._types import (
    CaptureContext,
    DecisionEvidence,
    DecisionSource,
    RawDecision,
    decision_source_registry,
)

__all__ = [
    "CaptureContext",
    "DecisionEvidence",
    "DecisionSource",
    "RawDecision",
    "decision_source_registry",
]
