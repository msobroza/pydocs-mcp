# Task-Shaped Surface, Part A (Slice 2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two-tool MCP surface (`search`/`lookup`) with six task-shaped tools — `get_overview`, `search_codebase`, `get_symbol`, `get_context`, `get_references`, `get_why` — mirrored 1:1 by CLI subcommands, documented from one `TOOL_DOCS` source, with next-step pointers re-targeted to the new tool names.

**Architecture:** Spec §D1/§D2/§D3/§D13 + §D16-2 (`docs/superpowers/specs/2026-07-06-task-shaped-surface-decisions-swe-qa-design.md`). A new `ToolRouter` (application layer) fronts the existing `MultiProjectSearch`/`MultiProjectLookup` bodies and per-project services; every response goes through the slice-1 `ResponseEnvelope`. `get_why` is wired to a `NullDecisionService` (real service lands in slice 3). Part B (separate plan, same branch) upgrades rendering: skeleton mode, centrality-ranked overview blocks, entry points, communities. Until then `get_context` renders each target with the existing hop-graded renderer, and `get_overview` ships the basic card (stats, package/module map, doc coverage).

**Tech Stack:** Python 3.11, FastMCP, pydantic v2, argparse, pytest.

**Conventions for every task:** run from repo root; venv interpreter `/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python` (system python is 3.8 and cannot import the package). Before each commit: `ruff check python/ tests/` + `ruff format python/ tests/`. New functions must stay ≤ 15 cognitive complexity (CI complexipy gate — if you run `complexipy` locally, `git checkout complexipy-snapshot.json` afterwards). Plain commits, NO Co-Authored-By trailers, no `--author`.

**Shared code facts** (verified 2026-07-06): `SearchInput`/`LookupInput` live at `python/pydocs_mcp/application/mcp_inputs.py:151-245` with YAML-wired `_LIMIT_DEFAULT`/`_SEARCH_LIMIT_DEFAULT` via `configure_from_app_config` (line 93). `server.py`: `_build_project_services` (33-57), `build_routers` (77-96), tool handlers (150, 212), FastMCP `instructions` (138-141), `run()` calls `configure_from_app_config` at 123. `__main__.py`: `_build_parser` (49-293; search 179-237, lookup 239-291), `_run_search` (542-569), `_run_lookup` (572-588), `_cmd_search`/`_cmd_lookup` (753-758); CLI tests patch `sys.argv` then call `main()`. `MultiProjectSearch`/`MultiProjectLookup` (slice 1) carry `envelope: ResponseEnvelope | None` and body methods `_search_body`/`_lookup_body`. Pointer machinery in `application/formatting.py`: `pointer_token`, `_POINTER_RE`, `resolve_pointers`, `strip_pointers`. `ProjectServices` = `(project, docs, api, lookup)`. `LookupService.lookup` dispatches show∈{default,tree,callers,callees,inherits,impact,context}. `ChunkFilterField` includes `QUALIFIED_NAME`-equivalent metadata key `qualified_name` and `SOURCE_PATH`. `DocumentTreeStore.load_all_modules(package) -> dict[str, DocumentNode]`. `PROJECT_PACKAGE_NAME = "__project__"` (models.py:35). Null pattern: `null_services.py:62-113`, errors in `mcp_errors.py`.

---

### Task 1: New input models + YAML wiring

**Files:**
- Modify: `python/pydocs_mcp/application/mcp_inputs.py`
- Test: `tests/application/test_tool_inputs.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/application/test_tool_inputs.py`:

```python
"""Input models for the six task-shaped tools (spec §D1)."""

import pytest
from pydantic import ValidationError

from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    OverviewInput,
    ReferencesInput,
    SymbolInput,
    WhyInput,
)


def test_symbol_input_defaults_and_depth_enum() -> None:
    payload = SymbolInput(target="pkg.mod.X")
    assert (payload.depth, payload.project) == ("summary", "")
    assert SymbolInput(target="t", depth="source").depth == "source"
    with pytest.raises(ValidationError):
        SymbolInput(target="t", depth="full")
    with pytest.raises(ValidationError):
        SymbolInput(target="")  # empty target is search_codebase's job


def test_context_input_targets_bounds() -> None:
    assert ContextInput(targets=["a"]).targets == ["a"]
    with pytest.raises(ValidationError):
        ContextInput(targets=[])           # spec §D1: empty list = validation error
    with pytest.raises(ValidationError):
        ContextInput(targets=["x"] * 21)   # max 20


def test_references_input_direction_enum_and_limit() -> None:
    payload = ReferencesInput(target="pkg.mod.f")
    assert payload.direction == "callers"
    assert payload.limit >= 1  # YAML-wired default, not a literal here
    for direction in ("callers", "callees", "inherits", "impact"):
        assert ReferencesInput(target="t", direction=direction).direction == direction
    with pytest.raises(ValidationError):
        ReferencesInput(target="t", direction="uses")


def test_why_input_shapes() -> None:
    assert WhyInput().query == "" and WhyInput().targets is None
    assert WhyInput(query="auth").query == "auth"
    assert WhyInput(targets=["a", "b"]).targets == ["a", "b"]
    with pytest.raises(ValidationError):
        WhyInput(targets=[])  # empty list is an error, not dashboard mode


def test_overview_input() -> None:
    assert OverviewInput().package == ""
    assert OverviewInput(package="fastapi", project="backend").project == "backend"
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/application/test_tool_inputs.py -q`
Expected: FAIL — `ImportError: cannot import name 'ContextInput'`

- [ ] **Step 3: Implement**

In `python/pydocs_mcp/application/mcp_inputs.py`, after `LookupInput` (line ~245), add — reusing the module's existing `_check_project` / limit-wiring idioms exactly as `LookupInput` uses them:

