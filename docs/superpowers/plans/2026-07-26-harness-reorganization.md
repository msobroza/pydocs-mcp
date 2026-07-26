# Harness reorganization plan — `ask_your_docs` → `pydocs_mcp.harness.ask_your_docs`

**Date:** 2026-07-26 · **Branch:** new branch off `main` (create fresh; do not reuse
`claude/harness-reorganization-backlog-cc42a9`, which is docs-only).
**Status:** owner-ratified refactor. This plan makes the ALREADY-DECIDED move executable by a
future session without re-research. Do not reopen the decisions in §Decided. One PR, one
breaking change, **no merge without the owner's explicit word**.

## Intent

Move the optional LangGraph-agent + Streamlit-UI subpackage (`ask_your_docs`) under a
`harness/` namespace in the product package, mirror the move in `tests/` and `examples/`,
and rename the two user-facing identifiers (console script + extra) to harness-scoped names.
This is a **pure reorganization**: no behavior change, no new features. Platform context — why
a `harness/` namespace exists at all, and why the Step 3 wheel-glob fix matters beyond this PR —
lives in the companion design spec
`docs/superpowers/specs/2026-07-26-retriever-centric-harness-platform-design.md`; this plan
remains normative for every P0 literal (globs, names, paths).

**The MCP surface is FROZEN at nine task-shaped tools** (`docs/tool-contracts.md`, ADRs
0001–0004). This refactor adds **zero MCP tools and zero MCP parameters**; nothing here
touches `server.py`'s tool signatures. Any behavior tuning stays in YAML via `AppConfig` —
and this plan deliberately does NOT rename the `ask_your_docs:` YAML block or the
`PYDOCS_ASK_YOUR_DOCS__*` env prefix (see §Decided item 6).

## Decided (owner-ratified — do not reopen)

1. `python/pydocs_mcp/ask_your_docs/` → `python/pydocs_mcp/harness/ask_your_docs/`.
2. `tests/ask_your_docs/` → `tests/harness/ask_your_docs/`.
3. `examples/ask_your_docs_agent/` → `examples/harness/ask_your_docs_agent/`.
4. **BREAKING**: console script `ask-your-docs` and extra `[ask-your-docs]` get
   harness-scoped names. Recorded lean: **`harness-ask-your-docs` for both** (see §Open —
   this exact string is the ONE name still needing owner confirmation before execution).
5. **NO compat shim** — no alias module, no duplicate `[project.scripts]` entry, no
   deprecated-extra passthrough. Anyone using the old names gets a clean break, stated
   plainly in the PR body and CHANGELOG.
6. `python/pydocs_mcp/retrieval/config/ask_your_docs_models.py` **STAYS PUT**. The config
   layer aggregates config only; moving it under `harness/` would invert the dependency
   (`app_config.py:26` imports it). Corollary: the `ask_your_docs:` YAML key and
   `PYDOCS_ASK_YOUR_DOCS__*` env prefix (derived from `app_config.py:176`) are NOT renamed —
   the directory move does not require it, and renaming them is a separate user-visible
   config break nobody ratified.
7. Use `git mv` for all three trees so history follows.
8. ONE PR stating the breaking change plainly. The executor must NOT merge without the
   owner's word.

### Rename-scope rule (plan-level; follows from §Decided 4/6)

Exactly three identifier classes get renamed: (a) the console script `ask-your-docs`,
(b) the extra bracket `[ask-your-docs]`, and (c) the module/dir path
`pydocs_mcp.ask_your_docs` / `*/ask_your_docs/` in the three moved trees. **Prose that
names the agent as a product — "the ask-your-docs agent", "the ask-your-docs UI",
"the ask-your-docs prompt-assembly channel" — is NOT renamed.** It is neither a command
invocation, an extra bracket, nor a path, and it stays coherent with the unrenamed
`ask_your_docs:` YAML key and env prefix (§Decided 6). Every per-file edit list below
follows this rule; when a line mixes classes (e.g. prose plus an extra bracket), only the
identifier inside it changes.

## Open decisions

1. **Exact new identifier string.** Options: (a) `harness-ask-your-docs` (script) +
   `[harness-ask-your-docs]` (extra) — the recorded lean; (b) `pydocs-harness` /
   `[harness]` — shorter, but collides semantically with the *eval* harness vocabulary
   (`benchmarks/`, `pydocs_eval`, `AGENT_TRACK.md`), see trap T8. **Recommendation:** (a),
   as ratified-lean; confirm the literal string with the owner **before Step 2** (the gate
   is a Step 0 pre-flight checkbox), since Step 2 already writes it into `cli.py:51`
   (`prog=`) and `cli.py:38–39` (missing-extra message), and Step 3 bakes it into
   `pyproject.toml`, two test oracles, and every doc edit below.
