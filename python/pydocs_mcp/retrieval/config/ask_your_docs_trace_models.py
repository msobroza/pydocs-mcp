"""The ``ask_your_docs.trace`` config sub-model — the chat page's opt-in trace persistence.

The switch decides whether the chat page launches its serve child traced (ADR 0009's three
``PYDOCS_TRACE__*`` names, overlaid per child) and persists every answered question as a
trajectory directory the eval readers open. It is NOT the server-side ``trace:`` block
(``TraceConfig``): that one is the child's recorder switch, and the page sets it through
the child's environment only. Default off, so a stock page is byte-identical to one without
the knob. Defaults are duplicated in ``defaults/default_config.yaml`` on purpose — the YAML
is the user-visible knob (CLAUDE.md §Default values).

Example:
    >>> ChatTraceConfig().enabled
    False
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChatTraceConfig(BaseModel):
    """The ``ask_your_docs.trace:`` block."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False)
    # "" = <cache dir>/chat-traces, so PYDOCS_CACHE_DIR moves the traces with the bundles;
    # a path here is user-expanded (~). A str, not a Path: the YAML/env round-trip stays text.
    dir: str = Field(default="")


__all__ = ("ChatTraceConfig",)
