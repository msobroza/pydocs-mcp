# Ask-Your-Docs Branch Scope UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the ask-your-docs chat and graph pages branch-aware scoping — one always-visible "Searching in …" strip above the question with a "Where to search" picker (sticky, YAML-seeded), typed one-shot `in:<project>` / `on:<branch>` tokens, fan-out over `(project, branch)` cells with labeled results, a one-line answer footer in the user's words, follow-up buttons, and a graph compare overlay — while keeping the no-target path byte-identical to today and the MCP surface untouched. (Amended 2026-09-15 under owner decision D14: the 2026-09-04 interface — hidden soft defaults in a sidebar panel plus a composer popover for per-question pins — is replaced by the strip + picker + tokens; the engine is unchanged.)

**Architecture:** A frozen `QuestionScope` value object replaces the `ToolScope` dict; a new `scope_interceptor.py` applies it to every tool call through the existing `langchain-mcp-adapters` interceptor seam (defaults fill what the model left empty, pins overwrite and fan out); `ScopeObservations` carries per-call `meta` back to the page through in-place mutation of a container created in `ask()`; the strip's state compiles to the scope through one pure function (`compile_strip_scope`); capability gating (`ScopeCapabilities`, read from the advertised tool schemas) keeps every branch/slice control inactive until the server advertises `branch`, `changed` and `diff`. Four stages: **U0** (landed as draft PR #267), **U0r** (the D14 interface, replacing U0's before the PR leaves draft), **U1** (activated by multi-branch P1), **U2** (activated by P2).

**Tech Stack:** Python 3.11+, Streamlit ≥ 1.59 (`st.bottom`, keyed `st.popover`, `st.pills` for the U1 branch control — every U0r AppTest is verified on 1.59.1; the spec closes V7 and O8 and states the floor as `streamlit>=1.59` everywhere, so 1.59 is the only floor in play), langchain-mcp-adapters ≥ 0.3 (`MCPToolCallRequest.override`), `mcp.types.CallToolResult`, pydantic v2 config sub-models, SQLite read-only bundle readers, pytest + `streamlit.testing.v1.AppTest`.

**Spec:** `docs/superpowers/specs/2026-09-04-ask-your-docs-branch-scope-ui-design.md` (commit `34028eb`, **amended 2026-09-15** — §6.1, §6.7, §6.8, §6.9, §6.10, §6.10a, §6.12, §6.13, §7, §9, §10, §11, §12 and the Amendments section carry D14; the amended text wins wherever a U0 task below still quotes the 2026-09-04 wording). Section numbers below (§6.2, §6.3, …) refer to that document; "multi-branch spec" means `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md` (amended, commit `1c371bc`).

## Global Constraints

