# cli-mcp-docs-audit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement spec `docs/superpowers/specs/2026-07-11-cli-mcp-docs-audit-spec.md`: the permanent doc-conformance harness (`tests/test_doc_conformance.py`, groups G2–G5) plus the 11-defect fix list (§3.8), landing as one PR in three commits (§6).

**Architecture:** One ~450-line stdlib-only pytest file (Windows-safe, no subprocess, no markdown dep) + targeted fixes. All citations below are **re-verified against a815e8a** (post #184/#185) by a 4-agent scout (55 citations, 11 drifted-in-position, 0 claims invalidated). Watch-first landed ⇒ this spec's §1.3 watch-first branch applies: D10's line edit is moot (only the quoting lint ships), and D3's doc reconciliation uses "either switch enables" phrasing across FOUR surfaces (see D3 below). PR #186 (query-embedding-cache) is open and touches `retrieval/` config — the harness introspects `AppConfig`/registries at runtime, so it is order-safe against it.

**Resolved open questions** (spec §7, per the standing adopt-recommendations rule): Q1 verified — pointer grammar `[[next:<action>:<target>]]` (formatting.py:88-90) corrupts only on `:`/`]`; `/` is safe, and no `get_why` target is interpolated into a pointer token (decision_service emits only `pointer_token("overview","")`); a grammar-level `/` test ships with D1. Q2 doc-fix-now (D2), behavior deferred. Q3/Q4/Q5/Q8 out of scope (reported to owner). Q6 refactor ships (moved to commit 1 — the harness itself needs `_build_parser`; deviation from §6's commit split, spec-sanctioned by §3.4's own reasoning). Q7 D5/D7 in this PR. Q9 answered (watch-first).

---

## Verified anchor table (from the scout; use these, not the spec's)

| Defect | Current anchors |
|---|---|
| D1 | `mcp_inputs.py:36-38` (`_TARGET_RE`), `:389` (WhyInput), `:396-408` (validator); help `__main__.py:292-306`; `DOCUMENTATION.md:304-305`; `decision_service.py:74-89` (path branch 85-86); pointer grammar `formatting.py:88-90`, emitter `:117-121`, strip `:179-181` |
| D2 | `multi_project_search.py:121-145` (no limit read), `:180-186` routing, `:198,:200` union reads; `search_query.py:38-50`; help `__main__.py:249-256`; DOC sites `:258` (uncited, scout-found), `:271-272`, `:317`, `:685-687`, `:957` |
| D3 | trigger `__main__.py:890`; `models.py:518` (field), `:504-507` (docstring ∩ D5); `DOCUMENTATION.md:413-415` + `:421` ("overrides" claims); `default_config.yaml:145-153` (post-#185 "only way" wording — becomes false after wiring); `app_config.py:136` comment (scout-found) |
| D4 | `ci.yml:111` (no benchmarks/), `:114` (has it); `CLAUDE.md:72` + `:75` |
| D5 | `models.py:504-507`; `mcp_inputs.py:172`, `:220` |
| D6 | `EXTENSIONS.md:222` ("planned"), `:175` ([SHIPPED]); registered name `weighted_score_interpolation` (`weighted_score_interpolation.py:42`) |
| D7 | `base.py:67-75` (bad ctors at :70/:73); real fields: ChunkFetcherStep(provider REQUIRED positional, filter_adapter REQUIRED kw-only, limit/retriever_name/allowed_fields/name kw-only defaults); TokenBudgetStep(formatter, budget REQUIRED positional; name kw-only) |
| D8 | `documentation/index.md:20-21`; contrast `README.md:75-76` |
| D9 | `README.md:132-134`, `DOCUMENTATION.md:104-106` (omit watch); correct forms at `DOCUMENTATION.md:231`, `INSTALL.md:97` |
| D10 | GONE (deleted by #185); surviving extras-install lines ALL quoted (single: README:195,:304,:325,:420, DOCUMENTATION:85,:490-491; double: examples/ask_your_docs_agent/README.md:68,:70,:79) — lint must accept BOTH quote styles |
| D11 | JSON blocks `DOCUMENTATION.md:926-932,:936-942,:946-952` — all parse today; section `:915` |

Harness prereqs: `_build_parser` `__main__.py:52`; 10 subcommands (serve index watch search overview symbol context refs why lookup); ask-your-docs parser inline in `main()` `cli.py:41-54` (`_require_extra` :30-38, port 8501 literal :48); TOOL_DOCS 6 keys, "up to 20" at `tool_docs.py:74,:94`; input models at `mcp_inputs.py:171/283/304/326/354/389`, `max_length=20` at `:329/:393`; `server.py:252-267` register helper, handlers `:269-336`; AppConfig 17 roots, `extra="ignore"` top-level (`app_config.py:183-186`), defaults `search.output.default_limit=10` / `reference_graph.output.default_limit=50`; step_registry 21 names (lives at `retrieval/serialization.py:108` — import `pydocs_mcp.retrieval`), stage_registry 14; 21 MyST includes all-green, exactly 6 use `end-before: "---"`; tool table `DOCUMENTATION.md:314-321`; yaml fences: README 3 / DOCUMENTATION 3 / SPEC 1, EXTENSIONS 0 (spec's example drifted — harmless); json fences also in SPEC.md (4) — group 5 scope decided at implementation (parse-check them; if any is deliberately partial, scope to DOCUMENTATION.md:915-952 as the spec wrote).

Branch: `fix/cli-mcp-docs-audit` off a815e8a. No Co-Authored-By. Three commits.

---

### Commit 1 — harness + the doc fixes it forces + ask-your-docs parser refactor

**Files:** Create `tests/test_doc_conformance.py`. Modify `python/pydocs_mcp/ask_your_docs/cli.py`, `README.md`, `DOCUMENTATION.md`, `EXTENSIONS.md`, `documentation/index.md`, `CLAUDE.md:72`.

- [ ] **Step 1 (refactor first — harness prerequisite):** `ask_your_docs/cli.py`: add `_DEFAULT_PORT = 8501` module constant; extract the parser build (current `main()` lines 43-53) into module-level `_build_parser() -> argparse.ArgumentParser` (same prog/description, same options, `--port` default `_DEFAULT_PORT`, positional dest `streamlit_args` unchanged); `main()` becomes `_require_extra()` then `parser = _build_parser()`. Unit test (AC5) in `tests/ask_your_docs/`: `_build_parser()` callable core-only; parses `["--workspace", "w", "--config", "c"]`; `main()` behavior unchanged (existing tests stay green).
- [ ] **Step 2 (harness):** write `tests/test_doc_conformance.py` — module structure:
  - §3.2 dataclasses (`FencedBlock`, `DocCommand`, `YamlKeyRef`), `_DOC_FILES` (§3.3 seven files), `_iter_fenced_blocks` state machine, `<!-- doc-conformance: skip -->` escape hatch honored by all harvesters.
  - **Group 1:** command harvest (join `\` continuations; strip `$ ` prompts, full-line and trailing ` #` comments outside quotes; split on shell operators; keep `pydocs-mcp`/`ask-your-docs`-first segments; expand intra-token `|` alternation; shlex posix). `_parse_or_raise` patches `argparse.ArgumentParser.error` at class level (spec §3.4 code). Parametrized per-invocation test with `file:line` ids. Flag-inventory test (prose backticked `--flag` tokens ⊆ option strings collected recursively from both parsers). Extras-quoting lint: every `pip install …[extra]` line in the corpus carries the extra inside `'…'` or `"…"` (both styles legal — D10 verified inventory).
  - **Group 2:** key parity (TOOL_DOCS == input-model map == `_register(..., "name")` literals ast/regex-harvested from `server.py`, == the constitutional six); Examples-kwargs via `ast.parse`; handler↔model parity via a recording fake `mcp` object passed to `_register_tools` (signature-only, no calls); numeric claims single-sourced (read `max_length` from `ContextInput`/`WhyInput` metadata, defaults from `AppConfig()` — no hardcoded 20/10/50); tool-table row check over `DOCUMENTATION.md:314-321` (+ D2 caveat-presence assertion on the `limit` row).
  - **Group 3:** `_valid_dotted_paths(AppConfig)` recursive flattener (dict-typed fields terminate; Unions unioned); fenced-yaml validation (blueprint blocks with top-level `steps` → step/stage registry names + `dataclasses.fields` param check where the registry exposes the class; other blocks flatten + validate; unknown top-level key = failure, covering the `extra="ignore"` blind spot); prose dotted-path harvest gated on first segment ∈ the 17 AppConfig roots.
  - **Group 4:** MyST include integrity (all 21 directives: target exists + markers verbatim); the `---` end-before allowlist pinned to exactly the 6 known pages (a NEW `---` marker fails).
  - **Group 5:** fenced-json validity (DOCUMENTATION.md client snippets; extend to SPEC.md's 4 json fences iff they parse today — check first).
- [ ] **Step 3 (RED):** run the harness. Expected red: D2 caveat assertion (tool-table row), D6 prose assertion ("planned" present), D8 prose assertion, D9 prose assertion (README `--gpu` sentence lacks `watch`). Everything else green (scout pre-verified). Any UNEXPECTED red = a real new finding: fix doc or harvester judgment honestly, never loosen an assertion to pass.
- [ ] **Step 4 (GREEN — doc fixes):**
  - **D2** (5 sites): `DOCUMENTATION.md:271-272` comment → "# Cap results for multi-repo union searches (default 10). Single-project result count is set by the retrieval pipeline YAML, not this flag."; `:258` example gains the same short caveat comment; `:317` row description appends "`limit` caps multi-repo union results only; single-project result count comes from the pipeline YAML."; `:685-687` yaml comment → "# default: 10 — bounds the client `limit` param (multi-repo unions); single-project count = the pipeline YAML's limit step"; `:957` example gains a trailing caveat comment.
  - **D6**: `EXTENSIONS.md:222` → "…downstream `RRFFusionStep` (or `WeightedScoreInterpolationStep`, registered as `weighted_score_interpolation`) fuses…".
  - **D8**: `documentation/index.md:20-21` → "No network calls in the default configuration — network is used only if you opt into the OpenAI-compatible embedding provider, LLM decision structuring, or the LLM reasoning retrieval steps, each with your own key."
  - **D9**: README.md:132 "Add `--gpu` to `serve` / `index` / `watch` (or the benchmark runner)…"; DOCUMENTATION.md:104 "Pass `--gpu` to `serve` / `index` / `watch`…".
  - **D4 doc half**: `CLAUDE.md:72` gains "  # local gate — not run by any CI workflow".
- [ ] **Step 5:** harness fully green; jargon audit green; commit `test(docs): doc-conformance harness (G2-G5) + the doc fixes it forces`.

### Commit 2 — code fixes with their unit tests (D1, D3, D5, D7)

- [ ] **Step 1 (D1, RED first):** tests in `tests/application/test_mcp_inputs.py`: `WhyInput(query="q", targets=["a/b.py"])` and `["src/pydocs_mcp/db.py"]` validate; `["pkg.mod"]`, `["b.py"]` still validate; `["a:b"]`, `["a]]b"]`, `[""]` rejected. DecisionService reachability: `_classify_target("src/pydocs_mcp/db.py") == "path"` reached via a validated WhyInput value. Pointer-grammar `/` test: `pointer_token`-emitted token with a `/`-bearing target round-trips `_POINTER_RE` and `strip_pointers`. Run → red (targets rejected).
- [ ] **Step 2 (D1, GREEN):** in `mcp_inputs.py` add `_WHY_TARGET_RE = re.compile(r"^[A-Za-z0-9_.\-/]+$")` (admits qnames + POSIX paths; forbids `:`/`]`/whitespace — the two grammar-hostile chars, Q1-verified) with a WHY comment; `WhyInput._check_targets` validates against it; error message updated ("dotted name like 'pkg.mod.Class' or a path like 'a/b.py'"). `_TARGET_RE` untouched.
- [ ] **Step 3 (D3, RED first):** tests on `_cmd_serve` dispatch (find the existing serve-test fakes in `tests/test_cli.py` / `test_main_cli_watch*.py` and follow their pattern): overlay `serve.watch.enabled: true` + no `--watch` → watch loop invoked; flag on + key false → invoked; both off → not invoked. Run → red (key dead).
- [ ] **Step 4 (D3, GREEN + doc reconciliation):** `__main__.py:890` → `if getattr(args, "watch", False) or config.serve.watch.enabled:` (verify `config` in scope). Reconcile ALL FOUR doc surfaces to "either switch enables" (flag cannot force off): `DOCUMENTATION.md:413-415` prose, `:421` yaml comment, `defaults/default_config.yaml:145-146` ("either the CLI `--watch` flag or `enabled: true` here turns watching on — leave it false so plain `pydocs-mcp serve` is identical to today"), `app_config.py:136` comment. NOTE the default stays `false` (AC19) and `models.py:518` untouched.
- [ ] **Step 5 (D5):** rewrite `models.py:504-507` (six task-shaped tools + "either switch" wording — D3∩D5), `mcp_inputs.py:172` ("Input for the ``search_codebase`` MCP tool."), `:220` ("Internal routing input for the deprecated ``lookup`` CLI verb — the MCP surface exposes ``get_symbol`` / ``get_references`` instead."). Harness extension (in test_doc_conformance.py, added now with the fix): grep-assert "fixed 2 tools", "``search`` MCP tool", "``lookup`` MCP tool" absent under `python/pydocs_mcp/`.
- [ ] **Step 6 (D7):** fix `base.py:67-75` example to construct with real fields (`ChunkFetcherStep(provider, filter_adapter=adapter, name="fetch", limit=200)`, `TokenBudgetStep(formatter, 2000, name="budget")` — verify BM25ScorerStep/TopKFilterStep kwargs against their `dataclasses.fields` too). Harness extension: ast-parse every `XxxStep(...)` call in that docstring; keyword names ⊆ `dataclasses.fields` of the class resolved from the step registry / module namespace.
- [ ] **Step 7:** full targeted suites green; commit `fix(cli+mcp): why --target accepts paths; serve.watch.enabled wired; stale docstrings + pipeline example corrected`.

### Commit 3 — D4 CI fix

- [ ] `ci.yml:111` → `uv run ruff check python/ tests/ benchmarks/` (already green locally). Commit `ci: lint benchmarks/ with ruff check — matches Makefile and CLAUDE.md`.

### Task 4 — adversarial AC review (ultracode)

- [ ] Workflow: refuters per AC group (AC1-7 harness-CLI; AC8-11 harness-MCP; AC12-16 harness-YAML/site; AC17-22 defect fixes; AC23-25 gates/invariants + non-goals: no MCP surface change, no new tunables, no execution in harness), cross-check pass; fix confirmed findings.

### Task 5 — gates, push, PR

- [ ] Full gate set (incl. `PYTHONPATH=benchmarks/src pytest benchmarks/tests/`, cargo, pip-audit env-mode, `uv lock --check` — NO lock change expected: zero dependency edits). AC4 spot-check: run harness with `HOME` pointed at a read-only dir. Re-fetch origin (PR #186 may merge meanwhile — harness is introspection-based, but re-run the suite after any rebase). Push, verify refs, `gh pr create`; no merge without explicit go.

---

## Self-review notes

- **Spec coverage:** G1 ships as §1.1 (in-spec) + this fix list; G2-G5 = commit 1; AC1-25 mapped in Tasks 1-5. D10 line edit intentionally absent (GONE — watch-first branch); quoting lint ships. D11: JSON validity only; path verification reported to owner as open (Q3).
- **Commit-split deviation from §6:** ask-your-docs refactor moved commit 2 → commit 1 (harness needs `_build_parser`; §3.4's own text justifies it). D5/D7 harness extensions land in commit 2 with their fixes so every commit boundary is green.
- **Scout-found extras beyond the spec:** DOCUMENTATION.md:258 (uncited `--limit` example) gets D2 treatment; `app_config.py:136` override comment gets D3 treatment.
- **No placeholders; every edit carries exact text or a verified anchor + decision rule.**
