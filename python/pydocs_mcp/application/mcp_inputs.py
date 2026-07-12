"""Pydantic input models for MCP tools (sub-PR #6 §4.3).

Enforces format via regex + protocol-safety caps. Limits are permissive:
query up to 30k chars, limit up to 1000 — covers runaway clients without
rejecting legit edge cases.

Per CLAUDE.md §"MCP API surface vs YAML configuration": pipeline tunables
live in YAML, NOT on the MCP tool surface. The one allowed exception is
input-shape validators on these models (e.g., ``LookupInput.limit`` /
``SearchInput.limit`` defaults and ceilings), which are deployment-time
bounds, not feature toggles. ``configure_from_app_config`` is the single
wire that pushes the YAML-loaded ``AppConfig`` into the module-level
slots those validators read at runtime.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    # Type-only import — keeps the runtime ``application -> retrieval`` edge
    # lazy (the actual ``cfg.reference_graph`` / ``cfg.search`` attribute
    # reads in ``configure_from_app_config`` happen on whatever the caller
    # passes, validated structurally via ``_ConfigShape``). Avoids the
    # circular-import risk called out in the pre-refactor docstring.
    from pydocs_mcp.retrieval.config import ReferenceGraphConfig, SearchConfig
    from pydocs_mcp.retrieval.config.models import SymbolSourceConfig

# Format validators — reject malformed input at the boundary.
_PACKAGE_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9._-]*[a-zA-Z0-9])?|__project__)$"
)  # alphanumeric start AND end (rejects trailing dot/dash); dots/dashes/underscores allowed in middle
_TARGET_RE = re.compile(
    r"^(?:[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)?$"
)  # empty or dotted-identifier chain; rejects foo..bar, foo., leading digit
_WHY_TARGET_RE = re.compile(
    r"^[A-Za-z0-9_.\-/]+$"
)  # get_why targets are documented as PATH|QNAME (DecisionService._classify_target
# branches on '/'), so '/' is admitted here — unlike _TARGET_RE, which guards the
# symbol/context tools. ':' and ']' stay forbidden: they are the only characters
# that corrupt the [[next:…]] pointer-token grammar (application/formatting.py).

# Module-level slots — installed by ``configure_from_app_config`` at
# server / CLI startup. The initial values match the shipped
# ``default_config.yaml`` (``reference_graph.output.default_limit=50``,
# ``max_limit=1000`` for LookupInput; ``search.output.default_limit=10``,
# ``max_limit=1000`` for SearchInput) so the models behave correctly even
# if ``configure_from_app_config`` is never called (e.g., direct unit
# tests that just instantiate ``LookupInput`` / ``SearchInput``).
#
# Why module-level globals instead of class-level defaults? Pydantic
# resolves field defaults / validator constraints at class definition,
# which happens at import time — long before ``AppConfig.load`` runs. A
# ``default_factory=lambda: _LIMIT_DEFAULT`` and a validator that reads
# ``_LIMIT_MAX`` inside its body re-read the slots on every model
# instantiation, so a single ``configure_from_app_config`` call at startup
# is enough to make every subsequent ``LookupInput(...)`` /
# ``SearchInput(...)`` validate against the YAML-supplied bounds.
_LIMIT_DEFAULT: int = 50
_LIMIT_MAX: int = 1000
# SearchInput's historical default is 10 (not 50) — distinct knob from
# LookupInput. Both pairs are tunable via separate YAML sub-models
# (``reference_graph.output.*`` vs ``search.output.*``); deployments can
# adjust one without the other.
_SEARCH_LIMIT_DEFAULT: int = 10
_SEARCH_LIMIT_MAX: int = 1000
# get_symbol(depth="source") verbatim line cap — installed from
# ``cfg.symbol_source.max_lines`` by ``configure_from_app_config`` at
# startup. Initial literal matches the shipped ``default_config.yaml``
# (``symbol_source.max_lines: 400``) so direct construction of
# ``SymbolSourceService`` behaves correctly if ``configure_from_app_config``
# is never called (same rationale as the limit slots above — importing the
# canonical config constant here would invert the application -> retrieval
# layering). The per-project ``build_sqlite_symbol_source_service`` factory
# reads this slot for its fallback when no ``config`` is passed.
_SYMBOL_SOURCE_MAX_LINES: int = 400


@runtime_checkable
class _ConfigShape(Protocol):
    """Structural shape of the YAML-loaded ``AppConfig`` that
    :func:`configure_from_app_config` reads (I11).

    Replaces the previous ``cfg: Any`` parameter with a typed Protocol
    that documents exactly which cfg sub-trees the function consumes —
    ``reference_graph`` (for ``capture`` / ``resolver`` / ``output``
    sub-models), ``search`` (for the ``search.output`` bounds), and
    ``symbol_source`` (for the get_symbol(depth="source") line cap).

    The Protocol is ``@runtime_checkable`` so unit tests can structurally
    verify any duck-typed config carrier satisfies the shape via
    ``isinstance(cfg, _ConfigShape)``. The real ``AppConfig`` (defined in
    :mod:`pydocs_mcp.retrieval.config`) satisfies this Protocol nominally
    — no nominal subclassing required.

    Module-private (``_ConfigShape``) on purpose: external callers should
    pass a real ``AppConfig`` instance from
    :class:`pydocs_mcp.retrieval.config.AppConfig`. The Protocol exists
    only to document the structural contract and avoid a hard runtime
    dependency on ``AppConfig`` at the type level (which would create a
    circular import between ``application`` and ``retrieval``).
    """

    reference_graph: ReferenceGraphConfig
    search: SearchConfig
    symbol_source: SymbolSourceConfig


def configure_from_app_config(cfg: _ConfigShape) -> None:
    """Install YAML-loaded settings into the module-level slots this
    package reads at runtime.

    Called ONCE at server / CLI startup (see ``server.py::run`` and
    ``__main__.py::_cmd_*``). The parameter is typed against
    :class:`_ConfigShape` — a structural Protocol covering the two cfg
    sub-trees this function reads (``reference_graph`` and ``search``).
    Stamp coupling is gone: callers no longer pass an untyped ``Any``;
    static type-checkers can verify the contract.

    Four slots are updated:

    1. ``_LIMIT_DEFAULT`` / ``_LIMIT_MAX`` here in ``mcp_inputs`` — read
       by ``LookupInput.limit`` (default + ceiling).
    2. ``_SEARCH_LIMIT_DEFAULT`` / ``_SEARCH_LIMIT_MAX`` here in
       ``mcp_inputs`` — read by ``SearchInput.limit`` (default + ceiling).
       Separate slot pair so deployments can tune search and lookup
       limits independently.
    3. ``_SYMBOL_SOURCE_MAX_LINES`` here in ``mcp_inputs`` — the
       get_symbol(depth="source") verbatim line cap, read by the
       per-project ``build_sqlite_symbol_source_service`` factory as its
       no-config fallback.
    4. ``_CAPTURE_CONFIG`` in ``extraction.pipeline.stages`` — read by
       ``ReferenceCaptureStage`` to gate capture on/off and pick which
       reference kinds to emit. Pushed via ``_set_capture_config`` so the
       stage module owns its own slot (no cross-package mutation).
    """
    global _LIMIT_DEFAULT, _LIMIT_MAX
    global _SEARCH_LIMIT_DEFAULT, _SEARCH_LIMIT_MAX
    global _SYMBOL_SOURCE_MAX_LINES

    output = cfg.reference_graph.output
    _LIMIT_DEFAULT = output.default_limit
    _LIMIT_MAX = output.max_limit

    search_output = cfg.search.output
    _SEARCH_LIMIT_DEFAULT = search_output.default_limit
    _SEARCH_LIMIT_MAX = search_output.max_limit

    _SYMBOL_SOURCE_MAX_LINES = cfg.symbol_source.max_lines

    # Local import — keeps the application -> extraction edge lazy so
    # importing ``mcp_inputs`` at app startup doesn't drag in the whole
    # extraction pipeline (or fight with the existing import order).
    from pydocs_mcp.extraction.pipeline.stages import (
        _set_capture_config,
        _set_similar_config,
    )

    _set_capture_config(cfg.reference_graph.capture)
    # Synthetic kNN 'similar' edge generation (off by default) — same
    # module-level-slot push as the capture config above.
    _set_similar_config(cfg.reference_graph.similar_edges)

    # AC #15 stdlib-idx: push resolver config so IndexingService picks up
    # the include_stdlib toggle on next reindex. Parity with the capture
    # config push above — same module-level slot pattern.
    from pydocs_mcp.extraction.strategies.stdlib_qnames import (
        _set_resolver_config,
    )

    _set_resolver_config(cfg.reference_graph.resolver)


class SearchInput(BaseModel):
    """Input for the ``search_codebase`` MCP tool."""

    query: str = Field(min_length=1, max_length=30000)
    kind: Literal["docs", "api", "any", "decision"] = "any"
    package: str = ""
    scope: Literal["project", "deps", "all"] = "all"
    # Multi-repo corpus selector (sibling of ``package`` / ``scope``): restrict
    # the query to one loaded project by name. "" = union across all loaded
    # projects. No effect on a single-project server.
    project: str = ""
    # ``limit`` bounds the chunk-result count. Both the default and the
    # upper ceiling are driven by YAML (``search.output.default_limit`` /
    # ``max_limit``), pushed into module-level slots by
    # ``configure_from_app_config`` at server / CLI startup — parity with
    # ``LookupInput.limit`` (post-#5c). ``default_factory`` re-reads the
    # slot on every instantiation, and the ``@field_validator`` reads the
    # ceiling inside its body, so the model picks up YAML changes without
    # a re-import.
    limit: int = Field(default_factory=lambda: _SEARCH_LIMIT_DEFAULT, ge=1)

    @field_validator("limit")
    @classmethod
    def _check_limit_max(cls, v: int) -> int:
        # Read ``_SEARCH_LIMIT_MAX`` at call time so YAML reloads (or test
        # overrides) take effect on every ``SearchInput(...)`` rather than
        # being frozen at class-definition time.
        if v > _SEARCH_LIMIT_MAX:
            raise ValueError(
                f"limit must be <= {_SEARCH_LIMIT_MAX} (configured via search.output.max_limit)"
            )
        return v

    @field_validator("package")
    @classmethod
    def _check_package(cls, v: str) -> str:
        if v and not _PACKAGE_RE.match(v):
            raise ValueError("package must match ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ or be '__project__'")
        return v

    @field_validator("project")
    @classmethod
    def _check_project(cls, v: str) -> str:
        if v and not _PACKAGE_RE.match(v):
            raise ValueError("project must match ^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
        return v


class LookupInput(BaseModel):
    """Internal routing input for the deprecated ``lookup`` CLI verb — the
    MCP surface exposes ``get_symbol`` / ``get_references`` instead."""

    target: str = ""
    show: Literal[
        "default",
        "tree",
        "callers",
        "callees",
        "inherits",
        "impact",
        "context",
        "governed_by",
    ] = "default"
    # Multi-repo corpus selector: resolve the target inside one loaded project by
    # name. "" = resolve across all loaded projects (most-recent first). No effect
    # on a single-project server.
    project: str = ""
    # ``limit`` bounds reference-graph output (callers/callees/inherits).
    # The default and the upper ceiling are BOTH driven by YAML
    # (``reference_graph.output.default_limit`` / ``max_limit``), pushed
    # into module-level slots by ``configure_from_app_config`` at server /
    # CLI startup. ``default_factory`` re-reads the slot on every
    # instantiation, and the ``@field_validator`` reads the ceiling inside
    # its body, so the model picks up YAML changes without a re-import.
    limit: int = Field(default_factory=lambda: _LIMIT_DEFAULT, ge=1)

    @field_validator("target")
    @classmethod
    def _check_target(cls, v: str) -> str:
        if v and not _TARGET_RE.match(v):
            raise ValueError(
                "target must be a dotted identifier like 'pkg.mod.Class.method' or empty"
            )
        return v

    @field_validator("project")
    @classmethod
    def _check_project(cls, v: str) -> str:
        if v and not _PACKAGE_RE.match(v):
            raise ValueError("project must match ^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
        return v

    @field_validator("limit")
    @classmethod
    def _check_limit_max(cls, v: int) -> int:
        # Read ``_LIMIT_MAX`` at call time so YAML reloads (or test
        # overrides) take effect on every ``LookupInput(...)`` rather than
        # being frozen at class-definition time.
        if v > _LIMIT_MAX:
            raise ValueError(
                f"limit must be <= {_LIMIT_MAX} (configured via reference_graph.output.max_limit)"
            )
        return v


# ── Task-shaped tool inputs (spec §D1) ──────────────────────────────────
#
# The six task-shaped tools reuse the same YAML-wired limit slots and the
# same ``_PACKAGE_RE`` / ``_TARGET_RE`` boundary validators as
# ``SearchInput`` / ``LookupInput`` above. ``project`` carries the
# identical multi-repo corpus-selector semantics on every model that has it.


class OverviewInput(BaseModel):
    """get_overview — orientation card scope (spec §D1/§D17)."""

    package: str = ""
    project: str = ""

    @field_validator("package")
    @classmethod
    def _check_package(cls, v: str) -> str:
        if v and not _PACKAGE_RE.match(v):
            raise ValueError("package must match ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ or be '__project__'")
        return v

    @field_validator("project")
    @classmethod
    def _check_project(cls, v: str) -> str:
        if v and not _PACKAGE_RE.match(v):
            raise ValueError("project must match ^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
        return v


class SymbolInput(BaseModel):
    """get_symbol — known dotted path (spec §D1). depth='source' is the §D7 recovery contract."""

    target: str = Field(min_length=1)
    depth: Literal["summary", "tree", "source"] = "summary"
    project: str = ""

    @field_validator("target")
    @classmethod
    def _check_target(cls, v: str) -> str:
        if not _TARGET_RE.match(v):
            raise ValueError("target must be a dotted identifier like 'pkg.mod.Class.method'")
        return v

    @field_validator("project")
    @classmethod
    def _check_project(cls, v: str) -> str:
        if v and not _PACKAGE_RE.match(v):
            raise ValueError("project must match ^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
        return v


class ContextInput(BaseModel):
    """get_context — batched targets under one shared budget (spec §D1)."""

    targets: list[str] = Field(min_length=1, max_length=20)
    project: str = ""

    @field_validator("targets")
    @classmethod
    def _check_targets(cls, v: list[str]) -> list[str]:
        # Each item must be a non-empty dotted identifier, mirroring
        # SymbolInput/ReferencesInput.target — an unvalidated item gets
        # interpolated into "[[...]]" pointer tokens downstream
        # (formatting.py), so ":" / "]]" here would corrupt the grammar.
        for item in v:
            if not item or not _TARGET_RE.match(item):
                raise ValueError(
                    "each target must be a dotted identifier like 'pkg.mod.Class.method'"
                )
        return v

    @field_validator("project")
    @classmethod
    def _check_project(cls, v: str) -> str:
        if v and not _PACKAGE_RE.match(v):
            raise ValueError("project must match ^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
        return v


class ReferencesInput(BaseModel):
    """get_references — graph traversal incl. ranked transitive impact (spec §D1)."""

    target: str = Field(min_length=1)
    direction: Literal["callers", "callees", "inherits", "impact", "governed_by"] = "callers"
    project: str = ""
    limit: int = Field(default_factory=lambda: _LIMIT_DEFAULT, ge=1)

    @field_validator("target")
    @classmethod
    def _check_target(cls, v: str) -> str:
        if not _TARGET_RE.match(v):
            raise ValueError("target must be a dotted identifier like 'pkg.mod.Class.method'")
        return v

    @field_validator("project")
    @classmethod
    def _check_project(cls, v: str) -> str:
        if v and not _PACKAGE_RE.match(v):
            raise ValueError("project must match ^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
        return v

    @field_validator("limit")
    @classmethod
    def _check_limit_max(cls, v: int) -> int:
        # Read ``_LIMIT_MAX`` at call time so YAML reloads (or test
        # overrides) take effect on every ``ReferencesInput(...)`` rather
        # than being frozen at class-definition time — mirrors LookupInput.
        if v > _LIMIT_MAX:
            raise ValueError(
                f"limit must be <= {_LIMIT_MAX} (configured via reference_graph.output.max_limit)"
            )
        return v


class WhyInput(BaseModel):
    """get_why — decision search / per-target governing decisions / dashboard (spec §D11)."""

    query: str = ""
    targets: list[str] | None = Field(None, min_length=1, max_length=20)
    project: str = ""

    @field_validator("targets")
    @classmethod
    def _check_targets(cls, v: list[str] | None) -> list[str] | None:
        # Unlike ContextInput, why-targets are documented as PATH|QNAME and
        # DecisionService._classify_target has a '/'-path branch — so this
        # validates against _WHY_TARGET_RE (admits '/'), keeping only the
        # pointer-grammar-hostile ':' / ']' rejected.
        if v is None:
            return v
        for item in v:
            if not item or not _WHY_TARGET_RE.match(item):
                raise ValueError(
                    "each target must be a dotted name like 'pkg.mod.Class' or a path like 'a/b.py'"
                )
        return v

    @field_validator("project")
    @classmethod
    def _check_project(cls, v: str) -> str:
        if v and not _PACKAGE_RE.match(v):
            raise ValueError("project must match ^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
        return v
