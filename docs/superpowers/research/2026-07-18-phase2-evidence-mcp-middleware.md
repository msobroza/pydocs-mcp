# Phase 2 evidence — MCP server capture surface (D1 server-side tool-event capture)

Researcher scope: server.py registration/wrapping points, installed `mcp` package hook
surfaces, launch model + env/metadata channels, server-side token/turn visibility,
existing logging infra + JSONL overhead. Worktree
`/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/phase-2-instrumentation-spec-498def`
(branch `claude/phase-2-instrumentation` = origin/main `cebf08c`). All file:line cites
are against this worktree or its `.venv` unless noted. Date: 2026-07-18.

Path abbreviations used below:

- `server.py` = `python/pydocs_mcp/server.py`
- `SDK:<p>` = `.venv/lib/python3.11/site-packages/mcp/<p>` (installed mcp 1.28.1)

---

## 1. server.py — registration shape and wrapping points

### 1.1 Registration: FastMCP decorator, applied through ONE repo-owned closure

- `run()` builds the server: `mcp = FastMCP("pydocs-mcp", instructions=SERVER_INSTRUCTIONS)`
  then `_register_tools(mcp, tools)` then `mcp.run(transport="stdio")` — server.py:595-601.
- `_register_tools(mcp, tools)` (server.py:643-783) defines the six indexed-tool handlers
  as local `async def` functions and registers each via a single shared closure
  `_register(fn, name)` (server.py:669-694), which calls
  `mcp.tool(name=name, description=TOOL_DOCS[name], annotations=ToolAnnotations(...))(fn)`
  (server.py:686-694). The three filesystem tools go through the SAME closure:
  `_register_filesystem_tools(_register, tools)` (server.py:783, definition 786-857).
- Every handler body is a one-liner delegation: build the pydantic input payload, then
  `return await _run_tool(name, lambda: tools.<method>(payload), ENVELOPE_MODELS[name])`
  — e.g. get_overview server.py:696-702, search_codebase 704-731, grep 800-832.
- `_register` also stamps the return annotation
  `fn.__annotations__["return"] = Annotated[CallToolResult, ENVELOPE_MODELS[name]]`
  (server.py:685) so FastMCP advertises the outputSchema.

### 1.2 Shared per-call boundary: `_run_tool`

