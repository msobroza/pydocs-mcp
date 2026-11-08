# Runbook: ask-your-docs (pydocs-mcp 0.6.0) on example_needle with OpenRouter embeddings and chat (corrected)

Target code: `origin/main` f6943a8. Unless marked otherwise, every `file:line` refers to that commit. Where a cite points at an installed package, the version is named.

Checks run for this correction (2026-09-10):
- I read the source with `git show origin/main:<path>`.
- Other checks were read-only: SQLite `mode=ro&immutable=1` queries against the bundles; the pyctx7 `.venv` installs (mcp 1.27.1, langchain-mcp-adapters 0.3.0, openai 1.109.1, httpx 0.28.1, streamlit 1.59.1); OpenRouter's public `/api/v1/models` and `/api/v1/embeddings/models`; PyPI JSON; `git ls-remote`.
- Nothing was indexed, published or edited. `/Users/msobroza/Projects/pyctx7-mcp` and `/Users/msobroza/Projects/example_needle` were not modified.

## Corrections

1. **Status-line bearer cell (Q6, Step 7).**
   - The draft said the status line shows the key's last 4 characters. It doesn't. In `auth.api_key_env` mode the cell reads `$OPENROUTER_API_KEY set` (or `missing`). Last-four is shown only in token-service mode (`harness/ask_your_docs/connection_dialog.py:77-78,81-82,90-94`).
   - The full expected line is `openrouter.ai · qwen/qwen3.8-27b · $OPENROUTER_API_KEY set · vision: no (configured)`. Sources: `display_host` drops the path (`bearer_tokens.py:127-133`); `vision_cell` is at `connection_dialog.py:97-101`; `CapabilitySource.CONFIGURED = "configured"` is at `multimodal.py:37,45`; the line is joined at `connection_dialog.py:104-118`.
   - This is derived from the code; the page wasn't rendered.

2. **Sidebar (Step 7).**
   - The draft said the Project picker is labelled "own code only". It isn't. The sidebar has a **Project** selectbox with `All projects` and `example_needle` (`app.py:306`) and a **Code** radio with All code / Own code / Dependencies (`app.py:308-310`).
   - There is no **Package** picker, because there are no dependency packages (`app.py:321`).
   - The text "own code only (no dependency packages indexed)" is in the agent's system-prompt catalog (`catalog.py:57-64`), not the sidebar.
   - The pickers default to All projects / All code, so no pin is applied unless you pick one.

3. **File counts (Q5).**
   - The built-in exclusion entry `"egg-info"` matches only a path component named exactly `egg-info` (`extraction/config.py:61,94-114`). So `src/example_needle.egg-info/*.txt` is indexed.
   - Files in scope: **164** (113 `.py`, 32 `.md`, 5 `.yaml`, 5 `.yml`, **5 `.txt`**, 3 `.toml`, 1 `.ini`; about 392 KB), not 159.
   - The verified bundle has chunks from 163 of them. The 4 egg-info `.txt` files that produced chunks give 5 chunks (measured).
   - `.claude` adds about **179** files by the same filter, not 169.
   - Excluding `example_needle.egg-info` is optional; see Step 2b.

4. **"0.5.1 then refuses the file" (Q4) is misleading.**
   - 0.5.1 is schema **v14** (`v0.5.1:python/pydocs_mcp/db.py:18`), so it already refuses the v15 bundles in `~/pydocs-index` (`FutureSchemaError`, `v0.5.1:multirepo.py:31`).
   - Once 0.6.0 migrates them to v16, the build that starts refusing them is the v15 dev build that serves `~/pydocs-index` today (same guard on main: `multirepo.py:45-76`).

5. **What happens to the Continue demo bundle (§0.3, Q4).**
   - The demo runs **pydocs-mcp 0.3.1 (schema v9)** from `example_needle/.venv` (installed from git commit fab180d).
   - Its `open_index_database` drops all tables and rebuilds on any version it doesn't recognize (`example_needle/.venv/.../pydocs_mcp/db.py:3,273-308`).
   - So if 0.6.0 writes v16 into `~/.pydocs-mcp/example_needle_5383c5f58b.db`, the demo doesn't refuse the file. Its next serve silently wipes it and re-indexes with Qwen3-0.6B.

6. **How far the cache override isolates (Q4). Confirmed, with precise limits.**
   - `index` with `--cache-dir` writes only `<dir>/{name}_{slug}.db`, with the `.tq` alongside via `with_suffix` (`__main__.py:605-617`; `db.py:739,784` creates only that directory).
   - A CLI query with `--workspace` never computes or opens a default-cache db (`__main__.py:933-937`).
   - **Serve children do not get `PYDOCS_CACHE_DIR`.** It isn't in the mcp inherited list (`mcp/client/stdio/__init__.py:28-45,127`), so inside the child `default_cache_dir()` resolves to `~/.pydocs-mcp` (`db.py:261-276`).
   - That doesn't matter here. `serve --workspace` reads only the workspace (`__main__.py:1281-1290`, `server.py:165-166`), and the cross-link overlay is off below 2 bundles (`server.py:315`).
   - With 2 or more bundles, the overlay goes to `<workspace>/pydocs-links.sqlite3`. It falls back to `~/.pydocs-mcp/links/<md5>.sqlite3` only if the workspace isn't writable (`server.py:248-270`, `storage/factories.py:935-951`).
   - The before/after check in Steps 0 and 8 now covers every file. The old check looked only at `.db` files and missed `.tq`, `-wal`, `-shm` and any new files.

