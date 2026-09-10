# Final fix design: environment for the ask-your-docs serve child (target 0.6.1)

**Baseline correction.** origin/main is now `2a5592a chore(release): v0.6.0 (#235)`, one commit on top of `f6943a8`. That commit touches only `CHANGELOG.md`, `Cargo.toml`, `Cargo.lock`, `pyproject.toml`, `uv.lock` and `tests/test_pyproject_extras.py`, so no harness code changed. Tag `v0.6.0` exists, pyproject says `0.6.0`, and CHANGELOG reads `## [0.6.0] — 2026-09-10`. **0.6.1 is the correct target.** Critic 2's point that 0.6.0 has not shipped is out of date.

## 1. Root cause

- **One place builds the connection:** `serve_connection` in `python/pydocs_mcp/harness/ask_your_docs/agent.py:264-290`. It adds an `env` key only when `subprocess_env` is given.
  - The UI (`app._build_page_agent` → `build_agent`, app.py:196-207) passes no env.
  - The eval binding (`binding._serve_session_tools`, binding.py:336) passes only the three `PYDOCS_TRACE__*` variables.
- **What the child receives:**
  - langchain-mcp-adapters 0.3.2 expands `${VAR}` in env values and passes `env` on unchanged (`sessions.py:248-256`).
  - mcp's `stdio_client` starts the child with `{**get_default_environment(), **server.env}` (`mcp/client/stdio/__init__.py:127`). On POSIX that default is `HOME LOGNAME PATH SHELL TERM USER`, never `os.environ`.
- **The crash:** `pydocs_mcp serve` builds the embedder at startup (server.py:454). `OpenAIEmbedder.__post_init__` (`extraction/strategies/embedders/openai.py:43-51`) can't find `embedding.api_key_env` or `OPENAI_API_KEY`, raises `RuntimeError`, and the child exits before the handshake. The client sees `McpError: Connection closed`.
- **The same stripping loses more than the key:**
  - `TMPDIR`: the default FastEmbed cache on macOS. The proposer reproduced the effect; I did not re-run it.
  - `PYDOCS_CONFIG_PATH` and other `PYDOCS_*` overlays.
  - `PYDOCS_CACHE_DIR`, even though `db.py:18-23` assumes children inherit it.
  - Proxy and CA-bundle variables.
- **Why the minimal env exists:** the SDK's own default, which the comments at agent.py:283-287, binding.py:19-22 and trace_env.py:10-12 describe as a limitation. It was never a design choice.

## 2. Chosen approach and why

One helper, two policies, plus a separate channel for the launcher's flags.

| Path | Policy | What the child gets |
|---|---|---|
| UI, and headless `build_agent` without `mcp_tools` | **Shell parity** | The parent `os.environ`, with three exceptions: every spelling of `PYDOCS_TRACE*`, exported shell functions, and values containing `${...}`. The child behaves like `pydocs-mcp serve` run in the launching shell. |
| Eval binding (`_serve_session_tools`) | **Sealed config tier** | Shell parity, minus every inherited `PYDOCS_*` except `PYDOCS_CACHE_DIR` (case-insensitive), and minus `OPENAI_BASE_URL` / `LLM_MODEL`. Then the trace overlay. |
| Launcher `--base-url` / `--model` | **Private channel** | They are no longer written to `OPENAI_BASE_URL` / `LLM_MODEL`. The app reads them from `HARNESS_ASK_YOUR_DOCS_BASE_URL` / `HARNESS_ASK_YOUR_DOCS_MODEL` as the CLI tier of spec R3. |

**Why inherit instead of an allowlist (proposal options B, C and D):**
- The set a serve child legitimately needs is open-ended: every provider's key variable, `HF_*` / `FASTEMBED_*`, `SSL_*` / `*_PROXY`, `PYDOCS_*`.
- The child is our own code, run by the same interpreter as the same user.
- An allowlist needs a config load at build time and drifts one bug report at a time.