```python
class OverviewInput(BaseModel):
    """get_overview — orientation card scope (spec §D1/§D17)."""

    package: str = ""
    project: str = ""


class SymbolInput(BaseModel):
    """get_symbol — known dotted path (spec §D1). depth='source' is the §D7 recovery contract."""

    target: str = Field(min_length=1)
    depth: Literal["summary", "tree", "source"] = "summary"
    project: str = ""


class ContextInput(BaseModel):
    """get_context — batched targets under one shared budget (spec §D1)."""

    targets: list[str] = Field(min_length=1, max_length=20)
    project: str = ""


class ReferencesInput(BaseModel):
    """get_references — graph traversal incl. ranked transitive impact (spec §D1)."""

    target: str = Field(min_length=1)
    direction: Literal["callers", "callees", "inherits", "impact"] = "callers"
    project: str = ""
    limit: int = Field(default_factory=lambda: _LIMIT_DEFAULT, ge=1)

    _check_limit = LookupInput.__pydantic_decorators__  # placeholder — see note


class WhyInput(BaseModel):
    """get_why — decision search / per-target governing decisions / dashboard (spec §D11)."""

    query: str = ""
    targets: list[str] | None = Field(None, min_length=1, max_length=20)
    project: str = ""
```

NOTE on `ReferencesInput` limit validation: do NOT copy decorators via `__pydantic_decorators__` (the line above is a marker to replace) — instead replicate the exact `@field_validator("limit")`-style max-check `LookupInput` uses (read `LookupInput._check_limit_max` at mcp_inputs.py:199-245 and mirror it verbatim so `_LIMIT_MAX` applies). Reuse the module's existing `_check_project` validator on every model that has `project` the same way `LookupInput` attaches it.

- [ ] **Step 4: Run tests + the existing input suite**

Run: `PYTHONPATH=python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/application/test_tool_inputs.py tests/ -q -k "mcp_input or tool_input"`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/mcp_inputs.py tests/application/test_tool_inputs.py
git commit -m "feat(inputs): pydantic models for the six task-shaped tools"
```

---

### Task 2: `TOOL_DOCS` — single-source tool documentation + lint test

**Files:**
- Create: `python/pydocs_mcp/application/tool_docs.py`
- Test: `tests/application/test_tool_docs_lint.py` (create)

- [ ] **Step 1: Write the failing lint test** (this test IS the §D13 acceptance)

Create `tests/application/test_tool_docs_lint.py`:

```python
"""§D13 docstring contract: six sections, size budgets, cross-references."""

from pydocs_mcp.application.tool_docs import SERVER_INSTRUCTIONS, TOOL_DOCS

_TOOLS = (
    "get_overview", "search_codebase", "get_symbol",
    "get_context", "get_references", "get_why",
)
_REQUIRED_MARKERS = (
    "When to use", "When NOT to use", "Workflow", "Response contract", "Examples",
)
_CHARS_PER_TOKEN = 4
_PER_TOOL_TOKEN_BUDGET = 500
_TOTAL_TOKEN_BUDGET = 2400


def test_all_six_tools_documented() -> None:
    assert set(TOOL_DOCS) == set(_TOOLS)


def test_each_doc_has_required_sections() -> None:
    for name, doc in TOOL_DOCS.items():
        for marker in _REQUIRED_MARKERS:
            assert marker in doc, f"{name} missing section {marker!r}"


def test_batching_guidance_where_targets_exist() -> None:
    for name in ("get_context", "get_why"):
        assert "ONE call" in TOOL_DOCS[name], f"{name} must carry batching guidance"


def test_size_budgets() -> None:
    total = 0
    for name, doc in TOOL_DOCS.items():
        tokens = len(doc) // _CHARS_PER_TOKEN
        assert tokens <= _PER_TOOL_TOKEN_BUDGET, f"{name}: {tokens} tokens > 500"
        total += tokens
    assert total <= _TOTAL_TOKEN_BUDGET, f"surface total {total} tokens > 2400"


def test_docs_reference_sibling_tools_not_old_surface() -> None:
    joined = "\n".join(TOOL_DOCS.values()) + SERVER_INSTRUCTIONS
    assert "lookup(" not in joined and 'show="' not in joined


def test_project_scoped_example_everywhere() -> None:
    for name, doc in TOOL_DOCS.items():
        assert 'project="' in doc, f"{name} missing a project= example"
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/application/test_tool_docs_lint.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the docs module**

Create `python/pydocs_mcp/application/tool_docs.py` with EXACTLY this content (the docstrings are the §D13 deliverable — consumed verbatim by both `server.py` tool registrations and CLI `--help`):