7. **Config validation.**
   - `AppConfig` is **`extra="ignore"` at the top level** (`app_config.py:191`). A misspelled top-level key such as `ask_your_doc:` or `embeddings:` is silently dropped.
   - Every nested block this runbook uses is `extra="forbid"`: `embedder_models.py:94`; `extraction/config.py` (ExtractionConfig, DiscoveryConfig, DiscoveryScopeConfig); `ask_your_docs_models.py:113,146,178`.
   - Environment variables beat the YAML: source order is init > env > user YAML > shipped (`app_config.py:293-297`).
   - Only **one** user YAML is loaded. An explicit `--config` wins outright; `PYDOCS_CONFIG_PATH`, `./pydocs-mcp.yaml` and `~/.config/pydocs-mcp/config.yaml` are consulted only when there is no explicit path (`app_config.py:462-484`). None of those files exist on this machine (checked).
   - I added Step 2e to print the effective values.

8. **What actually goes over the wire for embeddings (Q1).**
   - When the caller doesn't set `encoding_format`, the openai SDK adds `encoding_format: "base64"` to every request and decodes the reply (openai 1.109.1 `resources/embeddings.py:107-108,111-127`).
   - Queries are sent as `input=<str>` (`openai.py:66-71`); indexing batches as `input=[...]` (`openai.py:81-85`).
   - OpenRouter accepted both in the verified index and search runs.

