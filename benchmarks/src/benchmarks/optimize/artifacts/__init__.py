"""Optimizable text artifacts (spec §D2). Each artifact lives in its own
module; importing this package fires the ``@artifact_registry.register``
decorators on every concrete artifact so callers can look them up by name.

The shared delimited format lives in ``_delimited`` so ``render``/
``with_content`` and the §D6 overlay speak one grammar.
"""

from __future__ import annotations

from .tool_docs import ToolDocsArtifact

__all__ = [
    "ToolDocsArtifact",
]