- **No MCP surface change** (§8): no new tool, parameter or envelope field; the harness only reads what the server advertises. Client-side merged results never reach the server.
- **Byte-identity** (§8, R7, R11): with the shipped YAML and no pin, on a server that does not advertise `branch`, every tool call carries exactly today's arguments, and the assembled system prompt is byte-identical (rule 7 and the catalog branch segment are gated on the `branch` capability). `SYSTEM_PROMPT` bytes never change in any stage (the eval seed parity pin `tests/harness/ask_your_docs/test_prompt_seed_parity.py` stays green without regeneration).
- **Eval binding untouched** (§8, AC-24): `binding.py` keeps importing `_intercept` and `serve_connection` from `agent.py`, keeps calling `build_agent(...)` and unpacking a 2-tuple, and never calls `ask()` — so the interceptor is a strict passthrough on that path.
- **Streamlit floor** `streamlit>=1.59` in the `[harness-ask-your-docs]` extra (what `pyproject.toml` carries on the branch, and the only version every U0r AppTest — `at.pills`, `at.checkbox`, the `st.bottom` block — is verified against) with the WHY comment `# WHY: st.bottom (chat composer row) + stateful st.popover (where-to-search picker) + st.pills`; no `_bottom` shim, no fallback shape (E10). Never relock to 1.57: the spec states the floor as `streamlit>=1.59` everywhere and closes O8 / V7 (`at.pills` is an AppTest accessor on the pinned floor, verified on the installed 1.59.1) — 1.59 is the working floor and the only one this plan targets.
- **Every tunable is YAML**: the `ask_your_docs.scope` block (§7) — nine keys `project, branch_default, branch_name, slice, code, package, max_cells, tokens_enabled, footer_hint`; the strip is a session override of YAML, never a second source of defaults; `max_cells` is read from config wherever it is shown on screen (the strip's "limit M", the picker's "N of M"), never re-encoded. No CLI flag, no MCP param; `in:` / `on:` are client-side text.
- **Widget keys and session keys (U0r)**: strip `scope_strip_only_these`, `scope_strip_clear`, `scope_chip_<project>_<branch>`; picker popover `scope_picker`, `scope_picker_project_<name>`, `scope_picker_branches_<name>` (U1), `scope_picker_code`, `scope_picker_package`, `scope_picker_files` (U2), `scope_picker_use`, `scope_picker_reset`; follow-ups `follow_up_<index>_<kind>`; graph page `graph_where_to_search`; attachments `chip_<symbol>`, `chip_clear`. Session keys `scope_strip: StripState` (targets, the user's own "Only these" tick, the "More" values), `scope_listing_workspace`, `scope_capabilities`, `attached`. The 2026-09-04 keys (`scope_defaults_*`, `scope_pin*`, `scope_chip_slice`, `scope_chip_project`) and today's `scope_project` / `scope_code` / `scope_package` are asserted absent by name (AC-49). Every strip / picker write is an `on_click` / `on_change` callback (P15); the one exception is row 2's forcing write of `scope_strip_only_these`, which precedes the checkbox's instantiation in the same run and never reaches `StripState.only_these`.
- **On-screen vocabulary (D14)**: no "pin", "keep for next", "Reset to shipped", "whole branch", "own code", "Scope defaults", no "scope" as a button label, never "default" / "stale" as bare origin words; the model-facing `[pinned scope: …]` note keeps its bytes (AC-28) through the `MODEL_NOTE_*` tables. Pinned by `test_scope_vocabulary.py` (source text, core deps) and `test_app_scope_vocabulary.py` (the rendered trees of both pages, AppTest) — AC-47.
- **Closed vocabularies are `StrEnum`s** with UPPER_SNAKE members; value objects are `@dataclass(frozen=True, slots=True)`; error messages carry the offending value and the expected shape; files under 500 lines (`agent.py` ≤ 500, target ≤ 468 — the target is not a gate); functions 4–20 lines; max two indentation levels.
- **Structured logs**: scope events are one JSON line each with named fields (`{"event": "scope_default_replaced", "tool": …, "argument": …, "passed": …, "replacement": …}`), emitted through `logging.getLogger("pydocs_mcp.harness.ask_your_docs.question_scope")`.
- **`harness/ask_your_docs/` is mypy-excluded and coverage-excluded** (pyproject `[tool.mypy] exclude`); therefore every enum a mypy-checked module needs (the config sub-model) lives in `retrieval/config/ask_your_docs_models.py` and is re-exported from `question_scope.py` under the spec's names.
- **Git authorship**: commits are authored by the repository's configured user only — no `Co-Authored-By` trailers, no `--author`, no signing flags.
- **Test venvs**: core-suite tests run from the worktree venv (`pytest tests/harness/ask_your_docs -q`); AppTest smoke tests need the `[harness-ask-your-docs]` extra, installed in the main checkout's venv (`/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest`) **with `PYTHONPATH=<worktree>/python -o pythonpath=<worktree>/python`**, or the main checkout's sources are imported. Every AppTest **seeds `st.session_state["scope_capabilities"]`** so the page never builds the agent (no MCP subprocess, no LLM client) during a test; a test that must SEND goes through the `_AgentStackSpy` pattern of `test_app_serve_session.py` (patched `build_agent` / `ask` / `reformulate`). Every AppTest goes through `_page_fixtures.page(**seeds)` / `graph_page(**seeds)`, never a raw `AppTest.from_file`; strip widgets sit in `st.bottom` (outside `at.main` / `at.sidebar`) and are asserted through the flat accessors.
- **Non-degenerate fixtures (the PR #267 lesson, commit `6d4da185`)**: a fixture must tell the intended rule from the plausible mutant. Two-project strips use projects that DISAGREE on default vs base; an ordering test uses a listing whose insertion order is neither alphabetical nor the strip's insertion order (`WorkspaceBranchListing.project_names` is dict order, and `_LISTING`'s `backend, tooling` is ALSO alphabetical — a sort-by-name mutant survives it); a single seeded target is never the listing's first row and never the YAML default project; a forced-checkbox test seeds the box OFF; the label-rule fixture carries a branch the sent arguments do not; the absence assertion lists the OLD keys; booleans are asserted with `is`; footer origin phrases are asserted as full segment strings.
- **Gates before every push**: `ruff format --check python/ tests/`, `ruff check python/ tests/`, `mypy python/pydocs_mcp`, `complexipy python/pydocs_mcp --max-complexity-allowed 15`, `vulture python/pydocs_mcp --min-confidence 80`, `pytest tests/ --ignore=tests/test_parity.py -q`, `uv lock --check` (after the floor bump, relock with `~/.local/bin/uv lock`).

---

## File map

| Path | Status | Stage | Owns |
|---|---|---|---|
| `python/pydocs_mcp/retrieval/config/ask_your_docs_scope_models.py` (re-exported from `ask_your_docs_models.py`; as built, P1) | modify | U0 / U0r | `ScopeSlice`, `ScopeCode`, `ScopeBranchDefault`, `ANY_PROJECT`, `ScopeDefaultsConfig` (+ `tokens_enabled`, `footer_hint` in 11g), `AskYourDocsConfig.scope` |
| `python/pydocs_mcp/defaults/default_config.yaml` | modify | U0 / U0r | the `ask_your_docs.scope` block — nine keys after 11g |
| `python/pydocs_mcp/harness/ask_your_docs/bundle.py` | modify | U0 / U1 | `IndexedBranch`, `BundleReader.branches()`; U1: `branch_symbol_chunks()`, `reference_rows(branch=)` |
| `python/pydocs_mcp/harness/ask_your_docs/catalog.py` | modify | U0 / U1 / U2 | `WorkspaceBranchListing`, `EMPTY_BRANCH_LISTING`, `CatalogService.branch_listing()`, `workspace_branch_listing()`, `render_catalog(branches=, show_merged=)` |
| `python/pydocs_mcp/harness/ask_your_docs/attachments.py` | modify | U0 | `AttachedSymbol`; `weave_attachments` accepts it |
| `python/pydocs_mcp/harness/ask_your_docs/question_scope.py` | new | U0 / U0r / U1 / U2 | `ScopeKind`, `ScopeCell`, `QuestionScope`, `ScopeDefaultsOverride`, `resolve_question_scope_defaults`, `resolve_default_branch`, `scope_prefix`, `scope_caption_text(from_question=)`, `pin_with_attached_symbols`, `snapshot_pin_for_send(active, attached)`, `log_scope_event`, server-value tables, on-screen `SLICE_LABELS` / `CODE_LABELS` + the model-note `MODEL_NOTE_*` tables; **U0r (11b):** `pin_or_none`, `token_scope`, `ordered_unique` (public, shared with `strip_state.py`); `pin_summary_label` deleted; U2: `branch_option_ids`, `cells_from_branch_selection` |
| `python/pydocs_mcp/harness/ask_your_docs/strip_state.py` | **new (pure — no Streamlit, no langchain; imports `question_scope`)** | U0r / U1 | `StripTarget`, `StripState(targets, only_these, more)`, `strip_cells`, `ordered_targets`, `strip_target_for`, `initial_strip_state`, `compile_strip_scope(…, more=, capabilities=)`, `missing_strip_cells`, `strip_chip_label` (11b); U1: `strip_target_for(…, config, capabilities)` (12b). Split out so `question_scope.py` stays near the 200–300 ideal instead of landing at ~490 |
| `python/pydocs_mcp/harness/ask_your_docs/scope_tokens.py` | **new (pure — no Streamlit, no langchain)** | U0r | `PROJECT_TOKEN_PREFIX`, `BRANCH_TOKEN_PREFIX`, `BRANCHES_NOT_CHOOSABLE`, `ParsedScopeTokens`, `parse_scope_tokens`, `strip_scope_tokens` (11a) |
| `python/pydocs_mcp/harness/ask_your_docs/scope_strip.py` | **new (Streamlit-only)** | U0r | `NO_TARGET_SENTENCE`, `ONLY_THESE_LABEL`, `FORCED_HINT`, `OVER_CAP_HINT`, `CHANGE_LABEL`, `current_strip_state`, `add_strip_target`, `drop_missing_targets`, `render_where_to_search_strip`, `render_composer_row` (11c); "Only these" is read from `StripState.only_these`, never from the widget key |
| `python/pydocs_mcp/harness/ask_your_docs/scope_picker.py` | **new (Streamlit-only)** | U0r / U1 / U2 | `PICKER_KEY`, `STRIP_STATE_KEY`, `ONLY_THESE_KEY`, `PICKER_TITLE`, `forget_picker_widgets`, `render_where_to_search_picker` — project rows, U1 pills, "More" (code / package / U2 "Which files"), preview, "Use these" / "Reset" (11c; U2 merged group in Task 17) |
| `python/pydocs_mcp/harness/ask_your_docs/page_scope.py` (as built, split from `app.py`) | modify | U0 / U0r | `scan_workspace`, `page_scope_capabilities`, `remember_scope_capabilities`, `load_branch_listing`, `answer_footer_and_chips(turn, capabilities, listing, config, strip_scope, asked)` |
| `python/pydocs_mcp/harness/ask_your_docs/scope_capabilities.py` | new | U0 | `ScopeCapabilities`, `NO_SCOPE_CAPABILITIES`, `inspect_scope_capabilities`, `BuiltAgent` |
| `python/pydocs_mcp/harness/ask_your_docs/scope_interceptor.py` | new | U0 / U0r / U1 / U2 | contextvars, `ScopeRuntime`, `BranchOrigin`, `CellObservation`, `ScopeObservations`, `intercept_question_scope`, `target_cells`, `cell_arguments`, `fan_out_over_cells`, `merge_cell_results(cells, sent, results)`; **U0r (11e):** `cell_label(cell, sent_args)` names what was sent |
| `python/pydocs_mcp/harness/ask_your_docs/agent.py` | modify | U0 / U1 | `_intercept` delegate, `ask()` new keywords, `_assemble_prompt` keywords, `build_agent_with_scope_capabilities`, `build_agent` wrapper |
| `python/pydocs_mcp/harness/ask_your_docs/__init__.py` | modify | U0 | `_LAZY["scope_prefix"] = "question_scope"` |
| `python/pydocs_mcp/harness/ask_your_docs/answer_footer.py` | new | U0 / U0r / U1 / U2 | `render_answer_footer(observations, listing, config, capabilities=)` with the D14 words, `FRESH` / `BEHIND`, the teaching hint; `FollowUpKind {ASK_ON, COMPARE_WITH, PIN_BRANCH, SHOW_DIFF}`, `FollowUpChip`, `derive_follow_up_chips(…, strip_scope, asked=, max_cells=)` (one chip per kind; no "Keep searching" chip that would push the strip past `max_cells`), `apply_follow_up_chip(chip, strip_scope, defaults)` (11e) |
| `python/pydocs_mcp/harness/ask_your_docs/activity_labels.py` | modify | U0r | `scope_note` re-worded to `Searching only in: <parts>` — the activity panel's "Show technical details" scope line is on screen, so the D14 vocabulary and AC-47's sweep reach it (11e) |
| `python/pydocs_mcp/harness/ask_your_docs/scope_panel.py` | new (Streamlit-only) | U0 / U0r / U1 | after 11c: `branch_caption`, `render_attachment_chip_row`, `render_follow_up_chips`, `GraphBranchSelection`, `render_graph_branch_row` — the defaults button / panel, the popover, the pin chip row and `drop_pin_if_listing_changed` are deleted |
| `python/pydocs_mcp/harness/ask_your_docs/app.py` | modify | U0 / U0r / U1 | `send_question(question, images, scope, transient_note, *, display_question, from_question)`, `get_agent` → `BuiltAgent`, transcript entries with footer + chips; **U0r:** sidebar scope block deleted, `drop_missing_targets` + `current_strip_state` + `compile_strip_scope` before the transcript, `render_attachment_chip_row`, the follow-up handler writing the strip, `render_composer_row` (strip + bare `st.chat_input` in `st.bottom`), the token parse / refusal gate before the other pre-send refusals (11c, 11d) |
| `python/pydocs_mcp/harness/ask_your_docs/pages/2_Graph.py` | modify | U0 / U0r / U1 | one "Where to search" popover (`graph_where_to_search`) over the shared picker, the strip-fed `default_branch`, branch row, `AttachedSymbol` attach, compare overlay (11c, 11f) |
| `python/pydocs_mcp/harness/ask_your_docs/graph_compare.py` | new | U1 | `ChangeState`, `BranchGraphComparison`, `compare_branch_graphs`, `changed_only` |
| `python/pydocs_mcp/harness/core/prompts/system_v1.j2` | modify | U1 | rule 7 inside the `is defined` guard |
| `pyproject.toml` | modify | U0 | `streamlit>=1.59` (as built; the WHY comment is re-worded in 11j) |
| `examples/harness/ask_your_docs_agent/README.md`, `CHANGELOG.md` | modify | U0 / U0r / U1 / U2 | user-facing description — the "Where to search" paragraph, the screenshot caption, the one `[Unreleased]` bullet rewritten in place (11i) |
| `tests/harness/ask_your_docs/_fixture.py` | modify | U0 / U0r / U1 | `branches` / `branch_chunks` tables, `branches=` rows, `project=`; U0r (11c): `packages=` rows, because `SqliteBundleReader.packages()` filters `__project__` out and the picker's Package selectbox renders only over a non-empty pool |
| `tests/harness/ask_your_docs/_page_fixtures.py` (as built) | — | U0 | `page(**seeds)`, `graph_page(**seeds)`, autouse `page_env`, `write_config` — every AppTest's only entry point |
| `tests/fixtures/goldens/ask_your_docs_system_v1.txt` | new | U0 | today's rendered `system_v1` bytes (AC-11 golden) |
| `tests/harness/ask_your_docs/test_{question_scope,scope_capabilities,scope_interceptor,bundle_branches,catalog_branches,answer_footer,graph_compare,app_scope_states,graph_page_scope,scope_pin,module_line_budgets}.py` | new / as built | per stage | the acceptance criteria; `test_app_scope_states.py` and the sidebar half of `test_graph_page_scope.py` are rewritten in U0r |
| `tests/harness/ask_your_docs/test_scope_tokens.py`, `tests/harness/ask_your_docs/test_scope_vocabulary.py` (pure), `tests/harness/ask_your_docs/test_app_scope_vocabulary.py` (AppTest) | **new** | U0r | AC-36–AC-39 (parser), AC-47 (source-text half / rendered-tree half) |

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
# (multi-branch spec §6.5b); v18 also stamps landing_kind.
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
            # landing_kind arrives with schema v18; read NULL on v16.
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

> **Superseded by U0r (Task 11e) — words and one kind.** The logic below stands; the on-screen strings do not: `ORIGIN_LABELS` / the segment format / `index stale` are re-worded, `FollowUpKind` gains `ASK_ON`, `derive_follow_up_chips` yields at most one chip per kind (not one `PIN_BRANCH` per cell) and takes the strip scope plus the asked text, `render_answer_footer` takes the config. Every string assertion in `test_answer_footer.py` is rewritten in 11e; `test_a_fourth_answered_cell_is_cut_by_the_kind_count_cap` is deleted there.

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

> **Superseded by U0r (Tasks 11c + 11d) — the interface.** Kept from this task: the Streamlit floor bump, `snapshot_pin_for_send` (re-shaped in 11b: no `keep`), `render_follow_up_chips`, `render_graph_branch_row` / `GraphBranchSelection`, the `page(**seeds)` fixture discipline and the two attachment AppTests. Deleted by 11c: `render_scope_defaults_button`, `render_scope_defaults_panel`, `render_scope_pin_popover`, `render_scope_chip_row`, `drop_pin_if_listing_changed`, `SOFT_DEFAULTS_CAPTION`, the `[1, 12]` composer split, the sidebar scope block, the `scope_pin` / `scope_pin_keep` / `scope_defaults_open` session keys and every `scope_defaults_*` / `scope_pin_*` widget key. `test_app_scope_states.py` is rewritten whole in 11c/11d; the intent of `test_removing_one_of_two_cell_chips_keeps_the_other` and `test_reset_to_shipped_restores_the_yaml_values` survives there against the new keys.

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/scope_panel.py`
- Modify: `python/pydocs_mcp/harness/ask_your_docs/app.py` (full rewrite below)
- Modify: `pyproject.toml:170` (`streamlit>=1.59`), `uv.lock` (relock)
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

In `pyproject.toml`, line 170 becomes:

```toml
    "streamlit>=1.59",               # WHY: st.bottom (the strip's bottom container) + stateful st.popover (the keyed where-to-search picker) + st.pills; chat_input(accept_file=...) needs 1.43
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

> **Superseded by U0r (Task 11f) — the sidebar block only.** The branch row, the U0 caption (re-worded `indexed on <branch> @<sha7>`), `AttachedSymbol(selected, project, selection.branch)` and R8's Compare-with / changed-only row stand. The `render_scope_defaults_button()` + `render_scope_defaults_panel(...)` pair becomes one "Where to search" popover (`graph_where_to_search`) over the shared picker; `test_graph_page_scope.py`'s two sidebar tests and its override test are rewritten in 11f.

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/pages/2_Graph.py`

**Interfaces:**
- Consumes: `render_scope_defaults_button`, `render_scope_defaults_panel`, `render_graph_branch_row`, `GraphBranchSelection` (Task 9); `resolve_question_scope_defaults`, `resolve_default_branch` (Task 4); `AttachedSymbol` (Task 4); `CatalogService.branch_listing()` (Task 3); `NO_SCOPE_CAPABILITIES`, `ScopeCapabilities` (Task 5).
- Produces: the graph page's `selection: GraphBranchSelection` (consumed by Task 14's compare overlay); "Add to question" appends `AttachedSymbol(selected, project, selection.branch)`.

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

> **Superseded by U0r (Tasks 11i + 11j) — the text; the procedure stands.** The README paragraph and the CHANGELOG bullet written here describe the sidebar button, the panel and the composer popover; 11i rewrites both in place (one bullet, never a second) and redraws the screenshot caption. The jargon audit, the conformance run, the gate list, the `git checkout -- complexipy-snapshot.json` line and the vulture note are reused verbatim by 11j.

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
  `diff`. The `[harness-ask-your-docs]` extra now requires `streamlit>=1.59`.
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

# Stage U0r — interface redesign (A + C, owner decision D14 of spec §3)

Precondition: Stage U0's Tasks 1–11 are on `feat/ayd-branch-scope-u0` (draft PR #267). U0r replaces that stage's **interface** before the PR leaves draft; the **engine** — `QuestionScope`, the interceptor, fan-out, observations, the footer and chip derivation — is kept. Nothing here needs a server change: a multi-project pin is accepted today (`tests/harness/ask_your_docs/test_scope_interceptor.py::test_ac6b_two_project_pin_fans_out_over_project_only`, the load-bearing U0r criterion AC-6b). The spec is already amended (§6.1, §6.7, §6.8, §6.9, §6.10, §6.10a, §6.12, §6.13, §7, §9 E13–E16, §10 AC-35–AC-51, §11, §12); this stage implements the amended text, which is the source of truth wherever this plan and the spec differ.

**Numbering.** The tasks keep the suffixed numbers 11a–11k of the plan map so Tasks 12–18 and every cross-reference keep their numbers. They are listed in **execution order** — 11g, 11a, 11b, 11c, 11d, 11e, 11f, 11h, 11i, 11j, 11k — because the two config keys of 11g are read by every later fixture. "Owner decision D14" always means the spec's D14 (§3); the plan's own deviation table is renumbered P1–P16 below (§Deviations) to end the D-number collision, and this stage adds P17–P25.

**Venvs.** `WT=/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/branch-footer`. Core suites run from the worktree venv (`cd $WT && .venv/bin/pytest tests/harness/ask_your_docs -q`; create it with `uv venv && uv sync --frozen --group dev` if missing). AppTest suites need `[harness-ask-your-docs]`, installed in the main checkout's venv, pointed at the worktree sources: `cd $WT && PYTHONPATH=$WT/python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest -o pythonpath=$WT/python tests/harness/ask_your_docs/test_app_scope_states.py -q`. Without `PYTHONPATH` the main checkout's sources are imported and every U0r AppTest fails for the wrong reason.

**Decisions this stage fixes (the plan map left them open; each is a deviation row P17–P25):**

- **Flags are parameters.** `scope_tokens.py` and `answer_footer.py` take `tokens_enabled` / `footer_hint` (through `ScopeDefaultsConfig`) as arguments; only the two pages read `AppConfig`. 11g therefore runs first but nothing below imports config at module level.
- **`scope_panel.py` splits, and so does `question_scope.py`.** `scope_panel.py` has no headroom (467/500): the strip lives in a new `scope_strip.py`, the picker in a new `scope_picker.py`; `scope_panel.py` keeps the attachment chip row, the follow-up chips, the graph branch row and the shared branch caption. `question_scope.py` (348 lines) would land at ~490 with the strip value objects and grow again in 12b and Task 17, so the strip state and its compiler live in a new pure `strip_state.py` (imports `question_scope`, never the reverse); `question_scope.py` keeps `token_scope`, `pin_or_none` and the tables. Each new module gets its own `_BUDGETS` entry (300 lines).
- **Strip state shape.** `StripTarget(project, branches)` and `StripState(targets, only_these, more)` (frozen, in `strip_state.py` — the spec's `StripState(targets, only_these)` plus the "More" values) live under the session key `scope_strip`. "Only these" is the user's OWN tick, held in the state so it is sticky "until changed" (a widget key alone is dropped by Streamlit on any page that does not render the strip — the graph page — and would silently reset it); the checkbox `scope_strip_only_these` is seeded from `StripState.only_these` on every run and written back through its `on_change` callback. The forced-on state at two or more cells is a rendering rule only: the key is set to `True` before the checkbox instantiates and the box is disabled, but the forced value is never persisted, so dropping back to one cell shows the user's own choice again. `compile_strip_scope(targets, only_these, config, listing, more=, capabilities=)` keeps the spec's positional shape; `more` carries the picker's "More" values as a `ScopeDefaultsOverride`; `capabilities` (default `NO_SCOPE_CAPABILITIES`) lets the U1 one-target soft case send the branch the chip shows (12b) without touching U0.
- **What a U0 target carries.** A strip target created on U0 carries the listing's **stamped** branch (`default_row`), so the chip key is `scope_chip_<project>_<branch>`, the transcript caption reads `searched in: backend · feature/retry | tooling · main`, and the fixture shape equals `test_ac6b_…`'s (cells carry a branch the interceptor does not send: `cell_arguments` drops it while `branch_selector` is false). A **token** cell on U0r is `(project, "")` (spec §6.10a). Both are harmless to the engine; the label rule of 11e names what was sent either way.
- **Every strip / picker mutation is an `on_click` / `on_change` callback** (chip `✕`, "Clear", "Use these", "Reset", the "Only these" tick) — the rule of P15 — because a widget's own key is writable only before the widget instantiates in a run, and the picker's checkboxes must re-seed from the strip after every change (`forget_picker_widgets`). The one non-callback write is row 2's forcing of `scope_strip_only_these` to `True` at two or more cells: it happens in the render path immediately before that checkbox instantiates, which Streamlit allows (V2), and it never reaches `StripState.only_these`.
- **The model note keeps its own words.** `scope_prefix` (AC-28, bytes frozen) reads `MODEL_NOTE_SLICE_WORDS` / `MODEL_NOTE_CODE_WORDS` (today's `SLICE_LABELS` / `CODE_LABELS` values, renamed); the on-screen `SLICE_LABELS` / `CODE_LABELS` take the D14 vocabulary. The vocabulary sweep (11h) exempts the two model-note tables by name.
- **`render_answer_footer(observations, listing, config, capabilities=NO_SCOPE_CAPABILITIES)`** — the spec's third parameter plus the capability record the U1 `on:` hint needs. `derive_follow_up_chips(observations, listing, capabilities, strip_scope, asked="", *, max_cells=…)` gains the answered question's text because an `ASK_ON` chip re-sends it, and the cap because a "Keep searching <branch>" chip must never grow the strip past `max_cells` (the picker refuses that; the chip must too, or every later send fails at E4).
- **Typed tokens: two grammar details the spec's §6.10a does not spell out** (P24): punctuation glued to a token (`in:backend?`, `in:backend,`) is not part of the name — `?.,;:!` are stripped from the end of a token before the lookup — and a bare `on:` that precedes an `in:` token in the same question is refused with a message that names the order (`on:main must come after its in:<project> (found in:backend later in the question). Nothing was sent.`), whatever the strip holds. `in:` matches the listing's **project names** only (the refusal lists exactly the accepted names; a bundle stem is the multirepo selector's spelling and never reaches a caption).

**Superseded U0 tasks.** Task 8 (words + `ASK_ON`, 11e), Task 9 (the fragments and the page: 11c + 11d), Task 10 (the graph sidebar block: 11f) and Task 11 (the docs text: 11i) carry a "Superseded by U0r" note at their heads. Tasks 1–7 stand; Task 1 is extended by 11g, Task 4 by 11b, Task 6 by the one-function label change in 11e.

### Task 11g: Config keys `tokens_enabled` and `footer_hint`

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config/ask_your_docs_scope_models.py` (`ScopeDefaultsConfig`; the docstring still says "The sidebar "Scope defaults" panel")
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` (the `scope:` block after `images:`, whose header comment still says *the sidebar "Scope defaults" panel*)
- Test: `tests/test_config_ask_your_docs.py`

**Interfaces:**
- Consumes: `ScopeDefaultsConfig` (Task 1).
- Produces: `ScopeDefaultsConfig.tokens_enabled: bool = True`, `ScopeDefaultsConfig.footer_hint: bool = True`; the YAML block grows to nine keys.
- Satisfies: AC-22 (D14 words).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config_ask_your_docs.py`:

```python
def test_scope_tokens_and_footer_hint_default_on() -> None:
    """AC-22 (D14): the two D14 keys default to true in BOTH sources; `is`, not `==`,
    so an int 1 from a sloppy YAML layer cannot pass (the PR #267 bool-coercion trap)."""
    from pydocs_mcp.retrieval.config.ask_your_docs_scope_models import ScopeDefaultsConfig

    scope = AppConfig.load().ask_your_docs.scope
    assert scope.tokens_enabled is True and scope.footer_hint is True
    assert ScopeDefaultsConfig(tokens_enabled=False, footer_hint=False).tokens_enabled is False


def test_scope_tokens_enabled_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__SCOPE__TOKENS_ENABLED", "false")
    scope = AppConfig.load().ask_your_docs.scope
    assert scope.tokens_enabled is False
    assert scope.footer_hint is True  # the sibling key is untouched by the override


def test_default_yaml_ships_exactly_nine_scope_keys() -> None:
    """The exact sorted key list — a count would pass with one key renamed."""
    from pathlib import Path as _P

    import yaml

    root = _P(__file__).resolve().parents[1]
    shipped = yaml.safe_load(
        (root / "python/pydocs_mcp/defaults/default_config.yaml").read_text(encoding="utf-8")
    )
    assert sorted(shipped["ask_your_docs"]["scope"]) == [
        "branch_default",
        "branch_name",
        "code",
        "footer_hint",
        "max_cells",
        "package",
        "project",
        "slice",
        "tokens_enabled",
    ]
```

and extend `test_default_yaml_ships_the_block_keys` with two lines after `assert block["scope"]["max_cells"] == 4`:

```python
    assert block["scope"]["tokens_enabled"] is True
    assert block["scope"]["footer_hint"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $WT && .venv/bin/pytest tests/test_config_ask_your_docs_scope.py -q`
Expected: FAIL — `AttributeError: 'ScopeDefaultsConfig' object has no attribute 'tokens_enabled'`, `ValidationError: … Extra inputs are not permitted` (the `extra="forbid"` model refuses the kwarg), and `KeyError: 'tokens_enabled'` on the YAML block.

- [ ] **Step 3: Add the two fields**

In `ask_your_docs_scope_models.py`, after `max_cells`:

```python
    # D14 typed tokens: "in:<project>" / "on:<branch>" inside the question, one-shot;
    # false leaves the text untouched and parses nothing (UI spec §6.10a).
    tokens_enabled: bool = Field(default=True)
    # The footer's teaching hint ("add in:<name> to search there too", §6.8); it only
    # ever renders when tokens_enabled is also true.
    footer_hint: bool = Field(default=True)
```

and rewrite the class docstring's first paragraph: `The "Where to search" strip and picker override these for one session only; they fill what the model leaves unspecified and never overwrite a model-passed argument.`

- [ ] **Step 4: Rewrite the YAML block**

Replace the `scope:` block of `default_config.yaml` with the spec §7 block verbatim — the header comment becomes `the initial "Where to search" state for the chat and graph pages; the strip overrides these for one session only`, the `branch_default` comment says `in the picker`, the `max_cells` comment gains `shown on screen as the strip's "limit M" and the picker's "N of M"`, and the two new keys close the block:

```yaml
    tokens_enabled: true          # allow "in:<project>" / "on:<branch>" tokens inside
                                  # the question (one-shot); false leaves the text
                                  # untouched and parses nothing
    footer_hint: true             # append the teaching hint to the answer footer
                                  # ("add in:<name> to search there too") when
                                  # tokens_enabled is true
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd $WT && .venv/bin/pytest tests/test_config_ask_your_docs.py -q`
Expected: PASS, including `test_scope_defaults_yaml_matches_pydantic_defaults` (YAML ↔ `Field` parity) and `test_scope_rejects_unknown_keys` (`extra="forbid"` still holds; no key was removed, so an overlay written for the 2026-09-04 block still loads).

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/config/ask_your_docs_scope_models.py python/pydocs_mcp/defaults/default_config.yaml tests/test_config_ask_your_docs.py
git commit -m "ask-your-docs: scope.tokens_enabled and scope.footer_hint config keys"
```

---

### Task 11a: `scope_tokens.py` — the `in:` / `on:` parser

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/scope_tokens.py`
- Modify: `tests/harness/ask_your_docs/test_module_line_budgets.py` (`_BUDGETS` gains `scope_tokens.py: 200`)
- Test: `tests/harness/ask_your_docs/test_scope_tokens.py` (new)

**Interfaces:**
- Consumes: `WorkspaceBranchListing.project_names / pickable / default_row` (Task 3); `ScopeCell` (Task 4); `ScopeCapabilities.branch_selector` (Task 5).
- Produces: `PROJECT_TOKEN_PREFIX = "in:"`, `BRANCH_TOKEN_PREFIX = "on:"`, `BRANCHES_NOT_CHOOSABLE` (the E14 sentence), `ParsedScopeTokens(cells, stripped_text, refusal="")`, `parse_scope_tokens(text, listing, capabilities, strip_projects, *, tokens_enabled, max_cells) -> ParsedScopeTokens`, `strip_scope_tokens(text) -> str`.
- Pure by contract: no `streamlit`, no `langchain` (subprocess pin). The U1 half (`on:` accepted, default-row cells) is written here and inactive behind `branch_selector`.
- Grammar details beyond §6.10a (P24): a token's name is the word after the prefix with trailing `?.,;:!` removed (`in:backend?` names `backend`; the whole word, punctuation included, leaves the sent text); `in:` matches `listing.project_names` exactly — not bundle stems — so the refusal's "Indexed:" list is the complete set of accepted spellings; a bare `on:` that precedes an `in:` anywhere later in the question is refused with the ordering message even when one project is in play, because the later `in:` says which project the user meant.
- Satisfies: AC-36, AC-37, AC-38, the parser half of AC-39; E13, E14, E15, E4 (token path).

- [ ] **Step 1: Write the failing tests**

Create `tests/harness/ask_your_docs/test_scope_tokens.py`:

```python
"""``scope_tokens`` — AC-36, AC-37, AC-38, AC-39 (parser half), E13–E15 (spec §6.10a).

Fixture rule (spec §11): the tokened project is ``tooling`` — neither the YAML default
project (``any``) nor the listing's first row — so "tokens win", "the default was already
that" and "the first project wins" are three different answers.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch
from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import ScopeCell
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.harness.ask_your_docs.scope_tokens import (
    BRANCH_TOKEN_PREFIX,
    BRANCHES_NOT_CHOOSABLE,
    PROJECT_TOKEN_PREFIX,
    ParsedScopeTokens,
    parse_scope_tokens,
    strip_scope_tokens,
)
from pydocs_mcp.models import BranchStatus

U1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)


def _row(name, *, default=False, base=None):
    return IndexedBranch(name, "a" * 40, base, default, BranchStatus.ACTIVE, None, None, 1.0)


# backend's stamped row is feature/retry (base main); tooling's is main: the two
# projects disagree on default vs base, so "default row" and "base" are told apart.
LISTING = WorkspaceBranchListing(
    projects={
        "backend": (_row("feature/retry", default=True, base="main"), _row("main")),
        "tooling": (_row("main", default=True), _row("develop", base="main")),
        "example_needle": (_row("main", default=True),),
    }
)
ONE_PROJECT = WorkspaceBranchListing(projects={"backend": LISTING.rows("backend")})


def _parse(text, *, listing=LISTING, capabilities=NO_SCOPE_CAPABILITIES, strip=(), enabled=True, cap=4):
    return parse_scope_tokens(
        text, listing, capabilities, strip, tokens_enabled=enabled, max_cells=cap
    )


class TestGrammar:  # AC-36
    def test_in_token_names_a_project_and_is_stripped(self):
        parsed = _parse("why does retry drop the last attempt? in:tooling")
        assert parsed == ParsedScopeTokens(
            (ScopeCell("tooling", ""),), "why does retry drop the last attempt?"
        )

    def test_tokens_anywhere_keep_question_order_and_collapse_their_whitespace(self):
        parsed = _parse("in:tooling  what   in:backend is Foo?")
        assert parsed.cells == (ScopeCell("tooling", ""), ScopeCell("backend", ""))
        assert parsed.stripped_text == "what is Foo?"

    def test_a_repeated_project_yields_one_cell(self):
        assert _parse("in:tooling q in:tooling").cells == (ScopeCell("tooling", ""),)

    def test_a_bare_prefix_is_question_text_not_a_token(self):
        parsed = _parse("what does in: mean on: this page?")
        assert parsed.cells == () and parsed.stripped_text == "what does in: mean on: this page?"

    def test_no_token_text_is_byte_identical(self):
        # Doubled spaces on purpose: a stripper that always re-joins would collapse them.
        text = "what  is   Foo?"
        parsed = _parse(text)
        assert parsed.cells == () and parsed.stripped_text == text and parsed.refusal == ""
        assert strip_scope_tokens(text) == text

    def test_tokens_disabled_parses_nothing_and_refuses_nothing(self):
        text = "q in:backnd on:nope"
        assert _parse(text, enabled=False) == ParsedScopeTokens((), text)

    def test_on_attaches_to_the_nearest_preceding_in(self):
        parsed = _parse("q in:backend on:main in:tooling", capabilities=U1)
        # tooling had no on: → its DEFAULT row (main), not the base and not backend's branch
        assert parsed.cells == (ScopeCell("backend", "main"), ScopeCell("tooling", "main"))
        assert parsed.stripped_text == "q"

    def test_several_on_after_one_in_select_several_branches(self):
        parsed = _parse("in:tooling on:develop on:main q", capabilities=U1)
        assert parsed.cells == (ScopeCell("tooling", "develop"), ScopeCell("tooling", "main"))

    def test_lone_on_with_one_project_in_play_resolves(self):
        by_strip = _parse("q on:develop", capabilities=U1, strip=("tooling",))
        assert by_strip.cells == (ScopeCell("tooling", "develop"),)
        by_workspace = _parse("q on:main", listing=ONE_PROJECT, capabilities=U1)
        assert by_workspace.cells == (ScopeCell("backend", "main"),)

    def test_punctuation_glued_to_a_token_is_not_part_of_the_name(self):
        """P24: `in:tooling?` names tooling; the whole word (punctuation included) leaves
        the sent text, so the question ends without its `?` — stated, not hidden."""
        parsed = _parse("what is Foo in:tooling?")
        assert parsed.cells == (ScopeCell("tooling", ""),) and parsed.stripped_text == "what is Foo"
        assert _parse("in:tooling, what is Foo").cells == (ScopeCell("tooling", ""),)
        assert _parse("q in:backend on:main!", capabilities=U1).cells == (ScopeCell("backend", "main"),)
        # A prefix followed by punctuation only is still question text, not a token.
        assert _parse("what does in:? mean").cells == ()

    def test_a_bundle_stem_is_not_a_project_name(self):
        """`in:` matches project names only (P24): the refusal's list is then the complete
        set of accepted spellings, and no stem ever reaches a caption."""
        stems = WorkspaceBranchListing(projects=LISTING.projects, bundle_stems=frozenset({"backend_0123456789"}))
        parsed = _parse("q in:backend_0123456789", listing=stems)
        assert parsed.cells == () and parsed.refusal.startswith("No project named 'backend_0123456789'")

    def test_prefix_constants_are_the_only_source_of_the_literals(self):
        import pydocs_mcp.harness.ask_your_docs.scope_tokens as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert (PROJECT_TOKEN_PREFIX, BRANCH_TOKEN_PREFIX) == ("in:", "on:")
        assert source.count('"in:"') == 1 and source.count('"on:"') == 1


class TestRefusals:  # AC-37, AC-38, E15, E4
    def test_unknown_project_refuses_with_the_indexed_names(self):
        parsed = _parse("what is Foo? in:backnd")
        assert parsed.refusal == (
            "No project named 'backnd'. Indexed: backend, tooling, example_needle. "
            "Nothing was sent."
        )
        assert parsed.cells == () and parsed.stripped_text == "what is Foo? in:backnd"

    def test_on_is_refused_before_branch_selector_even_for_the_stamped_branch(self):
        # main IS tooling's stamped branch: a parser that lets the stamped name through fails.
        assert _parse("q in:tooling on:main").refusal == BRANCHES_NOT_CHOOSABLE
        # Refused BEFORE the project name is checked (§6.10a).
        assert _parse("q in:backnd on:main").refusal == BRANCHES_NOT_CHOOSABLE
        assert BRANCHES_NOT_CHOOSABLE == (
            "Branches can't be chosen yet: this server indexes one branch per project."
        )

    def test_unknown_branch_of_a_known_project(self):
        parsed = _parse("q in:backend on:featur/retry", capabilities=U1)
        assert parsed.refusal == (
            "No branch named 'featur/retry' on backend. Indexed: feature/retry, main. "
            "Nothing was sent."
        )

    def test_lone_on_with_two_projects_in_play_is_refused(self):
        parsed = _parse("q on:main", capabilities=U1)
        assert parsed.refusal == (
            "on:main needs a project: add in:<project> before it. "
            "In play: backend, tooling, example_needle. Nothing was sent."
        )
        two = _parse("q on:main", capabilities=U1, strip=("backend", "tooling"))
        assert two.refusal.startswith("on:main needs a project") and "In play: backend, tooling." in two.refusal

    def test_a_bare_on_before_an_in_is_refused_and_names_the_order(self):
        """P24: the ordering is the mistake, so the message says so — and a later `in:`
        wins over the lone-project rule, because it says which project was meant."""
        parsed = _parse("q on:main in:backend", capabilities=U1)
        assert parsed.refusal == (
            "on:main must come after its in:<project> (found in:backend later in the question). "
            "Nothing was sent."
        )
        with_lone = _parse("q on:develop in:backend", capabilities=U1, strip=("tooling",))
        assert with_lone.refusal.startswith("on:develop must come after its in:<project>")
        assert with_lone.cells == ()

    def test_over_the_cap_is_refused_before_any_call(self):
        many = WorkspaceBranchListing(projects={p: (_row("main", default=True),) for p in "abcde"})
        parsed = _parse("q in:a in:b in:c in:d in:e", listing=many, cap=4)
        assert parsed.cells == ()
        assert parsed.refusal == (
            "5 searches exceed the limit of 4 (ask_your_docs.scope.max_cells). Nothing was sent."
        )
        assert _parse("q in:a in:b in:c in:d", listing=many, cap=4).refusal == ""


def test_scope_tokens_module_stays_streamlit_and_langchain_free() -> None:
    code = (
        "import sys\n"
        "import pydocs_mcp.harness.ask_your_docs.scope_tokens\n"
        "assert not any(m.startswith(('langchain', 'streamlit')) for m in sys.modules)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $WT && .venv/bin/pytest tests/harness/ask_your_docs/test_scope_tokens.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pydocs_mcp.harness.ask_your_docs.scope_tokens'`.

- [ ] **Step 3: Write the module**

Create `python/pydocs_mcp/harness/ask_your_docs/scope_tokens.py`:

```python
"""Typed ``in:<project>`` / ``on:<branch>`` tokens inside a question (UI spec §6.10a).

Pure by contract — no streamlit, no langchain (a subprocess pin holds it). The page
hands the typed text in and gets back the cells to pin (a one-shot PIN), the text to
send, and — when a name is unknown — the refusal that stops the send.

Example:
    parsed = parse_scope_tokens("why? in:backend", listing, caps, (), tokens_enabled=True, max_cells=4)
    parsed.cells == (ScopeCell("backend", ""),) and parsed.stripped_text == "why?"
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import ScopeCell
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities

# The single source of both literals (R13); nothing else in the harness spells them.
PROJECT_TOKEN_PREFIX = "in:"
BRANCH_TOKEN_PREFIX = "on:"
BRANCHES_NOT_CHOOSABLE = (
    "Branches can't be chosen yet: this server indexes one branch per project."
)
_NOTHING_SENT = "Nothing was sent."
# Punctuation a person glues to the last word of a sentence ("… in:backend?"); it is
# never part of a project or branch name, so it never reaches the lookup (P24).
_GLUED_PUNCTUATION = "?.,;:!"


@dataclass(frozen=True, slots=True)
class ParsedScopeTokens:
    """``cells`` empty = no token in the text; ``refusal`` non-empty = do not send."""

    cells: tuple[ScopeCell, ...]
    stripped_text: str
    refusal: str = ""


def _token_name(word: str, prefix: str) -> str | None:
    """The name after ``prefix`` with glued punctuation removed, or None when ``word``
    is not that token (a bare prefix, or a prefix followed by punctuation only, is
    question text)."""
    if not word.startswith(prefix):
        return None
    return word[len(prefix) :].rstrip(_GLUED_PUNCTUATION) or None


def _is_token(word: str) -> bool:
    return any(
        _token_name(word, prefix) is not None
        for prefix in (PROJECT_TOKEN_PREFIX, BRANCH_TOKEN_PREFIX)
    )


def strip_scope_tokens(text: str) -> str:
    """The question without its tokens, whitespace collapsed to one space — and
    byte-identical when nothing parses (spec §8, token neutrality)."""
    words = text.split()
    kept = [word for word in words if not _is_token(word)]
    return text if len(kept) == len(words) else " ".join(kept)


def _lone_project(strip_projects: tuple[str, ...], listing: WorkspaceBranchListing) -> str:
    """The one project a bare ``on:`` may attach to: the strip's single target, else
    the workspace's single project, else ``""`` (refused)."""
    in_play = strip_projects or listing.project_names
    return in_play[0] if len(in_play) == 1 else ""


def _unknown_project(name: str, listing: WorkspaceBranchListing) -> str:
    # The list IS the accepted set: `in:` matches project names, never bundle stems (P24).
    return f"No project named {name!r}. Indexed: {', '.join(listing.project_names)}. {_NOTHING_SENT}"


def _on_before_in(branch: str, later_project: str) -> str:
    return (
        f"{BRANCH_TOKEN_PREFIX}{branch} must come after its {PROJECT_TOKEN_PREFIX}<project> "
        f"(found {PROJECT_TOKEN_PREFIX}{later_project} later in the question). {_NOTHING_SENT}"
    )


def _first_project_token(words: list[str]) -> str:
    """The first ``in:`` name among ``words``, or ``""``."""
    return next((n for n in (_token_name(w, PROJECT_TOKEN_PREFIX) for w in words) if n), "")


def _unknown_branch(branch: str, project: str, names: tuple[str, ...]) -> str:
    return f"No branch named {branch!r} on {project}. Indexed: {', '.join(names)}. {_NOTHING_SENT}"


def _needs_project(branch: str, in_play: tuple[str, ...]) -> str:
    return (
        f"{BRANCH_TOKEN_PREFIX}{branch} needs a project: add {PROJECT_TOKEN_PREFIX}<project> "
        f"before it. In play: {', '.join(in_play)}. {_NOTHING_SENT}"
    )


def _too_many(count: int, cap: int) -> str:
    return f"{count} searches exceed the limit of {cap} (ask_your_docs.scope.max_cells). {_NOTHING_SENT}"


def _attach_branch(
    targets: dict[str, list[str]],
    branch: str,
    owner: str,
    listing: WorkspaceBranchListing,
) -> str:
    """Attach ``branch`` to ``owner``; the refusal text when it is not pickable there."""
    names = tuple(r.name for r in listing.pickable(owner))
    if branch not in names:
        return _unknown_branch(branch, owner, names)
    targets.setdefault(owner, []).append(branch)
    return ""


def _bare_branch_owner(
    branch: str,
    later_words: list[str],
    strip_projects: tuple[str, ...],
    listing: WorkspaceBranchListing,
) -> tuple[str, str]:
    """(owner, refusal) for an ``on:`` with no ``in:`` before it: a later ``in:`` is an
    ordering mistake and is named (P24); else the lone project in play; else refused."""
    later = _first_project_token(later_words)
    if later:
        return "", _on_before_in(branch, later)
    owner = _lone_project(strip_projects, listing)
    if not owner:
        return "", _needs_project(branch, strip_projects or listing.project_names)
    return owner, ""


def _walk_tokens(
    words: list[str],
    listing: WorkspaceBranchListing,
    strip_projects: tuple[str, ...],
) -> tuple[dict[str, list[str]], str]:
    """project -> branches in token order, or the first refusal met."""
    targets: dict[str, list[str]] = {}
    current = ""
    for position, word in enumerate(words):
        project = _token_name(word, PROJECT_TOKEN_PREFIX)
        if project is not None:
            if project not in listing.project_names:  # names only, never stems (P24)
                return {}, _unknown_project(project, listing)
            current = project
            targets.setdefault(project, [])
            continue
        branch = _token_name(word, BRANCH_TOKEN_PREFIX) or ""
        owner, refusal = (current, "") if current else _bare_branch_owner(
            branch, words[position + 1 :], strip_projects, listing
        )
        refusal = refusal or _attach_branch(targets, branch, owner, listing)
        if refusal:
            return {}, refusal
    return targets, ""


def _cells_of(
    targets: dict[str, list[str]],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> tuple[ScopeCell, ...]:
    """U0r: ``(project, "")`` per project (nothing branch-shaped can be sent). U1: one
    cell per attached branch, or the project's default row when none was named."""
    cells: list[ScopeCell] = []
    for project, branches in targets.items():
        if not capabilities.branch_selector:
            cells.append(ScopeCell(project, ""))
            continue
        row = listing.default_row(project)
        cells.extend(ScopeCell(project, b) for b in (branches or [row.name if row else ""]))
    return tuple(dict.fromkeys(cells))


def parse_scope_tokens(
    text: str,
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    strip_projects: tuple[str, ...],
    *,
    tokens_enabled: bool,
    max_cells: int,
) -> ParsedScopeTokens:
    """The grammar and refusals of UI spec §6.10a (plus P24: glued punctuation is
    not part of a name, ``in:`` matches project names only, and a bare ``on:``
    ahead of an ``in:`` is refused by naming the order). The two flags are
    parameters so this module reads no config; every ``on:`` is refused before
    ``branch_selector`` is advertised, before any name is checked."""
    if not tokens_enabled:
        return ParsedScopeTokens((), text)
    words = [word for word in text.split() if _is_token(word)]
    if not words:
        return ParsedScopeTokens((), text)
    names_a_branch = any(_token_name(w, BRANCH_TOKEN_PREFIX) is not None for w in words)
    if names_a_branch and not capabilities.branch_selector:
        return ParsedScopeTokens((), text, BRANCHES_NOT_CHOOSABLE)
    targets, refusal = _walk_tokens(words, listing, strip_projects)
    if refusal:
        return ParsedScopeTokens((), text, refusal)
    cells = _cells_of(targets, listing, capabilities)
    if len(cells) > max_cells:
        return ParsedScopeTokens((), text, _too_many(len(cells), max_cells))
    return ParsedScopeTokens(cells, strip_scope_tokens(text))


__all__ = (
    "BRANCHES_NOT_CHOOSABLE",
    "BRANCH_TOKEN_PREFIX",
    "PROJECT_TOKEN_PREFIX",
    "ParsedScopeTokens",
    "parse_scope_tokens",
    "strip_scope_tokens",
)
```

- [ ] **Step 4: Add the line budget**

In `tests/harness/ask_your_docs/test_module_line_budgets.py`, after the `scope_pin.py` entry: `_HARNESS / "scope_tokens.py": 200,`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd $WT && .venv/bin/pytest tests/harness/ask_your_docs/test_scope_tokens.py tests/harness/ask_your_docs/test_module_line_budgets.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/scope_tokens.py tests/harness/ask_your_docs/test_scope_tokens.py tests/harness/ask_your_docs/test_module_line_budgets.py
git commit -m "ask-your-docs: scope_tokens parser for in:/on: tokens (one-shot, refuse on unknown)"
```

---

### Task 11b: The strip-state compiler — `strip_state.py` with `StripTarget`, `StripState`, `compile_strip_scope`

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/strip_state.py` (pure — no Streamlit, no langchain; the strip value objects and `compile_strip_scope`, `initial_strip_state`, `strip_cells`, `ordered_targets`, `strip_target_for`, `missing_strip_cells`, `strip_chip_label`)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/question_scope.py` (add `token_scope`, `pin_or_none`; make `_ordered_unique` public as `ordered_unique` (the strip module shares it); re-word `SLICE_LABELS` / `CODE_LABELS` and add the model-note tables; `scope_caption_text` gains the `searched in:` prefix and `from_question=`; `snapshot_pin_for_send` loses `keep`; **delete** `pin_summary_label`; re-word the `ScopeDefaultsOverride` docstring)
- Modify: `tests/harness/ask_your_docs/test_module_line_budgets.py` (`strip_state.py: 300`; `question_scope.py: 400` — the module has no entry today and lands near 390)
- Test: `tests/harness/ask_your_docs/test_question_scope.py` (rewrite `TestPrefix::test_caption_and_summary` and the two `snapshot_pin_for_send` tests; append `TestStripScope`, which imports from `strip_state` and reuses this file's `_LISTING` / `_row`), `tests/harness/ask_your_docs/test_scope_interceptor.py` (append the cap test), `tests/harness/ask_your_docs/test_scope_pin.py` (one more subprocess purity pin, for `strip_state`)

**Interfaces:**
- Consumes: `resolve_question_scope_defaults`, `ScopeDefaultsOverride`, `pin_with_attached_symbols`, `code_compatible_with_slice`, `log_scope_event` (Task 4); `WorkspaceBranchListing.project_names / default_row / has_branch / knows_project` (Task 3); `ScopeDefaultsConfig` (Task 1, 11g); `ScopeCapabilities`, `NO_SCOPE_CAPABILITIES` (Task 5 — `scope_capabilities.py` imports nothing from the harness's scope modules, so the import is cycle-free).
- Produces (`strip_state.py`): `StripTarget(project, branches=())` with `cells()`; `StripState(targets=(), only_these=False, more=ScopeDefaultsOverride())` with `projects()`, `with_target(target)`, `without_cell(cell)`, `cleared()` (no target, `only_these` back to `False`, "More" kept); `strip_cells(targets) -> tuple[ScopeCell, ...]`; `ordered_targets(targets, listing)` (listing order, unknown projects last); `strip_target_for(project, listing)`; `initial_strip_state(config, listing) -> StripState` (logs `scope_default_replaced` for an unlisted YAML project, as `resolve_question_scope_defaults` does); `compile_strip_scope(targets, only_these, config, listing, more=ScopeDefaultsOverride(), capabilities=NO_SCOPE_CAPABILITIES) -> QuestionScope`; `missing_strip_cells(state, listing) -> tuple[ScopeCell, ...]`; `strip_chip_label(cell) -> str`.
- Produces (`question_scope.py`): `token_scope(cells, active) -> QuestionScope` (slice fixed to `WHOLE_BRANCH`); `pin_or_none(scope) -> QuestionScope | None`; `snapshot_pin_for_send(active, attached) -> QuestionScope`; `scope_caption_text(scope, *, from_question=False)`; `MODEL_NOTE_SLICE_WORDS` / `MODEL_NOTE_CODE_WORDS` (today's label bytes, for `scope_prefix` only); `ordered_unique`.
- Deletes: `pin_summary_label` (its only caller was the popover button label) and its test lines.
- Satisfies: AC-35; the caption half of AC-39; AC-30 (D14 words: the session target is the strip); AC-28 stays green through the model-note tables.

- [ ] **Step 1: Write the failing tests**

In `tests/harness/ask_your_docs/test_question_scope.py`, drop `pin_summary_label` from the `question_scope` import and add `token_scope, MODEL_NOTE_SLICE_WORDS, SLICE_LABELS`; add `from pydocs_mcp.harness.ask_your_docs.strip_state import StripState, StripTarget, compile_strip_scope, initial_strip_state, missing_strip_cells`. Replace `TestPrefix::test_caption_and_summary` with:

```python
    def test_caption_reads_searched_in(self):
        """AC-39 (caption half): the transcript caption names the cells, pipe-separated per project."""
        assert scope_caption_text(_PIN) == (
            "searched in: backend · main, feature/retry · only the changes themselves"
        )
        two = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("a", "main"), ScopeCell("b", "main")))
        assert scope_caption_text(two) == "searched in: a · main | b · main"
        assert scope_caption_text(two, from_question=True) == (
            "searched in: a · main | b · main (from your question)"
        )
        bare = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("tooling", ""),))
        assert scope_caption_text(bare, from_question=True) == "searched in: tooling (from your question)"
        assert scope_caption_text(None) == ""
        assert scope_caption_text(QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),))) == ""

    def test_model_note_bytes_do_not_follow_the_screen_words(self):
        """AC-28 stays: the screen tables change, the note's tables do not."""
        assert scope_prefix(_PIN) == (
            "[pinned scope: project=backend, branches=main, feature/retry, diff hunks, own code only] "
        )
        assert MODEL_NOTE_SLICE_WORDS[ScopeSlice.DIFF_HUNKS] == "diff hunks"
        assert SLICE_LABELS[ScopeSlice.DIFF_HUNKS] == "only the changes themselves"