```python
"""Single source of truth for tool documentation (spec §D13).

``TOOL_DOCS[name]`` becomes the MCP tool description AND the CLI subcommand
help text; ``SERVER_INSTRUCTIONS`` is the FastMCP server-level orientation.
The §D13 lint test enforces the six-section structure and size budgets, so
edits here fail fast instead of drifting.
"""

from __future__ import annotations

_WORKFLOW = (
    "Workflow: get_overview → search_codebase → get_context → "
    "get_symbol / get_references; get_why before architectural changes."
)
_CONTRACT = (
    "Response contract: every response starts with an [index: …] freshness "
    "line — silence means current; a [⚠ index stale…] line means re-index "
    "before trusting details. Hits end with a ready-made follow-up call. "
    "Elided content always carries a recovery pointer."
)

TOOL_DOCS: dict[str, str] = {
    "get_overview": f"""Orient yourself: what is indexed and what shape this repo/package has.

When to use: first call on an unfamiliar project; refreshing your map after a re-index; checking what packages/modules exist before searching.
When NOT to use: you already know a dotted path (get_symbol) or a topic (search_codebase).
{_WORKFLOW}
{_CONTRACT}
Examples:
  get_overview()
  get_overview(package="fastapi")
  get_overview(package="__project__", project="backend")
""",
    "search_codebase": f"""Find code, docs, or decisions about a topic you can't name exactly.

When to use: keyword/concept/partial-name queries; "how do I X"; "where is the code for X".
When NOT to use: you know the exact dotted path (get_symbol); you're asking WHY code is designed a certain way (get_why).
{_WORKFLOW}
{_CONTRACT}
Examples:
  search_codebase(query="batch inference", kind="docs")
  search_codebase(query="retry logic", package="requests")
  search_codebase(query="our parser", scope="project", project="backend")
""",
    "get_symbol": f"""Details — or verbatim source — for a dotted path you already know.

When to use: known package/module/class/method paths; depth="tree" for the full nested subtree; depth="source" for the exact source bytes (this is how you recover content a truncated response elided).
When NOT to use: you only have a keyword (search_codebase); you want the whole dependency closure (get_context).
{_WORKFLOW}
{_CONTRACT}
Examples:
  get_symbol(target="fastapi.routing.APIRouter")
  get_symbol(target="pkg.mod.BigClass", depth="source")
  get_symbol(target="app.db.Pool", depth="tree", project="backend")
""",
    "get_context": f"""Everything needed to understand one or more targets, packed under a token budget.

When to use: before reading or modifying code — one call replaces separate doc/signature/caller reads. Pass ALL targets in ONE call: one shared budget beats N sequential calls.
When NOT to use: single known symbol, full source wanted (get_symbol); pure who-calls-what (get_references).
{_WORKFLOW}
{_CONTRACT}
Examples:
  get_context(targets=["pydocs_mcp.retrieval.pipeline"])
  get_context(targets=["pkg.mod.A", "pkg.mod.B"], project="backend")
""",
    "get_references": f"""Who calls X, what X calls, what X extends, or what breaks if X changes.

When to use: direction="callers" for usage sites; "callees" for dependencies; "inherits" for base classes; "impact" for the ranked transitive blast radius before a risky change.
When NOT to use: you want source or docs (get_symbol / get_context).
{_WORKFLOW}
{_CONTRACT}
Examples:
  get_references(target="fastapi.routing.APIRouter.include_router", direction="callers")
  get_references(target="pkg.mod.f", direction="impact", project="backend")
""",
    "get_why": f"""Why is this code the way it is — which recorded decisions govern it?

When to use: before proposing architectural changes; questions like "why sqlite here?"; pass ALL paths of interest in ONE call via targets. No arguments = governance dashboard.
When NOT to use: what/where questions (search_codebase); implementation details (get_symbol).
{_WORKFLOW}
{_CONTRACT}
Examples:
  get_why(query="why are vectors in a sidecar file")
  get_why(targets=["python/pydocs_mcp/db.py"], project="backend")
""",
}

SERVER_INSTRUCTIONS = (
    "pydocs-mcp indexes your project's source AND every installed dependency "
    "into a local hybrid index (dense embeddings + BM25 + a reference graph). "
    "Use it before web search for: installed-library APIs, symbols in the "
    "user's own code (package \"__project__\"), call-graph navigation, and "
    "design rationale. Six task-shaped tools: " + _WORKFLOW + " " + _CONTRACT +
    " Do NOT use for libraries that aren't installed here (use web search)."
)
```

- [ ] **Step 4: Run the lint test; tune budgets ONLY by tightening prose** (never by raising the budget constants)

Run: `PYTHONPATH=python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/application/test_tool_docs_lint.py -q`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/tool_docs.py tests/application/test_tool_docs_lint.py
git commit -m "feat(docs): TOOL_DOCS single-source tool documentation with lint contract"
```

---

### Task 3: Pointer vocabulary v2 — six-tool syntax + `search` action

**Files:**
- Modify: `python/pydocs_mcp/application/formatting.py` (pointer emit/resolve table)
- Modify: `tests/application/test_next_pointers.py` (goldens swap to new syntax)
- Modify: `tests/application/test_response_envelope.py`, `tests/application/test_router_envelope_wiring.py` (same swap where they assert resolved syntax)

- [ ] **Step 1: Update the tests to the new contract first**

In `tests/application/test_next_pointers.py`, replace the resolution assertions (keep token-emission tests as-is — tokens are unchanged):

```python
def test_resolve_mcp_syntax() -> None:
    text = "hit\n[[next:lookup:pkg.mod.X]]\n"
    assert resolve_pointers(text, "mcp") == 'hit\n→ get_symbol(target="pkg.mod.X")\n'


def test_resolve_cli_syntax() -> None:
    text = "hit\n[[next:lookup:pkg.mod.X]]\n"
    assert resolve_pointers(text, "cli") == "hit\n→ pydocs-mcp symbol pkg.mod.X\n"


def test_resolve_show_variants_map_to_new_tools() -> None:
    assert resolve_pointers("[[next:lookup-show:pkg.mod.X:callers]]", "mcp") == (
        '→ get_references(target="pkg.mod.X", direction="callers")'
    )
    assert resolve_pointers("[[next:lookup-show:pkg.mod.X:impact]]", "cli") == (
        "→ pydocs-mcp refs pkg.mod.X --direction impact"
    )
    assert resolve_pointers("[[next:lookup-show:pkg.mod.X:context]]", "mcp") == (
        '→ get_context(targets=["pkg.mod.X"])'
    )
    assert resolve_pointers("[[next:lookup-show:pkg.mod.X:tree]]", "mcp") == (
        '→ get_symbol(target="pkg.mod.X", depth="tree")'
    )


def test_search_action_token() -> None:
    assert pointer_token("search", "retry logic") == "[[next:search:retry logic]]"
    assert resolve_pointers("[[next:search:retry logic]]", "mcp") == (
        '→ search_codebase(query="retry logic")'
    )
    assert resolve_pointers("[[next:search:retry logic]]", "cli") == (
        '→ pydocs-mcp search "retry logic"'
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/application/test_next_pointers.py -q`
Expected: FAIL on the new syntax assertions.

- [ ] **Step 3: Implement in `formatting.py`**

Replace `_POINTER_RE` and `_render_pointer` (token grammar gains `search`; payload for search may contain spaces but still no `:` or `]]`):

```python
_POINTER_RE = re.compile(r"\[\[next:(lookup|lookup-show|search):([^:\]]+)(?::([^:\]]+))?\]\]")

# show-mode → (mcp renderer, cli renderer). context maps to a one-element
# get_context batch; tree/default stay on get_symbol via depth.
_SHOW_TO_TOOL: dict[str, tuple[str, str]] = {
    "callers": ('get_references(target="{t}", direction="callers")', "pydocs-mcp refs {t} --direction callers"),
    "callees": ('get_references(target="{t}", direction="callees")', "pydocs-mcp refs {t} --direction callees"),
    "inherits": ('get_references(target="{t}", direction="inherits")', "pydocs-mcp refs {t} --direction inherits"),
    "impact": ('get_references(target="{t}", direction="impact")', "pydocs-mcp refs {t} --direction impact"),
    "context": ('get_context(targets=["{t}"])', "pydocs-mcp context {t}"),
    "tree": ('get_symbol(target="{t}", depth="tree")', "pydocs-mcp symbol {t} --depth tree"),
    "source": ('get_symbol(target="{t}", depth="source")', "pydocs-mcp symbol {t} --depth source"),
}


