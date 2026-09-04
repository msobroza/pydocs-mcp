# Ask-Your-Docs Branch Scope UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the ask-your-docs chat and graph pages branch-aware scoping — hidden soft defaults, per-question hard pins that fan out over `(project, branch)` cells with labeled results, a one-line answer footer, follow-up chips, and a graph compare overlay — while keeping the default single-cell path byte-identical to today and the MCP surface untouched.

**Architecture:** A frozen `QuestionScope` value object replaces the `ToolScope` dict; a new `scope_interceptor.py` applies it to every tool call through the existing `langchain-mcp-adapters` interceptor seam (defaults fill what the model left empty, pins overwrite and fan out); `ScopeObservations` carries per-call `meta` back to the page through in-place mutation of a container created in `ask()`; capability gating (`ScopeCapabilities`, read from the advertised tool schemas) keeps every branch/slice control dormant until the server advertises `branch`, `changed` and `diff`. Three stages: **U0** (mergeable now, P0 servers), **U1** (activated by multi-branch P1), **U2** (activated by P2).

**Tech Stack:** Python 3.11+, Streamlit ≥ 1.57 (`st.bottom`, keyed `st.popover`), langchain-mcp-adapters ≥ 0.3 (`MCPToolCallRequest.override`), `mcp.types.CallToolResult`, pydantic v2 config sub-models, SQLite read-only bundle readers, pytest + `streamlit.testing.v1.AppTest`.

**Spec:** `docs/superpowers/specs/2026-09-04-ask-your-docs-branch-scope-ui-design.md` (commit `34028eb`). Section numbers below (§6.2, §6.3, …) refer to that document; "multi-branch spec" means `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md` (amended, commit `1c371bc`).

## Global Constraints

- **No MCP surface change** (§8): no new tool, parameter or envelope field; the harness only reads what the server advertises. Client-side merged results never reach the server.
- **Byte-identity** (§8, R7, R11): with the shipped YAML and no pin, on a server that does not advertise `branch`, every tool call carries exactly today's arguments, and the assembled system prompt is byte-identical (rule 7 and the catalog branch segment are gated on the `branch` capability). `SYSTEM_PROMPT` bytes never change in any stage (the eval seed parity pin `tests/harness/ask_your_docs/test_prompt_seed_parity.py` stays green without regeneration).
- **Eval binding untouched** (§8, AC-24): `binding.py` keeps importing `_intercept` and `serve_connection` from `agent.py`, keeps calling `build_agent(...)` and unpacking a 2-tuple, and never calls `ask()` — so the interceptor is a strict passthrough on that path.
- **Streamlit floor** `streamlit>=1.57` in the `[harness-ask-your-docs]` extra with the WHY comment `# WHY: st.bottom (chat composer row) + stateful st.popover (scope pin)`; no `_bottom` shim, no fallback shape (E10).
- **Every tunable is YAML**: the `ask_your_docs.scope` block (§7) — seven keys `project, branch_default, branch_name, slice, code, package, max_cells`; the panel is a session override of YAML, never a second source of defaults. No CLI flag, no MCP param.
- **Closed vocabularies are `StrEnum`s** with UPPER_SNAKE members; value objects are `@dataclass(frozen=True, slots=True)`; error messages carry the offending value and the expected shape; files under 500 lines (`agent.py` ≤ 500, target ≤ 468 — the target is not a gate); functions 4–20 lines; max two indentation levels.
- **Structured logs**: scope events are one JSON line each with named fields (`{"event": "scope_default_replaced", "tool": …, "argument": …, "passed": …, "replacement": …}`), emitted through `logging.getLogger("pydocs_mcp.harness.ask_your_docs.question_scope")`.
- **`harness/ask_your_docs/` is mypy-excluded and coverage-excluded** (pyproject `[tool.mypy] exclude`); therefore every enum a mypy-checked module needs (the config sub-model) lives in `retrieval/config/ask_your_docs_models.py` and is re-exported from `question_scope.py` under the spec's names.
- **Git authorship**: commits are authored by the repository's configured user only — no `Co-Authored-By` trailers, no `--author`, no signing flags.
- **Test venvs**: core-suite tests run from the worktree venv (`pytest tests/harness/ask_your_docs -q`); AppTest smoke tests need the `[harness-ask-your-docs]` extra, installed in the main checkout's venv (`/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest`). Every AppTest **seeds `st.session_state["scope_capabilities"]`** so the page never builds the agent (no MCP subprocess, no LLM client) during a test.
- **Gates before every push**: `ruff format --check python/ tests/`, `ruff check python/ tests/`, `mypy python/pydocs_mcp`, `complexipy python/pydocs_mcp --max-complexity-allowed 15`, `vulture python/pydocs_mcp --min-confidence 80`, `pytest tests/ --ignore=tests/test_parity.py -q`, `uv lock --check` (after the floor bump, relock with `~/.local/bin/uv lock`).

---

## File map

| Path | Status | Stage | Owns |
|---|---|---|---|
| `python/pydocs_mcp/retrieval/config/ask_your_docs_models.py` | modify | U0 | `ScopeSlice`, `ScopeCode`, `ScopeBranchDefault`, `ANY_PROJECT`, `ScopeDefaultsConfig`, `AskYourDocsConfig.scope` |
| `python/pydocs_mcp/defaults/default_config.yaml` | modify | U0 | the `ask_your_docs.scope` block |
| `python/pydocs_mcp/harness/ask_your_docs/bundle.py` | modify | U0 / U1 | `IndexedBranch`, `BundleReader.branches()`; U1: `branch_symbol_chunks()`, `reference_rows(branch=)` |
| `python/pydocs_mcp/harness/ask_your_docs/catalog.py` | modify | U0 / U1 / U2 | `WorkspaceBranchListing`, `EMPTY_BRANCH_LISTING`, `CatalogService.branch_listing()`, `workspace_branch_listing()`, `render_catalog(branches=, show_merged=)` |
| `python/pydocs_mcp/harness/ask_your_docs/attachments.py` | modify | U0 | `AttachedSymbol`; `weave_attachments` accepts it |
| `python/pydocs_mcp/harness/ask_your_docs/question_scope.py` | new | U0 | `ScopeKind`, `ScopeCell`, `QuestionScope`, `ScopeDefaultsOverride`, `resolve_question_scope_defaults`, `resolve_default_branch`, `scope_prefix`, `scope_caption_text`, `pin_summary_label`, `pin_with_attached_symbols`, `log_scope_event`, server-value / label tables |
| `python/pydocs_mcp/harness/ask_your_docs/scope_capabilities.py` | new | U0 | `ScopeCapabilities`, `NO_SCOPE_CAPABILITIES`, `inspect_scope_capabilities`, `BuiltAgent` |
| `python/pydocs_mcp/harness/ask_your_docs/scope_interceptor.py` | new | U0 / U1 / U2 | contextvars, `ScopeRuntime`, `BranchOrigin`, `CellObservation`, `ScopeObservations`, `intercept_question_scope`, `target_cells`, `cell_arguments`, `fan_out_over_cells`, `merge_cell_results` |
| `python/pydocs_mcp/harness/ask_your_docs/agent.py` | modify | U0 / U1 | `_intercept` delegate, `ask()` new keywords, `_assemble_prompt` keywords, `build_agent_with_scope_capabilities`, `build_agent` wrapper |
| `python/pydocs_mcp/harness/ask_your_docs/__init__.py` | modify | U0 | `_LAZY["scope_prefix"] = "question_scope"` |
| `python/pydocs_mcp/harness/ask_your_docs/answer_footer.py` | new | U0 / U1 / U2 | `render_answer_footer`, `FollowUpKind`, `FollowUpChip`, `derive_follow_up_chips`, `apply_follow_up_chip` |
| `python/pydocs_mcp/harness/ask_your_docs/scope_panel.py` | new (Streamlit-only) | U0 / U1 / U2 | `render_scope_defaults_button`, `render_scope_defaults_panel`, `render_scope_pin_popover`, `render_scope_chip_row`, `render_follow_up_chips`, `render_graph_branch_row`, `drop_pin_if_listing_changed` |
| `python/pydocs_mcp/harness/ask_your_docs/app.py` | modify | U0 / U1 | `send_question`, `page_scope_capabilities`, `load_branch_listing`, `get_agent` → `BuiltAgent`, transcript entries with footer + chips, `st.bottom` composer row |
| `python/pydocs_mcp/harness/ask_your_docs/pages/2_Graph.py` | modify | U0 / U1 | shared panel, branch row, `AttachedSymbol` attach, compare overlay |
| `python/pydocs_mcp/harness/ask_your_docs/graph_compare.py` | new | U1 | `ChangeState`, `BranchGraphComparison`, `compare_branch_graphs`, `changed_only` |
| `python/pydocs_mcp/harness/core/prompts/system_v1.j2` | modify | U1 | rule 7 inside the `is defined` guard |
| `pyproject.toml` | modify | U0 | `streamlit>=1.57` |
| `examples/harness/ask_your_docs_agent/README.md`, `CHANGELOG.md` | modify | U0 / U2 | user-facing description |
| `tests/harness/ask_your_docs/_fixture.py` | modify | U0 / U1 | `branches` / `branch_chunks` tables, `branches=` rows |
| `tests/fixtures/goldens/ask_your_docs_system_v1.txt` | new | U0 | today's rendered `system_v1` bytes (AC-11 golden) |
| `tests/harness/ask_your_docs/test_{question_scope,scope_capabilities,scope_interceptor,bundle_branches,catalog_branches,answer_footer,graph_compare,app_scope_states}.py` | new | per stage | the acceptance criteria |

---

# Stage U0 — mergeable now (P0 servers, schema v16)

### Task 1: Scope vocabularies and the `ask_your_docs.scope` config block

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config/ask_your_docs_models.py`
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` (after the `images:` block, inside `ask_your_docs:`)
- Test: `tests/test_config_ask_your_docs.py`

**Interfaces:**
- Produces: `ScopeSlice {WHOLE_BRANCH, CHANGED_FILES, DIFF_HUNKS}`, `ScopeCode {ALL, OWN, DEPS}`, `ScopeBranchDefault {BASE, CHECKED_OUT}`, `ANY_PROJECT = "any"`, `ScopeDefaultsConfig(project: str, branch_default: ScopeBranchDefault, branch_name: str, slice: ScopeSlice, code: ScopeCode, package: str, max_cells: int)`, `AskYourDocsConfig.scope: ScopeDefaultsConfig`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config_ask_your_docs.py`:

```python
def test_scope_defaults_yaml_matches_pydantic_defaults() -> None:
    """AC-22: the shipped YAML scope block equals ScopeDefaultsConfig()."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

    assert AppConfig.load().ask_your_docs.scope == ScopeDefaultsConfig()


def test_scope_branch_default_is_a_closed_vocabulary() -> None:
    from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

    with pytest.raises(ValidationError):
        ScopeDefaultsConfig(branch_default="main")


def test_scope_rejects_unknown_keys() -> None:
    from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

    with pytest.raises(ValidationError):
        ScopeDefaultsConfig(branches="main")


def test_scope_max_cells_env_override(monkeypatch) -> None:
    monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__SCOPE__MAX_CELLS", "2")
    assert AppConfig.load().ask_your_docs.scope.max_cells == 2


def test_scope_rejects_a_slice_with_dependencies_only() -> None:
    """E11 at config load: deps and changed/diff are disjoint server slices."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

    with pytest.raises(ValidationError) as excinfo:
        ScopeDefaultsConfig(slice="diff_hunks", code="deps")
    assert "diff_hunks" in str(excinfo.value) and "deps" in str(excinfo.value)
```

and extend `test_default_yaml_ships_the_block_keys` with two lines after `assert block["images"]["session_retention"] == 12`:

```python
    assert block["scope"]["branch_default"] == "base"
    assert block["scope"]["max_cells"] == 4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_config_ask_your_docs.py -q`
Expected: FAIL — `ImportError: cannot import name 'ScopeDefaultsConfig'` and `KeyError: 'scope'`.

- [ ] **Step 3: Add the vocabularies and the sub-model**

In `python/pydocs_mcp/retrieval/config/ask_your_docs_models.py`, replace the import block and append the new classes; mount `scope` on `AskYourDocsConfig`:

```python
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The YAML spelling of "no project default" (ask_your_docs.scope.project).
ANY_PROJECT = "any"
# Single source of the fan-out cap default (the YAML restates it for readers).
_DEFAULT_SCOPE_MAX_CELLS = 4


class ScopeSlice(StrEnum):
    """Which part of a branch a search covers (UI spec §6.2).

    The server spells the last two ``changed`` / ``diff`` (multi-branch spec
    §6.5); whole branch sends no ``scope`` value at all.
    """

    WHOLE_BRANCH = "whole_branch"
    CHANGED_FILES = "changed_files"
    DIFF_HUNKS = "diff_hunks"


class ScopeCode(StrEnum):
    """Today's own-code vs dependency filter; OWN is the server's ``project``."""

    ALL = "all"
    OWN = "own"
    DEPS = "deps"


class ScopeBranchDefault(StrEnum):
    """Symbolic branch defaults — split from a free branch name so a branch
    literally named ``base`` stays addressable through ``branch_name``."""

    BASE = "base"
    CHECKED_OUT = "checked_out"
```

then, after `ImagesConfig`:

```python
class ScopeDefaultsConfig(BaseModel):
    """Soft scope defaults for the chat and graph pages (UI spec §7).

    The sidebar "Scope defaults" panel overrides these for one session only;
    they fill what the model leaves unspecified and never overwrite a
    model-passed argument. ``branch_name`` is checked against the workspace's
    branch listing at resolution time, not here — the config is
    workspace-agnostic.
    """

    model_config = ConfigDict(extra="forbid")

    project: str = Field(default=ANY_PROJECT)
    branch_default: ScopeBranchDefault = Field(default=ScopeBranchDefault.BASE)
    branch_name: str = Field(default="")
    slice: ScopeSlice = Field(default=ScopeSlice.WHOLE_BRANCH)
    code: ScopeCode = Field(default=ScopeCode.ALL)
    package: str = Field(default="")
    max_cells: int = Field(default=_DEFAULT_SCOPE_MAX_CELLS, ge=1, le=16)

    @model_validator(mode="after")
    def _slice_excludes_dependencies_only(self) -> ScopeDefaultsConfig:
        # E11: the server's deps slice and its changed/diff slices are disjoint.
        if self.slice is not ScopeSlice.WHOLE_BRANCH and self.code is ScopeCode.DEPS:
            raise ValueError(
                f"ask_your_docs.scope: slice={self.slice.value!r} cannot combine with "
                f"code={self.code.value!r}; expected code 'all' or 'own' with a slice"
            )
        return self
```

and on `AskYourDocsConfig`, after `images`:

```python
    scope: ScopeDefaultsConfig = Field(default_factory=ScopeDefaultsConfig)
```

Extend `__all__` with `"ANY_PROJECT", "ScopeBranchDefault", "ScopeCode", "ScopeDefaultsConfig", "ScopeSlice"` (keep it sorted).

- [ ] **Step 4: Add the YAML block**

In `python/pydocs_mcp/defaults/default_config.yaml`, after the `max_reinspect_per_turn` line of the `images:` block (still inside `ask_your_docs:`), add:

```yaml
  scope:                          # soft defaults for the chat and graph pages;
                                  # the sidebar "Scope defaults" panel overrides
                                  # them for one session only
    project: any                  # any | <indexed project name>; "any" sends no
                                  # project (the server's union across bundles)
    branch_default: base          # base | checked_out — "base" = the bundle's base
                                  # branch (branches.base_name; stamped by multi-branch
                                  # P1.6, so it resolves to nothing on P0 bundles),
                                  # shown as "main (base branch)" in the panel;
                                  # "checked_out" sends nothing (the server's own default)
    branch_name: ""               # an indexed branch name; wins over branch_default
                                  # when non-empty; checked against the listing at
                                  # resolution time, not at config load
    slice: whole_branch           # whole_branch | changed_files | diff_hunks;
                                  # the last two need the server's scope=changed /
                                  # scope=diff values (multi-branch P2) and apply
                                  # to search_codebase and grep only
    code: all                     # all | own | deps  (today's own-vs-dependency filter)
    package: ""                   # "" = no package default
    max_cells: 4                  # fan-out cap: the most (project, branch) cells
                                  # one tool call may query under a pin; refused
                                  # before any call when exceeded
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_config_ask_your_docs.py -q`
Expected: PASS (all, including the pre-existing `test_ask_your_docs_yaml_matches_pydantic_defaults`).

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/config/ask_your_docs_models.py python/pydocs_mcp/defaults/default_config.yaml tests/test_config_ask_your_docs.py
git commit -m "ask-your-docs: scope vocabularies + ask_your_docs.scope YAML defaults"
```

---

### Task 2: `IndexedBranch` and `BundleReader.branches()`

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/bundle.py`
- Modify: `tests/harness/ask_your_docs/_fixture.py`
- Test: `tests/harness/ask_your_docs/test_bundle_branches.py`

**Interfaces:**
- Produces: `IndexedBranch(name, head_sha, base_name, is_default, status: BranchStatus, merged_into, landing_kind, indexed_at)` with the property `is_landing_unit`; `BundleReader.branches() -> tuple[IndexedBranch, ...]`; `make_bundle(..., branches=[(name, head_sha, base_name, is_default, status, merged_into), ...], with_branch_tables=True)`.

- [ ] **Step 1: Grow the fixture**

In `tests/harness/ask_your_docs/_fixture.py` add the v16 branch tables and the new parameters:

```python
# The v16 branch tables (db.py _V16_STATEMENTS), created unless the test wants
# a pre-v16 bundle (with_branch_tables=False → E8 path).
_BRANCH_SCHEMA = """
CREATE TABLE branches (
    name TEXT PRIMARY KEY, head_sha TEXT NOT NULL, base_name TEXT, merge_base_sha TEXT,
    source TEXT NOT NULL, worktree_path TEXT, is_default INTEGER NOT NULL DEFAULT 0,
    pipeline_hash TEXT NOT NULL, indexed_at REAL NOT NULL, last_used_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', merged_into TEXT, retired_at REAL,
    purge_after REAL, pinned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE branch_chunks (
    branch TEXT NOT NULL, chunk_id INTEGER NOT NULL, source_path TEXT NOT NULL,
    start_line INTEGER, end_line INTEGER, changed INTEGER NOT NULL DEFAULT 0,
    slice TEXT NOT NULL DEFAULT 'tree', PRIMARY KEY (branch, chunk_id)
);
"""

# (name, head_sha, base_name, is_default, status, merged_into)
BranchRow = tuple[str, str, str | None, int, str, str | None]
```

Change the `make_bundle` signature to

```python
def make_bundle(
    path: Path,
    *,
    project: str = "demo",
    user_version: int = 99,
    members: list[tuple[str, str, str]] = (),
    refs: list[tuple[str, str, str]] = (),
    markdown: list[tuple[str, str, str]] = (),
    decisions: list[tuple[str, str]] = (),
    docstrings: dict[str, str] | None = None,
    branches: list[BranchRow] = (),
    branch_chunks: list[tuple[str, int]] = (),
    with_branch_tables: bool = True,
) -> Path:
```

and, before `conn.commit()`:

```python
    if with_branch_tables:
        conn.executescript(_BRANCH_SCHEMA)
        for name, head_sha, base_name, is_default, status, merged_into in branches:
            conn.execute(
                "INSERT INTO branches (name, head_sha, base_name, source, is_default, "
                "pipeline_hash, indexed_at, last_used_at, status, merged_into) VALUES "
                "(?, ?, ?, 'working_tree', ?, 'ph', 1.0, 1.0, ?, ?)",
                (name, head_sha, base_name, is_default, status, merged_into),
            )
        for branch, chunk_id in branch_chunks:
            conn.execute(
                "INSERT INTO branch_chunks (branch, chunk_id, source_path) VALUES (?, ?, 'x.py')",
                (branch, chunk_id),
            )
```

- [ ] **Step 2: Write the failing tests**

Create `tests/harness/ask_your_docs/test_bundle_branches.py`:

```python
"""BundleReader.branches() — AC-14 / AC-14b of the branch-scope UI spec."""

from __future__ import annotations

import sqlite3

import pytest

from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch, SqliteBundleReader
from pydocs_mcp.models import BranchStatus

from ._fixture import make_bundle

_SHA = "3e1a9c2" + "0" * 33  # 40 hex characters — a landing-unit name
_HEAD = "a" * 40


def test_branches_are_ordered_default_first_then_by_name(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        branches=[
            ("zeta", _HEAD, "main", 0, "active", None),
            ("feature/x", _HEAD, "main", 1, "active", None),
            ("main", _HEAD, None, 0, "active", None),
        ],
    )
    names = [row.name for row in SqliteBundleReader(db).branches()]
    assert names == ["feature/x", "main", "zeta"]


def test_rows_carry_status_base_and_default_flag(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        branches=[("feature/x", _HEAD, "main", 1, "active", None)],
    )
    (row,) = SqliteBundleReader(db).branches()
    assert row == IndexedBranch(
        name="feature/x",
        head_sha=_HEAD,
        base_name="main",
        is_default=True,
        status=BranchStatus.ACTIVE,
        merged_into=None,
        landing_kind=None,
        indexed_at=1.0,
    )


def test_pre_v16_bundle_yields_no_rows(tmp_path):
    db = make_bundle(tmp_path / "demo_0123456789.db", with_branch_tables=False)
    assert SqliteBundleReader(db).branches() == ()


def test_other_operational_errors_are_re_raised(tmp_path):
    db = make_bundle(tmp_path / "demo_0123456789.db", with_branch_tables=False)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE branches (name TEXT)")  # a wrong shape, not a missing table
    with pytest.raises(sqlite3.OperationalError):
        SqliteBundleReader(db).branches()


def test_branches_never_migrates_the_bundle(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        branches=[("main", _HEAD, None, 1, "active", None)],
    )
    SqliteBundleReader(db).branches()
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 99


def test_landing_units_and_tombstones_are_distinguishable(tmp_path):
    """AC-14b: one default row, one MERGED tombstone, one 40-hex landing row."""
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        branches=[
            ("main", _HEAD, None, 1, "active", None),
            ("feature/old", _HEAD, "main", 0, "merged", _SHA),
            (_SHA, _SHA, "main", 0, "active", None),
        ],
    )
    rows = {row.name: row for row in SqliteBundleReader(db).branches()}
    assert len(rows) == 3
    assert rows[_SHA].is_landing_unit and not rows["main"].is_landing_unit
    assert rows["feature/old"].status is BranchStatus.MERGED
    assert rows["feature/old"].merged_into == _SHA
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_bundle_branches.py -q`
Expected: FAIL — `ImportError: cannot import name 'IndexedBranch'`.

- [ ] **Step 4: Implement `IndexedBranch` and `branches()`**

In `python/pydocs_mcp/harness/ask_your_docs/bundle.py` add the import `from dataclasses import dataclass` and `from pydocs_mcp.models import BranchStatus`, then after `_SLUG_RE`:

```python
# A landing unit is stored as a branches row named by its 40-hex sha
# (multi-branch spec §6.5b); v17 also stamps landing_kind.
_LANDING_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class IndexedBranch:
    """One ``branches`` row of a bundle (schema v16+), read-only."""

    name: str
    head_sha: str
    base_name: str | None
    is_default: bool
    status: BranchStatus
    merged_into: str | None
    landing_kind: str | None
    indexed_at: float

    @property
    def is_landing_unit(self) -> bool:
        return self.landing_kind is not None or bool(_LANDING_SHA_RE.match(self.name))


def _indexed_branch(row: tuple) -> IndexedBranch:
    name, head_sha, base_name, is_default, status, merged_into, landing_kind, indexed_at = row
    return IndexedBranch(
        name=str(name),
        head_sha=str(head_sha),
        base_name=base_name,
        is_default=bool(is_default),
        status=BranchStatus(status),
        merged_into=merged_into,
        landing_kind=landing_kind,
        indexed_at=float(indexed_at or 0.0),
    )
```

Add to the `BundleReader` Protocol, after `indexed_at`:

```python
    def branches(self) -> tuple[IndexedBranch, ...]:
        """Every ``branches`` row, default first then by name; ``()`` before v16."""
        ...
```

Add to `SqliteBundleReader`:

```python
    def _columns(self, conn: sqlite3.Connection, table: str) -> frozenset[str]:
        # PRAGMA table_info yields no rows (and no error) for a missing table.
        return frozenset(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))  # noqa: S608 — table names are literals

    def branches(self) -> tuple[IndexedBranch, ...]:
        with self._conn() as conn:
            columns = self._columns(conn, "branches")
            if not columns:
                return ()  # pre-v16 bundle (E8): no table, no rows
            # landing_kind arrives with schema v17; read NULL on v16.
            landing = "landing_kind" if "landing_kind" in columns else "NULL"
            rows = conn.execute(
                "SELECT name, head_sha, base_name, is_default, status, merged_into, "  # noqa: S608 — column name from a closed choice
                f"{landing}, indexed_at FROM branches ORDER BY is_default DESC, name"
            ).fetchall()
        return tuple(_indexed_branch(row) for row in rows)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/test_bundle_branches.py tests/harness/ask_your_docs/test_graph_service.py -q`
Expected: PASS. (`test_graph_service.py` still passes: `FakeBundleReader` is duck-typed and `GraphService` never calls `branches()`.)

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/bundle.py tests/harness/ask_your_docs/_fixture.py tests/harness/ask_your_docs/test_bundle_branches.py
git commit -m "ask-your-docs: IndexedBranch + read-only BundleReader.branches()"
```

---