```

Replace `TestAttachedSymbols::test_snapshot_drops_a_one_shot_pin_and_keeps_a_kept_one` and `::test_snapshot_folds_attached_cells_into_the_sent_scope_only` with:

```python
    def test_snapshot_sends_the_active_scope_grown_by_attached_cells_only(self):
        """AC-30 (D14): the strip is sticky, so a snapshot returns ONE scope — the active one,
        grown by attached cells for this send only. An attached cell separates "grew" from
        "passed through"."""
        defaults = resolve_question_scope_defaults(
            ScopeDefaultsConfig(), ScopeDefaultsOverride(), _LISTING
        )
        assert snapshot_pin_for_send(defaults, []) == defaults
        assert snapshot_pin_for_send(_PIN, []) == _PIN
        attached = [AttachedSymbol("mod.Foo", "tooling", "main")]
        sent = snapshot_pin_for_send(defaults, attached)
        assert sent.kind is ScopeKind.PIN and sent.cells == (ScopeCell("tooling", "main"),)
        assert snapshot_pin_for_send(_PIN, attached).cells == (*_PIN.cells, ScopeCell("tooling", "main"))

    def test_token_cells_become_a_one_shot_pin_carrying_the_more_values(self):
        """AC-40 (pure half): code / package ride from the active scope; the slice is
        ALWAYS the whole branch (owner decision D14 §4, spec §6.10a) — an active diff
        slice must not leak into a typed `in:` question."""
        active = QuestionScope(
            kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),), code=ScopeCode.OWN, package="fastapi"
        )
        scope = token_scope((ScopeCell("tooling", ""),), active)
        assert scope == QuestionScope(
            kind=ScopeKind.PIN, cells=(ScopeCell("tooling", ""),), code=ScopeCode.OWN, package="fastapi"
        )
        sliced = QuestionScope(
            kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),), slice=ScopeSlice.DIFF_HUNKS, code=ScopeCode.OWN
        )
        assert token_scope((ScopeCell("tooling", ""),), sliced).slice is ScopeSlice.WHOLE_BRANCH
```

Append:

```python
class TestStripScope:  # AC-35
    """``tooling`` is the target throughout: it is neither ``ScopeDefaultsConfig().project``
    (``any``) nor ``_LISTING``'s first row, so DEFAULT and PIN have different cells and a
    "first project wins" mutant is caught."""

    _CONFIG = ScopeDefaultsConfig()

    @staticmethod
    def _target(project: str, *branches: str) -> StripTarget:
        return StripTarget(project, branches)

    def test_no_target_is_todays_default_path_byte_identical(self):
        # The R11 fence: the strip's empty state IS resolve_question_scope_defaults.
        expected = resolve_question_scope_defaults(self._CONFIG, ScopeDefaultsOverride(), _LISTING)
        assert compile_strip_scope((), False, self._CONFIG, _LISTING) == expected
        assert compile_strip_scope((), True, self._CONFIG, _LISTING) == expected  # no row 2: the box is moot

    def test_no_target_means_the_union_even_when_yaml_names_a_project(self):
        # "Clear" empties the strip; the YAML project is the INITIAL state (Reset), not a floor.
        scope = compile_strip_scope((), False, ScopeDefaultsConfig(project="backend"), _LISTING)
        assert scope.kind is ScopeKind.DEFAULT and scope.cells == (ScopeCell("", ""),)

    def test_one_target_only_these_off_is_a_soft_default_for_that_project(self):
        scope = compile_strip_scope((self._target("tooling", "main"),), False, self._CONFIG, _LISTING)
        assert scope.kind is ScopeKind.DEFAULT
        assert scope.cells == (ScopeCell("tooling", ""),)  # the branch is resolved per call
        assert (scope.branch_default, scope.branch_name) == (ScopeBranchDefault.BASE, "")

    def test_one_target_only_these_on_pins_that_one_cell(self):
        scope = compile_strip_scope((self._target("tooling", "main"),), True, self._CONFIG, _LISTING)
        assert scope.kind is ScopeKind.PIN and scope.cells == (ScopeCell("tooling", "main"),)

    # Listing order is dict insertion order (catalog.py `project_names`), and _LISTING's
    # (backend, tooling) is ALSO alphabetical — so a compiler that sorts by name would
    # pass against it by luck. This listing lists tooling FIRST: the three candidate
    # orders now all differ — listing (tooling, backend) ≠ sorted (backend, tooling) ≠
    # the strip's insertion order below (backend, tooling).
    _REVERSED = WorkspaceBranchListing(
        projects={"tooling": _LISTING.rows("tooling"), "backend": _LISTING.rows("backend")}
    )

    def test_two_targets_pin_in_listing_order_whatever_the_checkbox_holds(self):
        targets = (self._target("backend", "feature/x"), self._target("tooling", "main"))
        for only_these in (False, True):
            scope = compile_strip_scope(targets, only_these, self._CONFIG, self._REVERSED)
            assert scope.kind is ScopeKind.PIN
            # tooling first: the LISTING order, which is neither sorted nor as inserted.
            assert scope.cells == (ScopeCell("tooling", "main"), ScopeCell("backend", "feature/x"))

    def test_one_target_with_two_branches_pins_two_cells(self):
        scope = compile_strip_scope((self._target("backend", "feature/x", "main"),), False, self._CONFIG, _LISTING)
        assert scope.kind is ScopeKind.PIN
        assert scope.cells == (ScopeCell("backend", "feature/x"), ScopeCell("backend", "main"))

    def test_a_target_with_no_branch_rows_pins_the_bare_project(self):
        scope = compile_strip_scope((StripTarget("tooling", ()),), True, self._CONFIG, _LISTING)
        assert scope.cells == (ScopeCell("tooling", ""),)

    def test_more_values_ride_through_every_case(self):
        more = ScopeDefaultsOverride(code=ScopeCode.OWN, package="fastapi")
        soft = compile_strip_scope((), False, self._CONFIG, _LISTING, more=more)
        one = compile_strip_scope((self._target("tooling", "main"),), False, self._CONFIG, _LISTING, more=more)
        pinned = compile_strip_scope((self._target("tooling", "main"),), True, self._CONFIG, _LISTING, more=more)
        for scope in (soft, one, pinned):
            assert (scope.code, scope.package) == (ScopeCode.OWN, "fastapi")
        # E11 still applies under PIN: a slice never combines with dependencies-only.
        sliced = ScopeDefaultsOverride(code=ScopeCode.DEPS, slice=ScopeSlice.DIFF_HUNKS)
        assert compile_strip_scope((self._target("tooling", "main"),), True, self._CONFIG, _LISTING, more=sliced).code is ScopeCode.ALL

    def test_initial_state_seeds_from_yaml(self, caplog):
        assert initial_strip_state(self._CONFIG, _LISTING) == StripState()
        named = initial_strip_state(ScopeDefaultsConfig(project="backend"), _LISTING)
        # The STAMPED row (feature/x), not the base (main): the fixture disagrees on purpose.
        assert named.targets == (StripTarget("backend", ("feature/x",)),)
        assert named.only_these is False and named.more == ScopeDefaultsOverride()  # "More" starts at the YAML values
        with caplog.at_level("INFO"):
            unknown = initial_strip_state(ScopeDefaultsConfig(project="frontend"), _LISTING)
        assert unknown.targets == ()  # an unlisted YAML project seeds no target (AC-3's rule)…
        record = json.loads(caplog.records[-1].getMessage())  # …and still logs it, as the per-call path does
        assert (record["event"], record["argument"], record["passed"], record["replacement"]) == (
            "scope_default_replaced", "project", "frontend", ""
        )

    def test_state_edits_are_per_cell(self):
        state = StripState(
            (StripTarget("backend", ("feature/x", "main")), StripTarget("tooling", ("main",))), only_these=True
        )
        assert state.without_cell(ScopeCell("backend", "main")).targets == (
            StripTarget("backend", ("feature/x",)),
            StripTarget("tooling", ("main",)),
        )
        assert state.without_cell(ScopeCell("tooling", "main")).targets == (
            StripTarget("backend", ("feature/x", "main")),
        )
        assert state.without_cell(ScopeCell("tooling", "main")).only_these is True  # the user's tick survives an edit
        assert state.with_target(StripTarget("backend", ("main",))) == state  # already there
        assert state.with_target(StripTarget("backend", ("release",))).targets[0] == (
            StripTarget("backend", ("feature/x", "main", "release"))
        )
        cleared = state.cleared()
        assert cleared.targets == () and cleared.more == state.more
        assert cleared.only_these is False  # no target: the tick is moot and does not linger

    def test_missing_cells_name_only_what_the_listing_lacks(self):
        state = StripState((StripTarget("backend", ("feature/x", "gone")), StripTarget("frontend", ("main",))))
        assert missing_strip_cells(state, _LISTING) == (
            ScopeCell("backend", "gone"),
            ScopeCell("frontend", "main"),
        )
```

(`json` joins the test module's imports.) Append to `tests/harness/ask_your_docs/test_scope_pin.py`, beside `test_scope_pin_module_stays_langchain_free`, the same subprocess pin for `pydocs_mcp.harness.ask_your_docs.strip_state` (assert no `langchain*` / `streamlit*` module is imported). Append to `tests/harness/ask_your_docs/test_scope_interceptor.py` (import `StripTarget, compile_strip_scope` from `strip_state` and `ScopeDefaultsConfig` from `pydocs_mcp.retrieval.config.ask_your_docs_models`):

```python
def test_ac35_five_target_strip_is_refused_before_any_call():
    """The strip's cap is the interceptor's cap: five cells, max_cells 4, zero handler calls."""
    listing = WorkspaceBranchListing(projects={p: (_row("main", default=True),) for p in "abcde"})
    targets = tuple(StripTarget(p, ("main",)) for p in "abcde")
    scope = compile_strip_scope(targets, False, ScopeDefaultsConfig(), listing)
    assert scope.kind is ScopeKind.PIN and len(scope.cells) == 5
    handler = RecordingHandler()
    runtime = ScopeRuntime(listing=listing, capabilities=NO_SCOPE_CAPABILITIES, max_cells=4)
    with active(scope, runtime):
        result = call("get_overview", {}, handler)
    assert handler.sent == [] and result.isError is True
    assert "ask_your_docs.scope.max_cells" in result.content[0].text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $WT && .venv/bin/pytest tests/harness/ask_your_docs/test_question_scope.py tests/harness/ask_your_docs/test_scope_interceptor.py tests/harness/ask_your_docs/test_scope_pin.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pydocs_mcp.harness.ask_your_docs.strip_state'` and `ImportError: cannot import name 'token_scope' from 'pydocs_mcp.harness.ask_your_docs.question_scope'`.

- [ ] **Step 3: Re-word the screen tables and keep the model note's bytes**

In `question_scope.py`, replace the two label tables with four:

```python
# The model-facing "[pinned scope: …]" note keeps today's words — AC-28 pins its bytes
# and it is never on screen (D14 §6). Only scope_prefix reads these two tables.
MODEL_NOTE_SLICE_WORDS: dict[ScopeSlice, str] = {
    ScopeSlice.WHOLE_BRANCH: "whole branch",
    ScopeSlice.CHANGED_FILES: "changed files",
    ScopeSlice.DIFF_HUNKS: "diff hunks",
}
MODEL_NOTE_CODE_WORDS: dict[ScopeCode, str] = {
    ScopeCode.ALL: "all code",
    ScopeCode.OWN: "own code only",
    ScopeCode.DEPS: "dependencies only",
}
# On-screen words (D14 vocabulary): the picker, the footer, the caption.
SLICE_LABELS: dict[ScopeSlice, str] = {
    ScopeSlice.WHOLE_BRANCH: "everything on the branch",
    ScopeSlice.CHANGED_FILES: "only files this branch changed",
    ScopeSlice.DIFF_HUNKS: "only the changes themselves",
}
CODE_LABELS: dict[ScopeCode, str] = {
    ScopeCode.ALL: "project code and dependencies",
    ScopeCode.OWN: "project code only",
    ScopeCode.DEPS: "dependencies only",
}
```

and in `scope_prefix` use `MODEL_NOTE_SLICE_WORDS[scope.slice]` / `MODEL_NOTE_CODE_WORDS[scope.code]`. The `ScopeDefaultsOverride` docstring becomes `"""The strip's "More" values for the session; ``None`` = use YAML."""`.

- [ ] **Step 4: Create `strip_state.py` — the strip value objects and the compiler**

In `question_scope.py`, rename `_ordered_unique` → `ordered_unique` (every in-file caller follows; it joins `__all__`). Then create `python/pydocs_mcp/harness/ask_your_docs/strip_state.py`:

```python
"""The "Searching in …" strip's state and its compiler (UI spec §6.1, §6.7, AC-35).

Pure by contract — no streamlit, no langchain (a subprocess pin holds it). The
strip is the page's sticky "where to search"; ``compile_strip_scope`` is the ONE
way it becomes a ``QuestionScope``.

Example:
    scope = compile_strip_scope((StripTarget("backend", ("main",)),), True, config, listing)
    scope.kind is ScopeKind.PIN and scope.cells == (ScopeCell("backend", "main"),)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    ANY_PROJECT,
    QuestionScope,
    ScopeCell,
    ScopeDefaultsOverride,
    ScopeKind,
    code_compatible_with_slice,
    log_scope_event,
    ordered_unique,
    resolve_question_scope_defaults,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig


@dataclass(frozen=True, slots=True)
class StripTarget:
    """One ticked project in the "Searching in" strip and its chosen branches.

    On U0 the picker fills the listing's STAMPED branch (informational: the
    interceptor never sends it while ``branch_selector`` is off); ``()`` is a
    bundle without branch rows, which pins the bare project.
    """

    project: str
    branches: tuple[str, ...] = ()

    def cells(self) -> tuple[ScopeCell, ...]:
        return tuple(ScopeCell(self.project, b) for b in self.branches) or (
            ScopeCell(self.project, ""),
        )


@dataclass(frozen=True, slots=True)
class StripState:
    """The sticky strip (UI spec §6.7): targets, the user's own "Only these" tick,
    and the picker's "More" values.

    ``only_these`` is what the person ticked — held here, not on the widget key,
    so it survives a page that does not render the strip (Streamlit drops the
    keys of unrendered widgets). The forced-on state at two or more cells is a
    rendering rule (§6.1, E16) and is never written into this field.
    """

    targets: tuple[StripTarget, ...] = ()
    only_these: bool = False
    more: ScopeDefaultsOverride = ScopeDefaultsOverride()

    def projects(self) -> tuple[str, ...]:
        return tuple(t.project for t in self.targets)

    def with_target(self, target: StripTarget) -> StripState:
        """This state plus ``target``; an existing project gains the new branches only."""
        mine = next((t for t in self.targets if t.project == target.project), None)
        if mine is None:
            return replace(self, targets=(*self.targets, target))
        grown = StripTarget(mine.project, ordered_unique((*mine.branches, *target.branches)))
        return replace(self, targets=tuple(grown if t is mine else t for t in self.targets))

    def without_cell(self, cell: ScopeCell) -> StripState:
        """This state minus one chip; a project whose last branch goes leaves the strip."""
        kept: list[StripTarget] = []
        for target in self.targets:
            if target.project != cell.project:
                kept.append(target)
                continue
            rest = tuple(b for b in target.branches if b != cell.branch)
            if rest:
                kept.append(StripTarget(target.project, rest))
        return replace(self, targets=tuple(kept))

    def cleared(self) -> StripState:
        """No target and no "Only these" (moot without a target); "More" survives a Clear."""
        return StripState(more=self.more)


def strip_cells(targets: Sequence[StripTarget]) -> tuple[ScopeCell, ...]:
    return ordered_unique(cell for target in targets for cell in target.cells())


def ordered_targets(
    targets: Sequence[StripTarget], listing: WorkspaceBranchListing
) -> tuple[StripTarget, ...]:
    """Listing order (spec §6.1: fan-out runs in listing order); unknown projects last."""
    names = listing.project_names
    return tuple(sorted(targets, key=lambda t: names.index(t.project) if t.project in names else len(names)))


def strip_target_for(project: str, listing: WorkspaceBranchListing) -> StripTarget:
    """A target on the project's stamped row — the only branch U0 can show."""
    row = listing.default_row(project)
    return StripTarget(project, (row.name,) if row else ())


def initial_strip_state(config: ScopeDefaultsConfig, listing: WorkspaceBranchListing) -> StripState:
    """The YAML seed of the strip: ``project: any`` or an unlisted name = no target
    (the AC-3 replacement rule, applied at the strip instead of per call — and
    logged the same way, so an operator's typo in YAML is still visible)."""
    if config.project == ANY_PROJECT:
        return StripState()
    if not listing.knows_project(config.project):
        log_scope_event(
            "scope_default_replaced", tool="", argument="project", passed=config.project, replacement=""
        )
        return StripState()
    return StripState(targets=(strip_target_for(config.project, listing),))


def _soft_override(
    more: ScopeDefaultsOverride,
    targets: Sequence[StripTarget],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> ScopeDefaultsOverride:
    """The DEFAULT cases' override: no target -> the union; one target -> that
    project and, once the server takes a branch, the ONE branch the person picked
    (so the chip and the sent branch never disagree — U1, 12b); on U0 the branch
    stays YAML's, because nothing branch-shaped is sent anyway."""
    if not targets:
        return replace(more, project=ANY_PROJECT)
    target = targets[0]
    chosen = target.branches[0] if len(target.branches) == 1 else ""
    if capabilities.branch_selector and chosen and listing.has_branch(target.project, chosen):
        return replace(more, project=target.project, branch_name=chosen)
    return replace(more, project=target.project)


def compile_strip_scope(
    targets: Sequence[StripTarget],
    only_these: bool,
    config: ScopeDefaultsConfig,
    listing: WorkspaceBranchListing,
    more: ScopeDefaultsOverride = ScopeDefaultsOverride(),
    capabilities: ScopeCapabilities = NO_SCOPE_CAPABILITIES,
) -> QuestionScope:
    """The strip -> the engine (UI spec §6.1, AC-35).

    No target -> DEFAULT over the union (today's no-pin path, byte-identical);
    one cell with "Only these" off -> DEFAULT for that project (branch resolved
    per call; on U1 the picked branch rides as ``branch_name``); one cell with
    it on, or two or more cells -> PIN. Two or more cells imply PIN whatever
    the checkbox holds — the strip mirrors that by forcing the box on (§12 O10).
    """
    cells = strip_cells(ordered_targets(targets, listing))
    if not targets or (len(cells) == 1 and not only_these):
        override = _soft_override(more, targets, listing, capabilities)
        return resolve_question_scope_defaults(config, override, listing)
    soft = resolve_question_scope_defaults(config, replace(more, project=ANY_PROJECT), listing)
    return QuestionScope(
        kind=ScopeKind.PIN,
        cells=cells,
        slice=soft.slice,
        code=code_compatible_with_slice(soft.slice, soft.code),
        package=soft.package,
    )


def missing_strip_cells(state: StripState, listing: WorkspaceBranchListing) -> tuple[ScopeCell, ...]:
    """The cells a reloaded listing no longer has — dropped one by one (E12)."""
    return tuple(
        cell
        for cell in strip_cells(state.targets)
        if not listing.knows_project(cell.project)
        or (cell.branch and not listing.has_branch(cell.project, cell.branch))
    )


def strip_chip_label(cell: ScopeCell) -> str:
    return f"{cell.project} · {cell.branch} ✕" if cell.branch else f"{cell.project} ✕"


__all__ = (
    "StripState",
    "StripTarget",
    "compile_strip_scope",
    "initial_strip_state",
    "missing_strip_cells",
    "ordered_targets",
    "strip_cells",
    "strip_chip_label",
    "strip_target_for",
)
```

Then, in `question_scope.py` after `snapshot_pin_for_send`, add the two helpers that need nothing from the strip:

```python
def pin_or_none(scope: QuestionScope) -> QuestionScope | None:
    """The strip's pin for the chip derivation (§6.9); ``None`` under DEFAULT."""
    return scope if scope.kind is ScopeKind.PIN else None


def token_scope(cells: Sequence[ScopeCell], active: QuestionScope) -> QuestionScope:
    """A typed-token question's one-shot PIN (§6.10a): the token cells, code /
    package from the active scope (the picker's "More" values), and ALWAYS the
    whole branch as the slice (owner decision D14 §4) — a diff slice chosen in
    "More" belongs to the strip's own questions, never to a typed `in:` one."""
    return QuestionScope(
        kind=ScopeKind.PIN,
        cells=tuple(cells),
        slice=ScopeSlice.WHOLE_BRANCH,
        code=active.code,
        package=active.package,
    )
```

- [ ] **Step 5: The caption, the snapshot, and the deletion**

Replace `scope_caption_text`, delete `pin_summary_label`, and replace `snapshot_pin_for_send`:

```python
def scope_caption_text(scope: QuestionScope | None, *, from_question: bool = False) -> str:
    """The transcript caption above a pinned question: ``searched in: backend ·
    main, feature/retry | tooling · main``; ``(from your question)`` for tokens."""
    if scope is None or scope.kind is ScopeKind.DEFAULT:
        return ""
    groups: list[str] = []
    for project in scope.projects() or ("",):
        branches = scope.branches_for(project)
        label = project or "all projects"
        groups.append(f"{label} · {', '.join(branches)}" if branches else label)
    text = f"searched in: {' | '.join(groups)}"
    if scope.slice is not ScopeSlice.WHOLE_BRANCH:
        text = f"{text} · {SLICE_LABELS[scope.slice]}"
    return f"{text} (from your question)" if from_question else text


def snapshot_pin_for_send(
    active: QuestionScope, attached: Sequence[AttachedSymbol | str]
) -> QuestionScope:
    """The scope this question is sent under: the strip's active scope, grown by
    the attached symbols' cells for this send only (the strip itself is sticky
    and never grows by an attachment — UI spec §6.7, AC-30)."""
    return pin_with_attached_symbols(pin_or_none(active), attached, active) or active
```

Update `question_scope.__all__`: remove `pin_summary_label`; add `MODEL_NOTE_CODE_WORDS`, `MODEL_NOTE_SLICE_WORDS`, `ordered_unique`, `pin_or_none`, `token_scope`. Add `_HARNESS / "strip_state.py": 300,` and `_HARNESS / "question_scope.py": 400,` to `_BUDGETS`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd $WT && .venv/bin/pytest tests/harness/ask_your_docs/test_question_scope.py tests/harness/ask_your_docs/test_scope_interceptor.py tests/harness/ask_your_docs/test_scope_pin.py tests/harness/ask_your_docs/test_module_line_budgets.py tests/harness/ask_your_docs/test_prompt_seam.py tests/harness/ask_your_docs/test_image_attachment.py -q`
Expected: PASS — the last two are the AC-28 / AC-11 fences: `scope_prefix`'s bytes did not move. `test_answer_footer.py::TestFooter::test_distinct_slices_are_listed` now FAILS on the re-worded `SLICE_LABELS`; 11e rewrites it. `scope_panel.py` still imports `pin_summary_label` and breaks until 11c — 11b and 11c ship in consecutive commits; do not push between them.

- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/strip_state.py python/pydocs_mcp/harness/ask_your_docs/question_scope.py tests/harness/ask_your_docs/test_question_scope.py tests/harness/ask_your_docs/test_scope_interceptor.py tests/harness/ask_your_docs/test_scope_pin.py tests/harness/ask_your_docs/test_module_line_budgets.py
git commit -m "ask-your-docs: strip_state — the strip state compiles to the question scope"
```

---

### Task 11c: `scope_strip.py` + `scope_picker.py` — the strip and the picker replace the panel, the popover and the chip row

**Files:**
- Create: `python/pydocs_mcp/harness/ask_your_docs/scope_picker.py` (the keyed popover shared by both pages)
- Create: `python/pydocs_mcp/harness/ask_your_docs/scope_strip.py` (the strip, the composer row, the per-target drop)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/scope_panel.py` (**delete** `SOFT_DEFAULTS_CAPTION`, `_DEFAULTS_WIDGET_KEYS`, `_NAME_PREFIX`, `render_scope_defaults_button`, `_reset_defaults_widgets`, `_branch_option_labels`, `_render_branch_default_row`, `_render_package_picker`, `_render_code_radio`, `render_scope_defaults_panel`, `_apply_pin`, `_clear_pin`, `_render_pin_branches`, `_render_pin_controls`, `_render_pin_buttons`, `render_scope_pin_popover`, `render_composer_row`, `_pin_chips`, `_remove_pin_element`, `render_scope_chip_row`, `_missing_cell`, `drop_pin_if_listing_changed`, `_slice_options`, `_render_slice_radio`; **keep** `_symbol_of`, `render_follow_up_chips`, `GraphBranchSelection`, `render_graph_branch_row`; **rewrite** `_branch_caption` → public `branch_caption` with the D14 text; **add** `render_attachment_chip_row`)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/page_scope.py` (`answer_footer_and_chips` reads the strip, not `scope_pin`)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/app.py` (sidebar scope block deleted; the strip in `st.bottom`; the attachment row; the send path on the strip — no tokens yet, that is 11d)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/pages/2_Graph.py` (the two sidebar lines only, so the page keeps importing at this commit; 11f owns its tests and the row rule)
- Modify: `tests/harness/ask_your_docs/test_module_line_budgets.py` (`scope_strip.py: 300`, `scope_picker.py: 300`)
- Modify: `tests/harness/ask_your_docs/_fixture.py` (`make_bundle(..., packages: list[str] = ())` inserts one `packages` row per name beside `__project__` — `SqliteBundleReader.packages()` selects `WHERE name != '__project__'`, so without it the catalog's package pool is EMPTY and the picker's Package selectbox never renders)
- Test: `tests/harness/ask_your_docs/test_app_scope_states.py` (**rewritten whole**); `tests/harness/ask_your_docs/test_app_attachment.py` + `test_app_image_attachment.py` must stay green (they seed `attached=`, never a scope widget key)