def _render_pointer(match: re.Match[str], surface: str) -> str:
    action, target, show = match.group(1), match.group(2), match.group(3)
    if action == "search":
        if surface == "cli":
            return f'→ pydocs-mcp search "{target}"'
        return f'→ search_codebase(query="{target}")'
    if action == "lookup-show":
        mcp_fmt, cli_fmt = _SHOW_TO_TOOL[show]
        fmt = cli_fmt if surface == "cli" else mcp_fmt
        return "→ " + fmt.format(t=target)
    if surface == "cli":
        return f"→ pydocs-mcp symbol {target}"
    return f'→ get_symbol(target="{target}")'
```

`pointer_token` and `strip_pointers` are unchanged (token grammar is a superset).

- [ ] **Step 4: Run all pointer/envelope/wiring suites; update the two sibling test files' resolved-syntax assertions the same way**

Run: `PYTHONPATH=python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/application/test_next_pointers.py tests/application/test_response_envelope.py tests/application/test_router_envelope_wiring.py tests/application/test_truncation_recording.py -q`
Expected: all PASS after swapping `lookup(target=` expectations to `get_symbol(target=` / `pydocs-mcp symbol` in the sibling files.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/formatting.py tests/application/
git commit -m "feat(formatting): pointer resolution targets the six-tool surface; search action added"
```

---

### Task 4: `SymbolSourceService` — verbatim source for one symbol (§D7 recovery)

**Files:**
- Create: `python/pydocs_mcp/application/symbol_source.py`
- Modify: `python/pydocs_mcp/retrieval/config/models.py` + `python/pydocs_mcp/defaults/default_config.yaml` (`symbol_source.max_lines`)
- Test: `tests/application/test_symbol_source.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/application/test_symbol_source.py`:

```python
"""get_symbol depth=source — verbatim bytes + path + line-cap (spec §D1/§D7)."""

import asyncio

import pytest

from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.symbol_source import SymbolSourceService
from tests._fakes import make_fake_uow_factory
# Use this suite's existing chunk-fixture helper (grep tests/_fakes.py for the
# chunk-construction function other application tests use) to build chunks
# whose metadata carries qualified_name + source_path.


def _service(chunks) -> SymbolSourceService:
    return SymbolSourceService(
        uow_factory=make_fake_uow_factory(chunks=chunks),
        max_lines=5,
    )


def test_returns_source_block_with_path() -> None:
    chunk = _make_chunk(  # helper per note above
        qualified_name="pkg.mod.f", source_path="pkg/mod.py",
        text="def f():\n    return 1\n",
    )
    out = asyncio.run(_service((chunk,)).source_for("pkg.mod.f"))
    assert "```python" in out and "def f():" in out
    assert "pkg/mod.py" in out


def test_line_cap_truncates_with_recovery_note() -> None:
    body = "\n".join(f"line{i}" for i in range(20))
    chunk = _make_chunk(qualified_name="pkg.mod.big", source_path="pkg/mod.py", text=body)
    out = asyncio.run(_service((chunk,)).source_for("pkg.mod.big"))
    assert "line4" in out and "line5" not in out
    assert "pkg/mod.py" in out  # the file path is the terminal recovery step


def test_unknown_symbol_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        asyncio.run(_service(()).source_for("nope.missing"))
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/application/test_symbol_source.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Create `python/pydocs_mcp/application/symbol_source.py` (mandatory `uow_factory` service shape per CLAUDE.md):

```python
"""Verbatim source for one indexed symbol — get_symbol(depth="source") (spec §D1/§D7).

The §D7 recovery chain terminates here: a truncated card points at
get_symbol(..., depth="source"), and if even one symbol exceeds the line cap
the rendered file path is the final, always-valid recovery step (readable by
the agent's own file tools).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydocs_mcp.application.formatting import pointer_token
from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.truncation import TruncationEntry, get_active_ledger
from pydocs_mcp.storage.protocols import UnitOfWork

_DEFAULT_MAX_LINES = 400


@dataclass(frozen=True, slots=True)
class SymbolSourceService:
    uow_factory: Callable[[], UnitOfWork]
    max_lines: int = _DEFAULT_MAX_LINES

    async def source_for(self, target: str) -> str:
        async with self.uow_factory() as uow:
            chunks = await uow.chunks.list(filter={"qualified_name": target}, limit=1)
        if not chunks:
            raise NotFoundError(
                f"'{target}' has no indexed source. "
                f"{pointer_token('search', target.rsplit('.', 1)[-1])}"
            )
        chunk = chunks[0]
        path = str(chunk.metadata.get("source_path") or "")
        lines = (chunk.text or "").splitlines()
        shown = lines[: self.max_lines]
        header = f"# Source — `{target}`" + (f"  ·  {path}" if path else "")
        body = "\n".join(shown)
        out = f"{header}\n\n```python\n{body}\n```\n"
        if len(lines) > self.max_lines:
            elided = len(lines) - self.max_lines
            out += f"[… {elided} more lines — read {path or 'the source file'} directly]\n"
            ledger = get_active_ledger()
            if ledger is not None:
                ledger.record(
                    TruncationEntry(
                        description=f"{elided} source lines beyond the {self.max_lines}-line cap",
                        recovery="",  # the inline file path IS the terminal recovery
                    )
                )
        return out
```

Config: in `retrieval/config/models.py` add `class SymbolSourceConfig(BaseModel): max_lines: int = Field(_DEFAULT_MAX_LINES_SYMBOL_SOURCE, ge=20, le=5000)` with module constant `_DEFAULT_MAX_LINES_SYMBOL_SOURCE = 400`, register `symbol_source: SymbolSourceConfig = SymbolSourceConfig()` on `AppConfig`; add to `default_config.yaml`:

```yaml
symbol_source:
  max_lines: 400            # get_symbol(depth="source") bound (spec §D7)
```

(Two `_DEFAULT` homes — one in the service, one in config — is the same intentional split every YAML-tunable service default in this repo uses: config is canonical for YAML, the service constant covers direct construction; wire config→service in Task 7.)

Filter-shape note: if `uow.chunks.list(filter={"qualified_name": target})` is not the fake/real stores' accepted mapping shape, mirror how `ReferenceService.context` hydrates chunks by qualified name (reference_service.py lines ~203-206, `FieldIn`) — same store API, single value.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/application/test_symbol_source.py tests/test_config_output_block.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/symbol_source.py python/pydocs_mcp/retrieval/config/models.py python/pydocs_mcp/defaults/default_config.yaml tests/application/test_symbol_source.py
git commit -m "feat(symbol-source): verbatim per-symbol source with line cap (get_symbol depth=source)"
```

---

### Task 5: `NullDecisionService`

**Files:**
- Modify: `python/pydocs_mcp/application/null_services.py`
- Test: `tests/application/test_null_decision_service.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""get_why without decision capture must raise, never mislead (spec §D9 Null rule)."""

import asyncio

import pytest

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
from pydocs_mcp.application.null_services import NullDecisionService


def test_every_mode_raises_with_yaml_pointer() -> None:
    svc = NullDecisionService()
    for call in (
        lambda: svc.search("why sqlite"),
        lambda: svc.for_targets(["a.py"]),
        lambda: svc.dashboard(),
    ):
        with pytest.raises(ServiceUnavailableError, match="decision_capture"):
            asyncio.run(call())
```

- [ ] **Step 2: Run to verify failure** — `ImportError`.

- [ ] **Step 3: Implement** in `null_services.py`, mirroring `NullTreeService` (lines 62-85):

```python
_DECISIONS_DISABLED_MSG = (
    "Architectural-decision capture is not enabled in this build. "
    "It ships with the decision layer (decision_capture.enabled in "
    "pydocs-mcp.yaml); until then get_why has nothing to answer from."
)


@dataclass(frozen=True, slots=True)
class NullDecisionService:
    """get_why's backing service when decision capture is absent (spec §D9).

    Raises rather than returning empty: decisions are user-requested via the
    MCP surface — a silent empty result would mislead the caller (same
    rationale as NullTreeService vs NullVectorStore).
    """

    async def search(self, query: str) -> str:
        raise ServiceUnavailableError(_DECISIONS_DISABLED_MSG)

    async def for_targets(self, targets: list[str]) -> str:
        raise ServiceUnavailableError(_DECISIONS_DISABLED_MSG)

    async def dashboard(self) -> str:
        raise ServiceUnavailableError(_DECISIONS_DISABLED_MSG)
```

(Match the file's existing import/dataclass style; slice 3 replaces this with the real `DecisionService` behind the same three-method shape.)

- [ ] **Step 4: Run** — PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/null_services.py tests/application/test_null_decision_service.py
git commit -m "feat(null-services): NullDecisionService behind get_why until slice 3"
```

---

### Task 6: `ToolRouter` — the six tool entry points

**Files:**
- Create: `python/pydocs_mcp/application/tool_router.py`
- Modify: `python/pydocs_mcp/application/multi_project_search.py` (`ProjectServices` gains `symbol_source` + `decisions` fields)
- Test: `tests/application/test_tool_router.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/application/test_tool_router.py` — reuse the fake `ProjectServices` helper pattern from `tests/application/test_router_envelope_wiring.py` (extend the fixture to also carry a fake `symbol_source` + `NullDecisionService`):

```python
"""ToolRouter — each tool routes to the right body and stays enveloped (spec §D1)."""

import asyncio

import pytest

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
from pydocs_mcp.application.mcp_inputs import (
    ContextInput, OverviewInput, ReferencesInput, SearchInput, SymbolInput, WhyInput,
)
from pydocs_mcp.application.tool_router import ToolRouter


# _tool_router() builds a ToolRouter over the wiring-test fakes with a static
# envelope probe (surface="mcp") — extract a shared helper with
# test_router_envelope_wiring.py rather than duplicating it.


def test_search_codebase_is_enveloped_search() -> None:
    out = asyncio.run(_tool_router().search_codebase(SearchInput(query="x")))
    assert out.startswith("[index:")
    assert "[[next:" not in out


def test_symbol_summary_and_tree_route_to_lookup_body() -> None:
    out = asyncio.run(_tool_router().get_symbol(SymbolInput(target="pkg.mod.X")))
    assert out.startswith("[index:")


def test_symbol_source_routes_to_symbol_source_service() -> None:
    out = asyncio.run(
        _tool_router().get_symbol(SymbolInput(target="pkg.mod.X", depth="source"))
    )
    assert "```python" in out


def test_context_renders_one_card_per_target() -> None:
    out = asyncio.run(
        _tool_router().get_context(ContextInput(targets=["pkg.mod.A", "pkg.mod.B"]))
    )
    assert out.count("# Context for") == 2


def test_references_maps_direction_to_show() -> None:
    out = asyncio.run(
        _tool_router().get_references(ReferencesInput(target="pkg.mod.f", direction="impact"))
    )
    assert "Impact of" in out


def test_why_raises_service_unavailable() -> None:
    with pytest.raises(ServiceUnavailableError, match="decision_capture"):
        asyncio.run(_tool_router().get_why(WhyInput(query="why")))


def test_overview_lists_packages() -> None:
    out = asyncio.run(_tool_router().get_overview(OverviewInput()))
    assert out.startswith("[index:") and "# Overview" in out
```

- [ ] **Step 2: Run to verify failure** — module not found.

- [ ] **Step 3: Implement**

(a) `ProjectServices` (multi_project_search.py:45-52) gains two fields:

```python
    symbol_source: SymbolSourceService
    decisions: NullDecisionService  # slice 3 widens this to a DecisionNavigator Protocol
```

(update the wiring-test fixture and `server._build_project_services` construction in Task 8; for THIS task only the fakes construct it).

(b) Create `python/pydocs_mcp/application/tool_router.py`:

```python
"""ToolRouter — the six task-shaped tools over the multi-project layer (spec §D1).

One method per tool; every response is produced inside the shared
ResponseEnvelope (freshness header, pointer resolution, truncation footer).
Bodies delegate to the slice-1 router internals (_search_body/_lookup_body)
so ranking/dedup/project-routing stay in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    LookupInput,
    OverviewInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
    WhyInput,
)
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
    _select_service,
)