### Task 3: `WorkspaceBranchListing` and the catalog branch segment

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/catalog.py`
- Test: `tests/harness/ask_your_docs/test_catalog_branches.py`

**Interfaces:**
- Consumes: `IndexedBranch`, `BundleReader.branches()` (Task 2).
- Produces: `WorkspaceBranchListing(projects: Mapping[str, tuple[IndexedBranch, ...]], bundle_stems: frozenset[str])` with `has_projects`, `project_count`, `project_names`, `knows_project(name)`, `rows(project)`, `pickable(project)`, `merged(project)`, `default_row(project)`, `row(project, branch)`, `has_branch(project, branch)`, `head_sha(project, branch)`; `EMPTY_BRANCH_LISTING`; `CatalogService.branch_listing()`; `workspace_branch_listing(workspace)`; `render_catalog(catalog, branches=None, *, show_merged=False)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/harness/ask_your_docs/test_catalog_branches.py`:

```python
"""WorkspaceBranchListing + render_catalog(branches=) — AC-13, AC-14b."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch
from pydocs_mcp.harness.ask_your_docs.catalog import (
    EMPTY_BRANCH_LISTING,
    CatalogService,
    WorkspaceBranchListing,
    render_catalog,
)
from pydocs_mcp.models import BranchStatus

_SHA = "3e1a9c2" + "0" * 33


def _row(name, *, default=False, base=None, status=BranchStatus.ACTIVE, merged_into=None):
    return IndexedBranch(
        name=name,
        head_sha="a" * 40,
        base_name=base,
        is_default=default,
        status=status,
        merged_into=merged_into,
        landing_kind=None,
        indexed_at=1.0,
    )


_LISTING = WorkspaceBranchListing(
    projects={
        "backend": (
            _row("feature/retry", default=True, base="main"),
            _row("main"),
            _row("feature/old", base="main", status=BranchStatus.MERGED, merged_into=_SHA),
            _row(_SHA, base="main"),
        ),
        "tooling": (_row("main", default=True),),
    },
    bundle_stems=frozenset({"backend_0123456789", "tooling_abcdefabcd"}),
)
_CATALOG = {"backend": ["fastapi", "pydantic"], "tooling": []}


def test_pickable_excludes_landing_units_and_tombstones():
    assert [r.name for r in _LISTING.pickable("backend")] == ["feature/retry", "main"]
    assert [r.name for r in _LISTING.merged("backend")] == ["feature/old"]


def test_knows_project_accepts_names_and_bundle_stems():
    assert _LISTING.knows_project("backend")
    assert _LISTING.knows_project("backend_0123456789")
    assert not _LISTING.knows_project("frontend")


def test_default_row_and_lookups():
    assert _LISTING.default_row("backend").name == "feature/retry"
    assert _LISTING.has_branch("backend", "main")
    assert not _LISTING.has_branch("tooling", "feature/retry")
    assert _LISTING.has_branch("", "feature/retry")  # union: any project
    assert _LISTING.head_sha("backend", "main") == "a" * 40
    assert _LISTING.head_sha("backend", "nope") == ""


def test_render_catalog_without_branches_is_byte_identical():
    """AC-13."""
    assert render_catalog(_CATALOG) == render_catalog(_CATALOG, branches=None)
    assert render_catalog(_CATALOG) == (
        "- backend — dependency packages: fastapi, pydantic\n"
        "- tooling — own code only (no dependency packages indexed)"
    )


def test_render_catalog_with_branches_lists_pickable_rows_only():
    assert render_catalog(_CATALOG, branches=_LISTING) == (
        "- backend — branches: feature/retry (default), main — dependency packages: fastapi, pydantic\n"
        "- tooling — branches: main (default) — own code only (no dependency packages indexed)"
    )


def test_render_catalog_show_merged_appends_the_tombstone_marker():
    rendered = render_catalog(_CATALOG, branches=_LISTING, show_merged=True)
    assert (
        "branches: feature/retry (default), main, feature/old (merged into main @3e1a9c2) — "
        "dependency packages" in rendered
    )
    assert _SHA not in rendered


def test_empty_listing_renders_no_branch_segment():
    assert render_catalog(_CATALOG, branches=EMPTY_BRANCH_LISTING) == render_catalog(_CATALOG)


class _FakeReader:
    def __init__(self, db: Path) -> None:
        self._stem = db.stem

    def project_name(self) -> str:
        return self._stem.rsplit("_", 1)[0]

    def indexed_at(self) -> float:
        return 2.0 if self._stem.endswith("new") else 1.0

    def packages(self) -> list[str]:
        return []

    def branches(self) -> tuple[IndexedBranch, ...]:
        return (_row("main", default=True),) if self._stem.endswith("new") else (_row("old"),)


def test_branch_listing_newest_bundle_wins_and_collects_stems(tmp_path):
    (tmp_path / "backend_old").with_suffix(".db").write_bytes(b"")
    (tmp_path / "backend_new").with_suffix(".db").write_bytes(b"")
    listing = CatalogService(str(tmp_path), reader_factory=_FakeReader).branch_listing()
    assert [r.name for r in listing.rows("backend")] == ["main"]
    assert listing.bundle_stems == frozenset({"backend_old", "backend_new"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_catalog_branches.py -q`
Expected: FAIL — `ImportError: cannot import name 'EMPTY_BRANCH_LISTING'`.

- [ ] **Step 3: Implement the listing and the branch segment**

In `python/pydocs_mcp/harness/ask_your_docs/catalog.py`, extend the imports:

```python
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp.harness.ask_your_docs.bundle import BundleReader, IndexedBranch, SqliteBundleReader
from pydocs_mcp.models import BranchStatus
```

Add the value object before `CatalogService`:

```python
@dataclass(frozen=True, slots=True)
class WorkspaceBranchListing:
    """Every indexed project's ``branches`` rows, newest bundle per project.

    ``bundle_stems`` are the ``{project}_{slug}`` filename stems, the second
    form the server's ``project=`` selector accepts (multirepo.select_project).
    """

    projects: Mapping[str, tuple[IndexedBranch, ...]]
    bundle_stems: frozenset[str] = frozenset()

    @property
    def has_projects(self) -> bool:
        return bool(self.projects)

    @property
    def project_count(self) -> int:
        return len(self.projects)

    @property
    def project_names(self) -> tuple[str, ...]:
        return tuple(self.projects)

    def knows_project(self, name: str) -> bool:
        return name in self.projects or name in self.bundle_stems

    def rows(self, project: str) -> tuple[IndexedBranch, ...]:
        return self.projects.get(project, ())

    def pickable(self, project: str) -> tuple[IndexedBranch, ...]:
        """Live rows only — what the pickers and the catalog line list."""
        return tuple(
            r
            for r in self.rows(project)
            if r.status is BranchStatus.ACTIVE and not r.is_landing_unit
        )

    def merged(self, project: str) -> tuple[IndexedBranch, ...]:
        """Tombstones whose landing sha is known (the U2 "merged" group)."""
        return tuple(
            r
            for r in self.rows(project)
            if r.status in (BranchStatus.MERGED, BranchStatus.DELETED) and r.merged_into
        )

    def default_row(self, project: str) -> IndexedBranch | None:
        rows = self.rows(project)
        return next((r for r in rows if r.is_default), rows[0] if rows else None)

    def row(self, project: str, branch: str) -> IndexedBranch | None:
        return next((r for r in self.rows(project) if r.name == branch), None)

    def has_branch(self, project: str, branch: str) -> bool:
        if not project:  # a union request: any loaded project
            return any(self.row(name, branch) is not None for name in self.projects)
        return self.row(project, branch) is not None

    def head_sha(self, project: str, branch: str) -> str:
        row = self.row(project, branch)
        return row.head_sha if row else ""


EMPTY_BRANCH_LISTING = WorkspaceBranchListing(projects={})
```

Add to `CatalogService`:

```python
    def branch_listing(self) -> WorkspaceBranchListing:
        """Every project's branch rows (newest bundle wins) plus the bundle stems."""
        best: dict[str, tuple[float, tuple[IndexedBranch, ...]]] = {}
        stems: set[str] = set()
        for db in self._bundles():
            reader = self.reader_factory(db)
            name, indexed_at = reader.project_name(), reader.indexed_at()
            stems.add(db.stem)
            if name not in best or indexed_at > best[name][0]:
                best[name] = (indexed_at, reader.branches())
        projects = {name: rows for name, (_, rows) in sorted(best.items())}
        return WorkspaceBranchListing(projects=projects, bundle_stems=frozenset(stems))
```

Replace `render_catalog` and add the module wrapper:

```python
def workspace_branch_listing(workspace: str) -> WorkspaceBranchListing:
    """Project -> branch rows for the whole workspace (panel, popover, footer, prompt)."""
    return CatalogService(workspace).branch_listing()


def _branch_segment(project: str, branches: WorkspaceBranchListing | None, show_merged: bool) -> str:
    """``branches: main (default), feature/x — `` or ``""`` (nothing to list)."""
    if branches is None:
        return ""
    names = [f"{r.name} (default)" if r.is_default else r.name for r in branches.pickable(project)]
    if show_merged:
        default = branches.default_row(project)
        for r in branches.merged(project):
            # merged_into is the LANDING SHA, never a branch name (multi-branch §6.8a).
            base = r.base_name or (default.name if default else "base")
            names.append(f"{r.name} (merged into {base} @{str(r.merged_into)[:7]})")
    return f"branches: {', '.join(names)} — " if names else ""


def _catalog_line(
    name: str, packages: list[str], branches: WorkspaceBranchListing | None, show_merged: bool
) -> str:
    packages_text = (
        f"dependency packages: {', '.join(packages)}"
        if packages
        else "own code only (no dependency packages indexed)"
    )
    return f"- {name} — {_branch_segment(name, branches, show_merged)}{packages_text}"


def render_catalog(
    catalog: dict[str, list[str]],
    branches: WorkspaceBranchListing | None = None,
    *,
    show_merged: bool = False,
) -> str:
    """One line per project, naming the exact project=/branch=/package= values.

    ``branches=None`` renders today's bytes (AC-13); the branch segment is
    inserted between the project name and the package segment and lists
    pickable rows only; ``show_merged`` appends the U2 tombstone markers.
    """
    return "\n".join(
        _catalog_line(name, packages, branches, show_merged) for name, packages in catalog.items()
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/test_catalog_branches.py tests/harness/ask_your_docs/test_prompt_seam.py -q`
Expected: PASS (the prompt-seam byte-identity tests still pass — `render_catalog(catalog)` is unchanged).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/catalog.py tests/harness/ask_your_docs/test_catalog_branches.py
git commit -m "ask-your-docs: WorkspaceBranchListing + gated catalog branch segment"
```

---

### Task 4: `QuestionScope` value object, `AttachedSymbol`, and the moved `scope_prefix`

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/question_scope.py`
- Modify: `python/pydocs_mcp/harness/ask_your_docs/attachments.py` (add `AttachedSymbol`; `weave_attachments` accepts it)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/__init__.py:21-28` (`"scope_prefix": "question_scope"`)
- Test: `tests/harness/ask_your_docs/test_question_scope.py`

**Interfaces:**
- Consumes: `ScopeSlice`, `ScopeCode`, `ScopeBranchDefault`, `ANY_PROJECT`, `ScopeDefaultsConfig` (Task 1); `WorkspaceBranchListing` (Task 3).
- Produces: `ScopeKind {DEFAULT, PIN}`, `ScopeCell(project, branch)`, `QuestionScope(kind, cells, slice, code, package, branch_default, branch_name)` with `is_multi_branch`, `default_project`, `projects()`, `branches_for(project)`, `with_cells(cells)`, `without_cell(cell)`; `ScopeDefaultsOverride`; `resolve_question_scope_defaults(config, session, listing) -> QuestionScope`; `resolve_default_branch(scope, project, listing) -> str`; `scope_prefix(scope) -> str`; `scope_caption_text(scope) -> str`; `pin_summary_label(pin) -> str`; `pin_with_attached_symbols(pin, attached, defaults) -> QuestionScope | None`; `code_compatible_with_slice(slice, code) -> ScopeCode`; `log_scope_event(event, **fields)`; `SLICE_SERVER_VALUES`, `CODE_SERVER_VALUES`, `SLICE_LABELS`, `CODE_LABELS`; `AttachedSymbol(symbol, project="", branch="")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/harness/ask_your_docs/test_question_scope.py`:

```python
"""QuestionScope invariants, default resolution, prefix — AC-23, 28, 29, 30, 33."""

from __future__ import annotations

import json

import pytest

from pydocs_mcp.harness.ask_your_docs.attachments import AttachedSymbol, weave_attachments
from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch
from pydocs_mcp.harness.ask_your_docs.catalog import EMPTY_BRANCH_LISTING, WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    ScopeBranchDefault,
    ScopeCell,
    ScopeCode,
    ScopeDefaultsOverride,
    ScopeKind,
    ScopeSlice,
    pin_summary_label,
    pin_with_attached_symbols,
    resolve_default_branch,
    resolve_question_scope_defaults,
    scope_caption_text,
    scope_prefix,
)
from pydocs_mcp.models import BranchStatus
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig


def _row(name, *, default=False, base=None):
    return IndexedBranch(name, "a" * 40, base, default, BranchStatus.ACTIVE, None, None, 1.0)


_LISTING = WorkspaceBranchListing(
    projects={
        "backend": (_row("feature/x", default=True, base="main"), _row("main")),
        "tooling": (_row("main", default=True),),
    },
    bundle_stems=frozenset({"backend_0123456789"}),
)
_PIN = QuestionScope(
    kind=ScopeKind.PIN,
    cells=(ScopeCell("backend", "main"), ScopeCell("backend", "feature/retry")),
    slice=ScopeSlice.DIFF_HUNKS,
    code=ScopeCode.OWN,
)


class TestInvariants:  # AC-23
    def test_default_needs_exactly_one_empty_branch_cell(self):
        with pytest.raises(ValueError, match="DEFAULT scope holds exactly one cell"):
            QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("a", ""), ScopeCell("b", "")))
        with pytest.raises(ValueError, match="empty branch"):
            QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("p", "main"),))

    def test_cells_must_be_non_empty_and_unique(self):
        with pytest.raises(ValueError, match="cells is empty"):
            QuestionScope(kind=ScopeKind.PIN, cells=())
        with pytest.raises(ValueError, match="duplicates"):
            QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("p", "m"), ScopeCell("p", "m")))

    def test_slice_excludes_dependencies_only(self):
        with pytest.raises(ValueError) as excinfo:
            QuestionScope(
                kind=ScopeKind.PIN,
                cells=(ScopeCell("p", "m"),),
                slice=ScopeSlice.DIFF_HUNKS,
                code=ScopeCode.DEPS,
            )
        assert "diff_hunks" in str(excinfo.value) and "deps" in str(excinfo.value)

    def test_with_and_without_cells(self):
        grown = _PIN.with_cells((ScopeCell("backend", "main"), ScopeCell("tooling", "main")))
        assert grown.cells == (*_PIN.cells, ScopeCell("tooling", "main"))
        assert _PIN.with_cells(_PIN.cells) is _PIN
        one = _PIN.without_cell(ScopeCell("backend", "feature/retry"))
        assert one.cells == (ScopeCell("backend", "main"),)
        assert one.without_cell(ScopeCell("backend", "main")) is None


class TestPrefix:  # AC-28
    def test_two_branch_pin_renders_every_element(self):
        assert scope_prefix(_PIN) == (
            "[pinned scope: project=backend, branches=main, feature/retry, diff hunks, own code only] "
        )

    def test_default_renders_nothing(self):
        assert scope_prefix(QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),))) == ""
        assert scope_prefix(None) == ""

    def test_one_cell_pin_without_branch_is_todays_bytes(self):
        pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", ""),))
        assert scope_prefix(pin) == "[pinned scope: project=backend] "

    def test_caption_and_summary(self):
        assert scope_caption_text(_PIN) == "backend · main, feature/retry · diff hunks"
        assert pin_summary_label(_PIN) == "backend · 2 branches"
        two = QuestionScope(
            kind=ScopeKind.PIN, cells=(ScopeCell("a", "main"), ScopeCell("b", "main"))
        )
        assert pin_summary_label(two) == "2 projects"
        one = QuestionScope(
            kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"),), slice=ScopeSlice.DIFF_HUNKS
        )
        assert pin_summary_label(one) == "backend · main · diff hunks"
        assert pin_summary_label(None) == ""


class TestResolveDefaultBranch:  # AC-29
    def _scope(self, **kwargs):
        return QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("backend", ""),), **kwargs)

    def test_base_resolves_to_the_default_rows_base_when_listed_and_different(self):
        assert resolve_default_branch(self._scope(), "backend", _LISTING) == "main"
        assert resolve_default_branch(self._scope(), "tooling", _LISTING) == ""

    def test_checked_out_sends_nothing(self):
        scope = self._scope(branch_default=ScopeBranchDefault.CHECKED_OUT)
        assert resolve_default_branch(scope, "backend", _LISTING) == ""

    def test_named_default_wins_when_listed(self):
        assert resolve_default_branch(self._scope(branch_name="main"), "backend", _LISTING) == "main"

    def test_unlisted_name_resolves_to_nothing_and_logs(self, caplog):
        with caplog.at_level("INFO"):
            got = resolve_default_branch(self._scope(branch_name="gone"), "backend", _LISTING)
        assert got == ""
        record = json.loads(caplog.records[-1].getMessage())
        assert record == {
            "argument": "branch_name",
            "event": "scope_default_replaced",
            "passed": "gone",
            "replacement": "",
            "tool": "",
        }

    def test_union_project_sends_nothing(self):
        assert resolve_default_branch(self._scope(), "", _LISTING) == ""

    def test_p0_bundle_without_base_name_sends_nothing(self):
        listing = WorkspaceBranchListing(projects={"backend": (_row("main", default=True),)})
        assert resolve_default_branch(self._scope(), "backend", listing) == ""


class TestResolveDefaults:  # AC-33 layering
    def test_shipped_config_and_empty_override_give_the_union_cell(self):
        scope = resolve_question_scope_defaults(
            ScopeDefaultsConfig(), ScopeDefaultsOverride(), _LISTING
        )
        assert scope == QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),))

    def test_session_override_wins_over_yaml(self):
        scope = resolve_question_scope_defaults(
            ScopeDefaultsConfig(code=ScopeCode.OWN),
            ScopeDefaultsOverride(project="backend", code=ScopeCode.ALL, package="fastapi"),
            _LISTING,
        )
        assert scope.cells == (ScopeCell("backend", ""),)
        assert scope.code is ScopeCode.ALL and scope.package == "fastapi"

    def test_unknown_project_default_falls_back_to_the_union(self, caplog):
        with caplog.at_level("INFO"):
            scope = resolve_question_scope_defaults(
                ScopeDefaultsConfig(project="gone"), ScopeDefaultsOverride(), _LISTING
            )
        assert scope.cells == (ScopeCell("", ""),)
        assert "scope_default_replaced" in caplog.records[-1].getMessage()

    def test_empty_listing_keeps_a_named_project(self):
        # Nothing scanned yet (CLI / tests): the name passes through unchecked.
        scope = resolve_question_scope_defaults(
            ScopeDefaultsConfig(project="backend"), ScopeDefaultsOverride(), EMPTY_BRANCH_LISTING
        )
        assert scope.cells == (ScopeCell("backend", ""),)


class TestAttachedSymbols:  # AC-30
    def test_attaching_with_no_pin_creates_a_one_shot_cell_pin(self):
        defaults = resolve_question_scope_defaults(
            ScopeDefaultsConfig(), ScopeDefaultsOverride(), _LISTING
        )
        attached = [AttachedSymbol("mod.Foo", "backend", "feature/retry")]
        pin = pin_with_attached_symbols(None, attached, defaults)
        assert pin.kind is ScopeKind.PIN and pin.cells == (ScopeCell("backend", "feature/retry"),)
        assert weave_attachments(attached, "what is it?") == "Regarding `mod.Foo`: what is it?"

    def test_attaching_under_a_pin_adds_the_cell_once(self):
        attached = [AttachedSymbol("mod.Foo", "backend", "main"), AttachedSymbol("mod.Bar", "tooling", "main")]
        pin = pin_with_attached_symbols(_PIN, attached, _PIN)
        assert pin.cells == (*_PIN.cells, ScopeCell("tooling", "main"))

    def test_plain_string_attachments_still_weave(self):
        assert weave_attachments(["a.B", "a.B", ""], "q") == "Regarding `a.B`: q"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_question_scope.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pydocs_mcp.harness.ask_your_docs.question_scope'`.

- [ ] **Step 3: Add `AttachedSymbol` to `attachments.py`**

After `ImageAttachment`:

```python
@dataclass(frozen=True, slots=True)
class AttachedSymbol:
    """A symbol attached from the graph page, with the cell it was read from
    (UI spec R8) — so the woven question and the tool calls agree on the branch."""

    symbol: str
    project: str = ""
    branch: str = ""
```

Replace `weave_attachments`:

```python
def _attached_name(attachment: AttachedSymbol | str) -> str:
    return attachment.symbol if isinstance(attachment, AttachedSymbol) else attachment


def weave_attachments(attached: Sequence[AttachedSymbol | str], question: str) -> str:
    """Prepend de-duped attached symbols to a question as plain context text."""
    seen: dict[str, None] = {}
    for a in attached:
        if name := _attached_name(a):
            seen.setdefault(name, None)
    if not seen:
        return question
    names = ", ".join(f"`{a}`" for a in seen)
    return f"Regarding {names}: {question}"
```

with `from collections.abc import Sequence` added to the imports and `"AttachedSymbol"` added to `__all__`.

- [ ] **Step 4: Create `question_scope.py`**

```python
"""The per-question scope of the ask-your-docs agent (UI spec §6.1–§6.2).

Two kinds: DEFAULT (soft — fills what the model left empty; one cell whose
branch is resolved per call) and PIN (hard — overwrites and fans out over
its cells). Light module by contract: no streamlit / langgraph imports.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypeVar

from pydocs_mcp.harness.ask_your_docs.attachments import AttachedSymbol
from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.retrieval.config.ask_your_docs_models import (
    ANY_PROJECT,
    ScopeBranchDefault,
    ScopeCode,
    ScopeDefaultsConfig,
    ScopeSlice,
)

logger = logging.getLogger(__name__)
_T = TypeVar("_T")

# Server spellings: multi-branch spec §6.5 for the slices; today's agent rule
# for the code filter (OWN is the server's "project").
SLICE_SERVER_VALUES: dict[ScopeSlice, str] = {
    ScopeSlice.CHANGED_FILES: "changed",
    ScopeSlice.DIFF_HUNKS: "diff",
}
CODE_SERVER_VALUES: dict[ScopeCode, str] = {ScopeCode.OWN: "project", ScopeCode.DEPS: "deps"}
# Human labels shared by the footer, the chips, the caption and the pinned note.
SLICE_LABELS: dict[ScopeSlice, str] = {
    ScopeSlice.WHOLE_BRANCH: "whole branch",
    ScopeSlice.CHANGED_FILES: "changed files",
    ScopeSlice.DIFF_HUNKS: "diff hunks",
}
CODE_LABELS: dict[ScopeCode, str] = {
    ScopeCode.ALL: "all code",
    ScopeCode.OWN: "own code only",
    ScopeCode.DEPS: "dependencies only",
}


def log_scope_event(event: str, **fields: object) -> None:
    """One structured JSON log line per scope event (named fields, sorted keys)."""
    logger.info(json.dumps({"event": event, **fields}, sort_keys=True, default=str))


def _ordered_unique(values: Iterable[_T]) -> tuple[_T, ...]:
    return tuple(dict.fromkeys(values))


class ScopeKind(StrEnum):
    DEFAULT = "default"
    PIN = "pin"


@dataclass(frozen=True, slots=True)
class ScopeCell:
    project: str  # "" = union across loaded projects (mcp_inputs SearchInput.project)
    branch: str  # "" = let the server resolve


@dataclass(frozen=True, slots=True)
class QuestionScope:
    """Exactly one is active per question (UI spec §6.1)."""

    kind: ScopeKind
    cells: tuple[ScopeCell, ...]
    slice: ScopeSlice = ScopeSlice.WHOLE_BRANCH
    code: ScopeCode = ScopeCode.ALL
    package: str = ""
    # DEFAULT only: the branch is resolved lazily per call against the
    # effective project (resolve_default_branch). Ignored under PIN.
    branch_default: ScopeBranchDefault = ScopeBranchDefault.BASE
    branch_name: str = ""

    def __post_init__(self) -> None:
        if not self.cells:
            raise ValueError(
                f"QuestionScope.cells is empty; expected at least one (project, branch) "
                f"cell for kind={self.kind.value!r}"
            )
        if len(set(self.cells)) != len(self.cells):
            raise ValueError(f"QuestionScope.cells has duplicates: {self.cells!r}")
        if self.kind is ScopeKind.DEFAULT and (len(self.cells) != 1 or self.cells[0].branch):
            raise ValueError(
                "a DEFAULT scope holds exactly one cell with an empty branch "
                f"(resolved per call), got cells={self.cells!r}"
            )
        if self.slice is not ScopeSlice.WHOLE_BRANCH and self.code is ScopeCode.DEPS:
            raise ValueError(
                f"slice={self.slice.value!r} cannot combine with code={self.code.value!r}; "
                "expected code 'all' or 'own' with a slice"
            )

    @property
    def is_multi_branch(self) -> bool:
        return len(self.cells) > 1

    @property
    def default_project(self) -> str:
        return self.cells[0].project

    def projects(self) -> tuple[str, ...]:
        return _ordered_unique(c.project for c in self.cells if c.project)

    def branches_for(self, project: str) -> tuple[str, ...]:
        return tuple(c.branch for c in self.cells if c.project == project and c.branch)

    def with_cells(self, cells: Iterable[ScopeCell]) -> QuestionScope:
        """This scope plus the cells it lacks (cells are a set; order kept)."""
        missing = tuple(c for c in _ordered_unique(cells) if c not in self.cells)
        return replace(self, cells=(*self.cells, *missing)) if missing else self

    def without_cell(self, cell: ScopeCell) -> QuestionScope | None:
        """This scope minus ``cell``; ``None`` when that was the last cell."""
        rest = tuple(c for c in self.cells if c != cell)
        return replace(self, cells=rest) if rest else None


@dataclass(frozen=True, slots=True)
class ScopeDefaultsOverride:
    """The "Scope defaults" panel's session values; ``None`` = use YAML."""

    project: str | None = None
    branch_default: ScopeBranchDefault | None = None
    branch_name: str | None = None
    slice: ScopeSlice | None = None
    code: ScopeCode | None = None
    package: str | None = None


def _pick(override: _T | None, default: _T) -> _T:
    return default if override is None else override


def resolve_question_scope_defaults(
    config: ScopeDefaultsConfig,
    session: ScopeDefaultsOverride,
    listing: WorkspaceBranchListing,
) -> QuestionScope:
    """YAML + panel override + listing -> the DEFAULT scope (one project cell).

    A named project the listing does not know falls back to the union and
    logs; an empty listing (nothing scanned) passes the name through.
    """
    project = _pick(session.project, config.project)
    cell_project = "" if project == ANY_PROJECT else project
    if cell_project and listing.has_projects and not listing.knows_project(cell_project):
        log_scope_event(
            "scope_default_replaced", tool="", argument="project", passed=cell_project, replacement=""
        )
        cell_project = ""
    return QuestionScope(
        kind=ScopeKind.DEFAULT,
        cells=(ScopeCell(cell_project, ""),),
        slice=_pick(session.slice, config.slice),
        code=_pick(session.code, config.code),
        package=_pick(session.package, config.package),
        branch_default=_pick(session.branch_default, config.branch_default),
        branch_name=_pick(session.branch_name, config.branch_name),
    )


def resolve_default_branch(
    scope: QuestionScope, project: str, listing: WorkspaceBranchListing
) -> str:
    """The branch to inject for a DEFAULT call on ``project``; ``""`` = nothing.

    Rules (UI spec §6.2): union -> nothing; a listed ``branch_name`` -> itself
    (unlisted -> nothing + log); BASE -> the default row's ``base_name`` when
    listed and different from the default row; CHECKED_OUT -> nothing.
    """
    if not project:
        return ""
    if scope.branch_name:
        if listing.has_branch(project, scope.branch_name):
            return scope.branch_name
        log_scope_event(
            "scope_default_replaced",
            tool="",
            argument="branch_name",
            passed=scope.branch_name,
            replacement="",
        )
        return ""
    if scope.branch_default is ScopeBranchDefault.CHECKED_OUT:
        return ""
    row = listing.default_row(project)
    if row is None or not row.base_name or row.base_name == row.name:
        return ""
    return row.base_name if listing.has_branch(project, row.base_name) else ""


def _named_parts(scope: QuestionScope) -> list[str]:
    parts: list[str] = []
    projects = scope.projects()
    if len(projects) == 1:
        parts.append(f"project={projects[0]}")
    elif projects:
        parts.append(f"projects={', '.join(projects)}")
    branches = _ordered_unique(c.branch for c in scope.cells if c.branch)
    if len(branches) == 1:
        parts.append(f"branch={branches[0]}")
    elif branches:
        parts.append(f"branches={', '.join(branches)}")
    return parts


def scope_prefix(scope: QuestionScope | None) -> str:
    """The "[pinned scope: ...]" note prepended to a question, or "" (defaults are silent)."""
    if scope is None or scope.kind is ScopeKind.DEFAULT:
        return ""
    parts = _named_parts(scope)
    if scope.package:
        parts.append(f"package={scope.package}")
    if scope.slice is not ScopeSlice.WHOLE_BRANCH:
        parts.append(SLICE_LABELS[scope.slice])
    if scope.code is not ScopeCode.ALL:
        parts.append(CODE_LABELS[scope.code])
    return f"[pinned scope: {', '.join(parts)}] " if parts else ""


def scope_caption_text(scope: QuestionScope | None) -> str:
    """The transcript's scope chip: ``backend · main, feature/retry · diff hunks``."""
    if scope is None or scope.kind is ScopeKind.DEFAULT:
        return ""
    groups = []
    for project in scope.projects() or ("",):
        branches = scope.branches_for(project)
        label = project or "all projects"
        groups.append(f"{label} · {', '.join(branches)}" if branches else label)
    text = " | ".join(groups)
    if scope.slice is not ScopeSlice.WHOLE_BRANCH:
        text = f"{text} · {SLICE_LABELS[scope.slice]}"
    return text


def pin_summary_label(pin: QuestionScope | None) -> str:
    """The popover button's label while a pin is active (UI spec §6.4a)."""
    if pin is None:
        return ""
    projects = pin.projects()
    if len(projects) > 1:
        return f"{len(projects)} projects"
    project = projects[0] if projects else "all projects"
    branches = pin.branches_for(project)
    if len(branches) > 1:
        return f"{project} · {len(branches)} branches"
    label = f"{project} · {branches[0]}" if branches else project
    if pin.slice is not ScopeSlice.WHOLE_BRANCH:
        label = f"{label} · {SLICE_LABELS[pin.slice]}"
    return label


def code_compatible_with_slice(slice_value: ScopeSlice, code: ScopeCode) -> ScopeCode:
    """E11: a slice never combines with dependencies-only — widen to ALL."""
    if slice_value is not ScopeSlice.WHOLE_BRANCH and code is ScopeCode.DEPS:
        return ScopeCode.ALL
    return code


def pin_with_attached_symbols(
    pin: QuestionScope | None,
    attached: Sequence[AttachedSymbol | str],
    defaults: QuestionScope,
) -> QuestionScope | None:
    """Fold attached symbols' cells into the pin (UI spec §6.11, AC-30).

    No pin + attached cells -> a one-shot PIN over those cells (slice / code /
    package from ``defaults``); an active pin gains each cell once.
    """
    cells = tuple(
        ScopeCell(a.project, a.branch)
        for a in attached
        if isinstance(a, AttachedSymbol) and a.project
    )
    if not cells:
        return pin
    if pin is not None:
        return pin.with_cells(cells)
    return QuestionScope(
        kind=ScopeKind.PIN,
        cells=_ordered_unique(cells),
        slice=defaults.slice,
        code=defaults.code,
        package=defaults.package,
    )


__all__ = (
    "CODE_LABELS",
    "CODE_SERVER_VALUES",
    "SLICE_LABELS",
    "SLICE_SERVER_VALUES",
    "QuestionScope",
    "ScopeBranchDefault",
    "ScopeCell",
    "ScopeCode",
    "ScopeDefaultsOverride",
    "ScopeKind",
    "ScopeSlice",
    "code_compatible_with_slice",
    "log_scope_event",
    "pin_summary_label",
    "pin_with_attached_symbols",
    "resolve_default_branch",
    "resolve_question_scope_defaults",
    "scope_caption_text",
    "scope_prefix",
)
```

- [ ] **Step 5: Re-point the lazy export**

In `python/pydocs_mcp/harness/ask_your_docs/__init__.py` change `"scope_prefix": "agent",` to `"scope_prefix": "question_scope",`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/test_question_scope.py tests/harness/ask_your_docs/test_attachment.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/question_scope.py python/pydocs_mcp/harness/ask_your_docs/attachments.py python/pydocs_mcp/harness/ask_your_docs/__init__.py tests/harness/ask_your_docs/test_question_scope.py
git commit -m "ask-your-docs: QuestionScope value object, default-branch resolution, AttachedSymbol"
```

---

### Task 5: `ScopeCapabilities` and `BuiltAgent`

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/scope_capabilities.py`
- Test: `tests/harness/ask_your_docs/test_scope_capabilities.py`

**Interfaces:**
- Produces: `ScopeCapabilities(branch_selector, changed_slice, diff_slice)`, `NO_SCOPE_CAPABILITIES`, `inspect_scope_capabilities(tools) -> ScopeCapabilities`, `BuiltAgent(graph, llm, scope_capabilities)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/harness/ask_your_docs/test_scope_capabilities.py`:

```python
"""inspect_scope_capabilities over the registration golden — AC-15."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    BuiltAgent,
    ScopeCapabilities,
    inspect_scope_capabilities,
)

_GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "goldens" / "mcp_registration_surface.json"


@dataclass(frozen=True)
class _SchemaTool:
    name: str
    args_schema: object


def _golden_tools() -> list[_SchemaTool]:
    surface = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    return [_SchemaTool(name, copy.deepcopy(entry["inputSchema"])) for name, entry in surface.items()]


def test_todays_surface_advertises_nothing():
    assert inspect_scope_capabilities(_golden_tools()) == NO_SCOPE_CAPABILITIES
    assert inspect_scope_capabilities([]) == NO_SCOPE_CAPABILITIES


def test_branch_on_every_tool_enables_the_selector():
    tools = _golden_tools()
    for tool in tools:
        tool.args_schema["properties"]["branch"] = {"type": "string", "default": ""}
    assert inspect_scope_capabilities(tools).branch_selector is True
    tools[0].args_schema["properties"].pop("branch")
    assert inspect_scope_capabilities(tools).branch_selector is False


def test_slice_values_are_read_from_search_and_grep_scope_enums():
    tools = {t.name: t for t in _golden_tools()}
    tools["search_codebase"].args_schema["properties"]["scope"]["enum"] = ["project", "deps", "all", "changed"]
    caps = inspect_scope_capabilities(list(tools.values()))
    assert caps == ScopeCapabilities(branch_selector=False, changed_slice=True, diff_slice=False)
    tools["search_codebase"].args_schema["properties"]["scope"]["enum"].append("diff")
    assert inspect_scope_capabilities(list(tools.values())).diff_slice is False  # grep lacks it
    tools["grep"].args_schema["properties"]["scope"]["enum"] = ["project", "deps", "all", "diff"]
    assert inspect_scope_capabilities(list(tools.values())).diff_slice is True


def test_non_mapping_schemas_are_ignored():
    class _PydanticLike:  # a StructuredTool.from_function tool carries a model class
        pass

    tools = [*_golden_tools(), _SchemaTool("reinspect_images", _PydanticLike)]
    for tool in tools[:-1]:
        tool.args_schema["properties"]["branch"] = {"type": "string"}
    assert inspect_scope_capabilities(tools).branch_selector is True


def test_built_agent_is_a_frozen_record():
    built = BuiltAgent(graph="G", llm="L", scope_capabilities=NO_SCOPE_CAPABILITIES)
    assert (built.graph, built.llm) == ("G", "L")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_scope_capabilities.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `scope_capabilities.py`**

```python
"""What the server advertises for scope arguments (UI spec §6.12).

Read once per agent build from each loaded tool's ``args_schema`` — the
adapter sets it to the raw MCP ``inputSchema`` dict. The page hides every
control whose capability is false, and the interceptor never sends an
argument the capability does not cover, so a P0 server never sees ``branch``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScopeCapabilities:
    branch_selector: bool  # "branch" in every tool's inputSchema properties
    changed_slice: bool  # "changed" in search_codebase's scope enum
    diff_slice: bool  # "diff" in search_codebase's AND grep's scope enum


NO_SCOPE_CAPABILITIES = ScopeCapabilities(branch_selector=False, changed_slice=False, diff_slice=False)


@dataclass(frozen=True, slots=True)
class BuiltAgent:
    """``build_agent_with_scope_capabilities``'s result — the graph, the llm,
    and the capability record the page and the interceptor read."""

    graph: object
    llm: object
    scope_capabilities: ScopeCapabilities


def _properties(schema: object) -> Mapping[str, object]:
    props = schema.get("properties") if isinstance(schema, Mapping) else None
    return props if isinstance(props, Mapping) else {}


def _enum_values(schema: Mapping[str, object], name: str) -> tuple[str, ...]:
    """The enum of property ``name``: inline, hoisted into ``$defs``, or under ``anyOf``."""
    prop = _properties(schema).get(name)
    if not isinstance(prop, Mapping):
        return ()
    if isinstance(prop.get("enum"), list):
        return tuple(str(v) for v in prop["enum"])
    ref = prop.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        defs = schema.get("$defs")
        definition = defs.get(ref.rsplit("/", 1)[-1], {}) if isinstance(defs, Mapping) else {}
        return tuple(str(v) for v in definition.get("enum", ()))
    options = prop.get("anyOf", ())
    return tuple(
        str(v)
        for option in options
        if isinstance(option, Mapping)
        for v in option.get("enum", ())
    )


def inspect_scope_capabilities(tools: Sequence[object]) -> ScopeCapabilities:
    """The capability record for a loaded tool list (non-dict schemas are ignored)."""
    schemas = {
        str(getattr(tool, "name", "")): getattr(tool, "args_schema", None) for tool in tools
    }
    dict_schemas = {name: s for name, s in schemas.items() if isinstance(s, Mapping)}
    if not dict_schemas:
        return NO_SCOPE_CAPABILITIES
    search_scope = _enum_values(dict_schemas.get("search_codebase", {}), "scope")
    grep_scope = _enum_values(dict_schemas.get("grep", {}), "scope")
    return ScopeCapabilities(
        branch_selector=all("branch" in _properties(s) for s in dict_schemas.values()),
        changed_slice="changed" in search_scope,
        diff_slice="diff" in search_scope and "diff" in grep_scope,
    )


__all__ = (
    "NO_SCOPE_CAPABILITIES",
    "BuiltAgent",
    "ScopeCapabilities",
    "inspect_scope_capabilities",
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/test_scope_capabilities.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/scope_capabilities.py tests/harness/ask_your_docs/test_scope_capabilities.py
git commit -m "ask-your-docs: ScopeCapabilities read from advertised tool schemas"
```

---

### Task 6: The scope interceptor — U0 rules, fan-out, merging, observations

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/scope_interceptor.py`
- Test: `tests/harness/ask_your_docs/test_scope_interceptor.py`

**Interfaces:**
- Consumes: `QuestionScope`, `ScopeCell`, `ScopeKind`, `ScopeCode`, `ScopeSlice`, `SLICE_SERVER_VALUES`, `CODE_SERVER_VALUES`, `log_scope_event`, `resolve_default_branch` (Task 4); `WorkspaceBranchListing`, `EMPTY_BRANCH_LISTING` (Task 3); `ScopeCapabilities`, `NO_SCOPE_CAPABILITIES` (Task 5); `ScopeDefaultsConfig` (Task 1).
- Produces: contextvars `ACTIVE_QUESTION_SCOPE`, `ACTIVE_SCOPE_RUNTIME`, `ACTIVE_SCOPE_OBSERVATIONS`; `ScopeRuntime(listing, capabilities, max_cells)`, `EMPTY_SCOPE_RUNTIME`; `BranchOrigin {DEFAULT, PINNED, AGENT_CHOSEN, SERVER}`; `CellObservation(tool, project, branch, branch_origin, slice, meta, replaced=False, is_error=False)`; `ScopeObservations` with `append`, `records()`, `by_cell()`, `__len__`; `PACKAGE_TOOLS`, `SLICE_TOOLS`; `intercept_question_scope(request, handler)`; `target_cells(tool, args, scope, capabilities)`; `cell_arguments(args, cell, capabilities)`; `fan_out_over_cells(request, handler, args, cells, runtime, observations)`; `merge_cell_results(cells, results)`; `cell_label(cell)`; `too_many_cells_result(count, cap)`.
- The request type is duck-typed (`name`, `args`, `override(args=...)`) so the tests run without the adapter installed; the branch rules of this file are the U0 stubs — Task 12 replaces `_default_branch` and Task 17 replaces `_scope_argument_value` with their full bodies.

- [ ] **Step 1: Write the failing tests**

Create `tests/harness/ask_your_docs/test_scope_interceptor.py`:

```python
"""The scope interceptor — AC-1, 2, 3, 4, 6b, 8, 9, 10 (U0 rules)."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
from dataclasses import dataclass, replace
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch
from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    ScopeCell,
    ScopeCode,
    ScopeKind,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.harness.ask_your_docs.scope_interceptor import (
    ACTIVE_QUESTION_SCOPE,
    ACTIVE_SCOPE_OBSERVATIONS,
    ACTIVE_SCOPE_RUNTIME,
    BranchOrigin,
    ScopeObservations,
    ScopeRuntime,
    intercept_question_scope,
)
from pydocs_mcp.models import BranchStatus

NINE = (
    "get_overview",
    "search_codebase",
    "get_symbol",
    "get_context",
    "get_references",
    "get_why",
    "grep",
    "glob",
    "read_file",
)
BRANCHED = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)