**Interfaces:**
- Consumes: `StripState`, `StripTarget`, `compile_strip_scope`, `initial_strip_state`, `strip_cells`, `ordered_targets`, `strip_target_for`, `missing_strip_cells`, `strip_chip_label` (11b, `strip_state`); `pin_or_none`, `snapshot_pin_for_send`, `CODE_LABELS`, `SLICE_LABELS` (11b, `question_scope`); `WorkspaceBranchListing` (Task 3); `ScopeCapabilities` (Task 5); `ScopeDefaultsConfig` (11g).
- Produces (`scope_picker.py`): `PICKER_KEY = "scope_picker"`, `STRIP_STATE_KEY = "scope_strip"`, `ONLY_THESE_KEY = "scope_strip_only_these"`, `PICKER_TITLE = "Where to search"`, `seed_widget_once(key, value)`, `forget_picker_widgets()`, `render_where_to_search_picker(popover_key, label, state, config, catalog, listing, capabilities) -> None`.
- Produces (`scope_strip.py`): `NO_TARGET_SENTENCE`, `ONLY_THESE_LABEL`, `FORCED_HINT`, `OVER_CAP_HINT`, `CHANGE_LABEL = "Change…"`, `current_strip_state(config, listing) -> StripState`, `add_strip_target(target)`, `drop_missing_targets(listing, workspace)`, `render_where_to_search_strip(state, config, catalog, listing, capabilities)`, `render_composer_row(state, config, catalog, listing, capabilities) -> ChatInputValue | None`. "Only these" is read as `state.only_these` by the pages — there is no widget-key reader.
- The strip renders the same on every listing size: a one-bundle workspace with one target still shows the chip's `✕`, "Change…" and row 2 (spec §6.7's single-project paragraph: the one-cell "Only these" checkbox is the only way to reach `PIN` there, and "Change…" is the only way to reach "More"). Nothing collapses.
- Produces (`scope_panel.py`): `branch_caption(project, listing) -> str` (`indexed on <branch> @<sha7>`), `render_attachment_chip_row(attached) -> None`.
- Session keys after this task: `scope_strip: StripState`, `scope_strip_only_these` (widget), `scope_listing_workspace`, `scope_capabilities`, `attached`; widget keys `scope_picker` (popover), `scope_picker_project_<name>`, `scope_picker_branches_<name>` (U1, inactive), `scope_picker_code`, `scope_picker_package`, `scope_picker_files` (U2, inactive), `scope_picker_use`, `scope_picker_reset`, `scope_chip_<project>_<branch>`, `scope_strip_clear`, `chip_<symbol>`, `chip_clear`, `follow_up_<index>_<kind>`, `graph_where_to_search`. **Gone:** every `scope_defaults_*`, `scope_pin*`, `scope_chip_slice`, `scope_chip_project`, `scope_project` / `scope_code` / `scope_package`.
- The U1 branch pills and the U2 "Which files" radio are written here, inactive behind `ScopeCapabilities` (as Task 8 shipped the chips); Task 12b / Task 17 activate and extend them.
- Satisfies: AC-33, AC-42, AC-44, AC-48, AC-49, AC-50 (strip half), AC-52 (the single-project listing); the chip-removal intent of the 2026-09-04 tests; E12 (per-target drop — its AppTest is in 11d), E16.

- [ ] **Step 1: Write the failing tests**

Replace `tests/harness/ask_your_docs/test_app_scope_states.py` whole:

```python
"""AppTest smoke tests for the strip states and the picker — AC-33, 42, 44, 48, 49, 50.

Every test seeds ``scope_capabilities`` so the page never builds the agent (no serve
subprocess, no LLM client); ``page()`` adds the fake serve opener and the connection
seams every page test shares. Strip widgets live in ``st.bottom``, an anonymous block
outside ``at.main`` / ``at.sidebar`` — assert them through the FLAT accessors only.
Seed strip state BEFORE the first run: a widget that already holds a session value
ignores a changed default. Runs where the [harness-ask-your-docs] extra is installed
(the main checkout's venv, PYTHONPATH at the worktree), skipped elsewhere.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

from pydocs_mcp.harness.ask_your_docs.question_scope import ScopeCode
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.harness.ask_your_docs.scope_strip import (
    FORCED_HINT,
    NO_TARGET_SENTENCE,
    OVER_CAP_HINT,
)
from pydocs_mcp.harness.ask_your_docs.strip_state import StripState, StripTarget

from ._fixture import make_bundle

# page_env is autouse: importing it into this module is what arms it.
from ._page_fixtures import page, page_env

U1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)
BACKEND_MAIN, BACKEND_RETRY = "a" * 40, "b" * 40
TOOLING_MAIN, TOOLING_DEVELOP = "c" * 40, "d" * 40
TOOLING = ("tooling", "main")
BACKEND = ("backend", "feature/retry")
# The 2026-09-04 keys (AC-49): an absence test over the NEW keys would pass on an empty page.
OLD_KEYS = frozenset(
    {
        "scope_project",
        "scope_code",
        "scope_package",
        "scope_defaults_project",
        "scope_defaults_branch",
        "scope_defaults_slice",
        "scope_defaults_code",
        "scope_defaults_package",
        "scope_defaults_open",
        "scope_defaults_button",
        "scope_defaults_reset",
        "scope_pin_popover",
        "scope_pin_project",
        "scope_pin_branches",
        "scope_pin_slice",
        "scope_pin_keep",
        "scope_pin_apply",
        "scope_pin_clear",
        "scope_chip_slice",
        "scope_chip_project",
    }
)


@pytest.fixture
def workspace(tmp_path, page_env):
    """Two projects that DISAGREE on default vs base, listed backend then tooling.

    backend's stamped (default) row is ``feature/retry`` with base ``main``; tooling's
    stamped row is ``main``. A single seeded target is always ``tooling`` — never the
    listing's first row — and the two stamped rows differ from the two bases, so a
    strip that showed the base, or the first row, fails (the PR #267 fixture lesson).
    """
    make_bundle(
        tmp_path / "ws" / "backend_0123456789.db",
        project="backend",
        members=[("mod_a", "Foo", "class")],
        branches=[
            ("main", BACKEND_MAIN, None, 0, "active", None),
            ("feature/retry", BACKEND_RETRY, "main", 1, "active", None),
        ],
    )
    make_bundle(
        tmp_path / "ws" / "tooling_0123456789.db",
        project="tooling",
        members=[("mod_t", "Bar", "class")],
        packages=["fastapi"],  # a real dependency: the Package selectbox needs a non-empty pool
        branches=[
            ("main", TOOLING_MAIN, None, 1, "active", None),
            ("develop", TOOLING_DEVELOP, "main", 0, "active", None),
        ],
    )
    return tmp_path / "ws"


def _app(capabilities=NO_SCOPE_CAPABILITIES, **seeds):
    return page(scope_capabilities=capabilities, **seeds)


def _strip(*targets: tuple[str, str], only_these: bool = False) -> StripState:
    return StripState(targets=tuple(StripTarget(p, (b,)) for p, b in targets), only_these=only_these)


def _widget_keys(at) -> set[str]:
    widgets = [*at.button, *at.checkbox, *at.selectbox, *at.radio, *at.multiselect, *at.pills, *at.toggle]
    return {w.key for w in widgets if w.key}


def _chip_keys(at) -> set[str]:
    return {b.key for b in at.button if b.key and b.key.startswith("scope_chip_")}


def _captions(at) -> list[str]:
    return [c.value for c in at.caption]


class TestNoTarget:  # AC-49
    def test_default_view_has_no_sidebar_scope_widget_and_no_old_key(self, workspace):
        at = _app()
        at.run()
        assert not at.exception, at.exception
        assert not any(b.label in {"Scope defaults", "Where to search"} for b in at.sidebar.button)
        assert not (_widget_keys(at) & OLD_KEYS)
        assert any(m.value == NO_TARGET_SENTENCE for m in at.markdown)
        assert not any(c.key == "scope_strip_only_these" for c in at.checkbox)  # no row 2
        assert not any(b.key == "scope_strip_clear" for b in at.button)
        assert len(at.chat_input) == 1  # a bare composer: no popover column beside it
        # V2/V3: the keyed popover registered its open flag and its body rendered.
        assert "scope_picker" in at.session_state
        assert any(b.key == "scope_picker_use" for b in at.button)


class TestPicker:  # AC-48, AC-44, AC-33
    def test_one_row_per_project_in_listing_order_and_no_branch_control_on_u0(self, workspace):
        at = _app()
        at.run()
        assert not at.exception, at.exception
        rows = [c.key for c in at.checkbox if c.key and c.key.startswith("scope_picker_project_")]
        assert rows == ["scope_picker_project_backend", "scope_picker_project_tooling"]
        assert not any(p.key and p.key.startswith("scope_picker_branches_") for p in at.pills)
        assert any(r.key == "scope_picker_code" for r in at.radio)
        assert not any(r.key == "scope_picker_files" for r in at.radio)
        # Tick tooling (not the first row): its STAMPED row is a read-only caption.
        at.checkbox(key="scope_picker_project_tooling").check().run()
        assert not at.exception, at.exception
        assert f"indexed on main @{TOOLING_MAIN[:7]}" in _captions(at)
        assert f"indexed on feature/retry @{BACKEND_RETRY[:7]}" not in _captions(at)  # unticked

    def test_use_these_keeps_every_ticked_row_and_closes_the_picker(self, workspace):
        at = _app()
        at.run()
        # tooling FIRST, backend second: a picker that keeps only the last tick, or that
        # stores insertion order, fails against the listing-ordered expectation.
        at.checkbox(key="scope_picker_project_tooling").check().run()
        at.checkbox(key="scope_picker_project_backend").check().run()
        at.button(key="scope_picker_use").click().run()
        assert not at.exception, at.exception
        assert at.session_state["scope_strip"].targets == (
            StripTarget("backend", ("feature/retry",)),
            StripTarget("tooling", ("main",)),
        )
        assert at.session_state["scope_picker"] is False  # closed by the callback
        assert _chip_keys(at) == {"scope_chip_backend_feature/retry", "scope_chip_tooling_main"}

    def test_package_hides_when_code_is_project_only(self, workspace):
        """The pool is the catalog's dependency packages — `SqliteBundleReader.packages()`
        filters `__project__` OUT, so the fixture's `packages=["fastapi"]` row on tooling is
        what makes the positive half true; without it no selectbox renders at all and the
        negative half would prove nothing."""
        at = _app()
        at.run()
        package = at.selectbox(key="scope_picker_package")
        assert list(package.options) == ["All packages", "fastapi"]
        at.radio(key="scope_picker_code").set_value(ScopeCode.OWN).run()
        assert not at.exception, at.exception
        assert not any(s.key == "scope_picker_package" for s in at.selectbox)

    def test_branch_pills_render_only_when_advertised(self, workspace):
        """The U1 dormancy pin: the same page, U1 fake capabilities, pills over the
        PICKABLE names defaulting to the target's branch."""
        at = _app(U1, scope_strip=_strip(TOOLING))
        at.run()
        assert not at.exception, at.exception
        pills = at.pills(key="scope_picker_branches_tooling")
        assert list(pills.options) == ["main", "develop"]
        assert list(pills.value) == ["main"]
        assert not any(p.key == "scope_picker_branches_backend" for p in at.pills)  # unticked
        assert f"indexed on main @{TOOLING_MAIN[:7]}" not in _captions(at)  # pills, not the caption

    def test_preview_counts_cells_and_disables_use_these_past_the_cap(self, workspace, monkeypatch):
        """AC-44 with the cap lowered to 1 by the env layer (AppConfig reads it): two
        targets exceed it, the caption names the YAML key, and nothing else changes."""
        monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__SCOPE__MAX_CELLS", "1")
        at = _app(scope_strip=_strip(TOOLING, BACKEND))
        at.run()
        assert not at.exception, at.exception
        assert (
            "Next question runs 2 searches: backend · feature/retry, tooling · main · 2 of 1"
            in _captions(at)
        )
        assert at.button(key="scope_picker_use").disabled is True
        assert any("ask_your_docs.scope.max_cells" in c for c in _captions(at))
        assert "2 searches per question · limit 1" in _captions(at)

    def test_preview_with_one_target_is_enabled(self, workspace):
        at = _app(scope_strip=_strip(TOOLING))
        at.run()
        assert "Next question runs 1 search: tooling · main · 1 of 4" in _captions(at)
        assert at.button(key="scope_picker_use").disabled is False

    def test_reset_restores_the_yaml_values(self, workspace):
        """AC-33: starts AWAY from the shipped values (a target + code OWN)."""
        at = _app(scope_strip=_strip(TOOLING))
        at.run()
        at.radio(key="scope_picker_code").set_value(ScopeCode.OWN).run()
        assert at.radio(key="scope_picker_code").value is ScopeCode.OWN
        at.button(key="scope_picker_reset").click().run()
        assert not at.exception, at.exception
        assert at.radio(key="scope_picker_code").value is ScopeCode.ALL
        assert at.session_state["scope_strip"] == StripState()  # YAML project: any
        assert at.checkbox(key="scope_picker_project_tooling").value is False
        assert any(m.value == NO_TARGET_SENTENCE for m in at.markdown)


class TestStrip:  # AC-42, AC-50
    def test_one_target_row_2_is_editable(self, workspace):
        at = _app(scope_strip=_strip(TOOLING))
        at.run()
        assert not at.exception, at.exception
        box = at.checkbox(key="scope_strip_only_these")
        assert box.value is False and box.disabled is False
        assert _chip_keys(at) == {"scope_chip_tooling_main"}
        assert any(b.key == "scope_strip_clear" for b in at.button)
        assert not any("searches per question" in c for c in _captions(at))
        assert FORCED_HINT not in _captions(at)

    def test_ticking_only_these_lives_in_the_strip_state(self, workspace):
        """The tick is STATE, not a widget key: it survives a page that never renders the
        strip (the graph page), where Streamlit drops the key. Seeded off, so a page that
        forgot the on_change write-back fails here."""
        at = _app(scope_strip=_strip(TOOLING))
        at.run()
        at.checkbox(key="scope_strip_only_these").check().run()
        assert not at.exception, at.exception
        assert at.session_state["scope_strip"].only_these is True
        at.run()  # a plain rerun re-seeds the box from the state
        assert at.checkbox(key="scope_strip_only_these").value is True
        at.button(key="scope_strip_clear").click().run()
        assert at.session_state["scope_strip"].only_these is False  # Clear resets the tick too

    def test_two_targets_force_only_these_on_and_disabled(self, workspace):
        """AC-42: the box is seeded OFF (state AND widget key) — a mutant that drops the
        forcing keeps it off. The forced value must NOT reach the state."""
        at = _app(scope_strip=_strip(TOOLING, BACKEND), scope_strip_only_these=False)
        at.run()
        assert not at.exception, at.exception
        box = at.checkbox(key="scope_strip_only_these")
        assert box.value is True and box.disabled is True
        assert at.session_state["scope_strip"].only_these is False  # a rendering rule, never persisted
        assert FORCED_HINT in _captions(at)
        assert "2 searches per question · limit 4" in _captions(at)
        assert _chip_keys(at) == {"scope_chip_backend_feature/retry", "scope_chip_tooling_main"}

    def test_removing_one_of_two_chips_keeps_the_other(self, workspace):
        """The chip removes ITS cell — a mutant clearing the whole strip passes the
        last-chip test below, not this one. Back at one cell the box shows the user's
        OWN choice (off), not the forced value the two-cell strip displayed."""
        at = _app(scope_strip=_strip(TOOLING, BACKEND))
        at.run()
        at.button(key="scope_chip_backend_feature/retry").click().run()
        assert not at.exception, at.exception
        assert at.session_state["scope_strip"].targets == (StripTarget("tooling", ("main",)),)
        assert _chip_keys(at) == {"scope_chip_tooling_main"}
        box = at.checkbox(key="scope_strip_only_these")
        assert box.value is False and box.disabled is False
        # The picker's rows follow the strip: backend is unticked again.
        assert at.checkbox(key="scope_picker_project_backend").value is False

    def test_an_over_cap_strip_says_so_on_row_2(self, workspace, monkeypatch):
        """A YAML cap lowered under an existing strip (the picker and the "Keep searching"
        chip both refuse to build one): every send would hit E4, so row 2 names the
        overflow instead of printing a count silently past the limit. Two targets against
        a cap of 1 — the fixture has two projects, which is all the rule needs."""
        monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__SCOPE__MAX_CELLS", "1")
        at = _app(scope_strip=_strip(TOOLING, BACKEND))
        at.run()
        assert not at.exception, at.exception
        assert f"2 searches exceed the limit of 1 (ask_your_docs.scope.max_cells) — {OVER_CAP_HINT}" in _captions(at)

    def test_removing_the_last_chip_returns_to_no_target(self, workspace):
        at = _app(scope_strip=_strip(TOOLING))
        at.run()
        at.button(key="scope_chip_tooling_main").click().run()
        assert not at.exception, at.exception
        assert at.session_state["scope_strip"].targets == ()
        assert any(m.value == NO_TARGET_SENTENCE for m in at.markdown)

    def test_clear_empties_the_strip(self, workspace):
        at = _app(scope_strip=_strip(TOOLING, BACKEND))
        at.run()
        at.button(key="scope_strip_clear").click().run()
        assert not at.exception, at.exception
        assert at.session_state["scope_strip"].targets == ()
        assert _chip_keys(at) == set()

    def test_transcript_caption_survives_a_rerun(self, workspace):
        """AC-50 (caption half): a stored caption re-renders above its question."""
        caption = "searched in: backend · feature/retry | tooling · main"
        at = _app(
            scope_strip=_strip(TOOLING, BACKEND),
            messages=[{"role": "user", "text": "what is Foo?", "scope_caption": caption}],
            history=[],
        )
        at.run()
        assert not at.exception, at.exception
        assert caption in _captions(at)


@pytest.fixture
def single_workspace(tmp_path, page_env):
    """ONE bundle, one project — the listing the picker cannot make plural.

    What this fixture must NOT be (the PR #267 degenerate-fixture lesson): it is not the
    two-project ``workspace`` with one bundle deleted and it never seeds a second target,
    because on a one-project listing "all projects" and "only solo" cover the SAME corpus —
    the wording and the compiled kind are the only things that tell them apart, so the
    assertions below are on those, never on a result set. Its stamped (default) row is
    ``feature/solo``, not ``main`` and not the YAML default project, so a strip that printed
    the base name, the alphabetically-first row or the YAML default fails; and the box is
    seeded OFF, so AC-42's two-target forcing rule cannot make the editable-row-2
    assertion pass by accident.
    """
    make_bundle(
        tmp_path / "ws" / "solo_0123456789.db",
        project="solo",
        members=[("mod_s", "Baz", "class")],
        branches=[
            ("main", SOLO_MAIN, None, 0, "active", None),
            ("feature/solo", SOLO_FEATURE, "main", 1, "active", None),
        ],
    )
    return tmp_path / "ws"


class TestSingleProjectListing:  # AC-52
    def test_no_target_then_one_target_keeps_every_strip_control(self, single_workspace):
        """Nothing collapses on a one-bundle workspace: the sentence with no target, then a
        chip with its ✕, an EDITABLE row 2 and "Clear" — and NO count line, because a
        "searches per question" caption is AC-50's two-target shape."""
        at = _app()
        at.run()
        assert not at.exception, at.exception
        assert any(m.value == NO_TARGET_SENTENCE for m in at.markdown)
        assert not any(c.key == "scope_strip_only_these" for c in at.checkbox)  # no row 2

        at = _app(scope_strip=_strip(SOLO), scope_strip_only_these=False)
        at.run()
        assert not at.exception, at.exception
        assert _chip_keys(at) == {"scope_chip_solo_feature/solo"}
        box = at.checkbox(key="scope_strip_only_these")
        assert box.value is False and box.disabled is False  # never forced at one cell
        assert any(b.key == "scope_strip_clear" for b in at.button)
        assert not any("searches per question" in c for c in _captions(at))
        assert FORCED_HINT not in _captions(at)

    def test_ticking_only_these_compiles_to_a_one_cell_pin(self, single_workspace):
        """The tick is the ONLY way to reach PIN here (§6.7's single-project paragraph),
        so the assertion is on the compiled kind and cells — a compiler that answered
        DEFAULT for a lone project would search the same corpus and hide the bug."""
        at = _app(scope_strip=_strip(SOLO), scope_strip_only_these=False)
        at.run()
        at.checkbox(key="scope_strip_only_these").check().run()
        assert not at.exception, at.exception
        state = at.session_state["scope_strip"]
        assert state.only_these is True
        scope = compile_strip_scope(
            state, ScopeDefaultsConfig(), load_branch_listing(str(single_workspace))
        )
        assert scope.kind is ScopeKind.PIN
        assert scope.cells == (ScopeCell("solo", "feature/solo"),)
```