_DEPTH_TO_SHOW = {"summary": "default", "tree": "tree"}


@dataclass(frozen=True, slots=True)
class ToolRouter:
    services: tuple[ProjectServices, ...]
    envelope: ResponseEnvelope
    search_router: MultiProjectSearch   # constructed WITHOUT envelope; bodies only
    lookup_router: MultiProjectLookup   # constructed WITHOUT envelope; bodies only

    def _svc(self, project: str) -> ProjectServices:
        if project:
            return _select_service(self.services, project)
        return self.services[0]

    async def search_codebase(self, payload: SearchInput) -> str:
        return await self.envelope.wrap(lambda: self.search_router._search_body(payload))

    async def get_symbol(self, payload: SymbolInput) -> str:
        if payload.depth == "source":
            svc = self._svc(payload.project)
            return await self.envelope.wrap(
                lambda: svc.symbol_source.source_for(payload.target)
            )
        body = LookupInput(
            target=payload.target,
            show=_DEPTH_TO_SHOW[payload.depth],
            project=payload.project,
        )
        return await self.envelope.wrap(lambda: self.lookup_router._lookup_body(body))

    async def get_references(self, payload: ReferencesInput) -> str:
        body = LookupInput(
            target=payload.target,
            show=payload.direction,
            project=payload.project,
            limit=payload.limit,
        )
        return await self.envelope.wrap(lambda: self.lookup_router._lookup_body(body))

    async def get_context(self, payload: ContextInput) -> str:
        async def _cards() -> str:
            cards = []
            for target in payload.targets:
                body = LookupInput(target=target, show="context", project=payload.project)
                cards.append(await self.lookup_router._lookup_body(body))
            return "\n\n".join(cards)

        return await self.envelope.wrap(_cards)

    async def get_why(self, payload: WhyInput) -> str:
        svc = self._svc(payload.project)

        async def _body() -> str:
            if payload.query and payload.targets:
                # §D11 both-set mode: targets filtered by query — the Null
                # service raises either way; slice 3 implements the filter.
                return await svc.decisions.for_targets(list(payload.targets))
            if payload.query:
                return await svc.decisions.search(payload.query)
            if payload.targets:
                return await svc.decisions.for_targets(list(payload.targets))
            return await svc.decisions.dashboard()

        return await self.envelope.wrap(_body)

    async def get_overview(self, payload: OverviewInput) -> str:
        body = LookupInput(target=payload.package, show="default", project=payload.project)

        async def _card() -> str:
            listing = await self.lookup_router._lookup_body(body)
            title = payload.package or "all indexed packages"
            return f"# Overview — {title}\n\n{listing}"

        return await self.envelope.wrap(_card)
