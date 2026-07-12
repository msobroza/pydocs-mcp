# Promote the file watcher to the default install

| Field    | Value                                            |
|----------|--------------------------------------------------|
| Version  | 0.1 (draft)                                      |
| Status   | Proposed                                         |
| Date     | 2026-07-11                                       |
| Audience | Implementers + reviewers                         |

**One-line summary:** move `watchdog>=4.0,<6.0` from the `[watch]` optional
extra into the required runtime dependencies so `pydocs-mcp serve --watch` and
the `watch` verb work out of the box, keep `[watch]` as an empty back-compat
alias, delete the now-unreachable install-hint guard, and update every doc /
test / lockfile surface that pins the current layout.

---

## 1. Context & problem statement

### 1.1 What the user asked for

> "Watch option should be installed by default."

Today, a fresh `pip install pydocs-mcp` followed by `pydocs-mcp serve . --watch`
fails at watcher construction with:

```
ServiceUnavailableError: --watch requires the 'watch' extras. Install via:
    pip install pydocs-mcp[watch]
```

The guard lives in `python/pydocs_mcp/serve/watcher.py`:

- `serve/watcher.py:35-37` — module-level `_INSTALL_HINT` string.
- `serve/watcher.py:53-70` — `_load_watchdog()`: `from watchdog.observers
  import Observer` wrapped in `try/except ImportError`, re-raised as
  `ServiceUnavailableError(_INSTALL_HINT)`.
- `serve/watcher.py:92-97` — `FileWatcher.__post_init__` resolves the import at
  construction time when `observer_factory is None` (production path).

Note: despite the task's shorthand, there is **no** `_require_extra` function
on the watcher path — that name exists only in
`python/pydocs_mcp/ask_your_docs/cli.py:30` for the `[ask-your-docs]` extra.
The watcher's guard is exactly `_load_watchdog` + `_INSTALL_HINT`, and that is
what this spec removes.

### 1.2 Why the watcher is core UX, not an optional add-on

The watcher is not a peripheral integration; it has two first-class CLI entry
points and shipped default configuration:

- `pydocs-mcp serve --watch` — flag registered at `__main__.py:195-201`,
  dispatched from `_cmd_serve` at `__main__.py:891-905`
  (`asyncio.run(_run_watch_loop(...))`).
- `pydocs-mcp watch` — standalone subcommand registered in the subcommand
  table at `__main__.py:143-147` ("Index + watch project for changes (no MCP
  server)") and dispatched via `_CMD_TABLE` at `__main__.py:975-986`.
- YAML tunables ship in the default config: `serve.watch.{enabled,
  debounce_ms, extensions, ignore_globs}` at
  `python/pydocs_mcp/defaults/default_config.yaml:151-162`.
- `DOCUMENTATION.md:355-443` dedicates a full "Live re-indexing" user-guide
  section, republished on the Sphinx site via the MyST include in
  `documentation/user-guide/live-reindex.md:3-8`.
- The dependency-manifest retrigger (`pyproject.toml` / `requirements*.txt`
  always watched, `serve/watcher.py:40-50`) is built into the matcher — the
  watcher is integrated with dependency discovery, not bolted on.

An index that silently goes stale is the single worst failure mode for an MCP
documentation server consumed by AI coding assistants: the agent gets
confidently wrong answers about code that changed five minutes ago. A feature
this central should not fail on first use with an install lecture.

### 1.3 The policy being amended — and why an amendment is required

Both the manifest and the repo constitution pin the current layout:

- `pyproject.toml:59-61` — the comment block above
  `[project.optional-dependencies]` says extras are "User-facing extras only",
  and `pyproject.toml:62` declares `watch = ["watchdog>=4.0,<6.0"]`. The pin
  lives **only** there; the `[tool.uv]` constraint-dependencies block
  (`pyproject.toml:140-146`) has no watchdog entry.
- `CLAUDE.md:143` — "Optional extras (opt-in, never in the default install):
  `[watch]`, `[sentence-transformers]`, `[openvino]`, `[late-interaction]`,
  `[graph]`, and `[ask-your-docs]`".

CLAUDE.md's extras policy exists to keep the default install lean (~90MB
transitively today, dominated by `onnxruntime` + `tokenizers` + the `openai`
client — `CLAUDE.md:142`). The policy's own framing invites explicit
amendment: "Any change to that policy must be argued explicitly." This spec IS
that explicit argument. The case:

1. **watchdog is tiny.** Measured, not estimated: watchdog 5.0.3 wheels are
   79,320–96,740 bytes each (`uv.lock:5097-5121` — manylinux2014 py3-none
   wheels ~79.3 KB, macOS cp311 universal2 ~96.7 KB, Windows win32/amd64/ia64
   ~79.3 KB; sdist 129,556 bytes). Installed footprint on macOS/py3.11:
   596 KB `site-packages/watchdog` + 88 KB dist-info ≈ **684 KB**. Against a
   ~90 MB default install, that is a **+0.7 %** size delta — three orders of
   magnitude below the extras that motivated the policy
   (sentence-transformers, torch-adjacent stacks, streamlit).
2. **Zero transitive dependencies.** The uv.lock entry for watchdog 5.0.3 has
   no `dependencies` array (`uv.lock:5097-5121`). Its only optional extra,
   `watchmedo`, requires `PyYAML>=3.10` — which pydocs-mcp already requires
   (`pyyaml>=6.0`, `pyproject.toml:49`). Promotion adds exactly one package to
   the dependency closure, full stop.
3. **Pure-Python core + tiny per-platform C helpers, wheels everywhere.**
   watchdog ships wheels covering every platform pydocs-mcp declares: Linux
   x86_64/aarch64/armv7l/i686/ppc64/ppc64le/s390x via
   `py3-none-manylinux2014_*`, macOS cp311/cp312/cp313 x86_64 + arm64 +
   universal2, and Windows win32/amd64/ia64 (`uv.lock:5101-5121`). No user is
   forced into a source build; air-gap wheelhouses need exactly one extra
   ~80–97 KB wheel (§3.7).
4. **Core UX** (§1.2): two first-class CLI verbs, shipped YAML defaults, a
   dedicated user-guide section. The policy targets *heavy, niche* deps; the
   watcher is neither.

The precedent boundary this spec sets: an extra may be promoted to a required
dep only when (a) installed footprint is <1 % of the default install, (b) it
adds zero transitive dependencies, (c) prebuilt wheels exist for every
supported platform, and (d) the gated feature has first-class CLI/YAML
surface. `[sentence-transformers]`, `[openvino]`, `[late-interaction]`,
`[graph]`, and `[ask-your-docs]` fail (a) and/or (b) and remain extras.

### 1.4 Everything that pins the current layout (change inventory)

