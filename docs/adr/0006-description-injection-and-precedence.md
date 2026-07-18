# ADR 0006 — Description injection: import-time rendering, explicit override at entry points, universal strictness, no hot-reload

- **Status:** Accepted · **Date:** 2026-07-18 · **Phase:** 1
- **Decision area:** D2 of the Phase 1 owner spec ("externalized optimizable surface & deterministic harness behavior")
- **Depends on:** the source-format decision (ADR 0005): one packaged delimited
  document, `python/pydocs_mcp/defaults/descriptions.md`, parsed by the product-side
  grammar in `pydocs_mcp/application/description_source.py`.

## Context

The tool contract freeze deliberately left descriptions mutable: "Names frozen,
descriptions optimizable — that split is the point of this freeze"
(`docs/tool-contracts.md:32-40`). Today the mutable substrate is a pair of hardcoded
Python literals — `TOOL_DOCS` and `SERVER_INSTRUCTIONS` in
`python/pydocs_mcp/application/tool_docs.py`. Phase 1 replaces the literals with a
rendered external document; this ADR decides **how the rendered text gets into the
running MCP server and the CLI**: the loading mechanism, the precedence chain
(packaged default → user override → optimization candidate), the failure behavior for
missing/invalid sources, and the cost of swapping one optimization candidate for
another.

Spec constraints: the same rendered bundle must reach MCP and CLI; precedence must
terminate in packaged defaults; an invalid or drift-failing source in an optimization
run must be a **hard error, never a silent fallback**; and the artifact hash must
reflect what is actually served.

## Evidence

All paths relative to repo root; worktree HEAD f4a8f2e.

**The injection seam is real and already exercised.** Every consumer reads the
`tool_docs` module attributes at call time, never at module import: the MCP server
imports them function-locally inside `run()` and `_register_tools()`
(`server.py:527` → `:551`; `server.py:622` → `:644`, `description=TOOL_DOCS[name]`),
and the CLI builds its parser fresh per invocation (`__main__.py:1432-1433`) from the
same attributes (`__main__.py:60/66/258-263`); the comment at `__main__.py:57-59`
says the function-local import exists so a pre-parser rebinding is picked up. A
working rebinding wrapper already ships in
`benchmarks/src/pydocs_eval/optimize/_overlay_server.py`: it validates a delimited
overlay file fail-closed (`ToolDocsArtifact().with_content(text).validate()`, lines
99-115 — invalid overlay means "the server refuses to boot"), rebinds
`td.SERVER_INSTRUCTIONS` / `td.TOOL_DOCS[name]`, then late-imports and calls
`pydocs_mcp.server.run`. Its docstring (lines 1-13) records the earlier design
decision this ADR revisits: a product-side `AppConfig.tool_docs_overlay_path` field
was rejected in favor of the zero-product-hook wrapper. The docstring's line
citations are stale (`server.py:249/261/183` vs the actual `:527` and `:622→:644`).

**Startup and restart costs (measured, macOS, 5-chunk scratch project, ONNX model
pre-cached; single machine, not a controlled benchmark):**

- `pydocs-mcp --help`: 0.86 s first run, 0.37–0.39 s warm; bare
  `import pydocs_mcp` 0.38–0.47 s; importing `tool_docs` 0.43 s.
- `serve` time-to-ready ("MCP ready", `server.py:554`): **5.24 s** run 1, **1.90 s**
  page-cache-warm run 2. Client-visible `initialize` response at **3.14 s**.
- The embedding model loads **eagerly at startup**, not on first query: first vs
  subsequent `tools/call` on a live server is 0.206 s / 0.182 s / 0.182 s. The
  single-db `serve` path loads the fastembed model twice (`storage/factories.py:623`
  in the pre-serve indexing phase, `retrieval/factories.py:89` for retrieval deps);
  isolated `fastembed.TextEmbedding` construction is 0.94–1.49 s.
- One-shot CLI query: 1.25–1.27 s per run (fresh process = fresh model load).

**Hot-reload is not available from the current product+SDK wiring.** The MCP SDK
defines `ServerSession.send_tool_list_changed()` (`mcp/server/session.py:489-491`,
inspected at the pinned 1.28.1) but **nothing in the SDK ever calls it**; FastMCP's
runtime `add_tool`/`remove_tool` do not emit the notification; and `run_stdio_async`
passes no `notification_options`, so the defaults advertise
`ToolsCapability(listChanged=False)` (`mcp/server/lowlevel/server.py:112-120, 217`).
Wire-verified on the live pydocs-mcp server: the `initialize` response returned
`tools: {'listChanged': False}`. (Caveat: the wire run used the installed mcp 1.27.1;
the 1.28.1 lock pin was source-read only — same `NotificationOptions` defaults.)
Client honoring of the notification is **web-derived and unverified**: one CLI agent
client documents support from ~v2.1.x with a public history of missing-handler and
spec-compliance bugs, a desktop client reportedly ignores it, another vendor's CLI
has an open feature request; whether any client re-fetches **descriptions** (not
just names) on refresh was not verified anywhere.