def _row(name, *, default=False, base=None):
    return IndexedBranch(name, "a" * 40, base, default, BranchStatus.ACTIVE, None, None, 1.0)


LISTING = WorkspaceBranchListing(
    projects={
        "backend": (_row("feature/x", default=True, base="main"), _row("main")),
        "tooling": (_row("main", default=True),),
    },
    bundle_stems=frozenset({"backend_0123456789"}),
)


@dataclass(frozen=True)
class FakeRequest:
    """The adapter's MCPToolCallRequest shape: name, args, override(args=)."""

    name: str
    args: dict[str, Any]
    server_name: str = "pydocs"

    def override(self, **overrides: Any) -> FakeRequest:
        return replace(self, **overrides)


def _result(text: str = "ok", *, meta: dict | None = None, items=(), error: bool = False) -> CallToolResult:
    if error:
        return CallToolResult(content=[TextContent(type="text", text=text)], isError=True)
    structured = {"text": text, "items": list(items), "meta": {"tool": "t", "project": "backend", **(meta or {})}}
    return CallToolResult(content=[TextContent(type="text", text=text)], structuredContent=structured)


class RecordingHandler:
    """Records every request it receives; answers from a queue (or a default)."""

    def __init__(self, results: list[CallToolResult] | None = None) -> None:
        self.requests: list[FakeRequest] = []
        self.results = list(results or [])

    async def __call__(self, request: FakeRequest) -> CallToolResult:
        self.requests.append(request)
        return self.results.pop(0) if self.results else _result(f"answer for {request.args}")

    @property
    def sent(self) -> list[dict[str, Any]]:
        return [r.args for r in self.requests]


@contextlib.contextmanager
def active(scope, runtime=None, observations=None):
    tokens = (
        ACTIVE_QUESTION_SCOPE.set(scope),
        ACTIVE_SCOPE_RUNTIME.set(runtime),
        ACTIVE_SCOPE_OBSERVATIONS.set(observations),
    )
    try:
        yield
    finally:
        ACTIVE_QUESTION_SCOPE.reset(tokens[0])
        ACTIVE_SCOPE_RUNTIME.reset(tokens[1])
        ACTIVE_SCOPE_OBSERVATIONS.reset(tokens[2])


def call(tool: str, args: dict, handler: RecordingHandler) -> CallToolResult:
    return asyncio.run(intercept_question_scope(FakeRequest(tool, dict(args)), handler))


DEFAULT_UNION = QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),))
RUNTIME_U0 = ScopeRuntime(listing=LISTING, capabilities=NO_SCOPE_CAPABILITIES, max_cells=4)


@pytest.mark.parametrize("tool", NINE)
def test_ac1_no_active_question_is_a_strict_passthrough(tool):
    handler = RecordingHandler()
    args = {"project": "unknown-project", "scope": "deps", "package": "x", "branch": "b"}
    call(tool, args, handler)
    assert handler.sent == [args]


@pytest.mark.parametrize("tool", NINE)
@pytest.mark.parametrize("project", ["", "backend", "backend_0123456789"])
def test_ac2_shipped_defaults_send_the_models_arguments(tool, project):
    handler = RecordingHandler()
    args = {"query": "q", "project": project} if project else {"query": "q"}
    with active(DEFAULT_UNION, RUNTIME_U0):
        call(tool, args, handler)
    assert handler.sent == [args]


def test_ac3_unknown_project_is_replaced_and_logged(caplog):
    handler = RecordingHandler()
    with active(DEFAULT_UNION, RUNTIME_U0), caplog.at_level("INFO"):
        call("search_codebase", {"query": "q", "project": "frontend"}, handler)
    assert handler.sent == [{"query": "q", "project": ""}]
    record = json.loads(caplog.records[-1].getMessage())
    assert record == {
        "argument": "project",
        "event": "scope_default_replaced",
        "passed": "frontend",
        "replacement": "",
        "tool": "search_codebase",
    }


def test_ac3_named_default_replaces_with_that_project():
    scope = QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("tooling", ""),))
    handler = RecordingHandler()
    with active(scope, RUNTIME_U0):
        call("get_symbol", {"target": "a.b", "project": "frontend"}, handler)
        call("get_symbol", {"target": "a.b"}, handler)
    assert handler.sent == [{"target": "a.b", "project": "tooling"}] * 2


def test_ac3_empty_listing_never_replaces():
    handler = RecordingHandler()
    runtime = ScopeRuntime(listing=WorkspaceBranchListing({}), capabilities=NO_SCOPE_CAPABILITIES, max_cells=4)
    with active(DEFAULT_UNION, runtime):
        call("grep", {"pattern": "x", "project": "whatever"}, handler)
    assert handler.sent == [{"pattern": "x", "project": "whatever"}]


def test_ac4_code_default_touches_search_codebase_only():
    scope = QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),), code=ScopeCode.OWN)
    handler = RecordingHandler()
    with active(scope, RUNTIME_U0):
        call("search_codebase", {"query": "q"}, handler)
        call("grep", {"pattern": "p"}, handler)
        call("search_codebase", {"query": "q", "scope": "deps"}, handler)
    assert handler.sent == [
        {"query": "q", "scope": "project"},
        {"pattern": "p"},
        {"query": "q", "scope": "deps"},
    ]


def test_default_package_is_injected_only_when_omitted():
    scope = QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),), package="fastapi")
    handler = RecordingHandler()
    with active(scope, RUNTIME_U0):
        call("get_overview", {}, handler)
        call("get_overview", {"package": "pydantic"}, handler)
        call("get_symbol", {"target": "a"}, handler)
    assert handler.sent == [{"package": "fastapi"}, {"package": "pydantic"}, {"target": "a"}]


def test_ac6b_two_project_pin_fans_out_over_project_only():
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"), ScopeCell("tooling", "main")))
    handler = RecordingHandler([_result("A"), _result("B")])
    with active(pin, RUNTIME_U0):
        merged = call("search_codebase", {"query": "q", "branch": "main"}, handler)
    assert handler.sent == [{"query": "q", "project": "backend"}, {"query": "q", "project": "tooling"}]
    texts = [b.text for b in merged.content]
    assert texts == ["## backend\n", "A", "## tooling\n", "B"]
    assert merged.structuredContent["text"] == "## backend\nA\n## tooling\nB"
    assert merged.isError is False


def test_ac8_fan_out_over_the_cap_is_refused_before_any_call():
    pin = QuestionScope(
        kind=ScopeKind.PIN,
        cells=(ScopeCell("a", "m"), ScopeCell("b", "m"), ScopeCell("c", "m")),
    )
    handler = RecordingHandler()
    runtime = ScopeRuntime(listing=LISTING, capabilities=NO_SCOPE_CAPABILITIES, max_cells=2)
    with active(pin, runtime):
        result = call("get_overview", {}, handler)
    assert handler.sent == []
    assert result.isError is True
    text = result.content[0].text
    assert "max_cells=2" in text and "ask_your_docs.scope.max_cells" in text


def test_ac9_partial_failure_keeps_the_error_text_under_its_label():
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("a", "m"), ScopeCell("b", "m")))
    handler = RecordingHandler([_result("fine"), _result("boom", error=True)])
    with active(pin, RUNTIME_U0):
        merged = call("get_overview", {}, handler)
    assert merged.isError is False
    assert [b.text for b in merged.content] == ["## a\n", "fine", "## b\n", "boom"]
    assert merged.structuredContent["text"] == "## a\nfine\n## b\nboom"
    handler = RecordingHandler([_result("x", error=True), _result("y", error=True)])
    with active(pin, RUNTIME_U0):
        merged = call("get_overview", {}, handler)
    assert merged.isError is True


def test_merged_items_carry_project_and_branch_and_meta_is_the_first_cells():
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("a", "m"), ScopeCell("b", "n")))
    handler = RecordingHandler(
        [_result("A", items=[{"id": 1}], meta={"branch": "m"}), _result("B", items=[{"id": 2}], meta={"branch": "n"})]
    )
    with active(pin, RUNTIME_U0):
        merged = call("search_codebase", {"query": "q"}, handler)
    assert merged.structuredContent["items"] == [
        {"id": 1, "project": "a", "branch": "m"},
        {"id": 2, "project": "b", "branch": "n"},
    ]
    assert merged.structuredContent["meta"] == {"tool": "t", "project": "backend", "branch": "m"}


def test_ac10_observations_record_origin_per_call():
    observations = ScopeObservations()
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"),))
    handler = RecordingHandler([_result("A", meta={"branch": "main", "index_stale": True})])
    with active(pin, RUNTIME_U0, observations):
        call("get_symbol", {"target": "x"}, handler)
    (record,) = observations.records()
    assert (record.tool, record.project, record.branch_origin) == ("get_symbol", "backend", BranchOrigin.PINNED)
    assert record.meta["index_stale"] is True and record.branch == ""  # nothing sent on U0
    observations = ScopeObservations()
    with active(DEFAULT_UNION, RUNTIME_U0, observations):
        call("get_symbol", {"target": "x"}, handler)
    assert observations.records()[0].branch_origin is BranchOrigin.SERVER


def test_ac10_copied_context_child_task_populates_the_same_container():
    """The tool node runs interceptors in child tasks with copied contexts."""
    observations = ScopeObservations()
    handler = RecordingHandler()

    async def _run() -> None:
        with active(DEFAULT_UNION, RUNTIME_U0, observations):
            task = asyncio.create_task(
                intercept_question_scope(FakeRequest("grep", {"pattern": "p"}), handler),
                context=contextvars.copy_context(),
            )
            await task

    asyncio.run(_run())
    assert len(observations) == 1