2. **Whether `tests/test_config_ask_your_docs.py` moves to `tests/harness/`.** It targets
   `pydocs_mcp.retrieval.config.ask_your_docs_models` (which stays put), so its imports
   are unaffected. **Recommendation:** leave it at `tests/` top level — it tests the config
   layer, not the harness package; moving it would misfile it. Only its prose mentions of
   the extra name change (Step 5).
3. **The log-channel name** `logging.getLogger("pydocs-mcp.ask-your-docs")` at
   `multimodal.py:20`. It is observable output (log filters/handlers key on it), not prose,
   so the rename-scope rule does not settle it, and §Decided 4 ratified only the script +
   extra. Options: (a) rename to `"pydocs-mcp.harness.ask-your-docs"` in this PR —
   consistent with the ratified clean-break posture, one line, and this is the only PR
   where the break is expected; (b) keep it — stable channel for existing log configs.
   **Recommendation:** (a); confirm with the owner alongside Open 1.

## Step-by-step execution

### Step 0 — Pre-flight (verify the inventory still holds)

- [x] **Owner has confirmed the literal identifier string** (recorded lean:
      `harness-ask-your-docs` for both script and extra — §Open 1) and ruled on the
      log-channel name (§Open 3). Do not begin Step 1 without both.
- [x] Fresh branch off up-to-date `main`; working tree clean.
- [x] Confirm counts: `git ls-files python/pydocs_mcp/ask_your_docs | wc -l` → **28**;
      `git ls-files tests/ask_your_docs | wc -l` → **18** (**19** if trap T12's
      file-watcher branch landed first — see T12);
      `git ls-files examples/ask_your_docs_agent | wc -l` → **7**.
- [x] Confirm parents do not exist yet: `ls python/pydocs_mcp/ | grep harness` (empty),
      same for `tests/` and `examples/`.
- [x] Confirm zero core-import blast radius:
      `grep -rn 'ask_your_docs\|ask-your-docs' python/pydocs_mcp/ --exclude-dir=ask_your_docs`
      — expect exactly 6 files / 11 hits:
      `retrieval/config/ask_your_docs_models.py` (:7 extra name — **edited**, Step 3; :36
      product prose, no edit; :71 YAML-key docstring, no edit),
      `retrieval/config/app_config.py:26,:176` (the ratified stay-put import + env-prefix
      field, §Decided 6 — no edit),
      `defaults/default_config.yaml` (:207 product prose, no edit; :330 extra name —
      **edited**, Step 3; :337 the `ask_your_docs:` key, no edit),
      `retrieval/config/models.py:678` (product prose, no edit),
      `retrieval/prompts/_loader.py:27` (product prose, no edit),
      `__main__.py:998` (product prose, no edit).
      Any other hit = stale inventory; halt and re-survey.
- [x] Check for concurrent open PRs and branches touching `ask_your_docs`,
      `pyproject.toml`, or `uv.lock` (`gh pr list` + `git branch -a`) — standing rule:
      survey before touching shared files. Known at plan time:
      `fix/ask-your-docs-file-watcher-default` (trap T12).
- [x] Build/verify the per-worktree venv (trap T1/T2) BEFORE trusting any test run.

### Step 1 — `git mv` the three trees + create package inits

- [x] `mkdir python/pydocs_mcp/harness tests/harness examples/harness`
- [x] `git mv python/pydocs_mcp/ask_your_docs python/pydocs_mcp/harness/ask_your_docs`
- [x] `git mv tests/ask_your_docs tests/harness/ask_your_docs`
- [x] `git mv examples/ask_your_docs_agent examples/harness/ask_your_docs_agent`
- [x] Create `python/pydocs_mcp/harness/__init__.py` (one-line docstring; `pydocs_mcp` is a
      regular package, not a namespace package — every subpackage has an `__init__.py`).
- [x] Create `tests/harness/__init__.py` (`tests/__init__.py` exists; without the new init,
      `tests.harness.ask_your_docs._fixture` will not import).
- [x] `git status` — expect 53 renames (R) + 2 new files (54 renames if T12's
      file-watcher branch landed first); **stage individually, never
      `git add -A`** (trap T5).

### Step 2 — Rewrite package-internal self-references (20 files)

All `from pydocs_mcp.ask_your_docs.…` → `from pydocs_mcp.harness.ask_your_docs.…`, plus
docstrings/log names. Files with hit counts (paths post-move under
`python/pydocs_mcp/harness/ask_your_docs/`):

- [x] `agent.py` (10) · `app.py` (8) · `cli.py` (6) · `architectures/base.py` (4) ·
      `pages/2_Graph.py` (4)