**AppConfig layering (verified, corrects a common misquote).** Source order is
`[init_settings, env_settings, user-YAML, shipped defaults]`
(`retrieval/config/app_config.py:287-306`; pydantic-settings first-source-wins) —
env vars **outrank** the user YAML overlay, and `pipelines/*.yaml` files are not a
settings layer at all (they are referenced by path fields and resolved by pipeline
assembly). The natural home for a descriptions path is `ServeConfig`
(`retrieval/config/models.py:656-663`), giving YAML `serve.descriptions_path` and env
`PYDOCS_SERVE__DESCRIPTIONS_PATH` via the `PYDOCS_` prefix + `__` nesting
(`app_config.py:192-201`).

## Options considered

**(a) Startup-time load from a path/env var; one process per candidate.** Simple; a
candidate swap costs a restart: 1.9–5.2 s to ready / 3.1 s to `initialize`, dominated
by eager embedder construction, not description loading.

**(b) (a) plus hot-reload via `notifications/tools/list_changed`.** Would amortize
restarts across candidates. Killed by the evidence: the server never sends the
notification and advertises `listChanged: false` (wire-verified), so this means new
product machinery (session reference, `tools_changed=True` initialization options,
manual `send_tool_list_changed()`) to save seconds of restart against multi-minute
rollout evaluations — and client honoring is unverified anyway.

**(c) Build-time codegen of the packaged defaults, combined with (a) or (b).** The
spec's prior. Codegen's only benefit over runtime parsing is import-time cost, and
parsing one ~3.6K-token document is estimated at low single-digit milliseconds
against the measured 0.4 s baseline interpreter+import cost (estimate, not separately
measured — bounded by the CI golden test below). Buried: codegen buys nothing
measurable and adds a second mechanism plus a generated-vs-source drift surface.

## Decision

**Option (c)+(a) in shape, with codegen replaced by import-time rendering — one
mechanism for both the packaged default and the override path. Hot-reload dropped on
measurement. Strictness universal.**

1. **Import-time rendering of the packaged default.** `tool_docs.py` keeps its
   `TOOL_DOCS` / `SERVER_INSTRUCTIONS` module attributes — every existing consumer
   (`server.py:527/622`, `__main__.py:60`, the benchmarks overlay wrapper, the lint
   tests and their importable constants at `application/tool_docs.py:14-23`) keeps
   working unchanged — but the attributes are **populated at import** by parsing the
   packaged `defaults/descriptions.md` via `importlib.resources`. A validation
   failure of the shipped file is a packaging bug: it fails loudly at import and is
   caught pre-release by a CI golden test.

2. **Explicit override via one `apply_source(path)` API, applied at entry points.**
   Surface: CLI flag `pydocs-mcp serve --descriptions <path>`, YAML
   `serve.descriptions_path`, env `PYDOCS_SERVE__DESCRIPTIONS_PATH` (all through
   `ServeConfig`). `apply_source` runs **before** MCP registration (ahead of
   `FastMCP(...)` at `server.py:551`) and before CLI parser build: parse → validate
   (grammar + frozen-tool-name drift + the tool-docs lint markers and token budgets)
   → rebind the module attributes. Because CLI help is rebuilt per invocation from
   the same attributes, MCP/CLI parity holds with zero extra code, pinned by test.

3. **Precedence:** CLI flag > env var > user YAML > packaged default — matching the
   verified AppConfig mechanism, in which env **outranks** user YAML (do not document
   the folk order "defaults → YAML → env"). The chain terminates in the packaged
   document: the default is used **only when no override is supplied at all**.

4. **Universal strictness — no separate "optimization mode" flag.** Any explicitly
   supplied source (flag, env, or YAML) that is missing or fails validation is a hard
   error at startup. Strictly simpler than the spec's mode-scoped strict flag, and it
   closes the same hole by construction: fallback only exists where no override was
   named, so no code path lets a named candidate degrade to the packaged default.
   (Precedent: explicit-but-missing `--config` already raises, `app_config.py:324-327`;
   the benchmarks wrapper is already fail-closed.)

5. **Hot-reload dropped; one process per candidate.** Candidate swap cost = process
   restart: 1.90–5.24 s to ready, 3.14 s to client-visible `initialize` — noise
   against multi-minute rollout evaluations, and mostly embedder load, not
   description handling. The server-side `list_changed` machinery does not exist in
   the current wiring, and client honoring is web-derived/unverified.