def test_by_cell_groups_sorted():
    observations = ScopeObservations()
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("b", ""), ScopeCell("a", "")))
    with active(pin, RUNTIME_U0, observations):
        call("get_overview", {}, RecordingHandler())
    assert list(observations.by_cell()) == [("a", ""), ("b", "")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_scope_interceptor.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `scope_interceptor.py`**

```python
"""Apply the active QuestionScope to every MCP tool call (UI spec §6.3–§6.5).

Defaults fill what the model left empty; pins overwrite and fan out over
(project, branch) cells with labeled, merged results. The interceptor runs
in a child task with a COPIED context (langchain-core copies the config
context per tool call), so it reads the scope from contextvars that
``ask()`` set and reports back through in-place mutation of a container
``ask()`` created — the ``_reinspect_state`` precedent in agent.py.

Strict passthrough when no question is active: the eval binding invokes
the graph directly and never calls ``ask()``, so every call it makes goes
through unchanged (R11).
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from mcp.types import CallToolResult, TextContent

from pydocs_mcp.harness.ask_your_docs.catalog import EMPTY_BRANCH_LISTING, WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    CODE_SERVER_VALUES,
    QuestionScope,
    ScopeCell,
    ScopeCode,
    ScopeKind,
    ScopeSlice,
    log_scope_event,
    resolve_default_branch,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

# Which corpus filters each tool accepts (pydocs_mcp.server): ``project`` on all
# nine; ``package`` on the two below; ``scope`` on search_codebase (code
# filter) and, from multi-branch P2, the changed/diff slices on both of
# SLICE_TOOLS.
PACKAGE_TOOLS = frozenset({"search_codebase", "get_overview"})
SLICE_TOOLS = frozenset({"search_codebase", "grep"})


class ToolCallRequestLike(Protocol):
    """The adapter's ``MCPToolCallRequest`` shape (duck-typed for tests)."""

    name: str
    args: dict[str, Any]

    def override(self, **overrides: Any) -> ToolCallRequestLike: ...


ToolCallHandler = Callable[[ToolCallRequestLike], Awaitable[CallToolResult]]


@dataclass(frozen=True, slots=True)
class ScopeRuntime:
    """What the interceptor needs beyond the scope: the workspace's branch
    listing, the server's capabilities, and the fan-out cap."""

    listing: WorkspaceBranchListing
    capabilities: ScopeCapabilities
    max_cells: int


EMPTY_SCOPE_RUNTIME = ScopeRuntime(
    listing=EMPTY_BRANCH_LISTING,
    capabilities=NO_SCOPE_CAPABILITIES,
    max_cells=ScopeDefaultsConfig().max_cells,
)


class BranchOrigin(StrEnum):
    """Where a call's branch came from — observed at the interceptor, never
    inferred from ``meta`` (which has no such field)."""

    DEFAULT = "default"
    PINNED = "pinned"
    AGENT_CHOSEN = "agent_chosen"
    SERVER = "server"


@dataclass(frozen=True, slots=True)
class CellObservation:
    tool: str
    project: str  # what was SENT ("" = union)
    branch: str  # what was SENT ("" = nothing)
    branch_origin: BranchOrigin
    slice: ScopeSlice
    meta: Mapping[str, Any]
    replaced: bool = False  # a model-passed argument was replaced (§6.3 rule a / E2)
    is_error: bool = False


class ScopeObservations:
    """Per-question record of every tool call's cell + ``meta``.

    Mutable on purpose: the interceptor appends from a copied-context child
    task, and only in-place mutation of a container created in ``ask()``
    travels back to the page.
    """

    def __init__(self) -> None:
        self._records: list[CellObservation] = []

    def append(self, record: CellObservation) -> None:
        self._records.append(record)

    def records(self) -> tuple[CellObservation, ...]:
        return tuple(self._records)

    def by_cell(self) -> dict[tuple[str, str], tuple[CellObservation, ...]]:
        """Records grouped by the sent ``(project, branch)``, cells sorted."""
        grouped: dict[tuple[str, str], list[CellObservation]] = {}
        for record in self._records:
            grouped.setdefault((record.project, record.branch), []).append(record)
        return {cell: tuple(grouped[cell]) for cell in sorted(grouped)}

    def __len__(self) -> int:
        return len(self._records)


ACTIVE_QUESTION_SCOPE: contextvars.ContextVar[QuestionScope | None] = contextvars.ContextVar(
    "active_question_scope", default=None
)
ACTIVE_SCOPE_RUNTIME: contextvars.ContextVar[ScopeRuntime | None] = contextvars.ContextVar(
    "active_scope_runtime", default=None
)
ACTIVE_SCOPE_OBSERVATIONS: contextvars.ContextVar[ScopeObservations | None] = (
    contextvars.ContextVar("active_scope_observations", default=None)
)


# --- results -----------------------------------------------------------------


def cell_label(cell: ScopeCell) -> str:
    return f"{cell.project} · {cell.branch}" if cell.branch else cell.project


def _result_meta(result: CallToolResult) -> dict[str, Any]:
    structured = result.structuredContent or {}
    meta = structured.get("meta") or {}
    return dict(meta) if isinstance(meta, Mapping) else {}


def _result_text(result: CallToolResult) -> str:
    structured = result.structuredContent or {}
    if "text" in structured:
        return str(structured["text"])
    return "".join(getattr(block, "text", "") for block in result.content)


def _slice_of(args: Mapping[str, Any]) -> ScopeSlice:
    value = args.get("scope")
    if value == "changed":
        return ScopeSlice.CHANGED_FILES
    return ScopeSlice.DIFF_HUNKS if value == "diff" else ScopeSlice.WHOLE_BRANCH


def too_many_cells_result(count: int, cap: int) -> CallToolResult:
    """An ``isError`` result (never an exception): the adapter renders it as an
    error ToolMessage the model can read, whereas a bare exception escapes."""
    text = (
        f"scope pin spans {count} (project, branch) cells; the limit is max_cells={cap} "
        "(ask_your_docs.scope.max_cells). Narrow the pin or pass branch=<name>."
    )
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=True)


def merge_cell_results(
    cells: Sequence[ScopeCell], results: Sequence[CallToolResult]
) -> CallToolResult:
    """One labeled result per cell; the ``{text, items, meta}`` envelope shape
    is kept, ``meta`` is exactly the first cell's, ``isError`` only when all erred."""
    content: list[Any] = []
    texts: list[str] = []
    items: list[dict[str, Any]] = []
    errors = 0
    for cell, result in zip(cells, results, strict=True):
        label = cell_label(cell)
        content.append(TextContent(type="text", text=f"## {label}\n"))
        content.extend(result.content)
        errors += int(bool(result.isError))
        texts.append(f"## {label}\n{_result_text(result)}")
        structured = result.structuredContent or {}
        items.extend(
            {**item, "project": cell.project, "branch": cell.branch}
            for item in structured.get("items", ())
        )
    merged = {"text": "\n".join(texts), "items": items, "meta": _result_meta(results[0])}
    return CallToolResult(content=content, structuredContent=merged, isError=errors == len(results))


# --- observation -------------------------------------------------------------


def _observe(
    observations: ScopeObservations | None,
    *,
    tool: str,
    project: str,
    branch: str,
    origin: BranchOrigin,
    args: Mapping[str, Any],
    result: CallToolResult,
    replaced: bool = False,
) -> None:
    if observations is None:
        return
    observations.append(
        CellObservation(
            tool=tool,
            project=project,
            branch=branch,
            branch_origin=origin,
            slice=_slice_of(args),
            meta=_result_meta(result),
            replaced=replaced,
            is_error=bool(result.isError),
        )
    )


# --- DEFAULT rules -----------------------------------------------------------


def _default_project(
    tool: str, args: dict[str, Any], scope: QuestionScope, listing: WorkspaceBranchListing
) -> tuple[str, bool]:
    """(project sent, replaced?) — rule (a): keep a known name, replace an unknown one."""
    passed = str(args.get("project") or "")
    fallback = scope.default_project
    if not passed:
        if fallback:
            args["project"] = fallback
        return fallback, False
    if not listing.has_projects or listing.knows_project(passed):
        return passed, False
    args["project"] = fallback
    log_scope_event(
        "scope_default_replaced", tool=tool, argument="project", passed=passed, replacement=fallback
    )
    return fallback, True


def _default_branch(
    tool: str, args: dict[str, Any], scope: QuestionScope, project: str, runtime: ScopeRuntime
) -> tuple[str, BranchOrigin, bool]:
    """(branch sent, origin, replaced?). U0 body: the server does not advertise
    ``branch``, so nothing is ever sent and a stray model argument is dropped."""
    args.pop("branch", None)
    return "", BranchOrigin.SERVER, False


def _default_package(tool: str, args: dict[str, Any], scope: QuestionScope) -> None:
    if tool in PACKAGE_TOOLS and scope.package and not args.get("package"):
        args["package"] = scope.package


def _scope_argument_value(
    tool: str, scope: QuestionScope, capabilities: ScopeCapabilities
) -> str | None:
    """The ``scope`` value the scope implies for ``tool`` (U0: the code filter only)."""
    if tool == "search_codebase" and scope.code is not ScopeCode.ALL:
        return CODE_SERVER_VALUES[scope.code]
    return None


async def _apply_defaults(
    request: ToolCallRequestLike,
    handler: ToolCallHandler,
    scope: QuestionScope,
    runtime: ScopeRuntime,
    observations: ScopeObservations | None,
) -> CallToolResult:
    args = dict(request.args)
    project, project_replaced = _default_project(request.name, args, scope, runtime.listing)
    branch, origin, branch_replaced = _default_branch(request.name, args, scope, project, runtime)
    _default_package(request.name, args, scope)
    value = _scope_argument_value(request.name, scope, runtime.capabilities)
    if value is not None and not args.get("scope"):
        args["scope"] = value  # DEFAULT injects only what the model omitted
    result = await handler(request.override(args=args))
    _observe(
        observations,
        tool=request.name,
        project=project,
        branch=branch,
        origin=origin,
        args=args,
        result=result,
        replaced=project_replaced or branch_replaced,
    )
    return result


# --- PIN rules ---------------------------------------------------------------


def cell_arguments(
    args: Mapping[str, Any], cell: ScopeCell, capabilities: ScopeCapabilities
) -> dict[str, Any]:
    """The per-cell arguments: the cell's project, plus its branch ONLY when
    the server advertises ``branch`` and the cell names one (AC-6b)."""
    out = dict(args)
    out["project"] = cell.project
    if capabilities.branch_selector and cell.branch:
        out["branch"] = cell.branch
    else:
        out.pop("branch", None)
    return out


def target_cells(
    tool: str, args: Mapping[str, Any], scope: QuestionScope, capabilities: ScopeCapabilities
) -> tuple[ScopeCell, ...]:
    """Which pinned cells a call covers (UI spec §6.4): a model-named pinned
    branch narrows to the matching cells; a pinned project narrows to its
    cells; anything else — the pin is hard — fans out over every cell."""
    cells = scope.cells
    passed_project = str(args.get("project") or "")
    passed_branch = str(args.get("branch") or "") if capabilities.branch_selector else ""
    if passed_branch:
        matching = tuple(
            c
            for c in cells
            if c.branch == passed_branch and (not passed_project or c.project == passed_project)
        )
        if matching:
            return matching
        log_scope_event(
            "scope_pin_branch_ignored",
            tool=tool,
            branch=passed_branch,
            pinned=[f"{c.project}:{c.branch}" for c in cells],
        )
    if passed_project:
        by_project = tuple(c for c in cells if c.project == passed_project)
        if by_project:
            return by_project
        log_scope_event(
            "scope_pin_project_ignored", tool=tool, project=passed_project, pinned=scope.projects()
        )
    return cells


async def fan_out_over_cells(
    request: ToolCallRequestLike,
    handler: ToolCallHandler,
    args: Mapping[str, Any],
    cells: Sequence[ScopeCell],
    runtime: ScopeRuntime,
    observations: ScopeObservations | None,
) -> CallToolResult:
    """One handler call per cell, in cell order, merged with labels; the cap
    is checked BEFORE any call (E4)."""
    if len(cells) > runtime.max_cells:
        return too_many_cells_result(len(cells), runtime.max_cells)
    results: list[CallToolResult] = []
    for cell in cells:
        cell_args = cell_arguments(args, cell, runtime.capabilities)
        result = await handler(request.override(args=cell_args))
        results.append(result)
        _observe(
            observations,
            tool=request.name,
            project=cell.project,
            branch=cell_args.get("branch", ""),
            origin=BranchOrigin.PINNED,
            args=cell_args,
            result=result,
        )
    return merge_cell_results(cells, results)


async def _apply_pin(
    request: ToolCallRequestLike,
    handler: ToolCallHandler,
    scope: QuestionScope,
    runtime: ScopeRuntime,
    observations: ScopeObservations | None,
) -> CallToolResult:
    args = dict(request.args)
    if request.name in PACKAGE_TOOLS and scope.package:
        args["package"] = scope.package
    value = _scope_argument_value(request.name, scope, runtime.capabilities)
    if value is not None:
        args["scope"] = value  # a pin overwrites
    cells = target_cells(request.name, args, scope, runtime.capabilities)
    if len(cells) > 1:
        return await fan_out_over_cells(request, handler, args, cells, runtime, observations)
    cell_args = cell_arguments(args, cells[0], runtime.capabilities)
    result = await handler(request.override(args=cell_args))
    _observe(
        observations,
        tool=request.name,
        project=cells[0].project,
        branch=cell_args.get("branch", ""),
        origin=BranchOrigin.PINNED,
        args=cell_args,
        result=result,
    )
    return result


# --- entry point -------------------------------------------------------------


async def intercept_question_scope(
    request: ToolCallRequestLike, handler: ToolCallHandler
) -> CallToolResult:
    """The interceptor ``agent._intercept`` delegates to."""
    scope = ACTIVE_QUESTION_SCOPE.get()
    if scope is None:
        return await handler(request)  # no question active: strict passthrough
    runtime = ACTIVE_SCOPE_RUNTIME.get() or EMPTY_SCOPE_RUNTIME
    observations = ACTIVE_SCOPE_OBSERVATIONS.get()
    if scope.kind is ScopeKind.PIN:
        return await _apply_pin(request, handler, scope, runtime, observations)
    return await _apply_defaults(request, handler, scope, runtime, observations)


__all__ = (
    "ACTIVE_QUESTION_SCOPE",
    "ACTIVE_SCOPE_OBSERVATIONS",
    "ACTIVE_SCOPE_RUNTIME",
    "EMPTY_SCOPE_RUNTIME",
    "PACKAGE_TOOLS",
    "SLICE_TOOLS",
    "BranchOrigin",
    "CellObservation",
    "ScopeObservations",
    "ScopeRuntime",
    "cell_arguments",
    "cell_label",
    "fan_out_over_cells",
    "intercept_question_scope",
    "merge_cell_results",
    "target_cells",
    "too_many_cells_result",
)
```

`vulture` may flag the unused `scope`, `project`, `runtime` parameters of the U0 `_default_branch` stub; they are consumed by the Task 12 body — if `vulture --min-confidence 80` reports them at this commit, add the three names to the call as keyword arguments (it already passes them positionally) and move on; the gate runs at Task 11.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/test_scope_interceptor.py -q`
Expected: PASS (`test_ac6b_...` proves the branch key is dropped on U0; the copied-context test proves the container write-back).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/scope_interceptor.py tests/harness/ask_your_docs/test_scope_interceptor.py
git commit -m "ask-your-docs: scope interceptor — defaults, pins, cell fan-out, observations"
```

---

### Task 7: Rewire `agent.py` — delegate interceptor, `ask()` keywords, `BuiltAgent`, prompt-gating keywords, AC-11 golden

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/agent.py`
- Create: `tests/fixtures/goldens/ask_your_docs_system_v1.txt` (today's rendered `system_v1` bytes — captured BEFORE Task 13 edits the template)
- Modify: `tests/harness/ask_your_docs/test_prompt_seam.py`, `tests/harness/ask_your_docs/test_image_attachment.py:94`
- Test: `tests/harness/ask_your_docs/test_prompt_seam.py`, `tests/harness/ask_your_docs/test_binding.py` (unchanged, must stay green)

**Interfaces:**
- Consumes: `QuestionScope`, `scope_prefix` (Task 4); `render_catalog(branches=, show_merged=)`, `workspace_branch_listing`, `WorkspaceBranchListing` (Task 3); `BuiltAgent`, `ScopeCapabilities`, `NO_SCOPE_CAPABILITIES`, `inspect_scope_capabilities` (Task 5); the three contextvars, `ScopeObservations`, `ScopeRuntime`, `intercept_question_scope` (Task 6).
- Produces: `_intercept(request, handler)` (delegate; imported by name in `binding.py`), `_assemble_prompt(name, catalog, prompts, session_start_context=None, skill_block=None, *, scope_capabilities=NO_SCOPE_CAPABILITIES, branches=None)`, `build_agent_with_scope_capabilities(...build_agent's signature..., branches=None) -> BuiltAgent`, `build_agent(*args, **kwargs) -> tuple[graph, llm]`, `ask(agent, history, question, scope: QuestionScope | None = None, max_history=8, *, images=(), image_store=None, transient_note="", observations: ScopeObservations | None = None, scope_runtime: ScopeRuntime | None = None) -> str`.

- [ ] **Step 1: Capture the AC-11 golden (before any template edit)**

```bash
PYTHONPATH=python python -c "from pydocs_mcp.harness.ask_your_docs.prompts import render_shared; import pathlib; pathlib.Path('tests/fixtures/goldens/ask_your_docs_system_v1.txt').write_bytes(render_shared('system_v1').encode('utf-8'))"
git add tests/fixtures/goldens/ask_your_docs_system_v1.txt
```

Verify: `PYTHONPATH=python python -c "from pydocs_mcp.harness.ask_your_docs.prompts import SYSTEM_PROMPT; import pathlib; assert pathlib.Path('tests/fixtures/goldens/ask_your_docs_system_v1.txt').read_bytes().decode() == SYSTEM_PROMPT; print('golden ok')"` prints `golden ok`.

- [ ] **Step 2: Write the failing tests**

Append to `tests/harness/ask_your_docs/test_prompt_seam.py` (add `from pathlib import Path` to the imports, plus `from pydocs_mcp.harness.ask_your_docs.agent import build_agent_with_scope_capabilities`, `from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch`, `from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing`, `from pydocs_mcp.harness.ask_your_docs.prompts import render_shared`, `from pydocs_mcp.harness.ask_your_docs.scope_capabilities import NO_SCOPE_CAPABILITIES`, `from pydocs_mcp.models import BranchStatus`):

```python
_SYSTEM_GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "goldens" / "ask_your_docs_system_v1.txt"
_LISTING = WorkspaceBranchListing(
    projects={"proj": (IndexedBranch("main", "a" * 40, None, True, BranchStatus.ACTIVE, None, None, 1.0),)}
)


class _SchemaTool:
    def __init__(self, name: str, *, branch: bool) -> None:
        self.name = name
        props = {"project": {"type": "string"}}
        if branch:
            props["branch"] = {"type": "string"}
        self.args_schema = {"properties": props, "type": "object"}


class TestBranchGating:
    def test_no_variable_render_matches_the_golden(self) -> None:
        """AC-11 / V4: the template renders today's bytes with NO variables under StrictUndefined."""
        golden = _SYSTEM_GOLDEN.read_bytes().decode("utf-8")
        assert render_shared("system_v1") == golden
        assert SYSTEM_PROMPT == golden

    def test_listing_is_ignored_when_branch_is_not_advertised(self) -> None:
        expected = f"{SYSTEM_PROMPT}\nIndexed projects and packages:\n{render_catalog(_CATALOG)}"
        assembled = _assemble_prompt(
            "text_react", _CATALOG, None, scope_capabilities=NO_SCOPE_CAPABILITIES, branches=_LISTING
        )
        assert assembled == expected

    def test_build_agent_keeps_its_pair_shape_and_the_record_carries_capabilities(
        self, monkeypatch
    ) -> None:
        """AC-27."""
        from pydocs_mcp.harness.ask_your_docs import agent as agent_mod
        from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities

        class _FakeMcpClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def get_tools(self):
                return [_SchemaTool("search_codebase", branch=True), _SchemaTool("grep", branch=True)]

        def _fake_build(name, *, llm, tools, prompt, capabilities, config, model):
            return "GRAPH"

        monkeypatch.setattr(agent_mod, "MultiServerMCPClient", _FakeMcpClient)
        monkeypatch.setattr(agent_mod, "_build_architecture", _fake_build)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        caps = ModelCapabilities(multimodal=False, source="override")
        kwargs = dict(catalog=_CATALOG, architecture="text_react", capabilities=caps)
        pair = asyncio.run(build_agent("/tmp/ws", "m", **kwargs))
        assert len(pair) == 2 and pair[0] == "GRAPH"
        built = asyncio.run(build_agent_with_scope_capabilities("/tmp/ws", "m", **kwargs))
        assert built.graph == "GRAPH" and built.scope_capabilities.branch_selector is True
```

Update `tests/harness/ask_your_docs/test_image_attachment.py` — the `ask(...)` call in `test_ask_without_images_sends_plain_str` (today `scope={"project": "p"}`) becomes:

```python
    from pydocs_mcp.harness.ask_your_docs.question_scope import QuestionScope, ScopeCell, ScopeKind

    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("p", ""),))
    asyncio.run(ask(agent, history, "q1", scope=pin))
```

Run `grep -rn "scope={" tests/harness/ask_your_docs` and convert every other `scope={"project": ...}` dict the same way (a `QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell(<project>, ""),))` pin).

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_prompt_seam.py tests/harness/ask_your_docs/test_image_attachment.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_agent_with_scope_capabilities'` and `TypeError` on `_assemble_prompt(... scope_capabilities=...)`.

- [ ] **Step 4: Rewire `agent.py`**

Replace the module docstring example (`scope={"project": "backend"}`) with:

```python
"""Ask-your-docs agent — a LangGraph ReAct agent over pydocs-mcp.

agent, llm = await build_agent("~/pydocs-index", model="gpt-4o-mini")
history: list = []
pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", ""),))
answer = await ask(agent, history, "how do I open a database pool?", scope=pin)
"""
```

Replace the imports of `render_catalog, workspace_catalog` and add the new modules:

```python
from pydocs_mcp.harness.ask_your_docs.catalog import (
    WorkspaceBranchListing,
    render_catalog,
    workspace_branch_listing,
    workspace_catalog,
)
from pydocs_mcp.harness.ask_your_docs.question_scope import (  # noqa: F401 — scope_prefix keeps its import path
    QuestionScope,
    ScopeCell,
    ScopeKind,
    scope_prefix,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    BuiltAgent,
    ScopeCapabilities,
    inspect_scope_capabilities,
)
from pydocs_mcp.harness.ask_your_docs.scope_interceptor import (
    ACTIVE_QUESTION_SCOPE,
    ACTIVE_SCOPE_OBSERVATIONS,
    ACTIVE_SCOPE_RUNTIME,
    ScopeObservations,
    ScopeRuntime,
    intercept_question_scope,
)
```

Delete `ToolScope`, `_active_scope` (with its comment), the `_PACKAGE_TOOLS` block and comment, the body of `_intercept`, and `scope_prefix`. The interceptor becomes a delegate (the name is imported by `binding.py:320`):

```python
async def _intercept(request: MCPToolCallRequest, handler):
    """The question-scope interceptor (scope_interceptor.intercept_question_scope);
    kept under this name because the eval binding imports it."""
    return await intercept_question_scope(request, handler)
```

Replace `_assemble_prompt`:

```python
def _resolved_system_prompt(
    name: str, prompts: AskPrompts | None, branch_selector_advertised: bool
) -> str:
    """The candidate system prompt, else the per-architecture render — with NO
    variables unless ``branch`` is advertised, so today's bytes are the
    no-variable render (AC-11) and rule 7 appears only on U1 servers."""
    if prompts and prompts.system_prompt:
        return prompts.system_prompt
    namespace = prompts_for(name)
    if branch_selector_advertised:
        return namespace.render("system_v1", branch_selector_advertised=True)
    return namespace.render("system_v1")


def _assemble_prompt(
    name: str,
    catalog: dict[str, list[str]],
    prompts: AskPrompts | None,
    session_start_context: str | None = None,
    skill_block: str | None = None,
    *,
    scope_capabilities: ScopeCapabilities = NO_SCOPE_CAPABILITIES,
    branches: WorkspaceBranchListing | None = None,
) -> str:
    """The ONE prompt-assembly site: candidate-or-shipped system + catalog.

    The fallback is the per-architecture render (``prompts_for(name)``), never
    the ``SYSTEM_PROMPT`` constant — a ``prompts/<name>/system_v1.j2``
    override must apply whenever that architecture is selected. A second
    assembly site is the one forbidden shape (single source of truth).

    ``session_start_context`` (ADR 0008) appends the harness-injected
    session-start pack after the catalog; ``skill_block`` (run-contract
    design §9 stage 2) appends the skill-artifact guidance after it.
    ``scope_capabilities`` gates rule 7 and the catalog's branch segment on
    the server advertising ``branch`` (UI spec §6.6, R7); ``branches`` is
    ignored unless it is advertised. Every default keeps the assembled prompt
    byte-identical to the pre-existing shape.
    """
    system = _resolved_system_prompt(name, prompts, scope_capabilities.branch_selector)
    listing = branches if scope_capabilities.branch_selector else None
    catalog_block = render_catalog(catalog, listing, show_merged=scope_capabilities.diff_slice)
    return assemble_system_prompt(system, catalog_block, session_start_context, skill_block)
```

Rename today's `build_agent` to `build_agent_with_scope_capabilities`, add the trailing keyword `branches: WorkspaceBranchListing | None = None`, change its return annotation to `-> BuiltAgent`, replace the docstring's first line with `"""Start pydocs-mcp over the workspace; return a :class:`BuiltAgent`.` and append this paragraph to the docstring: `` ``branches`` (the workspace's branch listing) feeds the catalog's branch segment when the server advertises ``branch``; ``None`` scans the workspace in that case and is ignored otherwise. ``, then replace the block from `if catalog is None:` to `return graph, llm` with:

```python
    if catalog is None:
        catalog = await asyncio.to_thread(workspace_catalog, workspace)
    scope_caps = inspect_scope_capabilities(tools)
    if branches is None and scope_caps.branch_selector:
        branches = await asyncio.to_thread(workspace_branch_listing, workspace)

    llm = ChatOpenAI(model=model, base_url=base_url)
    cfg = config or AskYourDocsConfig()
    name = architecture or cfg.architecture
    # Per-architecture system prompt by the directory convention (an
    # architecture without prompts/<name>/system_v1.j2 gets shared/). Note:
    # `auto` composes with its own (shared) system prompt even when it
    # delegates the graph — a per-arch system override applies when that
    # architecture is selected directly.
    #
    # Session-start context pack (ADR 0008): appended at this single assembly
    # site ONLY when serve.session_start_context.enabled — the gate returns
    # None when off, keeping the prompt byte-identical (the ablation phase's
    # control arm).
    session_start_pack = await build_session_start_context_for_agent_prompt(
        workspace, pydocs_config
    )
    skill_block = _resolved_skill_block(skill_override, task_name)
    prompt = _assemble_prompt(
        name,
        catalog,
        prompts,
        session_start_pack,
        skill_block,
        scope_capabilities=scope_caps,
        branches=branches,
    )
    caps = capabilities
    if caps is None:
        caps = await detect_capabilities(model, base_url, cfg.multimodal.detection)
    graph = _build_architecture(
        name,
        llm=llm,
        tools=tools,
        prompt=prompt,
        capabilities=caps,
        config=cfg,
        model=model,
    )
    return BuiltAgent(graph=graph, llm=llm, scope_capabilities=scope_caps)


async def build_agent(*args: Any, **kwargs: Any):
    """Start pydocs-mcp over the workspace; return ``(agent, llm)``.

    The pre-scope shape, kept byte for byte for the eval binding, the CLI and
    the prompt-seam tests (a 2-tuple, never a third element). Everything else
    is :func:`build_agent_with_scope_capabilities`, whose keyword surface this
    wrapper forwards unchanged.
    """
    built = await build_agent_with_scope_capabilities(*args, **kwargs)
    return built.graph, built.llm


# inspect.signature(build_agent) follows __wrapped__, so the keyword-only
# prompts= seam pin (test_prompt_seam.py) still reads the full signature.
build_agent.__wrapped__ = build_agent_with_scope_capabilities  # type: ignore[attr-defined]
```

Add `from typing import Any` to the imports. Replace `ask`:

```python
def _bind_question_context(
    scope: QuestionScope | None,
    scope_runtime: ScopeRuntime | None,
    observations: ScopeObservations | None,
    image_store: dict | None,
) -> list[tuple[contextvars.ContextVar, contextvars.Token]]:
    """Set the per-question contextvars inside ask()'s coroutine; returns the
    tokens to reset. Concurrent questions (two browser tabs on one cached
    agent) each see their own values — never shared mutable state."""
    pairs: list[tuple[contextvars.ContextVar, contextvars.Token]] = []
    for var, value in (
        (ACTIVE_QUESTION_SCOPE, scope),
        (ACTIVE_SCOPE_RUNTIME, scope_runtime),
        (ACTIVE_SCOPE_OBSERVATIONS, observations if observations is not None else ScopeObservations()),
        (_active_image_store, image_store),
        (_reinspect_state, {"calls": 0, "memo": {}}),
    ):
        pairs.append((var, var.set(value)))
    return pairs


async def ask(
    agent,
    history: list,
    question: str,
    scope: QuestionScope | None = None,
    max_history: int = 8,
    *,
    images: tuple = (),
    image_store: dict | None = None,
    transient_note: str = "",
    observations: ScopeObservations | None = None,
    scope_runtime: ScopeRuntime | None = None,
) -> str:
    """One conversation turn under ``scope``; updates ``history`` in place.

    The scope is applied two ways: on every tool call (the interceptor reads
    the contextvar) and, for a PIN, as a transient "[pinned scope: ...]" note.
    Only the note is transient — ``history`` keeps the BARE question, so a
    later scope change can't leak a stale pin into reformulation.
    ``None`` (the CLI / eval shape) makes the interceptor a strict passthrough.

    ``observations`` (a container the page owns) receives one record per tool
    call — mutated in place, the ``_reinspect_state`` pattern, because the
    interceptor runs in a copied context. ``scope_runtime`` carries the branch
    listing, the capability record and the fan-out cap.

    ``images`` (ImageAttachment tuple) are per-turn ephemera like the scope
    note: the blocks ride only on the CURRENT HumanMessage; history keeps a
    textual "[attached images: ...]" placeholder (§3.6 decision 2).
    """
    bound = _bind_question_context(scope, scope_runtime, observations, image_store)
    try:
        # transient_note attaches AFTER reformulation, exactly like the scope
        # prefix — prefixing it before the rewrite would let the rewrite LLM
        # strip it, and storing it in history would leak a stale note.
        note = f"{transient_note}\n" if transient_note else ""
        prefixed = scope_prefix(scope) + note + question
        content: str | list = prefixed
        if images:
            content = [
                {"type": "text", "text": prefixed},
                *(att.as_content_block() for att in images),
            ]
        result = await agent.ainvoke({"messages": [*history, HumanMessage(content=content)]})
        answer = result["messages"][-1].content
    finally:
        for var, token in reversed(bound):
            var.reset(token)
    placeholder = f" [attached images: {', '.join(att.name for att in images)}]" if images else ""
    history += [HumanMessage(question + placeholder), AIMessage(answer)]
    del history[:-max_history]
    return answer
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs -q`
Expected: PASS — including `test_binding.py` (AC-24: `_intercept` and `serve_connection` still importable, delivery-map digest unchanged), `test_prompt_seed_parity.py`, `test_prompts_package.py`, `test_tool_binding.py`.

Also run: `wc -l python/pydocs_mcp/harness/ask_your_docs/agent.py`
Expected: under 500 (≈ 487; the ≤ 468 target of AC-26 is not a gate).

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/agent.py tests/fixtures/goldens/ask_your_docs_system_v1.txt tests/harness/ask_your_docs/test_prompt_seam.py tests/harness/ask_your_docs/test_image_attachment.py
git commit -m "ask-your-docs: ask() takes QuestionScope; build_agent_with_scope_capabilities; gated prompt assembly"
```

---

### Task 8: Answer footer and follow-up chips (pure)

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/answer_footer.py`
- Test: `tests/harness/ask_your_docs/test_answer_footer.py`

**Interfaces:**
- Consumes: `WorkspaceBranchListing` (Task 3); `QuestionScope`, `ScopeCell`, `ScopeKind`, `ScopeCode`, `ScopeSlice`, `SLICE_LABELS` (Task 4); `ScopeCapabilities` (Task 5); `BranchOrigin`, `CellObservation`, `ScopeObservations` (Task 6); `BranchStatus`.
- Produces: `render_answer_footer(observations, listing) -> str`; `FollowUpKind {COMPARE_WITH, SHOW_DIFF, PIN_BRANCH}`; `FollowUpChip(kind, label, project, branches, slice, question)`; `answered_cells(observations, listing) -> dict[ScopeCell, tuple[CellObservation, ...]]`; `derive_follow_up_chips(observations, listing, capabilities, kept_pin) -> tuple[FollowUpChip, ...]`; `apply_follow_up_chip(chip, kept_pin, defaults) -> tuple[str | None, QuestionScope | None]`; `ORIGIN_LABELS`.
- The chips are written here, dormant behind `ScopeCapabilities` (they need `branch` or `diff`); U1 / U2 activate them.

- [ ] **Step 1: Write the failing tests**

Create `tests/harness/ask_your_docs/test_answer_footer.py`:

```python
"""Footer + follow-up chips — AC-17, AC-18, AC-31, AC-34."""

from __future__ import annotations

import random

from pydocs_mcp.harness.ask_your_docs.answer_footer import (
    FollowUpKind,
    apply_follow_up_chip,
    derive_follow_up_chips,
    render_answer_footer,
)
from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch
from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    ScopeCell,
    ScopeCode,
    ScopeKind,
    ScopeSlice,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.harness.ask_your_docs.scope_interceptor import (
    BranchOrigin,
    CellObservation,
    ScopeObservations,
)
from pydocs_mcp.models import BranchStatus

_SHA = "3e1a9c2" + "0" * 33
U1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)
U2 = ScopeCapabilities(branch_selector=True, changed_slice=True, diff_slice=True)


def _row(name, *, default=False, base=None, status=BranchStatus.ACTIVE, merged_into=None, sha="3e1a9c2" + "f" * 33):
    return IndexedBranch(name, sha, base, default, status, merged_into, None, 1.0)


LISTING = WorkspaceBranchListing(
    projects={
        "backend": (
            _row("feature/retry", default=True, base="main"),
            _row("main", sha="9abcdef" + "0" * 33),
            _row("feature/old", status=BranchStatus.MERGED, merged_into=_SHA),
        ),
        "tooling": (_row("main", default=True),),
    }
)
SINGLE = WorkspaceBranchListing(projects={"demo": ()})


def _obs(tool="search_codebase", project="backend", branch="feature/retry", origin=BranchOrigin.PINNED, slice_=ScopeSlice.WHOLE_BRANCH, meta=None, replaced=False):
    return CellObservation(tool, project, branch, origin, slice_, meta or {"branch": branch or None}, replaced)


def _observations(*records) -> ScopeObservations:
    observations = ScopeObservations()
    for record in records:
        observations.append(record)
    return observations


DEFAULTS = QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),))


class TestFooter:  # AC-18, AC-34
    def test_pinned_cell_segment(self):
        footer = render_answer_footer(_observations(_obs(), _obs(tool="get_symbol")), LISTING)
        assert footer == "answered from backend · feature/retry @3e1a9c2 · whole branch · pinned"

    def test_segments_are_sorted_and_joined_with_bars(self):
        footer = render_answer_footer(
            _observations(_obs(project="tooling", branch="main"), _obs(project="backend", branch="main")), LISTING
        )
        assert footer == (
            "answered from backend · main @9abcdef · whole branch · pinned"
            " | answered from tooling · main @3e1a9c2 · whole branch · pinned"
        )

    def test_union_answer_on_a_multi_project_listing_reads_all_projects(self):
        record = _obs(project="", branch="", origin=BranchOrigin.SERVER, meta={"project": "backend", "branch": "main", "indexed_git_head": "abcdef0123"})
        footer = render_answer_footer(_observations(record), LISTING)
        assert footer == "answered from all projects · main @abcdef0 · whole branch · server default"

    def test_replaced_argument_and_stale_index_are_visible(self):
        record = _obs(project="", branch="", origin=BranchOrigin.SERVER, replaced=True, meta={"project": "demo", "index_stale": True})
        footer = render_answer_footer(_observations(record), SINGLE)
        assert footer == "answered from demo · no branch · agent-chosen → default · index stale"

    def test_pre_v16_bundle_footer(self):
        """AC-34: no branch, no sha, server default."""
        record = _obs(project="", branch="", origin=BranchOrigin.SERVER, meta={"project": "demo", "branch": None})
        assert render_answer_footer(_observations(record), SINGLE) == "answered from demo · no branch · server default"

    def test_distinct_slices_are_listed(self):
        footer = render_answer_footer(
            _observations(_obs(slice_=ScopeSlice.DIFF_HUNKS), _obs(slice_=ScopeSlice.WHOLE_BRANCH)), LISTING
        )
        assert "· whole branch, diff hunks ·" in footer

    def test_no_tool_calls(self):
        assert render_answer_footer(ScopeObservations(), LISTING) == "answered without tool calls"


class TestChips:  # AC-17
    def test_one_answered_cell_with_a_base_yields_compare_and_pin(self):
        chips = derive_follow_up_chips(_observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, U1, None)
        assert [c.kind for c in chips] == [FollowUpKind.COMPARE_WITH, FollowUpKind.PIN_BRANCH]
        assert chips[0].label == "compare with main" and chips[0].branches == ("feature/retry", "main")
        assert chips[0].question == "Compare the previous answer between feature/retry and main: what differs?"
        assert chips[1].label == "pin feature/retry"

    def test_nothing_on_u0(self):
        assert derive_follow_up_chips(_observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, NO_SCOPE_CAPABILITIES, None) == ()

    def test_pinned_cells_and_kept_cells_get_no_pin_chip(self):
        assert [c.kind for c in derive_follow_up_chips(_observations(_obs()), LISTING, U1, None)] == [FollowUpKind.COMPARE_WITH]
        kept = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "feature/retry"),))
        chips = derive_follow_up_chips(_observations(_obs(origin=BranchOrigin.AGENT_CHOSEN)), LISTING, U1, kept)
        assert [c.kind for c in chips] == [FollowUpKind.COMPARE_WITH]

    def test_two_answered_cells_yield_two_pin_chips_in_order_and_no_compare(self):
        records = [
            _obs(project="tooling", branch="main", origin=BranchOrigin.DEFAULT),
            _obs(project="backend", branch="main", origin=BranchOrigin.SERVER),
        ]
        chips = derive_follow_up_chips(_observations(*records), LISTING, U1, None)
        assert [(c.kind, c.project) for c in chips] == [(FollowUpKind.PIN_BRANCH, "backend"), (FollowUpKind.PIN_BRANCH, "tooling")]

    def test_show_the_diff_needs_u2_and_no_prior_diff_slice(self):
        chips = derive_follow_up_chips(_observations(_obs()), LISTING, U2, None)
        assert [c.kind for c in chips] == [FollowUpKind.COMPARE_WITH, FollowUpKind.SHOW_DIFF]
        assert chips[1].slice is ScopeSlice.DIFF_HUNKS and chips[1].branches == ("feature/retry",)
        chips = derive_follow_up_chips(_observations(_obs(slice_=ScopeSlice.DIFF_HUNKS)), LISTING, U2, None)
        assert FollowUpKind.SHOW_DIFF not in [c.kind for c in chips]

    def test_show_the_diff_on_a_tombstone_targets_the_landing_sha(self):
        chips = derive_follow_up_chips(_observations(_obs(branch="feature/old", origin=BranchOrigin.AGENT_CHOSEN)), LISTING, U2, None)
        diff = next(c for c in chips if c.kind is FollowUpKind.SHOW_DIFF)
        assert diff.branches == (_SHA,)

    def test_at_most_three_and_deterministic_under_shuffle(self):
        records = [_obs(project=p, branch="main", origin=BranchOrigin.DEFAULT) for p in ("backend", "tooling")]
        records.append(_obs(project="backend", branch="feature/retry", origin=BranchOrigin.DEFAULT))
        random.Random(7).shuffle(records)
        chips = derive_follow_up_chips(_observations(*records), LISTING, U2, None)
        assert len(chips) == 3 and [c.kind for c in chips] == [FollowUpKind.PIN_BRANCH] * 3
        assert [(c.project, c.branches[0]) for c in chips] == [("backend", "feature/retry"), ("backend", "main"), ("tooling", "main")]

    def test_union_records_on_a_multi_project_listing_yield_nothing(self):
        record = _obs(project="", branch="", origin=BranchOrigin.SERVER, meta={"project": "backend", "branch": "main"})
        assert derive_follow_up_chips(_observations(record), LISTING, U1, None) == ()


class TestApply:  # AC-31
    def test_compare_returns_the_question_and_a_one_shot_pin_leaving_kept_alone(self):
        kept = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"),))
        (chip, _) = derive_follow_up_chips(_observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, U1, None)
        question, pin = apply_follow_up_chip(chip, kept, DEFAULTS)
        assert question == chip.question
        assert pin.cells == (ScopeCell("backend", "feature/retry"), ScopeCell("backend", "main"))
        assert kept.cells == (ScopeCell("backend", "main"),)

    def test_pin_branch_returns_no_question_and_grows_the_kept_pin(self):
        (_, chip) = derive_follow_up_chips(_observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, U1, None)
        question, pin = apply_follow_up_chip(chip, None, DEFAULTS)
        assert question is None and pin.cells == (ScopeCell("backend", "feature/retry"),)
        kept = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("tooling", "main"),))
        _, grown = apply_follow_up_chip(chip, kept, DEFAULTS)
        assert grown.cells == (ScopeCell("tooling", "main"), ScopeCell("backend", "feature/retry"))

    def test_show_diff_drops_a_dependencies_only_default(self):
        defaults = QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),), code=ScopeCode.DEPS)
        chips = derive_follow_up_chips(_observations(_obs()), LISTING, U2, None)
        diff = next(c for c in chips if c.kind is FollowUpKind.SHOW_DIFF)
        question, pin = apply_follow_up_chip(diff, None, defaults)
        assert question == "Show the diff hunks behind the previous answer."
        assert pin.slice is ScopeSlice.DIFF_HUNKS and pin.code is ScopeCode.ALL
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_answer_footer.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `answer_footer.py`**

```python
"""The answer footer and the follow-up chips (UI spec §6.8–§6.9).

Pure and deterministic: observations in, one caption line and at most
``len(FollowUpKind)`` chips out. Streamlit rendering lives in scope_panel.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    SLICE_LABELS,
    QuestionScope,
    ScopeCell,
    ScopeKind,
    ScopeSlice,
    code_compatible_with_slice,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities
from pydocs_mcp.harness.ask_your_docs.scope_interceptor import (
    BranchOrigin,
    CellObservation,
    ScopeObservations,
)
from pydocs_mcp.models import BranchStatus

ORIGIN_LABELS: dict[BranchOrigin, str] = {
    BranchOrigin.DEFAULT: "default",
    BranchOrigin.PINNED: "pinned",
    BranchOrigin.AGENT_CHOSEN: "agent-chosen",
    BranchOrigin.SERVER: "server default",
}
# When one cell mixes origins (a model-passed branch equal to the default on
# one call, omitted on another), the most specific one names the segment.
_ORIGIN_PRECEDENCE = (
    BranchOrigin.PINNED,
    BranchOrigin.AGENT_CHOSEN,
    BranchOrigin.DEFAULT,
    BranchOrigin.SERVER,
)
NO_BRANCH = "no branch"
ALL_PROJECTS = "all projects"
_TOMBSTONE_STATUSES = (BranchStatus.MERGED, BranchStatus.DELETED)


# --- footer ------------------------------------------------------------------


def _shown_project(project: str, meta: dict, listing: WorkspaceBranchListing) -> str:
    """A union request spans every bundle while ``meta.project`` names only the
    first loaded one — so a multi-project listing reads ``all projects``."""
    if project:
        return project
    if listing.project_count > 1:
        return ALL_PROJECTS
    return str(meta.get("project") or "") or ALL_PROJECTS


def _origin_text(records: tuple[CellObservation, ...]) -> str:
    if any(r.replaced for r in records):
        return "agent-chosen → default"
    origin = min((r.branch_origin for r in records), key=_ORIGIN_PRECEDENCE.index)
    return ORIGIN_LABELS[origin]


def _slice_text(records: tuple[CellObservation, ...]) -> str:
    slices = sorted({r.slice for r in records}, key=list(ScopeSlice).index)
    return ", ".join(SLICE_LABELS[s] for s in slices)


def _segment(
    cell: tuple[str, str], records: tuple[CellObservation, ...], listing: WorkspaceBranchListing
) -> str:
    project, branch = cell
    meta = dict(records[0].meta)
    shown_branch = branch or str(meta.get("branch") or "") or NO_BRANCH
    # The listing's sha is exact per bundle and branch when a cell was sent;
    # otherwise the server's probe (bundle #1 on multi-bundle servers, E6).
    sha = listing.head_sha(project, branch) if project and branch else ""
    sha = sha or str(meta.get("indexed_git_head") or "")
    head = f"answered from {_shown_project(project, meta, listing)} · {shown_branch}"
    parts = [f"{head} @{sha[:7]}" if sha else head]
    if shown_branch != NO_BRANCH:  # slices are branch-relative
        parts.append(_slice_text(records))
    parts.append(_origin_text(records))
    if any(bool(r.meta.get("index_stale")) for r in records):
        parts.append("index stale")  # R10: never hidden
    return " · ".join(parts)


def render_answer_footer(observations: ScopeObservations, listing: WorkspaceBranchListing) -> str:
    """One caption line: a segment per distinct sent cell, sorted, joined by `` | ``."""
    groups = observations.by_cell()
    if not groups:
        return "answered without tool calls"
    return " | ".join(_segment(cell, records, listing) for cell, records in groups.items())


# --- chips -------------------------------------------------------------------


class FollowUpKind(StrEnum):
    COMPARE_WITH = "compare_with"
    SHOW_DIFF = "show_diff"
    PIN_BRANCH = "pin_branch"


@dataclass(frozen=True, slots=True)
class FollowUpChip:
    kind: FollowUpKind
    label: str
    project: str
    branches: tuple[str, ...]
    slice: ScopeSlice
    question: str  # "" for PIN_BRANCH (sends nothing)


def _resolved_cell(record: CellObservation, listing: WorkspaceBranchListing) -> ScopeCell | None:
    """The cell a record answered from, or None when it cannot be named
    unambiguously (a union answer on a multi-project workspace)."""
    single = listing.project_names[0] if listing.project_count == 1 else ""
    project = record.project or single
    branch = record.branch or str(record.meta.get("branch") or "")
    return ScopeCell(project, branch) if project and branch else None


def answered_cells(
    observations: ScopeObservations, listing: WorkspaceBranchListing
) -> dict[ScopeCell, tuple[CellObservation, ...]]:
    grouped: dict[ScopeCell, list[CellObservation]] = {}
    for record in observations.records():
        cell = _resolved_cell(record, listing)
        if cell is not None:
            grouped.setdefault(cell, []).append(record)
    ordered = sorted(grouped, key=lambda c: (c.project, c.branch))
    return {cell: tuple(grouped[cell]) for cell in ordered}


def _compare_chip(
    cell: ScopeCell, listing: WorkspaceBranchListing, capabilities: ScopeCapabilities
) -> FollowUpChip | None:
    if not capabilities.branch_selector:
        return None
    row = listing.row(cell.project, cell.branch)
    base = row.base_name if row else None
    if not base or base == cell.branch or not listing.has_branch(cell.project, base):
        return None
    return FollowUpChip(
        kind=FollowUpKind.COMPARE_WITH,
        label=f"compare with {base}",
        project=cell.project,
        branches=(cell.branch, base),
        slice=ScopeSlice.WHOLE_BRANCH,
        question=f"Compare the previous answer between {cell.branch} and {base}: what differs?",
    )


def _show_diff_chip(
    cell: ScopeCell,
    records: tuple[CellObservation, ...],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> FollowUpChip | None:
    if not capabilities.diff_slice or any(r.slice is ScopeSlice.DIFF_HUNKS for r in records):
        return None
    row = listing.row(cell.project, cell.branch)
    if row is None:
        return None
    # A merged tombstone answers scope=diff through its landing sha only.
    tombstone = row.status in _TOMBSTONE_STATUSES and row.merged_into
    target = str(row.merged_into) if tombstone else cell.branch
    if not tombstone and row not in listing.pickable(cell.project):
        return None
    return FollowUpChip(
        kind=FollowUpKind.SHOW_DIFF,
        label="show the diff",
        project=cell.project,
        branches=(target,),
        slice=ScopeSlice.DIFF_HUNKS,
        question="Show the diff hunks behind the previous answer.",
    )


def _pin_chips(
    cells: dict[ScopeCell, tuple[CellObservation, ...]],
    kept_pin: QuestionScope | None,
    capabilities: ScopeCapabilities,
) -> tuple[FollowUpChip, ...]:
    if not capabilities.branch_selector:
        return ()
    chips = []
    for cell, records in cells.items():
        if all(r.branch_origin is BranchOrigin.PINNED for r in records):
            continue
        if kept_pin is not None and cell in kept_pin.cells:
            continue
        chips.append(
            FollowUpChip(
                kind=FollowUpKind.PIN_BRANCH,
                label=f"pin {cell.branch}",
                project=cell.project,
                branches=(cell.branch,),
                slice=ScopeSlice.WHOLE_BRANCH,
                question="",
            )
        )
    return tuple(chips)


def derive_follow_up_chips(
    observations: ScopeObservations,
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    kept_pin: QuestionScope | None,
) -> tuple[FollowUpChip, ...]:
    """At most one chip per kind (UI spec §6.9); the cap is the member count."""
    cells = answered_cells(observations, listing)
    chips: list[FollowUpChip] = []
    if len(cells) == 1:
        ((cell, records),) = cells.items()
        compare = _compare_chip(cell, listing, capabilities)
        diff = _show_diff_chip(cell, records, listing, capabilities)
        chips.extend(c for c in (compare, diff) if c is not None)
    chips.extend(_pin_chips(cells, kept_pin, capabilities))
    return tuple(chips[: len(FollowUpKind)])


def apply_follow_up_chip(
    chip: FollowUpChip, kept_pin: QuestionScope | None, defaults: QuestionScope
) -> tuple[str | None, QuestionScope | None]:
    """(question to send, pin to send it under). COMPARE_WITH / SHOW_DIFF build
    a one-shot pin and leave the kept pin alone; PIN_BRANCH returns no question
    and the kept pin grown by the cell (slice / code / package from ``defaults``)."""
    cells = tuple(ScopeCell(chip.project, branch) for branch in chip.branches)
    if chip.kind is FollowUpKind.PIN_BRANCH:
        if kept_pin is not None:
            return None, kept_pin.with_cells(cells)
        pin = QuestionScope(
            kind=ScopeKind.PIN,
            cells=cells,
            slice=defaults.slice,
            code=defaults.code,
            package=defaults.package,
        )
        return None, pin
    one_shot = QuestionScope(
        kind=ScopeKind.PIN,
        cells=cells,
        slice=chip.slice,
        code=code_compatible_with_slice(chip.slice, defaults.code),
        package=defaults.package,
    )
    return chip.question, one_shot


__all__ = (
    "ALL_PROJECTS",
    "NO_BRANCH",
    "ORIGIN_LABELS",
    "FollowUpChip",
    "FollowUpKind",
    "answered_cells",
    "apply_follow_up_chip",
    "derive_follow_up_chips",
    "render_answer_footer",
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/harness/ask_your_docs/test_answer_footer.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/answer_footer.py tests/harness/ask_your_docs/test_answer_footer.py
git commit -m "ask-your-docs: answer footer + follow-up chips (pure, capability-gated)"
```

---

### Task 9: Streamlit fragments, the chat page rewrite, and the Streamlit floor bump

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/scope_panel.py`
- Modify: `python/pydocs_mcp/harness/ask_your_docs/app.py` (full rewrite below)
- Modify: `pyproject.toml:143` (`streamlit>=1.57`), `uv.lock` (relock)
- Modify: `tests/harness/ask_your_docs/test_app_attachment.py`, `tests/harness/ask_your_docs/test_app_image_attachment.py` (fixture workspace + seeded capabilities)
- Test: `tests/harness/ask_your_docs/test_app_scope_states.py`

**Interfaces:**
- Consumes: everything from Tasks 1–8, plus `snapshot_pin_for_send` (added to `question_scope.py` in this task).
- Produces (`scope_panel.py`): `render_scope_defaults_button()`, `render_scope_defaults_panel(config, catalog, listing, capabilities) -> ScopeDefaultsOverride`, `render_scope_pin_popover(listing, capabilities, defaults, max_cells) -> None`, `render_scope_chip_row(attached, pin) -> None`, `render_follow_up_chips(index, chips) -> FollowUpChip | None`, `render_graph_branch_row(listing, project, capabilities, default_branch) -> GraphBranchSelection(branch, compare_with, changed_only)`, `drop_pin_if_listing_changed(listing, workspace) -> None`, `SOFT_DEFAULTS_CAPTION`.
- Produces (`app.py`): `send_question(question, images, scope, transient_note="")`, `page_scope_capabilities(workspace, model, base_url, config)`, `load_branch_listing(workspace)`, `get_agent(...) -> BuiltAgent`. Session-state keys: `scope_pin: QuestionScope | None`, `scope_pin_keep` (the popover toggle), `scope_defaults_open`, `scope_capabilities` (seeded by tests, else set from the agent build), `scope_listing_workspace`, widget keys `scope_defaults_*`, `scope_pin_*`, `scope_chip_*`, `chip_*`, `chip_clear`, `follow_up_<index>_<kind>`. Transcript entries are dicts: user `{"role", "text", "scope_caption"}`, assistant `{"role", "text", "footer", "chips"}`.

- [ ] **Step 1: Add `snapshot_pin_for_send` to `question_scope.py`**

Append before `__all__` (and add `"snapshot_pin_for_send"` to `__all__`):

```python
def snapshot_pin_for_send(
    pin: QuestionScope | None,
    keep: bool,
    attached: Sequence[AttachedSymbol | str],
    defaults: QuestionScope,
) -> tuple[QuestionScope, QuestionScope | None]:
    """(the scope this question is sent under, the pin that stays active after).

    A one-shot pin (``keep`` false) is gone before ``ask()`` runs — the
    transcript's scope chip is its only trace (UI spec §6.7 "Pin lifecycle").
    """
    scope = pin_with_attached_symbols(pin, attached, defaults) or defaults
    return scope, (pin if pin is not None and keep else None)
```

and this test to `tests/harness/ask_your_docs/test_question_scope.py` (`TestAttachedSymbols`):

```python
    def test_snapshot_drops_a_one_shot_pin_and_keeps_a_kept_one(self):
        defaults = resolve_question_scope_defaults(ScopeDefaultsConfig(), ScopeDefaultsOverride(), _LISTING)
        from pydocs_mcp.harness.ask_your_docs.question_scope import snapshot_pin_for_send

        assert snapshot_pin_for_send(_PIN, False, [], defaults) == (_PIN, None)
        assert snapshot_pin_for_send(_PIN, True, [], defaults) == (_PIN, _PIN)
        assert snapshot_pin_for_send(None, False, [], defaults) == (defaults, None)
```

Run: `pytest tests/harness/ask_your_docs/test_question_scope.py -q` — PASS.

- [ ] **Step 2: Bump the Streamlit floor and relock**

In `pyproject.toml`, line 143 becomes:

```toml
    "streamlit>=1.57",               # WHY: st.bottom (chat composer row) + stateful st.popover (scope pin); chat_input(accept_file=...) needs 1.43
```

Then:

```bash
~/.local/bin/uv lock
uv lock --check
```

Expected: the lock updates the `streamlit` requirement marker for the extra; `uv lock --check` exits 0. (Relock only with `~/.local/bin/uv`; the anaconda `uv` churns markers.)

- [ ] **Step 3: Write the failing AppTests**

Create `tests/harness/ask_your_docs/test_app_scope_states.py`:

```python
"""AppTest smoke tests for the three screen states — AC-19, 20, 21, 21b, 33.

Every test seeds ``scope_capabilities`` so the page never builds the agent
(no serve subprocess, no LLM client). Runs where the [harness-ask-your-docs]
extra is installed (the main checkout's venv), skipped elsewhere.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    ScopeCell,
    ScopeCode,
    ScopeKind,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)

from ._fixture import make_bundle

_HEAD = "a" * 40
U1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("mod_a", "Foo", "class")],
        branches=[("main", _HEAD, None, 1, "active", None), ("feature/retry", _HEAD, "main", 0, "active", None)],
    )
    monkeypatch.setenv("PYDOCS_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return tmp_path


def _app(capabilities=NO_SCOPE_CAPABILITIES):
    from streamlit.testing.v1 import AppTest

    import pydocs_mcp.harness.ask_your_docs.app as appmod

    at = AppTest.from_file(appmod.__file__, default_timeout=60)
    at.session_state["scope_capabilities"] = capabilities
    return at


def test_state_1_default_view(workspace):
    """AC-19."""
    at = _app()
    at.run()
    assert not at.exception, at.exception
    assert any(b.label == "Scope defaults" for b in at.sidebar.button)
    widget_keys = {w.key for w in [*at.selectbox, *at.radio, *at.multiselect]}
    assert not widget_keys & {"scope_project", "scope_code", "scope_package", "scope_defaults_project"}
    assert not any("Searches run only inside this scope" in c.value for c in at.caption)
    assert any(b.key == "scope_pin_apply" for b in at.button)  # the popover rendered its children
    assert not any(m.key == "scope_pin_branches" for m in at.multiselect)


def test_state_2_panel_on_u0(workspace):
    """AC-20: controls + soft-defaults caption; the branch row is a read-only caption."""
    at = _app()
    at.session_state["scope_defaults_open"] = True
    at.run()
    assert not at.exception, at.exception
    assert any(s.key == "scope_defaults_project" for s in at.selectbox)
    assert any(r.key == "scope_defaults_code" for r in at.radio)
    assert not any(r.key == "scope_defaults_slice" for r in at.radio)
    assert not any(s.key == "scope_defaults_branch" for s in at.selectbox)
    captions = [c.value for c in at.caption]
    assert any("Soft defaults" in c for c in captions)
    assert any("branch: main @aaaaaaa (checked out)" in c for c in captions)


def test_state_2_branch_controls_when_advertised(workspace):
    at = _app(U1)
    at.session_state["scope_defaults_open"] = True
    at.run()
    assert not at.exception, at.exception
    assert any(s.key == "scope_defaults_branch" for s in at.selectbox)
    assert any(m.key == "scope_pin_branches" for m in at.multiselect)
    assert not any(r.key == "scope_pin_slice" for r in at.radio)


def test_state_3_pin_active(workspace):
    """AC-21: chips per cell + clear all; the transcript question carries its caption."""
    pin = QuestionScope(
        kind=ScopeKind.PIN,
        cells=(ScopeCell("demo", "main"), ScopeCell("demo", "feature/retry")),
    )
    at = _app()
    at.session_state["scope_pin"] = pin
    at.session_state["messages"] = [
        {"role": "user", "text": "what is Foo?", "scope_caption": "demo · main, feature/retry"}
    ]
    at.session_state["history"] = []
    at.run()
    assert not at.exception, at.exception
    chip_keys = {b.key for b in at.button if b.key.startswith("scope_chip_")}
    assert chip_keys == {"scope_chip_demo_main", "scope_chip_demo_feature/retry"}
    assert any(b.key == "chip_clear" for b in at.button)
    assert any(c.value == "demo · main, feature/retry" for c in at.caption)


def test_clear_all_clears_the_kept_pin(workspace):
    """AC-21b (second half); the one-shot half is snapshot_pin_for_send's unit test."""
    at = _app()
    at.session_state["scope_pin"] = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("demo", "main"),))
    at.run()
    at.button(key="chip_clear").click().run()
    assert not at.exception, at.exception
    assert at.session_state["scope_pin"] is None