(The AC-52 block adds `SOLO_MAIN, SOLO_FEATURE = "e" * 40, "f" * 40` and `SOLO = ("solo", "feature/solo")` beside the other sha constants, and four imports: `compile_strip_scope` from `strip_state`, `ScopeCell` / `ScopeKind` from `question_scope`, `ScopeDefaultsConfig` from `pydocs_mcp.retrieval.config.ask_your_docs_models`, `load_branch_listing` from `page_scope`.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $WT && PYTHONPATH=$WT/python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest -o pythonpath=$WT/python tests/harness/ask_your_docs/test_app_scope_states.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pydocs_mcp.harness.ask_your_docs.scope_strip'`.

- [ ] **Step 3: Write `scope_picker.py`**

```python
"""The "Where to search" picker — ONE keyed popover shared by the chat page (opened by
the strip's "Change…") and the graph page (its sidebar button), with the same widget
keys (UI spec §6.7, §6.10, §6.11).

Streamlit-only; the decisions are question_scope's. Every write to the strip or to a
picker widget's key happens in an ``on_click`` callback — the only place a widget's
own key may be written, because callbacks run before the next run instantiates it.
"""

from __future__ import annotations

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from dataclasses import replace

from pydocs_mcp.harness.ask_your_docs.question_scope import (
    CODE_LABELS,
    SLICE_LABELS,
    ScopeCode,
    ScopeDefaultsOverride,
    ScopeSlice,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities
from pydocs_mcp.harness.ask_your_docs.scope_panel import branch_caption
from pydocs_mcp.harness.ask_your_docs.strip_state import (
    StripState,
    StripTarget,
    initial_strip_state,
    ordered_targets,
    strip_cells,
    strip_target_for,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

PICKER_KEY = "scope_picker"  # the chat page's popover; the graph page uses its own key
STRIP_STATE_KEY = "scope_strip"
ONLY_THESE_KEY = "scope_strip_only_these"
PICKER_TITLE = "Where to search"
USE_THESE_LABEL = "Use these"
RESET_LABEL = "Reset"
_WIDGET_PREFIX = "scope_picker_"


def forget_picker_widgets() -> None:
    """Pop every picker widget key so the next run re-seeds them from the strip and
    the YAML. Callbacks only."""
    for key in [k for k in st.session_state if str(k).startswith(_WIDGET_PREFIX)]:
        st.session_state.pop(key, None)


def seed_widget_once(key: str, value: object) -> None:
    """Seed a widget's key BEFORE it instantiates (the strip's row 2 uses it too)."""
    # setdefault, never ``value=``: a key already set through session state plus a
    # default is Streamlit's "created with a default value but also set via the
    # Session State API" warning, and the default would be ignored anyway.
    st.session_state.setdefault(key, value)


_seed = seed_widget_once  # the short local spelling below


def _pick(override, default):
    return default if override is None else override


# --- project rows ------------------------------------------------------------


def _render_branch_row(
    name: str,
    target: StripTarget | None,
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> StripTarget:
    if not capabilities.branch_selector:  # U0: informational — nothing branch-shaped is sent
        st.caption(branch_caption(name, listing))
        return strip_target_for(name, listing)
    names = [r.name for r in listing.pickable(name)]  # U2 (Task 17): the merged group follows
    key = f"{_WIDGET_PREFIX}branches_{name}"
    wanted = list(target.branches) if target else list(strip_target_for(name, listing).branches)
    _seed(key, [b for b in wanted if b in names])
    # A closed list by construction: pills accept no free text (R6).
    picked = st.pills("Branches", names, selection_mode="multi", key=key)
    return StripTarget(name, tuple(picked))


def _render_project_rows(
    state: StripState, listing: WorkspaceBranchListing, capabilities: ScopeCapabilities
) -> tuple[StripTarget, ...]:
    current = {t.project: t for t in state.targets}
    ticked: list[StripTarget] = []
    for name in listing.project_names:
        key = f"{_WIDGET_PREFIX}project_{name}"
        _seed(key, name in current)
        if st.checkbox(name, key=key):
            ticked.append(_render_branch_row(name, current.get(name), listing, capabilities))
    return tuple(ticked)


# --- "More" ------------------------------------------------------------------


def _render_code_radio(more: ScopeDefaultsOverride, config: ScopeDefaultsConfig) -> ScopeCode:
    key = f"{_WIDGET_PREFIX}code"
    _seed(key, _pick(more.code, config.code))
    return ScopeCode(st.radio("Code", list(ScopeCode), format_func=CODE_LABELS.get, horizontal=True, key=key))


def _render_package_picker(
    projects: tuple[str, ...],
    code: ScopeCode,
    catalog: dict[str, list[str]],
    more: ScopeDefaultsOverride,
    config: ScopeDefaultsConfig,
) -> str:
    if code is ScopeCode.OWN:  # packages are dependencies (today's rule, app.py:140-142)
        return ""
    pool = sorted({p for name, pkgs in catalog.items() if not projects or name in projects for p in pkgs})
    if not pool:
        return ""
    key = f"{_WIDGET_PREFIX}package"
    wanted = _pick(more.package, config.package)
    _seed(key, wanted if wanted in pool else "")
    return st.selectbox("Package", ["", *pool], format_func=lambda p: p or "All packages", key=key)


def _slice_options(capabilities: ScopeCapabilities) -> list[ScopeSlice]:
    options = [ScopeSlice.WHOLE_BRANCH]
    if capabilities.changed_slice:
        options.append(ScopeSlice.CHANGED_FILES)
    if capabilities.diff_slice:
        options.append(ScopeSlice.DIFF_HUNKS)
    return options


def _render_files_radio(
    more: ScopeDefaultsOverride,
    config: ScopeDefaultsConfig,
    capabilities: ScopeCapabilities,
    *,
    disabled: bool,
) -> ScopeSlice:
    """"Which files" — absent until the server advertises a slice value (U2);
    disabled (and everything-on-the-branch) while code is dependencies-only (E11)."""
    options = _slice_options(capabilities)
    if len(options) == 1:
        return ScopeSlice.WHOLE_BRANCH
    key = f"{_WIDGET_PREFIX}files"
    wanted = _pick(more.slice, config.slice)
    _seed(key, wanted if wanted in options else ScopeSlice.WHOLE_BRANCH)
    picked = st.radio("Which files", options, format_func=SLICE_LABELS.get, key=key, disabled=disabled)
    return ScopeSlice.WHOLE_BRANCH if disabled else ScopeSlice(picked)


def _render_more(
    state: StripState,
    config: ScopeDefaultsConfig,
    catalog: dict[str, list[str]],
    ticked: tuple[StripTarget, ...],
    capabilities: ScopeCapabilities,
) -> ScopeDefaultsOverride:
    with st.expander("More"):
        code = _render_code_radio(state.more, config)
        package = _render_package_picker(tuple(t.project for t in ticked), code, catalog, state.more, config)
        slice_value = _render_files_radio(state.more, config, capabilities, disabled=code is ScopeCode.DEPS)
    return ScopeDefaultsOverride(slice=slice_value, code=code, package=package)


# --- preview + buttons -------------------------------------------------------


def _use_these(popover_key: str, targets: tuple[StripTarget, ...], more: ScopeDefaultsOverride) -> None:
    # on_click: every ticked row is KEPT (never replaced) and the user's "Only these"
    # tick survives; the popover's own key is writable here because the callback runs
    # before the rerun (V2).
    current = st.session_state.get(STRIP_STATE_KEY, StripState())
    st.session_state[STRIP_STATE_KEY] = replace(current, targets=targets, more=more)
    st.session_state.pop(ONLY_THESE_KEY, None)  # row 2 re-seeds the box from the state
    forget_picker_widgets()
    st.session_state[popover_key] = False


def _reset_picker(popover_key: str, config: ScopeDefaultsConfig, listing: WorkspaceBranchListing) -> None:
    st.session_state[STRIP_STATE_KEY] = initial_strip_state(config, listing)  # only_these False
    st.session_state.pop(ONLY_THESE_KEY, None)
    forget_picker_widgets()
    st.session_state[popover_key] = False


def _cells_sentence(cells) -> str:
    return ", ".join(f"{c.project} · {c.branch}" if c.branch else c.project for c in cells) or "all projects"


def _render_preview_and_buttons(
    popover_key: str,
    ticked: tuple[StripTarget, ...],
    more: ScopeDefaultsOverride,
    config: ScopeDefaultsConfig,
    listing: WorkspaceBranchListing,
) -> None:
    targets = ordered_targets(ticked, listing)
    cells = strip_cells(targets)
    count = max(len(cells), 1)  # no target still runs one search: the union
    noun = "search" if count == 1 else "searches"
    st.caption(f"Next question runs {count} {noun}: {_cells_sentence(cells)} · {count} of {config.max_cells}")
    over = count > config.max_cells
    if over:
        st.caption(f"{count} searches exceed the limit of {config.max_cells} (ask_your_docs.scope.max_cells)")
    use, reset = st.columns(2)
    use.button(USE_THESE_LABEL, key=f"{_WIDGET_PREFIX}use", disabled=over, on_click=_use_these, args=(popover_key, targets, more))
    reset.button(RESET_LABEL, key=f"{_WIDGET_PREFIX}reset", on_click=_reset_picker, args=(popover_key, config, listing))


def render_where_to_search_picker(
    popover_key: str,
    label: str,
    state: StripState,
    config: ScopeDefaultsConfig,
    catalog: dict[str, list[str]],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> None:
    """One component, two pages. The body always executes (V3), so its widgets are
    addressable in AppTest without opening it — never gate it on ``.open``."""
    with st.popover(label, key=popover_key, on_change="rerun"):
        st.markdown(f"**{PICKER_TITLE}**")
        if not listing.has_projects:
            st.caption("No indexed projects in this workspace.")
            return
        ticked = _render_project_rows(state, listing, capabilities)
        more = _render_more(state, config, catalog, ticked, capabilities)
        _render_preview_and_buttons(popover_key, ticked, more, config, listing)


__all__ = (
    "ONLY_THESE_KEY",
    "PICKER_KEY",
    "PICKER_TITLE",
    "STRIP_STATE_KEY",
    "forget_picker_widgets",
    "render_where_to_search_picker",
    "seed_widget_once",
)
```

- [ ] **Step 4: Write `scope_strip.py`**

```python
"""The "Searching in …" strip above the chat input (UI spec §6.7, §6.10).

Two rows inside ``st.bottom``, then a plain ``st.chat_input``. The state is sticky
(session state) and compiles to the question scope through ``compile_strip_scope``.
Every mutation is an ``on_click`` / ``on_change`` callback (see scope_picker); the
one render-path write is row 2's forcing of the "Only these" key, which precedes
that checkbox's instantiation (P15).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import ScopeCell
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities
from pydocs_mcp.harness.ask_your_docs.scope_picker import (
    ONLY_THESE_KEY,
    PICKER_KEY,
    STRIP_STATE_KEY,
    forget_picker_widgets,
    render_where_to_search_picker,
    seed_widget_once,
)
from pydocs_mcp.harness.ask_your_docs.strip_state import (
    StripState,
    StripTarget,
    initial_strip_state,
    missing_strip_cells,
    strip_cells,
    strip_chip_label,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

if TYPE_CHECKING:
    from streamlit.elements.widgets.chat import ChatInputValue

NO_TARGET_SENTENCE = "Searching in all projects, each on its indexed branch"
ONLY_THESE_LABEL = "Only these — the agent never searches elsewhere"
FORCED_HINT = "several targets always run as separate searches"
OVER_CAP_HINT = "remove a target before asking"
CHANGE_LABEL = "Change…"
CLEAR_LABEL = "Clear"


def current_strip_state(config: ScopeDefaultsConfig, listing: WorkspaceBranchListing) -> StripState:
    """The sticky state, seeded from YAML on a session's first run."""
    if STRIP_STATE_KEY not in st.session_state:
        st.session_state[STRIP_STATE_KEY] = initial_strip_state(config, listing)
    return st.session_state[STRIP_STATE_KEY]


def _write_strip(state: StripState) -> None:
    # Before any strip / picker widget instantiates (a callback, or the page's pre-render
    # handlers): the picker's rows AND row 2's checkbox re-seed from the state on the
    # next run — the box's key is dropped here so a forced value never sticks.
    st.session_state[STRIP_STATE_KEY] = state
    st.session_state.pop(ONLY_THESE_KEY, None)
    forget_picker_widgets()


def _remove_cell(state: StripState, cell: ScopeCell) -> None:
    _write_strip(state.without_cell(cell))


def _clear_strip(state: StripState) -> None:
    _write_strip(state.cleared())  # cleared() also resets only_these


def _set_only_these(state: StripState) -> None:
    # on_change of the checkbox: the person's own tick lives in the STATE, so it survives
    # a page that does not render the strip (Streamlit drops unrendered widget keys).
    # Never reached while the box is forced (a disabled widget fires no on_change).
    st.session_state[STRIP_STATE_KEY] = replace(
        state, only_these=bool(st.session_state[ONLY_THESE_KEY])
    )


def add_strip_target(target: StripTarget) -> None:
    """"Keep searching <branch>" (§6.9): the strip grows by one target and stays."""
    _write_strip(st.session_state.get(STRIP_STATE_KEY, StripState()).with_target(target))


def drop_missing_targets(listing: WorkspaceBranchListing, workspace: str) -> None:
    """A workspace change reloads the listing; each cell the new listing lacks is
    removed with its own toast (E12) — the others stay. Runs before the strip renders."""
    previous = st.session_state.get("scope_listing_workspace")
    st.session_state["scope_listing_workspace"] = workspace
    state = st.session_state.get(STRIP_STATE_KEY)
    if previous is None or previous == workspace or state is None:
        return
    for cell in missing_strip_cells(state, listing):
        state = state.without_cell(cell)
        shown = f"{cell.project} · {cell.branch}" if cell.branch else cell.project
        st.toast(f"{shown} is no longer indexed — removed from where to search")
    _write_strip(state)


# --- rendering ---------------------------------------------------------------


def _render_row_1(state: StripState, cells: tuple[ScopeCell, ...], picker) -> None:
    if not cells:
        sentence, change = st.columns([6, 1])
        sentence.markdown(NO_TARGET_SENTENCE)
        with change:
            picker()
        return
    columns = st.columns([2, *([3] * len(cells)), 2])
    columns[0].markdown("Searching in")
    # zip(strict=False): the column count is derived from a dynamic chip list.
    for column, cell in zip(columns[1:-1], cells, strict=False):
        column.button(
            strip_chip_label(cell),
            key=f"scope_chip_{cell.project}_{cell.branch}",
            on_click=_remove_cell,
            args=(state, cell),
        )
    with columns[-1]:
        picker()


def _render_row_2(state: StripState, cells: tuple[ScopeCell, ...], max_cells: int) -> None:
    forced = len(cells) >= 2
    seed_widget_once(ONLY_THESE_KEY, state.only_these)  # the person's own tick, from the state
    if forced:
        # Two or more cells imply PIN in the engine (§6.1); the box mirrors it. This is
        # the ONE non-callback write (P15): it precedes the checkbox's instantiation in
        # this run, which Streamlit allows (V2), and it never reaches StripState.only_these.
        st.session_state[ONLY_THESE_KEY] = True
    box, count, clear = st.columns([5, 3, 1])
    box.checkbox(
        ONLY_THESE_LABEL, key=ONLY_THESE_KEY, disabled=forced, on_change=_set_only_these, args=(state,)
    )
    if forced:
        count.caption(FORCED_HINT)
        count.caption(f"{len(cells)} searches per question · limit {max_cells}")
    if len(cells) > max_cells:  # a cap lowered under an existing strip: every send would hit E4
        count.caption(
            f"{len(cells)} searches exceed the limit of {max_cells} (ask_your_docs.scope.max_cells) — {OVER_CAP_HINT}"
        )
    clear.button(CLEAR_LABEL, key="scope_strip_clear", on_click=_clear_strip, args=(state,))


def render_where_to_search_strip(
    state: StripState,
    config: ScopeDefaultsConfig,
    catalog: dict[str, list[str]],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> None:
    """Row 1 (label, chips, "Change…"), row 2 only with a target (§6.7)."""

    def picker() -> None:
        render_where_to_search_picker(PICKER_KEY, CHANGE_LABEL, state, config, catalog, listing, capabilities)

    cells = strip_cells(state.targets)
    _render_row_1(state, cells, picker)
    if cells:
        _render_row_2(state, cells, config.max_cells)


def render_composer_row(
    state: StripState,
    config: ScopeDefaultsConfig,
    catalog: dict[str, list[str]],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> str | ChatInputValue | None:
    """The strip, then a plain chat input, both pinned by ``st.bottom`` (>= 1.57);
    returns the input's submission, if any. Built in the main flow: ``st.bottom``
    refuses the sidebar and dialogs."""
    with st.bottom:
        render_where_to_search_strip(state, config, catalog, listing, capabilities)
        return st.chat_input(
            "Ask about your indexed projects…",
            accept_file="multiple",
            file_type=["png", "jpg", "jpeg", "webp", "gif"],
        )


__all__ = (
    "CHANGE_LABEL",
    "FORCED_HINT",
    "NO_TARGET_SENTENCE",
    "ONLY_THESE_LABEL",
    "OVER_CAP_HINT",
    "add_strip_target",
    "current_strip_state",
    "drop_missing_targets",
    "render_composer_row",
    "render_where_to_search_strip",
)
```

- [ ] **Step 4b: The fixture's dependency packages**

In `tests/harness/ask_your_docs/_fixture.py`, add `packages: list[str] = ()` to `make_bundle`'s keyword parameters and, right after the `('__project__', '')` insert:

```python
    # Dependency packages: SqliteBundleReader.packages() filters __project__ OUT, so a
    # bundle built without these has an EMPTY package pool and no Package selectbox.
    for package in packages:
        conn.execute("INSERT INTO packages VALUES (?, '')", (package,))
```

- [ ] **Step 5: Shrink `scope_panel.py`**

Delete everything listed under Files. Rewrite the docstring (`Streamlit fragments shared by the pages: the attachment chip row, the follow-up chips, the graph page's branch row, the branch caption (UI spec §6.9, §6.10, §6.11). The strip and the picker live in scope_strip / scope_picker.`), drop the now-unused imports, and keep / add:

```python
def branch_caption(project: str, listing: WorkspaceBranchListing) -> str:
    """The read-only U0 line under a project: the stamped branch, nothing sendable."""
    row = listing.default_row(project)
    if row is None:
        return "no branch information"
    return f"indexed on {row.name} @{row.head_sha[:7]}"


def render_attachment_chip_row(attached: list[AttachedSymbol | str]) -> None:
    """Attached-symbol chips and "clear all" — attachments only; the strip owns its
    own chips and its own "Clear" (§6.10)."""
    if not attached:
        return
    cols = st.columns(len(attached) + 1)
    for col, attachment in zip(cols, list(attached), strict=False):
        symbol = _symbol_of(attachment)
        if col.button(f"✕ {symbol.rsplit('.', 1)[-1]}", key=f"chip_{symbol}"):
            attached.remove(attachment)
            st.rerun()
    if cols[-1].button("clear all", key="chip_clear"):
        attached.clear()
        st.rerun()
```

`render_graph_branch_row` calls `branch_caption` (it used `_branch_caption`). `__all__` = `("GraphBranchSelection", "branch_caption", "render_attachment_chip_row", "render_follow_up_chips", "render_graph_branch_row")`. Add `_HARNESS / "scope_strip.py": 300` and `_HARNESS / "scope_picker.py": 300` to `_BUDGETS`.

- [ ] **Step 6: Rewire `app.py` (render path and the strip-based send; tokens come in 11d)**

Imports: drop `render_scope_defaults_button`, `render_scope_defaults_panel`, `render_scope_chip_row`, `render_composer_row`, `drop_pin_if_listing_changed` from the `scope_panel` import and `resolve_question_scope_defaults` from `question_scope`; add `render_attachment_chip_row` (scope_panel), `pin_or_none` (question_scope), `StripTarget`, `compile_strip_scope` (strip_state), and `add_strip_target`, `current_strip_state`, `drop_missing_targets`, `render_composer_row` (scope_strip). The sidebar block loses its two scope lines and the `override` variable:

```python
    catalog, listing = scan_workspace(workspace, load_catalog)
    ayd_cfg = load_ayd_config(config_path)
    scope_caps = page_scope_capabilities()  # the strip, the picker and the footer read it
    technical = technical_details_toggle(ui_config)
```

After the empty-workspace `st.stop()`:

```python
drop_missing_targets(listing, workspace)  # before any strip widget renders (E12)
strip = current_strip_state(ayd_cfg.scope, listing)
active_scope = compile_strip_scope(
    strip.targets, strip.only_these, ayd_cfg.scope, listing, more=strip.more
)
```

(12b adds `capabilities=scope_caps` to that call on both pages.)

`render_scope_chip_row(attached, st.session_state.get("scope_pin"))` becomes `render_attachment_chip_row(attached)`. The follow-up chip block:

```python
if clicked_chip is not None:
    # Handled BEFORE the strip renders: "Keep searching" writes the strip state.
    canned, pin = apply_follow_up_chip(clicked_chip, pin_or_none(active_scope), active_scope)
    if canned is None:
        add_strip_target(StripTarget(clicked_chip.project, clicked_chip.branches))
        st.rerun()
    refuse_unless_connected(canned)
    send_question(canned, (), pin if pin is not None else active_scope)

submission = render_composer_row(strip, ayd_cfg.scope, catalog, listing, scope_caps)
```

and the submission block's tail:

```python
    scope = snapshot_pin_for_send(active_scope, attached)  # the strip is sticky: no write-back
    send_question(question, images, scope, transient_note)
```

Rewrite the module docstring's scope paragraph (`:16-19`) for the strip. In `page_scope.py`, `answer_footer_and_chips` reads the strip:

```python
def answer_footer_and_chips(
    turn: AskTurn, capabilities: ScopeCapabilities, listing: WorkspaceBranchListing, strip_scope: QuestionScope | None
) -> tuple[str, tuple[FollowUpChip, ...]]:
    """The footer line and follow-up chips (§6.8–§6.9); the strip's pin after the send
    decides which cells can still be kept."""
    footer = render_answer_footer(turn.observations, listing)
    return footer, derive_follow_up_chips(turn.observations, listing, capabilities, strip_scope)
```

(`app.py` passes `pin_or_none(active_scope)`; 11e grows both signatures again.) In `pages/2_Graph.py`, replace the two sidebar lines (`render_scope_defaults_button()` / `override = render_scope_defaults_panel(...)`) with:

```python
    strip = current_strip_state(_scope_config(), listing)
    render_where_to_search_picker(
        "graph_where_to_search", PICKER_TITLE, strip, _scope_config(), projects, listing, scope_caps
    )
    defaults = compile_strip_scope(
        strip.targets, strip.only_these, _scope_config(), listing, more=strip.more
    )
```

(imports: `current_strip_state` from `scope_strip`; `PICKER_TITLE`, `render_where_to_search_picker` from `scope_picker`; `compile_strip_scope` from `strip_state`; drop `resolve_question_scope_defaults`, `render_scope_defaults_button`, `render_scope_defaults_panel`.) 11f finishes the graph page.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd $WT && PYTHONPATH=$WT/python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest -o pythonpath=$WT/python tests/harness/ask_your_docs/test_app_scope_states.py tests/harness/ask_your_docs/test_app_attachment.py tests/harness/ask_your_docs/test_app_image_attachment.py tests/harness/ask_your_docs/test_app_serve_session.py -q` and `cd $WT && .venv/bin/pytest tests/harness/ask_your_docs -q --deselect tests/harness/ask_your_docs/test_answer_footer.py::TestFooter::test_distinct_slices_are_listed`
Expected: PASS. `test_graph_page_scope.py`'s two sidebar tests and the caption test fail until 11f (they assert the old button and the old caption text); everything else in that file passes.

- [ ] **Step 8: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/scope_picker.py python/pydocs_mcp/harness/ask_your_docs/scope_strip.py python/pydocs_mcp/harness/ask_your_docs/scope_panel.py python/pydocs_mcp/harness/ask_your_docs/page_scope.py python/pydocs_mcp/harness/ask_your_docs/app.py python/pydocs_mcp/harness/ask_your_docs/pages/2_Graph.py tests/harness/ask_your_docs/_fixture.py tests/harness/ask_your_docs/test_app_scope_states.py tests/harness/ask_your_docs/test_module_line_budgets.py
git commit -m "ask-your-docs: the Searching-in strip and the Where-to-search picker replace the defaults panel and the composer popover"
```

---

### Task 11d: The token send path in `app.py` — refuse, strip, one-shot pin, sticky strip

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/app.py` (`send_question` / `_record_question` take the display text; the submission block parses tokens before the other pre-send refusals)
- Test: `tests/harness/ask_your_docs/test_app_scope_states.py` (append `TestTokens` and `TestSticky`)

**Interfaces:**
- Consumes: `parse_scope_tokens`, `ParsedScopeTokens` (11a); `token_scope`, `snapshot_pin_for_send`, `scope_caption_text(from_question=)` (11b); `refuse` (`page_turn.py`); `current_strip_state`, `drop_missing_targets`, `add_strip_target` (11c); `AttachedSymbol` (Task 4); the `_AgentStackSpy` precedent of `test_app_serve_session.py` for a full send with no network.
- Attached symbols ride EVERY send (AC-30): on the token path the one-shot token PIN is grown by the attached cells exactly as the strip's scope is on the other path — `weave_attachments` names the symbol in the text, so its cell must be reachable by the tools. The parser's `max_cells` check covers the token cells only; the folded count is checked by the interceptor (E4) before any call.
- Produces: `send_question(question, images, scope, transient_note="", *, display_question="", from_question=False)` — `question` is the text handed to `weave_attachments` / reformulation / `ask()` (tokens stripped), `display_question` (default: `question`) is what the transcript and the failure texts show; `_record_question(shown, images, scope, from_question)`.
- Satisfies: AC-37, AC-38, AC-39, AC-40 (page halves), AC-41, the `PIN_BRANCH` page wiring of AC-31; E12, E13, E14.

- [ ] **Step 1: Write the failing tests**

Append to `tests/harness/ask_your_docs/test_app_scope_states.py` (imports: `pydocs_mcp.harness.ask_your_docs.agent as agent_module`, `pydocs_mcp.harness.ask_your_docs.reformulation as reformulation_module`, `FakeBearer` from `._connection_fakes`, `write_config` from `._page_fixtures`, `FollowUpChip, FollowUpKind` from `answer_footer`, `AttachedSymbol` from `attachments`, `QuestionScope, ScopeCell, ScopeKind, ScopeSlice` from `question_scope`, `BRANCHES_NOT_CHOOSABLE` from `scope_tokens`):

```python
class _SendSpy:
    """Stands in for build_agent / ask / reformulate and records what ask() received:
    the standalone text and the ``scope=`` keyword (the two facts AC-39 / AC-40 assert)."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, QuestionScope]] = []
        self.reformulated: list[str] = []

    async def build(self, *_args, **_kwargs):
        return "agent", "llm"

    async def ask(self, _agent, _history, question, *, scope, **_kwargs):
        self.asked.append((question, scope))
        return "an answer"

    async def reformulate(self, _llm, _history, question, **_kwargs):
        self.reformulated.append(question)
        return question

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(agent_module, "build_agent", self.build)
        monkeypatch.setattr(agent_module, "ask", self.ask)
        monkeypatch.setattr(reformulation_module, "reformulate", self.reformulate)


@pytest.fixture
def spy(tmp_path, monkeypatch) -> _SendSpy:
    monkeypatch.setenv("PYDOCS_CONFIG", write_config(tmp_path, model="main-a"))
    send_spy = _SendSpy()
    send_spy.install(monkeypatch)
    return send_spy


def _connected(**seeds):
    return _app(connection_bearer=FakeBearer(), messages=[], history=[], **seeds)


class TestTokens:  # AC-37, AC-38, AC-39, AC-40
    def test_unknown_project_refuses_the_send_and_records_nothing(self, workspace, spy):
        at = _connected()
        at.run()
        at.chat_input[0].set_value("what is Foo? in:backnd").run()
        assert not at.exception, at.exception
        assert "No project named 'backnd'. Indexed: backend, tooling. Nothing was sent." in [
            e.value for e in at.error
        ]
        assert any("Your question (not sent): what is Foo? in:backnd" in i.value for i in at.info)
        assert at.session_state["messages"] == [] and at.session_state["history"] == []
        assert spy.asked == [] and spy.reformulated == []

    def test_on_is_refused_on_u0_even_for_the_stamped_branch(self, workspace, spy):
        at = _connected()
        at.run()
        at.chat_input[0].set_value("what is Bar? in:tooling on:main").run()
        assert not at.exception, at.exception
        assert BRANCHES_NOT_CHOOSABLE in [e.value for e in at.error]
        assert at.session_state["messages"] == [] and spy.asked == []

    def test_a_token_question_is_stripped_sent_one_shot_and_leaves_the_strip_alone(self, workspace, spy):
        """AC-39 + AC-40 on a NON-EMPTY strip (backend): the token names tooling, so
        "the strip was already that" cannot explain a passing assertion."""
        at = _connected(scope_strip=_strip(BACKEND))
        at.run()
        before = at.session_state["scope_strip"]
        at.chat_input[0].set_value("what is  Bar? in:tooling").run()
        assert not at.exception, at.exception
        ((sent_text, sent_scope),) = spy.asked
        assert sent_text == "what is Bar?" and spy.reformulated == ["what is Bar?"]
        assert sent_scope == QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("tooling", ""),))
        user = next(m for m in at.session_state["messages"] if m["role"] == "user")
        assert user["text"] == "what is  Bar? in:tooling"  # the ORIGINAL, doubled space and all
        assert user["scope_caption"] == "searched in: tooling (from your question)"
        assert at.session_state["scope_strip"] == before  # sticky, untouched by the one-shot

    def test_an_attached_symbol_rides_a_token_question_too(self, workspace, spy):
        """AC-30 on the token path: the woven text names `mod_a.Foo` (backend), so the
        one-shot token PIN over tooling is grown by the attached cell — the tools can
        reach what the prompt asks about. The token cells come first, as the parser
        built them; the attached cell follows."""
        at = _connected(attached=[AttachedSymbol("mod_a.Foo", "backend", "feature/retry")])
        at.run()
        at.chat_input[0].set_value("what is Foo? in:tooling").run()
        assert not at.exception, at.exception
        ((sent_text, sent_scope),) = spy.asked
        assert sent_text.startswith("Regarding `mod_a.Foo`")  # woven, tokens stripped
        assert sent_scope.kind is ScopeKind.PIN
        assert sent_scope.cells == (ScopeCell("tooling", ""), ScopeCell("backend", "feature/retry"))

    def test_no_token_text_reaches_ask_byte_identical(self, workspace, spy):
        at = _connected()
        at.run()
        at.chat_input[0].set_value("what  is   Foo?").run()
        assert not at.exception, at.exception
        assert spy.reformulated == ["what  is   Foo?"]
        user = next(m for m in at.session_state["messages"] if m["role"] == "user")
        assert user["scope_caption"] == ""  # DEFAULT: no caption

    def test_tokens_disabled_sends_the_text_verbatim_and_refuses_nothing(self, workspace, spy, monkeypatch):
        monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__SCOPE__TOKENS_ENABLED", "false")
        at = _connected()
        at.run()
        at.chat_input[0].set_value("what is Foo? in:backnd").run()
        assert not at.exception, at.exception
        ((sent_text, sent_scope),) = spy.asked
        assert sent_text == "what is Foo? in:backnd" and sent_scope.kind is ScopeKind.DEFAULT
        assert at.error == []


class TestSticky:  # AC-41, AC-31 (PIN_BRANCH page wiring)
    def test_one_target_with_only_these_sends_a_pin(self, workspace, spy):
        """The tick reaches the engine through the STATE (`strip.only_these`), not a widget
        key: seeded in the state alone, with no `scope_strip_only_these` seed."""
        at = _connected(scope_strip=_strip(TOOLING, only_these=True))
        at.run()
        at.chat_input[0].set_value("what is Bar?").run()
        assert not at.exception, at.exception
        ((_, sent_scope),) = spy.asked
        assert sent_scope.kind is ScopeKind.PIN and sent_scope.cells == (ScopeCell("tooling", "main"),)

    def test_the_strip_survives_a_send_and_a_rerun(self, workspace, spy):
        at = _connected(scope_strip=_strip(TOOLING, BACKEND))
        at.run()
        at.chat_input[0].set_value("what is Foo?").run()
        assert not at.exception, at.exception
        ((_, sent_scope),) = spy.asked
        assert sent_scope.kind is ScopeKind.PIN and len(sent_scope.cells) == 2
        at.run()  # a plain rerun
        assert at.session_state["scope_strip"] == _strip(TOOLING, BACKEND)
        assert _chip_keys(at) == {"scope_chip_backend_feature/retry", "scope_chip_tooling_main"}
        user = next(m for m in at.session_state["messages"] if m["role"] == "user")
        assert user["scope_caption"] == "searched in: backend · feature/retry | tooling · main"

    def test_a_workspace_change_drops_only_the_missing_target(self, workspace, tmp_path, monkeypatch):
        """E12: the new workspace still has tooling but not backend."""
        other = tmp_path / "other"
        other.mkdir()
        make_bundle(
            other / "tooling_0123456789.db",
            project="tooling",
            branches=[("main", TOOLING_MAIN, None, 1, "active", None)],
        )
        at = _app(scope_strip=_strip(TOOLING, BACKEND), scope_listing_workspace=str(workspace))
        at.run()
        assert not at.exception, at.exception
        monkeypatch.setenv("PYDOCS_WORKSPACE", str(other))
        at.run()
        assert not at.exception, at.exception
        assert at.session_state["scope_strip"].targets == (StripTarget("tooling", ("main",)),)
        assert [t.value for t in at.toast] == [
            "backend · feature/retry is no longer indexed — removed from where to search"
        ]

    def test_keep_searching_chip_grows_the_strip_and_sends_nothing(self, workspace, spy):
        """The U1 PIN_BRANCH wiring, exercised against a stored chip (dormant otherwise)."""
        chip = FollowUpChip(FollowUpKind.PIN_BRANCH, "Keep searching develop", "tooling", ("develop",), ScopeSlice.WHOLE_BRANCH, "")
        at = _connected(
            U1,
            scope_strip=_strip(BACKEND),
            messages=[
                {"role": "user", "text": "q", "scope_caption": ""},
                {"role": "assistant", "text": "a", "footer": "", "chips": (chip,)},
            ],
        )
        at.run()
        at.button(key="follow_up_1_pin_branch").click().run()
        assert not at.exception, at.exception
        assert at.session_state["scope_strip"].targets == (
            StripTarget("backend", ("feature/retry",)),
            StripTarget("tooling", ("develop",)),
        )
        assert spy.asked == []
```

(The chip key index is the entry's position in `messages` — `enumerate` from 0 in `transcript.render_transcript`, passed to `render_follow_up_chips(index, …)` — so the assistant entry of a two-entry transcript is index 1: `follow_up_1_…`. The fresh-answer render in `send_question` uses `len(messages)` AFTER the user entry was appended, which is that same future index.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $WT && PYTHONPATH=$WT/python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest -o pythonpath=$WT/python tests/harness/ask_your_docs/test_app_scope_states.py -q -k "Tokens or Sticky"`
Expected: FAIL — `test_unknown_project_refuses_the_send_and_records_nothing` sends the question (`spy.asked` has one entry, no error rendered); `test_a_token_question_…` receives the unstripped text under a DEFAULT scope; `test_a_workspace_change_…` still finds both targets (no drop yet — `drop_missing_targets` is called but 11c's toast text is asserted here for the first time); `test_keep_searching_…` passes already if 11c wired `add_strip_target` (keep it as the pin).

- [ ] **Step 3: Two texts through `send_question`**

```python
def _record_question(
    shown_question: str,
    images: tuple[ImageAttachment, ...],
    scope: QuestionScope,
    from_question: bool,
) -> dict[str, ImageAttachment]:
    """Show the ORIGINAL question (with its scope caption) and keep it; returns the PRIOR image store."""
    ...  # body unchanged except:
    shown = shown_question + ("\n\n" + " ".join(f"`🖼 {att.name}`" for att in images) if images else "")
    caption = scope_caption_text(scope, from_question=from_question)
    ...


def send_question(
    question: str,
    images: tuple[ImageAttachment, ...],
    scope: QuestionScope,
    transient_note: str = "",
    *,
    display_question: str = "",
    from_question: bool = False,
) -> None:
    """The ONE send path. ``question`` is what the model gets (tokens stripped);
    ``display_question`` is what the transcript and the failure texts show — the
    typed text, tokens and all (UI spec §6.10a). A follow-up chip passes neither."""
    shown = display_question or question
    prior_images = _record_question(shown, images, scope, from_question)
    ...
        woven = weave_attachments(attached, question)
        ...
        answer, handle = _run_turn(shown, woven, turn, panel)
```

- [ ] **Step 4: The submission block**

```python
if submission:
    typed = submission.text or ""
    parsed = parse_scope_tokens(
        typed,
        listing,
        scope_caps,
        strip.projects(),
        tokens_enabled=ayd_cfg.scope.tokens_enabled,
        max_cells=ayd_cfg.scope.max_cells,
    )
    if parsed.refusal:  # E13–E15: the question is not sent; the echo quotes the typed text
        refuse(typed, parsed.refusal, bearer)
    refuse_unless_connected(typed)
    images = collect_images(list(submission.files or ()), ayd_cfg.images)
    ...  # the vision policy block, unchanged, with `typed` in its refuse()
    # A token question is a one-shot PIN over its cells (the strip stays as it is);
    # either way the attached symbols' cells ride THIS send (AC-30) — the woven text
    # names them, so the tools must be able to reach them.
    base_scope = token_scope(parsed.cells, active_scope) if parsed.cells else active_scope
    scope = snapshot_pin_for_send(base_scope, attached)
    send_question(
        parsed.stripped_text,
        images,
        scope,
        transient_note,
        display_question=typed,
        from_question=bool(parsed.cells),
    )
```

`from pydocs_mcp.harness.ask_your_docs.scope_tokens import parse_scope_tokens` and `token_scope` join the imports. The follow-up chip path (`send_question(canned, (), …)`) is untouched: it never parses tokens (§6.10a).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd $WT && PYTHONPATH=$WT/python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest -o pythonpath=$WT/python tests/harness/ask_your_docs/test_app_scope_states.py tests/harness/ask_your_docs/test_app_serve_session.py tests/harness/ask_your_docs/test_app_activity.py tests/harness/ask_your_docs/test_app_caches.py -q` and `cd $WT && .venv/bin/pytest tests/harness/ask_your_docs/test_module_line_budgets.py -q` (`app.py` must stay under 500 — it is at 497; the deleted sidebar block and `scope_pin` lines pay for the token block).
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/app.py tests/harness/ask_your_docs/test_app_scope_states.py
git commit -m "ask-your-docs: typed in:/on: tokens refuse or send one-shot; the strip is sticky"
```

---

### Task 11e: Footer words, the teaching hint, `ASK_ON`, and labels that name what was sent

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/answer_footer.py` (`ORIGIN_LABELS`, `_origin_text`, `_segment`, `render_answer_footer` + the hint; `FollowUpKind.ASK_ON`, `_ask_on_chip`, the three re-worded labels, `derive_follow_up_chips(…, asked=)`, `apply_follow_up_chip` on the strip scope; `NO_BRANCH` / `ALL_PROJECTS` stay)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/scope_interceptor.py` (`cell_label(cell, sent_args)`; `merge_cell_results(cells, sent, results)`; `fan_out_over_cells` passes the per-cell arguments)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/page_scope.py` (`answer_footer_and_chips(turn, capabilities, listing, config, strip_scope, asked)`), `python/pydocs_mcp/harness/ask_your_docs/app.py` (the call site passes `ayd_cfg.scope`, `pin_or_none(active_scope)` and the standalone question — read from the turn's activity trace or pass the woven text; the woven text is what `ask()` received before reformulation and is the documented choice)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/activity_labels.py` (`scope_note` — the one on-screen string this module owns, re-worded to the D14 sentence)
- Modify: `tests/harness/ask_your_docs/test_module_line_budgets.py` (`answer_footer.py: 500`)
- Test: `tests/harness/ask_your_docs/test_answer_footer.py` (every string assertion; the cap tests re-derived; `TestApply` on the strip scope), `tests/harness/ask_your_docs/test_scope_interceptor.py` (`test_ac6b_…` and `test_ac9_…` expectations; one new AC-51 test), `tests/harness/ask_your_docs/test_activity_labels.py` (the re-worded scope line)

**Interfaces:**
- Consumes: `ScopeDefaultsConfig` (11g); `SLICE_LABELS` (11b); `WorkspaceBranchListing.project_names / pickable / row` (Task 3); `cell_arguments` (Task 6).
- Produces: `render_answer_footer(observations, listing, config, capabilities=NO_SCOPE_CAPABILITIES) -> str`; `ORIGIN_LABELS` = `{DEFAULT: "your default", PINNED: "only these", AGENT_CHOSEN: "the agent's choice", SERVER: "the server's default"}`; `FRESH = "index up to date"`, `BEHIND = "index behind your checkout — reindex to search it"`; `FollowUpKind {ASK_ON, COMPARE_WITH, PIN_BRANCH, SHOW_DIFF}`; `derive_follow_up_chips(observations, listing, capabilities, strip_scope, asked="", *, max_cells=_SHIPPED_MAX_CELLS) -> tuple[FollowUpChip, ...]` (`_SHIPPED_MAX_CELLS = ScopeDefaultsConfig().max_cells` — the pydantic default read once, never a repeated literal; the page passes `config.max_cells`); `apply_follow_up_chip(chip, strip_scope, defaults)`; `cell_label(cell, sent_args) -> str`; `merge_cell_results(cells, sent, results)`.
- The "Keep searching <branch>" chip is suppressed when the strip's cells plus one would exceed `max_cells`: the picker refuses such a strip, so the chip must too, or every later send would fail at E4 while row 2 only printed a count past the limit.
- Produces (`activity_labels.py`): `scope_note` keeps its signature and its no-pin `None`, and its text becomes exactly `Searching only in: <parts>` — `f"Searching only in: {', '.join(parts)}"`, e.g. `Searching only in: project "backend", package "fastapi"` (§6.7 words: the activity panel's "Show technical details" line is on screen, and `pinned by you` is a retired segment AC-47 sweeps this module for). The `Scope: ` prefix and the ` (pinned by you)` suffix both go; `activity_scope_words` (`scope_pin.py`) and the panel's call site are untouched.
- Satisfies: AC-17 (D14 words), AC-18, AC-34, AC-43, AC-46 (against U1 fakes, inactive on U0r), AC-31 (D14 words), AC-51, AC-6b's label clause, AC-47's activity-panel clause (the wording; the sweep that pins it is 11h); retires plan deviation P7.

- [ ] **Step 1: Write the failing tests**

`tests/harness/ask_your_docs/test_answer_footer.py`: add `QUIET = ScopeDefaultsConfig(footer_hint=False)` and `HINTS = ScopeDefaultsConfig()` (import from `pydocs_mcp.retrieval.config.ask_your_docs_models`); every existing `render_answer_footer(obs, LISTING)` call becomes `render_answer_footer(obs, LISTING, QUIET)`, and every `assert footer == …` takes the new sentence. Origin phrases are asserted as **full segment strings** (the four share words) and booleans with `is`. The rewritten `TestFooter`:

```python
class TestFooter:  # AC-18, AC-34
    def test_pinned_cell_segment(self):
        footer = render_answer_footer(_observations(_obs(), _obs(tool="get_symbol")), LISTING, QUIET)
        assert footer == "Searched backend · feature/retry @3e1a9c2 (only these) · index up to date"

    def test_segments_are_sorted_and_joined_with_bars(self):
        records = [
            _obs(project="tooling", branch="main", origin=BranchOrigin.DEFAULT),
            _obs(project="backend", branch="main", origin=BranchOrigin.PINNED),
        ]
        footer = render_answer_footer(_observations(*records), LISTING, QUIET)
        assert footer == (
            "Searched backend · main @9abcdef (only these) · index up to date"
            " | tooling · main @3e1a9c2 (your default) · index up to date"
        )

    def test_union_answer_on_a_multi_project_listing_reads_all_projects(self):
        record = _obs(project="", branch="", origin=BranchOrigin.SERVER, meta={"project": "backend", "branch": "main", "indexed_git_head": "9abcdef" + "0" * 33})
        footer = render_answer_footer(_observations(record), LISTING, QUIET)
        assert footer == "Searched all projects · main @9abcdef (the server's default) · index up to date"

    def test_replaced_argument_and_stale_index_are_visible(self):
        record = _obs(origin=BranchOrigin.DEFAULT, replaced=True, meta={"branch": "feature/retry", "index_stale": True})
        footer = render_answer_footer(_observations(record), LISTING, QUIET)
        assert footer == (
            "Searched backend · feature/retry @3e1a9c2 (the agent's choice → your default)"
            " · index behind your checkout — reindex to search it"
        )

    def test_pre_v16_bundle_footer(self):
        """AC-34: no listing rows, no meta.branch, no sha — freshness still present."""
        record = _obs(project="", branch="", origin=BranchOrigin.SERVER, meta={"project": "demo"})
        footer = render_answer_footer(_observations(record), SINGLE, QUIET)
        assert footer == "Searched demo · no branch (the server's default) · index up to date"

    def test_distinct_slices_are_listed_after_the_origin_and_the_default_is_omitted(self):
        records = [_obs(slice_=ScopeSlice.CHANGED_FILES), _obs(slice_=ScopeSlice.DIFF_HUNKS), _obs()]
        footer = render_answer_footer(_observations(*records), LISTING, QUIET)
        assert footer == (
            "Searched backend · feature/retry @3e1a9c2 (only these)"
            " · only files this branch changed, only the changes themselves · index up to date"
        )

    def test_mixed_origins_name_the_most_specific_one(self):
        records = [_obs(origin=BranchOrigin.SERVER), _obs(origin=BranchOrigin.AGENT_CHOSEN)]
        footer = render_answer_footer(_observations(*records), LISTING, QUIET)
        assert footer == "Searched backend · feature/retry @3e1a9c2 (the agent's choice) · index up to date"

    def test_no_tool_calls(self):
        assert render_answer_footer(_observations(), LISTING, QUIET) == "answered without tool calls"


class TestHint:  # AC-43
    def test_names_the_first_unsearched_project_in_listing_order(self):
        # tooling answered; backend (listed first) is unsearched → backend is named, not tooling.
        footer = render_answer_footer(_observations(_obs(project="tooling", branch="main")), LISTING, HINTS)
        assert footer.endswith(" · add `in:backend` to search there too")

    def test_no_hint_when_every_project_was_searched(self):
        records = [_obs(project="tooling", branch="main"), _obs()]
        footer = render_answer_footer(_observations(*records), LISTING, HINTS)
        assert "add `in:" not in footer

    def test_no_hint_when_either_key_is_off(self):
        obs = _observations(_obs(project="tooling", branch="main"))
        assert "add `in:" not in render_answer_footer(obs, LISTING, ScopeDefaultsConfig(tokens_enabled=False))
        assert "add `in:" not in render_answer_footer(obs, LISTING, ScopeDefaultsConfig(footer_hint=False))

    def test_u1_one_cell_with_an_indexed_base_teaches_on(self):
        footer = render_answer_footer(_observations(_obs()), LISTING, HINTS, U1)
        assert footer.endswith(" · add `on:main` to compare with main")
        # Same records, no branch capability: the in: form, never on:.
        assert render_answer_footer(_observations(_obs()), LISTING, HINTS).endswith("add `in:tooling` to search there too")
```

`TestChips` — labels and the cap:

```python
    def test_one_answered_cell_with_a_base_yields_ask_on_compare_and_keep(self):
        chips = derive_follow_up_chips(_observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, U1, None, asked="why?")
        assert [c.kind for c in chips] == [FollowUpKind.ASK_ON, FollowUpKind.COMPARE_WITH, FollowUpKind.PIN_BRANCH]
        ask_on, compare, keep = chips
        # ASK_ON names the FIRST OTHER pickable branch in listing order (main), never the answered one.
        assert ask_on.label == "Ask this on main too" and ask_on.branches == ("main",) and ask_on.question == "why?"
        assert compare.label == "Compare with main"
        assert compare.question == "Compare the previous answer between feature/retry and main: what differs?"
        assert keep.label == "Keep searching feature/retry" and keep.question == ""

    def test_ask_on_is_absent_on_u0_and_without_another_branch(self):
        assert derive_follow_up_chips(_observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, NO_SCOPE_CAPABILITIES, None, asked="why?") == ()
        one_branch = WorkspaceBranchListing(projects={"tooling": (_row("main", default=True),)})
        chips = derive_follow_up_chips(_observations(_obs(project="tooling", branch="main", origin=BranchOrigin.DEFAULT)), one_branch, U1, None, asked="why?")
        assert FollowUpKind.ASK_ON not in [c.kind for c in chips]

    def test_two_answered_cells_yield_one_keep_chip_and_nothing_else(self):
        """D14: at most one chip per KIND — the 2026-09-04 rule gave one per cell."""
        records = [
            _obs(project="tooling", branch="main", origin=BranchOrigin.DEFAULT),
            _obs(project="backend", branch="", origin=BranchOrigin.SERVER, meta={"branch": "feature/retry"}),
        ]
        chips = derive_follow_up_chips(_observations(*records), LISTING, U1, None)
        assert [(c.kind, c.project, c.branches) for c in chips] == [
            (FollowUpKind.PIN_BRANCH, "backend", ("feature/retry",))  # the first in (project, branch) order
        ]

    def test_at_most_one_chip_per_kind_and_deterministic_under_shuffle(self):
        records = [_obs(project=p, branch="main", origin=BranchOrigin.DEFAULT) for p in ("backend", "tooling")]
        records.append(_obs(project="backend", branch="feature/retry", origin=BranchOrigin.DEFAULT))
        random.Random(7).shuffle(records)
        chips = derive_follow_up_chips(_observations(*records), LISTING, U2, None)
        kinds = [c.kind for c in chips]
        assert len(kinds) == len(set(kinds))  # asserted directly, not through the count
        assert len(chips) <= len(FollowUpKind) == 4
        assert [(c.kind, c.project, c.branches[0]) for c in chips] == [(FollowUpKind.PIN_BRANCH, "backend", "feature/retry")]

    def test_keep_searching_is_suppressed_when_the_strip_is_at_the_cap(self):
        """A chip that grew the strip past max_cells would make every later send fail at
        E4. Strip of two cells, cap 2: no keep chip; cap 3: the chip is back."""
        strip_scope = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("tooling", "main"), ScopeCell("backend", "main")))
        obs = _observations(_obs(origin=BranchOrigin.DEFAULT))  # backend · feature/retry, not in the strip
        at_cap = derive_follow_up_chips(obs, LISTING, U1, strip_scope, max_cells=2)
        assert FollowUpKind.PIN_BRANCH not in [c.kind for c in at_cap]
        assert FollowUpKind.COMPARE_WITH in [c.kind for c in at_cap]  # only the keep chip is gated
        room = derive_follow_up_chips(obs, LISTING, U1, strip_scope, max_cells=3)
        assert FollowUpKind.PIN_BRANCH in [c.kind for c in room]
```

(`test_a_fourth_answered_cell_is_cut_by_the_kind_count_cap` is **deleted**: with one chip per kind the count can never reach the cap through cells; the direct per-kind assertion above replaces it.) `TestApply`:

```python
class TestApply:  # AC-31, AC-46
    def test_compare_returns_the_question_and_a_one_shot_pin_leaving_the_strip_alone(self):
        strip_scope = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"),))
        chip = next(c for c in derive_follow_up_chips(_observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, U1, None) if c.kind is FollowUpKind.COMPARE_WITH)
        question, pin = apply_follow_up_chip(chip, strip_scope, DEFAULTS)
        assert question == chip.question
        assert pin.cells == (ScopeCell("backend", "feature/retry"), ScopeCell("backend", "main"))
        assert strip_scope.cells == (ScopeCell("backend", "main"),)

    def test_ask_on_returns_the_same_question_under_a_one_shot_pin_on_the_other_branch(self):
        chip = next(c for c in derive_follow_up_chips(_observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, U1, None, asked="why does retry drop?") if c.kind is FollowUpKind.ASK_ON)
        question, pin = apply_follow_up_chip(chip, None, DEFAULTS)
        assert question == "why does retry drop?"
        assert pin == QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"),))

    def test_keep_searching_returns_no_question_and_the_strip_scope_grown(self):
        chip = next(c for c in derive_follow_up_chips(_observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, U1, None) if c.kind is FollowUpKind.PIN_BRANCH)
        question, grown = apply_follow_up_chip(chip, None, DEFAULTS)
        assert question is None and grown.cells == (ScopeCell("backend", "feature/retry"),)
        strip_scope = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("tooling", "main"),))
        _, grown = apply_follow_up_chip(chip, strip_scope, DEFAULTS)
        assert grown.cells == (ScopeCell("tooling", "main"), ScopeCell("backend", "feature/retry"))
```

(`test_pin_branch_without_a_kept_pin_carries_the_session_defaults` and `test_show_diff_drops_a_dependencies_only_default` keep their logic; rename `kept` → `strip_scope`, and the diff chip's label is `Show what changed`.) In `test_scope_interceptor.py`, change `test_ac6b_…`'s label lines to:

```python
    # The label names what was SENT (§6.4 rule 3 under D14): no branch went out, so no branch
    # is printed even though the cells carry `main` — the old rule would print "## backend · main".
    texts = [b.text for b in merged.content]
    assert texts == ["## backend\n", "A", "## tooling\n", "B"]
    assert merged.structuredContent["text"] == "## backend\nA\n## tooling\nB"
```

`test_ac9_…` becomes `["## a\n", "fine", "## b\n", "boom"]` / `"## a\nfine\n## b\nboom"`, and add:

```python
def test_ac51_label_carries_the_branch_only_when_it_was_sent():
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"), ScopeCell("tooling", "main")))
    handler = RecordingHandler([_result("A"), _result("B")])
    with active(pin, ScopeRuntime(listing=LISTING, capabilities=BRANCHED, max_cells=4)):
        merged = call("search_codebase", {"query": "q"}, handler)
    assert [a.get("branch") for a in handler.sent] == ["main", "main"]
    assert [b.text for b in merged.content][::2] == ["## backend · main\n", "## tooling · main\n"]
```

In `test_activity_labels.py`, add (the existing `scope_note` assertions move to the new text):

```python
def test_the_scope_line_reads_in_the_d14_words_and_never_says_pinned_by_you():
    """§6.7: the activity panel's "Show technical details" scope line is ON SCREEN, so it
    follows the vocabulary — `pinned by you` is a retired segment (AC-47 sweeps this
    module for it) and `Scope:` is not how the rest of the page names a scope."""
    line = scope_note({"project": "backend", "package": "fastapi"})
    assert line == 'Searching only in: project "backend", package "fastapi"'
    assert "pinned by you" not in line and not line.startswith("Scope:")
    assert scope_note({}) is None  # a DEFAULT question still renders no line at all
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $WT && .venv/bin/pytest tests/harness/ask_your_docs/test_answer_footer.py tests/harness/ask_your_docs/test_scope_interceptor.py tests/harness/ask_your_docs/test_activity_labels.py -q`
Expected: FAIL — `TypeError: render_answer_footer() takes 2 positional arguments but 3 were given`, `AttributeError: ASK_ON`, the two label assertions (`"## backend · main\n" != "## backend\n"`), and the scope line still reading `Scope: project "backend", package "fastapi" (pinned by you)`.

- [ ] **Step 3: The footer**

In `answer_footer.py`:

```python
ORIGIN_LABELS: dict[BranchOrigin, str] = {
    BranchOrigin.DEFAULT: "your default",
    BranchOrigin.PINNED: "only these",
    BranchOrigin.AGENT_CHOSEN: "the agent's choice",
    BranchOrigin.SERVER: "the server's default",
}
FRESH = "index up to date"
BEHIND = "index behind your checkout — reindex to search it"
_SEARCHED = "Searched "
# The shipped cap, read from the pydantic default once (never a repeated literal); the
# pages pass the deployment's real value through derive_follow_up_chips(max_cells=).
_SHIPPED_MAX_CELLS = ScopeDefaultsConfig().max_cells


def _origin_text(records: tuple[CellObservation, ...]) -> str:
    if any(r.replaced for r in records):
        return f"{ORIGIN_LABELS[BranchOrigin.AGENT_CHOSEN]} → {ORIGIN_LABELS[BranchOrigin.DEFAULT]}"
    origin = min((r.branch_origin for r in records), key=_ORIGIN_PRECEDENCE.index)
    return ORIGIN_LABELS[origin]


def _slice_text(records: tuple[CellObservation, ...]) -> str:
    """Distinct non-default slices in enum order; "" when every call ran the default."""
    slices = sorted({r.slice for r in records} - {ScopeSlice.WHOLE_BRANCH}, key=list(ScopeSlice).index)
    return ", ".join(SLICE_LABELS[s] for s in slices)


def _segment(cell, records, listing) -> str:
    project, branch = cell
    meta = records[0].meta
    shown_branch = branch or str(meta.get("branch") or "") or NO_BRANCH
    sha = listing.head_sha(project, branch) if project and branch else ""
    sha = sha or str(meta.get("indexed_git_head") or "")
    head = f"{_shown_project(project, meta, listing)} · {shown_branch}"
    parts = [f"{head} @{sha[:7]} ({_origin_text(records)})" if sha else f"{head} ({_origin_text(records)})"]
    if shown_branch != NO_BRANCH and _slice_text(records):  # slices are branch-relative
        parts.append(_slice_text(records))
    stale = any(bool(r.meta.get("index_stale")) for r in records)
    parts.append(BEHIND if stale else FRESH)  # R10: never hidden, in both states
    return " · ".join(parts)


def _unsearched_project(groups, listing) -> str:
    searched = {project for project, _ in groups} | {
        p for (project, _), records in groups.items() if not project for p in listing.project_names
    }  # a union answer searched every project
    return next((p for p in listing.project_names if p not in searched), "")


def _teaching_hint(groups, listing, config, capabilities) -> str:
    """"add `in:<name>` …" for the first unsearched project; on U1 with one answered
    cell whose base is indexed, "add `on:<base>` …" instead (§6.8)."""
    if not (config.tokens_enabled and config.footer_hint):
        return ""
    if capabilities.branch_selector and len(groups) == 1:
        ((project, branch),) = groups
        row = listing.row(project, branch) if project else None
        base = row.base_name if row else None
        if base and base != branch and listing.has_branch(project, base):
            return f"add `on:{base}` to compare with {base}"
    name = _unsearched_project(groups, listing)
    return f"add `in:{name}` to search there too" if name else ""


def render_answer_footer(
    observations: ScopeObservations,
    listing: WorkspaceBranchListing,
    config: ScopeDefaultsConfig,
    capabilities: ScopeCapabilities = NO_SCOPE_CAPABILITIES,
) -> str:
    """One caption line: ``Searched `` once, a segment per distinct sent cell, sorted,
    joined by `` | ``, then the teaching hint when the two YAML keys allow it."""
    groups = observations.by_cell()
    if not groups:
        return "answered without tool calls"
    line = _SEARCHED + " | ".join(_segment(cell, records, listing) for cell, records in groups.items())
    hint = _teaching_hint(groups, listing, config, capabilities)
    return f"{line} · {hint}" if hint else line
```

The `in:` / `on:` literals inside the hint are the model-facing? No — they are on screen; write them through the constants: `from pydocs_mcp.harness.ask_your_docs.scope_tokens import BRANCH_TOKEN_PREFIX, PROJECT_TOKEN_PREFIX` and format `f"add `{PROJECT_TOKEN_PREFIX}{name}` …"` (AC-36's single-source rule). `scope_tokens` imports nothing from `answer_footer`, so there is no cycle.

- [ ] **Step 4: The chips**

```python
class FollowUpKind(StrEnum):
    ASK_ON = "ask_on"
    COMPARE_WITH = "compare_with"
    PIN_BRANCH = "pin_branch"
    SHOW_DIFF = "show_diff"


def _ask_on_chip(cell, listing, capabilities, asked) -> FollowUpChip | None:
    """"Ask this on <branch> too": the first OTHER pickable branch of the project, in
    listing order; re-sends the answered question itself (§6.9)."""
    if not capabilities.branch_selector or not asked:
        return None
    other = next((r.name for r in listing.pickable(cell.project) if r.name != cell.branch), "")
    if not other:
        return None
    return FollowUpChip(FollowUpKind.ASK_ON, f"Ask this on {other} too", cell.project, (other,), ScopeSlice.WHOLE_BRANCH, asked)
```

`_compare_chip`'s label → `f"Compare with {base}"`; `_show_diff_chip`'s → `"Show what changed"`; `_pin_chip`'s → `f"Keep searching {cell.branch}"`. `_pin_chips` becomes `_keep_chip(cells, strip_scope, capabilities, max_cells) -> FollowUpChip | None` returning the chip for the **first** wanted cell only, and `None` when `len(strip_scope.cells if strip_scope else ()) + 1 > max_cells` (the grown strip would be refused at E4 on every send). `derive_follow_up_chips(observations, listing, capabilities, strip_scope, asked="", *, max_cells=_SHIPPED_MAX_CELLS)` orders `ask_on, compare, diff` for one cell, then the keep chip; the docstring says "at most one chip per kind (§6.9); `len(FollowUpKind)` is the ceiling, never reached through cells". `apply_follow_up_chip(chip, strip_scope, defaults)`: `PIN_BRANCH` → `(None, _grown_strip_scope(cells, strip_scope, defaults))` (rename `_grown_kept_pin`); `ASK_ON` / `COMPARE_WITH` / `SHOW_DIFF` → `(chip.question, one_shot)` as today. `page_scope.answer_footer_and_chips(turn, capabilities, listing, config, strip_scope, asked)` is, explicitly:

```python
    footer = render_answer_footer(turn.observations, listing, config, capabilities)  # capabilities: the U1 on: hint
    chips = derive_follow_up_chips(
        turn.observations, listing, capabilities, strip_scope, asked, max_cells=config.max_cells
    )
    return footer, chips
```

— `capabilities` reaches the footer too, or the U1 `on:` hint can never render from the page. `app.py` passes `ayd_cfg.scope`, `pin_or_none(active_scope)` and `woven`.

- [ ] **Step 5: The label rule in the interceptor**

```python
def cell_label(cell: ScopeCell, sent_args: Mapping[str, Any]) -> str:
    """Names what was SENT (§6.4 rule 3 under D14): the branch appears only when the
    per-cell arguments carried it — never on U0, even for a cell that holds one."""
    branch = str(sent_args.get("branch") or "")
    return f"{cell.project} · {branch}" if branch else cell.project


def merge_cell_results(
    cells: Sequence[ScopeCell], sent: Sequence[Mapping[str, Any]], results: Sequence[CallToolResult]
) -> CallToolResult:
    ...
    for cell, args, result in zip(cells, sent, results, strict=True):
        label = cell_label(cell, args)
```

`fan_out_over_cells` builds `sent = [cell_arguments(args, cell, runtime.capabilities) for cell in cells]` and passes it (the same dicts `_call_pinned_cell` sends — compute once, pass to both).

- [ ] **Step 6: The activity panel's scope line**

In `activity_labels.py`, `scope_note` (`:201-207`) keeps its keys, its `CODE_SCOPE_WORDS` lookup and its `None` for a `DEFAULT` question; only the sentence changes:

```python
def scope_note(scope: Mapping[str, str]) -> str | None:
    """'Searching only in: project "x"' — only when a pin applies (§6.7 words)."""
    keys = ("project", "package")
    parts = [f'{key} "{clip_label_text(scope[key])}"' for key in keys if scope.get(key)]
    code = CODE_SCOPE_WORDS.get(str(scope.get("code", "all")))
    parts += [code] if code else []
    return f"Searching only in: {', '.join(parts)}" if parts else None
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd $WT && .venv/bin/pytest tests/harness/ask_your_docs -q` and the 11d AppTest command.
Expected: PASS (`answer_footer.py` under 500; `test_module_line_budgets` gains its entry).

- [ ] **Step 8: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/answer_footer.py python/pydocs_mcp/harness/ask_your_docs/scope_interceptor.py python/pydocs_mcp/harness/ask_your_docs/page_scope.py python/pydocs_mcp/harness/ask_your_docs/app.py python/pydocs_mcp/harness/ask_your_docs/activity_labels.py tests/harness/ask_your_docs/test_answer_footer.py tests/harness/ask_your_docs/test_scope_interceptor.py tests/harness/ask_your_docs/test_activity_labels.py tests/harness/ask_your_docs/test_module_line_budgets.py
git commit -m "ask-your-docs: footer in the user's words with a teaching hint; Ask-this-on chip; labels name what was sent"
```

---

### Task 11f: Graph page — one "Where to search" button over the shared picker

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/pages/2_Graph.py` (the `default_branch` rule under the strip; the sidebar block landed in 11c)
- Test: `tests/harness/ask_your_docs/test_graph_page_scope.py` (three tests rewritten, one caption string updated, four untouched)

**Interfaces:**
- Consumes: `render_where_to_search_picker`, `PICKER_TITLE` (11c); `compile_strip_scope`, `StripState`, `StripTarget` (11b); `render_graph_branch_row`, `GraphBranchSelection`, `branch_caption` (11c); `resolve_default_branch` (Task 4).
- Produces: the graph page's `selection: GraphBranchSelection` (consumed by Task 14's compare overlay) with `default_branch` = the first branch of the strip's target for the project (`strip.targets`, read from the STATE — not from the compiled scope: a one-target soft strip compiles to a DEFAULT whose cell is `(project, "")`, so `defaults.branches_for(project)` is `()` there) when it has one, else `resolve_default_branch` of the compiled scope, else the stamped row.
- Pins the on-screen label too: `PICKER_TITLE == "Where to search"` — a popover's label is not reachable from AppTest on 1.59, so the constant is the only pin of AC-45's wording.
- Satisfies: AC-45; AC-30 (D14 words — the session target is the strip; the attach tests stay as the regression fence).

- [ ] **Step 1: Write the failing tests**

In `test_graph_page_scope.py`, replace `test_sidebar_offers_the_shared_scope_defaults_button`, `test_open_panel_renders_the_shared_defaults_controls` and `test_panel_override_moves_the_branch_row_off_the_base_branch` with:

```python
def test_sidebar_offers_exactly_one_where_to_search_button(workspace):
    """AC-45: one popover keyed graph_where_to_search, labeled "Where to search", and none
    of the 2026-09-04 keys. A popover's label is NOT reachable from AppTest on 1.59 (it is
    not a Button element), so the label is pinned through the constant the page passes."""
    at = graph_page()
    at.run()
    assert not at.exception, at.exception
    assert PICKER_TITLE == "Where to search"  # the only pin of the on-screen wording
    assert not any(b.label in {"Where to search", "Scope defaults"} for b in at.sidebar.button)  # no Button twin
    assert "graph_where_to_search" in at.session_state  # the popover's key is the proof it rendered (V2)
    keys = {w.key for w in [*at.button, *at.checkbox, *at.selectbox, *at.radio] if w.key}
    assert not keys & {"scope_defaults_button", "scope_defaults_project", "scope_defaults_code"}


def test_the_button_opens_the_same_picker_as_the_chat_page(workspace):
    at = graph_page()
    at.run()
    assert not at.exception, at.exception
    assert any(c.key == "scope_picker_project_demo" for c in at.checkbox)
    assert any(r.key == "scope_picker_code" for r in at.radio)
    assert any(b.key == "scope_picker_use" for b in at.button)


def test_the_strips_branch_moves_the_row_off_the_base_branch(workspace):
    """The picker feeds the row (the surviving intent of the 2026-09-04 override test):
    a strip target on feature/retry preselects it, not the base (main) the YAML default
    would resolve to. Only-these is OFF on purpose: the strip then compiles to a DEFAULT
    whose cell is (demo, ""), so a row that read the branch from the COMPILED scope would
    fall through to the base and fail here — the row must read the strip's target.
    Seeded before the first run — a selectbox holding a value ignores a changed ``index``."""
    at = graph_page(
        scope_capabilities=U1,
        scope_strip=StripState(targets=(StripTarget("demo", ("feature/retry",)),)),
    )
    at.run()
    assert not at.exception, at.exception
    assert at.selectbox(key="graph_branch").value == "feature/retry"
```

and in `test_u0_branch_row_is_a_read_only_caption` the expected caption becomes `f"indexed on feature/retry @{_FEATURE_SHA[:7]}"`. Import `StripState, StripTarget` from `strip_state` and `PICKER_TITLE` from `scope_picker`. The other four tests (`test_u1_branch_selectbox_preselects_the_default_scope_branch`, `test_selected_panel_names_the_branch_the_symbol_was_read_from`, `test_add_to_question_attaches_the_project_and_the_branch`, and the caption test) are the R8 regression fence — re-run unchanged.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $WT && PYTHONPATH=$WT/python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest -o pythonpath=$WT/python tests/harness/ask_your_docs/test_graph_page_scope.py -q`
Expected: FAIL — `test_the_strips_branch_moves_the_row_off_the_base_branch` (the row still preselects `main`); the button and caption tests pass already after 11c's swap — keep them as the pins.

- [ ] **Step 3: The row rule**

In `2_Graph.py`, after the `defaults = compile_strip_scope(...)` line of 11c:

```python
    default_row = listing.default_row(project)
    # The strip's own branch for this project wins — read from the STATE, because a
    # one-target soft strip compiles to a DEFAULT whose cell carries no branch; then the
    # YAML-resolved default; then the stamped row (the only branch U0 can show).
    strip_branch = next((t.branches[0] for t in strip.targets if t.project == project and t.branches), "")
    default_branch = (
        strip_branch
        or resolve_default_branch(defaults, project, listing)
        or (default_row.name if default_row else "")
    )
```

and rewrite the page's sidebar comment (`# Same YAML the chat page reads; the panel overrides it for this session only.`) to name the strip.

- [ ] **Step 4: Run the tests to verify they pass**

Run: the Step 2 command.
Expected: PASS (seven tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/pages/2_Graph.py tests/harness/ask_your_docs/test_graph_page_scope.py
git commit -m "ask-your-docs graph page: one Where-to-search button over the shared picker"
```

---

### Task 11h: The vocabulary sweep

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/{question_scope,scope_panel,activity_labels,scope_pin,scope_strip,scope_picker,answer_footer,app}.py`, `pages/2_Graph.py` (only if the sweep finds a survivor — `activity_labels.py`'s line is re-worded in 11e and `scope_pin.py`'s `CODE_SCOPE_WORDS` still spells `own code only`, an AC-47 segment, so expect a survivor there)
- Test: `tests/harness/ask_your_docs/test_scope_vocabulary.py` (new, pure — core deps only) and `tests/harness/ask_your_docs/test_app_scope_vocabulary.py` (new, AppTest — the rendered trees of both pages plus the activity panel's scope line for a PIN question)

**Interfaces:**
- Consumes: the rendered AppTest trees of both pages (`_page_fixtures`) and the activity panel of a PIN question (the scripted-graph seams of `test_app_activity`), the source text of the seven screen modules.
- Produces: nothing new — two greppable pins.
- Two files, not one: pytest discovers autouse fixtures at collection from a module's globals, so the autouse `page_env` (which sets `PYDOCS_WORKSPACE` and clears the page caches) only arms through a MODULE-LEVEL `from ._page_fixtures import …` — and that import needs `pytest.importorskip("streamlit")` at module level, which would skip the pure source-text half in the worktree venv. So the source-text half stays streamlit-free in `test_scope_vocabulary.py` and the rendered-tree half gets its own module.
- Satisfies: AC-47.

- [ ] **Step 1: Write the failing tests**

`tests/harness/ask_your_docs/test_scope_vocabulary.py`:

```python
"""AC-47, source-text half: the retired segments never reach a screen string literal; the
model note keeps its bytes. Core deps only — the rendered-tree half is
test_app_scope_vocabulary.py (AppTest).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pydocs_mcp.harness.ask_your_docs.question_scope import (
    MODEL_NOTE_CODE_WORDS,
    MODEL_NOTE_SLICE_WORDS,
    QuestionScope,
    ScopeCell,
    ScopeCode,
    ScopeKind,
    ScopeSlice,
    scope_prefix,
)