- [x] `architectures/__init__.py` (3) · `architectures/inline.py` (3) ·
      `architectures/text_react.py` (3) · `attachments.py` (3) · `graph_service.py` (3) ·
      `multimodal.py` (3)
- [x] `__main__.py` (2 — incl. the `python -m pydocs_mcp.ask_your_docs` docstring →
      `python -m pydocs_mcp.harness.ask_your_docs`) · `architectures/auto.py` (2) ·
      `architectures/vision_subagent.py` (2) · `catalog.py` (2) · `prompts/__init__.py` (2) ·
      `reinspect.py` (2) · `session_start_injection.py` (2)
- [x] `__init__.py` (1) · `theme.py` (1)
- [x] **CRITICAL (trap T3):** `prompts/__init__.py:33` `_PACKAGE =
      "pydocs_mcp.ask_your_docs.prompts"` → `"pydocs_mcp.harness.ask_your_docs.prompts"`.
      This is a string fed to `importlib.resources.files()` — no import/lint/mypy error if
      missed; fails only at first prompt render.
- [x] `cli.py:1` module docstring `` ``ask-your-docs`` — launch the Streamlit chat UI. ``
      → new script name. **User-visible, not cosmetic**: `_build_parser` passes
      `description=__doc__` at `:51`, so a miss keeps the old name in `--help`, and
      `tests/test_doc_conformance.py:212/:251` help-validates that exact parser.
- [x] `cli.py:48` docstring naming both the ``ask-your-docs`` command and the
      ``[ask-your-docs]`` extra → new names.
- [x] `cli.py:51` `prog="ask-your-docs"` → new script name; `cli.py:38–39` missing-extra
      message `pip install 'pydocs-mcp[ask-your-docs]'` → new extra name.
- [x] **Dash-form identifier lines inside the moved tree** (the rename-scope rule decides
      each; per-file counts above already include these lines): edit `__init__.py:5` and
      `session_start_injection.py:4` (`[ask-your-docs]` extra brackets),
      `architectures/text_react.py:27` (`[ask-your-docs]`-extra WHY-comment),
      `app.py:3` (names the command AND `ask_your_docs.cli` — both change),
      `__main__.py:1` (`python -m pydocs_mcp.ask_your_docs` + command name — both change).
      `multimodal.py:20` `logging.getLogger("pydocs-mcp.ask-your-docs")` follows §Open 3.
      Leave alone (product prose): `theme.py:1`, `multimodal.py:1`, `attachments.py:1`,
      `app.py:1`, `prompts/__init__.py:1`, `session_start_injection.py:1`,
      `architectures/__init__.py:1`.
- [x] Zero-hit files move untouched: `bundle.py`, `model.py`, `prompts/inline/*.j2`,
      `prompts/shared/*.j2` (5), the `pages/` dir (no `__init__.py` — keep it adjacent to
      `app.py`, Streamlit discovers it by filesystem adjacency; trap T7).

### Step 3 — `pyproject.toml` (key-by-key; current → new)

- [x] `:138` extra name: `ask-your-docs = […]` → `harness-ask-your-docs = […]`
      (dependency list unchanged). Update the comment at `:134–135`
      (`pydocs_mcp/ask_your_docs/` path + command name).
- [x] `:181` script: `ask-your-docs = "pydocs_mcp.ask_your_docs.cli:main"` →
      `harness-ask-your-docs = "pydocs_mcp.harness.ask_your_docs.cli:main"`. Update the
      comment at `:180` (extra name).
- [x] `:208` maturin include: `"python/pydocs_mcp/ask_your_docs/prompts/*.j2"` →
      `"python/pydocs_mcp/harness/ask_your_docs/prompts/**/*.j2"` — **also fixes the
      latent glob bug**: the `.j2` files live in `prompts/shared/` and `prompts/inline/`,
      so the current flat glob matches nothing and shipped wheels omit the agent prompts
      (trap T4; nothing tests glob resolution — `tests/test_repository_hygiene.py:68–82`
      only checks `py.typed`).
- [x] `:232` coverage omit: `omit = ["*/pydocs_mcp/ask_your_docs/*"]` →
      `["*/pydocs_mcp/harness/ask_your_docs/*"]` (do not rely on `*` crossing `/`;
      treat as must-update — a miss collapses the 90% coverage gate on the core matrix
      where the extra is not installed). Update comments `:224` (extra name) and `:230`
      (`tests/ask_your_docs/test_graph_service.py` path).
- [x] `:290` mypy exclude: `['^python/pydocs_mcp/ask_your_docs/']` →
      `['^python/pydocs_mcp/harness/ask_your_docs/']` (anchored regex; a miss makes mypy
      discover the langgraph/streamlit stack and fail the typecheck job). Update comment
      `:286`.