def test_removing_the_last_cell_chip_clears_the_pin(workspace):
    at = _app()
    at.session_state["scope_pin"] = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("demo", "main"),))
    at.run()
    at.button(key="scope_chip_demo_main").click().run()
    assert at.session_state["scope_pin"] is None


def test_reset_to_shipped_restores_the_yaml_values(workspace):
    """AC-33."""
    at = _app()
    at.session_state["scope_defaults_open"] = True
    at.run()
    at.radio(key="scope_defaults_code").set_value(ScopeCode.OWN).run()
    assert at.radio(key="scope_defaults_code").value is ScopeCode.OWN
    at.button(key="scope_defaults_reset").click().run()
    assert not at.exception, at.exception
    assert at.radio(key="scope_defaults_code").value is ScopeCode.ALL
```

Update the two existing AppTests to the fixture workspace and the seeded capabilities — `tests/harness/ask_your_docs/test_app_attachment.py` becomes:

```python
import pytest

# The Streamlit UI ships only with the [harness-ask-your-docs] extra, which the core CI
# matrix does not install. Skip (don't fail) when streamlit is absent.
pytest.importorskip("streamlit")

from ._fixture import make_bundle


def test_attached_symbols_render_as_chips(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    import pydocs_mcp.harness.ask_your_docs.app as appmod
    from pydocs_mcp.harness.ask_your_docs.scope_capabilities import NO_SCOPE_CAPABILITIES

    make_bundle(tmp_path / "demo_0123456789.db", members=[("mod_a", "Foo", "class")])
    monkeypatch.setenv("PYDOCS_WORKSPACE", str(tmp_path))
    at = AppTest.from_file(appmod.__file__, default_timeout=60)
    at.session_state["scope_capabilities"] = NO_SCOPE_CAPABILITIES
    at.session_state["attached"] = ["mod_a.Foo"]
    at.run()
    assert not at.exception, at.exception
    assert any("Foo" in b.label for b in at.button)
```

and in `test_app_image_attachment.py` replace the two lines `os.environ["PYDOCS_WORKSPACE"] = ...` / `at = AppTest.from_file(...)` with the same fixture-workspace + seeded-capabilities shape (add `tmp_path, monkeypatch` parameters; the badge assertion `vision: yes (static)` and the image-chip assertions are unchanged).

- [ ] **Step 4: Run the AppTests to verify they fail**

Run: `/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest tests/harness/ask_your_docs/test_app_scope_states.py -q`
Expected: FAIL — `ModuleNotFoundError: ... scope_panel` (raised through `at.exception`).

- [ ] **Step 5: Create `scope_panel.py`**

```python
"""Streamlit fragments for the scope UI (UI spec §6.7, §6.9, §6.10, §6.11).

Streamlit-only by design: every decision is made by the pure modules
(question_scope, answer_footer); this file renders widgets and writes
session state. Callbacks (``on_click``) are the only place a widget's own
session-state key is written, because they run before the next run
instantiates the widget.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.answer_footer import FollowUpChip
from pydocs_mcp.harness.ask_your_docs.attachments import AttachedSymbol
from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    CODE_LABELS,
    SLICE_LABELS,
    QuestionScope,
    ScopeBranchDefault,
    ScopeCell,
    ScopeCode,
    ScopeDefaultsOverride,
    ScopeKind,
    ScopeSlice,
    code_compatible_with_slice,
    pin_summary_label,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities
from pydocs_mcp.retrieval.config.ask_your_docs_models import ANY_PROJECT, ScopeDefaultsConfig

SOFT_DEFAULTS_CAPTION = (
    "Soft defaults — they fill in what the agent leaves unspecified. The agent may "
    "pick another indexed project or branch when the question asks for it."
)
_DEFAULTS_WIDGET_KEYS = (
    "scope_defaults_project",
    "scope_defaults_branch",
    "scope_defaults_slice",
    "scope_defaults_code",
    "scope_defaults_package",
)
_NAME_PREFIX = "name:"  # selectbox option ids for named branches
_NO_COMPARE = "(none)"


# --- shared helpers ----------------------------------------------------------


def _branch_caption(project: str, listing: WorkspaceBranchListing) -> str:
    row = listing.default_row(project)
    if row is None:
        return "no branch information"
    return f"branch: {row.name} @{row.head_sha[:7]} (checked out)"


def _slice_options(capabilities: ScopeCapabilities) -> list[ScopeSlice]:
    options = [ScopeSlice.WHOLE_BRANCH]
    if capabilities.changed_slice:
        options.append(ScopeSlice.CHANGED_FILES)
    if capabilities.diff_slice:
        options.append(ScopeSlice.DIFF_HUNKS)
    return options


def _render_slice_radio(
    key: str, initial: ScopeSlice, capabilities: ScopeCapabilities, *, disabled: bool
) -> ScopeSlice:
    """The Slice radio — hidden until the server advertises a slice value (U2);
    disabled (and whole-branch) while the code filter is dependencies-only (E11)."""
    options = _slice_options(capabilities)
    if len(options) == 1:
        return ScopeSlice.WHOLE_BRANCH
    index = options.index(initial) if initial in options else 0
    picked = st.radio(
        "Slice",
        options,
        index=index,
        format_func=SLICE_LABELS.get,
        horizontal=True,
        key=key,
        disabled=disabled,
    )
    return ScopeSlice.WHOLE_BRANCH if disabled else ScopeSlice(picked)


# --- "Scope defaults" button + panel (§6.7 state 2) --------------------------


def render_scope_defaults_button() -> None:
    """The one sidebar button; the panel stays open for the session once clicked."""
    if st.button("Scope defaults", key="scope_defaults_button"):
        st.session_state["scope_defaults_open"] = not st.session_state.get("scope_defaults_open", False)
        st.rerun()


def _reset_defaults_widgets() -> None:
    # Popped BEFORE the widgets render this run, so each re-seeds from the YAML value.
    for key in _DEFAULTS_WIDGET_KEYS:
        st.session_state.pop(key, None)


def _branch_option_labels(project: str, listing: WorkspaceBranchListing) -> dict[str, str]:
    """option id -> label: the two symbolic entries, then the pickable names
    (a named entry needs a project; under "any" only the symbolic ones)."""
    default = listing.default_row(project) if project != ANY_PROJECT else None
    base = default.base_name if default and default.base_name else "main"
    labels = {
        ScopeBranchDefault.BASE.value: f"{base} (base branch)",
        ScopeBranchDefault.CHECKED_OUT.value: "checked-out branch",
    }
    if project != ANY_PROJECT:
        labels.update({f"{_NAME_PREFIX}{r.name}": r.name for r in listing.pickable(project)})
    return labels


def _render_branch_default_row(
    project: str,
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    config: ScopeDefaultsConfig,
) -> tuple[ScopeBranchDefault, str]:
    if not capabilities.branch_selector:  # U0: informational, nothing can be sent
        names = listing.project_names if project == ANY_PROJECT else (project,)
        for name in names:
            st.caption(f"{name} — {_branch_caption(name, listing)}")
        return config.branch_default, config.branch_name
    labels = _branch_option_labels(project, listing)
    options = list(labels)
    initial = f"{_NAME_PREFIX}{config.branch_name}" if config.branch_name else config.branch_default.value
    index = options.index(initial) if initial in options else 0
    picked = st.selectbox("Branch", options, index=index, format_func=labels.get, key="scope_defaults_branch")
    if picked.startswith(_NAME_PREFIX):
        return ScopeBranchDefault.BASE, picked.removeprefix(_NAME_PREFIX)
    return ScopeBranchDefault(picked), ""


def _render_package_picker(
    project: str, code: ScopeCode, catalog: dict[str, list[str]], config: ScopeDefaultsConfig
) -> str:
    if code is ScopeCode.OWN:  # packages are dependencies (today's rule)
        return ""
    pool = sorted({p for name, pkgs in catalog.items() if project == ANY_PROJECT or name == project for p in pkgs})
    if not pool:
        return ""
    options = ["", *pool]
    index = options.index(config.package) if config.package in options else 0
    return st.selectbox(
        "Package",
        options,
        index=index,
        format_func=lambda p: p or "All packages",
        key="scope_defaults_package",
    )


def render_scope_defaults_panel(
    config: ScopeDefaultsConfig,
    catalog: dict[str, list[str]],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> ScopeDefaultsOverride:
    """The panel below the button; ``ScopeDefaultsOverride()`` (all None) while closed."""
    if not st.session_state.get("scope_defaults_open"):
        return ScopeDefaultsOverride()
    if st.button("Reset to shipped", key="scope_defaults_reset"):
        _reset_defaults_widgets()
        st.rerun()
    projects = [ANY_PROJECT, *listing.project_names]
    index = projects.index(config.project) if config.project in projects else 0
    project = st.selectbox("Project", projects, index=index, key="scope_defaults_project")
    branch_default, branch_name = _render_branch_default_row(project, listing, capabilities, config)
    codes = list(ScopeCode)
    code = ScopeCode(
        st.radio(
            "Code",
            codes,
            index=codes.index(config.code),
            format_func=CODE_LABELS.get,
            horizontal=True,
            key="scope_defaults_code",
        )
    )
    slice_value = _render_slice_radio(
        "scope_defaults_slice", config.slice, capabilities, disabled=code is ScopeCode.DEPS
    )
    package = _render_package_picker(project, code, catalog, config)
    st.caption(SOFT_DEFAULTS_CAPTION)
    return ScopeDefaultsOverride(
        project=project,
        branch_default=branch_default,
        branch_name=branch_name,
        slice=slice_value,
        code=code,
        package=package,
    )


# --- the pin popover (§6.10) --------------------------------------------------


def _apply_pin(project: str, branches: tuple[str, ...], slice_value: ScopeSlice, defaults: QuestionScope) -> None:
    # on_click callback: runs before the rerun, so the popover's own key is writable.
    cells = tuple(ScopeCell(project, b) for b in branches) or (ScopeCell(project, ""),)
    st.session_state["scope_pin"] = QuestionScope(
        kind=ScopeKind.PIN,
        cells=cells,
        slice=slice_value,
        code=code_compatible_with_slice(slice_value, defaults.code),
        package=defaults.package,
    )
    st.session_state["scope_pin_popover"] = False


def _clear_pin() -> None:
    st.session_state["scope_pin"] = None
    st.session_state["scope_pin_popover"] = False


def _render_pin_branches(
    project: str, listing: WorkspaceBranchListing, capabilities: ScopeCapabilities
) -> tuple[str, ...]:
    if not capabilities.branch_selector:
        st.caption(_branch_caption(project, listing))
        return ()
    options = [r.name for r in listing.pickable(project)]
    # A closed list by construction: st.multiselect accepts no free text (R6).
    return tuple(st.multiselect("Branches", options, key="scope_pin_branches"))


def _render_pin_controls(
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    defaults: QuestionScope,
    max_cells: int,
) -> None:
    projects = list(listing.project_names)
    if not projects:
        st.caption("No indexed projects in this workspace.")
        return
    project = st.selectbox("Project", projects, key="scope_pin_project")
    branches = _render_pin_branches(project, listing, capabilities)
    slice_value = _render_slice_radio(
        "scope_pin_slice", ScopeSlice.WHOLE_BRANCH, capabilities, disabled=defaults.code is ScopeCode.DEPS
    )
    st.toggle("keep for next", key="scope_pin_keep")
    count = max(len(branches), 1)
    too_many = count > max_cells
    if too_many:
        st.caption(f"{count} cells exceed max_cells={max_cells} (ask_your_docs.scope.max_cells)")
    pin_col, clear_col = st.columns(2)
    pin_col.button(
        "Pin",
        key="scope_pin_apply",
        disabled=too_many,
        on_click=_apply_pin,
        args=(project, branches, slice_value, defaults),
    )
    clear_col.button("Clear", key="scope_pin_clear", on_click=_clear_pin)


def render_scope_pin_popover(
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    defaults: QuestionScope,
    max_cells: int,
) -> None:
    """The icon button left of the chat input; its label is the pin summary."""
    label = pin_summary_label(st.session_state.get("scope_pin")) or "scope"
    with st.popover(label, key="scope_pin_popover", on_change="rerun", icon=":material/tune:"):
        _render_pin_controls(listing, capabilities, defaults, max_cells)


# --- the chip row (§6.7 state 3) ---------------------------------------------


def _symbol_of(attachment: AttachedSymbol | str) -> str:
    return attachment.symbol if isinstance(attachment, AttachedSymbol) else attachment


def _pin_chips(pin: QuestionScope | None) -> list[tuple[str, str, ScopeCell | None]]:
    """(label, key, cell) per pin element; ``None`` cell = the slice chip."""
    if pin is None:
        return []
    chips = [
        (f"✕ {cell.branch or cell.project}", f"scope_chip_{cell.project}_{cell.branch}", cell)
        for cell in pin.cells
    ]
    if pin.slice is not ScopeSlice.WHOLE_BRANCH:
        chips.append((f"✕ {SLICE_LABELS[pin.slice]}", "scope_chip_slice", None))
    return chips


def _remove_pin_element(pin: QuestionScope, cell: ScopeCell | None) -> None:
    st.session_state["scope_pin"] = (
        replace(pin, slice=ScopeSlice.WHOLE_BRANCH) if cell is None else pin.without_cell(cell)
    )


def render_scope_chip_row(attached: list, pin: QuestionScope | None) -> None:
    """Pin element chips first, then attached symbols, then "clear all" (both)."""
    pin_chips = _pin_chips(pin)
    if not pin_chips and not attached:
        return
    cols = st.columns(len(pin_chips) + len(attached) + 1)
    for col, (label, key, cell) in zip(cols, pin_chips, strict=False):
        if col.button(label, key=key):
            _remove_pin_element(pin, cell)
            st.rerun()
    for col, attachment in zip(cols[len(pin_chips) :], list(attached), strict=False):
        symbol = _symbol_of(attachment)
        if col.button(f"✕ {symbol.rsplit('.', 1)[-1]}", key=f"chip_{symbol}"):
            attached.remove(attachment)
            st.rerun()
    if cols[-1].button("clear all", key="chip_clear"):
        attached.clear()
        st.session_state["scope_pin"] = None
        st.rerun()


# --- follow-up chips (§6.9) --------------------------------------------------


def render_follow_up_chips(index: int, chips: Sequence[FollowUpChip]) -> FollowUpChip | None:
    """Small buttons under the footer; returns the clicked chip, if any."""
    if not chips:
        return None
    clicked: FollowUpChip | None = None
    for col, chip in zip(st.columns(len(chips)), chips, strict=True):
        if col.button(chip.label, key=f"follow_up_{index}_{chip.kind.value}"):
            clicked = chip
    return clicked


# --- graph page row (§6.11) --------------------------------------------------


@dataclass(frozen=True, slots=True)
class GraphBranchSelection:
    branch: str
    compare_with: str | None
    changed_only: bool


def render_graph_branch_row(
    listing: WorkspaceBranchListing,
    project: str,
    capabilities: ScopeCapabilities,
    default_branch: str,
) -> GraphBranchSelection:
    """Branch selectbox (+ "Compare with" and "changed only" on U1); a caption on U0."""
    if not capabilities.branch_selector:
        st.caption(_branch_caption(project, listing))
        row = listing.default_row(project)
        return GraphBranchSelection(row.name if row else "", None, False)
    names = [r.name for r in listing.pickable(project)] or ["—"]
    index = names.index(default_branch) if default_branch in names else 0
    branch = st.selectbox("Branch", names, index=index, key="graph_branch")
    compare_options = [_NO_COMPARE, *[n for n in names if n != branch]]
    compare = st.selectbox("Compare with", compare_options, key="graph_compare_with")
    changed_only = st.checkbox(
        "changed only", value=False, key="graph_changed_only", disabled=compare == _NO_COMPARE
    )
    return GraphBranchSelection(branch, None if compare == _NO_COMPARE else compare, changed_only)


# --- pin lifecycle (§6.7 rule iv) --------------------------------------------


def drop_pin_if_listing_changed(listing: WorkspaceBranchListing, workspace: str) -> None:
    """A workspace change reloads the listing; a pin with a cell the new
    listing lacks is dropped whole, with a toast naming the missing cell (E12)."""
    previous = st.session_state.get("scope_listing_workspace")
    st.session_state["scope_listing_workspace"] = workspace
    pin = st.session_state.get("scope_pin")
    if previous is None or previous == workspace or pin is None:
        return
    missing = next(
        (
            c
            for c in pin.cells
            if not listing.knows_project(c.project)
            or (c.branch and not listing.has_branch(c.project, c.branch))
        ),
        None,
    )
    if missing is not None:
        st.session_state["scope_pin"] = None
        st.toast(f"Pin dropped: {missing.project} · {missing.branch or 'default'} is not in this workspace")


__all__ = (
    "SOFT_DEFAULTS_CAPTION",
    "GraphBranchSelection",
    "drop_pin_if_listing_changed",
    "render_follow_up_chips",
    "render_graph_branch_row",
    "render_scope_chip_row",
    "render_scope_defaults_button",
    "render_scope_defaults_panel",
    "render_scope_pin_popover",
)
```

- [ ] **Step 6: Rewrite `app.py`**

Replace the whole file with:

```python
"""Streamlit chat UI for the ask-your-docs agent.

Launched by the ``harness-ask-your-docs`` CLI (``harness.ask_your_docs.cli``). Connection
settings prefill from env: PYDOCS_WORKSPACE, LLM_MODEL, OPENAI_BASE_URL,
PYDOCS_CONFIG.

Scope (UI spec 2026-09-04): soft defaults live behind the sidebar's
"Scope defaults" button; a per-question pin lives in the popover left of the
chat input, as chips in the attachment row, and as follow-up chips under an
answer. ``send_question`` is the ONE send path.
"""

from __future__ import annotations

import asyncio
import base64
import os
import threading
from pathlib import Path

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.agent import (
    ask,
    build_agent_with_scope_capabilities,
    reformulate,
    weave_attachments,
)
from pydocs_mcp.harness.ask_your_docs.answer_footer import (
    apply_follow_up_chip,
    derive_follow_up_chips,
    render_answer_footer,
)
from pydocs_mcp.harness.ask_your_docs.attachments import (
    ImageAttachment,
    text_only_policy,
    update_image_store,
    validate_attachment,
)
from pydocs_mcp.harness.ask_your_docs.catalog import (
    EMPTY_BRANCH_LISTING,
    workspace_branch_listing,
    workspace_catalog,
)
from pydocs_mcp.harness.ask_your_docs.multimodal import detect_capabilities
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    resolve_question_scope_defaults,
    scope_caption_text,
    snapshot_pin_for_send,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    BuiltAgent,
    ScopeCapabilities,
)
from pydocs_mcp.harness.ask_your_docs.scope_interceptor import ScopeObservations, ScopeRuntime
from pydocs_mcp.harness.ask_your_docs.scope_panel import (
    drop_pin_if_listing_changed,
    render_follow_up_chips,
    render_scope_chip_row,
    render_scope_defaults_button,
    render_scope_defaults_panel,
    render_scope_pin_popover,
)
from pydocs_mcp.harness.ask_your_docs.theme import (
    current_palette,
    render_appearance_toggle,
    theme_css,
)
from pydocs_mcp.retrieval.config.app_config import AppConfig

st.set_page_config(
    page_title="ask your docs",
    page_icon="✦",
    layout="centered",
    # Keep the sidebar (and its page-navigation menu: chat / graph) open on load.
    initial_sidebar_state="expanded",
)


@st.cache_resource
def event_loop() -> asyncio.AbstractEventLoop:
    # The agent's async work must live on ONE loop across Streamlit reruns.
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def run(coro):
    return asyncio.run_coroutine_threadsafe(coro, event_loop()).result()


@st.cache_resource
def load_catalog(workspace: str) -> dict[str, list[str]]:
    # Cached per workspace (no ttl) and shared with the agent prompt, so the
    # pickers and the model always see the same projects. A newly indexed repo
    # appears on restart. Read-only — never mutates the bundles.
    return workspace_catalog(workspace)


@st.cache_resource
def load_branch_listing(workspace: str):
    # Same lifetime as the catalog; feeds the panel, the popover, the footer
    # and (when advertised) the prompt's branch segment. Read-only.
    return workspace_branch_listing(workspace)


@st.cache_resource
def load_ayd_config(config: str | None):
    # One YAML file configures both the pydocs-mcp subprocess and the agent
    # (spec §3.5): the same PYDOCS_CONFIG path, loaded through AppConfig
    # layering (defaults → overlay → PYDOCS_ASK_YOUR_DOCS__* env).
    return AppConfig.load(explicit_path=Path(config) if config else None).ask_your_docs


@st.cache_resource
def get_capabilities(model: str, base_url: str | None, config: str | None):
    # Detection runs once per (model, base_url, config) cache entry on the
    # shared background loop; the ladder default (static table only) is
    # network-free, so this is safe at sidebar-render time.
    cfg = load_ayd_config(config)
    return run(detect_capabilities(model, base_url or None, cfg.multimodal.detection))


@st.cache_resource
def get_agent(workspace: str, model: str, base_url: str | None, config: str | None) -> BuiltAgent:
    return run(
        build_agent_with_scope_capabilities(
            workspace,
            model,
            base_url or None,
            config or None,
            catalog=load_catalog(workspace),
            config=load_ayd_config(config),
            capabilities=get_capabilities(model, base_url, config),
            branches=load_branch_listing(workspace),
        )
    )


def page_scope_capabilities(
    workspace: str, model: str, base_url: str, config: str
) -> ScopeCapabilities:
    """The server's scope capabilities for this page.

    A seeded ``st.session_state["scope_capabilities"]`` (tests) wins; otherwise
    the cached agent build is the only source — so the first render of a
    workspace starts the serve subprocess, once. A failed build hides every
    branch control and warns (R10: never an error from a missing capability).
    """
    seeded = st.session_state.get("scope_capabilities")
    if isinstance(seeded, ScopeCapabilities):
        return seeded
    if not (workspace and model):
        return NO_SCOPE_CAPABILITIES
    try:
        caps = get_agent(workspace, model, base_url, config).scope_capabilities
    except Exception as exc:  # no bundles, no credentials, server failed to start
        st.warning(f"Couldn't start the agent: {exc}")
        return NO_SCOPE_CAPABILITIES
    st.session_state["scope_capabilities"] = caps  # shared with the graph page
    return caps


with st.sidebar:
    st.markdown('<div class="side-label">Appearance</div>', unsafe_allow_html=True)
    render_appearance_toggle()

    st.markdown('<div class="side-label">Connection</div>', unsafe_allow_html=True)
    workspace = st.text_input("Workspace", os.environ.get("PYDOCS_WORKSPACE", ""))
    model = st.text_input("Model", os.environ.get("LLM_MODEL", "gpt-4o-mini"))
    base_url = st.text_input("Base URL (optional)", os.environ.get("OPENAI_BASE_URL", ""))
    config = st.text_input("pydocs config (optional)", os.environ.get("PYDOCS_CONFIG", ""))
    if model:
        # Capability badge: makes auto's routing visible (spec §3.7) —
        # e.g. "vision: yes (static)" / "vision: no (default)".
        caps = get_capabilities(model, base_url, config)
        st.caption(f"vision: {'yes' if caps.multimodal else 'no'} ({caps.source})")
    st.caption("Point Workspace at a folder of pydocs-mcp index bundles.")

    catalog: dict[str, list[str]] = {}
    listing = EMPTY_BRANCH_LISTING
    if workspace:
        try:
            catalog = load_catalog(workspace)
            listing = load_branch_listing(workspace)
        except Exception as exc:  # unreadable dir, no bundles, corrupt db
            st.warning(f"Couldn't scan workspace: {exc}")
    ayd_cfg = load_ayd_config(config)
    scope_caps = page_scope_capabilities(workspace, model, base_url, config)
    # Hidden by default: one button, the panel only once clicked (state 2).
    render_scope_defaults_button()
    override = render_scope_defaults_panel(ayd_cfg.scope, catalog, listing, scope_caps)

st.markdown(theme_css(current_palette()), unsafe_allow_html=True)
st.markdown(
    '<div class="brand">ask your <span class="accent">docs</span></div>'
    '<div class="brand-sub">grounded answers from your indexed code and docs</div>',
    unsafe_allow_html=True,
)

if not workspace:
    st.markdown(
        """<div class="empty">
        <div class="empty-title">Point me at your indexed repos</div>
        <div>Set a <b>Workspace</b> in the sidebar — a folder of pydocs-mcp
        <code>.db</code> / <code>.tq</code> bundles — then ask things like:</div>
        <div class="eg">how does routing work?</div>
        <div class="eg">what does IndexStorePort.load return?</div>
        <div class="eg">who calls BaseIndexStore.append?</div>
        </div>""",
        unsafe_allow_html=True,
    )
    st.stop()

defaults = resolve_question_scope_defaults(ayd_cfg.scope, override, listing)
drop_pin_if_listing_changed(listing, workspace)

if "messages" not in st.session_state:
    st.session_state.messages, st.session_state.history = [], []

clicked_chip = None
for index, entry in enumerate(st.session_state.messages):
    with st.chat_message(entry["role"]):
        if entry.get("scope_caption"):
            st.caption(entry["scope_caption"])
        st.markdown(entry["text"])
        if entry["role"] == "assistant":
            st.caption(entry["footer"])
            clicked_chip = render_follow_up_chips(index, entry["chips"]) or clicked_chip

attached = st.session_state.setdefault("attached", [])
render_scope_chip_row(attached, st.session_state.get("scope_pin"))

# Image chips from the last image-bearing question — visually distinct from
# the symbol-name buttons above (🖼 markdown pills, not buttons). Pre-send
# removal is the chat_input file widget's own ✕ (accept_file arrives
# atomically with the question, spec §4.7).
image_chips = st.session_state.setdefault("image_chips", [])
if image_chips:
    st.caption("Images attached to the last question:")
    st.markdown(" ".join(f"`🖼 {name}`" for name in image_chips))


def _collect_images(files, images_cfg) -> tuple[ImageAttachment, ...]:
    """UploadedFiles → validated ImageAttachments; violations render an
    inline error chip and drop the offending file (spec §3.6)."""
    if len(files) > images_cfg.max_per_turn:
        st.warning(
            f"only the first {images_cfg.max_per_turn} images were kept (images.max_per_turn)"
        )
    collected: list[ImageAttachment] = []
    for f in files[: images_cfg.max_per_turn]:
        att = ImageAttachment(
            name=f.name,
            media_type=f.type or "application/octet-stream",
            data_b64=base64.b64encode(f.getvalue()).decode(),
        )
        try:
            validate_attachment(att, images_cfg)
        except ValueError as exc:
            st.error(str(exc))
            continue
        collected.append(att)
    return tuple(collected)


def send_question(
    question: str,
    images: tuple[ImageAttachment, ...],
    scope: QuestionScope,
    transient_note: str = "",
) -> None:
    """The ONE send path — the chat input and the follow-up chips both call it,
    so a canned question is woven, reformulated, prefixed and observed exactly
    like a typed one."""
    st.session_state.image_chips = [att.name for att in images]
    # Session image store: bytes from recent turns stay reinspectable by the
    # reinspect_images tool (history itself keeps only the placeholder).
    image_store = st.session_state.setdefault("image_store", {})
    # Snapshot BEFORE folding this turn's images: only LATER questions need to
    # reinspect them (necessity gating).
    prior_images = dict(image_store)
    update_image_store(image_store, images, retention=ayd_cfg.images.session_retention)
    shown = question + ("\n\n" + " ".join(f"`🖼 {att.name}`" for att in images) if images else "")
    caption = scope_caption_text(scope)
    st.session_state.messages.append({"role": "user", "text": shown, "scope_caption": caption})
    with st.chat_message("user"):
        if caption:
            st.caption(caption)
        st.markdown(shown)
    with st.chat_message("assistant"), st.spinner("searching your docs…"):
        built = get_agent(workspace, model, base_url, config)
        woven = weave_attachments(attached, question)
        st.session_state.attached = []
        # reformulate is text-only by contract (§3.6): it runs on the woven
        # question BEFORE image blocks are attached.
        standalone = run(reformulate(built.llm, st.session_state.history, woven))
        observations = ScopeObservations()  # the interceptor's only channel back
        runtime = ScopeRuntime(
            listing=listing,
            capabilities=built.scope_capabilities,
            max_cells=ayd_cfg.scope.max_cells,
        )
        answer = run(
            ask(
                built.graph,
                st.session_state.history,
                standalone,
                scope=scope,
                images=images,
                image_store=prior_images,  # PRIOR turns only — see snapshot note above
                transient_note=transient_note,
                observations=observations,
                scope_runtime=runtime,
            )
        )
        footer = render_answer_footer(observations, listing)
        chips = derive_follow_up_chips(
            observations, listing, built.scope_capabilities, st.session_state.get("scope_pin")
        )
        st.markdown(answer)
        st.caption(footer)
        render_follow_up_chips(len(st.session_state.messages), chips)
    st.session_state.messages.append(
        {"role": "assistant", "text": answer, "footer": footer, "chips": chips}
    )


if clicked_chip is not None:
    # Handled BEFORE the popover renders: a PIN_BRANCH chip writes the toggle's key.
    canned, pin = apply_follow_up_chip(clicked_chip, st.session_state.get("scope_pin"), defaults)
    if canned is None:
        st.session_state["scope_pin"] = pin
        st.session_state["scope_pin_keep"] = True
        st.rerun()
    send_question(canned, (), pin)

with st.bottom:
    # The composer row: the pin popover left of the chat input. An inline
    # chat_input is pinned by the bottom container (Streamlit >= 1.57).
    pin_col, input_col = st.columns([1, 12])
    with pin_col:
        render_scope_pin_popover(listing, scope_caps, defaults, ayd_cfg.scope.max_cells)
    with input_col:
        submission = st.chat_input(
            "Ask about your indexed projects…",
            accept_file="multiple",
            file_type=["png", "jpg", "jpeg", "webp", "gif"],
        )

if submission:
    question = submission.text or ""
    images = _collect_images(list(submission.files or ()), ayd_cfg.images)
    caps = get_capabilities(model, base_url, config)
    verdict = text_only_policy(images, caps, ayd_cfg.multimodal, model=model)
    if verdict is not None and verdict.kind == "reject":
        # Fail loudly BEFORE any LLM call (spec §3.8): nothing is sent, and
        # the question text stays visible for copy-back.
        st.error(verdict.message)
        st.info(f"Your question (not sent): {question}")
        st.stop()
    transient_note = ""
    if verdict is not None and verdict.kind == "describe":
        st.warning("The model cannot see the attached image(s); answering from text only.")
        # The cannot-see note rides ask()'s transient_note (attached AFTER
        # reformulation, never persisted) — the scope-pin pattern.
        transient_note = verdict.message
        images = ()
    scope, kept_pin = snapshot_pin_for_send(
        st.session_state.get("scope_pin"),
        bool(st.session_state.get("scope_pin_keep", False)),
        attached,
        defaults,
    )
    st.session_state["scope_pin"] = kept_pin  # a one-shot pin is gone before ask() runs
    send_question(question, images, scope, transient_note)
```

- [ ] **Step 7: Run the AppTests and the core suite**

Run: `/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest tests/harness/ask_your_docs/test_app_scope_states.py tests/harness/ask_your_docs/test_app_attachment.py tests/harness/ask_your_docs/test_app_image_attachment.py -q`
Expected: PASS. If `at.radio(key=...)` cannot find a radio inside the sidebar panel, use `at.sidebar.radio(key=...)`; if the popover's children are not reachable through `at.button`, the popover parsed as an unknown block — check `at.get("popover")` and, if empty, verify the installed Streamlit is ≥ 1.57 (`python -c "import streamlit; print(streamlit.__version__)"`).

Run: `pytest tests/harness/ask_your_docs -q`
Expected: PASS (the pure suites are unaffected).

- [ ] **Step 8: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/scope_panel.py python/pydocs_mcp/harness/ask_your_docs/app.py python/pydocs_mcp/harness/ask_your_docs/question_scope.py pyproject.toml uv.lock tests/harness/ask_your_docs/test_app_scope_states.py tests/harness/ask_your_docs/test_app_attachment.py tests/harness/ask_your_docs/test_app_image_attachment.py tests/harness/ask_your_docs/test_question_scope.py
git commit -m "ask-your-docs: scope defaults panel, pin popover, chip row, footer + follow-up chips in the chat page"
```

---

### Task 10: Graph page — shared panel, branch row, branch-carrying attachments

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/pages/2_Graph.py`

**Interfaces:**
- Consumes: `render_scope_defaults_button`, `render_scope_defaults_panel`, `render_graph_branch_row`, `GraphBranchSelection` (Task 9); `resolve_question_scope_defaults`, `resolve_default_branch` (Task 4); `AttachedSymbol` (Task 4); `CatalogService.branch_listing()` (Task 3); `NO_SCOPE_CAPABILITIES`, `ScopeCapabilities` (Task 5).
- Produces: the graph page's `selection: GraphBranchSelection` (consumed by Task 15's compare overlay); "Add to question" appends `AttachedSymbol(selected, project, selection.branch)`.

- [ ] **Step 1: Edit the page**

Add the imports:

```python
from pathlib import Path

from pydocs_mcp.harness.ask_your_docs.attachments import AttachedSymbol
from pydocs_mcp.harness.ask_your_docs.catalog import EMPTY_BRANCH_LISTING, CatalogService
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    resolve_default_branch,
    resolve_question_scope_defaults,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.harness.ask_your_docs.scope_panel import (
    render_graph_branch_row,
    render_scope_defaults_button,
    render_scope_defaults_panel,
)
from pydocs_mcp.retrieval.config.app_config import AppConfig
```

After `_projects`, add two cached loaders:

```python
@st.cache_data(ttl=60)
def _listing(workspace: str):
    return CatalogService(workspace).branch_listing()


@st.cache_resource
def _scope_config():
    config = os.environ.get("PYDOCS_CONFIG", "")
    return AppConfig.load(explicit_path=Path(config) if config else None).ask_your_docs.scope
```

In the sidebar block, after the `project = st.selectbox("Project", ...)` line and before `content = st.radio(...)`, insert:

```python
    listing = EMPTY_BRANCH_LISTING
    if workspace and projects:
        listing = _listing(workspace)
    # Capabilities come from the chat page's agent build (same session); the
    # graph page never starts the server itself.
    seeded = st.session_state.get("scope_capabilities")
    scope_caps: ScopeCapabilities = seeded if isinstance(seeded, ScopeCapabilities) else NO_SCOPE_CAPABILITIES
    render_scope_defaults_button()
    override = render_scope_defaults_panel(_scope_config(), projects, listing, scope_caps)
    defaults = resolve_question_scope_defaults(_scope_config(), override, listing)
    default_row = listing.default_row(project)
    default_branch = resolve_default_branch(defaults, project, listing) or (
        default_row.name if default_row else ""
    )
    st.markdown('<div class="side-label">Branch</div>', unsafe_allow_html=True)
    selection = render_graph_branch_row(listing, project, scope_caps, default_branch)
```

In the "Selected" sidebar block, after `st.markdown(f"**{meta.title}**  \n`{meta.id}`")`, add the branch label:

```python
            if selection.branch:
                st.caption(f"branch: {selection.branch}")
```

and replace the "Add to question" block with:

```python
        if st.button("➕ Add to question", key="graph_attach"):
            att = st.session_state.setdefault("attached", [])
            symbol = AttachedSymbol(selected, project, selection.branch)
            if symbol not in att:
                att.append(symbol)
            st.toast(f"Attached {selected} ({project} · {selection.branch or 'default branch'})")
```

- [ ] **Step 2: Verify the page still parses and runs headless**

Run: `PYDOCS_WORKSPACE=/nonexistent /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -c "import ast, pathlib; ast.parse(pathlib.Path('python/pydocs_mcp/harness/ask_your_docs/pages/2_Graph.py').read_text())"`
Expected: no output (parses). Then a manual smoke: `harness-ask-your-docs --workspace <a real workspace>` → open the Graph page → the "Scope defaults" button appears, the Branch caption names the stamped branch, "Add to question" toasts `(project · branch)` and the chat page's chip row shows the symbol.

- [ ] **Step 3: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/pages/2_Graph.py
git commit -m "ask-your-docs graph page: shared scope panel, branch row, branch-carrying attachments"
```

---

### Task 11: U0 docs, CHANGELOG, and the gate run

**Files:**
- Modify: `examples/harness/ask_your_docs_agent/README.md` (the paragraph beginning "The sidebar's **Scope** pickers")
- Modify: `CHANGELOG.md` (the `[0.6.0] — Unreleased` section)
- Test: `tests/test_doc_conformance.py`, full suite

- [ ] **Step 1: Replace the README paragraph**

```markdown
Scope is hidden by default. The sidebar's **Scope defaults** button reveals
soft defaults (project / own code vs dependencies / package — and, once the
server advertises branches, a branch default) that fill in whatever the agent
leaves unspecified; the agent may still pick another indexed project or
branch when the question asks for it. To pin one question hard, use the
**scope** popover left of the chat input: the pin overwrites the agent's
choices on every tool call, shows as removable chips in the attachment row,
and — when it spans several branches — returns one labeled result per branch.
A `langchain-mcp-adapters` tool interceptor enforces both the defaults and the
pins deterministically; the pinned question is also prefixed with a
`[pinned scope: ...]` note so the agent knows why. Every answer ends with one
footer line naming the project, branch and index state it came from, plus
follow-up chips (compare with the base branch, pin this branch, show the
diff) when they apply. Toggle **Light mode** at the top of the sidebar to
switch the palette.
```

- [ ] **Step 2: Add the CHANGELOG entry**

Under `## [0.6.0] — Unreleased`, add a bullet (keep the section's existing style):

```markdown
- **ask-your-docs scope UI**: the sidebar scope pickers become hidden soft
  defaults (`ask_your_docs.scope` in YAML, a "Scope defaults" panel per
  session) plus per-question hard pins (a popover left of the chat input,
  chips in the attachment row, follow-up chips under answers) that fan out
  over `(project, branch)` cells with labeled results; every answer carries a
  footer naming its project, branch, sha and index state. Branch and slice
  controls stay hidden until the server advertises `branch` / `changed` /
  `diff`. The `[harness-ask-your-docs]` extra now requires `streamlit>=1.57`.
```

- [ ] **Step 3: Run the README jargon audit and the doc conformance test**

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" -not -path "*/node_modules/*" -not -path "*/.git/*" | xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
pytest tests/test_doc_conformance.py -q
```

Expected: no audit matches; conformance PASS.

- [ ] **Step 4: Run the full gate set**

```bash
ruff format python/ tests/
ruff check python/ tests/
mypy python/pydocs_mcp
complexipy python/pydocs_mcp --max-complexity-allowed 15
vulture python/pydocs_mcp --min-confidence 80
pytest tests/ --ignore=tests/test_parity.py -q
uv lock --check
git checkout -- complexipy-snapshot.json
```

Expected: all green (AC-26). The last line restores the complexipy snapshot a local run rewrites in place — never commit it. If `vulture` reports the unused parameters of the U0 `_default_branch` stub in `scope_interceptor.py`, replace the stub body with `del scope, project, runtime  # consumed by the U1 body` above the `args.pop` line.

- [ ] **Step 5: Commit**

```bash
git add examples/harness/ask_your_docs_agent/README.md CHANGELOG.md python/ tests/
git commit -m "ask-your-docs: scope UI docs + changelog (U0 complete)"
```

**U0 exit:** open the U0 PR against `main`. Gate: AC-1, 2, 3, 4, 6b, 10, 11, 13, 14, 14b, 15, 18, 19, 20, 21, 21b, 22, 23, 24, 25, 26, 27, 28, 29, 30, 33, 34 green; the U1 / U2 criteria below are exercised against fakes in the same code base (AC-8, AC-9, AC-17, AC-31 already pass in Tasks 6 and 8).

---

# Stage U1 — after multi-branch P1 (the `branch` selector, schema v17, `base_name` stamped)

Precondition: the multi-branch amendment `1c371bc` is committed (it is) and the P1 plan's Task 16 (the `branch` parameter contract PR) has landed on `main`, so a P1 server advertises `branch` on all nine tools. Everything below is exercised against fake tools that advertise `branch` and stays dormant on P0 servers.

### Task 12: Interceptor branch rules — DEFAULT injection / replacement and PIN cell targeting

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/scope_interceptor.py` (`_default_branch`)
- Test: `tests/harness/ask_your_docs/test_scope_interceptor.py`

**Interfaces:**
- Consumes: `resolve_default_branch` (Task 4), `WorkspaceBranchListing.has_branch` (Task 3), `ScopeCapabilities.branch_selector` (Task 5).
- Produces: `_default_branch(tool, args, scope, project, runtime) -> tuple[str, BranchOrigin, bool]` with the full U1 body; `target_cells` / `cell_arguments` (Task 6) now see `branch` because the capability is true.

- [ ] **Step 1: Write the failing tests**

Append to `tests/harness/ask_your_docs/test_scope_interceptor.py`:

```python
RUNTIME_U1 = ScopeRuntime(listing=LISTING, capabilities=BRANCHED, max_cells=4)


def _default(**kwargs) -> QuestionScope:
    return QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),), **kwargs)


def test_ac2b_default_injects_the_base_when_stamped_and_different():
    from pydocs_mcp.harness.ask_your_docs.question_scope import ScopeBranchDefault

    handler = RecordingHandler()
    with active(DEFAULT_UNION, RUNTIME_U1):
        call("get_symbol", {"target": "x", "project": "backend"}, handler)
        call("get_symbol", {"target": "x"}, handler)  # union: nothing
        call("get_symbol", {"target": "x", "project": "tooling"}, handler)  # base == default row
    with active(_default(branch_default=ScopeBranchDefault.CHECKED_OUT), RUNTIME_U1):
        call("get_symbol", {"target": "x", "project": "backend"}, handler)
    assert handler.sent == [
        {"target": "x", "project": "backend", "branch": "main"},
        {"target": "x"},
        {"target": "x", "project": "tooling"},
        {"target": "x", "project": "backend"},
    ]


def test_ac2b_p0_bundle_without_base_name_sends_nothing():
    listing = WorkspaceBranchListing(projects={"backend": (_row("main", default=True),)})
    handler = RecordingHandler()
    with active(DEFAULT_UNION, ScopeRuntime(listing=listing, capabilities=BRANCHED, max_cells=4)):
        call("grep", {"pattern": "p", "project": "backend"}, handler)
    assert handler.sent == [{"pattern": "p", "project": "backend"}]


def test_agent_chosen_branch_is_kept_and_an_unknown_one_is_replaced(caplog):
    observations = ScopeObservations()
    handler = RecordingHandler()
    with active(DEFAULT_UNION, RUNTIME_U1, observations), caplog.at_level("INFO"):
        call("get_symbol", {"target": "x", "project": "backend", "branch": "main"}, handler)
        call("get_symbol", {"target": "x", "project": "backend", "branch": "nope"}, handler)
    assert handler.sent == [
        {"target": "x", "project": "backend", "branch": "main"},
        {"target": "x", "project": "backend", "branch": "main"},
    ]
    kept, replaced = observations.records()
    assert (kept.branch_origin, kept.replaced) == (BranchOrigin.AGENT_CHOSEN, False)
    assert (replaced.branch_origin, replaced.replaced) == (BranchOrigin.DEFAULT, True)
    record = json.loads(caplog.records[-1].getMessage())
    assert record["argument"] == "branch" and record["passed"] == "nope" and record["replacement"] == "main"


def test_unknown_branch_on_a_union_request_is_dropped():
    handler = RecordingHandler()
    with active(DEFAULT_UNION, RUNTIME_U1):
        call("get_symbol", {"target": "x", "branch": "nope"}, handler)
        call("get_symbol", {"target": "x", "branch": "feature/x"}, handler)  # exists in some project
    assert handler.sent == [{"target": "x"}, {"target": "x", "branch": "feature/x"}]


@pytest.mark.parametrize("tool", NINE)
def test_ac5_single_cell_pin_overwrites_project_and_branch(tool):
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"),))
    handler = RecordingHandler()
    with active(pin, RUNTIME_U1):
        call(tool, {"project": "tooling", "branch": "feature/x", "q": 1}, handler)
    assert handler.sent == [{"project": "backend", "branch": "main", "q": 1}]


def test_ac6_three_cell_fan_out_in_cell_order_with_labels_items_and_first_meta():
    pin = QuestionScope(
        kind=ScopeKind.PIN,
        cells=(ScopeCell("backend", "main"), ScopeCell("backend", "feature/x"), ScopeCell("tooling", "main")),
    )
    handler = RecordingHandler(
        [
            _result("A", items=[{"id": 1}], meta={"branch": "main"}),
            _result("B", items=[{"id": 2}], meta={"branch": "feature/x"}),
            _result("C", items=[], meta={"branch": "main", "project": "tooling"}),
        ]
    )
    with active(pin, RUNTIME_U1):
        merged = call("search_codebase", {"query": "q"}, handler)
    assert handler.sent == [
        {"query": "q", "project": "backend", "branch": "main"},
        {"query": "q", "project": "backend", "branch": "feature/x"},
        {"query": "q", "project": "tooling", "branch": "main"},
    ]
    texts = [b.text for b in merged.content]
    assert texts[::2] == ["## backend · main\n", "## backend · feature/x\n", "## tooling · main\n"]
    assert merged.structuredContent["items"] == [
        {"id": 1, "project": "backend", "branch": "main"},
        {"id": 2, "project": "backend", "branch": "feature/x"},
    ]
    assert merged.structuredContent["meta"] == {"tool": "t", "project": "backend", "branch": "main"}


def test_ac7_named_pinned_branch_narrows_shared_names_fan_out_unpinned_names_are_ignored(caplog):
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"), ScopeCell("backend", "feature/x")))
    handler = RecordingHandler()
    with active(pin, RUNTIME_U1):
        call("get_symbol", {"target": "x", "branch": "feature/x"}, handler)
    assert handler.sent == [{"target": "x", "project": "backend", "branch": "feature/x"}]

    shared = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("a", "main"), ScopeCell("b", "main")))
    handler = RecordingHandler()
    with active(shared, RUNTIME_U1):
        call("get_symbol", {"target": "x", "branch": "main"}, handler)
    assert [a["project"] for a in handler.sent] == ["a", "b"]

    handler = RecordingHandler()
    with active(pin, RUNTIME_U1), caplog.at_level("INFO"):
        call("get_symbol", {"target": "x", "branch": "release"}, handler)
    assert [a["branch"] for a in handler.sent] == ["main", "feature/x"]
    assert json.loads(caplog.records[-1].getMessage())["event"] == "scope_pin_branch_ignored"


def test_pinned_project_narrows_to_its_cells():
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"), ScopeCell("tooling", "main")))
    handler = RecordingHandler()
    with active(pin, RUNTIME_U1):
        call("get_overview", {"project": "tooling"}, handler)
    assert handler.sent == [{"project": "tooling", "branch": "main"}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_scope_interceptor.py -q -k "ac2b or agent_chosen or union_request or ac5 or ac6 or ac7 or pinned_project"`
Expected: FAIL — the U0 stub sends no `branch` (`test_ac2b_...`, `test_ac5_...`, `test_ac6_...`, `test_ac7_...`).

- [ ] **Step 3: Replace `_default_branch` with the full body**

```python
def _default_branch(
    tool: str, args: dict[str, Any], scope: QuestionScope, project: str, runtime: ScopeRuntime
) -> tuple[str, BranchOrigin, bool]:
    """(branch sent, origin, replaced?) — UI spec §6.3 row ``branch``.

    Not advertised: nothing is sent and a stray model argument is dropped.
    A model-passed name the listing has for the effective project is kept
    (AGENT_CHOSEN, even when it equals the default); an unknown name is
    replaced by the resolved default (E2) and logged; an omitted branch gets
    the resolved default when non-empty, else nothing (SERVER).
    """
    if not runtime.capabilities.branch_selector:
        args.pop("branch", None)
        return "", BranchOrigin.SERVER, False
    passed = str(args.get("branch") or "")
    if passed and runtime.listing.has_branch(project, passed):
        return passed, BranchOrigin.AGENT_CHOSEN, False
    resolved = resolve_default_branch(scope, project, runtime.listing)
    if passed:
        log_scope_event(
            "scope_default_replaced", tool=tool, argument="branch", passed=passed, replacement=resolved
        )
    if resolved:
        args["branch"] = resolved
        return resolved, BranchOrigin.DEFAULT, bool(passed)
    args.pop("branch", None)
    return "", BranchOrigin.SERVER, bool(passed)
```

- [ ] **Step 4: Run the whole interceptor suite**

Run: `pytest tests/harness/ask_your_docs/test_scope_interceptor.py -q`
Expected: PASS (U0 tests unchanged: with `NO_SCOPE_CAPABILITIES` the first branch of the new body is the old stub).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/scope_interceptor.py tests/harness/ask_your_docs/test_scope_interceptor.py
git commit -m "ask-your-docs: interceptor branch rules — base-branch default, replacement, pinned-cell targeting"
```

---

### Task 13: Prompt rule 7 and the catalog branch segment, gated on the `branch` capability

**Files:**
- Modify: `python/pydocs_mcp/harness/core/prompts/system_v1.j2`
- Test: `tests/harness/ask_your_docs/test_prompt_seam.py`, `tests/harness/ask_your_docs/test_prompt_seed_parity.py`, `tests/harness/ask_your_docs/test_prompts_package.py`

**Interfaces:**
- Consumes: `_assemble_prompt(scope_capabilities=, branches=)` and `_resolved_system_prompt` (Task 7); `render_catalog(branches=, show_merged=)` (Task 3).
- Produces: the template variable `branch_selector_advertised` (guarded by `is defined`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/harness/ask_your_docs/test_prompt_seam.py` (inside `TestBranchGating`):

```python
    _RULE_7 = (
        '7. Every tool takes a "branch" argument; the indexed-projects list below\n'
        "   names each project's branches and marks the default. Leave \"branch\" empty\n"
        "   to answer from the default branch, or from the pinned branches when the\n"
        "   question carries a pin (the app applies the pin for you). Under a pin with\n"
        "   several branches, an empty \"branch\" returns one labeled result per branch\n"
        "   — best when the user is comparing branches; pass branch=<name> to read one\n"
        "   branch at a time when only one is relevant or the output is long — under\n"
        "   a pin, <name> must be one of the pinned branches; any other name is\n"
        "   answered from all pinned branches. A pinned-scope note may also list\n"
        "   branches and a slice; the app applies those too. Always say which branch\n"
        "   each claim comes from.\n"
    )

    def test_rule_7_renders_only_when_advertised(self) -> None:
        """AC-12 (U1 half)."""
        advertised = render_shared("system_v1", branch_selector_advertised=True)
        assert advertised.endswith("widening it.\n" + self._RULE_7)
        assert advertised.startswith(SYSTEM_PROMPT.removesuffix("\n"))
        assert "7. Every tool" not in render_shared("system_v1")

    def test_catalog_branch_segment_rides_the_same_gate(self) -> None:
        from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities

        u1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)
        assembled = _assemble_prompt("text_react", _CATALOG, None, scope_capabilities=u1, branches=_LISTING)
        assert "- proj — branches: main (default) — dependency packages: pkg_a, pkg_b" in assembled
        assert self._RULE_7.rstrip("\n") in assembled
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_prompt_seam.py -q -k "rule_7 or rides_the_same_gate"`
Expected: FAIL — rule 7 absent.

- [ ] **Step 3: Edit the template**

In `python/pydocs_mcp/harness/core/prompts/system_v1.j2`, after the last line of rule 6 (`   widening it.`) and BEFORE the file's trailing blank line, insert exactly:

```
{% if branch_selector_advertised is defined and branch_selector_advertised %}
7. Every tool takes a "branch" argument; the indexed-projects list below
   names each project's branches and marks the default. Leave "branch" empty
   to answer from the default branch, or from the pinned branches when the
   question carries a pin (the app applies the pin for you). Under a pin with
   several branches, an empty "branch" returns one labeled result per branch
   — best when the user is comparing branches; pass branch=<name> to read one
   branch at a time when only one is relevant or the output is long — under
   a pin, <name> must be one of the pinned branches; any other name is
   answered from all pinned branches. A pinned-scope note may also list
   branches and a slice; the app applies those too. Always say which branch
   each claim comes from.
{% endif %}
```

Why the `is defined` guard: the loader uses `StrictUndefined`, so a bare `{% if branch_selector_advertised %}` would raise on the no-variable renders (`SYSTEM_PROMPT`, `render_shared("system_v1")`, `prompts_for(name).render("system_v1")`) and break the import of the prompts package. With `trim_blocks` / `lstrip_blocks` the two tag lines vanish, so the false branch renders today's bytes exactly (the AC-11 golden proves it).

- [ ] **Step 4: Run the prompt suites**

Run: `pytest tests/harness/ask_your_docs/test_prompt_seam.py tests/harness/ask_your_docs/test_prompt_seed_parity.py tests/harness/ask_your_docs/test_prompts_package.py tests/harness/ask_your_docs/test_prompt_freeze.py -q`
Expected: PASS — including `test_no_variable_render_matches_the_golden` (the pre-edit bytes) and the seed parity pin (no regeneration).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/harness/core/prompts/system_v1.j2 tests/harness/ask_your_docs/test_prompt_seam.py
git commit -m "ask-your-docs prompt: rule 7 + catalog branch listing behind the branch capability"
```

---

### Task 14: Graph compare overlay — branch-scoped reader methods, `graph_compare.py`, page wiring

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/bundle.py` (`branch_symbol_chunks`, `reference_rows(branch=)`)
- Create: `python/pydocs_mcp/harness/ask_your_docs/graph_compare.py`
- Modify: `python/pydocs_mcp/harness/ask_your_docs/pages/2_Graph.py`
- Modify: `tests/harness/ask_your_docs/test_graph_service.py` (`FakeBundleReader.reference_rows(branch=None)`)
- Test: `tests/harness/ask_your_docs/test_graph_compare.py`, `tests/harness/ask_your_docs/test_bundle_branches.py`

**Interfaces:**
- Consumes: `GraphService.modules()`, `type_of`, `is_test` (graph_service.py); `Node`, `Edge` (model.py); `GraphBranchSelection` (Task 9).
- Produces: `BundleReader.branch_symbol_chunks(branch) -> dict[str, int]`, `BundleReader.reference_rows(branch: str | None = None)`; `ChangeState {UNCHANGED, CHANGED, ADDED, REMOVED}`; `BranchGraphComparison(branch_a, branch_b, nodes, edges)` with `state_of(node_id)`, `edge_state(key)`, `counts()`; `compare_branch_graphs(reader, branch_a, branch_b, *, hide_tests=True)`; `changed_only(comparison)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/harness/ask_your_docs/test_bundle_branches.py`:

```python
def test_branch_symbol_chunks_maps_names_to_content_addressed_ids(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        markdown=[],
        branches=[("main", _HEAD, None, 1, "active", None), ("feature/x", _HEAD, "main", 0, "active", None)],
    )
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO chunks (id, package, module, title, text, origin, content_hash, qualified_name) "
            "VALUES (?, '__project__', 'mod_a', ?, 'body', 'python_symbol', '', ?)",
            [(10, "Foo", "mod_a.Foo"), (11, "Foo", "mod_a.Foo"), (12, "bar", "mod_a.bar")],
        )
        conn.executemany(
            "INSERT INTO branch_chunks (branch, chunk_id, source_path) VALUES (?, ?, 'mod_a.py')",
            [("main", 10), ("main", 12), ("feature/x", 11), ("feature/x", 12)],
        )
    reader = SqliteBundleReader(db)
    assert reader.branch_symbol_chunks("main") == {"mod_a.Foo": 10, "mod_a.bar": 12}
    assert reader.branch_symbol_chunks("feature/x") == {"mod_a.Foo": 11, "mod_a.bar": 12}
    assert reader.branch_symbol_chunks("nope") == {}


def test_reference_rows_are_branch_agnostic_on_v16(tmp_path):
    db = make_bundle(tmp_path / "demo_0123456789.db", refs=[("mod_a.Foo", "mod_b.bar", "calls")])
    reader = SqliteBundleReader(db)
    assert reader.reference_rows(branch="main") == reader.reference_rows()


def test_reference_rows_filter_by_branch_when_the_column_exists(tmp_path):
    db = make_bundle(tmp_path / "demo_0123456789.db")
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE node_references ADD COLUMN branch TEXT NOT NULL DEFAULT ''")
        conn.executemany(
            "INSERT INTO node_references VALUES ('__project__', ?, ?, ?, 'calls', ?)",
            [("a", "b", "x.b", "main"), ("a", "c", "x.c", "feature/x"), ("a", "d", "x.d", "")],
        )
    rows = SqliteBundleReader(db).reference_rows(branch="main")
    assert sorted(to for _, to, _ in rows) == ["x.b", "x.d"]
```

Create `tests/harness/ask_your_docs/test_graph_compare.py`:

```python
"""compare_branch_graphs over a fake reader — AC-16."""

from __future__ import annotations

from pydocs_mcp.harness.ask_your_docs.graph_compare import (
    ChangeState,
    changed_only,
    compare_branch_graphs,
)

from .test_graph_service import FakeBundleReader


class FakeBranchReader(FakeBundleReader):
    def __init__(self, *, symbol_chunks, branch_refs, members=()):
        super().__init__(members=members, refs=[r for rows in branch_refs.values() for r in rows])
        self._symbol_chunks = symbol_chunks
        self._branch_refs = branch_refs

    def branch_symbol_chunks(self, branch):
        return dict(self._symbol_chunks.get(branch, {}))

    def reference_rows(self, branch=None):
        if branch is None:
            return list(self._refs)
        return list(self._branch_refs.get(branch, []))


def _reader() -> FakeBranchReader:
    return FakeBranchReader(
        members=[("mod_a", "Foo", "class"), ("mod_a", "bar", "def"), ("mod_b", "baz", "def"), ("tests.test_x", "t", "def")],
        symbol_chunks={
            "main": {"mod_a.Foo": 1, "mod_a.bar": 2, "mod_b.gone": 3, "tests.test_x.t": 9},
            "feature": {"mod_a.Foo": 1, "mod_a.bar": 5, "mod_b.baz": 4, "tests.test_x.t": 9},
        },
        branch_refs={
            "main": [("mod_a.Foo", "mod_a.bar", "calls"), ("mod_a.Foo", "mod_b.gone", "calls")],
            "feature": [("mod_a.Foo", "mod_a.bar", "calls"), ("mod_a.Foo", "mod_b.baz", "imports")],
        },
    )


def test_nodes_are_classified_by_chunk_identity():
    comparison = compare_branch_graphs(_reader(), "main", "feature")
    states = {node.id: state for node, state in comparison.nodes}
    assert states == {
        "mod_a.Foo": ChangeState.UNCHANGED,
        "mod_a.bar": ChangeState.CHANGED,
        "mod_b.baz": ChangeState.ADDED,
        "mod_b.gone": ChangeState.REMOVED,
    }
    assert comparison.state_of("mod_a.bar") is ChangeState.CHANGED
    assert comparison.state_of("nope") is None


def test_edges_are_compared_as_sets():
    comparison = compare_branch_graphs(_reader(), "main", "feature")
    edge_states = {(e.source, e.target, e.kind): s for e, s in comparison.edges}
    assert edge_states == {
        ("mod_a.Foo", "mod_a.bar", "calls"): ChangeState.UNCHANGED,
        ("mod_a.Foo", "mod_b.gone", "calls"): ChangeState.REMOVED,
        ("mod_a.Foo", "mod_b.baz", "imports"): ChangeState.ADDED,
    }


def test_changed_only_drops_every_unchanged_node_and_edge():
    filtered = changed_only(compare_branch_graphs(_reader(), "main", "feature"))
    assert ChangeState.UNCHANGED not in {s for _, s in filtered.nodes}
    assert ChangeState.UNCHANGED not in {s for _, s in filtered.edges}
    assert filtered.counts()[ChangeState.CHANGED] == 1


def test_test_symbols_are_hidden_by_default_and_kept_on_request():
    assert "tests.test_x.t" not in {n.id for n, _ in compare_branch_graphs(_reader(), "main", "feature").nodes}
    shown = compare_branch_graphs(_reader(), "main", "feature", hide_tests=False)
    assert "tests.test_x.t" in {n.id for n, _ in shown.nodes}
```

Change `FakeBundleReader.reference_rows` in `test_graph_service.py` to `def reference_rows(self, branch=None): return list(self._refs)`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_graph_compare.py tests/harness/ask_your_docs/test_bundle_branches.py -q`
Expected: FAIL — `ModuleNotFoundError: graph_compare`; `AttributeError: branch_symbol_chunks`.

- [ ] **Step 3: Add the reader methods**

In `bundle.py`, the Protocol gains (and `reference_rows` gains its parameter):

```python
    def reference_rows(self, branch: str | None = None) -> list[tuple[str, str | None, str]]:
        """Every ``(from_node_id, to_node_id, kind)`` edge; ``branch`` narrows to
        that branch's rows (plus branch-less rows) once schema v17 stamps them —
        on v16 the edges are branch-agnostic and ``branch`` is ignored."""
        ...

    def branch_symbol_chunks(self, branch: str) -> dict[str, int]:
        """``qualified_name -> chunk id`` for the branch's own-code tree chunks.
        Chunks are content-addressed, so an edited symbol maps to a NEW id —
        the compare overlay's change signal."""
        ...
```

`SqliteBundleReader`:

```python
    def reference_rows(self, branch: str | None = None) -> list[tuple[str, str | None, str]]:
        sql = "SELECT from_node_id, to_node_id, kind FROM node_references WHERE from_package=?"
        with self._conn() as conn:
            if branch is None or "branch" not in self._columns(conn, "node_references"):
                return conn.execute(sql, (_OWN,)).fetchall()  # v16: branch-agnostic
            return conn.execute(f"{sql} AND branch IN (?, '')", (_OWN, branch)).fetchall()  # noqa: S608 — literal SQL

    def branch_symbol_chunks(self, branch: str) -> dict[str, int]:
        with self._conn() as conn:
            if not self._columns(conn, "branch_chunks"):
                return {}
            rows = conn.execute(
                "SELECT c.qualified_name, MIN(c.id) FROM branch_chunks bc "
                "JOIN chunks c ON c.id = bc.chunk_id "
                "WHERE bc.branch = ? AND bc.slice = 'tree' AND c.package = ? "
                "AND c.qualified_name != '' GROUP BY c.qualified_name",
                (branch, _OWN),
            ).fetchall()
        return {str(name): int(chunk_id) for name, chunk_id in rows}
```

- [ ] **Step 4: Create `graph_compare.py`**

```python
"""Branch-vs-branch comparison for the graph explorer (UI spec §6.11, R8).

A symbol present on both branches with the same chunk id is UNCHANGED;
different ids mean CHANGED (chunks are content-addressed per blob); present
on one side only is ADDED / REMOVED relative to ``branch_a``. Edges are
compared as sets of (source, target, kind). No SQL, no Streamlit.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from pydocs_mcp.harness.ask_your_docs.bundle import BundleReader
from pydocs_mcp.harness.ask_your_docs.graph_service import GraphService, is_test, type_of
from pydocs_mcp.harness.ask_your_docs.model import Edge, Node


class ChangeState(StrEnum):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    ADDED = "added"
    REMOVED = "removed"


EdgeKey = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class BranchGraphComparison:
    branch_a: str
    branch_b: str
    nodes: tuple[tuple[Node, ChangeState], ...]
    edges: tuple[tuple[Edge, ChangeState], ...]

    def state_of(self, node_id: str) -> ChangeState | None:
        return next((state for node, state in self.nodes if node.id == node_id), None)

    def edge_state(self, key: EdgeKey) -> ChangeState | None:
        return next((s for e, s in self.edges if (e.source, e.target, e.kind) == key), None)

    def counts(self) -> dict[ChangeState, int]:
        return {state: sum(1 for _, s in self.nodes if s is state) for state in ChangeState}


def _node_state(chunk_a: int | None, chunk_b: int | None) -> ChangeState:
    if chunk_a is None:
        return ChangeState.ADDED
    if chunk_b is None:
        return ChangeState.REMOVED
    return ChangeState.UNCHANGED if chunk_a == chunk_b else ChangeState.CHANGED


def _edge_keys(rows: Iterable[tuple[str, str | None, str]]) -> set[EdgeKey]:
    return {(a, b, kind) for a, b, kind in rows if a and b}


def _edge_states(keys_a: set[EdgeKey], keys_b: set[EdgeKey]) -> dict[EdgeKey, ChangeState]:
    states = {key: ChangeState.UNCHANGED for key in keys_a & keys_b}
    states.update({key: ChangeState.ADDED for key in keys_b - keys_a})
    states.update({key: ChangeState.REMOVED for key in keys_a - keys_b})
    return states


def compare_branch_graphs(
    reader: BundleReader, branch_a: str, branch_b: str, *, hide_tests: bool = True
) -> BranchGraphComparison:
    """Classify every symbol and edge of the two branches (sorted by id)."""
    symbols_a = reader.branch_symbol_chunks(branch_a)
    symbols_b = reader.branch_symbol_chunks(branch_b)
    modules = GraphService(reader, hide_tests=hide_tests).modules()
    names = sorted(set(symbols_a) | set(symbols_b))
    nodes = tuple(
        (Node(name, name.rsplit(".", 1)[-1], type_of(name, modules)), _node_state(symbols_a.get(name), symbols_b.get(name)))
        for name in names
        if not (hide_tests and is_test(name))
    )
    states = _edge_states(_edge_keys(reader.reference_rows(branch=branch_a)), _edge_keys(reader.reference_rows(branch=branch_b)))
    edges = tuple((Edge(*key), state) for key, state in sorted(states.items()))
    return BranchGraphComparison(branch_a, branch_b, nodes, edges)


def changed_only(comparison: BranchGraphComparison) -> BranchGraphComparison:
    """The "changed only" toggle: drop every UNCHANGED node and edge."""
    return BranchGraphComparison(
        comparison.branch_a,
        comparison.branch_b,
        tuple((n, s) for n, s in comparison.nodes if s is not ChangeState.UNCHANGED),
        tuple((e, s) for e, s in comparison.edges if s is not ChangeState.UNCHANGED),
    )


__all__ = ("BranchGraphComparison", "ChangeState", "changed_only", "compare_branch_graphs")
```

- [ ] **Step 5: Wire the overlay into the page**

In `2_Graph.py` add `from pydocs_mcp.harness.ask_your_docs.graph_compare import ChangeState, changed_only, compare_branch_graphs` and, next to `_EDGE_COLOR`, the overlay palette:

```python
# Compare overlay (UI spec §6.11): state colours override the type colours.
_STATE_COLOR = {ChangeState.CHANGED: "#EF9F27", ChangeState.ADDED: "#2A9D8F", ChangeState.REMOVED: "#D4537E"}
```

After the line `edges = tuple(e for e in svc.edges_for(ids, edge_kinds) if e.source in ids and e.target in ids)` insert:

```python
comparison = None
if selection.compare_with:
    comparison = compare_branch_graphs(
        svc.reader, selection.branch, selection.compare_with, hide_tests=hide_tests
    )
    if selection.changed_only:
        comparison = changed_only(comparison)
        changed_ids = {node.id for node, _ in comparison.nodes}
        kids = tuple(n for n in kids if n.id in changed_ids)
        ids = {n.id for n in kids}
        edges = tuple(e for e in edges if e.source in ids and e.target in ids)
    counts = comparison.counts()
    st.caption(
        f"compare {selection.branch} → {selection.compare_with}: "
        f"{counts[ChangeState.CHANGED]} changed · {counts[ChangeState.ADDED]} added · "
        f"{counts[ChangeState.REMOVED]} removed"
    )


def _node_color(node_id: str, node_type: str) -> str:
    state = comparison.state_of(node_id) if comparison else None
    return _STATE_COLOR.get(state, _TYPE_STYLE.get(node_type, ("dot", "#8A97A6", ""))[1])


def _edge_color(edge) -> str:
    state = comparison.edge_state((edge.source, edge.target, edge.kind)) if comparison else None
    return _STATE_COLOR.get(state, _EDGE_COLOR.get(edge.kind, "#8A97A6"))
```

and replace the `color=` arguments of the `ANode(...)` and `AEdge(...)` constructions with `color=_node_color(n.id, n.node_type)` and `color=_edge_color(e)`.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/harness/ask_your_docs/test_graph_compare.py tests/harness/ask_your_docs/test_bundle_branches.py tests/harness/ask_your_docs/test_graph_service.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/bundle.py python/pydocs_mcp/harness/ask_your_docs/graph_compare.py python/pydocs_mcp/harness/ask_your_docs/pages/2_Graph.py tests/harness/ask_your_docs/test_graph_compare.py tests/harness/ask_your_docs/test_bundle_branches.py tests/harness/ask_your_docs/test_graph_service.py
git commit -m "ask-your-docs graph: branch compare overlay over content-addressed chunk ids"
```

---

### Task 15: U1 activation — docs, gates, and the live check against a P1 server

**Files:**
- Modify: `examples/harness/ask_your_docs_agent/README.md` (one sentence on branch pins and the compare overlay), `CHANGELOG.md`

- [ ] **Step 1: Docs**

Append to the README's scope paragraph: `On a server that indexes several branches (see the multi-branch design), the panel and the popover list each project's branches, the footer names the branch and commit each answer came from, the "compare with <base>" and "pin <branch>" chips appear under answers, and the graph page's **Compare with** selector colours symbols and edges by change state (changed / added / removed) with a **changed only** toggle.` Add a CHANGELOG bullet: `ask-your-docs: branch pins, the base-branch default, rule 7 of the system prompt, and the graph compare overlay activate on servers that advertise the branch selector.`

- [ ] **Step 2: Gates**

```bash
ruff format python/ tests/ && ruff check python/ tests/ && mypy python/pydocs_mcp && complexipy python/pydocs_mcp --max-complexity-allowed 15 && vulture python/pydocs_mcp --min-confidence 80 && pytest tests/ --ignore=tests/test_parity.py -q && uv lock --check
git checkout -- complexipy-snapshot.json
```

Expected: green.

- [ ] **Step 3: Live check (manual, against a P1 build)**

Index a repository on two branches (`pydocs-mcp index . && pydocs-mcp index . --branch feature/x`, the P1 CLI), start `harness-ask-your-docs --workspace ~/pydocs-index`, and verify: the popover shows a Branches multiselect; pinning two branches and asking a question yields one labeled section per branch and a footer with two segments; the "compare with main" chip appears under a feature-branch answer; the graph page's Compare with colours changed symbols. Record the outcome in the PR description.

- [ ] **Step 4: Commit and open the U1 PR**

```bash
git add examples/harness/ask_your_docs_agent/README.md CHANGELOG.md
git commit -m "ask-your-docs: U1 docs — branch pins, base default, compare overlay"
```

Gate: AC-2b, 5, 6, 7, 8, 9, 12 (U1 half), 16, 17 (compare / pin), 31 (COMPARE_WITH / PIN_BRANCH) green.

---

# Stage U2 — after multi-branch P2 (`changed` / `diff` scope values, landing units)

Precondition: the P2 plan's Task 7 (the scope-values contract PR) and P2.8 (the landing-unit index) have landed.

### Task 16: Slice injection in the interceptor

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/scope_interceptor.py` (`_scope_argument_value`, new `_slice_advertised`)
- Test: `tests/harness/ask_your_docs/test_scope_interceptor.py`

**Interfaces:**
- Consumes: `SLICE_SERVER_VALUES`, `ScopeSlice` (Task 4); `ScopeCapabilities.changed_slice / diff_slice` (Task 5); `SLICE_TOOLS` (Task 6).
- Produces: `_scope_argument_value(tool, scope, capabilities) -> str | None` with the slice-aware body; `_slice_advertised(capabilities, slice_value) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/harness/ask_your_docs/test_scope_interceptor.py`:

```python
from pydocs_mcp.harness.ask_your_docs.question_scope import ScopeSlice

SLICED = ScopeCapabilities(branch_selector=True, changed_slice=True, diff_slice=True)
RUNTIME_U2 = ScopeRuntime(listing=LISTING, capabilities=SLICED, max_cells=4)


def test_default_slice_is_injected_on_search_and_grep_only_when_advertised():
    scope = _default(slice=ScopeSlice.DIFF_HUNKS)
    handler = RecordingHandler()
    with active(scope, RUNTIME_U2):
        call("search_codebase", {"query": "q"}, handler)
        call("grep", {"pattern": "p"}, handler)
        call("get_symbol", {"target": "x"}, handler)
        call("search_codebase", {"query": "q", "scope": "deps"}, handler)  # model-passed: kept
    with active(scope, RUNTIME_U1):  # not advertised: nothing
        call("search_codebase", {"query": "q"}, handler)
    assert handler.sent == [
        {"query": "q", "scope": "diff"},
        {"pattern": "p", "scope": "diff"},
        {"target": "x"},
        {"query": "q", "scope": "deps"},
        {"query": "q"},
    ]


def test_default_slice_wins_over_the_code_value_and_changed_needs_its_own_capability():
    scope = _default(slice=ScopeSlice.CHANGED_FILES, code=ScopeCode.OWN)
    handler = RecordingHandler()
    with active(scope, RUNTIME_U2):
        call("search_codebase", {"query": "q"}, handler)
    only_diff = ScopeRuntime(listing=LISTING, capabilities=ScopeCapabilities(True, False, True), max_cells=4)
    with active(scope, only_diff):
        call("search_codebase", {"query": "q"}, handler)  # changed not advertised → the code value
    assert handler.sent == [{"query": "q", "scope": "changed"}, {"query": "q", "scope": "project"}]


def test_pin_slice_overwrites_and_is_observed():
    observations = ScopeObservations()
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"),), slice=ScopeSlice.DIFF_HUNKS)
    handler = RecordingHandler()
    with active(pin, RUNTIME_U2, observations):
        call("search_codebase", {"query": "q", "scope": "all"}, handler)
        call("grep", {"pattern": "p", "scope": "project"}, handler)
        call("get_overview", {}, handler)
    assert [a.get("scope") for a in handler.sent] == ["diff", "diff", None]
    assert [r.slice for r in observations.records()] == [
        ScopeSlice.DIFF_HUNKS,
        ScopeSlice.DIFF_HUNKS,
        ScopeSlice.WHOLE_BRANCH,
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_scope_interceptor.py -q -k slice`
Expected: FAIL — no `scope` injected for the slice.

- [ ] **Step 3: Replace `_scope_argument_value`**

```python
def _slice_advertised(capabilities: ScopeCapabilities, slice_value: ScopeSlice) -> bool:
    if slice_value is ScopeSlice.CHANGED_FILES:
        return capabilities.changed_slice
    return slice_value is ScopeSlice.DIFF_HUNKS and capabilities.diff_slice


def _scope_argument_value(
    tool: str, scope: QuestionScope, capabilities: ScopeCapabilities
) -> str | None:
    """The ``scope`` value the scope implies for ``tool``: the slice on the
    two slice tools when advertised (it implies own code — spec §6.5
    ``all ⊃ project ⊃ changed``), else the code filter on search_codebase."""
    if (
        tool in SLICE_TOOLS
        and scope.slice is not ScopeSlice.WHOLE_BRANCH
        and _slice_advertised(capabilities, scope.slice)
    ):
        return SLICE_SERVER_VALUES[scope.slice]
    if tool == "search_codebase" and scope.code is not ScopeCode.ALL:
        return CODE_SERVER_VALUES[scope.code]
    return None
```

and add `SLICE_SERVER_VALUES` to the `question_scope` import of `scope_interceptor.py`.

- [ ] **Step 4: Run the suite**

Run: `pytest tests/harness/ask_your_docs/test_scope_interceptor.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/scope_interceptor.py tests/harness/ask_your_docs/test_scope_interceptor.py
git commit -m "ask-your-docs: changed/diff slice injection on search_codebase and grep"
```

---

### Task 17: The "merged" picker group, the catalog tombstone marker, and `show the diff`

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/question_scope.py` (`branch_option_ids`, `cells_from_branch_selection`)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/scope_panel.py` (`_render_pin_branches`, `_apply_pin`, `_branch_option_labels`)
- Test: `tests/harness/ask_your_docs/test_question_scope.py`, `tests/harness/ask_your_docs/test_prompt_seam.py`, `tests/harness/ask_your_docs/test_app_scope_states.py`

**Interfaces:**
- Consumes: `WorkspaceBranchListing.merged()` (Task 3); `render_catalog(show_merged=)` (Task 3, wired through `_assemble_prompt` in Task 7); the `SHOW_DIFF` chip (Task 8).
- Produces: `branch_option_ids(project, listing, capabilities) -> dict[str, str]` (option id → label: live names as `name:<branch>`, tombstones as `merged:<branch>` labeled `feature/old (merged into main @3e1a9c2)`); `cells_from_branch_selection(project, option_ids, listing) -> tuple[tuple[ScopeCell, ...], bool]` (the cells and whether a landing sha forces `DIFF_HUNKS`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/harness/ask_your_docs/test_question_scope.py`:

```python
class TestMergedGroup:  # AC-32 (pure half)
    _SHA = "3e1a9c2" + "0" * 33
    _LISTING = WorkspaceBranchListing(
        projects={
            "backend": (
                _row("main", default=True),
                _row("feature/retry", base="main"),
                IndexedBranch("feature/old", "a" * 40, "main", False, BranchStatus.MERGED, _SHA, None, 1.0),
            )
        }
    )

    def test_option_ids_list_live_names_then_the_merged_group_on_u2_only(self):
        from pydocs_mcp.harness.ask_your_docs.question_scope import branch_option_ids
        from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities

        u1 = ScopeCapabilities(True, False, False)
        u2 = ScopeCapabilities(True, True, True)
        assert list(branch_option_ids("backend", self._LISTING, u1)) == ["name:main", "name:feature/retry"]
        ids = branch_option_ids("backend", self._LISTING, u2)
        assert list(ids) == ["name:main", "name:feature/retry", "merged:feature/old"]
        assert ids["merged:feature/old"] == "feature/old (merged into main @3e1a9c2)"

    def test_selecting_a_tombstone_pins_the_landing_sha_with_diff_forced(self):
        from pydocs_mcp.harness.ask_your_docs.question_scope import cells_from_branch_selection

        cells, forced = cells_from_branch_selection("backend", ("name:main", "merged:feature/old"), self._LISTING)
        assert cells == (ScopeCell("backend", "main"), ScopeCell("backend", self._SHA))
        assert forced is True
        cells, forced = cells_from_branch_selection("backend", ("name:main",), self._LISTING)
        assert cells == (ScopeCell("backend", "main"),) and forced is False
        assert cells_from_branch_selection("backend", (), self._LISTING) == ((ScopeCell("backend", ""),), False)
```

Append to `tests/harness/ask_your_docs/test_prompt_seam.py` (`TestBranchGating`):

```python
    def test_tombstone_marker_needs_the_diff_capability(self) -> None:
        """AC-12 (U2 half)."""
        from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities

        sha = "3e1a9c2" + "0" * 33
        listing = WorkspaceBranchListing(
            projects={
                "proj": (
                    IndexedBranch("main", "a" * 40, None, True, BranchStatus.ACTIVE, None, None, 1.0),
                    IndexedBranch("feature/old", "a" * 40, "main", False, BranchStatus.MERGED, sha, None, 1.0),
                )
            }
        )
        u1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)
        u2 = ScopeCapabilities(branch_selector=True, changed_slice=True, diff_slice=True)
        assert "merged into" not in _assemble_prompt("text_react", _CATALOG, None, scope_capabilities=u1, branches=listing)
        assert "feature/old (merged into main @3e1a9c2)" in _assemble_prompt(
            "text_react", _CATALOG, None, scope_capabilities=u2, branches=listing
        )
```

Append to `tests/harness/ask_your_docs/test_app_scope_states.py`:

```python
def test_merged_group_and_slice_controls_on_u2(tmp_path, monkeypatch):
    """AC-32 (page half) + AC-20 (slice half)."""
    sha = "3e1a9c2" + "0" * 33
    make_bundle(
        tmp_path / "demo_0123456789.db",
        branches=[
            ("main", _HEAD, None, 1, "active", None),
            ("feature/old", _HEAD, "main", 0, "merged", sha),
        ],
    )
    monkeypatch.setenv("PYDOCS_WORKSPACE", str(tmp_path))
    at = _app(ScopeCapabilities(branch_selector=True, changed_slice=True, diff_slice=True))
    at.session_state["scope_defaults_open"] = True
    at.run()
    assert not at.exception, at.exception
    branches = at.multiselect(key="scope_pin_branches")
    assert branches.options == ["name:main", "merged:feature/old"]
    assert any(r.key == "scope_defaults_slice" for r in at.radio)
    assert any(r.key == "scope_pin_slice" for r in at.radio)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/harness/ask_your_docs/test_question_scope.py tests/harness/ask_your_docs/test_prompt_seam.py -q -k "Merged or tombstone"`
Expected: FAIL — `ImportError: branch_option_ids`; the prompt test passes already (Task 7 wired `show_merged`) — keep it as the pin.

- [ ] **Step 3: Add the pure helpers to `question_scope.py`**

(Import `ScopeCapabilities` from `scope_capabilities` — that module imports nothing from `question_scope`, so no cycle.)

```python
NAME_OPTION_PREFIX = "name:"
MERGED_OPTION_PREFIX = "merged:"


def branch_option_ids(
    project: str, listing: WorkspaceBranchListing, capabilities: ScopeCapabilities
) -> dict[str, str]:
    """Picker option id -> label: live names, then (on U2) the merged group,
    labeled ``feature/old (merged into main @3e1a9c2)`` — ``merged_into`` is
    the landing sha, never a branch name (multi-branch spec §6.8a)."""
    options = {f"{NAME_OPTION_PREFIX}{r.name}": r.name for r in listing.pickable(project)}
    if not capabilities.diff_slice:
        return options
    default = listing.default_row(project)
    for row in listing.merged(project):
        base = row.base_name or (default.name if default else "base")
        options[f"{MERGED_OPTION_PREFIX}{row.name}"] = (
            f"{row.name} (merged into {base} @{str(row.merged_into)[:7]})"
        )
    return options


def cells_from_branch_selection(
    project: str, option_ids: Sequence[str], listing: WorkspaceBranchListing
) -> tuple[tuple[ScopeCell, ...], bool]:
    """(cells, diff_forced): a merged entry pins ``(project, landing sha)`` —
    the retired NAME is never sent — and forces the diff-hunks slice, because
    a landing unit answers ``scope=diff`` only (multi-branch spec §6.5b)."""
    cells: list[ScopeCell] = []
    forced = False
    for option in option_ids:
        if option.startswith(MERGED_OPTION_PREFIX):
            row = listing.row(project, option.removeprefix(MERGED_OPTION_PREFIX))
            if row is not None and row.merged_into:
                cells.append(ScopeCell(project, str(row.merged_into)))
                forced = True
        elif option.startswith(NAME_OPTION_PREFIX):
            cells.append(ScopeCell(project, option.removeprefix(NAME_OPTION_PREFIX)))
    return (_ordered_unique(cells) or (ScopeCell(project, ""),)), forced
```

Add both names (and the two prefixes) to `__all__`.

- [ ] **Step 4: Wire the popover and the panel**

In `scope_panel.py`, replace `_render_pin_branches` and `_apply_pin`:

```python
def _render_pin_branches(
    project: str, listing: WorkspaceBranchListing, capabilities: ScopeCapabilities
) -> tuple[str, ...]:
    if not capabilities.branch_selector:
        st.caption(_branch_caption(project, listing))
        return ()
    labels = branch_option_ids(project, listing, capabilities)
    # A closed list by construction: st.multiselect accepts no free text (R6).
    return tuple(st.multiselect("Branches", list(labels), format_func=labels.get, key="scope_pin_branches"))


def _apply_pin(
    project: str,
    option_ids: tuple[str, ...],
    slice_value: ScopeSlice,
    defaults: QuestionScope,
    listing: WorkspaceBranchListing,
) -> None:
    # on_click callback: runs before the rerun, so the popover's own key is writable.
    cells, diff_forced = cells_from_branch_selection(project, option_ids, listing)
    chosen_slice = ScopeSlice.DIFF_HUNKS if diff_forced else slice_value
    st.session_state["scope_pin"] = QuestionScope(
        kind=ScopeKind.PIN,
        cells=cells,
        slice=chosen_slice,
        code=code_compatible_with_slice(chosen_slice, defaults.code),
        package=defaults.package,
    )
    st.session_state["scope_pin_popover"] = False
```

update the Pin button's `args=(project, branches, slice_value, defaults, listing)`, the `count = max(len(branches), 1)` line stays, and import `MERGED_OPTION_PREFIX, branch_option_ids, cells_from_branch_selection` from `question_scope`. In `_branch_option_labels` (the defaults panel), replace the `labels.update({f"{_NAME_PREFIX}{r.name}": r.name for r in listing.pickable(project)})` line with `labels.update(branch_option_ids(project, listing, capabilities))` and thread `capabilities` into `_branch_option_labels(project, listing, capabilities)`; in `_render_branch_default_row` a picked `merged:` entry maps to `(ScopeBranchDefault.BASE, "")` (a tombstone is never a *default*; only a pin can name a landing sha):

```python
    if picked.startswith(MERGED_OPTION_PREFIX):
        return ScopeBranchDefault.BASE, ""
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/harness/ask_your_docs/test_question_scope.py tests/harness/ask_your_docs/test_prompt_seam.py tests/harness/ask_your_docs/test_answer_footer.py -q` and `/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest tests/harness/ask_your_docs/test_app_scope_states.py -q`
Expected: PASS (the `show the diff` chip's AC-17 / AC-31 tests from Task 8 are its activation pins).

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/question_scope.py python/pydocs_mcp/harness/ask_your_docs/scope_panel.py tests/harness/ask_your_docs/test_question_scope.py tests/harness/ask_your_docs/test_prompt_seam.py tests/harness/ask_your_docs/test_app_scope_states.py
git commit -m "ask-your-docs: merged picker group pins landing shas with scope=diff; tombstone catalog marker"
```

---

### Task 18: U2 activation — E9 display, docs, gates

**Files:**
- Modify: `examples/harness/ask_your_docs_agent/README.md`, `CHANGELOG.md`

- [ ] **Step 1: E9 is already the transcript's behavior — verify it**

A landing unit outside the retention window makes the server return an `InvalidArgumentError` naming `git.diff_chunks.retain` and the `branches pin` command; the adapter renders it as an error tool message, the agent reports it, and the footer shows the cell with no sha. Verify manually against a P2 server with `git.diff_chunks.retain.landings: 1` and a two-landing history: the popover's merged entry for the older landing produces a visible error in the answer, and the pin chip stays so the user can clear it. No code change; record the outcome in the PR description.

- [ ] **Step 2: Docs**

README, append to the scope paragraph: `On servers that index change slices, the panel and the popover offer a **Slice** (whole branch / changed files / diff hunks), merged branches appear in a "merged" group that pins their landing commit with the diff-hunks slice, and a "show the diff" chip follows answers.` CHANGELOG bullet: `ask-your-docs: slice controls, the merged picker group (landing shas with scope=diff) and the "show the diff" chip activate on servers that advertise the changed / diff scope values.`

- [ ] **Step 3: Gates**

```bash
ruff format python/ tests/ && ruff check python/ tests/ && mypy python/pydocs_mcp && complexipy python/pydocs_mcp --max-complexity-allowed 15 && vulture python/pydocs_mcp --min-confidence 80 && pytest tests/ --ignore=tests/test_parity.py -q && uv lock --check
git checkout -- complexipy-snapshot.json
pytest tests/test_doc_conformance.py -q
```

Expected: green.

- [ ] **Step 4: Commit and open the U2 PR**

```bash
git add examples/harness/ask_your_docs_agent/README.md CHANGELOG.md
git commit -m "ask-your-docs: U2 docs — slices, merged group, show the diff"
```

Gate: AC-12 (U2 half), AC-17 (`show the diff`), AC-20 (slice half), AC-31 (`SHOW_DIFF`), AC-32 green; the E9 manual check recorded.

---

## Deviations from the spec (recorded, not silent)

| # | Spec says | Plan does | Why |
|---|---|---|---|
| D1 | `ScopeSlice`, `ScopeCode`, `ScopeBranchDefault` live in `question_scope.py` (§6.2) | they live in `retrieval/config/ask_your_docs_models.py` and are re-exported from `question_scope.py` under the same names | the config sub-model is mypy-checked while `harness/ask_your_docs/` is mypy-excluded; a checked module must not import an excluded one |
| D2 | the interceptor reads the listing and the capabilities (§6.3) without saying how they reach it | a frozen `ScopeRuntime(listing, capabilities, max_cells)` on a third contextvar, set by `ask(scope_runtime=)`; `EMPTY_SCOPE_RUNTIME` when absent | same isolation rationale as the scope contextvar; the eval binding never sets it |
| D3 | `render_answer_footer(observations)` (§6.8); `apply_follow_up_chip(chip, kept_pin)` (§6.9) | both take the listing / the defaults as explicit parameters | the footer needs the listing's sha and project count; a new pin needs the session defaults' slice / code / package |
| D4 | chips are a U1 deliverable (§6.12) | the chip code and its tests land in U0 (Task 8), dormant behind `ScopeCapabilities`; U1 / U2 activate them | §6.12 also says U1 / U2 code is written with U0 against fakes; one module, one PR |
| D5 | `render_scope_defaults_panel(config, listing, capabilities)` | also takes the catalog | the package pool comes from the catalog (today's picker rule) |
| D6 | idle popover label `""` with an icon (§6.10) | label `scope` with the icon | a readable label when the icon font is missing; the summary label replaces it as soon as a pin is active |
| D7 | merged text labels `## <project> · <branch>` (§6.4) | `## <project>` when the cell has no branch (U0 two-project pins) | there is no branch to print |
| D8 | the footer segment always carries a slice (§6.8) vs AC-34's `answered from demo · no branch · server default` | the slice is omitted when the branch is unknown | slices are branch-relative; both AC-18 and AC-34 hold |
| D9 | a replaced argument renders `agent-chosen → default` (§6.8) | `CellObservation.replaced: bool` next to the four-member `BranchOrigin` | keeps the enum at the four members the spec names |
| D10 | — | the chat page reads the capability record from the cached agent build at render time (the first render of a workspace starts the serve subprocess); tests seed `st.session_state["scope_capabilities"]` | the capability is only knowable from the advertised schemas; an AppTest must never spawn the server against a fixture bundle |
| D11 | AC-21 asserts the popover button label (`2 branches`); AC-21b asserts a one-shot pin is `None` after a send | the label is pinned by `pin_summary_label`'s unit test; the one-shot half by `snapshot_pin_for_send`'s unit test; the AppTests assert the chip row, the caption and "clear all" | AppTest exposes neither the popover label nor a send without an agent |
| D12 | `agent.py` target ≤ 468 lines | ≈ 487 lines (under the 500 gate) | the wrapper, the record and the extra keywords outweigh the removed dict code |
| D13 | `weave_attachments(attached: list[str], …)` becomes `AttachedSymbol`-typed (§6.10) | accepts both `AttachedSymbol` and plain strings | the existing tests and any session state seeded with strings keep working |
| D14 | — | `WorkspaceBranchListing.has_branch("", name)` answers "any project has it" | a model may name a branch on a union request; the server resolves per bundle |
| D15 | the popover closes by writing `False` to its key before `st.rerun()` (§6.10 / V2) | the write happens inside the Pin / Clear `on_click` callbacks | Streamlit refuses writes to a widget's key after the widget is instantiated in the same run; callbacks run before instantiation |
| D16 | the defaults panel's Branch selectbox lists the merged group (§6.10) | it lists it but a merged entry maps to the base default | only a pin can name a landing sha (spec §6.10: selecting one pins `(project, merged_into)` with `scope=diff`); a *default* cannot carry a forced slice |

## Spec coverage

| AC | Task | AC | Task | AC | Task |
|---|---|---|---|---|---|
| AC-1 | 6 | AC-12 (U1 / U2) | 13 / 17 | AC-23 | 4 |
| AC-2 | 6 | AC-13 | 3 | AC-24 | 7 |
| AC-2b | 12 | AC-14 | 2 | AC-25 | 11 |
| AC-3 | 6 | AC-14b | 2, 3 | AC-26 | 11, 15, 18 |
| AC-4 | 6 (code), 16 (slice) | AC-15 | 5 | AC-27 | 7 |
| AC-5 | 12 | AC-16 | 14 | AC-28 | 4 |
| AC-6 | 12 | AC-17 | 8 (U2 half activated by 17) | AC-29 | 4 |
| AC-6b | 6 | AC-18 | 8 | AC-30 | 4, 10 |
| AC-7 | 12 | AC-19 | 9 | AC-31 | 8 |
| AC-8 | 6 | AC-20 | 9, 17 | AC-32 | 17 |
| AC-9 | 6 | AC-21 | 9 (D11) | AC-33 | 4, 9 |
| AC-10 | 6 | AC-21b | 9 (D11) | AC-34 | 8 |
| AC-11 | 7 | AC-22 | 1 | E1–E12 | 6, 9, 12, 16, 17, 18 |

Spec sections → tasks: §6.1–§6.2 → 1, 4; §6.3 → 6, 12, 16; §6.4–§6.4a → 6, 12; §6.5 → 6, 7; §6.6 → 7, 13; §6.7 → 9; §6.8 → 8; §6.9 → 8, 9; §6.10 → 2, 3, 9, 17; §6.11 → 10, 14; §6.12 → 5, 7, 9; §6.13 → file map; §7 → 1; §8 → 6, 7, 13 (byte-identity tests); §9 → 6 (E1, E2, E4, E5), 9 (E7, E10, E11, E12), 2 (E8), 18 (E9); §11 → every test file above.

## Handoff

Three PRs against `main`, one per stage: **U0** (Tasks 1–11) is mergeable now; **U1** (Tasks 12–15) after the P1 plan's contract PR; **U2** (Tasks 16–18) after the P2 plan's contract PR and P2.8. Open decisions the owner must settle before U1 starts (spec §12): O1 (indexed-but-unpinned branch under a pin — the plan encodes the strict reading), O2 (merged-group sequencing and label — the plan encodes `feature/old (merged into main @3e1a9c2)` and gates it on `diff_slice`), O3 (`branch_default: base` — encoded), O4 (one held session for the app — not done; `max_cells` bounds the spawn), O5 (catalog branch listing gated on the `branch` capability — encoded), O6 (per-project freshness — the footer tooltip caveat stands).