```

Exception note: `envelope.wrap` must not swallow `MCPToolError` — `get_why`'s `ServiceUnavailableError` propagates out of `wrap` (the ledger context manager exits cleanly on exception; no envelope is rendered on error at this layer — server-side handlers own error rendering). Add one test in Step 1's file asserting the raise escapes (`test_why_raises_service_unavailable` already does).

(Task 10 of plan 2b replaces `get_overview`'s basic card with the §D17 structural card; the tool contract and tests here stay valid.)

- [ ] **Step 4: Run**

Run: `PYTHONPATH=python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/application/test_tool_router.py tests/application/test_router_envelope_wiring.py -q`
Expected: PASS (extend the shared fake fixture as needed).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/tool_router.py python/pydocs_mcp/application/multi_project_search.py tests/application/
git commit -m "feat(router): ToolRouter — six task-shaped entry points over the multi-project layer"
```

---

### Task 7: Wire `configure_from_app_config` + `symbol_source` construction

**Files:**
- Modify: `python/pydocs_mcp/application/mcp_inputs.py` (`configure_from_app_config` reads `cfg.symbol_source`)
- Modify: `python/pydocs_mcp/storage/factories.py` (build `SymbolSourceService` next to the existing per-project service builders, from the same `uow_factory` + `cfg.symbol_source.max_lines`)
- Modify: `python/pydocs_mcp/server.py` `_build_project_services` (lines 33-57): construct `symbol_source=...`, `decisions=NullDecisionService()` on `ProjectServices`
- Test: extend `tests/application/test_tool_inputs.py` with a `configure_from_app_config` round-trip asserting `SymbolSourceService` default flows from YAML (mirror how the existing limit-wiring test does it — grep tests/ for `configure_from_app_config`).

Steps follow the standard TDD loop (failing wiring test → implement → green → commit):

```bash
git add python/pydocs_mcp/application/mcp_inputs.py python/pydocs_mcp/storage/factories.py python/pydocs_mcp/server.py tests/
git commit -m "feat(wiring): symbol_source config + per-project SymbolSourceService construction"
```

---

### Task 8: `server.py` — six MCP tools replace two

**Files:**
- Modify: `python/pydocs_mcp/server.py` (`build_routers` returns `ToolRouter`; six `@mcp.tool` registrations; `instructions=SERVER_INSTRUCTIONS`)
- Test: `tests/application/test_server_surface.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""The MCP surface is exactly the six task-shaped tools (spec §D1/§D2)."""

import inspect

from pydocs_mcp import server
from pydocs_mcp.application.tool_docs import TOOL_DOCS

_EXPECTED = {
    "get_overview", "search_codebase", "get_symbol",
    "get_context", "get_references", "get_why",
}


def test_run_registers_exactly_six_tools() -> None:
    source = inspect.getsource(server)
    for name in _EXPECTED:
        assert f"async def {name}(" in source
    for legacy in ("async def search(", "async def lookup("):
        assert legacy not in source


def test_tool_docstrings_come_from_tool_docs() -> None:
    source = inspect.getsource(server)
    assert "TOOL_DOCS" in source and "SERVER_INSTRUCTIONS" in source
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

In `server.py`:

(a) `build_routers` (77-96) — after building `services`, probe, and envelope exactly as slice 1 left it, construct body-only routers and return the `ToolRouter`:

```python
    body_search = MultiProjectSearch(services=services)     # envelope=None: bodies only
    body_lookup = MultiProjectLookup(services=services)
    tools = ToolRouter(
        services=services,
        envelope=envelope,
        search_router=body_search,
        lookup_router=body_lookup,
    )
    return tools, services