- [x] The new parent `python/pydocs_mcp/harness/__init__.py` (Step 1) sits **inside both
      gates on purpose**: it is outside the coverage omit and the mypy exclude, so it must
      be mypy-clean (a one-line docstring is) and it registers as covered transitively via
      `tests/harness/ask_your_docs/test_graph_service.py`'s import of
      `pydocs_mcp.harness.ask_your_docs.graph_service`. Do not widen either pattern to
      `*/pydocs_mcp/harness/*`.
- [x] Two extra-name strings OUTSIDE `pyproject.toml` ship in the wheel and change with
      the extra rename (files stay put): `defaults/default_config.yaml:330`
      (`([ask-your-docs] extra)` comment) and
      `retrieval/config/ask_your_docs_models.py:7` (``` ``[ask-your-docs]`` extra ```
      docstring line).
- [x] Confirmed no-edits: no all-extras union, no `[project.entry-points]`, no ruff
      per-file-ignores, no vulture whitelist, no complexipy-snapshot entries, pytest
      `testpaths = ["tests"]` unaffected.

### Step 4 — Tests outside the moved directory

- [x] `tests/test_doc_conformance.py:41` — `_DOC_FILES` path
      `"examples/ask_your_docs_agent/README.md"` → `"examples/harness/ask_your_docs_agent/README.md"`.
- [x] `tests/test_doc_conformance.py:46` — `_ENTRY_POINTS = ("pydocs-mcp", "ask-your-docs")`
      → new script name. **A miss here is a false-green**: the harvester silently stops
      validating every renamed-command line in docs (trap T6).
- [x] `tests/test_doc_conformance.py:212` — `from pydocs_mcp.ask_your_docs.cli import
      _build_parser as _ayd_parser` → new module path.
- [x] `tests/test_doc_conformance.py:251` — `walk(_parser_for("ask-your-docs"))` → new name.
- [x] `tests/test_structured_envelope.py:40` — hand-written fixture line
      `"- \`pydocs-mcp\` (script) - \`ask-your-docs\` (script) \n"` → new script name.
      The real value flows from `[project.scripts]` via `deps.parse_project_scripts` →
      `storage/factories.py:286` → `application/overview_service.py` →
      `application/formatting.py:984`, so `get_overview` output really changes; update the
      fixture in lockstep.
- [x] `tests/application/test_pointer_target_validity.py:47` —
      `("ask-your-docs", False),  # console-script name, dash` → new name.
- [x] `tests/test_config_ask_your_docs.py` — stays put (§Open 2); update only prose
      mentions of the extra name. Its imports target `retrieval.config.ask_your_docs_models`
      and the `ask_your_docs:` YAML block, both unchanged.

### Step 4b — Moved test files (import rewrites inside `tests/harness/ask_your_docs/`)

All `pydocs_mcp.ask_your_docs.…` → `pydocs_mcp.harness.ask_your_docs.…`:

- [x] `test_agent_registry.py` (:9,:13,:14,:25,:61,:62,:77) ·
      `test_app_attachment.py` (:6,:14) · `test_app_image_attachment.py` (:15) ·
      `test_architectures.py` (:13–:16) · `test_attachment.py` (:3,:9,:16)
- [x] `test_cli_parser.py` (:1,:2,:11, **:30** — subprocess string
      `"import pydocs_mcp.ask_your_docs.cli\n"`, invisible to import-rename tooling)
- [x] `test_graph_service.py` (:13–:17; **:27–:28** subprocess strings; :17
      `from tests.ask_your_docs._fixture import make_bundle` →
      `tests.harness.ask_your_docs._fixture`)
- [x] `test_image_attachment.py` (23 hits) · `test_multimodal_detection.py` (:12,:17,:91) ·
      `test_prompt_seam.py` (8 hits) · `test_prompt_seed_parity.py` (:22) ·
      `test_prompts_package.py` (:1,:14,:56,:69,:78,:79) · `test_reinspect_tool.py` (11 hits) ·
      `test_session_start_injection.py` (:4,:17) · `test_theme.py` (:12)
- [x] `_fixture.py`, `_agent_fakes.py` — zero hits, move only.

### Step 5 — Benchmarks (in-repo eval harness)

- [x] `benchmarks/src/pydocs_eval/optimize/ask_binding.py:32,113,123` — three
      `from pydocs_mcp.ask_your_docs.agent import …` → new path.
- [x] `benchmarks/src/pydocs_eval/optimize/artifacts/ask_prompt.py:30` (import), `:54`
      (`_PRODUCT_PROMPTS_DIR = "python/pydocs_mcp/ask_your_docs/prompts/shared/"` — rendered
      into `landing_note()`), `:110` (`"tests/ask_your_docs/test_prompts_package.py."` path
      string) → new paths.