_HARNESS = Path(__file__).resolve().parents[3] / "python/pydocs_mcp/harness/ask_your_docs"
# AC-47 names scope_panel.py, activity_labels.py and scope_pin.py; scope_strip.py,
# scope_picker.py, answer_footer.py and app.py are the D14 modules that inherited
# scope_panel.py's screen strings, so the tuple is AC-47's three plus those four.
_SCREEN_MODULES = (
    "scope_panel.py",
    "activity_labels.py",
    "scope_pin.py",
    "scope_strip.py",
    "scope_picker.py",
    "answer_footer.py",
    "app.py",
)
# AC-47's enumerated segments, verbatim and in its order. Each entry carries its OWN
# delimiters, and that is what makes this a whole-segment sweep rather than a substring
# one: "your default", "the server's default" and "main (base branch)" all contain the
# word "default" and must pass, because the retired spellings are "· default" and
# "(default)". `(default)` is retired only OUTSIDE the model-facing catalog line, which
# lives in catalog.py — not a screen module, so it is out of this tuple's reach by
# construction and needs no exemption here.
_RETIRED = (
    "Scope defaults",
    "keep for next",
    "Reset to shipped",
    "whole branch",
    "own code only",
    "all code",
    "answered from",
    "index stale",
    "(default)",
    "· default",
    "(pinned)",
    "· pinned",
    "pinned by you",
    "agent-chosen",
    "server default",
)
_RETIRED_BUTTON_LABEL = "Pin"  # a LABEL equality, never a substring: "Pinned to" is not it


