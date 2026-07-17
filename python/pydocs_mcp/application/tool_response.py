"""ToolResponse + the per-tool structured envelope models (contract §2/§3).

:class:`ToolResponse` is the surface-neutral result every ``ToolRouter``
method returns: the enveloped markdown ``text`` (byte-identical to the
pre-0.6.0 string output), the machine-readable ``items`` rows, and the
``meta`` attribution block. The pydantic models mirror the items[] field
sets of ``docs/tool-contracts.md`` §3 byte-for-byte; they exist to
(a) validate ``structuredContent`` before it leaves the server and
(b) advertise each tool's ``outputSchema`` at registration
(``server.py`` attaches them as the handlers' return-annotation metadata).

Field nullability: ``path``/``start_line``/``end_line`` are nullable on the
index-backed tools because the data may be absent per row (member rows
without a resolvable tree node, decision rows whose locators stay in
``get_why``). The filesystem tools (``grep``/``glob``/``read_file``) always
know their path and span, so theirs are required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class ToolResponse:
    """One tool response in both wire forms (contract §2.1)."""

    text: str
    items: tuple[dict[str, Any], ...]
    meta: dict[str, Any]

    def structured(self) -> dict[str, Any]:
        """The JSON-ready ``{"text", "items", "meta"}`` envelope object."""
        return {"text": self.text, "items": list(self.items), "meta": dict(self.meta)}


class MetaModel(BaseModel):
    """Shared ``meta`` block (contract §2.1)."""

    tool: str
    project: str
    indexed_git_head: str | None
    live_git_head: str | None
    index_stale: bool
    truncated: bool


class ReferencesMetaModel(MetaModel):
    """§2.2 — declared reference-resolution capability, ``get_references`` only."""

    resolution: str | None = None


class OverviewItem(BaseModel):
    """§3.1 — module-map rows."""

    kind: str
    id: str
    qualified_name: str
    path: str | None = None


class SearchItem(BaseModel):
    """§3.2 — ranked chunk / member / decision rows."""

    kind: str
    id: str
    qualified_name: str
    package: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    score: float


class SymbolItem(BaseModel):
    """§3.3 — document-tree nodes (contract names, not pageindex keys)."""

    node_id: str
    kind: str
    qualified_name: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class ContextItem(BaseModel):
    """§3.4 — one row per resolved context target."""

    qualified_name: str
    kind: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class ReferenceItem(BaseModel):
    """§3.5 — one graph edge; path/span from the resolvable defining node."""

    from_qualified_name: str
    to_qualified_name: str
    kind: str
    direction: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class WhyItem(BaseModel):
    """§3.6 — one mined decision record."""

    decision_id: int
    title: str
    status: str
    locators: list[str]
    affected_files: list[str]


class GrepItem(BaseModel):
    """§3.7 — one match (``start_line == end_line`` unless multiline)."""

    path: str
    start_line: int
    end_line: int
    text: str


class GlobItem(BaseModel):
    """§3.8 — one matched path (mtime-descending order)."""

    path: str
    mtime: float


class ReadFileItem(BaseModel):
    """§3.9 — the returned line span."""

    path: str
    start_line: int
    end_line: int


class OverviewEnvelope(BaseModel):
    text: str
    items: list[OverviewItem]
    meta: MetaModel


class SearchEnvelope(BaseModel):
    text: str
    items: list[SearchItem]
    meta: MetaModel


class SymbolEnvelope(BaseModel):
    text: str
    items: list[SymbolItem]
    meta: MetaModel


class ContextEnvelope(BaseModel):
    text: str
    items: list[ContextItem]
    meta: MetaModel


class ReferencesEnvelope(BaseModel):
    text: str
    items: list[ReferenceItem]
    meta: ReferencesMetaModel


class WhyEnvelope(BaseModel):
    text: str
    items: list[WhyItem]
    meta: MetaModel


class GrepEnvelope(BaseModel):
    text: str
    items: list[GrepItem]
    meta: MetaModel


class GlobEnvelope(BaseModel):
    text: str
    items: list[GlobItem]
    meta: MetaModel


class ReadFileEnvelope(BaseModel):
    text: str
    items: list[ReadFileItem]
    meta: MetaModel


# Single source for "which envelope model validates which tool's structured
# output" — registration and tests both key off the frozen nine names.
ENVELOPE_MODELS: dict[str, type[BaseModel]] = {
    "get_overview": OverviewEnvelope,
    "search_codebase": SearchEnvelope,
    "get_symbol": SymbolEnvelope,
    "get_context": ContextEnvelope,
    "get_references": ReferencesEnvelope,
    "get_why": WhyEnvelope,
    "grep": GrepEnvelope,
    "glob": GlobEnvelope,
    "read_file": ReadFileEnvelope,
}