- [x] `benchmarks/tests/optimize/test_ask_prompt_artifact.py:8` and
      `benchmarks/tests/optimize/test_ask_binding.py:59`
      (`pytest.importorskip("pydocs_mcp.ask_your_docs.architectures")`) → new paths.
- [x] `benchmarks/pyproject.toml:99` — `ask = ["pydocs-mcp[ask-your-docs]>=0.5.2"]` →
      **exactly** `ask = ["pydocs-mcp[harness-ask-your-docs]>=0.6.0"]` (with the owner-
      confirmed extra name from §Open 1). The floor is **0.6.0**: the rename ships in the
      already-open `## [0.6.0] — Unreleased` CHANGELOG section (`CHANGELOG.md:8`); the
      current floor 0.5.2 names a release that will never exist (`pyproject.toml:7` is
      `0.5.1` and the next release is 0.6.0). **No-edit confirmation:** `pyproject.toml:7`
      stays `version = "0.5.1"` in this PR — the 0.6.0 bump is a release-time action.
- [x] Rewrite the PUBLISH GATE comment at `benchmarks/pyproject.toml:92–98` to name
      **0.6.0** instead of 0.5.2 (same posture: eval 0.2.0 must not publish before the
      product release carrying the seam AND this rename exists — the `[ask]` extra stays
      unresolvable until then, which is expected, not a regression; trap T9).
- [x] The `all` union deliberately excludes `[ask]` (WHY-comment at :100–105) — no edit to the
      union itself, but **append a one-line forward note** to its WHY-comment: when
      `[ask]` is re-added to `all` after the product release exists, it must use the
      harness-scoped extra name.
- [x] `benchmarks/AGENT_TRACK.md:338,340` — path + config-block prose (the `ask_your_docs:`
      YAML name at :340 stays; only the directory path at :338 changes).
- [x] `benchmarks/src/pydocs_eval/optimize/run_config.py:76` — WHY-comment naming the CLI →
      new script name.
- [x] No-edit confirmations: `ask_architecture.py:97` references the `ask_your_docs` YAML
      block (unchanged); `benchmarks/configs/` and `benchmarks/data/` have zero hits.
- [x] `scripts/smoke_check_benchmark_imports.py` (runs in CI at `ci.yml:157`) imports every
      `pydocs_mcp.*` module named in benchmarks source — it is the drift gate that catches
      a half-done rename; only its `:69` comment needs an edit (the `[ask-your-docs]`
      extra name).

### Step 6 — Docs and root files

- [x] `CLAUDE.md:45` (extra name), `:46` (fenced bash: command name AND
      `examples/harness/ask_your_docs_agent/configs/serve_cpu_openvino.yaml` — this line is
      doc-conformance-harvested, both must change together), `:126` (tree entry →
      `harness/ask_your_docs/`), `:148` (extra name + path).
- [x] `README.md:295` (extra name), `:298–299` (fenced bash, harvested:
      `pip install 'pydocs-mcp[…]'` + command), `:306`, `:637` (example-dir links).
