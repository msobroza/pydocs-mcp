# d2 SUMMARY

Startup is almost entirely EAGER: `server.run()` (python/pydocs_mcp/server.py:514-557) does AppConfig.load → configure_from_app_config → build_routers (which opens every bundle's SQLite to read schema-version+metadata, then builds the fastembed embedder EAGERLY — FastEmbedEmbedder.__post_init__ constructs fastembed.TextEmbedding immediately) → FastMCP(instructions=SERVER_INSTRUCTIONS) → tool registration with description=TOOL_DOCS[name] → mcp.run(stdio). The ONNX model is loaded at startup, NOT on first query — wire-measured first tools/call on a live server is 0.206s vs 0.182s subsequent (no model-load spike), while every one-shot CLI query pays ~1.25s (fresh process = fresh model load). The single-db `serve` path loads the fastembed model TWICE (once in the pre-serve indexing phase via storage/factories.py:623, once via retrieval/factories.py:89); measured time-to-ready was 5.24s cold-ish and 1.90s FS-cache-warm, with client-visible initialize at 3.14s. The injection seam is confirmed: SERVER_INSTRUCTIONS/TOOL_DOCS are imported function-locally at server.py:527 and :622 (consumed at :551/:644) and at __main__.py:60 for CLI help, and a complete working rebinding overlay already ships in benchmarks/src/pydocs_eval/optimize/_overlay_server.py (`td.SERVER_INSTRUCTIONS = ...; td.TOOL_DOCS[key] = ...` before server.run, fail-closed through ToolDocsArtifact.validate), wired into arm-B .mcp.json by optimize/fitness/paired_agent.py. Hot-reload: mcp 1.28.1 (pyproject pin; installed measurement venv has 1.27.1) defines ServerSession.send_tool_list_changed but FastMCP never calls it and advertises ToolsCapability(listChanged=False) by default — wire-verified: the live server's initialize response returned `tools: {'listChanged': False}` — so description hot-reload today requires client re-connect or product changes; client honoring (Claude Code ≥2.1.x yes-with-history-of-bugs, Claude Desktop reportedly ignores it) is web-sourced and unverified. AppConfig precedence is init-kwargs > env vars (PYDOCS_*, nested via `__`) > user YAML (explicit --config > $PYDOCS_CONFIG_PATH > ./pydocs-mcp.yaml > ~/.config/pydocs-mcp/config.yaml) > shipped defaults — note env OUTRANKS the YAML overlay; a descriptions-source-path would naturally live in ServeConfig and is env-reachable as PYDOCS_SERVE__..., but the recorded §D6 decision explicitly rejected an AppConfig.tool_docs_overlay_path field in favor of the zero-product-hook wrapper. CLI help is built at runtime per invocation from the same TOOL_DOCS module attributes (help = first line, description = full text), so a rendered-source-document design would flow to CLI help with zero further code changes.

# MEASUREMENTS

- **pydocs-mcp --help wall time** = 0.86s first run; 0.37-0.39s warm  (/usr/bin/time -p /Users/msobroza/Projects/pyctx7-mcp/.venv/bin/pydocs-mcp --help (3 runs))
- **bare import cost** = import pydocs_mcp 0.38-0.47s; import tool_docs 0.43s  (/usr/bin/time -p .venv/bin/python -c 'import pydocs_mcp' (2 runs) and -c 'from pydocs_mcp.application.tool_docs import TOOL_DOCS, SERVER_INSTRUCTIONS')
- **index cold (5-chunk scratch project)** = 2.86s  (/usr/bin/time -p pydocs-mcp index . --skip-deps --no-inspect on scratchpad measureproj (includes fastembed model load + embedding))
- **serve time-to-ready ('MCP ready' stderr line)** = 5.242s run 1; 1.903s run 2 (page-cache warm). Phase breakdown run 1: first log 1.07s, indexing-phase done 3.84s, retrieval deps built 5.05s, ready 5.24s  (scratchpad/time_serve.py spawns 'pydocs-mcp serve . --skip-deps --no-inspect -v', stamps each stderr line with elapsed-since-spawn, stops at 'MCP ready' (server.py:554))
- **client-visible initialize latency + advertised tools capability** = initialize response at 3.139s; capabilities.tools = {'listChanged': False}  (scratchpad/mcp_query_timing.py speaks raw newline-delimited JSON-RPC over stdio (initialize -> initialized -> tools/call) against pydocs-mcp serve)
- **first vs subsequent tools/call on live server** = 0.206s / 0.182s / 0.182s — no model-load spike on first query (model loads at startup)  (same stdio driver, 3x tools/call search_codebase(query='batch process retry'))
- **one-shot CLI query** = 1.25s / 1.25s / 1.27s (identical — each CLI run reloads the model in a fresh process)  (/usr/bin/time -p pydocs-mcp search 'batch process retry' --project-dir . (3 consecutive runs))
- **isolated fastembed TextEmbedding construction** = 0.94s and 1.49s (whole python process incl ~0.15s interpreter; model pre-cached in ~/.cache/fastembed)  (/usr/bin/time -p .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')" (2 runs))
- **mcp SDK versions** = pyproject.toml:50 pins mcp>=1.28.1; uv.lock:1767-1768 resolves 1.28.1; measurement venv has 1.27.1 installed (drift)  (grep pyproject.toml + uv.lock; importlib.metadata.version('mcp') in /Users/msobroza/Projects/pyctx7-mcp/.venv)

# UNVERIFIED

- Client honoring of notifications/tools/list_changed: web-search-sourced only (Claude Code docs claim support ~v2.1.x with a history of missing-handler and race bugs per anthropics/claude-code#13646/#31893; Claude Desktop reportedly ignores it per #50339; Codex open feature request openai/codex#10105). Not tested against any real client; found no evidence either way for Continue or OpenCode.
- Runtime behavior of mcp 1.28.1 (the uv.lock pin) was verified by SOURCE READ only; the live wire measurement (listChanged: False, timings) ran on the installed mcp 1.27.1. Both show the same NotificationOptions defaults, but 1.28.1 was not executed.
- Timing numbers are from a single macOS machine on a 5-chunk scratch project with the ONNX model pre-cached; the 5.24s vs 1.90s time-to-ready spread shows strong filesystem-cache sensitivity, and a real-size project's serve indexing phase (package scans) would add time. Not a controlled benchmark; no network-download (first-ever install) cost measured.
- ~/pydocs-index workspace contents were not examined (the directory listing output was swamped by the ~/.pydocs-mcp listing); no claim made about it.
- The exact attribution of the intra-startup gaps (indexing-phase 1.1-2.6s and pre-query_cache 1.2s) to the two model loads is inferred from code (storage/factories.py:623, retrieval/factories.py:89) plus timing shape, not from a profiler.
- Whether Claude Code re-fetches tool DESCRIPTIONS (not just names) on a list_changed refresh was not verified anywhere.

# EVIDENCE

# R-D2 — Injection mechanism evidence

All paths relative to `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/phase-2-instrumentation-spec-498def` unless absolute. Worktree HEAD = f4a8f2e (== main; == the main checkout the measurement venv's editable install points at).

## 1. Server startup path — order, eager vs lazy

### CLI `serve` path (before `server.run` even starts)

Single-db `pydocs-mcp serve <path>` runs a FULL indexing phase first:
- `_cmd_serve` → `_run_cmd(_run_serve_indexing(args))` — `python/pydocs_mcp/__main__.py:1192-1196`; multi-repo (`--workspace`/`--db`) skips indexing entirely (`__main__.py:1185-1190`).
- `_run_indexing` (`__main__.py:579-647`): **eagerly opens SQLite** to ensure schema (`open_index_database(db_path).close()`, `__main__.py:598`), `AppConfig.load` (`__main__.py:603` via `_load_indexing_config` `__main__.py:567-576`), `configure_from_app_config` (`__main__.py:607`), then `build_project_indexer` which calls **`build_embedder(config.embedding)` eagerly** — `python/pydocs_mcp/storage/factories.py:623` — i.e. the fastembed model loads during the indexing phase even when the index turns out fully cached ("no changes (cached)").
- Watch check may run `AppConfig.load` a second time (`__main__.py:1207-1210`).

### `server.run()` proper (`python/pydocs_mcp/server.py:514-557`)

Order:
1. `config = AppConfig.load(explicit_path=config_path)` — server.py:531
2. `config.with_device(gpu=gpu)` — server.py:535
3. `configure_from_app_config(config)` (pushes YAML bounds into input-model module slots) — server.py:538
4. `build_routers(...)` — server.py:540-542 → server.py:421-511:
   - `_resolve_projects` (server.py:444, def 148-172) → `load_project` **eagerly opens SQLite** per bundle: schema-version probe on a throwaway connection (`python/pydocs_mcp/multirepo.py:62`, called from `load_project` at multirepo.py:131) then `open_index_database` + `read_index_metadata`, then **closes** (multirepo.py:113-140). No connection is held open.
   - read-only mode only: `validate_project_embedders` BEFORE any model load (server.py:444-452 comment: "a mismatched workspace fails BEFORE any model load").
   - `build_shared_retrieval_deps(config)` — server.py:453 → `python/pydocs_mcp/retrieval/factories.py:77-94`: `build_embedder(config.embedding)` (factories.py:89). For the default provider this is `FastEmbedEmbedder` whose `__post_init__` constructs `fastembed.TextEmbedding(...)` **immediately** — `python/pydocs_mcp/extraction/strategies/embedders/fastembed.py:118-143`. **The embedding model is EAGER at startup, not first-query.** (The sentence_transformers provider is likewise eager: retrieval/factories.py:125-128 comment "loads its torch model eagerly in __post_init__".) Heavy *imports* are lazy/function-local (embedders/providers.py:5-7 "registration never loads a model runtime") but *construction* here triggers the load. `build_llm_client` is a cheap client object (no network).
   - `_prepare_cross_links(..., run_link=True)` — server.py:465-472; inert Null objects unless cross_repo enabled AND ≥2 bundles (server.py:314-315).
   - Per-project services + retrieval pipelines built from YAML (server.py:473-484, `_build_project_services` 59-145). Query-time SQLite is **lazy per call**: `PerCallConnectionProvider` "opens/closes a fresh SQLite conn per acquire()" — `python/pydocs_mcp/retrieval/pipeline/connection.py:45-56`.
   - Freshness probe + envelope + ToolRouter (server.py:489-510).
5. Capability log per project (server.py:544-546).
6. `mcp = FastMCP("pydocs-mcp", instructions=SERVER_INSTRUCTIONS)` — server.py:551.
7. `_register_tools(mcp, tools)` — server.py:552 (nine tools, each `description=TOOL_DOCS[name]`, server.py:644).
8. `log.info("MCP ready ...")` — server.py:554-556; `mcp.run(transport="stdio")` — server.py:557 (blocks).

**Eager:** SQLite schema open + per-bundle metadata read; embedder ONNX load (TWICE on the single-db serve path — factories.py:623 then retrieval/factories.py:89); pipeline assembly; FastMCP construction/registration. **Lazy:** per-query SQLite connections (connection.py:45-56); optional-extra imports (ask_your_docs, torch runtimes) until configured/used; cross-link machinery unless ≥2 bundles.

## 2. Cold-start measurements

Environment: macOS (Darwin 23.6.0). Runner: `/Users/msobroza/Projects/pyctx7-mcp/.venv` (Python 3.11, **editable install of pydocs-mcp 0.5.1 → main checkout at f4a8f2e**, same commit as this worktree; installed `mcp==1.27.1`, `fastembed==0.8.0`; fastembed model pre-cached at `~/.cache/fastembed/models--qdrant--bge-small-en-v1.5-onnx-q`). The worktree itself has **no .venv**; `/opt/homebrew/bin/pydocs-mcp` is broken (ModuleNotFoundError). Existing bundles in `~/.pydocs-mcp` are stale-schema (example_needle v9, coding-agent-playbook has no `index_metadata`, friendly-swirles v13 with 0 packages, vs `SCHEMA_VERSION = 15` at `python/pydocs_mcp/db.py:18`), so a 5-chunk scratch project was created in the scratchpad and indexed (`pydocs-mcp index . --skip-deps --no-inspect`: **2.86s** cold, "Project: 5 chunks, 2 symbols, 3 trees").

Commands + numbers:

- (a) `/usr/bin/time -p .venv/bin/pydocs-mcp --help` → real **0.86s** (first), **0.39s / 0.37s** (repeats). `python -c "import pydocs_mcp"` → 0.38-0.47s; `python -c "from pydocs_mcp.application.tool_docs import TOOL_DOCS, SERVER_INSTRUCTIONS"` → 0.43s. Interpreter+import ≈ 0.4s warm.
- (b) time-to-ready, driver spawning `pydocs-mcp serve . --skip-deps --no-inspect -v` and stamping stderr lines relative to spawn until "MCP ready" (server.py:554):
  - Run 1: first log 1.07s → indexing phase "no changes (cached)" 3.76s → `query_cache_enabled` (i.e. shared retrieval deps built) 5.05s → **MCP ready 5.242s**.
  - Run 2 (page-cache warm): "no changes" 1.33s → **MCP ready 1.903s**.
  - The 1.1-2.6s gap inside the indexing phase and the 1.2s gap before `query_cache_enabled` are the two model loads (factories.py:623; retrieval/factories.py:89).
- (b') client-visible readiness via raw JSON-RPC over stdio (scratchpad driver: initialize → initialized → 3× tools/call search_codebase): **initialize response at 3.139s**. The response also returned `tools capability: {'listChanged': False}` (wire evidence for Q4).
- (c) first vs second query:
  - **Inside a live server:** query 1 **0.206s**, query 2 **0.182s**, query 3 **0.182s** — no model-load spike on first query; model load happened at startup.
  - **One-shot CLI** (`/usr/bin/time -p pydocs-mcp search "batch process retry" --project-dir .`): **1.25s / 1.25s / 1.27s** — identical across runs because every CLI invocation is a fresh process that reloads the model.
  - Isolated model cost: `python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"` → real **0.94s / 1.49s** (whole process incl. ~0.15s interpreter).

## 3. Function-local import seam + existing benchmarks rebinding code

- MCP server: `from pydocs_mcp.application.tool_docs import SERVER_INSTRUCTIONS` inside `run()` — **server.py:527**, consumed at **server.py:551**; `from pydocs_mcp.application.tool_docs import TOOL_DOCS` inside `_register_tools()` — **server.py:622**, consumed at **server.py:644** (`description=TOOL_DOCS[name]`; passed explicitly "so the tool text is the TOOL_DOCS single source regardless of how FastMCP resolves docstrings", server.py:626-632).
- CLI: `from pydocs_mcp.application.tool_docs import SERVER_INSTRUCTIONS, TOOL_DOCS` inside `_build_parser()` — **__main__.py:60**, with the explicit rationale comment at __main__.py:57-59: "Function-local on purpose (R2): the benchmarks-side description overlay re-binds these module attributes before the parser is built; a module-level import would freeze the pre-overlay binding."
- Contract confirmation: `docs/tool-contracts.md:32-40` — descriptions "remain mutable… the server reads them via function-local imports at registration time… which is what makes external overlay injection possible. Names frozen, descriptions optimizable — that split is the point of this freeze."

**What an overlay must do:** rebind the module attributes of `pydocs_mcp.application.tool_docs` (`td.SERVER_INSTRUCTIONS = new_text`; `td.TOOL_DOCS[name] = new_text` per tool) **before** `server.run()` executes (for MCP) or before `_build_parser()` (for CLI help). No mapping parameter exists on `run()`; the seam is module-attribute rebinding.

**Existing code that already does this:** `benchmarks/src/pydocs_eval/optimize/_overlay_server.py` (the "slice-6 §D6 overlay wrapper", 141 lines):
- module-level `import pydocs_mcp.application.tool_docs as td` (line 47);
- `_apply_overlay` (lines 99-115): validates the delimited overlay file through `ToolDocsArtifact().with_content(text).validate()` (line 106) — fail-closed `OverlayValidationError`, "the server refuses to boot" (lines 63-64, 107-110) — then `td.SERVER_INSTRUCTIONS = sections[_SERVER_KEY]` (line 112) and `td.TOOL_DOCS[key[len("TOOL: "):]] = content` (lines 113-115);
- `serve_with_overlay` (lines 67-96): applies the overlay, then late-imports `from pydocs_mcp.server import run` (line 92) and calls `run(db_path=cache_path_for_project(project.resolve()))` (line 96) using the SAME db-resolution helper as the CLI (docstring lines 20-24);
- launched as `python -m pydocs_eval.optimize._overlay_server <project> --overlay <file>` (lines 118-136);
- harness wiring: `benchmarks/src/pydocs_eval/optimize/fitness/paired_agent.py:61` (`_OVERLAY_SERVER_MODULE`), :181-186 (`_OverlayInjectingRunner` when `injection.overlay_path` set), :245-262 (`_rewrite_mcp_config_for_overlay` — in-place rewrite of each server's `.mcp.json` args to boot the wrapper).
- Its docstring (lines 1-13) records the §D6 design decision: the alternative "AppConfig.tool_docs_overlay_path field the product would read at startup" was **rejected**; attribute rebinding was "verified feasible (2026-07-08)". NOTE: the docstring's line citations ("server.py:249 → 261", "server.py:183") are **stale** — actual lines at f4a8f2e are 527 (SERVER_INSTRUCTIONS) and 622→644 (TOOL_DOCS).
- Delimited grammar: `benchmarks/src/pydocs_eval/optimize/artifacts/_delimited.py:32` (`=== SERVER_INSTRUCTIONS|SYSTEM_PROMPT|REWRITE_PROMPT|TOOL: <name> ===` sections); artifact firewall: `benchmarks/src/pydocs_eval/optimize/artifacts/tool_docs.py` (renders live surface, validates against `REQUIRED_MARKERS`/budgets). Lint constants are importable from the product: `python/pydocs_mcp/application/tool_docs.py:14-23` (`REQUIRED_MARKERS`, `CHARS_PER_TOKEN=4`, `PER_TOOL_TOKEN_BUDGET=500`, `TOTAL_TOKEN_BUDGET=3600`).

## 4. Hot-reload (`notifications/tools/list_changed`)

Versions: `pyproject.toml:50` requires `mcp>=1.28.1`; `uv.lock:1767-1768` resolves `mcp 1.28.1`. The measurement venv has **mcp 1.27.1 installed** (drift vs lock; consistent with server.py:589's "mcp 1.27.1 func_metadata.convert_result" comment). SDK source inspected at 1.28.1: `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a/.venv/lib/python3.12/site-packages/mcp/`.

- The protocol type exists: `ToolListChangedNotification`, method `"notifications/tools/list_changed"` — `mcp/types.py:1372-1378`.
- The server session CAN send it: `ServerSession.send_tool_list_changed()` — `mcp/server/session.py:489-491` — but **nothing in the SDK ever calls it** (grep found only the definition).
- FastMCP has runtime `add_tool` / `remove_tool` (`mcp/server/fastmcp/server.py:397-433, 435-444`) which mutate the tool manager but **do not emit the notification**.
- Capability advertisement: `FastMCP.run_stdio_async` calls `self._mcp_server.create_initialization_options()` with **no** `notification_options` (`mcp/server/fastmcp/server.py:753-760`), so `NotificationOptions` defaults apply (`tools_changed=False`, `mcp/server/lowlevel/server.py:112-120`) → `ToolsCapability(listChanged=False)` (`mcp/server/lowlevel/server.py:217`).
- **Wire-verified on the live pydocs-mcp server** (mcp 1.27.1): initialize response contained `tools: {'listChanged': False}`.
- Conclusion: description hot-reload via list_changed is NOT available from the current product+SDK wiring: it would require holding a session reference, calling `send_tool_list_changed()` manually, and constructing initialization options with `tools_changed=True` — i.e., product code changes beyond the overlay seam. Today the only reload path is restarting the stdio server (cheap: 1.9-5.2s measured).
- Client support (WEB-SOURCED, UNVERIFIED): Claude Code documents list_changed support (auto-refresh) from ~v2.1.x, but the history shows it shipped late and buggily — no handler registered originally (anthropics/claude-code#13646), spec-compliance gaps (#31893); Claude Desktop reportedly ignores it as of v1.3109.0 (#50339); OpenAI Codex has an open feature request (openai/codex#10105). No evidence found either way for Continue or OpenCode. Sources: [Claude Code MCP docs](https://code.claude.com/docs/en/mcp), [claude-code#13646](https://github.com/anthropics/claude-code/issues/13646), [claude-code#31893](https://github.com/anthropics/claude-code/issues/31893), [claude-code#50339](https://github.com/anthropics/claude-code/issues/50339), [codex#10105](https://github.com/openai/codex/issues/10105).

## 5. AppConfig layering + where a descriptions-source path would live

`AppConfig` lives at `python/pydocs_mcp/retrieval/config/app_config.py` (not config.py).

- Source order (`settings_customise_sources`, app_config.py:287-306; pydantic-settings = first source wins): `[init_settings, env_settings, user-YAML (if exists), shipped baseline]`. **Precedence highest→lowest: init kwargs > env vars > user YAML overlay > shipped `defaults/default_config.yaml`** (shipped path resolved at app_config.py:296-297,426-436). Caution for the spec: the commonly quoted "defaults → pipeline YAML → explicit overlay → env" is not the literal mechanism — env vars OUTRANK the user YAML overlay, and `pipelines/*.yaml` files are not a settings layer at all (they are referenced by path fields, e.g. `extraction.ingestion.pipeline_path`, and resolved by `pipeline_assembly`).
- User-YAML path resolution (`_resolved_user_config_path`, app_config.py:439-461): explicit `AppConfig.load(explicit_path=…)` > `$PYDOCS_CONFIG_PATH` > `./pydocs-mcp.yaml` > `~/.config/pydocs-mcp/config.yaml` > none. Explicit-but-missing path raises FileNotFoundError (app_config.py:324-327).
- Env plumbing: `env_prefix="PYDOCS_"`, `env_nested_delimiter="__"` (app_config.py:192-201) — any nested field is env-reachable, e.g. `PYDOCS_EMBEDDING__MODEL_NAME`.
- Natural home for a "descriptions source path": `ServeConfig` (`python/pydocs_mcp/retrieval/config/models.py:656-663` — "future serve-side knobs … get an obvious home"), giving YAML `serve.descriptions_path` and env `PYDOCS_SERVE__DESCRIPTIONS_PATH`. **Tension to surface:** the recorded §D6 decision (benchmarks `_overlay_server.py:1-13`) explicitly rejected an `AppConfig.tool_docs_overlay_path` product field in favor of the zero-product-hook wrapper; Phase 1 externalization must either revisit that decision or render `tool_docs.py`'s constants from the source document at build/import time instead of adding a runtime config field.
- CLI flags already mapped to AppConfig fields: `--config` → `AppConfig.load(explicit_path=…)` (`__main__.py:69-74`, threaded via `_serve_run` __main__.py:1172 → server.py:531); `--gpu` → `config.with_device` (server.py:535, app_config.py:269-285); `--watch` OR YAML `serve.watch.enabled` (either enables, flag cannot force off — `__main__.py:1202-1210`); `--full-dep` → merged into `embedding.full_index_dependencies` (app_config.py:251-267, __main__.py:567-576); `--depth` defaults to None so YAML `extraction.members.inspect_depth` wins (`__main__.py:159-169, 612-614`).

## 6. CLI help production

- Help is produced at **runtime, per invocation** — `main()` calls `_build_parser()` fresh each run (`__main__.py:1432-1433`); nothing is frozen at module import.
- `_build_parser` imports `TOOL_DOCS`/`SERVER_INSTRUCTIONS` function-locally (`__main__.py:60`); top-level `description=SERVER_INSTRUCTIONS` (`__main__.py:66`, "one source, zero drift — contract §6 note 4"); each of the nine canonical subcommands takes `help=TOOL_DOCS[canonical].splitlines()[0]` and `description=TOOL_DOCS[canonical]` with `RawDescriptionHelpFormatter` (`__main__.py:254-263`).
- Consequence: **today**, "swapping the source document" cannot change CLI help because there is no source document — `TOOL_DOCS`/`SERVER_INSTRUCTIONS` are hardcoded Python literals in `python/pydocs_mcp/application/tool_docs.py:37-…/139-…`. The two no-code-change paths that DO work today: (1) edit tool_docs.py (a code change), or (2) rebind the module attributes before `_build_parser()` runs (the R2 comment at __main__.py:57-59 explicitly anticipates this, though the shipped benchmarks wrapper only wraps `server.run`, not the CLI entry). If Phase 1 renders these module attributes from an external document at import/load time, both the MCP registration (server.py:551/644) and the CLI help (__main__.py:60/66/258-259) pick the change up automatically, because every consumer reads the module attributes at call time.

## Scratch artifacts (outside repo)

Measurement drivers + scratch project under `/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp--claude-worktrees-phase-2-instrumentation-spec-498def/28dd46c1-285c-4e80-b984-d685e61e96a9/scratchpad/` (`time_serve.py`, `mcp_query_timing.py`, `measureproj/`). Side effect outside the repo: indexing the scratch project created `~/.pydocs-mcp/measureproj_0a5544428e.db` (+.tq), alongside the hundreds of pre-existing `myproject_*` test bundles. No repo file was modified.