def _screen_literals(path: Path) -> list[str]:
    return re.findall(r'"([^"\\]*)"', path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", _SCREEN_MODULES)
def test_no_retired_segment_in_screen_string_literals(name: str) -> None:
    literals = _screen_literals(_HARNESS / name)
    assert not [s for s in literals if any(segment in s for segment in _RETIRED)], name
    assert _RETIRED_BUTTON_LABEL not in [s.strip("✕ ") for s in literals], name


def test_the_three_label_tables_are_reworded_but_the_note_is_not() -> None:
    """The exemption is BY NAME: the two MODEL_NOTE_* tables keep the 2026-09-04 words.
    AC-47's third table is answer_footer's ORIGIN_LABELS, asserted here too — its values
    carry the word "default" and are exactly the approved phrases that must pass."""
    from pydocs_mcp.harness.ask_your_docs.answer_footer import ORIGIN_LABELS
    from pydocs_mcp.harness.ask_your_docs.question_scope import CODE_LABELS, SLICE_LABELS

    assert "whole branch" not in SLICE_LABELS.values() and "own code only" not in CODE_LABELS.values()
    assert not [v for v in ORIGIN_LABELS.values() if any(segment in v for segment in _RETIRED)]
    assert set(ORIGIN_LABELS.values()) == {
        "your default",
        "only these",
        "the agent's choice",
        "the server's default",
    }
    assert MODEL_NOTE_SLICE_WORDS[ScopeSlice.WHOLE_BRANCH] == "whole branch"
    assert MODEL_NOTE_CODE_WORDS[ScopeCode.OWN] == "own code only"
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"),), code=ScopeCode.OWN)
    assert scope_prefix(pin) == "[pinned scope: project=backend, branch=main, own code only] "


def test_the_picker_title_is_the_on_screen_wording() -> None:
    """AC-45's label: a popover's label is not reachable from AppTest, so this is its pin."""
    pytest.importorskip("streamlit")  # scope_picker imports streamlit at module level
    from pydocs_mcp.harness.ask_your_docs.scope_picker import PICKER_TITLE  # noqa: PLC0415 — streamlit-gated import

    assert PICKER_TITLE == "Where to search"
```

(That last test skips in the worktree venv and runs in the AppTest venv; the same assertion also runs in 11f's `test_sidebar_offers_exactly_one_where_to_search_button`, so the wording has a pin wherever streamlit is installed.)

`tests/harness/ask_your_docs/test_app_scope_vocabulary.py`:

```python
"""AC-47, rendered-tree half: neither page shows a retired segment in its default view,
and neither does the activity panel's scope line for a PIN question.

Runs where the [harness-ask-your-docs] extra is installed (the main checkout's venv,
PYTHONPATH at the worktree), skipped elsewhere. Both pages render their default view
with no bundle — the same workspace-less run the connection tests use.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("langgraph")  # the PIN run below drives the scripted ReAct graph

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
import pydocs_mcp.harness.ask_your_docs.reformulation as reformulation_module
from pydocs_mcp.harness.ask_your_docs.page_turn import TECHNICAL_TOGGLE_KEY
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import NO_SCOPE_CAPABILITIES
from pydocs_mcp.harness.ask_your_docs.strip_state import StripState, StripTarget

from ._agent_fakes import FakeActivityGraphBuilder
from ._connection_fakes import FakeBearer

# page_env is autouse: the MODULE-LEVEL import is what arms it (pytest reads autouse
# fixtures from the module's globals at collection; a function-local import is too late).
from ._page_fixtures import graph_page, page, page_env, write_config  # noqa: F401
from .test_app_activity import _every_text, _same_question
from .test_scope_vocabulary import _RETIRED, _RETIRED_BUTTON_LABEL

# Only STRING-valued elements: a Button's `.value` is its clicked bool and would raise
# `TypeError: argument of type 'bool' is not iterable` in the containment check.
def _shown_strings(at) -> list[str]:
    shown = [e.value for e in [*at.markdown, *at.caption]]
    shown += [b.label for b in at.button] + [c.label for c in at.checkbox]
    shown += [r.label for r in at.radio] + [s.label for s in at.selectbox]
    return [s for s in shown if isinstance(s, str)]


@pytest.mark.parametrize("build", [page, graph_page], ids=["chat", "graph"])
def test_rendered_pages_show_none_of_the_retired_segments(build) -> None:
    at = build()
    at.run()
    assert not at.exception, at.exception
    shown = _shown_strings(at)
    assert not [s for s in shown if any(segment in s for segment in _RETIRED)]
    assert not [s for s in shown if s in {_RETIRED_BUTTON_LABEL, "scope"}]


def test_the_activity_panels_scope_line_for_a_pin_shows_none_of_them_either(
    tmp_path, monkeypatch
) -> None:
    """AC-47's activity clause. The panel renders a scope line ONLY for a PIN question —
    `activity_scope_words` returns `{}` for DEFAULT — so this run seeds one strip target
    with "Only these" on and sends through the scripted graph `test_app_activity` uses,
    crossing the same stream → events → view path a real question does. The positive
    assertion comes first: without it a panel that rendered no line at all would pass.
    """
    monkeypatch.setenv("PYDOCS_CONFIG", write_config(tmp_path, model="main-a"))
    monkeypatch.setattr(agent_module, "build_agent", FakeActivityGraphBuilder(None))
    monkeypatch.setattr(reformulation_module, "reformulate", _same_question)
    at = page(
        connection_bearer=FakeBearer("tok-sentinel-abcd"),
        scope_capabilities=NO_SCOPE_CAPABILITIES,
        scope_strip=StripState(targets=(StripTarget("backend", ("main",)),), only_these=True),
    )
    at.run()
    at.chat_input[0].set_value("why does retry drop?").run()
    at.toggle(key=TECHNICAL_TOGGLE_KEY).set_value(True).run()
    assert not at.exception, at.exception
    shown = _every_text(at)
    assert [s for s in shown if s.startswith("Searching only in: ")]  # the line IS rendered
    assert not [s for s in shown if any(segment in s for segment in _RETIRED)]
```

- [ ] **Step 2: Run the tests to verify they fail or pass for the right reason**

Run: `cd $WT && .venv/bin/pytest tests/harness/ask_your_docs/test_scope_vocabulary.py -q` and `cd $WT && PYTHONPATH=$WT/python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest -o pythonpath=$WT/python tests/harness/ask_your_docs/test_app_scope_vocabulary.py -q`
Expected: the source-text half FAILS on any survivor 11b–11f left and on `scope_pin.CODE_SCOPE_WORDS["project"] == "own code only"`, which no earlier task touches (`"scope"` as a label, `"· default"` as an origin segment, a button labelled `Pin`) — fix the source, never the test; `CODE_SCOPE_WORDS` is a SCREEN table (`activity_labels.scope_note` reads it), so re-word it there, while `MODEL_NOTE_CODE_WORDS` keeps `own code only` for the model. If both halves pass at once, mutate one label (e.g. `CHANGE_LABEL = "scope"`) and watch BOTH fail before committing (the rendered half must catch it too, or `page_env` is not armed).

- [ ] **Step 3: Commit**

```bash
git add tests/harness/ask_your_docs/test_scope_vocabulary.py tests/harness/ask_your_docs/test_app_scope_vocabulary.py python/pydocs_mcp/harness/ask_your_docs/
git commit -m "ask-your-docs: vocabulary sweep — no pin / keep-for-next / scope-defaults words on screen"
```

---

### Task 11i: Docs — README paragraph, screenshot caption, CHANGELOG bullet

**Files:**
- Modify: `examples/harness/ask_your_docs_agent/README.md` (the `<img … alt="…">` at `:9-10`; the paragraph beginning "Scope is hidden by default." at `:162-176`)
- Modify: `CHANGELOG.md` (the `[Unreleased]` "ask-your-docs scope UI" bullet at `:43-50` — **rewritten in place**, never a second bullet: the feature has not shipped and the changelog serialization rule of CLAUDE.md applies)
- Test: `tests/test_doc_conformance.py`; the README jargon audit

**Interfaces:** none (docs). Satisfies: AC-25 (D14 words).

- [ ] **Step 1: The screenshot caption**

`alt="Ask-your-docs chat UI — the 'Searching in' strip above the question, with its Change… picker, beside the grounded-answer chat"`. The PNG itself is redrawn by the owner (decision §8); the caption ships ahead of it — note this in the PR description.

- [ ] **Step 2: Replace the README paragraph**

```markdown
One line above the question always says where the next one will search:
**Searching in all projects, each on its indexed branch** until you change
it. **Change…** opens the *Where to search* picker — one row per indexed
project (on servers that index several branches, a row lists its branches),
and a *More* block for project code vs dependencies and a package. What you
pick stays for the session. With one project ticked, the agent may still
look elsewhere when a question asks for it; tick **Only these** to stop that.
With two or more projects (or branches) the question runs as separate
searches — the strip says how many, and `ask_your_docs.scope.max_cells`
caps it — and the answer comes back as one labeled section per search.
You can also say it in the question: `how does routing work? in:backend`
searches that project for this one question and leaves the strip alone; an
unknown name is refused with the indexed names and nothing is sent
(`ask_your_docs.scope.tokens_enabled` turns the tokens off). Every answer
ends with one footer line naming the project, branch and commit each search
came from, whose choice it was, and whether the index is up to date, plus
buttons that apply when they do — `Ask this on <branch> too`, `Compare with
<base>`, `Keep searching <branch>`, *Show what changed*. The shipped values
come from `ask_your_docs.scope` in the YAML.
```

(The placeholders sit in code spans on purpose: a bare `<branch>` in Markdown prose is an unknown HTML tag to GitHub's renderer and is dropped, leaving "Ask this on too".)

Leave `README.md:62` ("pinned project / package / code scope") and `:270`/`:276` (the activity panel's "pinned scope" line): they describe the model-facing note, which stays.

- [ ] **Step 3: Rewrite the CHANGELOG bullet in place**

```markdown
- **ask-your-docs "Where to search"**: the sidebar scope pickers are replaced
  by one always-visible strip above the question ("Searching in …") with a
  *Where to search* picker (one row per indexed project, a *More* block for
  code / package, an *Only these* checkbox), sticky for the session and
  seeded from `ask_your_docs.scope` in YAML; two or more targets run as
  separate searches with labeled results (capped by `scope.max_cells`);
  `in:<project>` / `on:<branch>` tokens inside a question search there for
  that question only and refuse the send on an unknown name
  (`scope.tokens_enabled`); every answer carries a footer naming the
  project, branch, commit, whose choice it was and the index state, plus
  *Ask this on … too* / *Compare with …* / *Keep searching …* / *Show what
  changed* buttons. Branch and slice controls stay hidden until the server
  advertises `branch` / `changed` / `diff`.
```

- [ ] **Step 4: Audit and conformance**

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
    -not -path "*/node_modules/*" -not -path "*/.git/*" | \
    xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
grep -nE "Scope defaults|keep for next|popover|pin " examples/harness/ask_your_docs_agent/README.md CHANGELOG.md
grep -nE '(^|[^`])<(branch|base)>' examples/harness/ask_your_docs_agent/README.md   # a bare placeholder renders as nothing
cd $WT && .venv/bin/pytest tests/test_doc_conformance.py -q
```

Expected: no audit match; the second grep matches only the model-note lines (`:62`, `:270`, `:276`); the third grep matches nothing (every `<branch>` / `<base>` sits in a code span); conformance PASS.

- [ ] **Step 5: Commit**

```bash
git add examples/harness/ask_your_docs_agent/README.md CHANGELOG.md
git commit -m "ask-your-docs: README and changelog for the Where-to-search strip, picker and typed tokens"
```

---

### Task 11j: U0r exit — the full gate run

**Files:** none (a gate task).

- [ ] **Step 1: Restore the worktree tooling if needed**

`cd $WT && ls .venv/bin/pytest || (uv venv && uv sync --frozen --group dev)`; the AppTest venv is the main checkout's (`[harness-ask-your-docs]` installed there).

- [ ] **Step 2: Run the full gate set**

```bash
cd $WT
ruff format python/ tests/
ruff check python/ tests/
mypy python/pydocs_mcp
complexipy python/pydocs_mcp --max-complexity-allowed 15
vulture python/pydocs_mcp --min-confidence 80
.venv/bin/pytest tests/ --ignore=tests/test_parity.py -q
PYTHONPATH=$WT/python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest -o pythonpath=$WT/python tests/harness/ask_your_docs/test_app_scope_states.py tests/harness/ask_your_docs/test_graph_page_scope.py tests/harness/ask_your_docs/test_scope_vocabulary.py tests/harness/ask_your_docs/test_app_scope_vocabulary.py tests/harness/ask_your_docs/test_app_attachment.py tests/harness/ask_your_docs/test_app_image_attachment.py tests/harness/ask_your_docs/test_app_serve_session.py -q
uv lock --check
git checkout -- complexipy-snapshot.json
```

Expected: all green (AC-26). The last line restores the complexipy snapshot a local run rewrites in place — never commit it. If `pyproject.toml`'s streamlit line is touched, its WHY comment reads `# WHY: st.bottom (chat composer row) + stateful st.popover (where-to-search picker) + st.pills` and the floor stays `>=1.59` (Global Constraints). `vulture`: `scope_tokens.strip_scope_tokens` and `scope_picker.PICKER_TITLE` are public and imported; if it flags `_render_files_radio`'s U2 branch, the U2 AppTest in Task 17 is its consumer — add it to the vulture allowlist with a comment, do not delete it. `tests/harness/ask_your_docs/test_prompt_seed_parity.py`, `test_prompt_freeze.py`, `test_binding.py` and the AC-11 golden are the byte-identity fences: they must pass without regeneration.

- [ ] **Step 3: Commit whatever the formatter touched**

```bash
git add python/ tests/
git commit -m "ask-your-docs: U0r gate run (format only)"
```

(Skip when the tree is clean.)

**U0r exit:** draft PR #267 leaves draft with U0r's commits on top of U0's. Gate: AC-1, 2, 3, 4, 6b, 10, 11, 13, 14, 14b, 15, 18, 22, 23, 24, 25, 26, 27, 28, 29, 30, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 47, 48, 49, 50, 51 green; the U1 / U2 criteria are exercised against fakes in the same code base (AC-8, AC-9, AC-17, AC-31, AC-46 already pass in Tasks 6, 11e); AC-19, 20, 21, 21b are superseded (spec §10). Open with the owner before merging: O4 (the per-cell subprocess spawn is now on the common path — a two-project strip is the headline gesture), O7 (attached symbols as strip chips?), O8 (V7: the plan's working floor is `streamlit>=1.59`, where every U0r AppTest incl. `at.pills` is verified; the question is whether the spec's older `>=1.57` sentence must be re-verified or simply raised), O9 (`max_cells: 4` now on screen), O10 (the forced "Only these" is both an engine rule in `compile_strip_scope` and a UI state — this plan encodes both; the forced value is never persisted into `StripState.only_these`), P24 / P25 (two §6.10a grammar details and the AC-44 singular need one owner-side sentence each in the spec).

---

### Task 11k: Plan maintenance — U1 / U2 body edits, the deviation table, coverage and handoff

**Files:** this document only.

- [x] **Step 1: Applied in the amendment that added this stage (2026-09-15)** — Task 12b added (U1 activation of the inactive U0r surfaces), Task 15 and Task 18's docs text re-worded, Task 17 Step 4 rewritten against `scope_picker.py`, Task 10's "Task 15's compare overlay" corrected to Task 14, the deviation table renumbered P1–P16 with P6 / P7 / P11 / P16 retired and P17–P23 added, the Spec coverage and Handoff tables extended, the File map and Global Constraints rewritten against the as-built tree. Nothing for the executor; listed so the map's 11k is accounted for.

---

# Stage U1 — after multi-branch P1 (the `branch` selector, schema v18, `base_name` stamped)

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

### Task 12b: U1 activation of the U0r surfaces — branch pills, `on:` tokens, the branch-carrying chips

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/strip_state.py` (`initial_strip_state` / `strip_target_for` honour `branch_default` / `branch_name` once `branch_selector` is advertised; `compile_strip_scope`'s `capabilities=` keyword — written in 11b, inactive — is now threaded from both pages)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/scope_strip.py`, `scope_picker.py`, `app.py`, `pages/2_Graph.py` (thread `scope_caps` into `current_strip_state`, `_reset_picker` and both `compile_strip_scope` call sites; the picker's pills need nothing unless the live check finds a gap — the U0r body already renders them behind the capability)
- Test: `tests/harness/ask_your_docs/test_question_scope.py` (`TestStripScope` gains the U1 seed case and the U1 one-target soft case), `tests/harness/ask_your_docs/test_app_scope_states.py` (one U1 end-to-end case), `tests/harness/ask_your_docs/test_scope_tokens.py` (already U1-covered in 11a — re-run)

**Interfaces:**
- Consumes: `resolve_default_branch` (Task 4); `StripTarget`, `initial_strip_state`, `compile_strip_scope(capabilities=)` (11b); `parse_scope_tokens` (11a); `_ask_on_chip` (11e); `ScopeCapabilities.branch_selector` (Task 5).
- Produces: `strip_target_for(project, listing, config=None, capabilities=NO_SCOPE_CAPABILITIES)` — the seeded branch is `resolve_default_branch` of a DEFAULT scope built from `config` when `branch_selector` is true and that resolves to a listed name, else the stamped row (today's U0 rule); everything else in this task is **already written and inactive** — pills (`scope_picker_branches_<name>`, 11c), `on:` tokens (11a), "Ask this on … too" / "Keep searching …" / "Compare with …" (11e), chips carrying the branch (11c: a chip is a cell), the one-target soft case sending the picked branch (11b's `_soft_override`) — and this task only proves it live.
- The chip and the sent branch never disagree: on U1 a single target on `develop` with "Only these" OFF still compiles to DEFAULT (soft — the agent may go elsewhere), but with `branch_name=develop`, so `resolve_default_branch` sends what the chip shows instead of YAML's base. On U0 nothing branch-shaped is sent, so the U0 cases of 11b keep `branch_name == ""`.
- Satisfies: AC-36 (U1 half), AC-46, AC-17's U1 labels, AC-48's pills clause against a P1 server.

- [ ] **Step 1: Write the failing tests**

Append to `TestStripScope` in `test_question_scope.py`:

```python
    def test_u1_initial_target_follows_branch_default_not_the_stamped_row(self):
        """On U1 `branch_default: base` seeds backend's BASE (main), no longer its stamped
        row (feature/x) — the fixture disagrees on the two, so the rule is visible."""
        from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities

        u1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)
        state = initial_strip_state(ScopeDefaultsConfig(project="backend"), _LISTING, capabilities=u1)
        assert state.targets == (StripTarget("backend", ("main",)),)
        named = ScopeDefaultsConfig(project="backend", branch_name="feature/x")
        assert initial_strip_state(named, _LISTING, capabilities=u1).targets == (StripTarget("backend", ("feature/x",)),)
        assert initial_strip_state(ScopeDefaultsConfig(project="backend"), _LISTING).targets == (StripTarget("backend", ("feature/x",)),)  # U0 unchanged

    def test_u1_one_target_only_these_off_sends_the_picked_branch_as_a_soft_default(self):
        """The chip reads `backend · feature/x`; with Only-these OFF the compile is still
        DEFAULT, but its branch is the picked one, not YAML's base (main) — the fixture's
        stamped row differs from its base, so a compiler that discards the target's branch
        is caught. On U0 (no capability) the branch stays YAML's: nothing is sent anyway."""
        from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities

        u1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)
        target = (StripTarget("backend", ("feature/x",)),)
        soft = compile_strip_scope(target, False, self._CONFIG, _LISTING, capabilities=u1)
        assert soft.kind is ScopeKind.DEFAULT and soft.cells == (ScopeCell("backend", ""),)
        assert soft.branch_name == "feature/x"
        assert resolve_default_branch(soft, "backend", _LISTING) == "feature/x"
        assert compile_strip_scope(target, False, self._CONFIG, _LISTING).branch_name == ""  # U0 unchanged
        # An unlisted pick never becomes a branch_name (resolve_default_branch would log + send nothing).
        gone = (StripTarget("backend", ("gone",)),)
        assert compile_strip_scope(gone, False, self._CONFIG, _LISTING, capabilities=u1).branch_name == ""