9. **Streamlit passthrough is now verified (was "Couldn't confirm" #5).**
   - With streamlit 1.59.1, click parses `app.py --server.fileWatcherType none --server.headless true` as Streamlit options (`make_context` gives `server_fileWatcherType='none'`, `server_headless=True`).
   - On CPython 3.12, argparse `REMAINDER` keeps the leading `--`, and `cli.py:85` strips it.
   - The config option's environment variable is `STREAMLIT_SERVER_FILE_WATCHER_TYPE`.

10. **Clearing the UI config field (Q3).** Besides the serve child's `EmbedderMismatchError`, the parent loses the `ask_your_docs.llm` block. It falls back to `gpt-4o-mini` on api.openai.com with a lenient `OPENAI_API_KEY` bearer (`llm_connection.py:134-135,196-197`; `ask_your_docs_models.py:23-24`).

11. **Stray `OPENAI_BASE_URL` / `LLM_MODEL` (§0.5) affect the UI only.** `build_agent` leaves the environment tier empty (`agent.py:400-404,418-419`), so the headless script ignores them.

12. **`--workers` (Q1).** It is passed through `run_index_pass` to the orchestrator (`__main__.py:690`, `application/index_project.py:47,110`). I didn't trace how the orchestrator uses it, so I softened that claim.

13. **Install.**
   - Checked again: PyPI's latest is 0.5.1 and 0.6.0 is absent. The GitHub repo is public, and f6943a8 is `refs/heads/main`.
   - The git build needs `cargo` on PATH; Step 1 now exports it.
   - The script and extra were renamed in 0.6.0 with **no compatibility shim**, and pip only warns about an unknown extra (CHANGELOG 0.6.0 "BREAKING: the ask-your-docs harness moved…"). Don't use 0.5.1's `[ask-your-docs]` / `ask-your-docs` names.

14. **Minor.** The UI catalog glob (`catalog.py:29-30`) doesn't exclude `pydocs-links.*`; only the server does (`multirepo.py:156`). It's harmless, because the overlay's real name ends in `.sqlite3`.

**Checked and unchanged:**

*Config*
- Every `embedding` key and its placement (`embedder_models.py:96-101,151-153`). The registry dimension is 2560 (`:52`) and the dimension validators are at `:178-212`. `send_dimensions: false` changes the cache hash (`:276-277`).
- `batch_size` is inert for the openai provider (`providers.py:69-75`). The real batch size is set in `ingestion.yaml:19` and read at `embed_chunks.py:139`. Batches run sequentially (`:99`).
- SDK defaults: 600 s timeout, 5 s connect, 2 retries (`openai/_constants.py:9-10`).
- The `ask_your_docs` keys, and the rule that `auth` takes exactly one of `token_url` / `api_key_env` (`ask_your_docs_models.py:107-128,139-186`).
- `vision: false` means text-only with no probes (`llm_connection.py:206-218,463-469`). `auto` with a text-only model routes to `text_react` (`auto.py:46-47`).
- The CLI flags and environment mapping (`cli.py:19-24,51-100`). `--config` must come before the subcommand (`__main__.py:69-74`). Cache-dir precedence (`__main__.py:86-93`, `db.py:29,35,261-289`). `serve` has `--workspace` (`__main__.py:231-232`).

*Serve child*
- The serve command line, and that no environment is passed (`agent.py:278-289`, `app.py:196-207`).
- langchain-mcp-adapters 0.3.0 passes `env=None` (`sessions.py:248-260`). mcp merges a given `env` over its minimal default (`stdio/__init__.py:127`), so `subprocess_env` gives the child the minimal set plus the key.
- The embedder check compares only model and dimension (`multirepo.py:166-181`, `storage/index_metadata.py` `embedder_matches`).

*Measured*
- Schema versions: v16 in the verified bundle; v9 with no `index_metadata` in `~/.pydocs-mcp`; v15 with an empty `index_metadata` in `~/pydocs-index`.
- The Step 4 output reproduces read-only against the verified bundle.
- OpenRouter IDs, prices, context sizes, modalities and tool/reasoning support match (public listings).

*Local tooling*
- The `.pth` one-liner parses and sets the key under `env -i` on cpython 3.12.12.
- `~/.local/bin/uv` is arm64 0.11.28; the anaconda `uv` comes first on PATH; `python3` is 3.8.8; `~/.cargo/bin/cargo` exists.
- `.env` line 2 is `OPENROUTER_API_KEY=` with no `export` prefix, no quotes and no trailing whitespace.
- example_needle's HEAD is 9c170b0. `MaxSimScorer` is at `src/needle/scoring/strategies.py:39`.

---

## 0. Read this first

1. **The Streamlit UI can't pass the OpenRouter key to its search server, so questions fail as shipped.**
   - The UI starts `pydocs-mcp serve` without an `env` map (`app.py:196-207`, `agent.py:283-289`).
   - With `env=None`, the MCP stdio client gives the child only `HOME LOGNAME PATH SHELL TERM USER` (mcp `client/stdio/__init__.py:28-45,51,127`, checked in 1.27.1 as installed). Nothing else reaches the child: not `PYDOCS_CACHE_DIR`, not `SSL_CERT_FILE`, not `PYTHONPATH`.
   - The serve child builds the OpenAI embedder at startup (`server.py:454`, `retrieval/factories.py:89`), and the embedder refuses to start without the key (`openai.py:44-51`).
   - **VERIFIED:** it fails with `OpenAIEmbedder requires the OPENROUTER_API_KEY environment variable…` followed by `McpError: Connection closed`.
   - Workarounds: a `.pth` key loader for the UI (Step 2c), or `build_agent(subprocess_env=…)` for the headless script (Step 6).
   - Upstream fix to consider: have `serve_connection` forward `embedding.api_key_env`.
2. **0.6.0 is not on PyPI.** The latest is 0.5.1, and f6943a8 still declares `version = "0.5.1"` (`pyproject.toml:7`). 0.6.0 renames the extra to `[harness-ask-your-docs]` and the script to `harness-ask-your-docs`, with no shim.
3. **The default cache location overwrites the Continue demo bundle.**
   - `pydocs-mcp index` with the default cache writes `~/.pydocs-mcp/example_needle_5383c5f58b.db`, which is the demo's file.
   - The demo's pydocs-mcp 0.3.1 then drops and rebuilds it on its next serve.
   - Always point the cache somewhere else (Q4).
4. **`.claude/worktrees/rag-load-test-locust-bdb5db` (about 179 files) gets indexed unless you exclude it.** `.claude` isn't in the built-in exclusion list (`extraction/config.py:47-86`). Likewise `src/example_needle.egg-info/*.txt` is indexed, because `"egg-info"` matches only a directory literally named `egg-info`.
5. **Stray shell variables win over the YAML in the UI.**
   - `OPENAI_BASE_URL` or `LLM_MODEL` override `ask_your_docs.llm` (`llm_connection.py:122-133`), and the bearer follows the effective endpoint.
   - Any `PYDOCS_*` variable overrides the YAML everywhere (`app_config.py:293-297`).
   - Unset them all (Step 0).

---

## Q1. Embedding config

```yaml
embedding:
  provider: openai
  model_name: qwen/qwen3-embedding-4b
  dim: 2560
  base_url: https://openrouter.ai/api/v1
  api_key_env: OPENROUTER_API_KEY
  send_dimensions: false
  batch_size: 32        # inert for provider openai (see below)
```

- **Precedent.** Identical to `benchmarks/configs/qwen3_4b.yaml:19-26`.
- **Validation.** `EmbeddingConfig` is `extra="forbid"` (`embedder_models.py:94`).
  - Fields: `:96` (provider), `:100-101` (dim, batch_size), `:151-153` (base_url, api_key_env, send_dimensions).
  - The registry has `"qwen/qwen3-embedding-4b": 2560` (`:52`), and the config won't load if `dim` differs (`:194-212`).
  - 2560 is a multiple of 8, as TurboQuant requires (`:178-192`).
- **What gets sent.**
  - The builder (`providers.py:50-75`) creates `AsyncOpenAI(api_key=os.environ[api_key_env], base_url=…)` (`openai.py:43-57`).
  - Indexing calls `embeddings.create(model=…, input=[texts])` (`openai.py:81-85`); queries call it with `input=<str>` (`openai.py:66-71`).
  - `dimensions` is added only when `send_dimensions: true`, which is the default (`openai.py:59-64`).
  - The SDK itself adds `encoding_format: "base64"` and decodes the reply (openai 1.109.1 `resources/embeddings.py:107-127`). OpenRouter accepts all of these (verified index and search runs).
- **Does OpenRouter accept `dimensions`?** Yes (**VERIFIED** earlier).
  - `dimensions=2560` returns 2560 values, `dimensions=1024` returns 1024, and omitting it returns the native 2560.
  - `send_dimensions: false` matches the benchmark, and is part of the cache identity (`embedder_models.py:276-277`). The serve-side embedder check compares only model and dimension.
- **Price.** $0.02 per million input tokens; 32,768-token context (checked again on `/api/v1/embeddings/models`).
- **Batching.**
  - `embedding.batch_size` isn't passed to the openai builder (`providers.py:69-75`).
  - The request size comes from `{ type: embed_chunks, batch_size: 32 }` (`pipelines/ingestion.yaml:19`, read at `embed_chunks.py:139`).
  - Batches run one after another (`embed_chunks.py:99-104`).
- **Timeouts, retries, concurrency.** None are configurable. You get the openai SDK defaults: 600 s timeout, 5 s connect, 2 retries on 408/409/429/5xx (`openai/_constants.py:9-10`).
  - `--workers` is passed to the index orchestrator (`application/index_project.py:110`). With `--skip-deps` there's only one package.
- **Query time.** The serve child uses the same embedder (`server.py:454`). Qwen3's separate instruction for queries isn't applied: queries go out verbatim (`qwen3_4b.yaml:12-16`).

## Q2. `ask_your_docs` block

```yaml
ask_your_docs:
  architecture: text_react          # "auto" gives the same result: vision:false routes to text_react (architectures/auto.py:46-47)
  llm:
    base_url: https://openrouter.ai/api/v1
    model: qwen/qwen3.8-27b
    auth:
      api_key_env: OPENROUTER_API_KEY   # exactly one of token_url / api_key_env (ask_your_docs_models.py:107-128)
    vision: false
```

- **Schema:** `ask_your_docs_models.py:139-186`. Every level is `extra="forbid"` (`:113,146,178`).
- **Key handling.**
  - With `api_key_env`, the bearer is that variable, re-read on every call. It is strict: if the variable is unset you get `BearerUnavailableError` (`bearer_tokens.py:159-178`, wired at `llm_connection.py:308-309,355-356`).
  - Leave `token_field` and `renew_on_status` unset; they only apply to token services.
- **Vision off.**
  - `vision: false` means text-only with no capability probes (`llm_connection.py:206-218,463-469`).
  - Attached images are rejected (`multimodal.text_only_fallback: reject`, `ask_your_docs_models.py:77`).
  - OpenRouter lists `qwen/qwen3.8-27b` as accepting text, image and video, so `false` is a deliberate choice.
- **Precedence in the UI.**
  - `--model` and `--base-url` are copied into `LLM_MODEL` and `OPENAI_BASE_URL` (`cli.py:19-24,80-84`).
  - The page layers YAML, then the environment, then the Connection dialog, which applies for the session only (`app.py:121-125`, `llm_connection.py:108-138`).
  - `auth` and `vision` come only from the YAML, or from a `PYDOCS_ASK_YOUR_DOCS__LLM__*` / `PYDOCS_ASK_YOUR_DOCS__LLM` / `PYDOCS_ASK_YOUR_DOCS` environment variable (`llm_connection.py:5-9`, `binding.py:87-95`).
  - Pass neither flag and let the YAML decide.
- **Headless.** `build_agent` doesn't consult `OPENAI_BASE_URL` / `LLM_MODEL` (`agent.py:400-404`).

## Q3. `harness-ask-your-docs` CLI

**Flags** (`cli.py:51-73`):

| Flag | Effect |
|---|---|
| `--workspace DIR` | Sets `PYDOCS_WORKSPACE` |
| `--model` | Sets `LLM_MODEL` |
| `--base-url` | Sets `OPENAI_BASE_URL` |
| `--config` | Sets `PYDOCS_CONFIG` |
| `--port` | Default 8501 (`:30`) |
| `-- <streamlit args>` | The leading `--` is stripped (`:85`) and the rest follow `app.py`. Streamlit parses them as its own options (**VERIFIED**, streamlit 1.59.1). Documented at `examples/harness/ask_your_docs_agent/README.md:128-129`. |

**How the launch works.**
- It runs `sys.executable -m streamlit run --server.port N <theme flags> app.py …` (`cli.py:87-100`).
- The sidebar pre-fills Workspace and config from those variables (`app.py:270-273`).
- If you clear the config field, both halves lose the YAML:
  - The serve child starts without `--config` and falls back to fastembed/bge-small at 384 dimensions, so the check fails with `EmbedderMismatchError` (`multirepo.py:162-181`).
  - The chat side falls back to `gpt-4o-mini` on api.openai.com (`llm_connection.py:134-135`).

**How the workspace finds bundles.**
- The server globs `*.db` directly under the directory, not recursively, and skips `pydocs-links.*` (`multirepo.py:145-159`). The UI catalog globs `*.db` without that skip (`catalog.py:29-30`).
- Bundle files are named `{dirname}_{md5(abspath)[:10]}.db`, with a `.tq` beside each (`db.py:279-289`).
- The project name comes from `index_metadata.project_name`, falling back to the filename minus its slug (`multirepo.py:98-101,141`). If two bundles share a name, the newest `indexed_at` wins (`catalog.py:32-42`).

**How it starts the search server.**
- The command is `[sys.executable, -m, pydocs_mcp, --config, CFG, serve, --workspace, WS]` (`agent.py:278-282`). `--config` is included only when the field is set.
- No environment beyond the minimal set is passed (§0.1).
- A fresh serve child starts for every tool call (`binding.py:24-27`; **VERIFIED**: `MCP ready` is logged once per call). So the key is needed on every spawn, and the query-embedding cache doesn't carry over between calls.
- Each child loads the bundles, checks the embedder (`server.py:445-449`), then builds it (`:454`).
- `serve --workspace` does no indexing and no watching (`__main__.py:1281-1290`).

## Q4. Keeping the new index separate

**Where bundles go.**
- Precedence: `--cache-dir DIR` over `PYDOCS_CACHE_DIR` over `~/.pydocs-mcp` (`__main__.py:86-93`; `db.py:29,35,261-276`, which also expands `~`).
- `--cache-dir` keeps the `{dirname}_{hash}` filename, and the `.tq` lands beside the `.db` (`__main__.py:605-617`). Either setting alone isolates `index`; `--cache-dir` wins when both are set.
- `AppConfig.cache_dir` exists (`app_config.py:98`) but nothing reads it (`default_config.yaml:3-7`), so a YAML `cache_dir:` key does nothing.
- CLI queries with `--workspace` never touch the cache root (`__main__.py:933-937`). Serve children don't see `PYDOCS_CACHE_DIR`, but with one bundle they write nothing under `~/.pydocs-mcp` (Correction 6).

**What goes wrong if you don't isolate.**
- **Default-cache index.** It rewrites `~/.pydocs-mcp/example_needle_5383c5f58b.db`. That file is currently **v9** (measured). 0.6.0 would migrate it to v16 and re-embed with the new model. The demo's 0.3.1 build would then drop and rebuild it on its next serve.
- **Workspace pointed at `~/pydocs-index`.**
  - Those bundles are v15 with an **empty** `index_metadata`, so their embedder can't be checked (`storage/index_metadata.py:54-68`).
  - 0.6.0 opens each one through `open_index_database` (`multirepo.py:131`), which migrates it to v16 **in place, before** the embedder check (`multirepo.py:166-181`).
  - The v15 dev build that serves them today then refuses them with `FutureSchemaError`. 0.5.1, at v14, already refuses v15.
- **Auto-loaded config files.** `PYDOCS_CONFIG_PATH`, `./pydocs-mcp.yaml` and `~/.config/pydocs-mcp/config.yaml` are loaded only when no `--config` or explicit path is given (`app_config.py:462-484`). The main case is the UI with its config field cleared. Don't name your config after them.
- **Cross-repo links overlay.** Only built with 2 or more bundles (`server.py:315`).

## Q5. How example_needle is indexed, and `--skip-deps`

**The demo's own invocation.**
- `needle-demo index` runs `pydocs-mcp --config .continue/pydocs-mcp.yaml index <root> --skip-deps --no-inspect` (`example_needle/src/needle_demo/cli.py:221-229`).
- Serve uses the same flags (`cli.py:52-69`; `.continue/mcpServers/needle-docs.yaml:9-16`; `docs/continue-demo.md:90-95`).
- The demo config sets only `embedding` (`.continue/pydocs-mcp.yaml:4-8`). There's no `--depth` and no `include_extensions` override.

**Use `--skip-deps`.**
- Dependency names come from the manifests (`deps.py:189-208`). They're resolved against the **running interpreter's** installed packages (`_dep_helpers.py:64-80`), and any that aren't installed are skipped.
- A fresh venv would index unrelated versions of numpy and others, and you'd pay to embed them.
- Keep `--no-inspect` for parity with the demo.

**Size (with `.claude` excluded).**
- 164 files in scope: 113 `.py`, 32 `.md`, 5 `.yaml`, 5 `.yml`, 5 `.txt` (all under `src/example_needle.egg-info/`), 3 `.toml`, 1 `.ini`; about 392 KB.
- **VERIFIED** result:
  - 1343 chunks: python_def 885, markdown_section 356, text_section 69, decision_record 33.
  - 163 source files produced chunks (the 4 egg-info `.txt` files give 5 chunks). 391,377 characters, roughly 100k tokens, about **$0.002**.
  - 42 embedding requests, 70 s wall.
- Without the `.claude` exclusion, about 179 more files come in as a duplicate copy of the code.

## Q6. Gotchas

- **Streamlit file watcher.**
  - The fix exists only as local commit a5c748a on the unpushed branch `fix/ask-your-docs-file-watcher-default`. No remote branch contains it.
  - On origin/main, pass `-- --server.fileWatcherType none --server.headless true` (**VERIFIED** parsing), or set `STREAMLIT_SERVER_FILE_WATCHER_TYPE=none`.
  - Allowed values are auto, watchdog, poll and none (streamlit 1.59.1 `config.py:823-838`).
- **TLS on macOS.**
  - httpx 0.28.1 uses `SSL_CERT_FILE` / `SSL_CERT_DIR` if set, and certifi otherwise (`httpx/_config.py:33-40`). All probes from this Mac worked without them.
  - Serve children don't inherit them, so if you need one, add it to the `.pth` or to `EXTRA_CHILD_ENV`.
- **OpenRouter limits.** Requests are sequential, 32 inputs at most, about one per second; no 429s seen. Nothing is configurable.
- **Reasoning output** (**VERIFIED** earlier with langchain-openai 1.1.9).
  - A tool turn returns `content == ''` plus `tool_calls`.
  - Reasoning tokens are billed (about 34 per turn at $3/M output), but the reasoning text is dropped.
  - The final answer starts with `"\n\n"`.
  - There's no `<think>` stripping and no reasoning-effort setting (`llm_connection.py:412-424`).
- **Tool calling.** `text_react` is `create_react_agent(llm, tools, prompt=…)` (`text_react.py:32-34`). qwen3.8-27b supports `tools` (checked again): 1M context, $0.42/M input.
- **Python and uv on this Mac.** `python3` on PATH is 3.8.8. The first `uv` on PATH is anaconda's; use `~/.local/bin/uv` (arm64 0.11.28).
- **Not pinned.** pip doesn't use `uv.lock`, so check the mcp inherited-environment list after installing (Step 1).

## Q7. Headless checks

- The query subcommands accept `--workspace` and `--cache-dir` (`__main__.py:136-149`). `search_codebase` and `get_symbol` have aliases `search` and `symbol` (`:299,340`), and `--depth` takes `summary|tree|source`.
- One-shot script using the product's own `build_agent` / `reformulate` / `ask` with `subprocess_env`: Step 6 (**VERIFIED**).
- `make_harness_runner` (`binding.py:492-499`) is not a CLI.
  - It needs `workspace`, `model` and `trace_root` (`binding.py:175-188`), and each sample needs `record_id`, `task_name`, `rendered_prompt` and `gold`.
  - Its serve child receives only the trace variables (`binding.py:323-340`), so it also needs the `.pth`.
- `streamlit.testing.v1.AppTest` hooks exist (`app.py:7-10`). It also spawns serve children, so it needs the `.pth` too. Not verified.

---

## Steps

### Step 0. Clean the shell, load only the OpenRouter key, take a snapshot

Stop the Continue demo first, so its own writes don't show up in the comparison.

```bash
unset OPENAI_BASE_URL LLM_MODEL PYDOCS_CONFIG_PATH PYDOCS_WORKSPACE PYDOCS_CONFIG PYDOCS_CACHE_DIR
env | cut -d= -f1 | grep -iE '^(PYDOCS_|STREAMLIT_)' ; echo "(expect no names)"
export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' /Users/msobroza/Projects/pyctx7-mcp/.env | cut -d= -f2-)"
test -n "$OPENROUTER_API_KEY" && echo key-loaded
ls -laT ~/.pydocs-mcp ~/pydocs-index ~/.pydocs-mcp/links 2>/dev/null > /tmp/ayd-before.txt   # isolation check in Step 8
```

The `.env` has no `export` lines and the key is unquoted, which is why this doesn't use `set -a; source`.

### Step 1. Fresh venv

```bash
~/.local/bin/uv venv --python cpython-3.12-macos-aarch64-none ~/venvs/ayd-openrouter
source ~/venvs/ayd-openrouter/bin/activate
~/.local/bin/uv pip install "pydocs-mcp[harness-ask-your-docs]==0.6.0"
# Until 0.6.0 is on PyPI (the repo is public; maturin builds the Rust extension):
# export PATH="$HOME/.cargo/bin:$PATH"
# ~/.local/bin/uv pip install "pydocs-mcp[harness-ask-your-docs] @ git+https://github.com/msobroza/pydocs-mcp@f6943a87e32fb6eecc01e75fe00ee4e8dc176fd5"
python -c "import importlib.metadata as m, pydocs_mcp.db as d; print(m.version('pydocs-mcp'), d.SCHEMA_VERSION)"
which pydocs-mcp harness-ask-your-docs
python -c "import mcp.client.stdio as s; print(s.DEFAULT_INHERITED_ENV_VARS)"
```

Expected:
- `0.6.0 16` from PyPI, or `0.5.1 16` from the git install.
- Both commands are found.
- `['HOME', 'LOGNAME', 'PATH', 'SHELL', 'TERM', 'USER']`, which confirms the workaround in 2c is needed.

### Step 2. Create the files

**a) Directories**