| Surface | Location | What it says today |
|---|---|---|
| Manifest | `pyproject.toml:62` | `watch = ["watchdog>=4.0,<6.0"]` |
| Import guard | `serve/watcher.py:35-37, 53-70, 92-97` | lazy import + `ServiceUnavailableError(_INSTALL_HINT)` |
| Module docstrings | `serve/watcher.py:8-13`, `serve/__init__.py:1-7` | "watchdog lives behind the `[watch]` extras group; importing it at module top would crash `pydocs-mcp serve`" |
| CLI help | `__main__.py:195-201` | "--watch ... Requires the 'watch' extras: pip install pydocs-mcp[watch]" |
| Default config comment | `defaults/default_config.yaml:150` | "Requires `pip install pydocs-mcp[watch]`" — comment block co-owned with the docs-audit spec's DEFECT 3 (§1.5 C1) |
| README | `README.md:138-152` | "Live re-indexing (optional)" section, `pip install 'pydocs-mcp[watch]'` at :145 |
| DOCUMENTATION.md | `:376-388` | "### Install" subsection: "ships as an optional extra", install command at :381, exit-with-hint behavior at :386-388 — line `:381` is also the docs-audit spec's D10 target (§1.5 C2) |
| SPEC.md | `:367`, `:514` | "requires the `[watch]` extra"; extras list in Installation |
| CLAUDE.md | `:41`, `:119`, `:143` | serve example annotated `([watch] extra)`; architecture tree; extras policy line |
| Extras-layout tests | `tests/test_pyproject_extras.py:56-81` | three tests pin watch extra present + watchdog NOT in main deps + pin range in the extra |
| Guard regression test | `tests/test_watcher.py:27-50` | hides watchdog via `builtins.__import__`, asserts `ServiceUnavailableError` with `pip install pydocs-mcp[watch]` in the message |
| Doc-pin tests | `tests/test_readme_watch_mention.py:11-20, :41-43` | README must mention `--watch` + `[watch]`; DOCUMENTATION.md must contain the literal `pip install pydocs-mcp[watch]` — rewrite must coexist with the docs-audit spec's quoting lint (§1.5 C3) |
| Sphinx conf | `documentation/conf.py:64-68` | commented-out `autodoc_mock_imports` listing `watchdog` (inert; comment premise changes) |
| Benchmarks docstring | `benchmarks/src/pydocs_eval/_retrieval_extra.py:29` | cites "`[late-interaction]` / `[watch]`" extras style as precedent |
| mypy config | `pyproject.toml:309`, `:259-262` | `pydocs_mcp.serve.watcher` in ignore_errors; comment says watchdog not in follow_imports because only imported from ignored modules |
| Lockfile | `uv.lock:3416-3418, :3478, :3480` | watchdog under `[package.optional-dependencies] watch`, `marker = "extra == 'watch'"`, `provides-extras` |
| CI | `.github/workflows/ci.yml:45-54, :107-108, :286-294` | `uv lock --check` gate; test venv synced with NO `--extra watch`; pip-audit export |
| Watch-mode trigger (co-owned surface — NOT changed by this spec) | `__main__.py:891` | `if getattr(args, "watch", False):` — the sole watch trigger; `serve.watch.enabled` is never read (dead key, docs-audit spec DEFECT 3). Wired by that spec's fix D3, not here — see §1.5 C1 |

Surfaces verified as needing **no** edit: `INSTALL.md` never mentions
`[watch]` or watchdog (its only watch reference is the GPU section's
`pydocs-mcp serve|index|watch` at line 80); `CHANGELOG.md`'s watch/watcher
mentions (seven — e.g. `:113, :198, :376`) are historical records, not
current-state docs;
`docs/superpowers/plans/2026-05-27-serve-watch-flag.md` and the companion
design spec introduced the original "watchdog ships behind `[watch]`"
acceptance criterion — internal planning artifacts are not updated
retroactively (CLAUDE.md, "Where PR / task history DOES belong").

### 1.5 Same-day spec collision — the CLI/MCP docs-audit spec

`docs/superpowers/specs/2026-07-11-cli-mcp-docs-audit-spec.md` ("CLI/MCP
help + documentation conformance — audit and permanent doc-tests"; same
date, also Proposed) rewrites three of the surfaces in §1.4's inventory
with end-states that diverge from this spec's, and one of its findings
falsifies a premise this spec would otherwise carry silently. This section
pins each divergence and the single reconciled end-state, so the two specs
compose instead of fighting.

**C1 — `defaults/default_config.yaml:145-150` comment +
`__main__.py:891` watch trigger.**

- *Audit spec:* DEFECT 3 (CONFIRMED) — `serve.watch.enabled` is a **dead
  key**. `__main__.py:891`'s `if getattr(args, "watch", False):` is the
  only watch-mode trigger; nothing reads the field beyond its declaration
  (`retrieval/config/models.py:518`), so the comment's "CLI flag overrides
  `enabled`" claim (`:145-146`) is false today. Its fix D3 wires the key —
  `if getattr(args, "watch", False) or config.serve.watch.enabled:` — and
  its AC19 pins the new dispatch behavior, making the claim TRUE.
- *This spec:* §3.5 edits the same comment block (deleting the `:150`
  extras sentence).
- *Reconciled:* the extras sentence dies with this spec either way (only
  promotion makes it false). The overrides clause at `:145-146` is **owned
  by D3**: it stays verbatim if D3 lands first; if this spec lands first,
  §3.5's rewording rule applies so this spec never ships a claim it knows
  to be false. This spec does NOT wire `_cmd_serve` — that is D3's change,
  deliberately absent from §1.4's inventory of *this* spec's edits but
  recorded there as a co-owned surface.

**C2 — `DOCUMENTATION.md`, "Live re-indexing".**

- *Audit spec:* D10 (MINOR) quotes the unquoted
  `pip install pydocs-mcp[watch]` at `DOCUMENTATION.md:381` (zsh glob
  expansion) and adds a harvester lint requiring every
  `pip install …[extra]` line in the doc corpus to be quoted. Separately,
  DEFECT 3's doc side also cites `DOCUMENTATION.md:413-415`/`:421`
  ("The CLI `--watch` flag overrides `enabled` at runtime").
- *This spec:* §3.6 deletes the entire extras-install instruction at
  `:376-388` — including the exact line D10 quotes — and the rewritten
  doc-pin test forbids any watch extras-install instruction thereafter.
- *Reconciled:* **deletion supersedes quoting.** If this spec lands first,
  the audit spec closes D10 as superseded for watch (its quoting lint
  still lands with the harness and governs the remaining real extras —
  `[graph]`, `[late-interaction]`, …). If D10 lands first, its `:381` fix
  is one-PR churn that this spec then deletes — harmless. The
  `:413-415`/`:421` override claims are NOT touched by this spec: they are
  DEFECT 3 / D3 territory and become true when D3 wires the key.

**C3 — `tests/test_readme_watch_mention.py`.**

- *Audit spec:* D10's regression lint (group-1 harvester) requires any
  extras-install line that exists in the docs to be quoted.