```

Change the return contract to `(ToolRouter, services)` and update ALL call sites (`run()` here; `__main__._run_search`/`_run_lookup` in Task 9).

(b) Replace the two `@mcp.tool` handlers (150-203, 212-262) with six thin ones — one shown in full; the other five follow the identical shape with their own input model and router method:

```python
    def _register(fn, name: str):
        fn.__doc__ = TOOL_DOCS[name]
        return mcp.tool(
            annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
        )(fn)

    async def get_symbol(target: str, depth: str = "summary", project: str = "") -> str:
        payload = SymbolInput(target=target, depth=depth, project=project)
        try:
            return await tools.get_symbol(payload)
        except MCPToolError:
            raise
        except Exception as e:
            log.exception("get_symbol failed unexpectedly")
            raise ServiceUnavailableError(f"get_symbol failed: {e}") from e

    _register(get_symbol, "get_symbol")
```

`search_codebase` keeps `SearchInput`'s parameter set `(query, kind="any", package="", scope="all", limit=<no literal — omit and let the model default apply by constructing SearchInput without limit when the arg is absent; mirror how the current handler passes limit>, project="")`. `get_context(targets: list[str], project="")`, `get_references(target, direction="callers", limit=..., project="")`, `get_why(query="", targets: list[str] | None = None, project="")`, `get_overview(package="", project="")`.

(c) `instructions=SERVER_INSTRUCTIONS` replaces the inline string (138-141); import from `application.tool_docs`.

FastMCP adaptation note: if `mcp.tool(...)` does not pick up `fn.__doc__` set before decoration, pass the text via the decorator's `description=` parameter instead — check `FastMCP.tool`'s signature in the installed `mcp` package and use whichever carries the description to the client; the `test_tool_docstrings_come_from_tool_docs` test only pins that TOOL_DOCS is the source.

- [ ] **Step 4: Run**

Run: `PYTHONPATH=python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/application/test_server_surface.py tests/ -q -k "server or router"`
Expected: PASS; fix any test that constructed the old `(search_router, lookup_router, services)` triple.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/server.py tests/
git commit -m "feat(server): six task-shaped MCP tools replace search/lookup"
```

---

### Task 9: CLI parity — six subcommands + deprecated `lookup` alias

**Files:**
- Modify: `python/pydocs_mcp/__main__.py` (`_build_parser` 49-293; `_run_search` 542-569 → `_run_tool` family; `_cmd_*` 753-758)
- Test: extend `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests** (same `patch("sys.argv", …)` + `main()` pattern the file already uses; reuse its seeded-project fixtures):

```python
def test_symbol_subcommand_prints_enveloped_output(indexed_project, capsys) -> None:
    with patch("sys.argv", ["pydocs-mcp", "symbol", "app.greet",
                            "--project-dir", str(indexed_project)]):
        main()
    out = capsys.readouterr().out
    assert out.startswith("[index:")


def test_refs_subcommand_direction_flag(indexed_project, capsys) -> None:
    with patch("sys.argv", ["pydocs-mcp", "refs", "app.greet",
                            "--direction", "callers", "--project-dir", str(indexed_project)]):
        main()
    assert "Callers of" in capsys.readouterr().out


def test_context_accepts_multiple_targets(indexed_project, capsys) -> None:
    with patch("sys.argv", ["pydocs-mcp", "context", "app.greet", "app",
                            "--project-dir", str(indexed_project)]):
        main()
    assert capsys.readouterr().out.count("# Context for") == 2


def test_overview_subcommand(indexed_project, capsys) -> None:
    with patch("sys.argv", ["pydocs-mcp", "overview", "--project-dir", str(indexed_project)]):
        main()
    assert "# Overview" in capsys.readouterr().out


def test_why_reports_unavailable(indexed_project, capsys) -> None:
    with patch("sys.argv", ["pydocs-mcp", "why", "anything",
                            "--project-dir", str(indexed_project)]):
        rc = main()
    assert rc != 0
    assert "decision_capture" in capsys.readouterr().err


def test_lookup_alias_warns_and_delegates(indexed_project, capsys) -> None:
    with patch("sys.argv", ["pydocs-mcp", "lookup", "app.greet",
                            "--project-dir", str(indexed_project)]):
        main()
    captured = capsys.readouterr()
    assert "deprecated" in captured.err and "pydocs-mcp symbol" in captured.err
    assert captured.out.startswith("[index:")
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement in `__main__.py`**