- [x] `DOCUMENTATION.md:955` (tree entry → `harness/ask_your_docs/`). `:820` ("into the
      ask-your-docs agent prompt") is product-name prose — **no edit** (rename-scope rule).
- [x] `SPEC.md:525` (extra-name list).
- [x] `.env.example:22,24` (extra name + `pydocs_mcp/harness/ask_your_docs/cli.py` path).
- [x] `docs/description-authoring.md:265` ("into the ask-your-docs agent prompt") is
      product-name prose — **no edit** (rename-scope rule).
- [x] `examples/harness/ask_your_docs_agent/README.md` — 8 hit lines
      (:6,:36,:63,:71,:73,:82,:98,:101 — four are fenced bash) → new extra/command/paths.
      `:108` names the `ask_your_docs:` YAML block — **unchanged** (§Decided 6). Same for
      `configs/index_gpu.yaml:21` and `configs/serve_cpu_openvino.yaml:24` (the
      commented-out `# ask_your_docs:` key). `configs/index_gpu.yaml:19` and
      `configs/serve_cpu_openvino.yaml:22` ("the ask-your-docs UI reads this same file")
      are product prose — no edit (rename-scope rule).
- [x] `.github/workflows/ci.yml:102–106` — comment block only (extra name +
      `tests/harness/ask_your_docs/…` path + package name). No workflow step installs or
      path-filters the extra; `docs.yml` does not trigger on `examples/**`.
- [x] `CHANGELOG.md` — append the breaking-rename bullet **under the EXISTING
      `## [0.6.0] — Unreleased` section at `:8`** (Keep-a-Changelog `### Changed` with
      explicit breaking wording: old → new script and extra names, module path, no shim).
      Do NOT create a new version heading (no `[0.5.2]`, no `[0.6.1]`), do NOT touch
      `pyproject.toml:7` (the version bump is release-time), and do NOT rewrite historical
      entries (:69,:182–184,:244,…). The PR body notes the rename ships in 0.6.0 — the
      same floor the benchmarks `[ask]` extra names (Step 5).
- [x] Leave alone: `docs/adr/**` (15 lines across 5 files — ADRs 0002, 0005, 0007, 0008,
      0016; dated historical records whose file:line pointers may go stale, acceptable),
      `docs/superpowers/**` (333 hits — internal
      planning record), the two frozen research artifacts
      `docs/superpowers/research/2026-07-11-*.json{,l}` (116 + 18 hits), `INSTALL.md` /
      `CONTRIBUTING.md` / `EXTENSIONS.md` / `Makefile` / `.pre-commit-config.yaml` /
      `documentation/` (Sphinx tree) / `docs/tool-contracts.md` — all zero hits.

### Step 7 — Lockfile

- [ ] Relock with `~/.local/bin/uv lock` **only** (anaconda uv churns markers; trap T10).
      This regenerates the 7 derived `uv.lock` hits (:3391,:3460–3462,:3474–3475,:3488 —
      the generated `provides-extras` union). Never hand-edit `uv.lock`.
- [ ] `uv lock --check` green afterward.

## Verification gates (commands + expected results)

Run from the per-worktree venv (trap T1/T2 — verify `python -c "import pydocs_mcp;
print(pydocs_mcp.__file__)"` resolves to THIS worktree first).

- [ ] `ruff check python/ tests/ benchmarks/ scripts/` and
      `ruff format --check python/ tests/ benchmarks/ scripts/` — clean. The trailing
      `scripts/` matches CI (`ci.yml:111`/`:114`) and matters here: Step 5 edits
      `scripts/smoke_check_benchmark_imports.py`. (CLAUDE.md's §Tests & Lint block omits
      `scripts/` — it is stale on this point; follow ci.yml.)
- [ ] `mypy python/pydocs_mcp` — clean (proves the exclude regex was updated; a failure
      naming langgraph/streamlit stubs means Step 3 mypy-exclude was missed).
- [ ] `complexipy python/pydocs_mcp --max-complexity-allowed 15` — clean; then restore
      `complexipy-snapshot.json` from HEAD before staging (trap T5).
- [ ] `vulture python/pydocs_mcp --min-confidence 80` — clean.
- [ ] `pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90` —
      all pass (~3708 product tests), coverage ≥ 90% (proves the coverage-omit glob was
      updated).
- [ ] `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q` — all pass (root venv
      currently LACKS `unidiff` + `gepa`; install into the worktree venv first).
- [ ] `python scripts/smoke_check_benchmark_imports.py` — exits 0 (the rename drift gate).
- [ ] `uv lock --check` — clean.
- [ ] pip-audit: `uv export --frozen --no-emit-project --no-group docs --format
      requirements-txt > requirements-audit.txt` then audit. Requirement-mode pip-audit
      SIGABRTs under the local sandbox — use `<worktree>/.venv/bin/pip-audit --strict
      --local` on THIS worktree's frozen venv as the local equivalent (trap T11; run the
      T2 `import pydocs_mcp; print(pydocs_mcp.__file__)` assertion first); CI runs the
      canonical form.