```bash
mkdir -p ~/pydocs-openrouter/index
```

**b) `~/pydocs-openrouter/config.yaml`** (**VERIFIED** content)

```yaml
embedding:
  provider: openai
  model_name: qwen/qwen3-embedding-4b
  dim: 2560
  base_url: https://openrouter.ai/api/v1
  api_key_env: OPENROUTER_API_KEY
  send_dimensions: false
  batch_size: 32
extraction:
  discovery:
    project:
      exclude_dirs: [".claude"]     # adds to the built-in exclusion list; a bare name matches at any depth (default_config.yaml:58-61)
      # Optional: [".claude", "example_needle.egg-info"] drops 5 build-metadata chunks
      # (expect 1338 chunks, text_section 64 in Step 4; computed, not run).
ask_your_docs:
  architecture: text_react
  llm:
    base_url: https://openrouter.ai/api/v1
    model: qwen/qwen3.8-27b
    auth:
      api_key_env: OPENROUTER_API_KEY
    vision: false
```

**c) Key loader for the UI's serve children.** Remove it when you're done testing.

```bash
SITE=$(~/venvs/ayd-openrouter/bin/python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
cat > "$SITE/zz_openrouter_env.pth" <<'EOF'
import os, pathlib; _p = pathlib.Path("/Users/msobroza/Projects/pyctx7-mcp/.env"); _v = [l.split("=", 1)[1].strip().strip("\"'") for l in (_p.read_text().splitlines() if _p.exists() else []) if l.startswith("OPENROUTER_API_KEY=")]; _v and os.environ.setdefault("OPENROUTER_API_KEY", _v[0])
EOF
env -i HOME="$HOME" PATH="$PATH" ~/venvs/ayd-openrouter/bin/python -c "import os; print(bool(os.environ.get('OPENROUTER_API_KEY')))"
```

