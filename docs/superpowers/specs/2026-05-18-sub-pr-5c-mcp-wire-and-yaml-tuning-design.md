---
status: working-draft
shipped-in: pending
last-reviewed: 2026-05-18
original-draft: 2026-05-18
depends-on: PR #21 (sub-PR #5b) merged at commit 331055b
---

# Sub-PR #5c — Reference-graph MCP wire + YAML tuning

**Date:** 2026-05-18.
**Depends on:** PR #21 (sub-PR #5b). All capture/storage/resolver/service infrastructure already lives on `main`.
**Architectural rule it enforces:** CLAUDE.md §"MCP API surface vs YAML configuration" — pipeline behavior toggles never become MCP tool parameters.

---

## 1. Goal

Flip `lookup(target=X, show="callers"|"callees"|"inherits")` from `ServiceUnavailableError` to actual rendered rows. MCP tool **signatures unchanged**; behavior tuned via YAML.

This is the user-visible payoff for the entire #5b reference-graph trilogy.

---

## 2. The architectural rule (committed first)

CLAUDE.md gains §"MCP API surface vs YAML configuration":

- Pipeline / feature / behavior settings — capture toggles, resolver thresholds, retrieval limits, output bounds, kinds-to-emit — MUST be configured via YAML loaded through `AppConfig`, NEVER as new MCP tool parameters.
- The MCP tool surface is FIXED at two tools: `search(query, kind, ...)` and `lookup(target, show, ...)`. New features land **behind** the existing surface via YAML + internal composition.
- One allowed exception: *input-shape validators* on the existing MCP input models (e.g., `LookupInput.limit: int = Field(50, ge=1, le=1000)`). These constrain a single client request's bounds and are client-driven, not feature toggles.
- Rationale: MCP client stability, experiment tracking + benchmark evaluation, per-deployment tuning.

This rule is the first commit of the PR and the contract every subsequent file change in this PR honors.

---

## 3. Scope

### In scope

1. **Project-qname-prefix fix** — `python.pydocs_mcp.X` → `pydocs_mcp.X`. Single-file change in extraction discovery; bumps AC #15 baseline.
2. **`LookupService.ref_svc` wired** end-to-end (composition roots + dispatch logic + rendering).
3. **`LookupInput.limit`** input-shape validator with YAML-driven default + ceiling.
4. **YAML `reference_graph` config section** (capture toggles + output bounds — 5 keys total).
5. **MENTIONS** — new `ReferenceKind.MENTIONS` + minimal markdown mention extractor, opt-in via YAML.
6. **Reference rendering** — new helper in `application/formatting.py`, markdown output.
7. **Delete the 2 scope-fence tests** that locked the deferred-wire state in #5b.
8. **Re-export `ReferenceService`** from `pydocs_mcp.application`.

### Out of scope (deferred)

- Resolver experiment knobs (`self_short_circuit`, `cross_package_reresolution` toggles). Add only when there's real OFF-branch code to test — adding a YAML knob with no behavior behind it is config-shape debt.
- Class-context type inference for `self.X.Y` resolution.
- Batched `_reresolve_cross_package` UPDATE (perf follow-up; correctness already correct).
- New MCP tool params or new MCP tools.

---

## 4. SOLID adherence

The PR follows the project SOLID guidance (CLAUDE.md §"SOLID Principles"). Specific callouts:

- **Single Responsibility:** `MarkdownMentionExtractor` only walks markdown for backtick-quoted dotted names; `_render_refs` only renders; `ReferenceGraphConfig` sub-model only types YAML keys; the rule commit only edits CLAUDE.md.
- **Open/Closed:** `ReferenceKind` gains a new `MENTIONS` enum value (extension) without modifying existing kinds. `ReferenceCaptureStage` gains a markdown branch alongside the AST branch; no rewrite.
- **Liskov:** `MarkdownMentionExtractor` is a plain callable matching the `ReferenceCollector` interface — substitutable wherever the AST capture helpers go.
- **Interface Segregation:** MCP tool surface stays at 2 tools, 0 new params. The `LookupInput.limit` field is the one allowed exception per the new rule.
- **Dependency Inversion:** `ReferenceService` (already built in #5b) is composed via `uow_factory: Callable[[], UnitOfWork]` per CLAUDE.md §"Creating new application services". `LookupService` consumes `ReferenceService` only through its constructor; no reach-through.

---

## 5. Target shape — before / after

### 5.1 `LookupService` dispatch

```python
# BEFORE (on main today, post-#5b)
async def _symbol_lookup(self, package: str, qname: str, show: str, ...) -> str:
    if show in ("callers", "callees", "inherits"):
        if self.ref_svc is None:
            raise ServiceUnavailableError(
                "reference graph not indexed — enable via sub-PR #5b",
            )
        # ...unreachable today: ref_svc is always None on main

# AFTER (#5c)
async def _symbol_lookup(self, package: str, qname: str, show: str, ...) -> str:
    if show in ("callers", "callees", "inherits"):
        if self.ref_svc is None:
            raise ServiceUnavailableError(
                "reference graph not configured "
                "(reference_graph.capture.enabled=false?)",
            )
        if show == "callers":
            rows = await self.ref_svc.callers(package, qname)
        elif show == "callees":
            rows = await self.ref_svc.callees(package, qname)
        else:  # inherits
            rows = await self.ref_svc.find_by_name(qname, kind=ReferenceKind.INHERITS)
        return _render_refs(rows, target=qname, show=show, limit=limit)
```

The `ref_svc is None` branch survives as a deployment safety net (YAML capture disabled → no service). The error message updates to point at the YAML knob.

### 5.2 `LookupInput`

```python
# python/pydocs_mcp/application/mcp_inputs.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, field_validator


# Module-level defaults; set by `configure_from_app_config(cfg)` at server/CLI startup.
# This avoids singleton AppConfig caching while keeping the validator stateless.
_LIMIT_DEFAULT: int = 50
_LIMIT_MAX: int = 1000


def configure_from_app_config(cfg) -> None:
    """Called once from server.py::run and __main__.py::_cmd_* before any MCP call.

    Wires YAML-driven defaults into the LookupInput validator. Keeps the
    Pydantic model itself stateless — no hidden singleton, just module-level
    constants mutated at process startup. Matches the project's existing
    "build config once, pass it down" composition-root pattern.
    """
    global _LIMIT_DEFAULT, _LIMIT_MAX
    _LIMIT_DEFAULT = cfg.reference_graph.output.default_limit
    _LIMIT_MAX = cfg.reference_graph.output.max_limit


class LookupInput(BaseModel):
    target: str = Field(..., max_length=512)
    show: Literal["docs", "tree", "callers", "callees", "inherits"] = "docs"
    limit: int = Field(
        default_factory=lambda: _LIMIT_DEFAULT,
        ge=1,
        description=(
            "Max rows to return for callers/callees/inherits modes. "
            "Ignored for docs/tree modes."
        ),
    )

    @field_validator("limit")
    @classmethod
    def _check_max(cls, v: int) -> int:
        if v > _LIMIT_MAX:
            raise ValueError(
                f"limit={v} exceeds reference_graph.output.max_limit={_LIMIT_MAX}",
            )
        return v
```

**Design notes:**
- `ge=1` is a constant — stays on the `Field`.
- `MAX` is dynamic from YAML — `@field_validator`.
- No singleton cache, no lazy AppConfig accessor. Two module-level ints + one configure-at-startup function. Stateless validator, explicit lifecycle.

### 5.3 `AppConfig` extension

```python
# python/pydocs_mcp/retrieval/config.py
class ReferenceCaptureConfig(BaseModel):
    enabled: bool = True
    kinds: list[Literal["calls", "imports", "inherits", "mentions"]] = Field(
        default_factory=lambda: ["calls", "imports", "inherits"],  # MENTIONS opt-in
    )


class ReferenceOutputConfig(BaseModel):
    default_limit: int = Field(50, ge=1)
    max_limit: int = Field(1000, ge=1)

    @model_validator(mode="after")
    def _default_le_max(self) -> "ReferenceOutputConfig":
        if self.default_limit > self.max_limit:
            raise ValueError(
                f"default_limit={self.default_limit} > max_limit={self.max_limit}",
            )
        return self


class ReferenceGraphConfig(BaseModel):
    capture: ReferenceCaptureConfig = Field(default_factory=ReferenceCaptureConfig)
    output:  ReferenceOutputConfig  = Field(default_factory=ReferenceOutputConfig)


class AppConfig(BaseSettings):
    # ... existing sub-models ...
    reference_graph: ReferenceGraphConfig = Field(default_factory=ReferenceGraphConfig)
```

`default_config.yaml` gains:

```yaml
reference_graph:
  capture:
    enabled: true
    kinds: [calls, imports, inherits]   # add 'mentions' to opt in
  output:
    default_limit: 50
    max_limit: 1000
```

### 5.4 `ReferenceCaptureStage` honors `capture.enabled` + filters by `capture.kinds`

```python
@stage_registry.register("reference_capture")
@dataclass(frozen=True, slots=True)
class ReferenceCaptureStage:
    name: str = "reference_capture"

    async def run(self, state: IngestionState) -> IngestionState:
        cfg = state.app_config.reference_graph.capture       # NEW: read config
        if not cfg.enabled:                                  # NEW: master toggle
            return state
        allowed: set[str] = set(cfg.kinds)
        refs, aliases = await asyncio.to_thread(
            self._capture_all, state, allowed,               # filter inside
        )
        return replace(state, references=refs, reference_aliases=aliases)
```

`IngestionState.app_config` must be available — confirm via codebase survey during implementation. If not, add an `app_config: AppConfig` field to `IngestionState` (defaulted) and wire it through pipeline construction.

### 5.5 `ReferenceKind.MENTIONS` + `MarkdownMentionExtractor`

```python
# python/pydocs_mcp/extraction/reference_kind.py — Open/Closed extension
class ReferenceKind(StrEnum):
    CALLS    = "calls"
    IMPORTS  = "imports"
    INHERITS = "inherits"
    MENTIONS = "mentions"   # NEW — opt-in via YAML
```

```python
# python/pydocs_mcp/extraction/strategies/references.py — new helper, no class
# (capture_calls / capture_imports / capture_inherits are already module-level
#  functions; capture_mentions follows the same shape — Liskov / Single
#  Responsibility.)

import re

_MENTION_RE = re.compile(r"`([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)+)`")


def capture_mentions(
    markdown_text: str,
    *,
    from_package: str,
    from_node_id: str,
    collector: ReferenceCollector,
) -> None:
    """Capture backtick-quoted dotted names from a markdown chunk's text.

    Lower-precision than AST capture — regex over rendered markdown
    bodies. Each match is one MENTIONS edge with ``to_node_id=None``.
    The resolver then runs the same rules A-E to flip ``to_node_id`` if
    the mentioned name exists in the indexed-qname universe.
    """
    seen: set[str] = set()
    for m in _MENTION_RE.finditer(markdown_text):
        to_name = m.group(1)
        if to_name in seen:
            continue        # dedupe per chunk
        seen.add(to_name)
        collector.add(NodeReference(
            from_package=from_package,
            from_node_id=from_node_id,
            to_name=to_name,
            to_node_id=None,
            kind=ReferenceKind.MENTIONS,
        ))
```

`ReferenceCaptureStage._capture_all` adds a markdown branch alongside the existing `.py` AST branch:

```python
for path, source in state.file_contents:
    if path.endswith(".py") and "calls" in allowed:
        # ...existing AST capture (filtered by allowed kinds)
    elif path.endswith(".md") and "mentions" in allowed:
        module_qname = _module_from_path(path, state.root)
        capture_mentions(source, from_package=state.package_name,
                         from_node_id=module_qname, collector=collector)
```

**One regex, one helper, one branch.** No new strategy classes; matches the existing module-function pattern in `references.py`.

### 5.6 Project-qname-prefix fix

In `python/pydocs_mcp/extraction/strategies/discovery.py` (or wherever the project qname is computed — confirm during implementation), strip the `python.` filesystem prefix when the source root is `python/pydocs_mcp/`. Documented inline:

```python
def _qname_from_project_path(rel_path: str) -> str:
    """Project source qname must equal the Python module qname.

    Pre-#5c: returned `python.pydocs_mcp.X` (filesystem-walk path).
    Post-#5c: returns `pydocs_mcp.X` (matches `import pydocs_mcp.X`).
    Required for AC #15 — the resolver's qname universe must contain
    the same strings that ``capture_imports`` emits as ``to_name``.
    """
    # ... single-file change, ~10 lines ...
```

This fix is its own commit so it's bisectable against AC #15 measurements.

### 5.7 Rendering — `application/formatting.py`

```python
def format_references(
    rows: tuple[NodeReference, ...],
    *,
    target: str,
    show: Literal["callers", "callees", "inherits"],
    limit: int,
) -> str:
    """Render reference rows as markdown for the lookup MCP tool.

    Conventions:
      - H1 = question being answered ("Callers of X" / "Callees of X" / "Bases of X")
      - Count summary in lead paragraph: total + resolved/unresolved split
      - H2 = from_package group
      - Within each group: resolved rows first; ⚠ prefix on unresolved
      - Format: ``- `from_node_id` → `to_name|to_node_id` [*(reason)*]``
    """
```

See appendix §A.1 for a worked example.

### 5.8 Composition root wiring

```python
# storage/factories.py
def build_sqlite_lookup_service(db_path, config) -> LookupService:
    uow_factory = build_sqlite_uow_factory(db_path)
    return LookupService(
        package_lookup=PackageLookup(uow_factory=uow_factory),
        tree_svc=TreeService(uow_factory=uow_factory),
        ref_svc=ReferenceService(uow_factory=uow_factory),   # was None
    )
```

```python
# server.py::run + __main__.py::_cmd_* — call configure_from_app_config(config)
# ONCE after AppConfig.load(), BEFORE any LookupInput is constructed.
from pydocs_mcp.application.mcp_inputs import configure_from_app_config
config = AppConfig.load(explicit_path=...)
configure_from_app_config(config)
```

### 5.9 Re-export `ReferenceService`

```python
# python/pydocs_mcp/application/__init__.py
from pydocs_mcp.application.reference_service import ReferenceService  # NEW
__all__ = [..., "ReferenceService", ...]
```

### 5.10 Scope-fence test deletion

Delete in the same commit as the wire flip:

- `tests/application/test_reference_service_not_yet_exported.py`
- `tests/application/test_lookup_service_5b_deferred_wire.py`

Both files exist solely to break when #5c lands. They're the forcing function — successful deletion is the success signal.

---

## 6. Rendering example (appendix §A.1)

`lookup(target="pkg.helpers.compute", show="callers", limit=50)` with 5 callers across 3 packages (3 resolved, 2 unresolved):

````markdown
# Callers of `pkg.helpers.compute`

5 references found (3 resolved, 2 unresolved).

## from `pkg` (2 callers)

- `pkg.utils.runner.run_pipeline` → `pkg.helpers.compute`
- `pkg.cli.main` → `pkg.helpers.compute`

## from `acme-tools` (2 callers)

- `acme_tools.analytics.aggregate.summarize` → `pkg.helpers.compute`
- ⚠ `acme_tools.legacy._old_runner` → `compute` *(unresolved — to_name didn't match any indexed qname)*

## from `__project__` (1 caller)

- ⚠ `tests.application.test_helpers.test_compute_handles_empty` → `helpers.compute` *(unresolved — to_name didn't match any indexed qname)*
````

---

## 7. Files touched

| File | Change |
|---|---|
| `CLAUDE.md` | Add §"MCP API surface vs YAML configuration" rule (already drafted locally) |
| `python/pydocs_mcp/extraction/strategies/discovery.py` (or wherever project qname is computed) | Strip `python.` filesystem prefix |
| `python/pydocs_mcp/extraction/reference_kind.py` | Add `MENTIONS = "mentions"` enum value |
| `python/pydocs_mcp/extraction/strategies/references.py` | Add `capture_mentions(...)` module function |
| `python/pydocs_mcp/extraction/pipeline/stages.py` | `ReferenceCaptureStage` reads `cfg.capture.enabled` + filters by `cfg.capture.kinds`; markdown branch |
| `python/pydocs_mcp/retrieval/config.py` | Add `ReferenceCaptureConfig`, `ReferenceOutputConfig`, `ReferenceGraphConfig` Pydantic models; `AppConfig.reference_graph` field |
| `python/pydocs_mcp/defaults/default_config.yaml` | Add `reference_graph` section (5 keys) |
| `python/pydocs_mcp/application/mcp_inputs.py` | Add `LookupInput.limit` field + `configure_from_app_config` |
| `python/pydocs_mcp/application/lookup_service.py` | `_symbol_lookup` flip for callers/callees/inherits; thread `limit` |
| `python/pydocs_mcp/application/formatting.py` | Add `format_references(rows, target, show, limit)` |
| `python/pydocs_mcp/application/__init__.py` | Re-export `ReferenceService` |
| `python/pydocs_mcp/storage/factories.py` | `build_sqlite_lookup_service` constructs `ReferenceService(uow_factory=...)` (was `None`) |
| `python/pydocs_mcp/server.py` | Wire `configure_from_app_config(config)` once at startup; remove `ref_svc=None` |
| `python/pydocs_mcp/__main__.py` | Same as server.py for CLI |
| `tests/application/test_reference_service_not_yet_exported.py` | DELETE |
| `tests/application/test_lookup_service_5b_deferred_wire.py` | DELETE |
| Various new test files | See §9 below |

---

## 8. Acceptance criteria

| # | Description |
|---|---|
| 1 | CLAUDE.md contains §"MCP API surface vs YAML configuration" as committed text on main. |
| 2 | `LookupInput.limit` exists with `ge=1`, default driven by `configure_from_app_config`, max enforced by `field_validator`. |
| 3 | `LookupService._symbol_lookup` returns rendered markdown for `show in ("callers","callees","inherits")` instead of raising `ServiceUnavailableError` when `ref_svc is not None`. |
| 4 | `AppConfig.reference_graph` typed sub-model exists; `default_config.yaml` ships the 5 keys. |
| 5 | `ReferenceCaptureStage` honors `cfg.capture.enabled` (no-op when False). |
| 6 | `ReferenceCaptureStage` filters captured refs by `cfg.capture.kinds` (e.g., `kinds=[calls]` drops IMPORTS/INHERITS at the stage boundary). |
| 7 | `ReferenceKind.MENTIONS` enum value exists; `capture_mentions` regex helper emits MENTIONS edges from markdown when `mentions` in `kinds`. |
| 8 | `format_references` renders the §A.1 shape; resolved-first sort within each `from_package` group; ⚠ prefix on unresolved. |
| 9 | `ReferenceService` is re-exported from `pydocs_mcp.application`. |
| 10 | `build_sqlite_lookup_service` constructs `ReferenceService(uow_factory=uow_factory)` and threads it into `LookupService(ref_svc=...)`. |
| 11 | `server.py::run` calls `configure_from_app_config(config)` once before any MCP handler runs. `__main__.py` CLI paths do the same. |
| 12 | Both `test_reference_service_not_yet_exported.py` and `test_lookup_service_5b_deferred_wire.py` are DELETED. |
| 13 | Project-qname-prefix fix: project source qnames equal `import pydocs_mcp.X`, NOT `python.pydocs_mcp.X`. Verified by a new test that indexes the project and asserts `pkg.application.indexing_service` appears in the qname universe. |
| 14 | AC #15 self-index measurement re-runs after the qname-prefix fix; new floor recorded in the test docstring (expect material jump from 11.7%). |
| 15 | `grep -rnE "ServiceUnavailableError.*sub-PR #5b" python/` returns ZERO matches (the deferred-wire error message is gone). |
| 16 | All 914 baseline tests still pass (modulo the 3 deleted scope-fence tests = 911 baseline + new tests). |
| 17 | ruff clean; cargo fmt + clippy clean. |
| 18 | `grep -rnE "kind[s]?=" python/pydocs_mcp/application/mcp_inputs.py` returns nothing tying MCP input shape to pipeline behavior (rule check). |

---

## 9. Test plan

- New `tests/application/test_lookup_service_refs.py`: `_symbol_lookup` for each of `callers`/`callees`/`inherits` returns rendered markdown via `FakeReferenceService` (5+ tests).
- New `tests/application/test_format_references.py`: pin the §A.1 rendering shape across 0/1/many rows, resolved vs unresolved, package grouping (5+ tests).
- New `tests/application/test_mcp_inputs_limit.py`: `LookupInput.limit` default reads from `configure_from_app_config`; max bound enforced; `ge=1` enforced (4+ tests).
- New `tests/retrieval/test_reference_graph_config.py`: AppConfig YAML overlay parsing for `reference_graph` section; default values; `default_limit > max_limit` validation error (4+ tests).
- New `tests/extraction/test_capture_mentions.py`: regex hits backtick-quoted dotted names; ignores plain backtick code; dedupes per chunk (4+ tests).
- New `tests/extraction/test_capture_stage_config.py`: `enabled=False` → zero refs; `kinds=[calls]` → only CALLS edges survive (3+ tests).
- New `tests/test_project_qname_fix.py`: indexes the project; asserts `pydocs_mcp.X` qnames in universe, NOT `python.pydocs_mcp.X` (1 test).
- AC #15 self-index test (`tests/integration/test_self_index_resolution_rate.py`) re-measured with the qname fix applied; expect higher rate; floor in the test docstring updated.

**Estimated test count delta:** ~25-30 new tests + 2 deleted tests = baseline + ~25.

---

## 10. SOLID-aligned change summary (recap)

- **Single Responsibility:** every new function/class has one job; no new "swiss-army" classes.
- **Open/Closed:** `ReferenceKind.MENTIONS` and `capture_mentions` extend the existing enum and module without modifying existing kinds/captures.
- **Liskov:** `capture_mentions` matches the existing `capture_calls/capture_imports/capture_inherits` shape (free function taking `collector`).
- **Interface Segregation:** MCP tool surface unchanged. `LookupInput.limit` is the one allowed input-shape exception per CLAUDE.md.
- **Dependency Inversion:** `LookupService` consumes `ReferenceService` through its constructor `ref_svc` parameter, never reaches into stores. `ReferenceService` follows the `uow_factory`-only contract.

---

## 11. Ship sequence + commit shape

The PR ships as ~6 atomic commits, each independently buildable + testable:

1. `docs(#5c): add CLAUDE.md §"MCP API surface vs YAML configuration"`
2. `fix(#5c): project source qname uses pydocs_mcp prefix, not python.pydocs_mcp` (the qname-prefix fix; bisectable for AC #15)
3. `feat(#5c): AppConfig.reference_graph typed sub-model + default_config.yaml`
4. `feat(#5c): ReferenceCaptureStage honors capture.enabled + filters by kinds`
5. `feat(#5c): ReferenceKind.MENTIONS + capture_mentions regex helper + markdown branch`
6. `feat(#5c): wire LookupService.ref_svc + LookupInput.limit + format_references; delete scope-fence tests`

---

## 12. Non-goals

- Resolver experiment knobs.
- Class-context type inference for self.X.Y.
- Batched cross-package re-resolution UPDATE.
- New MCP tools or new MCP tool parameters beyond `LookupInput.limit`.
- Any change to `IndexingService` / `PackageLookup` / `ModuleInspector` / `TreeService` / `ProjectIndexer` constructor shapes (the post-#5a-2 contract stays intact).

---

## 13. Approval log

- 2026-05-18: brainstorm session. Locked: Option B (all-in single PR), Knobs 1+4 YAML (capture + output, 5 keys total), `LookupInput.limit` as the one allowed MCP input exception, markdown rendering format per §A.1, MENTIONS as a regex helper (not a new strategy class), project-qname-prefix fix as its own bisectable commit.