- *This spec:* §3.9 rewrites `:11-20`/`:41-43` into **negative**
  assertions (no watch extras-install instruction anywhere).
- *Reconciled:* no conflict once the negative assertions match **both
  spellings** — `pip install pydocs-mcp[watch]` AND
  `pip install 'pydocs-mcp[watch]'`. This is load-bearing: if D10 lands
  first, `:381` is already the quoted form, and an unquoted-only grep
  would pass while the instruction survived. With both spellings matched,
  the audit's lint passes vacuously for watch and keeps governing the
  other extras; §3.9 carries the requirement.

**Landing order.** Preferred: **the audit spec's D3 wiring lands before
this spec** (the rest of its harness is order-independent with respect to
this change). That order makes §3.5 a pure sentence deletion, keeps every
"overrides" claim true at every commit, and reduces D10 to harmless churn.
If this spec lands first instead: (a) §3.5's rewording rule for `:145-146`
applies; (b) the audit spec closes D10 as superseded; (c) the audit spec's
DEFECT 3 doc-side citations (`default_config.yaml:145-153`,
`DOCUMENTATION.md:413-421`) and D10 target must be rebased against the
post-deletion line numbering. The two specs must NOT share a PR — each
carries its own test inversions that cross-check its own edits (§6).
Whichever spec lands second re-verifies its pinned `file:line` citations
against the merged tree before implementation.

---

## 2. Goals / Non-goals

### Goals

1. `pip install pydocs-mcp` (no extras) suffices for `pydocs-mcp serve
   --watch` and `pydocs-mcp watch` on Linux, macOS, and Windows.
2. `watchdog>=4.0,<6.0` moves **verbatim** into `[project] dependencies` —
   same floor, same ceiling, no pin drift.
3. `pip install pydocs-mcp[watch]` remains a valid install command forever
   (empty alias extra) — zero breakage for existing docs, scripts, CI configs,
   and downstream lockfiles that reference the extra.
4. The unreachable guard path (`_INSTALL_HINT`, the `try/except ImportError`
   in `_load_watchdog`, the `ServiceUnavailableError` import in
   `serve/watcher.py`) and all dead guidance strings (CLI help, docstrings,
   YAML comments, doc prose) are deleted, not commented out.
5. Every doc surface (README, DOCUMENTATION.md, SPEC.md, CLAUDE.md,
   default_config.yaml comment, CLI help) reflects the new layout; the Sphinx
   site follows automatically through its MyST includes.
6. `uv.lock` is relocked; the `uv lock --check` gate, the coverage gate, and
   the pip-audit gate all stay green with understood, documented deltas.
7. CLAUDE.md's extras policy line is amended with the promotion criteria from
   §1.3 so the next promotion request has a written bar to clear.

### Non-goals

1. **No change to `serve.watch.enabled: false`.** "Installed by default" ≠
   "watching by default". Enabling watch-by-default is a behavioral change to
   `serve` (background reindex I/O on every server start) and is deliberately
   out of scope; it is listed as an open question (§7.1). Caveat (docs-audit
   spec DEFECT 3, §1.5 C1): the key is **dead today** — `__main__.py:891`
   keys watch mode off the CLI flag alone and nothing reads
   `serve.watch.enabled` beyond its declaration
   (`retrieval/config/models.py:518`) — so a bare default flip would be a
   no-op until that spec's fix D3 wires `_cmd_serve`. Per the repo
   constitution, if watch-by-default ever lands it lands as a YAML default
   flip (`AppConfig`, pydantic `Field(default=...)`) **on top of D3's
   wiring**, never as a new MCP parameter — the MCP surface stays fixed at
   the six task-shaped tools.
2. **No new MCP tools or parameters.** This change is purely packaging + dead
   code removal behind the existing surface (`get_overview`,
   `search_codebase`, `get_symbol`, `get_context`, `get_references`,
   `get_why` are untouched).
3. **No behavior change to the watcher itself** — same debounce, same
   matcher, same manifest retrigger, same `observer_factory` test seam.
4. **No change to multi-repo serve.** `--workspace`/`--db` mode has no
   indexing phase and returns before the watch branch
   (`__main__.py:872-881`); it stays read-only, no watch
   (`README.md:166`).
5. **No promotion of any other extra.** `[sentence-transformers]`,
   `[openvino]`, `[late-interaction]`, `[graph]`, `[ask-your-docs]` are
   unchanged and remain governed by the (amended) policy.
6. **No vendored polling fallback watcher** (rejected alternative, §4.2).

---

## 3. Detailed design

### 3.1 `pyproject.toml` — dependency move + alias extra

```toml
[project]
dependencies = [
    # ... existing ten deps unchanged (mcp, pydantic, pydantic-settings,
    # pyyaml, numpy, turbovec, fastembed, openai, jinja2, tiktoken) ...
    # Live re-indexing (serve --watch / the watch verb). Promoted from the
    # [watch] extra 2026-07: ~684 KB installed, zero transitive deps,
    # prebuilt wheels on every supported platform. See
    # docs/superpowers/specs/2026-07-11-watch-default-install-spec.md.
    "watchdog>=4.0,<6.0",
]

[project.optional-dependencies]
# DEPRECATED alias: watchdog moved into the required runtime deps.
# Kept empty so `pip install pydocs-mcp[watch]` in existing docs/scripts
# stays a valid no-op. Removal horizon: next major version.
watch = []
# ... other extras unchanged ...
```

Rules honored:

- The pin moves **verbatim** (`>=4.0,<6.0`) — the sole pin site today is the
  extra (`pyproject.toml:62`; no `[tool.uv]` constraint exists for watchdog,
  `pyproject.toml:140-146`), so after the move there is still exactly one pin
  site. Single source of truth for the version range is preserved
  (CLAUDE.md "Default values: single source of truth" — same spirit applied
  to dependency pins).
- The alias-extra mechanics are verified: `uv lock` records extras in
  `provides-extras` (`uv.lock:3480`), so an empty `watch = []` keeps the
  extra published in wheel metadata and `pip install pydocs-mcp[watch]`
  resolves as a no-op. No existing extra is empty today
  (`pyproject.toml:58-96`) — this is the repo's first alias extra, hence the
  explicit deprecation comment with a removal horizon.
- The comment block at `pyproject.toml:59-61` ("User-facing extras only")
  gains one clause noting that `watch` is a deprecated empty alias.

Why an empty alias rather than deleting the extra: `pip install
pydocs-mcp[nonexistent]` is a hard error on modern pip. Existing install
docs, user shell history, CI snippets, and downstream projects that pin
`pydocs-mcp[watch]` would break on upgrade for zero benefit. An empty extra
costs one line and one lock entry.

### 3.2 `serve/watcher.py` — guard removal

The `ImportError` branch becomes unreachable once watchdog is a required
runtime dep (a missing required dep is environment corruption — the package
itself failed to install correctly — not a supported state to catch and
translate). Changes:

1. **Delete** `_INSTALL_HINT` (`serve/watcher.py:35-37`).
2. **Delete** the `from pydocs_mcp.application.mcp_errors import
   ServiceUnavailableError` import (`serve/watcher.py:31`) — the guard at
   `serve/watcher.py:69` is that module's sole raise site of it, and after
   removal the import is dead. `ServiceUnavailableError` itself stays in
   `application/mcp_errors.py:33-37` untouched; the Null services
   (`application/null_services.py`, `storage/null_multi_vector_store.py`)
   and the `server.py` handler wrapper (`server.py:228`) still raise it.
   (The ask-your-docs guard, `ask_your_docs/cli.py:30`, raises
   `SystemExit`, not this error — unaffected either way.)
3. **Keep `_load_watchdog()` but simplify it** to a plain import wrapper:

   ```python
   def _load_watchdog():
       """Resolve `watchdog.observers.Observer`.

       Kept as a seam (rather than a top-level import) for two reasons:
       tests monkeypatch `watcher_mod._load_watchdog` to inject
       `FakeObserver` behavior, and the deferred import keeps
       `import pydocs_mcp.serve.watcher` cheap for callers that construct
       `FileWatcher(observer_factory=...)` explicitly.

       `_Handler` still uses duck typing (`handler.dispatch(event)`), so
       the FileSystemEventHandler class is deliberately not imported.
       """
       from watchdog.observers import Observer

       return Observer
   ```

   Rationale for keeping the function instead of inlining a top-level
   import: (a) ~6 test files monkeypatch `watcher_mod._load_watchdog` or
   pass `observer_factory=FakeObserver` — the seam must survive (research
   fact + open question resolved in favor of the seam); (b) it keeps the
   duck-typing note about `_Handler` attached to the import site. The
   `try/except ImportError` disappears; a broken environment now surfaces as
   a plain `ImportError` with the real traceback, which is strictly more
   diagnosable than a misleading "install the extras" hint that would no
   longer be the fix.