Expected: `True`. The one-liner was **VERIFIED** under `env -i` on cpython 3.12.12.

**d) `~/pydocs-openrouter/ask_once.py`** (**VERIFIED**)

```python
"""One-shot, headless ask-your-docs question (no Streamlit).
Usage: python ask_once.py WORKSPACE CONFIG "question" [--no-child-key]
EXTRA_CHILD_ENV (comma-separated names) forwards more vars to the serve child, e.g. SSL_CERT_FILE.
"""
from __future__ import annotations
import asyncio, os, sys
from pathlib import Path
from pydocs_mcp.harness.ask_your_docs.agent import ask, build_agent
from pydocs_mcp.harness.ask_your_docs.reformulation import reformulate
from pydocs_mcp.retrieval.config.app_config import AppConfig

def _child_env(forward_key: bool) -> dict[str, str]:
    names = ["OPENROUTER_API_KEY"] if forward_key else []
    names += [n for n in os.environ.get("EXTRA_CHILD_ENV", "").split(",") if n]
    return {n: os.environ[n] for n in names if n in os.environ}

async def main(workspace: str, config: str, question: str, forward_key: bool) -> None:
    ayd = AppConfig.load(explicit_path=Path(config)).ask_your_docs
    agent, llm = await build_agent(workspace, None, pydocs_config=config, config=ayd,
                                   subprocess_env=_child_env(forward_key))
    history: list = []
    standalone = await reformulate(llm, history, question)
    print("=== ANSWER ===")
    print(await ask(agent, history, standalone, scope={"project": "example_needle"}))

if __name__ == "__main__":
    ws, cfg, q = sys.argv[1:4]
    asyncio.run(main(ws, cfg, q, forward_key="--no-child-key" not in sys.argv))
```