6. **Artifact hash (truthfulness requirement).** `current_artifact_hash()` = SHA-256
   over the **normalized rendered document of the live module attributes** plus a
   `RENDERER_VERSION` constant (v1), computed on demand — so it reports whatever is
   actually bound, including text injected by the legacy benchmarks wrapper rather
   than by `apply_source`. Logged once at serve startup
   (`descriptions artifact <hash12> source=packaged|<path>`) and importable
   programmatically. **Not** added to the response envelope: `meta` field names are
   frozen (`docs/tool-contracts.md` §1–§2), no client consumes a per-response hash,
   and Phase 2 attribution reads it from the startup log / run config instead.

7. **This deliberately revisits the recorded overlay decision.** The
   `_overlay_server.py` docstring records that a product config field for an overlay
   path was rejected in favor of a zero-product-change wrapper — the right call for
   benchmarks-side injection against a then-frozen product. Phase 1's owner-fixed
   requirements (externalized model-facing text, hash-truthful serving,
   byte-identical default behavior) now mandate product-side loading, superseding
   that rejection. The wrapper keeps working unchanged (same attributes, rebound
   before `server.run`); migrating it onto `apply_source()` is a harness-side
   follow-up that also fixes its stale line citations.

## Consequences

**Benefits.** One mechanism instead of two (no codegen step, no generated-file
drift); MCP and CLI provably render the same bundle from the same attributes; seed
content is the current literals verbatim, so default behavior is byte-identical; the
on-demand hash stays truthful across both injection paths.

**Costs and risks.**

- Every import of `tool_docs` — including `--help` — now pays a parse+validate of the
  packaged document. The ~ms estimate is not separately measured; the CI golden test
  must assert a budget. A corrupted packaged document breaks **every** entry point at
  import, not just serve — the intended loud failure, but it makes that golden test
  load-bearing for releases.
- Module-attribute rebinding remains a global mutable seam with two writers
  (`apply_source` and the legacy wrapper) until the wrapper migrates. The hash is
  truthful about the **module attributes**; its equivalence to the **served**
  descriptions relies on all rebinding happening before registration (`server.py:644`
  captures `TOOL_DOCS[name]` at registration time). Both writers honor that today; a
  post-registration rebind would desynchronize hash from wire — the parity pin test
  must freeze this invariant.
- Universal strictness means a stale `serve.descriptions_path` in a user YAML — or a
  forgotten exported `PYDOCS_SERVE__DESCRIPTIONS_PATH`, which silently outranks it —
  hard-fails startup rather than falling back. Deliberate, but ops-visible; the error
  must name the winning source.
- One process per candidate pays the full eager startup, including the double
  embedder load on the single-db serve path. Acceptable at multi-minute rollout
  granularity; a future fast-iteration loop would reopen hot-reload, which then
  requires product changes **and** verified client re-fetch of descriptions —
  currently unevidenced. The timing numbers are single-machine, small-project, and
  filesystem-cache-sensitive (5.24 s vs 1.90 s spread); the restart-≪-rollout
  conclusion has wide margin, but the absolute numbers are not portable.
- Phase 2's hash consumption depends on the startup log line / run config, not the
  wire; the log format must be pinned by test or attribution breaks silently.

## Action items

Phase 1 (this phase) — product:

1. `application/tool_docs.py`: populate `TOOL_DOCS` / `SERVER_INSTRUCTIONS` at import
   from packaged `defaults/descriptions.md` (via `importlib.resources` and the
   grammar in `application/description_source.py`); keep the lint constants
   (`tool_docs.py:14-23`) importable and authoritative.
2. Implement `apply_source(path)` (parse → validate drift + markers/budgets → rebind)
   with hard-error semantics for missing/invalid explicit sources; error text names
   the selected source and why it won.
3. Add `ServeConfig.descriptions_path` (`retrieval/config/models.py:656-663`) → YAML
   `serve.descriptions_path` + env `PYDOCS_SERVE__DESCRIPTIONS_PATH`; add
   `--descriptions` to the serve subcommand in `__main__.py`; call `apply_source`
   in `server.run` before `FastMCP(...)` (`server.py:551`) and before
   `_build_parser()` when configured.
4. Implement `current_artifact_hash()` + `RENDERER_VERSION = 1`; emit the
   `descriptions artifact <hash12> source=...` line beside "MCP ready"
   (`server.py:554-556`); pin the line format by test.
5. Tests: CI golden test (packaged document parses, validates, renders byte-identical
   to the Phase 0 baseline literals, within an import-time budget); MCP/CLI parity
   pin; strictness tests for flag/env/YAML missing-and-invalid paths; precedence test
   asserting flag > env > user YAML > packaged.

Deferred — Phase 2 (harness instrumentation):

6. Migrate `benchmarks/src/pydocs_eval/optimize/_overlay_server.py` onto
   `apply_source()`, fixing its stale docstring citations; consume
   `current_artifact_hash()` from the startup log / run config for per-run
   attribution. The wrapper works unchanged until then.