**Why seal the config tier in eval** (critic 1 #3 and #4):
- It keeps main's semantics exactly: the child's configuration comes from `--config` plus config files only. So no arm that ran successfully before changes what it measures, and no arm hash, ledger or Trajectory change is needed.
- It needs **no owner ratification**. It enforces the rule the binding already documents ("a shell must not re-point an arm's endpoint", binding.py:405-411) and does not extend the 2026-09-10 ruling to the child.
- What changes: runs that used to crash (API-key providers) or re-download models (`TMPDIR`) now work.

**Why a private launcher channel, not a restore marker** (critic 1 #1, critic 2 #4):
- The app already passes `os.environ` as the environment tier and an empty `ConnectionOverride()` as the launch tier (app.py:124). Today the flags only win by overwriting `OPENAI_BASE_URL`.
- Putting them in the launch tier is how spec R3 defines precedence (YAML < env < CLI < dialog), and the resolved chat endpoint stays the same.
- No original values are stashed in the environment.
- The serve child then sees exactly the user's shell. The only extras are the launcher's `PYDOCS_WORKSPACE` / `PYDOCS_CONFIG` and the two private names. I checked `AppConfig` (`extra="ignore"`): it has no `workspace` or `config` field, so none of these are read.

### Critic issues and what happens to each

| Issue | What happens |
|---|---|
| C1#1 / C2#4: launcher `OPENAI_BASE_URL` leaks into the child | **Fixed in this change** via the private channel (§3.5, §3.6). |
| C1#2 / C2#1: trace withholding bypassed by case or JSON spelling | **Fixed:** anything whose upper-case name is `PYDOCS_TRACE` or starts with `PYDOCS_TRACE__` is withheld. Inherited keys that match an overlay key case-insensitively are dropped, so the overlay is the only spelling. |
| C1#3: arm identity | **Resolved by sealing,** not by a digest. No identity change and no ratification needed. |
| C1#4: endpoint policy | **Option (a):** eval withholds `OPENAI_BASE_URL` / `LLM_MODEL`; set arm endpoints with `embedding.base_url`. See the caveat in §8. |
| C1#5: HF offline flags make the env order-dependent | **Not applied.** The only in-process `os.environ` writes are `enable_hf_offline`'s two `setdefault` calls. The campaign index passes the cache root as `--cache-dir` argv (`index_cache.py:103-118`). A per-process snapshot would also carry a stale `PYDOCS_CACHE_DIR` across tests, because conftest's `_isolate_bundle_cache_dir` re-points it for every test. Kept as a conditional residual risk (§8). |
| C1#6: `${...}` side effects | **Partly applied.** `BASH_FUNC_*` names and `()`-prefixed values (which the SDK itself skips) are dropped quietly at DEBUG. Other braced values: dropped with one names-only WARNING. Showing the reason in the UI is **deferred**, because the failure surfaces at the first tool call inside the agent loop, not at build. |
| C1#7: G8 regression test | **Applied** (tests 17 and 22). |
| C2#2: CI would skip the regressions | **Applied.** The policy goes in a core, mypy-checked, coverage-counted module (`harness/core/`). `serve_connection` moves to a langchain-free module. The real-spawn tests use `mcp.client.stdio`, which is a core dependency. |
| C2#3: bool assertion shape | **Applied.** The probe tool returns `"present"` / `"absent"` text. There is a control test proving the SDK default gives `"absent"`. |
| C2#5: CHANGELOG target | **Refuted** (see the baseline correction). |
| C2#6: warning noise | **Applied.** The proposal's binding-side `PYDOCS_*` warning is dropped, because overlays no longer reach the eval child. The core log lists withheld names only; `PYDOCS_CACHE_DIR` is kept, so it never appears. The log fires once per distinct set per process. |

## 3. Exact code changes

### 3.1 New: `/Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/harness/core/serve_child_env.py`
About 110 lines. Imports only the standard library plus `pydocs_mcp.observability.trace_env` and `pydocs_mcp.db`. The one-way rule holds: core imports no concrete harness.

```python
NO_ENV_OVERLAY: Mapping[str, str] = MappingProxyType({})          # Null object, never None
_TRACE_SECTION_ENV_VAR = TRACE_ENABLED_ENV_VAR.rsplit("__", 1)[0]  # "PYDOCS_TRACE", derived
_CONFIG_TIER_PREFIX = "PYDOCS_"                                    # == AppConfig env_prefix (parity test)
_SEALED_TIER_KEEPS = frozenset({CACHE_DIR_ENV_VAR})                # from pydocs_mcp.db
_ENDPOINT_TIER_ENV_VARS = frozenset({"OPENAI_BASE_URL", "LLM_MODEL"})
_ADAPTER_EXPANDED_REF = re.compile(r"\$\{[^}]+\}")                 # mirrors adapter _BRACED_VAR_RE
_SHELL_FUNCTION_VALUE_PREFIX = "()"
_SHELL_FUNCTION_NAME_PREFIX = "BASH_FUNC_"
_WITHHELD_EVENT = "serve_child_env_withheld"
_REASON_SHELL_FUNCTION, _REASON_OVERLAID = "shell_function", "overlaid"          # quiet (DEBUG)
_REASON_TRACE_IDENTITY, _REASON_ADAPTER_REF = "trace_identity", "adapter_expanded_ref"
_REASON_ENDPOINT_SEALED, _REASON_CONFIG_SEALED = "endpoint_tier_sealed", "config_tier_sealed"
_QUIET_REASONS = frozenset({_REASON_SHELL_FUNCTION, _REASON_OVERLAID})
log = logging.getLogger("pydocs-mcp.harness.serve-child-env")

def serve_child_env(overlay: Mapping[str, str] = NO_ENV_OVERLAY, *,
                    environ: Mapping[str, str] = os.environ,
                    seal_config_tier: bool = False) -> dict[str, str]:
    """Parent env for OUR serve child, overlay on top (docstring + doctest below)."""
    overlaid = frozenset(name.upper() for name in overlay)
    kept: dict[str, str] = {}
    withheld: dict[str, list[str]] = {}
    for name, value in environ.items():
        reason = _withhold_reason(name, value, overlaid=overlaid, seal_config_tier=seal_config_tier)
        if reason is None:
            kept[name] = value
        else:
            withheld.setdefault(reason, []).append(name)
    _log_withheld_once(seal_config_tier, _frozen_withheld(withheld))
    return {**kept, **overlay}

def _withhold_reason(name, value, *, overlaid, seal_config_tier) -> str | None:
    # order: shell_function -> overlaid -> trace_identity -> (sealed tiers) -> adapter_expanded_ref
def _sealed_tier_reason(upper: str) -> str | None:       # endpoint pair, then PYDOCS_* minus keeps
def _frozen_withheld(withheld) -> tuple[tuple[str, tuple[str, ...]], ...]:   # sorted, hashable
@functools.cache
def _log_withheld_once(sealed: bool, withheld: tuple[...]) -> None:
    # no-op if empty; one JSON line {"event", "sealed_config_tier", "withheld": {reason: [names]}}
    # WARNING if any reason not in _QUIET_REASONS, else DEBUG; NAMES ONLY
def clear_serve_child_env_log_memo() -> None:             # test seam
```

Docstring example for `serve_child_env` (G8-safe dummy values):
```
>>> serve_child_env({"PYDOCS_TRACE__DIR": "/t"},
...                 environ={"OPENROUTER_API_KEY": "k", "pydocs_trace__dir": "/old"})
{'OPENROUTER_API_KEY': 'k', 'PYDOCS_TRACE__DIR': '/t'}
```

Constraints:
- Every function stays within 4–20 lines, with at most 2 nesting levels. Cognitive complexity of `_withhold_reason` is about 7, under complexipy's 15.
- The result is always a fresh dict. `environ` is never mutated.

### 3.2 New: `/Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/harness/ask_your_docs/serve_spawn.py`
About 45 lines, langchain-free. The package `__init__` is lazy, so this is core-importable. It holds the old `serve_connection`, moved out of agent.py:

```python
def serve_connection(workspace: str, pydocs_config: str | None = None,
                     pydocs_cmd: list[str] | None = None,
                     subprocess_env: Mapping[str, str] = NO_ENV_OVERLAY, *,
                     seal_config_tier: bool = False) -> dict:
```
- argv logic is unchanged (`--config` before `serve --workspace`).
- It **always** sets `"env": serve_child_env(subprocess_env, seal_config_tier=seal_config_tier)`.
- Keep the old docstring's "single source of the serve argv shape" sentence, and add the env rules.

### 3.3 `/Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/harness/ask_your_docs/agent.py`
- Delete the `serve_connection` definition (lines 264-290). Add `from pydocs_mcp.harness.ask_your_docs.serve_spawn import serve_connection`.
  - It is used by `build_agent`, so it is not a pure re-export. `binding` and the AC-42 test (`monkeypatch.setattr(agent_mod, "serve_connection", ...)`) keep working, because `build_agent` calls the module-global name.
- `build_agent`: change `subprocess_env: dict[str, str] | None = None` to `subprocess_env: Mapping[str, str] = NO_ENV_OVERLAY`. The signature is HARNESS-PRIVATE, and `test_build_agent_signature_keeps_..._seams` pins only `prompts`, `connection`, `bearer` and `vision_capabilities`. The call at line 349 stays as it is.
- In `build_agent`'s docstring, replace "All defaults together reproduce the pre-stage-2 build byte-for-byte" with: "All defaults together reproduce the pre-stage-2 build byte-for-byte except the serve child's environment, which since 0.6.1 always inherits the parent's (`harness.core.serve_child_env`). That is identical for every arm, so arms still differ only by these keywords."
- Net line change is about −20 (489 → about 470).

### 3.4 `/Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/harness/ask_your_docs/binding.py` (499 lines, **net change must be ≤ 0**)
- In `_serve_session_tools`, keep the existing import line (`from ...agent import _intercept, serve_connection`) and change the call:
  ```python
  # WHY sealed: settings-in, trajectory-out — the child inherits the embedder key, TMPDIR,
  # proxies and CA bundles, never a shell's PYDOCS_*/OPENAI_BASE_URL (harness.core.serve_child_env).
  connection = serve_connection(
      settings.workspace, settings.pydocs_config, subprocess_env=trace_env, seal_config_tier=True
  )
  ```
- Rewrite module docstring lines 19-23 in 3 lines (saves 2): "Trace lifecycle: the ADR 0009 env channel rides the serve connection's env map over a SEALED inherited environment (`harness.core.serve_child_env`); the per-trajectory directory and candidate skill persist under `trace_root`."
- In `connection_block_for_binding`'s docstring, replace "that child starts from a minimal environment" with "that child's environment tier is sealed (`serve_child_env`)". Same line count.
- No new binding-side warning helper. The core log replaces the proposal's `_warn_if_env_overlays_the_serve_child`.

### 3.5 `/Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/harness/ask_your_docs/cli.py`
- Add public constants `LAUNCH_BASE_URL_ENV_VAR = "HARNESS_ASK_YOUR_DOCS_BASE_URL"` and `LAUNCH_MODEL_ENV_VAR = "HARNESS_ASK_YOUR_DOCS_MODEL"`, plus `_LAUNCHER_OWNED = (LAUNCH_BASE_URL_ENV_VAR, LAUNCH_MODEL_ENV_VAR)`.
- `_ENV`: map `"model"` to `LAUNCH_MODEL_ENV_VAR` and `"base_url"` to `LAUNCH_BASE_URL_ENV_VAR`. `workspace` and `config` stay as they are.
- `main()`: after `env = os.environ.copy()`, run `for var in _LAUNCHER_OWNED: env.pop(var, None)` so a stale shell export can't pose as a flag, then the existing flag loop.
- Help strings stay accurate. Imports stay light: no httpx, streamlit or langgraph.

### 3.6 `/Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/harness/ask_your_docs/app.py` (492 lines, stays < 500)
- Add `from pydocs_mcp.harness.ask_your_docs.cli import LAUNCH_BASE_URL_ENV_VAR, LAUNCH_MODEL_ENV_VAR`.
- `resolve_connection` (line 121):
  ```python
  """The page's connection: YAML < environment < the launcher's flags < dialog (spec R3)."""
  launch = ConnectionOverride(
      os.environ.get(LAUNCH_BASE_URL_ENV_VAR), os.environ.get(LAUNCH_MODEL_ENV_VAR)
  )
  return resolve_llm_connection(load_ayd_config(config).llm, os.environ, launch, dialog, config_path=config)
  ```
- Module docstring line 6: replace "(copied into the environment by the CLI)" with "(forwarded by the CLI under private `HARNESS_ASK_YOUR_DOCS_*` names that the serve child never reads)".
- The chat endpoint and model are unchanged. The only visible difference is that the resolution log's tier label reads "launch" instead of "environment".

### 3.7 Doc comments
- `/Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/observability/trace_env.py`: replace the WHY paragraph at lines 10-12 (text in §4).
- `/Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/db.py:18-23`: after "Every such child inherits `os.environ`", add "(the ask-your-docs serve child through `harness.core.serve_child_env`, which keeps `PYDOCS_CACHE_DIR` even when it seals the config tier)".
- Optional: in `/Users/msobroza/Projects/pyctx7-mcp/examples/harness/ask_your_docs_agent/README.md` after line 113, add: "The pydocs-mcp server the UI starts sees the environment of the shell you launched from, minus trace variables." The README audit grep must still pass.

### 3.8 `/Users/msobroza/Projects/pyctx7-mcp/scripts/validate_traced_run.py`
The script is broken on main: it imports the removed `_trace_subprocess_env` from binding. Change line 31 to `from pydocs_mcp.observability.trace_env import trace_subprocess_env`, and rename the call at line 42.

### 3.9 Test infrastructure
- `/Users/msobroza/Projects/pyctx7-mcp/tests/harness/ask_your_docs/_page_fixtures.py:70`: add both `LAUNCH_*` names to the `page_env` delenv tuple, so AppTest pages stay hermetic.
- `/Users/msobroza/Projects/pyctx7-mcp/tests/harness/ask_your_docs/_agent_fakes.py`: add `FakeMultiServerMCPClient`.
  - Records every instance's `connections` in a class-level `recorded: list` (the fixture resets it).
  - `async get_tools() -> []`.
  - `session(name)`: an async context manager yielding a `FakeMcpSession`.
  - Replace `test_agent_connection.py`'s private `_FakeMcpClient` with it.
- New, core-only: `/Users/msobroza/Projects/pyctx7-mcp/tests/harness/ask_your_docs/_stdio_probe.py`, containing:
  - `ENV_PRESENCE_SERVER: Path`
  - `sdk_spawn_env(connection) -> dict[str, str]`: `{**mcp.client.stdio.get_default_environment(), **connection.get("env", {})}`, reproducing SDK line 127.
  - `async probe_env_presence(command, args, env, name, *, timeout=30.0) -> str`: core `stdio_client(StdioServerParameters(...))` + `ClientSession.initialize()` + `call_tool("env_presence", {"name": name})`, returning `content[0].text`, bounded by `asyncio.wait_for`.
- New fixture server: `/Users/msobroza/Projects/pyctx7-mcp/tests/harness/ask_your_docs/_env_presence_server.py`. A `mcp.server.fastmcp.FastMCP` stdio server with tool `env_presence(name: str) -> str` returning `"present"` or `"absent"`. It never returns values and ignores argv. The leading underscore keeps pytest from collecting it.
- New, core-only: `/Users/msobroza/Projects/pyctx7-mcp/tests/harness/ask_your_docs/_launcher_fakes.py` with `FakeStreamlitRun`, which records `cmd` and `env` and returns `CompletedProcess(cmd, 0)`.
- `/Users/msobroza/Projects/pyctx7-mcp/tests/harness/ask_your_docs/test_module_line_budgets.py`: add `binding.py: 500` and `serve_spawn.py: 500` to `_BUDGETS`.

### 3.10 Not touched
- `harness/external/serve_config.py` and `external/harness.py`: `.mcp.json` keeps trace-only values; `tests/harness/external/test_harness.py:233` already pins that.
- The nine MCP tools, their signatures and all YAML.
- `pyproject` mypy exclude and coverage omit: the new core module is covered automatically.

## 4. WHY comments to add (verbatim)

**Module docstring of `serve_child_env.py`:**
> Environment for OUR `pydocs_mcp serve` child spawned over an in-process MCP stdio connection.
> WHY this exists (0.6.0 bug): the MCP SDK starts a stdio child from `get_default_environment()` (HOME LOGNAME PATH SHELL TERM USER on POSIX) plus the connection's `env` map, never `os.environ` (`mcp/client/stdio` `stdio_client`, mcp 1.27–1.30). With no map, the serve child lost the embedder key: `OpenAIEmbedder` raised at startup and the client saw "McpError: Connection closed". It also lost `TMPDIR` (FastEmbed's default cache), `PYDOCS_CONFIG_PATH` / `PYDOCS_CACHE_DIR`, proxies and CA bundles.
> WHY inherit rather than allowlist: the set a serve child needs is open-ended (every provider's key variable, HF_* / FASTEMBED_*, SSL_* / *_PROXY, PYDOCS_* overlays), and an allowlist drifts one bug report at a time. The child is our own code, run by the same interpreter as the same user; the SDK's allowlist protects clients that launch THIRD-PARTY servers.
> Scope: pydocs-mcp's own serve child only. Never use this for a third-party server, and never write the result to a file: the composed CLI harness's `.mcp.json` persists in the trace dir, so it keeps its trace-only map.

**At `_TRACE_SECTION_ENV_VAR`:**
> WHY the whole section, case-insensitively: pydantic-settings matches env names case-insensitively and decodes a complex field from one JSON value, so `pydocs_trace__enabled` and `PYDOCS_TRACE='{...}'` reach TraceConfig too (probed 2026-09-10). An inherited identity fails a per-tool-call spawn with TraceStartupError (no dir) or TrajectoryIdReuseError (second spawn). Only the explicit overlay (`observability.trace_env`) may set it.

**At the sealed-tier constants:**
> WHY the eval binding seals the config tier: the binding is settings-in, trajectory-out. A shell's PYDOCS_* overlay or OPENAI_BASE_URL would change what an arm measures without moving its hash. Sealed, the child's configuration is its --config YAML alone, exactly as before 0.6.1. PYDOCS_CACHE_DIR stays: it is a location, not configuration (db.py sandbox channel). OPENAI_BASE_URL / LLM_MODEL are the pair `binding._llm_connection_for_run` already blocks for the chat endpoint. The child's OpenAI SDK falls back to OPENAI_BASE_URL when YAML leaves base_url null.

**At `_ADAPTER_EXPANDED_REF`:**
> Mirrors langchain_mcp_adapters.sessions._BRACED_VAR_RE (0.3.x; pinned by test_adapter_brace_pattern_matches_ours). The adapter rewrites ${VAR} in every env value, corrupting a value that merely contains the sequence, and logs an unresolved one IN FULL at WARNING. A secret must never ride that path.

**At the shell-function constants:**
> Mirrors the SDK's own get_default_environment, which skips "()"-prefixed values; bash exports functions as BASH_FUNC_<name>%%, which a Python child never needs.

**At `_log_withheld_once`:**
> WHY memoized: the eval binding spawns once per rollout, so a 1300-record campaign would print 1300 identical warnings (the sibling of binding._llm_block_from_config_file's one-warning-per-run memo). The key holds NAMES only, never values (H4/G8).

**In `serve_spawn.serve_connection`, above the env line:**
> WHY always an env map: with none, the SDK starts the child from six variables and the child cannot build its embedder (serve_child_env). The ADR 0009 trace channel still rides this map as the overlay, never as a parent os.environ mutation, which would race concurrent runs.

**`cli.py`, above the `LAUNCH_*` constants:**
> WHY private names for --base-url / --model (0.6.1): the serve child inherits this process's environment (harness.core.serve_child_env). Written into OPENAI_BASE_URL, the CHAT endpoint re-pointed the child's OpenAI embedder and LLM client (both fall back to OPENAI_BASE_URL when YAML leaves base_url null) and sent the embedding key to the chat host. app.py reads these as the CLI tier of the LLM-connection precedence (spec R3), so the resolved chat endpoint is unchanged.

**`trace_env.py`, replacing lines 10-12:**
> WHY an explicit map and never `os.environ` mutation: a parent mutation would race concurrent runs in the same process, and the inherited environment never carries a trace identity. `harness.core.serve_child_env` withholds every inherited `PYDOCS_TRACE*` spelling, so this map is the only way one reaches the child.

## 5. CHANGELOG

Insert above `## [0.6.0] — 2026-09-10` in `/Users/msobroza/Projects/pyctx7-mcp/CHANGELOG.md`. Leave the version bump to the release commit.

```markdown
## [0.6.1] — Unreleased

### Fixed

- **`harness-ask-your-docs`: every question failed with `McpError: Connection closed`
  when the index uses an API-key embedder.** The UI started its `pydocs-mcp serve`
  child with only the MCP SDK's default variables (`HOME LOGNAME PATH SHELL TERM USER`
  on POSIX), so with `embedding.provider: openai` the child could not find its key
  (`OPENAI_API_KEY`, or the variable named by `embedding.api_key_env`) and exited
  before the handshake. The child now inherits the environment of the shell you
  launched from, so it behaves like `pydocs-mcp serve` run in that shell. That
  includes `TMPDIR` (the default FastEmbed model cache on macOS), `PYDOCS_CONFIG_PATH`
  and other `PYDOCS_*` overlays, `PYDOCS_CACHE_DIR`, and proxy and CA-bundle variables.
  Three kinds of variable are withheld: `PYDOCS_TRACE*` in any spelling (trace
  identity is set per run by the evaluation harness only), exported shell functions,
  and any value containing `${…}` (the MCP adapter would rewrite it, or log it in full
  if unresolved). A names-only JSON warning lists what was withheld.
- **`harness-ask-your-docs --base-url` / `--model` no longer write `OPENAI_BASE_URL` /
  `LLM_MODEL`.** They reach the app under private `HARNESS_ASK_YOUR_DOCS_*` names and
  take the CLI tier of the documented precedence (YAML < `OPENAI_BASE_URL` /
  `LLM_MODEL` < `--base-url` / `--model` < Connection dialog), so the chat endpoint
  and model you get are unchanged. Otherwise the now-inherited environment would have
  pointed an `embedding.provider: openai` embedder with `embedding.base_url: null` at
  the chat endpoint, sending it the embedding key. If you export `OPENAI_BASE_URL` for
  embeddings, it still works, but prefer setting `embedding.base_url` in the YAML.
- **Evaluation binding (`harness.ask_your_docs.binding`):** the serve child now also
  receives the embedder key, `TMPDIR`, proxies and CA bundles, but its configuration
  tier stays sealed as before. Inherited `PYDOCS_*` variables (except
  `PYDOCS_CACHE_DIR`) and `OPENAI_BASE_URL` / `LLM_MODEL` are withheld, so a shell
  export cannot change what an arm measures; set endpoints such as
  `embedding.base_url` in the arm's YAML. A names-only warning lists any withheld
  variable once per process.
- `scripts/validate_traced_run.py` imports `trace_subprocess_env` from
  `pydocs_mcp.observability.trace_env` again (it referenced a removed private name).
```

## 6. Ordered TDD test list

Write each group red first, then implement the matching §3 step. "Core CI" means it runs under `uv sync --group dev`. "Extras" means it is importorskip-gated and runs only locally or in the extras venv.

**Step A: core policy.** New file `/Users/msobroza/Projects/pyctx7-mcp/tests/harness/core/test_serve_child_env.py` (core CI). All tests inject `environ=` and use caplog; an autouse fixture calls `clear_serve_child_env_log_memo()`. On main they all fail with ImportError.
1. `test_child_env_inherits_parent_variables`: environ `{OPENROUTER_API_KEY, TMPDIR, HTTPS_PROXY, SSL_CERT_FILE, SYSTEMROOT, TEMP}` all come back with identical values. This includes the Windows-shaped keys.
2. `test_overlay_wins_over_inherited_value`: `{"A": "old"}` + overlay `{"A": "new"}` gives `"new"`.
3. `test_overlay_is_the_only_spelling_of_its_names`: environ `{"pydocs_trace__dir": "/stale"}`, overlay `{"PYDOCS_TRACE__DIR": "/fresh"}`. Exactly one key has upper form `PYDOCS_TRACE__DIR`, and its value is `/fresh`.
4. `test_inherited_trace_section_is_withheld_in_every_spelling`, parametrized over `PYDOCS_TRACE__ENABLED`, `pydocs_trace__enabled`, `Pydocs_Trace__Trajectory_Id`, and `PYDOCS_TRACE` (JSON value). Each is absent with no overlay; one WARNING carries the name but not the value.
5. `test_explicit_trace_overlay_is_delivered_exactly`: overlay `trace_subprocess_env(Path("/t"), "id1")` with a stale inherited trio. The three `TRACE_*_ENV_VAR` constants equal the overlay values.
6. `test_braced_values_are_dropped_and_logged_by_name_only`: `{"WEIRD": "a${NOPE}b", "PASSISH": "x${HOME}y"}` are both absent. There is one WARNING. `json.loads(record.message)["event"] == "serve_child_env_withheld"` and `withheld["adapter_expanded_ref"] == ["PASSISH", "WEIRD"]`. Neither value appears in `caplog.text`.
7. `test_shell_functions_are_dropped_quietly`: `{"BASH_FUNC_foo%%": "() { echo ${x}; }", "OLDFN": "() { :; }"}` are absent, with no record at WARNING or above.
8. `test_nothing_withheld_logs_nothing`.
9. `test_warning_fires_once_per_distinct_withheld_set`: the same environ twice gives one record; a different set gives a second.
10. `test_result_is_a_fresh_dict_and_parent_is_untouched`: environ is equal before and after; the result `is not` environ; `os.environ` snapshot is unchanged.
11. `test_sealed_tier_withholds_config_overlays_case_insensitively`, parametrized over `PYDOCS_SEARCH__TOP_K`, `pydocs_pipelines__x`, `PYDOCS_SERVE__DESCRIPTIONS_PATH`, `PYDOCS_EMBEDDING__BASE_URL`, `PYDOCS_CONFIG_PATH`, `PYDOCS_ASK_YOUR_DOCS`. Each is absent with `seal_config_tier=True` and present with False.
12. `test_sealed_tier_keeps_cache_dir_and_credentials`: `PYDOCS_CACHE_DIR`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `TMPDIR` are present under the seal, and `PYDOCS_CACHE_DIR` is not in any log payload.
13. `test_sealed_tier_withholds_endpoint_variables`: `OPENAI_BASE_URL`, `LLM_MODEL` are absent sealed and present unsealed. The payload reason is `endpoint_tier_sealed`, names only.
14. `test_config_prefix_matches_appconfig_env_prefix`: `_CONFIG_TIER_PREFIX == AppConfig.model_config["env_prefix"]` and `_TRACE_SECTION_ENV_VAR == "PYDOCS_TRACE"`. These are parity pins.

**Step B: spawn shape and real stdio.** New file `/Users/msobroza/Projects/pyctx7-mcp/tests/harness/ask_your_docs/test_serve_spawn.py` (core CI, uses `_stdio_probe.py`; asyncio_mode is auto).
15. `test_serve_connection_always_carries_an_env_map`: with `monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")`, `sdk_spawn_env(serve_connection("/ws", "/cfg.yaml"))["OPENROUTER_API_KEY"] == "dummy"`. It fails on main (ImportError); for the semantic red see test 20.
16. `test_serve_connection_argv_unchanged`: `command == sys.executable`, `args == ["-m", "pydocs_mcp", "--config", "/cfg.yaml", "serve", "--workspace", "/ws"]`, and without a config there is no `--config`.
17. `test_real_stdio_child_sees_the_parent_variable`: `setenv("AYD_ENV_PROBE_SENTINEL", "1")`; build `serve_connection("/ws", pydocs_cmd=[sys.executable, str(ENV_PRESENCE_SERVER)])`; `await probe_env_presence(conn["command"], conn["args"], conn["env"], "AYD_ENV_PROBE_SENTINEL") == "present"`.
18. `test_sdk_default_environment_drops_the_parent_variable`: the same spawn with `env=None` gives `"absent"`. This is a canary: if a future mcp inherits the full environment, it goes red and the design should be revisited.
19. `test_child_startup_failure_leaks_no_secret` (G8): `setenv("AYD_G8_SENTINEL_KEY", "sk-g8-sentinel-9c1e")`, `pydocs_cmd=[sys.executable, "-c", "import sys; sys.exit(3)"]`, caplog at DEBUG. Under `pytest.raises(Exception)`, the sentinel value is absent from `str(exc)`, `repr(exc)`, `"".join(traceback.format_exception(exc))` and `caplog.text`.

**Step C: wiring and adapter (extras).**
20. In `/Users/msobroza/Projects/pyctx7-mcp/tests/harness/ask_your_docs/test_agent_connection.py`, `test_build_agent_ui_path_spawns_child_with_parent_env`: `harness` fixture with `FakeMultiServerMCPClient`, `setenv("OPENROUTER_API_KEY", "dummy")`, `build_agent("/tmp/ws", "m", catalog=_CATALOG, capabilities=_BLIND)`. Assert `FakeMultiServerMCPClient.recorded[-1]["pydocs"]["env"]["OPENROUTER_API_KEY"] == "dummy"`. **Fails on main** with KeyError on `"env"`.
21. In `/Users/msobroza/Projects/pyctx7-mcp/tests/harness/ask_your_docs/test_binding.py`, `test_binding_serve_session_seals_config_and_delivers_key_and_trace_overlay`:
    - After the autouse strip, setenv `OPENROUTER_API_KEY=dummy`, `PYDOCS_SEARCH__TOP_K=3` and `OPENAI_BASE_URL=http://shell/v1`; conftest already sets `PYDOCS_CACHE_DIR`.
    - Monkeypatch `langchain_mcp_adapters.client.MultiServerMCPClient` with `FakeMultiServerMCPClient`, and `langchain_mcp_adapters.tools.load_mcp_tools` with an async named fake returning `[]`.
    - Enter `binding._serve_session_tools(AskYourDocsRunnerSettings(**_settings(tmp_path)), trace_subprocess_env(root, "id1"))`.
    - Assert: the key is present; the trace trio equals the overlay; `PYDOCS_SEARCH__TOP_K` and `OPENAI_BASE_URL` are absent; `PYDOCS_CACHE_DIR` equals `os.environ`'s.
    - **Fails on main.**
22. New file `/Users/msobroza/Projects/pyctx7-mcp/tests/harness/ask_your_docs/test_serve_spawn_adapter.py`, with `importorskip("langchain_mcp_adapters")`:
    - `test_adapter_delivers_parent_variable_to_child`: a real `MultiServerMCPClient({"pydocs": conn}).get_tools()`, then invoke `env_presence`; text is `"present"`. Read the text through a `_tool_text(result)` helper that accepts a str or a list of `{"type": "text", "text": ...}` blocks. The critic observed the list shape on 0.3.2.
    - `test_adapter_brace_pattern_matches_ours`: over sample strings, `bool(sessions._BRACED_VAR_RE.search(s)) == bool(_ADAPTER_EXPANDED_REF.search(s))`. This is a deliberate private-attribute pin, like `test_sdk_pins.py`.
    - `test_adapter_startup_failure_leaks_no_secret`: the G8 adapter half of test 19, through `get_tools()`.

**Step D: launcher and app.**
23. New file `/Users/msobroza/Projects/pyctx7-mcp/tests/harness/ask_your_docs/test_cli_launcher_env.py` (core CI; monkeypatch `cli._require_extra` to a no-op, which is its documented seam, and `cli.subprocess.run` with `FakeStreamlitRun`):
    - `test_launcher_forwards_connection_flags_under_private_names`: `setenv("OPENAI_BASE_URL", "http://embed-gw/v1")`, `delenv("LLM_MODEL")`, `main(["--workspace", "w", "--base-url", "http://chat/v1", "--model", "m"])`. The recorded env has `LAUNCH_BASE_URL_ENV_VAR == "http://chat/v1"`, `LAUNCH_MODEL_ENV_VAR == "m"`, `OPENAI_BASE_URL == "http://embed-gw/v1"`, and no `"LLM_MODEL"`. **Fails on main.**
    - `test_launcher_clears_stale_private_names_when_flags_absent`: a stale `LAUNCH_BASE_URL_ENV_VAR` is absent from the recorded env.
    - `test_serve_child_never_sees_the_launcher_chat_endpoint`: `serve_child_env(environ=fake.env)` gives `OPENAI_BASE_URL` equal to the shell's value (absent when the shell had none), never `"http://chat/v1"`. This is the critic-requested regression. **Fails on main.**
24. In `/Users/msobroza/Projects/pyctx7-mcp/tests/harness/ask_your_docs/test_app_connection_dialog.py` (extras, AppTest), `test_launcher_flag_beats_openai_base_url_on_the_status_line`: `setenv(LAUNCH_BASE_URL_ENV_VAR, "http://gpu-box:8000/v1")` and `setenv("OPENAI_BASE_URL", "http://other:9000/v1")`; the status line starts with `gpu-box:8000`.

**Step E: guards.**
25. `test_module_line_budget` now covers `binding.py` and `serve_spawn.py`: both `< 500`.
26. Existing tests that must stay green:
    - AC-42 `test_no_model_chosen_fails_before_any_construction`
    - `test_build_agent_signature_keeps_..._seams`
    - the `test_binding.py` / `test_binding_auth.py` contract suites (`_serve_session_tools` keeps its signature)
    - `test_cli_parser.py` (import stays lazy, no httpx)
    - `test_app_connection_dialog.py:222` (`OPENAI_BASE_URL` still feeds the environment tier)
    - `tests/harness/external/test_harness.py:233` (`.mcp.json` trace-only)

**Gates before pushing:**
- The full `ci.yml` set: `ruff check`, `ruff format --check` (python/ tests/ benchmarks/ scripts/), `mypy python/pydocs_mcp` (now covers `serve_child_env.py`), complexipy (restore `complexipy-snapshot.json` from HEAD before staging), vulture, `pytest tests/ --ignore=tests/test_parity.py --cov-fail-under=90`, `uv lock --check`, pip-audit.
- `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`.
- The extras-venv run of Steps C and D.
- The README audit grep.
- A manual end-to-end check: `harness-ask-your-docs` with an `embedding.provider: openai` config answers a question.

## 7. Implementation order

1. Tests 1–14, then `serve_child_env.py`.
2. Tests 15–19, then `serve_spawn.py` and the agent.py move.
3. Tests 20–22, then agent.py and binding.py.
4. Tests 23–24, then cli.py, app.py and `_page_fixtures.py`.
5. Doc comments, CHANGELOG, the script fix, budgets.

Per the owner's rules: after review, do a separate simplify pass. Commits carry no `Co-Authored-By` trailer; that global rule overrides the session attribution note.

## 8. Uncertain or owner-facing items

- **[UNCERTAIN] Exception type when the child exits early** (test 19): `McpError` or an `ExceptionGroup`. The assertions don't depend on the type.
- **[UNCERTAIN] Adapter tool result shape** (test 22): a list of text blocks on 0.3.2, per the critic's probe. The `_tool_text` helper absorbs either shape.
- **[UNCERTAIN] Line budgets:** binding.py is at 499/500, so the §3.4 edits must come out net ≤ 0. app.py goes from 492 to about 497.
- **[UNCERTAIN] Windows:** not run. The design relies on upper-case comparisons (Windows `os.environ` keys are upper-case). The real-spawn tests should pass there, but that is untested.
- **[UNCERTAIN] `TMPDIR` / FastEmbed re-download:** the proposer reproduced it; I did not re-run it.
- **[OWNER FYI] Eval index vs serve parity:**
  - The campaign index subprocess (`index_cache._run`) inherits the full environment, while the sealed serve child does not.
  - An arm whose embedder depended on a shell `OPENAI_BASE_URL` or `PYDOCS_EMBEDDING__*` at index time now fails loudly (`EmbedderMismatchError`) or talks to the SDK default host.
  - On main the same arm crashed earlier for lack of a key.
  - Recommended: arms set `embedding.*` in YAML. A digest of the child env into arm identity was deliberately **not** added, because it would change the run contract and needs ratification.
- **[RESIDUAL, conditional] HF offline flags:** `enable_hf_offline`'s process-wide `setdefault` means children spawned after an in-process local-dir embedder load inherit `HF_HUB_OFFLINE=1`. This matters only if a campaign process also builds in-process embedders; unconfirmed.
- **[RESIDUAL] Dropped braced secret:** if the embedder key itself contains `${`, the child still fails at startup, and only the names-only WARNING explains why. Showing it in the UI is a follow-up.
- **Follow-up, out of scope:** `build_agent` without `mcp_tools` still starts a new serve child, and rebuilds the embedder, on every UI tool call. Hold one session the way the binding does. Separately, overlay values containing `${` (for example a trace-root path) are still expanded by the adapter.