```

(`resolve_default_branch` joins the test module's `question_scope` import.)

Append to `test_app_scope_states.py::TestTokens`:

```python
    def test_u1_on_token_pins_the_named_branch(self, workspace, spy):
        at = _connected(U1)
        at.run()
        at.chat_input[0].set_value("what is Bar? in:tooling on:develop").run()
        assert not at.exception, at.exception
        ((sent_text, sent_scope),) = spy.asked
        assert sent_text == "what is Bar?"
        assert sent_scope.cells == (ScopeCell("tooling", "develop"),)  # develop, not the stamped main
        user = next(m for m in at.session_state["messages"] if m["role"] == "user")
        assert user["scope_caption"] == "searched in: tooling · develop (from your question)"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $WT && .venv/bin/pytest tests/harness/ask_your_docs/test_question_scope.py -q -k u1_`
Expected: FAIL — `TypeError: initial_strip_state() got an unexpected keyword argument 'capabilities'`; `test_u1_one_target_only_these_off_…` passes already (11b wrote `_soft_override` behind the capability) — keep it as the activation pin, together with the AppTest case (11a + 11c + 11d shipped it inactive).

- [ ] **Step 3: Thread the capability into the seed**

```python
def strip_target_for(
    project: str,
    listing: WorkspaceBranchListing,
    config: ScopeDefaultsConfig | None = None,
    capabilities: ScopeCapabilities = NO_SCOPE_CAPABILITIES,
) -> StripTarget:
    """A target on the branch YAML asks for once the server can take one
    (branch_name, else the resolved base), else the project's stamped row."""
    if capabilities.branch_selector and config is not None:
        soft = resolve_question_scope_defaults(config, ScopeDefaultsOverride(project=project), listing)
        resolved = resolve_default_branch(soft, project, listing)
        if resolved:
            return StripTarget(project, (resolved,))
    row = listing.default_row(project)
    return StripTarget(project, (row.name,) if row else ())
```

(`resolve_default_branch` joins `strip_state.py`'s `question_scope` import.) `initial_strip_state(config, listing, capabilities=NO_SCOPE_CAPABILITIES)` passes both through; `current_strip_state` (scope_strip) and `_reset_picker` (scope_picker) take and forward `capabilities`; `app.py` / `2_Graph.py` pass `scope_caps` to them AND to both `compile_strip_scope(…, more=strip.more, capabilities=scope_caps)` calls, which activates 11b's `_soft_override` (the picked branch rides the one-target soft case). (`scope_capabilities.py` imports nothing from the strip modules, so the import is cycle-free.)

- [ ] **Step 4: Live check (manual, against a P1 build)**

Index a repository on two branches, start `harness-ask-your-docs --workspace ~/pydocs-index`, and verify: ticking a project in *Where to search* shows branch pills; picking two branches and asking yields one labeled section per branch (`## <project> · <branch>`) and a footer with two segments; `in:<project> on:<branch>` in a question is accepted and the strip is untouched afterwards; *Ask this on <branch> too*, *Compare with <base>* and *Keep searching <branch>* appear under a feature-branch answer, and *Keep searching* adds a chip to the strip. Record the outcome in the PR description.

- [ ] **Step 5: Run the tests and commit**

Run: `cd $WT && .venv/bin/pytest tests/harness/ask_your_docs -q` and the AppTest command of 11j.
Expected: PASS.

```bash
git add python/pydocs_mcp/harness/ask_your_docs/strip_state.py python/pydocs_mcp/harness/ask_your_docs/scope_strip.py python/pydocs_mcp/harness/ask_your_docs/scope_picker.py python/pydocs_mcp/harness/ask_your_docs/app.py python/pydocs_mcp/harness/ask_your_docs/pages/2_Graph.py tests/harness/ask_your_docs/test_question_scope.py tests/harness/ask_your_docs/test_app_scope_states.py
git commit -m "ask-your-docs: the strip seeds and sends the YAML branch default once the server takes a branch"
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
        that branch's rows (plus branch-less rows) once schema v18 stamps them —
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

Append to the README's "Where to search" paragraph (the three button names and the token sit in code spans — a bare `<branch>` in Markdown prose is dropped by GitHub's renderer; 11i's grep guards it): `On a server that indexes several branches (see the multi-branch design), each project row in the picker lists its branches, the strip's chips carry the branch, `on:<branch>` works inside a question, the footer names the branch and commit each search came from, the `Ask this on <branch> too`, `Compare with <base>` and `Keep searching <branch>` buttons appear under answers, and the graph page's **Compare with** selector colours symbols and edges by change state (changed / added / removed) with a **changed only** toggle.` Add a CHANGELOG bullet: `ask-your-docs: branch pills in the Where-to-search picker, on:<branch> tokens, the base-branch default, rule 7 of the system prompt, the "Ask this on … too" / "Compare with …" / "Keep searching …" buttons and the graph compare overlay activate on servers that advertise the branch selector.` No "pin", no "popover", no "panel" in either.

- [ ] **Step 2: Gates**

```bash
ruff format python/ tests/ && ruff check python/ tests/ && mypy python/pydocs_mcp && complexipy python/pydocs_mcp --max-complexity-allowed 15 && vulture python/pydocs_mcp --min-confidence 80 && pytest tests/ --ignore=tests/test_parity.py -q && uv lock --check
git checkout -- complexipy-snapshot.json
```

Expected: green.

- [ ] **Step 3: Live check (manual, against a P1 build)**

Index a repository on two branches (`pydocs-mcp index . && pydocs-mcp index . --branch feature/x`, the P1 CLI), start `harness-ask-your-docs --workspace ~/pydocs-index`, and verify: the *Where to search* picker shows branch pills under a ticked project; choosing two branches and asking a question yields one labeled section per branch (`## <project> · <branch>`) and a footer with two segments; the "Compare with main" button appears under a feature-branch answer; the graph page's Compare with colours changed symbols. (Task 12b's live check covers the tokens and the other two buttons.) Record the outcome in the PR description.

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

### Task 17: The "merged" picker group, the catalog tombstone marker, and "Show what changed"

**Files:**
- Modify: `python/pydocs_mcp/harness/ask_your_docs/question_scope.py` (`branch_option_ids`, `cells_from_branch_selection`)
- Modify: `python/pydocs_mcp/harness/ask_your_docs/scope_picker.py` (`_render_branch_row` lists the merged group after the live names; `_use_these` folds the forced diff slice; the "Which files" radio `scope_picker_files` is already there from 11c)
- Test: `tests/harness/ask_your_docs/test_question_scope.py`, `tests/harness/ask_your_docs/test_prompt_seam.py`, `tests/harness/ask_your_docs/test_app_scope_states.py`

**Interfaces:**
- Consumes: `WorkspaceBranchListing.merged()` (Task 3); `render_catalog(show_merged=)` (Task 3, wired through `_assemble_prompt` in Task 7); the `SHOW_DIFF` chip (Task 8, re-worded "Show what changed" in 11e); `_render_branch_row`, `_use_these`, `_render_files_radio` (11c).
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
def test_merged_group_and_which_files_on_u2(tmp_path, page_env):
    """AC-32 (page half) + AC-48 (the "Which files" clause): the tombstone follows the
    live names in the tooling row's pills, and "Which files" appears under More."""
    sha = "3e1a9c2" + "0" * 33
    make_bundle(
        tmp_path / "ws" / "tooling_0123456789.db",
        project="tooling",
        branches=[
            ("main", TOOLING_MAIN, None, 1, "active", None),
            ("develop", TOOLING_DEVELOP, "main", 0, "active", None),
            ("feature/old", TOOLING_MAIN, "main", 0, "merged", sha),
        ],
    )
    u2 = ScopeCapabilities(branch_selector=True, changed_slice=True, diff_slice=True)
    at = _app(u2, scope_strip=_strip(TOOLING))
    at.run()
    assert not at.exception, at.exception
    pills = at.pills(key="scope_picker_branches_tooling")
    assert list(pills.options) == ["main", "develop", "feature/old (merged into main @3e1a9c2)"]
    assert any(r.key == "scope_picker_files" for r in at.radio)
    pills.select("feature/old (merged into main @3e1a9c2)").run()
    at.button(key="scope_picker_use").click().run()
    assert not at.exception, at.exception
    assert at.session_state["scope_strip"].targets == (StripTarget("tooling", ("main", sha)),)
    assert at.session_state["scope_strip"].more.slice is ScopeSlice.DIFF_HUNKS  # forced by the landing sha
```

(`pills.options` holds the FORMATTED strings; the option ids are `name:` / `merged:` prefixed — see Step 4.)

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
    return (ordered_unique(cells) or (ScopeCell(project, ""),)), forced  # public since 11b
```

Add both names (and the two prefixes) to `__all__`.

- [ ] **Step 4: Wire the picker's pills (rewritten under U0r — the 2026-09-04 popover / defaults-panel patches targeted functions 11c deleted)**

In `scope_picker.py`, `_render_branch_row`'s U1 body lists the option ids with their labels — live names first, the merged group after them (owner decision D14 §3) — and returns cells, not names:

```python
    labels = branch_option_ids(name, listing, capabilities)  # live names, then the merged group (U2)
    key = f"{_WIDGET_PREFIX}branches_{name}"
    wanted = [f"{NAME_OPTION_PREFIX}{b}" for b in (target.branches if target else strip_target_for(name, listing).branches)]
    _seed(key, [o for o in wanted if o in labels])
    # A closed list by construction: pills accept no free text (R6).
    picked = st.pills("Branches", list(labels), selection_mode="multi", format_func=labels.get, key=key)
    cells, _ = cells_from_branch_selection(name, tuple(picked), listing)
    return StripTarget(name, tuple(c.branch for c in cells))
```

and `_use_these` folds the forced slice: it receives `diff_forced: bool` (true when any ticked row selected a `merged:` entry — collect it in `_render_project_rows`) and writes `replace(current, targets=targets, more=replace(more, slice=ScopeSlice.DIFF_HUNKS) if diff_forced else more)` (`current` is the session's `StripState`, so the user's `only_these` tick survives, as in 11c). `strip_target_for` is imported from `strip_state`; `branch_option_ids` / `cells_from_branch_selection` stay in `question_scope`. A tombstone therefore reaches the strip as the cell `(project, <landing sha>)` with the diff slice, exactly as `cells_from_branch_selection` returns it; the chip reads `tooling · 3e1a9c20… ✕`. There is no defaults-panel half any more: the 2026-09-04 rule "a merged entry maps to the base default" (retired plan deviation P16) has nothing to apply to — a *default* cannot carry a forced slice, and the strip's soft state (one target, "Only these" off) simply never holds a landing sha because the compile of a two-branch target is a PIN. Import `NAME_OPTION_PREFIX, branch_option_ids, cells_from_branch_selection` from `question_scope`.

- [ ] **Step 5: Run the tests**

Run: `cd $WT && .venv/bin/pytest tests/harness/ask_your_docs/test_question_scope.py tests/harness/ask_your_docs/test_prompt_seam.py tests/harness/ask_your_docs/test_answer_footer.py -q` and `cd $WT && PYTHONPATH=$WT/python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pytest -o pythonpath=$WT/python tests/harness/ask_your_docs/test_app_scope_states.py -q`
Expected: PASS (the "Show what changed" chip's AC-17 / AC-31 tests from Task 8 / 11e are its activation pins).

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/harness/ask_your_docs/question_scope.py python/pydocs_mcp/harness/ask_your_docs/scope_picker.py tests/harness/ask_your_docs/test_question_scope.py tests/harness/ask_your_docs/test_prompt_seam.py tests/harness/ask_your_docs/test_app_scope_states.py
git commit -m "ask-your-docs: merged picker group pins landing shas with scope=diff; tombstone catalog marker"
```

---

### Task 18: U2 activation — E9 display, docs, gates

**Files:**
- Modify: `examples/harness/ask_your_docs_agent/README.md`, `CHANGELOG.md`

- [ ] **Step 1: E9 is already the transcript's behavior — verify it**

A landing unit outside the retention window makes the server return an `InvalidArgumentError` naming `git.diff_chunks.retain` and the `branches pin` command; the adapter renders it as an error tool message, the agent reports it, and the footer shows the cell with no sha. Verify manually against a P2 server with `git.diff_chunks.retain.landings: 1` and a two-landing history: the picker's merged entry for the older landing produces a visible error in the answer, and the strip's chip stays so the user can remove it. No code change; record the outcome in the PR description.

- [ ] **Step 2: Docs**

README, append to the "Where to search" paragraph: `On servers that index change slices, the picker's **More** block offers **Which files** (everything on the branch / only files this branch changed / only the changes themselves), merged branches are listed after the live names and search their landing commit's changes, and a "Show what changed" button follows answers.` CHANGELOG bullet: `ask-your-docs: the "Which files" choice, merged branches in the Where-to-search picker (landing shas with scope=diff) and the "Show what changed" button activate on servers that advertise the changed / diff scope values.`

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

Gate: AC-12 (U2 half), AC-17 ("Show what changed"), AC-48 ("Which files" clause), AC-31 (`SHOW_DIFF`), AC-32 green; the E9 manual check recorded.

---

## Deviations from the spec (recorded, not silent)

Plan-side deviations are numbered **P1–P25** (renamed from D1–D16 on 2026-09-15: the spec's own owner decisions are D1–D14, and "D14" in this document must always mean the spec's). Retired rows are kept, struck through in the "Plan does" column, so the history stays greppable.

| # | Spec says | Plan does | Why |
|---|---|---|---|
| P1 | `ScopeSlice`, `ScopeCode`, `ScopeBranchDefault` live in `question_scope.py` (§6.2) | they live in `retrieval/config/ask_your_docs_scope_models.py` (re-exported from `ask_your_docs_models.py`, as built) and are re-exported from `question_scope.py` under the same names | the config sub-model is mypy-checked while `harness/ask_your_docs/` is mypy-excluded; a checked module must not import an excluded one |
| P2 | the interceptor reads the listing and the capabilities (§6.3) without saying how they reach it | a frozen `ScopeRuntime(listing, capabilities, max_cells)` on a third contextvar, set by `ask(scope_runtime=)`; `EMPTY_SCOPE_RUNTIME` when absent | same isolation rationale as the scope contextvar; the eval binding never sets it |
| P3 | `render_answer_footer(observations, listing, config)` (§6.8); `apply_follow_up_chip(chip, strip_scope)` (§6.9) | the footer also takes `capabilities` (the U1 `on:` hint); `apply_follow_up_chip` also takes the defaults | the footer needs the listing's sha, the project count and the capability; a new pin needs the active scope's slice / code / package |
| P4 | chips are a U1 deliverable (§6.12) | the chip code and its tests land in U0 (Task 8), inactive behind `ScopeCapabilities`; U1 / U2 activate them | §6.12 also says U1 / U2 code is written with U0 against fakes; one module, one PR |
| P5 | `render_where_to_search_picker(…, config, listing, capabilities)` | also takes the catalog and the current `StripState` | the package pool comes from the catalog (today's picker rule); the rows re-seed from the strip |
| P6 | *(2026-09-04)* idle popover label `""` with an icon (§6.10) | ~~label `scope` with the icon~~ **retired by U0r**: there is no composer popover; the strip's button is "Change…" | D14 |
| P7 | *(2026-09-04)* merged text labels `## <project> · <branch>` (§6.4) | ~~`## <project>` when the cell has no branch~~ **retired by U0r**: the amended §6.4 rule 3 says the label names what was SENT, which is the same output for a different reason and covers cells that DO carry a branch on U0 (11e) | D14 |
| P8 | the footer segment always carries a slice (§6.8) vs AC-34's `Searched demo · no branch (the server's default) · index up to date` | the slice is omitted when the branch is unknown and when it is the default | slices are branch-relative; both AC-18 and AC-34 hold |
| P9 | a replaced argument renders `the agent's choice → your default` (§6.8) | `CellObservation.replaced: bool` next to the four-member `BranchOrigin` | keeps the enum at the four members the spec names |
| P10 | — | the chat page reads the capability record from the cached agent build at render time (the first render of a workspace starts the serve subprocess); tests seed `st.session_state["scope_capabilities"]` | the capability is only knowable from the advertised schemas; an AppTest must never spawn the server against a fixture bundle |
| P11 | *(2026-09-04)* AC-21 asserts the popover button label; AC-21b the one-shot pin lifecycle | ~~the label is pinned by `pin_summary_label`'s unit test; …~~ **retired by U0r**: AC-21 / AC-21b are superseded by AC-50 / AC-41, both asserted in AppTest against the strip (11c, 11d) with a full send through the `_AgentStackSpy` pattern | D14 |
| P12 | `agent.py` target ≤ 468 lines | ≈ 487 lines (under the 500 gate) | the wrapper, the record and the extra keywords outweigh the removed dict code |
| P13 | `weave_attachments(attached: list[str], …)` becomes `AttachedSymbol`-typed (§6.10) | accepts both `AttachedSymbol` and plain strings | the existing tests and any session state seeded with strings keep working |
| P14 | — | `WorkspaceBranchListing.has_branch("", name)` answers "any project has it" | a model may name a branch on a union request; the server resolves per bundle |
| P15 | the popover closes by writing `False` to its key before `st.rerun()` (§6.10 / V2) | the write happens inside the "Use these" / "Reset" `on_click` callbacks; every other strip / picker write (chip `✕`, "Clear", "Keep searching", the "Only these" tick's `on_change`) is a callback or runs before the strip renders — with ONE render-path exception: row 2 forces `scope_strip_only_these` to `True` at two or more cells immediately before that checkbox instantiates in the same run, which Streamlit allows (V2); the forced value never reaches `StripState.only_these` | Streamlit refuses writes to a widget's key after the widget is instantiated in the same run; callbacks run before instantiation |
| P16 | *(2026-09-04)* the defaults panel's Branch selectbox lists the merged group (§6.10) | ~~it lists it but a merged entry maps to the base default~~ **retired by U0r**: there is no defaults panel; the merged group lives in the picker's pills only (Task 17) | D14 |
| P17 | `compile_strip_scope(targets, only_these, config, listing)` (§6.1); the strip state is `StripState(targets, only_these)` (§2) | two keywords: `more: ScopeDefaultsOverride = ScopeDefaultsOverride()` carrying the picker's "More" values, and `capabilities: ScopeCapabilities = NO_SCOPE_CAPABILITIES` so the U1 one-target soft case sends the branch the chip shows (`branch_name=<picked>`, still DEFAULT) instead of YAML's base; `StripState(targets, only_these, more)` holds the user's own "Only these" tick — the checkbox key is seeded from it each run and written back by `on_change`, and the forced-on state at ≥ 2 cells is never persisted; the value objects live in `strip_state.py`, not `question_scope.py` | the spec says the "More" values ride through every case but gives them no parameter; a widget key alone is dropped by Streamlit on any page that does not render the strip (the graph page) and would silently reset the tick, and a forced value left on the key would turn a one-cell strip into a hard PIN the user never asked for; `question_scope.py` would land at ~490 lines with the strip objects and grow again in 12b / Task 17 |
| P18 | a U0 strip target "shows the stamped branch, informational" (§6.7) | the target CARRIES the stamped branch (`StripTarget(project, (default_row.name,))`), so a U0 strip cell is `(project, <stamped>)` — the `test_ac6b_…` shape; a token cell on U0r is `(project, "")` per §6.10a | the chip key `scope_chip_<project>_<branch>` and the AC-50 caption need the branch; `cell_arguments` never sends it while `branch_selector` is false, and the label names what was sent |
| P19 | `scope_panel.py` owns the strip and the picker (§6.13, with a "split if it does not fit" clause) | split: `scope_strip.py` + `scope_picker.py` (300 lines each), `scope_panel.py` keeps the shared fragments | the file was at 467/500 before U0r; the spec allows the split |
| P20 | `derive_follow_up_chips(observations, listing, capabilities, strip_scope)` (§6.9) | plus `asked: str = ""`, the answered question's text | an `ASK_ON` chip re-sends the same question; chips are computed once and stored, so the text must be captured at derivation |
| P21 | `render_answer_footer(…, config)` reads `tokens_enabled` / `footer_hint` (§6.8); `parse_scope_tokens(text, listing, capabilities, strip_targets, tokens_enabled)` (§6.10a) | the two flags reach every pure module as parameters (`ScopeDefaultsConfig` for the footer, keyword booleans for the parser); the parser also takes `max_cells` (E4 is refused in the parser, before any call) and `strip_projects: tuple[str, ...]` — the project names, which is all the lone-`on:` rule reads; `derive_follow_up_chips` takes `max_cells` too (the "Keep searching" cap); only the two pages read `AppConfig` | the pure modules stay config-free and testable without an `AppConfig.load()` |
| P22 | the strip's chips are "one chip per target" (§6.7) | one chip per CELL (`scope_chip_<project>_<branch>`), removal is per cell; on U0 the two coincide | a U1 target with two branches needs two removable chips or the second branch has no `✕` |
| P23 | AC-38 says an `on:` token is refused "on a server that does not advertise branch" | refused before any name is checked — `in:backnd on:main` yields the branch refusal, not the project one | §6.10a's own sentence ("before the name is even checked"); the test pins the order |
| P24 | §6.10a's grammar: whitespace-delimited tokens, `in:` validated by `listing.knows_project`, a bare `on:` valid when exactly one project is in play; the unknown-branch message lists `Indexed: main, feature/retry` | (a) trailing `?.,;:!` are stripped from a token's name before the lookup, so `in:backend?` names `backend` and the whole word leaves the sent text; (b) `in:` matches `listing.project_names` only — a bundle stem is refused with the same "Indexed:" list; (c) a bare `on:` that precedes an `in:` anywhere later in the question is refused with `on:<name> must come after its in:<project> (found in:<later> later in the question). Nothing was sent.`, even when one project is in play; (d) the unknown-branch message lists the pickable names in listing order, default row first (`Indexed: feature/retry, main`) | (a) a person ends a sentence with the token and the punctuation is never a name; refusing `No project named 'backend?'` would block a send the person clearly meant; (b) the refusal must list the complete accepted set (R6's closed list) and a stem must never reach a caption or a chip; (c) the later `in:` says which project was meant, so attaching to the lone project would send somewhere the person did not name; (d) `_attach_branch` reads `listing.pickable(...)`, which is default-first — the spec's `main, feature/retry` is the same set in a different order and needs one sentence in §6.10a (owner-side edit; the plan's implementation is the one the fixture pins) |
| P25 | the picker's preview caption is `Next question runs N searches: <cells> · N of M` (§6.7, AC-44) | `1 search` for a single cell, `N searches` otherwise | grammar; the spec's fixed form is the N ≥ 2 shape — the singular needs one parenthetical in AC-44 (owner-side edit) |

## Spec coverage

| AC | Task | AC | Task | AC | Task |
|---|---|---|---|---|---|
| AC-1 | 6 | AC-15 | 5 | AC-31 | 8, 11e (words, `ASK_ON`), 11d (page) |
| AC-2 | 6 | AC-16 | 14 | AC-32 | 17 |
| AC-2b | 12 | AC-17 | 8, 11e (one chip per kind; U2 half activated by 17) | AC-33 | 11c (`scope_picker_reset`) |
| AC-3 | 6 | AC-18 | 11e | AC-34 | 11e |
| AC-4 | 6 (code), 16 (slice) | AC-19 | superseded → AC-49 | AC-35 | 11b |
| AC-5 | 12 | AC-20 | superseded → AC-48 | AC-36 | 11a (U1 half live in 12b) |
| AC-6 | 12 (label clause: 11e) | AC-21 | superseded → AC-50 | AC-37 | 11a, 11d |
| AC-6b | 6 (label clause: 11e) | AC-21b | superseded → AC-41 | AC-38 | 11a, 11d |
| AC-7 | 12 | AC-22 | 1, 11g | AC-39 | 11a, 11b, 11d |
| AC-8 | 6 | AC-23 | 4 | AC-40 | 11b, 11d |
| AC-9 | 6 (label shape: 11e) | AC-24 | 7 | AC-41 | 11c, 11d |
| AC-10 | 6 | AC-25 | 11i | AC-42 | 11c |
| AC-11 | 7 | AC-26 | 11j, 15, 18 | AC-43 | 11e |
| AC-12 (U1 / U2) | 13 / 17 | AC-27 | 7 | AC-44 | 11c |
| AC-13 | 3 | AC-28 | 4, 11b (model-note tables) | AC-45 | 11f |
| AC-14 | 2 | AC-29 | 4 | AC-46 | 11e (against fakes), 12b |
| AC-14b | 2, 3 | AC-30 | 4, 11b, 11f | AC-47 | 11h |
| E1–E16 | 6, 11a (E13, E14, E15, E4 token path), 11c (E16), 11d (E12), 12, 16, 17, 18 | AC-48 | 11c (pills clause live in 12b; "Which files" in 17) | AC-49 / AC-50 / AC-51 / AC-52 | 11c / 11c + 11d / 11e / 11c |

Spec sections → tasks: §6.1 → 1, 4, 11b; §6.2 → 4; §6.3 → 6, 12, 16; §6.4–§6.4a → 6, 11c (picker matrix), 11e (label rule), 12; §6.5 → 6, 7; §6.6 → 7, 13; §6.7 → 11c, 11d; §6.8 → 11e; §6.9 → 11e, 11d (page wiring); §6.10 → 2, 3, 11c, 17; §6.10a → 11a, 11d, 12b; §6.11 → 11f, 14; §6.12 → 5, 7, 12b; §6.13 → file map; §7 → 1, 11g; §8 → 6, 7, 11b (0-target byte-identity), 11d (token neutrality), 13; §9 → 6 (E1, E2, E4, E5), 11a (E13–E15), 11c (E16), 11d (E12), 2 (E8), 9 (E7, E10, E11 — now the picker's), 18 (E9); §11 → every test file above; Amendments → this stage.

## Handoff

Four PRs' worth of work in three PRs against `main`: **U0 + U0r** (Tasks 1–11 then 11g–11j) is draft PR #267, which leaves draft once 11j is green — U0r's commits sit on top of U0's, no rewrite of the U0 history; **U1** (Tasks 12, 12b, 13–15) after the P1 plan's contract PR; **U2** (Tasks 16–18) after the P2 plan's contract PR and P2.8. Open decisions the owner must settle (spec §12): before PR #267 merges — O4 (one held session for the app: fan-out is now the common multi-target path; `max_cells` bounds the spawn but the per-cell subprocess is on the headline gesture), O7 (attached symbols as strip chips? — the plan keeps them in their own row), O9 (`max_cells: 4` now on screen — the plan keeps 4), O10 (forced "Only these" — the plan encodes it as BOTH an engine rule in `compile_strip_scope` and a UI state); before U1 starts — O1 (indexed-but-unpinned branch under a pin — the strict reading), O2 (merged-group sequencing and label — `feature/old (merged into main @3e1a9c2)` gated on `diff_slice`), O3 (`branch_default: base` — encoded), O5 (catalog branch listing gated on `branch` — encoded), O6 (per-project freshness — the footer caveat stands). O8 / V7 (`at.pills` on the pinned floor) are **closed**, not owner items: `pyproject.toml:170`, the spec and this plan's Global Constraints all floor at `streamlit>=1.59`, where 11c's AppTests exercise it. Also for the owner, spec-side: the P24 grammar details (glued punctuation, project names only, the `on:`-before-`in:` refusal, the default-first order of the unknown-branch list) and the P25 singular belong in §6.10a / AC-44 — this plan does not edit the spec.