(a) In `_build_parser`, keep `search` (its flag set is already the new interface, plus add `--project` parity note — it exists as `--project` dest=`project_scope`). Add five subcommands, each with the shared query flags (`--project-dir`, `--workspace`, `--db`, `--project` dest=`project_scope`, `--no-rust`, `--cache-dir`, `-v`) exactly as `lookup` defines them today (copy lines 280-291's shared block):

```python
    p_overview = sub.add_parser("overview", help=TOOL_DOCS["get_overview"].splitlines()[0])
    p_overview.add_argument("package", nargs="?", default="")

    p_symbol = sub.add_parser("symbol", help=TOOL_DOCS["get_symbol"].splitlines()[0])
    p_symbol.add_argument("target")
    p_symbol.add_argument("--depth", choices=["summary", "tree", "source"], default="summary")

    p_context = sub.add_parser("context", help=TOOL_DOCS["get_context"].splitlines()[0])
    p_context.add_argument("targets", nargs="+")

    p_refs = sub.add_parser("refs", help=TOOL_DOCS["get_references"].splitlines()[0])
    p_refs.add_argument("target")
    p_refs.add_argument("--direction", choices=["callers", "callees", "inherits", "impact"],
                        default="callers")
    p_refs.add_argument("--limit", type=int, default=None)

    p_why = sub.add_parser("why", help=TOOL_DOCS["get_why"].splitlines()[0])
    p_why.add_argument("query", nargs="?", default="")
    p_why.add_argument("--target", action="append", dest="targets", default=None)
```

(b) The existing `lookup` subcommand STAYS for one release as a deprecated alias: its `_run_lookup` prints to stderr `"'pydocs-mcp lookup' is deprecated — use 'pydocs-mcp symbol' (or refs/context per --show); routing there now."` and maps `--show` → the new router calls (`default`/`tree`→`get_symbol`, graph shows→`get_references`, `context`→`get_context`, `impact`→`get_references(direction="impact")`).

(c) Replace `_run_search`/`_run_lookup` with one `_run_tool(args, method_name, payload)` helper: builds routers via the new `build_routers(...)` contract (`tools, services = build_routers(config, db_path=..., workspace=..., db_paths=..., surface="cli")` — note `surface="cli"` at BOTH former call sites 555/581 collapses to this one helper), constructs the input model per subcommand, prints the result. `why`'s `ServiceUnavailableError` is caught and printed to stderr with exit code 1 (mirror how `_run_cmd` maps MCPToolError today — grep `_run_cmd` and reuse its error path).

(d) `_cmd_overview/_cmd_symbol/_cmd_context/_cmd_refs/_cmd_why` one-liners mirror `_cmd_search` (753-754); register in the dispatch table `main()` uses.

- [ ] **Step 4: Run the CLI suite**

Run: `PYTHONPATH=python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: PASS (update any old-surface assertions the alias path changes).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/__main__.py tests/test_cli.py
git commit -m "feat(cli): six task-shaped subcommands + deprecated lookup alias"
```

---

### Task 10: Error/empty contract

**Files:**
- Modify: `python/pydocs_mcp/application/multi_project_search.py` (`MultiProjectLookup._lookup_body` NotFound path) and `python/pydocs_mcp/application/tool_router.py` (zero-hit search)
- Test: `tests/application/test_error_empty_contract.py` (create)

- [ ] **Step 1: Failing tests**

```python
"""Errors and empty results stay enveloped and carry a next step (spec §D1)."""


def test_unknown_target_message_carries_search_pointer() -> None:
    # NotFoundError raised by lookup must carry a [[next:search:...]] token so
    # the server-side error text (rendered by the MCP error path) still guides
    # the agent. Drive _tool_router().get_symbol at a missing target and
    # assert the raised NotFoundError's str() contains "[[next:search:".
    ...


def test_zero_hit_search_points_at_overview() -> None:
    # Fake docs/api services returning empty; assert the enveloped output
    # contains the resolved get_overview() pointer.
    ...
```

Write these as real tests against the Task-6 fakes (the bodies above name the exact assertions; fill constructor plumbing from the shared fixture).

- [ ] **Step 2-3: Implement**

- In `MultiProjectLookup._lookup_body`'s final `NotFoundError` raise (slice-1 renamed body of multi_project_search.py:189-193), append a search pointer token to the message: `f"'{payload.target}' not found in any loaded project. {pointer_token('search', payload.target.rsplit('.', 1)[-1])}"`.
- In `ToolRouter.search_codebase`, post-process: when the body equals the known empty-result strings (`"No matches found."` etc. — import the constants or compare via the existing `_DEFAULT_EMPTY_MSG` exports), append `"\n" + pointer_token("lookup", "")`? No — empty search points at overview: append `"\n[[next:overview:]]"` is NOT a defined action; instead extend `_SHOW_TO_TOOL`-adjacent grammar minimally: reuse the `search` action pointing nowhere is wrong too. Correct minimal design: add action `overview` to `_POINTER_RE` and `_render_pointer` (`→ get_overview()` / `→ pydocs-mcp overview`), emit it on the zero-hit path. Update Task 3's pointer tests with the new action in the same commit.

- [ ] **Step 4: Run** — `pytest tests/application/test_error_empty_contract.py tests/application/test_next_pointers.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/ tests/application/
git commit -m "feat(contract): errors and empty results carry next-step pointers"
```

---

### Task 11: CLAUDE.md amendment (§D2)

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1:** Rewrite §"MCP API surface vs YAML configuration": the surface is **a fixed set of six task-shaped tools** (`get_overview`, `search_codebase`, `get_symbol`, `get_context`, `get_references`, `get_why`), pinned in `server.py`; adding a seventh remains a design-doc-level versioning event; the two sanctioned parameter categories and the YAML litmus test are unchanged and apply per-tool. Update the two other places that say "fixed 2-tool `search` + `lookup`" / "two tools only" (project-overview paragraph and the Data-flow/instructions references), and the CLI examples in §Build & Run (`pydocs-mcp symbol|refs|context|overview|why` replacing `lookup` examples, keeping one deprecated-alias note).

- [ ] **Step 2:** Audit: `grep -n "two tools\|2-tool\|lookup(" CLAUDE.md` — every remaining hit must be intentional (history/rationale), not stale surface description.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): amend MCP-surface constitution to the six task-shaped tools"
```

---

### Task 12: Full gates + wrap-up

- [ ] **Step 1:** `PYTHONPATH=python /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest -q tests/` → 0 failures.
- [ ] **Step 2:** `PYTHONPATH=benchmarks/src /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python -m pytest benchmarks/tests/ -q` → 0 failures.
- [ ] **Step 3:** `ruff check python/ tests/ benchmarks/` + `ruff format --check python/ tests/ benchmarks/` + `mypy python/` (pre-existing fast_plaid/benchmarks-scripts noise excepted) + coverage `--cov=pydocs_mcp --cov-fail-under=90` + `complexipy --max-complexity-allowed 15` on all NEW/CHANGED python files (then `git checkout complexipy-snapshot.json`).
- [ ] **Step 4:** Live smoke: index the scratch project, then `pydocs-mcp overview --project-dir <scratch>`, `pydocs-mcp symbol app.greet --depth source --project-dir <scratch>`, `pydocs-mcp why x --project-dir <scratch>` (expect the decision_capture error on stderr, exit 1). Commit any fixups as `fix(slice2a): gate fixups`.

---

## Deferred to plan 2b (same branch — do NOT build here)

- Skeleton rendering + centrality-ranked bodies (`reference_graph.context.render`, `skeleton_body_ratio`) — §D6.
- §D17 structural overview card blocks 3–7 (centrality-ranked module map, entry points, communities, dependency profile, doc coverage) + the storage aggregate read methods they need; Task 6's basic overview card is the placeholder it upgrades.
- Proportional per-card budget split in `get_context` (2a renders each card at the configured budget).
- Anything decision-related beyond `NullDecisionService` (slice 3), SWE-QA (slices 4–5).
