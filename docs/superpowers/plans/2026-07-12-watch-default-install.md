# watch-default-install — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement spec `docs/superpowers/specs/2026-07-11-watch-default-install-spec.md` — full promotion (§4.3): `watchdog>=4.0,<6.0` moves verbatim into `[project] dependencies`, `[watch]` becomes an empty back-compat alias, the unreachable guard is deleted, and every doc/test/lock surface follows.

**Architecture:** Pure packaging + dead-code removal behind the existing surface. No MCP/YAML key changes; the watcher's behavior, `observer_factory` seam, and `_load_watchdog` test seam survive. Cross-spec state (§1.5): the docs-audit spec's D3 has NOT landed, so this spec lands first → §3.5's rewording rule applies to the `default_config.yaml:145-146` override claim ("`--watch` is currently the only way to enable watching"); `_cmd_serve` is untouched.

**Tech Stack:** Python 3.11+, pytest, tomllib, uv (`~/.local/bin/uv` 0.11.x ONLY — anaconda's 0.9.5 churns lock markers).

**Spec:** `docs/superpowers/specs/2026-07-11-watch-default-install-spec.md` (authoritative; AC-1 … AC-15 in §5).

**Parallel-session guard:** query-embedding-cache is being implemented in another session. Shared-surface risk: `pyproject.toml`/`uv.lock` (this PR relocks). Re-fetch origin and re-check open PRs immediately before pushing; rebase if needed.

---

## File structure

| File | Action |
|---|---|
| `pyproject.toml` | watchdog → `[project] dependencies` (+rationale comment); `watch = []` alias (+deprecation comment); extras-block header note (§3.1) |
| `uv.lock` | relock (`~/.local/bin/uv lock`) — metadata move only, no version churn (§3.8) |
| `python/pydocs_mcp/serve/watcher.py` | delete `_INSTALL_HINT`, `ServiceUnavailableError` import, try/except; simplify `_load_watchdog`; rewrite module docstring; trim `__post_init__` WHY comment (§3.2) |
| `python/pydocs_mcp/serve/__init__.py` | docstring: leaf-module/import-cost rationale only (§3.3) |
| `python/pydocs_mcp/__main__.py:195-201` | `--watch` help drops the extras sentence (§3.4) |
| `python/pydocs_mcp/defaults/default_config.yaml:145-150` | delete "Requires `pip install pydocs-mcp[watch]`"; reword override clause per §3.5 (this-spec-first branch) |
| `tests/test_pyproject_extras.py:56-81` | invert 3 layout tests; add `test_no_watch_install_hint_left` (§3.9, AC-1/2/5) |
| `tests/test_watcher.py:15,27-50` | delete guard test + its now-unused `ServiceUnavailableError` import (AC-4/7) |
| `tests/test_main_cli_watch.py` | append AC-6 help-text test (format_help pattern from `tests/test_tool_descriptions.py:85-93`) |
| `tests/test_readme_watch_mention.py` | rewrite :16-20/:41-43 into negative assertions matching BOTH quoting forms (§3.9, AC-10/15a) |
| `README.md:138-152` | retitle, delete extras-install step, add default-install statement (§3.6) |
| `DOCUMENTATION.md:376-388` | rewrite "### Install": default install, alias note WITHOUT any `pip install …[watch]` literal; keep section's `---` boundary intact (MyST include trap) |
| `SPEC.md:367,514` | "(requires the `[watch]` extra)" → "(included in the default install)"; drop `'pydocs-mcp[watch]'` from extras list |
| `CLAUDE.md:41,119,142-143` | drop `([watch] extra)` annotations; add watchdog to required-deps line; amend extras policy with §1.3 promotion criteria |
| `INSTALL.md` | add §3.7 air-gap wheelhouse subsection after Quick install |
| `documentation/conf.py:64-68` | move `watchdog` next to the required deps in the commented mock list |
| `benchmarks/src/pydocs_eval/_retrieval_extra.py:29` | precedent citation `[watch]` → `[graph]` |

Branch: `fix/watch-default-install` off origin/main. No `Co-Authored-By` trailers.

---

### Task 1: RED — invert packaging tests + AC-5 grep test

**Files:** `tests/test_pyproject_extras.py`

- [ ] **Step 1:** Replace the three tests at :56-81 and append the grep test:

```python
def test_watch_extra_is_empty_backcompat_alias() -> None:
    """watchdog moved into the required deps (spec
    2026-07-11-watch-default-install); [watch] stays as an empty alias so
    existing `pip install pydocs-mcp[watch]` commands keep resolving."""
    cfg = _load()
    extras = cfg["project"].get("optional-dependencies", {})
    assert "watch" in extras, f"watch alias extra missing. Got: {list(extras)}"
    assert extras["watch"] == [], f"watch extra must be an empty alias; got {extras['watch']}"


def test_watchdog_in_main_dependencies() -> None:
    """`pip install pydocs-mcp` (no extras) suffices for `serve --watch`."""
    cfg = _load()
    main_deps = cfg["project"]["dependencies"]
    watchdog_entries = [d for d in main_deps if d.startswith("watchdog")]
    assert len(watchdog_entries) == 1, f"exactly one watchdog entry expected, got: {main_deps}"


def test_watchdog_main_dep_pins_version_range() -> None:
    """Pin moved verbatim from the extra — a future watchdog 6.x breaking
    change must not silently break `--watch`."""
    cfg = _load()
    spec = next(d for d in cfg["project"]["dependencies"] if d.startswith("watchdog"))
    assert ">=4.0" in spec and "<6.0" in spec, f"watchdog spec must pin >=4.0,<6.0; got {spec!r}"


def test_no_watch_install_hint_left() -> None:
    """No shipped code may instruct `pip install pydocs-mcp[watch]` — the
    extra is an empty back-compat alias, not an install requirement."""
    pkg_root = Path(__file__).resolve().parents[1] / "python" / "pydocs_mcp"
    offenders = [
        str(p)
        for p in pkg_root.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".yaml"}
        and "pydocs-mcp[watch]" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"stale [watch] install hints in shipped code: {offenders}"
```

- [ ] **Step 2:** Run `pytest tests/test_pyproject_extras.py -q` → expect exactly 4 failures (the 3 inversions + the grep test, which today hits `serve/watcher.py:36`, `__main__.py:200`, `defaults/default_config.yaml:150`).

### Task 2: GREEN — pyproject + code edits + guard-test deletion + relock

- [ ] **Step 1: pyproject.toml.** Append to `dependencies` (with the §3.1 rationale comment above the entry — TOML arrays take comments between items when split multiline; keep the existing single-line array and instead add the comment above the `dependencies` line is NOT possible for one entry — so reformat `dependencies` as a multi-line array with the watchdog comment inline, all ten existing entries byte-identical). Replace the watch extra:

```toml
[project.optional-dependencies]
# User-facing extras only — installable via `pip install pydocs-mcp[<name>]`.
# Developer-only dependencies live in [dependency-groups] (PEP 735) so they
# stay out of wheel METADATA and can't be pulled in by end users.
# (`watch` below is a deprecated EMPTY alias, not a real extra.)
# DEPRECATED alias: watchdog moved into the required runtime deps.
# Kept empty so `pip install pydocs-mcp[watch]` in existing docs/scripts
# stays a valid no-op. Removal horizon: next major version.
watch = []
```

- [ ] **Step 2: `serve/watcher.py`** — module docstring paragraph 2 replaced by the seam rationale; delete `ServiceUnavailableError` import + `_INSTALL_HINT`; `_load_watchdog` becomes the §3.2 plain wrapper (docstring verbatim from the spec); `__post_init__` WHY comment drops "(or raise the install hint)".
- [ ] **Step 3: `serve/__init__.py`** docstring → leaf-module/import-cost rationale. **`__main__.py`** `--watch` help → `"Watch the project for changes and reindex on edits."`. **`default_config.yaml:145-150`** comment →

```yaml
# File-watcher tunables for `pydocs-mcp serve --watch` (`--watch` is
# currently the only way to enable watching — leave `enabled` false here
# so plain `pydocs-mcp serve` is identical to today). Debounce naturally
# collapses editor atomic-save sequences (temp create → delete → rename
# → 1 reindex). Ignore globs default-skip the high-churn paths that
# would otherwise produce false-positive reindexes.
```

- [ ] **Step 4: `tests/test_watcher.py`** — delete `test_watcher_construction_raises_when_watchdog_missing` (:27-50) and the `from pydocs_mcp.application.mcp_errors import ServiceUnavailableError` import (:15).
- [ ] **Step 5: relock + resync**: `~/.local/bin/uv lock && ~/.local/bin/uv lock --check`; diff review per AC-9 (watchdog loses `extra == 'watch'` marker, `provides-extras` keeps `watch`, no version churn). Then `~/.local/bin/uv sync --frozen --group dev` (watchdog arrives in the venv; the importorskip tests now RUN — AC-3 proxy).
- [ ] **Step 6:** `pytest tests/test_pyproject_extras.py tests/test_watcher.py tests/integration/test_watcher_real_watchdog.py tests/test_main_cli_watch.py tests/test_watcher_dispatch_contract.py -q` → all green, real-watchdog tests execute (not skip).
- [ ] **Step 7:** AC-6 help test appended to `tests/test_main_cli_watch.py` (verify no other "extras"/"[watch]" mention exists in serve's help first; scope the assertion to the full help text if clean, else to the `--watch` entry):

```python
def test_serve_watch_help_has_no_extras_hint() -> None:
    """AC-6 (spec 2026-07-11-watch-default-install): --watch help says what
    the flag does, with no install lecture — watchdog is a required dep."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    subparsers_action = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    help_text = subparsers_action.choices["serve"].format_help()
    assert "Watch the project for changes" in help_text
    assert "[watch]" not in help_text
    assert "extras" not in help_text
```

- [ ] **Step 8:** Commit: `feat(packaging): promote watchdog to the required runtime deps` (tests + pyproject + uv.lock + code together — the gates cross-check, §6).

### Task 3: docs (AC-10, AC-13, AC-15a)

- [ ] **Step 1: README.md:138-152** — retitle to `### Live re-indexing`; body:

```markdown
### Live re-indexing

The file watcher is part of the default install — no extra step. If you
edit code while you want the index to stay fresh, pick one of two modes —
both debounce edits to `.py`, `.md`, and `.ipynb` files into a single
reindex.

```bash
pydocs-mcp serve . --watch   # MCP server + watcher (for AI clients)
pydocs-mcp watch .            # watcher only (no MCP server; index stays fresh for CLI `search` / `symbol` / `refs`)
```
```

(keep the trailing YAML-tunables paragraph unchanged.)

- [ ] **Step 2: DOCUMENTATION.md:376-388** — "### Install" subsection becomes (NO `pip install …[watch]` literal in any quoting; no new `---`):

```markdown
### Install

The watcher uses `watchdog`, which is part of the default install:

```bash
pip install pydocs-mcp
pydocs-mcp serve . --watch    # or:
pydocs-mcp watch .
```

The `[watch]` extra from older install instructions remains as a
deprecated no-op alias, so existing commands and scripts that request it
keep resolving — it installs nothing beyond the default set.
```

- [ ] **Step 3: SPEC.md** — :367 comment → `# Watch mode — keep the index fresh on file edits (included in the default install)`; :514 extras list drops `'pydocs-mcp[watch]' / `.
- [ ] **Step 4: CLAUDE.md** — :41 drop `([watch] extra)`; :119 drop `(--watch / watch command, [watch] extra)` → `(--watch / watch command)`; :142 append `watchdog>=4.0,<6.0` to the required-deps enumeration; :143 remove `[watch]` from the opt-in list and append the promotion criteria sentence (footprint <1%, zero transitive deps, wheels everywhere, first-class CLI/YAML surface; watchdog 2026-07 is the precedent).
- [ ] **Step 5: INSTALL.md** — §3.7 air-gap wheelhouse subsection verbatim, placed after the Quick-install/first-run block. `documentation/getting-started/install.md` includes the whole file — republishes automatically.
- [ ] **Step 6: conf.py:64-68** — reorder `watchdog` next to the required deps in the commented mock list. **`_retrieval_extra.py:29`** — `` `[watch]` `` → `` `[graph]` ``.
- [ ] **Step 7: `tests/test_readme_watch_mention.py`** — replace :16-20 and :41-43:

```python
def test_readme_states_watcher_in_default_install() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "default install" in readme, "README must state the watcher ships in the default install"


def test_no_watch_extras_install_instruction_anywhere() -> None:
    """Promotion regression (spec 2026-07-11-watch-default-install §3.9 /
    AC-15a): neither user doc may instruct installing the watch extra —
    match BOTH quoting forms so the docs-audit spec's quoting lint (D10)
    can't mask a surviving instruction."""
    for doc in ("README.md", "DOCUMENTATION.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert "pip install pydocs-mcp[watch]" not in text, f"unquoted watch install hint in {doc}"
        assert "pip install 'pydocs-mcp[watch]'" not in text, f"quoted watch install hint in {doc}"
```

(keep `test_readme_mentions_watch_flag`, both DOCUMENTATION structure tests, and the jargon audit unchanged.)

- [ ] **Step 8:** `pytest tests/test_readme_watch_mention.py -q` green; MyST include sanity: the "## Live re-indexing" → next `---` range still contains the section (grep line numbers). Commit: `docs: watcher ships in the default install — README/DOCUMENTATION/SPEC/CLAUDE/INSTALL surfaces`.

### Task 4: adversarial spec-compliance review (ultracode)

- [ ] Workflow: independent refuters per AC group (packaging AC-1/2/9/11; guard+tests AC-3/4/5/6/7/12; docs AC-10/13; invariants AC-8/14/15 + §2 non-goals + §1.5 reconciliation), cross-check pass on non-minor findings. Fix confirmed findings, re-verify.

### Task 5: gates, push, PR

- [ ] Full CI gate set (CLAUDE.md §Tests & Lint): ruff check/format, mypy, complexipy, vulture, pytest+cov≥90, benchmarks suite, `~/.local/bin/uv lock --check`, pip-audit (env-mode `.venv/bin/pip-audit --strict --local` — sandbox workaround; AC-11's grep check on the export runs separately), cargo fmt/clippy/test.
- [ ] AC-11 check: `~/.local/bin/uv export --frozen --no-emit-project --no-group docs --format requirements-txt | grep -c "^watchdog"` → `1`.
- [ ] Re-fetch origin; re-check open PRs for the query-embedding-cache session's PR touching pyproject/uv.lock; rebase if needed.
- [ ] Push `fix/watch-default-install`; verify ls-remote == local HEAD; `gh pr create` (base main; no merge without explicit go).

---

## Self-review notes

- **Spec coverage:** AC-1/2/5 → Task 1+2; AC-3/4/6/7 → Task 2; AC-8 (no YAML key change — comment-only edit) → Task 2 Step 3; AC-9/11/12 → Tasks 2+5; AC-10/13/15a → Task 3; AC-14 (multi-repo untouched — no `_cmd_serve` edit anywhere) → holds by construction, existing tests run in Task 5; AC-15b → the reworded comment carries no override claim while `grep "watch.enabled" __main__.py` has no hit; AC-15c → D3 not landed, nothing to preserve.
- **§7 open questions:** all recorded as out of scope; §7.3 default (delete, don't convert) adopted; §7.6 — docs-audit spec's own recommendation (wire, don't delete) taken as D3's fate per the user's "adopt spec recommendations" instruction.
- **Placeholder scan:** none — every edit carries its exact text or a precise locator.
- **Type consistency:** no new signatures beyond `_load_watchdog()` (unannotated return, matching today's shape in an `ignore_errors` mypy module).