- [ ] Sphinx docs build (CI gate `docs.yml` — triggers on `python/**`, `*.md`,
      `pyproject.toml`, `uv.lock`, all touched here):
      `uv run sphinx-build -W --keep-going -b html documentation documentation/_build/html`
      — clean. `documentation/` itself needs **no direct edit** (zero `ask_your_docs`
      hits); the affected prose arrives through MyST includes of
      README/DOCUMENTATION/SPEC/CHANGELOG (e.g. `documentation/getting-started/quickstart.md:3`
      includes README's `## Quick start` span, which contains the `:295–306` edits).
- [ ] Rust trio (untouched by this PR, but part of the gate set): `cargo fmt --check`,
      `cargo clippy -- -D warnings`, `cargo test` — clean. `tests/test_parity.py` runs
      only after `maturin develop --release` (ci.yml's rust job, "Build with maturin +
      parity tests") and is out of scope for this PR — the `--ignore=tests/test_parity.py`
      cov-variant above is the intended local product-suite gate, not an oversight.
- [ ] **Functional smoke** — `pip install -e ".[harness-ask-your-docs]"` (final confirmed
      extra name) into the worktree venv, then: the new console script is on PATH, `--help`
      shows the new `prog`, and it launches to the Streamlit boot (or the sanctioned
      missing-dep `SystemExit` naming the NEW extra when deps are absent).
- [ ] **Wheel spot-check** for trap T4: `maturin build` (or `python -m build`), then
      `unzip -l dist/*.whl | grep '\.j2'` — expect all 6 harness prompt templates
      (`prompts/shared/*.j2` ×5, `prompts/inline/system_suffix_v1.j2`) plus
      `retrieval/prompts/*.j2`.
- [ ] `git log --follow python/pydocs_mcp/harness/ask_your_docs/agent.py` shows pre-move
      history (proves `git mv` rename detection held).

## Definition of done

- [ ] All three trees moved via `git mv`; both new `__init__.py` files present.
- [ ] **Completion grep (a) — module/path forms, must be EMPTY** (covers dotted imports,
      slash-form path strings, and the moved tests/examples trees; all file types, entire
      repo, minus the sanctioned dated records):
      `git grep -nE 'pydocs_mcp[./]ask_your_docs|tests/ask_your_docs|examples/ask_your_docs' -- . ':!docs/adr' ':!docs/superpowers' ':!CHANGELOG.md' ':!uv.lock'`
      → empty. (`uv.lock` is regenerated in Step 7, not hand-checked; the new
      `harness/ask_your_docs` paths do not match these patterns.)
- [ ] **Completion grep (b) — dash-form script/extra name, only sanctioned survivors**:
      `git grep -n 'ask-your-docs' -- pyproject.toml benchmarks/pyproject.toml tests/ python/ scripts/ examples/ .github/ CLAUDE.md README.md DOCUMENTATION.md SPEC.md .env.example docs/description-authoring.md`
      → only the product-name-prose lines the rename-scope rule leaves alone
      (Step 0's no-edit set, the in-tree prose docstrings listed in Step 2,
      `DOCUMENTATION.md:820`, `docs/description-authoring.md:265`, the two example config
      comments `configs/index_gpu.yaml:19` / `configs/serve_cpu_openvino.yaml:22`
      ("the ask-your-docs UI reads this same file"), and — if §Open 3 resolves to
      "keep" — `multimodal.py:20`). Any command, extra bracket, or path hit
      is a failure. (This form has no test gate behind it: `test_doc_conformance.py`
      validates only fenced commands whose first token is an entry point, never
      `pip install 'pydocs-mcp[…]'` lines.)
- [ ] Old script/extra names absent from `pyproject.toml`, both test oracles, and every
      harvested doc line; `ask_your_docs:` YAML key and env prefix untouched.
- [ ] `benchmarks/pyproject.toml` `[ask]` floor names **0.6.0** — the product release
      (per CHANGELOG's open `[0.6.0] — Unreleased` section) that carries this rename —
      and `pyproject.toml:7` still reads `0.5.1`.
- [ ] Full verification-gate list above green, including the ~3708-test product suite and
      the benchmarks suite.
- [ ] **Harness-scoped script installs and launches** (functional smoke above).
- [ ] Wheel contains the harness `.j2` templates (glob fix verified).
- [ ] CHANGELOG entry added; PR open with the breaking-change statement; owner sign-off
      obtained BEFORE merge.

## Traps (execution gotchas — read before starting)

- **T1 — PATH python is 3.8.** Bare `python`/`pytest` on PATH is Python 3.8. Use the repo
  `.venv` or build a fresh per-worktree venv. Benchmarks tests need
  `PYTHONPATH=benchmarks/src`, and the root `.venv` currently lacks `unidiff` + `gepa`.
- **T2 — Editable-install shadowing.** Running tests from a WORKTREE with the main
  checkout's venv can import the MAIN checkout's package via the editable finder. Build a
  per-worktree venv (aarch64 via `~/.local/bin/uv`) or verify
  `python -c "import pydocs_mcp; print(pydocs_mcp.__file__)"` before trusting any result.
- **T3 — `importlib.resources` package string** (`prompts/__init__.py:33`). Highest-risk
  silent break: a string, not an import; fails only at first prompt render.
  `tests/…/test_prompts_package.py` covers it and does run (jinja2 is a core dep).
- **T4 — maturin include glob is already latent-broken.** `prompts/*.j2` matches nothing
  (templates live in `shared/`/`inline/`). Fix to `prompts/**/*.j2` in the same PR; a
  source checkout passes regardless, so only the wheel spot-check proves it.
- **T5 — Never `git add -A`.** Untracked scratch exists in these trees, and local
  complexipy runs rewrite `complexipy-snapshot.json` in place — restore it from HEAD
  before staging.
- **T6 — Doc-conformance false-green.** If `_ENTRY_POINTS` isn't updated, the harness
  silently stops validating every renamed-command doc line instead of failing.
- **T7 — Streamlit `pages/` adjacency.** `pages/2_Graph.py` is discovered by filesystem
  adjacency to `app.py` (no `__init__.py`, no maturin entry — swept by
  `python-source = "python"`). Move the whole tree together; a split move loses the graph
  page with no error.
- **T8 — Namespace ambiguity.** "harness" elsewhere in this repo means the *eval* harness
  (`benchmarks/`, `pydocs_eval`). The PR body should state explicitly that
  `pydocs_mcp.harness` is the product-side agent-harness namespace, distinct from
  `pydocs-mcp-eval`.
- **T9 — Unreleased eval 0.2.0 is the only exposure — no published package breaks.**
  No published eval version references the harness package: the latest release
  (`eval-v0.1.1`) predates both the `[ask]` extra and `optimize/ask_binding.py` (verified
  against the tag — `git show eval-v0.1.1:benchmarks/pyproject.toml` has no `ask` extra).
  The released package therefore **cannot** break against this rename; no emergency eval
  release is needed. The exposure is confined to the UNRELEASED eval 0.2.0 in-repo, whose
  `[ask]` extra must ship with the renamed extra and the **0.6.0** floor (Step 5) — after
  this PR, `pip install "pydocs-mcp-eval[ask]"` from source stays unresolvable until
  product 0.6.0 publishes, which is the same posture as the pre-existing PUBLISH GATE
  (expected, not a regression). Standing rule holds: eval 0.2.0 must not publish before
  the product release carrying this rename exists; no tag or publish without the owner's
  explicit word for THAT release.
- **T10 — Relock only with `~/.local/bin/uv`** (anaconda's x86_64 uv churns markers and
  resolves wrong-platform wheels).
- **T11 — pip-audit sandbox SIGABRT.** Requirement-mode pip-audit aborts under the local
  sandbox; use `<worktree>/.venv/bin/pip-audit --strict --local` on THIS worktree's frozen
  venv locally — naming the worktree venv explicitly matters, because a bare `.venv/`
  may not exist in the worktree and falling back to the MAIN checkout's venv audits a
  different installed tree (exactly the shadowing T2 guards against). The T2 assertion
  (`python -c "import pydocs_mcp; print(pydocs_mcp.__file__)"`) is a precondition of the
  pip-audit step, not just of the pytest steps.
- **T12 — Ordering rule vs the file-watcher branch.** Branch
  `fix/ask-your-docs-file-watcher-default` (a5c748a, based on current main 849ac7f)
  modifies `python/pydocs_mcp/ask_your_docs/cli.py` (+13) AND adds
  `tests/ask_your_docs/test_cli_command.py` (+53). If it merges AFTER the reorg, git
  replays the added test file into the OLD `tests/ask_your_docs/` path — rename detection
  does not relocate newly added files — silently orphaning it outside `tests/harness/`
  and outside the coverage/mypy assumptions; its `cli.py` edit also conflicts with
  Step 2's rewrite of that file. **Rule: land that branch FIRST** — then the reorg's
  `git mv` carries the new test with the tree, Step 0's expected counts become 19 test
  files / Step 1's rename count becomes 54, and the executor re-runs the Step 0 inventory
  for `cli.py` (its hit count may shift). If the owner insists on the reverse order,
  that branch must be rebased onto the reorg and the file re-homed to
  `tests/harness/ask_your_docs/test_cli_command.py` before it merges.
- **T13 — Concurrent-PR check.** Re-survey open/just-merged PRs and re-fetch before
  branching; stale merge refs on shared files (`pyproject.toml`, `uv.lock`) have bitten
  before. Survey list at plan time: `fix/ask-your-docs-file-watcher-default` (T12).

## PR checklist

- [ ] ONE PR. Title and body state the breaking change plainly: script
  `ask-your-docs` → `<confirmed name>`, extra `[ask-your-docs]` → `[<confirmed name>]`,
  module path `pydocs_mcp.ask_your_docs` → `pydocs_mcp.harness.ask_your_docs`, **no compat
  shim, no deprecation period**; `ask_your_docs:` YAML key and env prefix unchanged.
- [ ] Body states: zero MCP surface change — nine tools, schemas, and envelope untouched
  (`docs/tool-contracts.md` frozen contract).
- [ ] Body notes the two drive-by fixes: maturin `.j2` glob (T4) and any comment-path
  updates, so reviewers don't mistake them for scope creep.
- [ ] Body flags the downstream publish implication (T9) for the next eval release.
- [ ] Authorship: **msobroza only** — no `Co-Authored-By` trailers, no `--author`, no
  commit signing on the user's behalf, git config untouched.
- [ ] Before push: verify branch ref == HEAD; after push: `git ls-remote` == local HEAD
  (detached-HEAD subagent trap).
- [ ] **Do NOT merge without the owner's explicit word.** Open the PR, post the summary,
  stop.