- `config=ayd` is required. Without it, `build_agent` uses the no-block default: `gpt-4o-mini` on api.openai.com (`agent.py:338`, `ask_your_docs_models.py:23`).
- `model=None` means the YAML's model is used (`agent.py:414-419`).
- A `subprocess_env` map is merged over mcp's minimal defaults (`stdio/__init__.py:127`), so the child gets `HOME`/`PATH`/… plus the key.

**e) Check the effective config (new).** This catches misspelled nested keys, which make `load` raise. A misspelled top-level key is silently ignored and shows up only as a default value here.

```bash
python - <<'EOF'
from pathlib import Path
from pydocs_mcp.retrieval.config.app_config import AppConfig
c = AppConfig.load(explicit_path=Path("~/pydocs-openrouter/config.yaml").expanduser())
e, l = c.embedding, c.ask_your_docs.llm
print(e.provider, e.model_name, e.dim, e.base_url, e.api_key_env, e.send_dimensions)
print(c.extraction.discovery.project.exclude_dirs, c.ask_your_docs.architecture)
print(l.base_url, l.model, l.auth.api_key_env, l.vision)
EOF
```

Expected (derived from the code, not run):

```
openai qwen/qwen3-embedding-4b 2560 https://openrouter.ai/api/v1 OPENROUTER_API_KEY False
['.claude'] text_react
https://openrouter.ai/api/v1 qwen/qwen3.8-27b OPENROUTER_API_KEY False
```