4. **Rewrite the module docstring** (`serve/watcher.py:8-13`): delete the
   "Lazy import boundary" paragraph (its premise — "watchdog lives behind
   the `[watch]` extras group" — is dead guidance) and replace it with one
   sentence noting the deferred-import seam exists for test injection and
   import-time cheapness, not extras gating. The event-loop-bridge paragraph
   (`:15-18`) stays as-is.
5. `FileWatcher.__post_init__` (`serve/watcher.py:92-97`) is unchanged in
   shape — `observer_factory is None → _load_watchdog()` — only the WHY
   comment loses its "or raise the install hint" clause. The
   `@dataclass(frozen=True, slots=True)` value-object shape and
   `object.__setattr__` normalization are untouched (constitution:
   frozen/slots value objects).

### 3.3 `serve/__init__.py` — docstring update

`serve/__init__.py:1-7` currently explains the package split with "Kept
separate ... so the `watchdog` lazy import lives in a leaf module ... Default
`pydocs-mcp serve` never imports anything in this package." The package split
itself remains correct and desirable (leaf module, `serve` without `--watch`
never imports it — import cost hygiene), but the *extras* rationale is dead.
Rewrite the docstring to justify the split on import-cost/leaf-module grounds
alone.

The function-local `from pydocs_mcp.serve.watcher import FileWatcher` in
`_build_watcher_and_callback` (`__main__.py:502-545`, local import at
`:514`) and the TYPE_CHECKING-only top-level import (`__main__.py:36-38`)
**stay as they are**: they are import-cost hygiene (don't pay the watcher
import on `pydocs-mcp search`), not extras gating, and they keep working
identically after promotion.

### 3.4 `__main__.py` — CLI help text

`__main__.py:195-201`: the `--watch` help string drops its second sentence.

```python
sp.add_argument(
    "--watch",
    action="store_true",
    help="Watch the project for changes and reindex on edits.",
)
```

### 3.5 YAML config surface

**No key changes.** The `serve.watch.*` block
(`defaults/default_config.yaml:151-162`) keeps its exact schema and defaults:

```yaml
serve:
  watch:
    enabled: false        # unchanged; dead until the docs-audit spec's D3
                          # wires it (Non-goal 1 / §1.5 C1 / §7.1)
    debounce_ms: 500
    extensions: [.py, .md, .ipynb]
    ignore_globs: [...]
```

The only edit is to the comment block at
`defaults/default_config.yaml:145-150`: delete the sentence
"Requires `pip install pydocs-mcp[watch]`" at `:150` (false after
promotion). **Collision guard (§1.5 C1):** the same block's "CLI flag
overrides `enabled`" clause (`:145-146`) is doc-side evidence in the
docs-audit spec's DEFECT 3 — the key is dead until that spec's fix D3 wires
it into `_cmd_serve` (`__main__.py:891`). If D3 has landed first (the
preferred order, §1.5/§6), keep the overrides clause verbatim — it is then
true. If this spec lands first, reword the clause to "`--watch` is currently
the only way to enable watching" so this spec does not ship an override
claim the code lacks, and leave restoring the override wording to D3. Either
way this spec complies with the constitution's YAML rule by omission: no new
tunable is introduced, no CLI flag or MCP param is added, and the existing
tunables stay YAML-owned in `AppConfig`.

### 3.6 Documentation updates

| File | Edit |
|---|---|
| `README.md:138-152` | Retitle "Live re-indexing (optional)" → "Live re-indexing". Delete the `pip install 'pydocs-mcp[watch]'` step at `:145`; state that the watcher is part of the default install. Keep the `--watch` / `watch`-verb usage examples. **Constraint:** `tests/test_readme_watch_mention.py:11-20` requires README to contain `--watch` AND (`pydocs-mcp[watch]` OR `[watch]`) — the test is rewritten (§5, AC-10) to require `--watch` plus a "default install" statement, and to *forbid* an extras-install instruction for watch. The README jargon audit (`tests/test_readme_watch_mention.py:46-70`) re-runs unchanged — the new prose cites no PR numbers. |
| `DOCUMENTATION.md:376-388` | Rewrite the "### Install" subsection of "Live re-indexing": drop "ships as an optional extra", drop the `pip install pydocs-mcp[watch]` command at `:381`, and delete the no-extras exit-with-hint behavior description at `:386-388` (that behavior no longer exists). Note the `[watch]` extra remains as a deprecated no-op alias for old instructions. The Sphinx page `documentation/user-guide/live-reindex.md:3-8` includes this section by MyST heading markers ("## Live re-indexing" → end-before "---"), so the published site updates with **zero** edits under `documentation/` — but beware the house rule: never use `---` as a *new* include end-before marker (memory: MyST include table-separator trap); this spec does not touch the include directives. **Collision (§1.5 C2):** this deletion removes the exact line the docs-audit spec's D10 quotes (`:381`) — deletion supersedes quoting. This spec deliberately does NOT touch the "YAML knobs" subsection's override claims at `:413-415`/`:421`; those are that spec's DEFECT 3 / D3 surface and become true when D3 wires the key. |
| `SPEC.md:367` | "requires the `[watch]` extra" → "included in the default install". |
| `SPEC.md:514` | Remove `'pydocs-mcp[watch]'` from the Installation extras list (or annotate it as a deprecated empty alias, matching pyproject). |
| `CLAUDE.md:41` | Drop the `([watch] extra)` annotation from the serve/watch command examples. |
| `CLAUDE.md:119` | Architecture-tree line for `serve/`: drop "`[watch]` extra", keep the watcher description. |
| `CLAUDE.md:143` | Amend the extras policy line: remove `[watch]` from the opt-in list; add watchdog to the required-deps sentence at `CLAUDE.md:142`; append the promotion criteria from §1.3 as the policy's exception clause (one or two sentences, so the next request has a written bar). |
| `INSTALL.md` | No extras list exists to update (verified: only watch reference is line 80's `serve|index|watch`). Add the air-gap wheelhouse note (§3.7) to the "Quick install" area (lines 3-17) or a small new subsection. |
| `documentation/conf.py:64-68` | The commented-out `autodoc_mock_imports` list names `watchdog`; the comment's premise ("if you'd rather NOT install runtime deps") now *includes* watchdog rather than treating it as an extra. One-line comment touch-up; the setting stays commented out. |
| `benchmarks/src/pydocs_eval/_retrieval_extra.py:29` | Docstring cites "``[late-interaction]`` / ``[watch]``" as the product repo's extras style; swap `[watch]` for `[graph]` (still-real extra) so the precedent citation stays truthful. |

### 3.7 Air-gap wheelhouse note

New prose in `INSTALL.md` (and inherited by the Sphinx site via
`documentation/getting-started/install.md:1`'s include):

> **Air-gapped installs.** pydocs-mcp and all required dependencies install
> from a wheelhouse with no network access:
>
> ```bash
> # On a connected machine (match the target's OS/arch/Python):
> pip download pydocs-mcp -d ./wheelhouse
> # On the air-gapped machine:
> pip install --no-index --find-links ./wheelhouse pydocs-mcp
> ```
>
> The live re-indexing dependency (`watchdog`) is part of the required set
> and adds a single ~80–97 KB wheel to the wheelhouse; prebuilt wheels exist
> for Linux (x86_64/aarch64/armv7l/i686/ppc64/ppc64le/s390x), macOS
> (x86_64/arm64/universal2), and Windows — no compiler needed.

Grounding: wheel coverage per `uv.lock:5101-5121`; the air-gap constraint is
a real deployment property of this project's consumers
(`benchmarks/pyproject.toml:48` documents the air-gapped-PyPI-mirror
environment; `docs/superpowers/specs/2026-07-03-airgap-local-embedders-design.md`
covers the embedder side). No existing doc describes a core-package
wheelhouse, so this is net-new prose and `INSTALL.md` is its natural home.

### 3.8 Lockfile & CI implications

**Relock.** `uv lock` after the pyproject edit. Because watchdog 5.0.3 is
*already resolved and pinned* in `uv.lock` (via the `[watch]` extra at
`uv.lock:3416-3418`/`:3478` and as streamlit's non-darwin dep at
`uv.lock:4515`), promotion changes only the pydocs-mcp metadata sections of
the lock — watchdog moves from `[package.optional-dependencies]` into the
main `dependencies` list, the `marker = "extra == 'watch'"` specifier loses
its marker, and `provides-extras` (`uv.lock:3480`) keeps `watch`. The
resolution universe does not change; the diff is small and reviewable.

Process notes (auto-memory–backed): macOS-produced relocks pass the Linux
`uv lock --check` gate, so relocking on this machine is CI-valid; and survey
open PRs before touching `uv.lock` — concurrent lockfile edits have collided
before (PRs #170/#172 precedent).

**CI gate deltas, one by one** (`.github/workflows/ci.yml`):

1. **`lockfile` job** (`ci.yml:45-54`, gate at `:54` `uv lock --check`):
   green iff the relock is committed in the same PR as the pyproject edit.
   Editing pyproject without relocking reddens CI — the relock is mandatory,
   not optional.
2. **Test matrix** (`ci.yml:107-108` `uv sync --frozen --group dev`): today
   this venv has **no** watchdog (no `--extra watch` is passed; only
   `--extra graph` is added later for the three node-score test files,
   `:195-204`). After promotion, watchdog arrives via the base project deps
   with no workflow edit. Consequence: tests gated by
   `pytest.importorskip("watchdog")` — `tests/test_watcher.py:56` and
   `tests/integration/test_watcher_real_watchdog.py:20` — flip from SKIP to
   RUN in CI. This is a **feature** (real-watchdog coverage was silently
   skipped in CI until now) but also new CI surface: the real-observer
   integration test now runs on all three OSes; if it proves flaky on any
   runner, that is a follow-up to fix the test, not to re-demote the dep.
   The same applies locally: the dev dependency-group has no watchdog
   (`pyproject.toml:98-128`), so local `uv sync --group dev` venvs gain it
   via base deps and the skipped tests start running everywhere.
3. **Coverage gate** (`ci.yml:166-176`, `--cov-fail-under=90`; the run is
   documented as "a faithful default-install (no-extra) check" at
   `:189-191`): watchdog becoming a default dep is *consistent* with that
   framing — the default install now includes it, so importing it in
   coverage runs is faithful. `serve/watcher.py` is already covered ≥90 %
   via FakeObserver tests (`tests/test_watcher.py`,
   `test_watcher_dispatch_contract.py`, `test_main_cli_watch*.py`);
   the newly-running real-watchdog tests only add coverage. Deleting the
   guard code removes lines that were covered by the guard regression test —
   both the lines and their test go together, so the ratio is unaffected in
   the same commit.
4. **Security / pip-audit job** (`ci.yml:286-294`: `uv export --frozen
   --no-emit-project --no-group docs` → `uvx pip-audit --strict`): verified
   empirically in this worktree — the exact CI export contains **zero**
   watchdog lines today and exactly **one** with `--all-extras`. Promotion
   therefore grows the audit surface by exactly one package (watchdog, no
   transitives). watchdog is a mature, widely-audited project; the `<6.0`
   ceiling bounds surprise majors. Should a watchdog CVE land, the strict
   gate reddens and the fix is a routine floor bump within the existing
   range — same posture as every other required dep.
5. **mypy job**: no forced change. `pydocs_mcp.serve.watcher` sits in the
   `ignore_errors` override list (`pyproject.toml:309`) and the
   third-party-stubs comment (`pyproject.toml:259-262`) explicitly notes
   watchdog is not in `follow_imports` overrides *because* it is only
   imported from ignored modules — a fact promotion does not change.
   Graduating `serve/watcher.py` out of `ignore_errors` (watchdog ships
   `py.typed` since 2.x, so no stub gap is expected) is desirable but
   **out of scope**; recorded as open question §7.4.

### 3.9 Test changes (design; ACs in §5)

- `tests/test_pyproject_extras.py:56-81` — the three layout-pinning tests are
  inverted to pin the **new** layout:
  - `test_watch_extras_group_present` (`:56-62`) → becomes
    `test_watch_extra_is_empty_backcompat_alias`: `watch` key exists in
    `[project.optional-dependencies]` AND its value is `[]`.
  - `test_watchdog_not_in_main_dependencies` (`:65-72`, assertion `:70-72`)
    → becomes `test_watchdog_in_main_dependencies`: exactly one entry in
    `[project] dependencies` starts with `watchdog`.
  - `test_watch_extras_pins_watchdog_version_range` (`:75-81`) → becomes
    `test_watchdog_main_dep_pins_version_range`: the main-deps watchdog
    entry contains `>=4.0` and `<6.0` (pin moved verbatim).
- `tests/test_watcher.py:27-50`
  (`test_watcher_construction_raises_when_watchdog_missing`) — **deleted**,
  together with the module-level `from pydocs_mcp.application.mcp_errors
  import ServiceUnavailableError` import at `tests/test_watcher.py:15`
  (that test is its only consumer; leaving it would redden `ruff check`
  with F401). With watchdog required, a simulated-missing watchdog is
  environment corruption, not a supported state; there is no
  `ServiceUnavailableError`-with-hint contract left to regress. (Converting
  it to assert a bare `ImportError` propagates would pin an accident of the
  import machinery, not a designed behavior — see §7.3 for the recorded
  decision.) The companion `importorskip`-gated construction test (`:53-67`)
  keeps its `importorskip` line (harmless once watchdog is always present;
  it degrades gracefully if someone builds a stripped environment) and now
  runs in CI.
- `tests/test_readme_watch_mention.py` — rewritten:
  - `:11-20`: README must still contain `--watch`; the `[watch]`-mention
    requirement flips to a **negative** assertion — README must NOT instruct
    `pip install ... [watch]` for the watcher (a passing mention of the
    deprecated alias is acceptable only if the implementer keeps one; the
    simplest compliant README has none).
  - `:41-43`: the DOCUMENTATION.md literal-`pip install pydocs-mcp[watch]`
    requirement is inverted the same way, or retargeted to require the
    "default install" statement.
  - **Both negative assertions MUST match the quoted and the unquoted
    spelling** — `pip install pydocs-mcp[watch]` AND
    `pip install 'pydocs-mcp[watch]'` (§1.5 C3). Load-bearing: if the
    docs-audit spec's D10 lands first, `DOCUMENTATION.md:381` is already
    the quoted form, and an unquoted-only grep would pass while the
    instruction survived. That spec's harvester lint (every extras-install
    line quoted) then passes vacuously for watch and keeps governing the
    remaining real extras.
  - `:46-70`: the README jargon audit re-runs unchanged.
- New test `tests/test_pyproject_extras.py::test_no_watch_install_hint_left`
  (belt-and-braces): grep `python/pydocs_mcp/` for the literal
  `pydocs-mcp[watch]` — zero hits allowed in shipped code (docs excluded).
  This catches a resurrected hint string in help text or error messages.

No application service, repository, UoW, Protocol, registry, or MCP handler
is touched — the hexagonal seams (constitution: composition roots only in
`server.py`/`__main__.py`/`storage/factories.py`) are unaffected because the
watcher wiring already lives in the `__main__.py` composition root
(`_build_watcher_and_callback`, `__main__.py:502-545`).

---

## 4. Alternatives considered

### 4.1 Keep `[watch]` as an extra, improve the hint (auto-hint / soft-fail)

Keep the current packaging; polish the failure UX — e.g. the CLI detects the
missing extra at argument-parse time and prints the exact install command
(possibly pip-vs-uv aware), or `serve --watch` degrades to plain `serve` with
a warning.

- **Pros:**
  - Zero packaging change; default install stays byte-identical.
  - No policy amendment; CLAUDE.md:143 stays literally true.
  - No lockfile, pip-audit, or CI-matrix deltas.
- **Cons:**
  - Does not satisfy the user requirement ("installed by default") — the
    first `--watch` run still fails or silently degrades.
  - Degrade-to-no-watch is the worst variant: a stale index with a warning
    the agent's user never sees violates the project's core value
    proposition (fresh answers).
  - The guard machinery (`_INSTALL_HINT`, `ServiceUnavailableError` path,
    the regression test that monkeypatches `builtins.__import__`) is
    permanent complexity for a 684 KB saving — the maintenance cost of the
    gate now exceeds the cost of the gated dependency.
  - CI keeps silently skipping the real-watchdog tests
    (`ci.yml:107-108` has no `--extra watch`) unless the matrix is also
    changed — the current setup ships watcher code whose real-observer path
    is never exercised in CI.

### 4.2 Vendor a stdlib-only polling fallback watcher

Keep watchdog optional; add a net-new polling observer (periodic
`os.scandir` + mtime diff over the watched tree) used when watchdog is
absent, so `--watch` always works with degraded latency.

- **Pros:**
  - `--watch` works on any install, even stripped environments, with zero
    added dependencies.
  - Latency degradation (poll interval vs. native events) is acceptable for
    the reindex-on-debounce use case (debounce is already 500 ms).
- **Cons:**
  - **Net-new code with no existing basis:** verified — there is no polling
    fallback anywhere in the repo; `serve/watcher.py`'s only observer source
    is `watchdog.observers.Observer` (`:53-70`), and `observer_factory`
    exists solely for FakeObserver test injection (`:87-90`). Even
    watchdog's own `PollingObserver` is unused (grep: zero hits in
    `python/`).
  - A hand-rolled poller is a minefield: recursive scans of large trees
    (site-packages-adjacent projects), ignore-glob parity with the event
    matcher, mtime granularity on network filesystems, CPU cost of the poll
    loop inside the MCP server process — each a bug class watchdog already
    solved. Maintaining a second observer implementation to avoid a 684 KB,
    zero-transitive-dep wheel inverts the cost-benefit completely.
  - Violates the fallback contract's spirit: the repo's Rust/Python
    substitution boundary demands behavioral parity
    (CLAUDE.md "Fallback contract"); a polling watcher is *not* behaviorally
    equivalent to a native-events watcher (latency, event coalescing, rename
    semantics), so it creates a two-tier UX that is hard to document and
    test honestly.
  - Doubles the watcher test matrix (every dispatch/debounce/ignore test ×
    two observers).
  - If a poller were ever genuinely wanted, `watchdog.observers.polling
    .PollingObserver` behind the existing `observer_factory` seam would be
    the way — which presupposes watchdog installed, i.e. this alternative
    collapses into full promotion anyway.

### 4.3 Full promotion (recommended)

Move `watchdog>=4.0,<6.0` into required deps; empty alias extra; delete the
guard.

- **Pros:**
  - Directly satisfies the user requirement: `--watch` and the `watch` verb
    work on a fresh default install, all platforms, no extra step.
  - Quantified cost is negligible: +684 KB installed (+0.7 % of ~90 MB),
    +1 wheel of ~80–97 KB in wheelhouses, **zero** transitive deps
    (§1.3, all figures from `uv.lock:5097-5121` and local measurement).
  - Deletes code: the guard, the hint string, a dead import, a
    simulate-missing regression test, and four dead doc paragraphs. Net
    complexity goes down.
  - CI gets *stronger*: the real-watchdog integration tests stop being
    silently skipped in the test matrix.
  - The lock delta is minimal because watchdog 5.0.3 is already resolved in
    `uv.lock` (via the extra and via streamlit's non-darwin dep,
    `uv.lock:4515`).
  - The back-compat alias makes the migration invisible to every existing
    install command.
- **Cons:**
  - Amends a written policy (CLAUDE.md:143) — mitigated by amending the
    policy *with criteria* (§1.3) rather than just carving a silent
    exception, so the policy comes out stronger, not weaker.
  - pip-audit surface grows by one package — bounded, measured (exactly one
    line in the CI export), and governed by the same CVE-response process as
    every other dep.
  - Users who never use `--watch` carry 684 KB they don't need — the
    definition of negligible in a 90 MB install.

**Recommendation: 4.3, full promotion.** 4.1 fails the requirement; 4.2 buys
independence from a 684 KB dependency at the price of an entire second
observer implementation. Promotion is the only option where the codebase gets
*smaller* while the feature gets more reliable.

---

## 5. Testing & acceptance criteria

Run set (constitution: full CI gate set before pushing): `pytest -q`,
`PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`, `ruff check` +
`ruff format --check` on `python/ tests/ benchmarks/`, `mypy
python/pydocs_mcp`, `complexipy --max-complexity-allowed 15`, `vulture
--min-confidence 80`, coverage gate `--cov-fail-under=90`, `uv lock
--check`, and the pip-audit export pair.

Each AC is independently checkable:

- **AC-1 (dependency move, verbatim pin).**
  `tests/test_pyproject_extras.py::test_watchdog_in_main_dependencies`
  passes: `[project] dependencies` contains exactly one `watchdog` entry;
  `test_watchdog_main_dep_pins_version_range` passes: that entry contains
  both `>=4.0` and `<6.0`.
- **AC-2 (alias extra).**
  `tests/test_pyproject_extras.py::test_watch_extra_is_empty_backcompat_alias`
  passes: `[project.optional-dependencies].watch == []`. Manual check:
  `pip install 'pydocs-mcp[watch]'` from the built wheel succeeds as a
  no-op (extra resolves, installs nothing beyond base deps).
- **AC-3 (fresh-install UX).** In a clean venv, `pip install .` (no extras)
  then `python -c "from watchdog.observers import Observer"` succeeds, and
  `pydocs-mcp serve <tmp-project> --watch` starts the watcher without
  raising `ServiceUnavailableError`. (Automated proxy: the previously
  importorskip-gated tests `tests/test_watcher.py:53-67` and
  `tests/integration/test_watcher_real_watchdog.py` execute — not skip — in
  the CI venv built with `uv sync --frozen --group dev` and no
  `--extra watch`.)
- **AC-4 (guard deletion).** `serve/watcher.py` contains no
  `_INSTALL_HINT`, no `except ImportError`, and no
  `ServiceUnavailableError` import; the deleted regression test
  `test_watcher_construction_raises_when_watchdog_missing`
  (`tests/test_watcher.py:27-50`) is gone. `vulture` reports no new dead
  code in `serve/watcher.py` (the simplified `_load_watchdog` is still
  called from `__post_init__`).
- **AC-5 (no hint strings in shipped code).** New test
  `test_no_watch_install_hint_left` passes: `grep -r "pydocs-mcp\[watch\]"
  python/pydocs_mcp/` → zero hits. This covers the CLI help
  (`__main__.py:200`), the watcher module, and the default-config comment
  (`defaults/default_config.yaml:150` ships inside the package).
- **AC-6 (CLI help).** `pydocs-mcp serve --help` output for `--watch`
  contains "Watch the project for changes" and does NOT contain "extras" or
  `[watch]`. New assertion added to `tests/test_main_cli_watch.py`'s
  parser-shape suite (which already builds the parser via `_build_parser()`),
  reusing the `sub.format_help()` help-text pattern from
  `tests/test_tool_descriptions.py:92` — `test_main_cli_watch.py` has no
  help-text test today, only `parse_args` assertions.
- **AC-7 (watcher behavior unchanged).** The entire existing FakeObserver
  suite (`tests/test_watcher.py` minus the deleted test,
  `tests/test_watcher_dispatch_contract.py`, `tests/test_main_cli_watch*.py`)
  passes without modification other than the one deletion (plus its
  now-unused `ServiceUnavailableError` import, §3.9) — debounce,
  extension normalization, ignore-globs, manifest retrigger, and the
  `observer_factory` seam are untouched.
- **AC-8 (YAML surface unchanged).** `serve.watch.enabled` default remains
  `false` (existing AppConfig test coverage); no new key appears in
  `defaults/default_config.yaml`; the `:150` comment no longer mentions an
  extras install.
- **AC-9 (lockfile).** `uv lock --check` passes on the PR (relock committed
  together with the pyproject edit). Lock diff review: watchdog appears in
  pydocs-mcp's main `dependencies` metadata without the
  `extra == 'watch'` marker; `provides-extras` still lists `watch`;
  watchdog stays at an existing resolved version (no resolution-universe
  churn).
- **AC-10 (doc pins).** Rewritten `tests/test_readme_watch_mention.py`
  passes: README contains `--watch` and a default-install statement, and
  neither README nor DOCUMENTATION.md instructs an extras install for the
  watcher; the README jargon audit still passes; SPEC.md `:367`/`:514` and
  CLAUDE.md `:41`/`:119`/`:143` no longer describe `[watch]` as required
  for the feature (checked by grep in review; CLAUDE.md:143 carries the
  amended policy + promotion criteria).
- **AC-11 (pip-audit surface).** `uv export --frozen --no-emit-project
  --no-group docs --format requirements-txt | grep -c "^watchdog"` → `1`
  (was `0`); `uvx pip-audit --strict` against that export passes.
- **AC-12 (coverage gate).** `pytest tests/ --ignore=tests/test_parity.py
  --cov=pydocs_mcp --cov-fail-under=90` passes with the guard lines and
  their test removed in the same change.
- **AC-13 (air-gap note).** INSTALL.md contains the wheelhouse subsection
  (§3.7) including the `pip download` / `pip install --no-index
  --find-links` pair and the watchdog wheel-size/platform note;
  `documentation/getting-started/install.md`'s include republishes it (no
  `documentation/` edit needed — verify by building docs or by include-range
  inspection).
- **AC-14 (multi-repo untouched).** `pydocs-mcp serve --workspace ...`
  still returns before the watch branch (`__main__.py:872-881`) — existing
  multi-repo tests pass unmodified.
- **AC-15 (cross-spec reconciliation, §1.5).** After this spec lands:
  (a) neither README nor DOCUMENTATION.md contains a watch extras-install
  instruction in **either** quoting form (`pydocs-mcp[watch]` or
  `'pydocs-mcp[watch]'`) — enforced in CI by the §3.9 negative assertions;
  (b) the `defaults/default_config.yaml` watch comment claims YAML-enable /
  override semantics **iff** `_cmd_serve` reads `serve.watch.enabled`
  (review check: `grep -n "watch.enabled" python/pydocs_mcp/__main__.py`
  hits exactly when the comment carries the overrides clause — §3.5's
  collision guard);
  (c) if the docs-audit spec's D3 landed first, its AC19 dispatch tests
  pass unmodified — this spec touches no `_cmd_serve` logic.

TDD ordering (constitution): invert the three `test_pyproject_extras.py`
tests and add AC-5's grep test first (red), make the pyproject + code edits
(green), then the doc/test rewrites, then relock.

---

## 6. Rollout / migration / back-compat

**Single PR.** The pyproject edit, relock, guard deletion, test inversions,
and doc edits must land atomically — the CI gates cross-check each other
(`uv lock --check` reddens on unrelocked pyproject; the doc-pin tests redden
on stale README; the coverage gate needs the guard test deleted alongside
the guard). No feature flag, no config migration, no schema change.

**Cross-spec landing order (§1.5).** This PR and the same-day docs-audit
spec's PR(s) must land **separately**, in the preferred order: that spec's
D3 wiring of `serve.watch.enabled` first, then this spec. That sequencing
makes §3.5 a pure sentence deletion, keeps the "CLI `--watch` overrides
`enabled`" claims true at every commit, and reduces the audit's D10 to
harmless churn (this spec deletes the line D10 quoted). If this spec lands
first: §3.5's rewording rule for `default_config.yaml:145-146` applies, the
audit spec closes D10 as superseded and rebases its DEFECT 3 doc-side line
citations against the post-deletion numbering. Whichever spec lands second
re-verifies its pinned `file:line` citations against the merged tree.

**User migration: none required.**

- `pip install pydocs-mcp` — gains watchdog automatically on next
  install/upgrade (+684 KB).
- `pip install pydocs-mcp[watch]` — keeps working forever as a no-op alias
  (§3.1). Existing docs, scripts, CI snippets, and downstream pins are
  unaffected. The alias is marked deprecated in the pyproject comment with a
  removal horizon of the next major version; actually removing it is a
  separate, future breaking-change decision.
- Downstream lockfiles (uv/poetry/pip-tools) pick up watchdog on their next
  relock of pydocs-mcp; until then nothing breaks because the extra still
  resolves.
- Air-gapped mirrors need the watchdog wheel present — one ~80–97 KB file;
  environments already installing `[watch]` (or streamlit on non-darwin,
  `uv.lock:4515`) mirror it today.

**Behavioral compatibility.**

- `serve --watch` / `watch` on an install that previously had the extra:
  byte-identical behavior (same watchdog version range, same watcher code
  minus the unreachable branch).
- `serve` without `--watch`: still never imports `pydocs_mcp.serve.*`
  (§3.3) — no startup-cost regression.
- The one observable change: an environment where watchdog is *corrupted*
  (present in metadata, broken on import) now raises `ImportError` with the
  real traceback instead of `ServiceUnavailableError` with a
  no-longer-correct install hint. This is a strict diagnosability
  improvement; no supported configuration hits it.

**Versioning.** This is a dependency addition + UX fix with full
back-compat: a minor version bump (feature: watch works by default), not a
major. CHANGELOG entry under the next release: "Live re-indexing
(`serve --watch` / `watch`) now works in the default install; `watchdog` is
a required dependency (+~0.7 MB). The `[watch]` extra remains as a
deprecated no-op alias."

**Rollback.** Revert the PR (one commit): pyproject, lock, code, tests, and
docs all restore together. No data or index migration is involved, so
rollback is clean at any point.

---

## 7. Open questions

1. **Watch-by-default for `serve`?** Should `serve.watch.enabled: false`
   (`defaults/default_config.yaml:151-153`) eventually flip to `true`, or
   `serve` grow an implied `--watch` when a watcher is cheap to run? Out of
   scope here (Non-goal 1); if pursued, it is a YAML default flip measured
   against index-freshness vs. background-I/O cost — squarely "A/B-testable
   against a benchmark", so per the constitution it lives in YAML, and per
   the fixed-surface rule it must not become an MCP parameter. Recommend a
   separate spec. **Prerequisite:** the flip is only meaningful after the
   docs-audit spec's D3 wires the key into `_cmd_serve` — today it is dead
   (§1.5 C1), so "a YAML default flip" presupposes D3, not just this spec.
2. **Alias-extra removal horizon.** This spec says "next major version" in
   the pyproject comment. Confirm at release-planning time whether the alias
   ever actually gets removed, or stays indefinitely (cost is ~zero; pip
   emits no deprecation warning for installing an empty extra, so removal is
   the only way users would notice — arguably a reason to keep it forever).
3. **Deleted vs. converted guard test.** This spec deletes
   `test_watcher_construction_raises_when_watchdog_missing` outright (§3.9)
   on the grounds that simulated-missing-required-dep pins an accident, not
   a contract. If a reviewer wants a tombstone, the alternative is a
   one-line test asserting `_load_watchdog` has no `except ImportError`
   handler — but AC-4's grep-level assertions already cover that. Default:
   delete.
4. **mypy graduation of `serve/watcher.py`.** With watchdog on the
   default-install import path and shipping `py.typed`, `serve/watcher.py`
   could leave the `ignore_errors` list (`pyproject.toml:309`). Deliberately
   out of scope (it may surface unrelated type debt in the module); file as
   a follow-up task, not part of this change.
5. **Windows CI flake budget for the real-observer test.**
   `tests/integration/test_watcher_real_watchdog.py` runs in CI for the
   first time on all OS runners (§3.8 item 2). If the native-observer test
   proves timing-flaky on a runner, the remediation is test hardening
   (longer debounce margins, retry-once) — decide the flake policy when/if
   it fires, and do not gate the promotion on it.
6. **Docs-audit D3 outcome.** The docs-audit spec *recommends* wiring
   `serve.watch.enabled` (its fix D3) but records key deletion as a
   considered-and-rejected alternative (its §4.4). If the maintainer
   overrules and deletes the key instead, §7.1's "YAML default flip"
   framing collapses (no key to flip), §3.5's reconciled comment must drop
   every `enabled` claim rather than reword it, and Non-goal 1 reduces to
   "no watch-by-default mechanism exists". Confirm D3's fate with the
   docs-audit spec's owner before implementing either spec (§1.5).