server.py:604-627 (docstring: "Shared handler error boundary (§5.2) + dual-form result
assembly"):

```python
async def _run_tool(
    name: str, produce: Callable[[], Awaitable[ToolResponse]], envelope_model: type[BaseModel]
) -> CallToolResult:
    try:
        response = await produce()
        return _to_call_tool_result(response, envelope_model)
    except MCPToolError:
        raise
    except Exception as e:
        log.exception("%s failed unexpectedly", name)
        raise ServiceUnavailableError(f"{name} failed: {e}") from e
```

ALL nine tools pass through it. It already has: tool `name`, the raw exception (typed
`MCPToolError` or wrapped `ServiceUnavailableError`), and the full `ToolResponse`
(text + items + meta) before wire conversion. It does NOT receive the call arguments —
each handler builds its payload inside its own closure before calling `_run_tool`
(e.g. server.py:697, 715-724).

### 1.3 Result identifiers already available at the boundary

- `ToolResponse` = `{text: str, items: tuple[dict], meta: dict}` —
  `python/pydocs_mcp/application/tool_response.py:27-37`.
- `meta` block fields (contract §2.1): `tool, project, indexed_git_head, live_git_head,
  index_stale, truncated` (tool_response.py:40-48); `+resolution` on get_references
  (:51-54); `+suggestion` on search_codebase/get_why/grep (:56-60, ADR 0007).
- items rows carry stable ids: e.g. `SearchItem` = `kind, id, qualified_name, package,
  path, start_line, end_line, score` (tool_response.py:79-89).

So a capture layer can log result identifiers (item ids + meta) without recomputing
anything.

### 1.4 Candidate interception points (ranked, no per-tool edits)

**A. Wrap `fn` inside `_register` (server.py:669)** — one decorator application covers
all nine tools. Captures: tool name, the PARSED kwargs FastMCP dispatched (post
inputSchema conversion), timing, the returned `CallToolResult`, and raw exceptions
(before FastMCP converts them to `isError=True`). **Constraint (load-bearing):** the
wrapper MUST preserve the wrapped function's exact `inspect.signature` — FastMCP builds
the advertised `inputSchema` from the function signature via
`Tool.from_function` → `func_metadata(fn, ...)` (SDK:server/fastmcp/tools/base.py:46-74).
A naive `*args, **kwargs` wrapper would collapse every tool's inputSchema. Use
`functools.wraps` + `wrapper.__signature__ = inspect.signature(fn)` (and note
server.py already mutates `fn.__annotations__["return"]` at :685 — wrap BEFORE that
line's effect or copy annotations).

**B. Subclass `FastMCP`, override `async def call_tool(self, name, arguments)`** —
the true single choke point. Evidence it works: `FastMCP.__init__` calls
`self._setup_handlers()` (SDK:server/fastmcp/server.py:239), which registers the BOUND
method: `self._mcp_server.call_tool(validate_input=False)(self.call_tool)`
(SDK:server/fastmcp/server.py:302-308). Bound-method resolution happens at `__init__`
time on the instance, so a subclass override IS the registered handler. The stock body
(SDK:server/fastmcp/server.py:343-346):

```python
async def call_tool(self, name: str, arguments: dict[str, Any]) -> Sequence[ContentBlock] | dict[str, Any]:
    """Call a tool by name with arguments."""
    context = self.get_context()
    return await self._tool_manager.call_tool(name, arguments, context=context, convert_result=True)
```

An override sees: tool name, the RAW client `arguments` dict (pre-pydantic), timing
around the `await`, the result, and raw exceptions. Zero signature concerns, zero
per-tool edits; the repo change is one line at server.py:595 (`FastMCP(...)` →
`InstrumentedFastMCP(...)`). Request context (request_id, session, `_meta`) is
reachable inside via `self.get_context()` (see §3.4).

**C. Extend `_run_tool`** — smallest diff for timing+error+result-meta, and it is the
only point that sees the typed `MCPToolError` hierarchy AND the pre-wire `ToolResponse`.
But it cannot capture call arguments without touching each handler (args live in the
per-tool closures). Suitable as a *complement* (result/meta capture), not as the sole
capture point.

**D. Wrap the `ToolRouter` instance** — `ToolRouter` methods are 1:1 with the nine tools
and return `ToolResponse` (`python/pydocs_mcp/application/tool_router.py:70-236`). A
wrapping proxy would ALSO capture CLI invocations (the CLI shares the router,
server.py:422-513 `build_routers` is used by both). Only relevant if CLI capture is in
scope; for MCP-only capture A/B are cleaner.

### 1.5 Error flow (where errors are visible)

- `_run_tool` re-raises `MCPToolError` unchanged and wraps unexpected exceptions in
  `ServiceUnavailableError` after `log.exception` (server.py:623-627).
- These propagate raw through `ToolManager.call_tool` → `FastMCP.call_tool`; the
  LOWLEVEL handler then flattens ANY exception to
  `CallToolResult(content=[TextContent(text=str(e))], isError=True)` via
  `_make_error_result` (SDK:server/lowlevel/server.py:473-480; catch-all at the end of
  the call_tool decorator handler, SDK:server/lowlevel/server.py:588-590
  `except Exception as e: return self._make_error_result(str(e))`).
- Consequence: capture must sit at or above `FastMCP.call_tool` (options A/B/C) to see
  typed exception classes; below that only the flattened string survives.
- Note: server.py's module docstring claims FastMCP surfaces typed errors "as structured
  JSON-RPC errors" (server.py:13-14) — in installed mcp 1.28.1 tool-handler exceptions
  actually become `isError=True` tool RESULTS, not JSON-RPC error responses
  (SDK:server/lowlevel/server.py:588-590). Docstring nuance, not a behavior bug.

---

## 2. Installed mcp package — version and hook surfaces

### 2.1 Version

- Installed: `mcp 1.28.1` — command run:
  `.venv/bin/python -c "import importlib.metadata as im; print(im.version('mcp'))"` →
  `1.28.1`. (`mcp.__version__` does not exist — verified, prints "no attr".)
- Constraint: `pyproject.toml:50` = `"mcp>=1.28.1"`. Lock: `uv.lock:1767-1768`
  (`name = "mcp"` / `version = "1.28.1"`).
- CLAUDE.md still says `mcp>=1.0` (stale doc), and server.py:633 comments cite
  "mcp 1.27.1 func_metadata.convert_result" — both are provenance nits vs the pinned
  1.28.1.

### 2.2 No native tool-call middleware in mcp 1.28.1

- Grep of `SDK:server/fastmcp/*.py`, `SDK:server/fastmcp/tools/*.py`,
  `SDK:server/lowlevel/*.py` for `middleware|interceptor|hook`: the only middleware is
  Starlette ASGI **auth** middleware for the HTTP transports
  (`SDK:server/fastmcp/server.py:24-34, 854-871, 898-913`;
  `SDK:server/auth/middleware/{auth_context,bearer_auth}.py`). Nothing applies to stdio,
  and nothing intercepts tool calls generically.
- `FastMCP.__init__` signature (SDK:server/fastmcp/server.py:146-176) has NO
  middleware/hook kwarg: only `lifespan`, auth, transport and warn flags.
- `ToolManager.call_tool` (SDK:server/fastmcp/tools/tool_manager.py:82-95) is a direct
  `tool.run(...)` dispatch — no hook list.
- **Conclusion: capture must be added by wrapping at registration time (option A) or by
  subclassing `FastMCP.call_tool` (option B). This is confirmed for 1.28.1 by source
  inspection, not assumption.**

### 2.3 Lifespan hook (exists — useful for trace-file lifecycle)

- `FastMCP(lifespan=...)` kwarg (SDK:server/fastmcp/server.py:173) is wrapped via
  `lifespan_wrapper` (:132-143) onto the lowlevel Server (:212). Default is a no-op
  `@asynccontextmanager` yielding `{}` (SDK:server/lowlevel/server.py:124-134).
- Whatever the lifespan yields becomes `lifespan_context` on EVERY request's
  `RequestContext` (SDK:server/lowlevel/server.py:760-770 constructor call;
  field at SDK:shared/context.py:19-31). A trace writer opened in a custom lifespan is
  therefore reachable from every tool call without globals.

### 2.4 Request context (per-call metadata the server can read)

- ContextVar: `request_ctx: contextvars.ContextVar[RequestContext[...]]`
  (SDK:server/lowlevel/server.py:109), set per dispatched request with
  `RequestContext(message.request_id, message.request_meta, session, lifespan_context,
  Experimental(...), request=request_data, ...)` (SDK:server/lowlevel/server.py:756-776)
  and reset in `finally` (:797-800).
- `RequestContext` dataclass fields: `request_id, meta: RequestParams.Meta | None,
  session, lifespan_context, experimental, request, ...`
  (SDK:shared/context.py:18-31).
- FastMCP `Context` accessors: `request_id` (SDK:server/fastmcp/server.py:1298-1300),
  `client_id` — reads `request_context.meta.client_id` if present, marked
  `TODO(maxisbey)` + pragma no cover (:1286-1295), `session` (:1303-1305),
  `request_context` (:1154-1160). Obtained via `FastMCP.get_context()` (:332-341),
  which `FastMCP.call_tool` already calls (:343-345).

### 2.5 Client metadata channels

- **initialize → clientInfo**: `InitializeRequestParams` carries `protocolVersion`,
  `capabilities`, `clientInfo: Implementation` (SDK:types.py:670-677);
  `Implementation` = name (via BaseMetadata) + `version` + optional `websiteUrl`/`icons`
  (SDK:types.py:263-272). The server session stores it:
  `ServerSession._client_params: types.InitializeRequestParams | None`
  (SDK:server/session.py:86), set on the initialize request (:180), exposed as the
  `client_params` property (:108-109). Reachable per call as
  `ctx.session.client_params.clientInfo`.
- **per-request `_meta`**: `RequestParams.Meta` has `progressToken` plus
  `model_config = ConfigDict(extra="allow")` (SDK:types.py:62-71), and
  `CallToolRequestParams` itself is `extra="allow"` (SDK:types.py:1348-1353). So the
  protocol ACCEPTS arbitrary extra `_meta` keys on `tools/call`, and the server can read
  them via `ctx.request_context.meta`. **UNVERIFIED:** whether Claude Code (or any
  target client) can be configured to SEND a custom `_meta` key on tool calls — nothing
  in this repo or the SDK client config surface suggests a user-facing knob for it; do
  not plan on this channel for trajectory_id.

---

## 3. Launch model, env vars, per-rollout identity

### 3.1 Transport = stdio, one process per client session

- `mcp.run(transport="stdio")` — server.py:601. `FastMCP.run` dispatches to
  `anyio.run(self.run_stdio_async)` (SDK:server/fastmcp/server.py:295-297), which wraps
  THE PROCESS's stdin/stdout in exactly one session:
  `async with stdio_server() as (read_stream, write_stream): await self._mcp_server.run(...)`
  (SDK:server/fastmcp/server.py:753-760). One server process ↔ one MCP session.
- CLI path: `pydocs-mcp serve <path>` → `_cmd_serve` (python/pydocs_mcp/__main__.py:1263)
  → index phase → `server.run(...)` on the main thread (no-watch path,
  __main__.py:1310-1320 comment block).

### 3.2 The benchmark harness already launches one server per rollout

- `render_mcp_config(corpus_dir, python)` renders the one-server `.mcp.json`:
  `{"mcpServers": {"pydocs-mcp": {"command": "<python>", "args": ["-m", "pydocs_mcp", "serve", "<corpus_dir>"]}}}`
  — benchmarks/src/pydocs_eval/agent_track/_command.py:117-135; constants
  `_SERVE_ARGS_PREFIX = ("-m", "pydocs_mcp", "serve")` / `_MCP_SERVER_NAME = "pydocs-mcp"`
  at :49-50. Passed to the agent CLI with `--mcp-config` + `--strict-mcp-config`
  (_command.py:100-105).
- The client spawns the server subprocess from this config per run ⇒ **one server
  process per rollout is the existing, natural model**.
- **Gap for Phase 2:** the rendered config today has NO `"env"` key
  (_command.py:131-135). Passing a per-rollout `trajectory_id` via env requires adding
  an `env` map to this rendered server block (a harness-side change; standard mcp-config
  feature).

### 3.3 Env inheritance is restricted on the client side

- The python SDK client spawns stdio servers with an allowlisted default env:
  `DEFAULT_INHERITED_ENV_VARS` (posix: HOME/LOGNAME/PATH/SHELL/TERM/USER; win32 list at
  SDK:client/stdio/__init__.py:28-46) via `get_default_environment()` (:51-69). Any
  custom variable (e.g. `PYDOCS_TRAJECTORY_ID`) must be EXPLICITLY listed in the client
  mcp-config `env` map — ambient shell export is not guaranteed to reach the server
  process. (Claude Code's spawner is closed-source; its documented `.mcp.json` `env`
  field is the supported channel — UNVERIFIED beyond docs knowledge.)

### 3.4 How the server would read a trajectory_id env var

- `AppConfig(BaseSettings)` — python/pydocs_mcp/retrieval/config/app_config.py:79;
  `model_config = SettingsConfigDict(env_prefix="PYDOCS_", env_nested_delimiter="__",
  extra="ignore")` (:197-201). Layering: "shipped baseline → user YAML → env → init"
  (:1, :84-85) — env OUTRANKS YAML.
- Working precedent for a nested serve-scoped env override:
  `PYDOCS_SERVE__DESCRIPTIONS_PATH` outranks the `serve.descriptions_path` YAML key
  (ServeConfig docstring, python/pydocs_mcp/retrieval/config/models.py:698-705).
- Therefore: add a typed sub-model field (e.g. under `serve`, models.py:693-713 — the
  docstring explicitly reserves ServeConfig as the home for "future serve-side knobs")
  and a rollout id arrives as `PYDOCS_SERVE__<SECTION>__<FIELD>=...`. Caveat:
  `extra="ignore"` means an env var with no backing field silently no-ops — the field
  must exist. A bare `os.environ.get(...)` read is the alternative for a pure
  identity value that should not be YAML-settable (app_config.py:452 shows the
  precedent of a direct env read: `os.environ.get("PYDOCS_CONFIG_PATH")`).
- Second env var already reserved: `PYDOCS_CONFIG_PATH` (config file resolution chain,
  app_config.py:444, 452).

### 3.5 initialize-time client metadata

Covered in §2.5: `clientInfo` (client name + version) IS readable server-side from
`session.client_params`. It identifies the client software, not the rollout — fine for
a `client` field in trace records, useless as a trajectory key.

---

## 4. Token accounting + turn boundaries (server-side visibility)

### 4.1 No token/usage data crosses MCP — verified

- `tools/call` request payload is exactly `name: str`, `arguments: dict | None`, plus
  optional `_meta` (SDK:types.py:1348-1356). No usage fields.
- Grep of SDK:types.py for `usage|tokens`: every hit is the doc-comment phrase
  "notes on _meta usage" or a resource `size` note ("estimate context window usage",
  :781) — there is NO usage/token field anywhere in the protocol types.
- Conclusion (expected, now verified): the server cannot see client-side prompt/response
  token counts. What the server CAN measure itself: response payload sizes and REAL
  token counts of its own outputs — tiktoken is a required dep (pyproject.toml:59) and
  the output path already counts real tokens (token-budget rendering,
  python/pydocs_mcp/retrieval/steps/token_budget.py:1-18 delegating to
  `application/formatting` budget helpers; overview card cap "in REAL tokens (tiktoken)"
  python/pydocs_mcp/defaults/default_config.yaml:205).

### 4.2 No turn boundaries or final answers — verified

- Everything a client can send: `ClientRequestType` = Ping/Initialize/Complete/
  SetLevel/GetPrompt/ListPrompts/ListResources/ListResourceTemplates/ReadResource/
  Subscribe/Unsubscribe/**CallTool**/ListTools/Task* (SDK:types.py:~1786-1812);
  `ClientNotificationType` = Cancelled | Progress | Initialized | RootsListChanged |
  TaskStatus (SDK:types.py:1818-1824).
- None of these encode agent turns, assistant messages, or final answers. The server
  observes only: initialize, tool calls, and (optionally) cancellations. Turn
  segmentation and final-answer capture must come from the client/harness side of
  Phase 2, not from this server.

---

## 5. Existing logging infra + JSONL overhead

### 5.1 Today's logging

- Plain text, stderr, stdlib logging: `logging.basicConfig(level=INFO|DEBUG,
  format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S",
  stream=sys.stderr)` — python/pydocs_mcp/__main__.py:569-575. Logger name
  `"pydocs-mcp"` (__main__.py:49; server.py:54).
- NOT structured JSON at the handler level, but several messages embed JSON/dict
  payloads in the text: `query_cache_enabled` logs a `json.dumps({...})` blob
  (server.py:456-465); the cross-repo link pass logs a dict (server.py:343-355);
  descriptions artifact line `"descriptions artifact %s source=%s"` (server.py:546) is
  format-pinned by test for Phase 2 attribution (comment at server.py:526-528).
- FastMCP also calls `configure_logging(self.settings.log_level)` at construction
  (SDK:server/fastmcp/server.py:241), configuring SDK loggers.
- **No per-tool-call timing exists anywhere today**: grep of
  `python/pydocs_mcp/application/` + server.py for `perf_counter|time.time()|monotonic`
  hits only indexing timestamps (indexing_service.py:328,356; index_project.py:127;
  overview_aggregates.py:117,326) and the SIMILAR-linker pass timer
  (similar_linker.py:73-87). Nothing times tool handlers.
- **No JSONL / append-to-file pattern exists in the server package**: grep for
  `jsonl|mode="a"|open(..., 'a')` over `python/pydocs_mcp/` → zero hits (JSONL appears
  only in benchmarks artifacts, e.g.
  `docs/superpowers/research/2026-07-11-improvement-specs-run-journal.jsonl`). A trace
  writer is new infrastructure — CLAUDE.md's "Structured JSON logs with named fields"
  rule (§Logging & defensive code) supports it.

### 5.2 Measured JSONL append overhead (this machine, macOS/APFS, py3.11 venv)

Benchmark script: scratchpad `jsonl_bench.py` (run 2026-07-18 with
`.venv/bin/python`); record = realistic 788-byte tool-event JSON line
(ts/event/tool/args/duration/items[10 ids]/meta/trajectory_id); N=10,000
(N=1,000 for fsync):

| strategy | per-call cost |
|---|---|
| `open(path,"a")` + write + close per call | 255.1 µs |
| held file handle + `write` + `flush` | **25.3 µs** |
| held handle + flush + `os.fsync` | 140.5 µs |

Interpretation: even the worst strategy is ~0.26 ms/call — negligible against tool
latencies (retrieval pipelines run embedding + SQLite work per call). A held
handle opened in a FastMCP lifespan (§2.3) at ~25 µs/call is the obvious design;
fsync per call is unnecessary for an append-only trace consumed post-run.

### 5.3 Concurrency note for the writer

The stdio session processes requests through anyio task groups (lowlevel dispatch,
SDK:server/lowlevel/server.py:735-800) — concurrent tool calls are possible if the
client parallelizes. A single-writer discipline (one file handle, writes of one line
per call, ≤~1 KB) stays atomic-enough on POSIX appends in practice, but a small
`anyio.Lock`/`asyncio.Lock` around `write+flush` is the cheap correctness guarantee.
(No existing repo pattern to reuse — §5.1.)

---

## 6. Summary matrix — what a capture middleware can record, from where

| datum | available? | source (cite) |
|---|---|---|
| tool name | yes | option A/B/C all receive it (server.py:604; SDK fastmcp/server.py:343) |
| raw args (as client sent) | yes | option B `call_tool(name, arguments)` (SDK fastmcp/server.py:343) |
| parsed kwargs | yes | option A wrapper around handler fn (server.py:669-694) |
| wall time per call | yes | wrap the await at A/B/C — none exists today (§5.1) |
| result identifiers (item ids, meta block) | yes | ToolResponse.items/meta at `_run_tool` (tool_response.py:27-60) |
| typed error class | yes, at A/B/C only | flattened to isError below FastMCP (SDK lowlevel/server.py:473-480, 588-590) |
| MCP request_id | yes | ctx.request_id (SDK fastmcp/server.py:1298) |
| client name/version | yes | session.client_params.clientInfo (SDK server/session.py:108; types.py:677) |
| trajectory_id via env | yes, if client config sets it | AppConfig env prefix PYDOCS_ + `__` nesting (app_config.py:197-200); harness config lacks `env` today (_command.py:131-135) |
| trajectory_id via `_meta` | protocol-possible, client-UNVERIFIED | RequestParams.Meta extra="allow" (SDK types.py:62-71) |
| client token usage | NO | no usage fields in protocol (SDK types.py grep, §4.1) |
| turn boundaries / final answer | NO | ClientNotificationType (SDK types.py:1818-1824, §4.2) |
| server-side token estimate of its own responses | yes (self-computed) | tiktoken required dep (pyproject.toml:59); budget helpers (§4.1) |

## Open questions (for the reconciler)

1. Does Claude Code forward a custom `env` map from `.mcp.json` verbatim to the spawned
   server process on macOS/Linux in the version the harness pins? (Assumed yes per its
   docs; not exercised this session.)
2. Can any target client be made to attach `_meta` keys on `tools/call`? If yes, `_meta`
   beats env for per-CALL (not just per-process) correlation.
3. Should capture wrap at B (subclass, raw args) or A (registration wrapper, parsed
   args)? B is recommended here (no signature-preservation hazard, pre-validation args,
   one-line composition-root change at server.py:595), with C as the complement if the
   pre-wire `ToolResponse.meta`/items are wanted without re-parsing the CallToolResult.
4. Stale doc nits found in passing (not fixed, out of scope): CLAUDE.md says `mcp>=1.0`
   vs pyproject.toml:50 `mcp>=1.28.1`; server.py:633 comment cites mcp 1.27.1;
   server.py:13-14 docstring says typed errors surface "as structured JSON-RPC errors"
   while 1.28.1 flattens them to `isError=True` results.