### Step 3. Build the index

```bash
cd ~
pydocs-mcp --config ~/pydocs-openrouter/config.yaml index /Users/msobroza/Projects/example_needle \
  --skip-deps --no-inspect --cache-dir ~/pydocs-openrouter/index
```

`--config` must come before the subcommand (`__main__.py:69-74`). `--cache-dir` alone isolates this command; exporting `PYDOCS_CACHE_DIR` as well is redundant.

Expected output (**VERIFIED** run, `index.log`):
- `Engine: Python` in the verified run; a Rust-built wheel prints `Rust` (`__main__.py:596-603`).
- `Project: /Users/msobroza/Projects/example_needle (mode=static)` (`:658`)
- `SearchBackend=SqliteCompositeBackend: lexical✓ dense✓ multi✗ hybrid✗ graph✓`
- About 42 lines of `HTTP Request: POST https://openrouter.ai/api/v1/embeddings "HTTP/1.1 200 OK"`
- `Project: 1343 chunks, 390 symbols, 164 trees`
- `Done: 0 indexed, 0 cached, 0 failed (db: 3644 KB)` (`:699-705`). In this project-only run the counters print 0 (observed); the project totals are on the `Project:` line.
- About 70 s total.
- Files: `~/pydocs-openrouter/index/example_needle_5383c5f58b.db` (about 3.6 MB) and `.tq` (about 1.7 MB).

### Step 4. Check the bundle (read-only)

```bash
python - <<'EOF'
import sqlite3, pathlib
p = pathlib.Path("~/pydocs-openrouter/index/example_needle_5383c5f58b.db").expanduser()
c = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
print(c.execute("PRAGMA user_version").fetchone(), c.execute("select project_name, embedding_provider, embedding_model, embedding_dim from index_metadata").fetchall())
print(c.execute("select origin, count(*) from chunks group by origin order by 2 desc").fetchall())
print("claude:", c.execute("select count(*) from chunks where source_path like '.claude%'").fetchone())
EOF
```

Expected (reproduced read-only against the verified bundle):

```
(16,) [('example_needle', 'openai', 'qwen/qwen3-embedding-4b', 2560)]
[('python_def', 885), ('markdown_section', 356), ('text_section', 69), ('decision_record', 33)]
claude: (0,)
```

### Step 5. CLI search against the new workspace

```bash
pydocs-mcp --config ~/pydocs-openrouter/config.yaml search_codebase "late interaction MaxSim scoring" --workspace ~/pydocs-openrouter/index
```

Expected (**VERIFIED**):
- One embeddings POST.
- The header `[index: 9c170b0 · 0d old · 1 packages]`, where 9c170b0 is example_needle's HEAD at index time.
- First hit `## class MaxSimScorer` with the pointer `pydocs-mcp symbol needle.scoring.strategies.MaxSimScorer`, followed by `ScoringStrategy` and `get_scorer`.

Optional, not run: `pydocs-mcp --config ~/pydocs-openrouter/config.yaml get_symbol needle.scoring.strategies.MaxSimScorer --depth source --workspace ~/pydocs-openrouter/index`

### Step 6. Headless agent

```bash
python ~/pydocs-openrouter/ask_once.py ~/pydocs-openrouter/index ~/pydocs-openrouter/config.yaml \
  "Which class implements late-interaction (MaxSim) scoring, and what does its score method return?"
```

Expected (**VERIFIED**):
- stderr shows `MCP ready (1 project(s): example_needle)` (`server.py:601`) several times: once to list the tools, then once per tool call.
- The answer names `MaxSimScorer` in `needle.scoring.strategies` (`src/needle/scoring/strategies.py:39`) and shows `score(...) -> float` returning `float((query @ document.T).max(axis=1).sum())`.

To reproduce the UI's failure, add `--no-child-key`. You'll get `OpenAIEmbedder requires the OPENROUTER_API_KEY environment variable…` followed by `McpError: Connection closed`.

### Step 7. Streamlit UI

This needs the `.pth` from Step 2c.

```bash
harness-ask-your-docs --workspace ~/pydocs-openrouter/index --config ~/pydocs-openrouter/config.yaml --port 8502 \
  -- --server.fileWatcherType none --server.headless true
```

Open `http://localhost:8502`. What to expect (derived from the code, not rendered):
- **Status line:** `openrouter.ai · qwen/qwen3.8-27b · $OPENROUTER_API_KEY set · vision: no (configured)`.
- **Sidebar:**
  - Workspace and config pre-filled.
  - **Project** selectbox: `All projects` / `example_needle`.
  - **Code** radio: All code / Own code / Dependencies.
  - **No Package picker.**
- **First question:** pick `example_needle`, or leave All projects, and ask the Step 6 question. You should get the same answer.
- **Without the `.pth`:** the status line still reads `set`, because the parent has the key. The question fails with a redacted caption, and the terminal shows the `OpenAIEmbedder` error from the serve child.

### Step 8. Clean up and check isolation

```bash
rm "$SITE/zz_openrouter_env.pth"
ls -laT ~/.pydocs-mcp ~/pydocs-index ~/.pydocs-mcp/links 2>/dev/null | diff /tmp/ayd-before.txt - && echo demo-bundles-untouched
```

---

## Couldn't confirm

1. What the 0.6.0 PyPI wheel will contain. It isn't published, and f6943a8 declares 0.5.1.
2. That the git+https build completes on a fresh resolve. The repo is public and the commit is reachable (checked); the Rust build and dependency resolve weren't run. The verified runs used the pure-Python engine and the pyctx7 dev venv.
3. The full Streamlit flow with the `.pth`. The pieces were verified separately, and the status-line and sidebar text above is derived from code.
4. mcp 1.28.1 (the locked version), and newer adapter releases. Only 1.27.1 and 0.3.0 were read; use the Step 1 check.
5. `AppTest` behaviour with a `chat_input` that accepts files.
6. OpenRouter embedding rate limits for this account. 42 sequential requests produced no 429s.
7. Whether per-provider routing changes tool or reasoning behaviour for qwen3.8-27b.
8. Why `Done:` reports 0 in project-only runs (observed, not traced), and whether `--workers` parallelizes anything with `--skip-deps` (passed to the orchestrator, not traced).
9. Whether `--no-inspect` has any effect together with `--skip-deps`. Kept for parity with the demo.
10. `make_harness_runner` end to end. Not run.
11. Git side effects of `git.enabled: auto` (`default_config.yaml:233-234`) on example_needle. The original run set `GIT_OPTIONAL_LOCKS=0`.
12. The Step 2e output and the optional egg-info chunk counts. Derived, not run.

Files are in the session scratchpad at `/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/`:
- `cfg/pydocs-openrouter.yaml`
- `ask_once.py`
- `or-index/example_needle_5383c5f58b.{db,tq}` (the verified v16 bundle)
- `index.log`
- `src/` (the origin/main files I dumped for this verification)