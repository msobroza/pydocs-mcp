# Phase 3 · D3 — Model-plumbing evidence (client stack, provider pinning, caching billing, retries)

Researcher scope: D3 — the client stack reality; provider pinning mechanics; caching billing
semantics; retry semantics. NO paid completions were made (metadata endpoints, docs, and
0-cost `401` route probes only). Every claim carries a `file:line`, a command + output, a fetched
URL, or is explicitly labelled UNVERIFIED.

Worktree: `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/phase-3-evaluation` @ `061d967`.

---

## 0. TL;DR (the load-bearing findings)

1. **The eval loop can natively drive ONLY Claude models.** Headless `claude` speaks the
   Anthropic Messages format (or Bedrock/Vertex/Foundry Claude endpoints). Anthropic's own docs
   state plainly: it "**doesn't support routing Claude Code to non-Claude models through any
   gateway.**" (code.claude.com/docs/en/llm-gateway). Gateway model-discovery even **ignores any
   `/v1/models` entry whose `id` doesn't begin with `claude` or `anthropic`**
   (code.claude.com/docs/en/llm-gateway-protocol).
2. **Non-Claude targets ARE drivable in practice — but only by inserting an Anthropic-Messages
   shim** (LiteLLM proxy is the documented path; it has a first-party "Use Claude Code with
   Non-Anthropic Models" tutorial). This is an *unsupported* configuration, not a loop the repo
   already has. Driving GPT/Gemini/etc. as target candidates ⇒ stand up a `/v1/messages`→provider
   translation proxy and point `ANTHROPIC_BASE_URL` at it.
3. **Model/provider is NOT a per-request CLI knob beyond `--model`.** The in-repo command builder
   emits only `--model / --max-turns / --allowedTools / --mcp-config / --strict-mcp-config`
   (`_command.py:24-33`). Base-URL / auth-token / provider selection is **process-env**, inherited
   by the spawned `claude` (the adapter passes no `env=` override — `_runner.py:129-135`).
4. **Anthropic caching billing (docs-verified current numbers):** cache write ×1.25 (5-min TTL) /
   ×2.0 (1-hour TTL); cache read ×0.1. The Phase-2 stream parser already captures the exact usage
   fields `cache_read_input_tokens` / `cache_creation_input_tokens` (`_parse.py:176-177`).
5. **Retries live at the loop layer, not the tool layer.** The `claude` CLI emits `system/api_retry`
   stream events and re-issues the API call; ADR-0009 server-side capture is per-tool-call, so a
   loop-level API retry does **not** re-run MCP tools and cannot double-count tool events. A retry
   *can* inflate token/cost usage if a partial response was billed before the retry — but the raw
   `stream.jsonl` is persisted verbatim so every usage number stays auditable.

---

## 1. THE CLIENT-STACK QUESTION — which models can the loop drive?

The eval loop is the headless `claude -p` CLI spawned per rollout (ADR 0007/0009). What it can
drive is bounded by (a) what `claude` speaks and (b) how the in-repo runner configures it.

### 1(a) Native — current Anthropic Claude model IDs

Enumerated from the `claude-api` skill catalog (cached 2026-06-24) cross-checked live against the
OpenRouter models endpoint (§3), which lists `anthropic/claude-sonnet-5`, `anthropic/claude-opus-4-8`,
etc. Active first-party IDs the CLI's `--model` / `ANTHROPIC_MODEL` accepts:

| Model | ID | Ctx | in $/1M | out $/1M |
|---|---|---|---|---|
| Claude Fable 5 | `claude-fable-5` | 1M | 10.00 | 50.00 |
| Claude Opus 4.8 | `claude-opus-4-8` | 1M | 5.00 | 25.00 |
| Claude Opus 4.7 | `claude-opus-4-7` | 1M | 5.00 | 25.00 |
| Claude Opus 4.6 | `claude-opus-4-6` | 1M | 5.00 | 25.00 |
| Claude Sonnet 5 | `claude-sonnet-5` | 1M | 3.00 (2.00 intro→2026-08-31) | 15.00 (10.00 intro) |
| Claude Sonnet 4.6 | `claude-sonnet-4-6` | 1M | 3.00 | 15.00 |
| Claude Haiku 4.5 | `claude-haiku-4-5` | 200K | 1.00 | 5.00 |

`--model` / `ANTHROPIC_MODEL` accept **either an alias (`opus`, `sonnet`) or a full model ID** —
the settings doc's `availableModels` note: model aliases `'opus'` and `'sonnet'`, or a full model
ID (code.claude.com/docs/en/settings). The **in-repo default arm model is `claude-sonnet-5`**
(`benchmarks/src/pydocs_eval/agent_track/_types.py:21` `DEFAULT_MODEL = "claude-sonnet-5"`; judge
inherits it, `:23`). The skillopt optimizer separately pins `claude-sonnet-4-6`
(`optimize/optimizers/skillopt.py:114`).

### 1(b) Env overrides — what ANTHROPIC_* actually support (docs)

From code.claude.com/docs/en/settings and .../env-vars:

| Env var | Meaning (quoted / paraphrased from docs) |
|---|---|
| `ANTHROPIC_API_KEY` | API key authenticating with Anthropic's Claude API |
| `ANTHROPIC_AUTH_TOKEN` | Alternative auth token; sent as `Authorization: Bearer` (gateway credential) |
| `ANTHROPIC_BASE_URL` | "Custom base URL for API endpoints (allows routing to alternative providers or gateways)" — the variable that "points Claude Code at the gateway" |
| `ANTHROPIC_MODEL` | Default model to use |
| `ANTHROPIC_SMALL_FAST_MODEL` | Lightweight/fast model for quick operations (the background/haiku slot) |
| `ANTHROPIC_CUSTOM_HEADERS` | Adds custom headers to every API request (also on `/v1/models` discovery) |
| `CLAUDE_CODE_USE_BEDROCK=1` (+ `ANTHROPIC_BEDROCK_BASE_URL`) | Speak Amazon Bedrock InvokeModel format |
| `CLAUDE_CODE_USE_VERTEX=1` (+ `ANTHROPIC_VERTEX_BASE_URL`) | Speak Google Cloud Agent Platform (ex-Vertex) `rawPredict` |
| `CLAUDE_CODE_USE_FOUNDRY` / `CLAUDE_CODE_USE_MANTLE` | Microsoft Foundry / Claude Platform on AWS Mantle |

**Gateway support is first-class and Anthropic-format-only.** From
code.claude.com/docs/en/llm-gateway-protocol → "API formats":

| Format | Selected by | Endpoints |
|---|---|---|
| Anthropic Messages | `ANTHROPIC_BASE_URL` | `/v1/messages`, `/v1/messages/count_tokens` (optional) |
| Amazon Bedrock InvokeModel | `ANTHROPIC_BEDROCK_BASE_URL` + `CLAUDE_CODE_USE_BEDROCK=1` | `/model/{model}/invoke`, `.../invoke-with-response-stream` |
| Google Agent Platform rawPredict | `ANTHROPIC_VERTEX_BASE_URL` + `CLAUDE_CODE_USE_VERTEX=1` | `:rawPredict`, `:streamRawPredict` |
| Foundry / Claude-Platform-on-AWS | `ANTHROPIC_FOUNDRY_BASE_URL` / `ANTHROPIC_AWS_BASE_URL` | Anthropic Messages format |

Inference posts to `/v1/messages?beta=true`; responses **must stream** (a buffering gateway stalls
the client). LiteLLM-style Anthropic-compatible endpoints are explicitly a supported gateway shape:
"Any gateway that exposes a supported API format works." **Critical restriction (quoted):**
"Anthropic doesn't endorse, maintain, or audit third-party gateway products, and **doesn't support
routing Claude Code to non-Claude models through any gateway.**"

Two more loop-relevant gateway facts:
- **Model discovery is Claude-gated.** `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` makes the CLI
  `GET /v1/models?limit=1000` and add rows to `/model` — but it "ignores entries whose `id` doesn't
  begin with `claude` or `anthropic`." So a gateway can only *surface* Claude-named models; a
  non-Claude id must be forced via `ANTHROPIC_MODEL`/`--model` under a Claude-looking alias.
- **Fine-grained tool streaming is OFF by default behind any custom base URL**
  (needs `CLAUDE_CODE_ENABLE_FINE_GRAINED_TOOL_STREAMING=1`), and a **system-prompt attribution
  block** is prepended (stable per-conversation from v2.1.181; positional strip only on
  `api.anthropic.com`). Both matter if a Phase-4 gateway is inserted for provider pinning — they
  change the prompt-cache key and the stream shape the Phase-2 parser sees.

### 1(c) Third-party routers exposing an Anthropic-compatible `/v1/messages`

| Router | Anthropic `/v1/messages`? | Evidence | Non-Claude via it? |
|---|---|---|---|
| **OpenRouter** | **Yes — route exists** at `https://openrouter.ai/api/v1/messages` | 0-cost probe: `POST /api/v1/messages -> HTTP 401` and `POST /api/v1/chat/completions -> HTTP 401` (route present, auth-gated) | OpenRouter's *primary* surface is OpenAI-compatible `/api/v1/chat/completions`; it fronts 300+ models incl. non-Claude |
| **LiteLLM proxy** | **Yes** — native Anthropic pass-through `/anthropic` → `/v1/messages` + `/v1/messages/count_tokens` | docs.litellm.ai/docs/anthropic_unified + /docs/tutorials/claude_non_anthropic_models | **Yes, explicitly** — LiteLLM ships a "Use Claude Code with Non-Anthropic Models" tutorial; translates `/v1/messages` ⇄ OpenAI/Gemini/DeepSeek |
| **Direct providers** (Anthropic API, Bedrock, Vertex, Foundry, Claude-Platform-on-AWS) | Yes (Claude only) | §1(b) table | No (Claude models only) |

**Bottom line for target-candidate selection:** non-Claude candidates are NOT drivable by the
current loop as-shipped. They become drivable only by standing up a LiteLLM-style Anthropic-Messages
translation proxy in front of `claude` (`ANTHROPIC_BASE_URL`=proxy, `ANTHROPIC_AUTH_TOKEN`=proxy
key, `ANTHROPIC_MODEL`=a proxy-mapped alias). That is an unsupported configuration and a Phase-4
build item, not a config toggle. **Security note (from search):** LiteLLM PyPI 1.82.7/1.82.8
shipped credential-stealing malware — pin a clean release if this path is taken.

> ⚠️ **UNVERIFIED nuance:** OpenRouter's `/api/v1/messages` route responds `401` without a key, so I
> cannot confirm from a 0-cost probe whether it accepts full Claude-Code beta headers/body fields
> (`output_config`, `context_management`, adaptive `thinking`) or only a reduced set. Treat
> OpenRouter's Anthropic endpoint as "route exists, fidelity unverified"; LiteLLM is the
> better-documented Anthropic-format shim.

---

## 2. IN-REPO CLIENT-STACK INVENTORY (reflector-wiring candidates)

These are the LLM client configs already present. Phase 3 configures them; Phase 4 uses them.

### 2.1 Benchmarks optimizer — `CritiqueLlmConfig` (the reflector seam)
`benchmarks/src/pydocs_eval/optimize/run_config.py:95-108`
```python
class CritiqueLlmConfig(BaseModel):        # spec §D4/§D7
    model_config = ConfigDict(frozen=True)
    provider: str
    model_name: str
    temperature: float = 0.7
```
- Attached as `run_config.py:234` `llm: CritiqueLlmConfig | None = None` — "Present only for
  `critique_refine` runs; `skillopt` configs omit the `llm` block." (docstring `:95-102`).
- Benchmarks-local by design: "mirrors the product's LLM config shape but **never reads product
  `AppConfig`**" — so a critique-model A/B is a YAML edit, decoupled from the served pipeline.
- This is the **primary reflector-wiring candidate**: provider + model_name + temperature is the
  full knob set the critique/refine reflector runs under.

### 2.2 Ask-runner / rubric-judge — `AskRunnerSettings`
`run_config.py:111-126`: `model: str = DEFAULT_MODEL` (`claude-sonnet-5`), `architecture`,
**`base_url: str | None = None`**, `workspace`, `task_timeout_seconds`. The `base_url` field is the
in-repo seam for pointing the ask agent + judge at an OpenAI-compatible / OpenRouter endpoint.

### 2.3 Product retrieval-side LLM — `LlmConfig`
`python/pydocs_mcp/retrieval/config/embedder_models.py:259-274`
```python
class LlmConfig(BaseModel):
    provider: Literal["openai"] = "openai"     # ONLY openai today
    model_name: str = "gpt-4o-mini"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    api_key: str | None = None                 # None -> SDK reads OPENAI_API_KEY
```
Wired at `retrieval/config/app_config.py:163` (`llm: LlmConfig = Field(default_factory=LlmConfig)`).
Concrete client: `retrieval/llm_clients/openai.py:90` `OpenAiLlmClient` — uses `openai.AsyncOpenAI`
/ `OpenAI`, **constructed lazily on first `chat()`** (`:106-115`), `api_key` only (no `base_url`
kwarg in the constructor path — an OpenRouter redirect for this client would need a `base_url`
addition). Reasoning-model shape handling at `:39-54` (`gpt-5`/`o1`/`o3`/`o4` ⇒ omit `temperature`,
map `max_tokens`→`max_completion_tokens`). This backs the `llm_tree_reasoning` retrieval step and
`llm_summary` — NOT the agent loop.

### 2.4 Ask-your-docs agent — OpenAI-compatible with configurable `base_url`
`python/pydocs_mcp/ask_your_docs/agent.py:20,238` `from langchain_openai import ChatOpenAI` →
`ChatOpenAI(model=model, base_url=base_url)`; `multimodal.py:187` the vision-probe rung likewise
`ChatOpenAI(model=model, base_url=base_url, timeout=timeout, max_retries=0)`. **`base_url` is the
OpenRouter/OpenAI-gateway seam** (per MEMORY the vision-sidecar feature points a separate OpenRouter
vision model here via `describe_images`; no literal `openrouter.ai` URL is committed in this
worktree — it is a config-supplied `base_url`).

> Note: `benchmarks/src/pydocs_eval/systems/{context7,neuledge,_mcp_http}.py` `base_url` fields are
> **MCP-HTTP endpoints of competitor systems**, NOT LLM base URLs — do not conflate.

---

## 3. PROVIDER PINNING MECHANICS

### 3.1 Anthropic direct (what pinning even means)
No router. "Pinning" = the exact model ID string + `anthropic-version` header (`2023-06-01`) +
optional `anthropic-beta` capability values. There is no provider-ordering / quantization concept —
you get Anthropic's serving of that model at that version. A **quantization pin is meaningless** for
the Anthropic-direct class: Anthropic does not expose weight-precision variants.

### 3.2 OpenRouter — documented request fields (openrouter.ai/docs/features/provider-routing)
The `provider` object fields:

| Field | Type | Meaning |
|---|---|---|
| `order` | string[] | provider slugs to try, in order (the pinning primitive) |
| `allow_fallbacks` | boolean | default `true`; enable/deny backup providers |
| `require_parameters` | boolean | only providers supporting every request param |
| `data_collection` | "allow"\|"deny" | exclude providers that may store data |
| `zdr` | boolean | restrict to Zero-Data-Retention endpoints |
| `only` / `ignore` | string[] | allow / skip specific provider slugs |
| `quantizations` | string[] | filter by weight precision — `int4,int8,fp8,fp16,bf16,fp32,…` |
| `sort` | string\|object | order by price / throughput / latency |
| `preferred_min_throughput` / `preferred_max_latency` / `max_price` | — | soft QoS + price ceiling |

**Live provider-metadata shape** (0-cost metadata endpoints):
- `GET /api/v1/models` → `data[]` of 338 models (probe output). Each Claude entry top-level keys:
  `architecture, benchmarks, canonical_slug, context_length, created, default_parameters,
  description, expiration_date, hugging_face_id, id, knowledge_cutoff, links, name,
  per_request_limits, pricing, reasoning, supported_parameters, supported_voices, top_provider`.
  `anthropic/claude-sonnet-5` pricing block:
  `{prompt: 0.000002, completion: 0.00001, input_cache_read: 0.0000002, input_cache_write:
  0.0000025, input_cache_write_1h: 0.000004, web_search: 0.01}` — i.e. cache-read = 0.1× prompt,
  cache-write-5m = 1.25× prompt, cache-write-1h = 2.0× prompt (matches Anthropic direct, §4).
- `GET /api/v1/models/anthropic/claude-sonnet-4.5/endpoints` → 8 endpoints. Per-endpoint metadata
  carries `provider_name` (Amazon Bedrock / Anthropic / Google / …), `quantization` (all report
  `"unknown"` for Claude — precision is not disclosed for these hosted endpoints, so a
  `quantizations` pin can only *exclude the unknowns*, not select a known level), `context_length`,
  `supported_parameters`, and per-endpoint `pricing` incl. `input_cache_read` / `input_cache_write`
  / `input_cache_write_1h` / `discount`.

**What a "quantization" pin means per provider class:** meaningful only for open-weight models
served by multiple GPU hosts at different precisions (OpenRouter's `quantizations` filter). For
Claude (Anthropic-direct or Anthropic-via-OpenRouter) it is a **no-op selector** — quantization is
`"unknown"`/undisclosed, so the pin at most filters out endpoints that decline to declare precision.

### 3.3 Sticky routing (caching-relevant)
OpenRouter "automatically routes subsequent requests to the same provider endpoint after a cached
request to maximize cache hit rates, unless you specify manual provider ordering via
`provider.order`" (prompt-caching doc). So an explicit `order` pin and cache-hit maximization can
conflict — pin deliberately.

---

## 4. CACHING BILLING SEMANTICS (docs-level; billing-evidence probe deferred to paid stage)

### 4.1 Anthropic prompt-caching billing (current, docs-verified)
From the `claude-api` skill `shared/prompt-caching.md` + OpenRouter's Claude passthrough table
(both agree):

| Event | Multiplier vs base input | Field |
|---|---|---|
| Cache **write**, 5-min TTL (default `{"type":"ephemeral"}`) | **×1.25** | `cache_creation_input_tokens` |
| Cache **write**, 1-hour TTL (`{"type":"ephemeral","ttl":"1h"}`) | **×2.0** | `cache_creation_input_tokens` |
| Cache **read** | **×0.1** | `cache_read_input_tokens` |
| Uncached remainder | ×1.0 | `input_tokens` |

- TTL options: **5 minutes (default)** and **1 hour**. Max 4 `cache_control` breakpoints/request.
- Min cacheable prefix is model-dependent (Opus 4.x / Haiku 4.5 = 4096 tok; Fable 5 / Sonnet 4.6 =
  2048; Sonnet 4.5 = 1024) — a short prefix silently reports `cache_creation_input_tokens: 0`.
- Total prompt = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`.

### 4.2 How the Claude Code result envelope / stream usage reports it — cross-check vs Phase 2
The **exact field names the Phase-2 stream parser already folds** (confirms the caching field names
above are the ones our capture reads):
`benchmarks/src/pydocs_eval/agent_track/_parse.py:176-177`
```python
return (
    int(usage.get("cache_read_input_tokens", 0)),        # -> StreamStats.cache_read_tokens
    int(usage.get("cache_creation_input_tokens", 0)),    # -> StreamStats.cache_write_tokens
)
```
- Usage blocks live under `message.usage` on `assistant` stream events (`_parse.py:19-21` docstring,
  `:164-178` reads `event.usage` OR `message.usage`). Summed across all usage blocks in a run
  (`parse_stream_events`, `:100-135`).
- Run-level **cost** is the CLI's own `total_cost_usd` (USD, not tokens) on the final
  `{"type":"result"}` line (`_parse.py:64-97` `_extract_cost` — top-level `total_cost_usd`, falling
  back to a nested `result.total_cost_usd`/`cost_usd`). So the harness has BOTH the token-level
  cache breakdown (from stream usage) AND the CLI's rolled-up dollar cost.
- Phase-2 evidence (prior-phase verified, cite as such) records the stream event taxonomy:
  `system/init` first line; `assistant`/`user` events carry `message`+content blocks; **last line is
  a `result` message with final text, cost, session metadata** — see
  `docs/superpowers/research/2026-07-18-phase2-evidence-claude-code-artifacts.md:129`.

### 4.3 OpenRouter caching passthrough (docs)
- **Anthropic/Claude on OpenRouter = explicit caching** — requires `cache_control:{"type":
  "ephemeral"}` breakpoints (NOT automatic like OpenAI/Gemini-2.5). Same ×1.25 / ×2.0 / ×0.1
  multipliers as Anthropic direct (§4.1); pricing fields `input_cache_read` / `input_cache_write` /
  `input_cache_write_1h` confirmed live in the models endpoint (§3.2).
- OpenRouter reports cache activity under `prompt_tokens_details` — `cached_tokens` (read) +
  `cache_write_tokens` (write) + a `cache_discount` savings field. **NB naming differs from the
  Anthropic-native `cache_read_input_tokens` / `cache_creation_input_tokens` the Phase-2 parser
  keys on** — if a future arm runs via OpenRouter's OpenAI-compatible surface, the parser's usage
  extractor (`_parse.py:164-178`) would read 0 for cache tokens. (Not a concern for the headless
  `claude` arm, which always emits Anthropic-native usage.)

> The D3 paid-stage probe should verify: (a) `cache_read_input_tokens`/`cache_creation_input_tokens`
> actually populate on a real 2-request identical-prefix run, and (b) `total_cost_usd` on the
> result line reconciles with the token×multiplier math. Docs-level expectation only, here.

---

## 5. RETRY SEMANTICS

### 5.1 What the claude CLI does on provider errors
- **Loop-layer retry, docs-verified:** "Claude Code retries automatically after some upstream
  rejections and disables the rejected capability for the rest of the conversation... The retry
  logic matches on the upstream's error wording, so forward error response bodies unmodified"
  (code.claude.com/docs/en/llm-gateway-protocol → Automatic retry and error forwarding). Recovered:
  `thinking` field, thinking-signature, and mid-conversation-system-message rejections. NOT
  retried: `context_management` and tool-schema-field `400`s (those surface to the caller).
- **Stream event, prior-phase verified (Phase 2):** the CLI emits `system/api_retry` events carrying
  `attempt, max_retries, retry_delay_ms, error_status, error, uuid, session_id`
  (`docs/superpowers/research/2026-07-18-phase2-evidence-claude-code-artifacts.md:129`). These land
  in the verbatim `stream.jsonl` the rollout persists.

### 5.2 Can a retry double-bill or corrupt traces? — reasoned against ADR-0009 capture
Answer: **a loop-layer API retry does NOT re-run tools and cannot double-count tool events; it can
at most add token/cost usage that the raw capture preserves for audit.** Reasoning:
- ADR-0009 dual capture = product-side recorder (per **MCP tool call**, via `PYDOCS_TRACE__*` env
  injected into the server through the `.mcp.json` `env` block — `_command.py:118-145`,
  `rollout.py:50-52`) + runner-side verbatim stream persistence.
- An `api_retry` is a retry of the **Anthropic Messages API call** at the loop layer — the model
  turn is re-requested. The MCP server (which the product-side recorder instruments) is only invoked
  when the model actually emits a `tool_use` and the CLI dispatches it. A retried *model* call that
  hasn't yet produced a tool_use triggers no server-side tool execution ⇒ no duplicate tool-trace
  rows. Tool calls are idempotent w.r.t. the retry because the retry replays the request, not the
  tool.
- **Persistence order is load-bearing (rollout.py:20-22):** raw `stream.jsonl` is written BEFORE any
  parse/fold, so every retry event and every usage block stays recomputable. The Phase-2 parser
  *sums* cache tokens across all usage blocks (`_parse.py:118-135`) — meaning a partial-then-retried
  turn's usage blocks are additively counted. That is the honest number (you were billed for the
  streamed partial if the provider billed it), not a corruption — but it means **`api_retry` on a
  mid-stream failure can inflate summed cache/token counts above a clean single-turn run.** The
  dollar figure the harness trusts for cost is the CLI's own `total_cost_usd` on the `result` line
  (§4.2), which reflects the CLI's authoritative accounting of the whole (retried) run.

### 5.3 In-repo runner timeout / process-group kill policy
`benchmarks/src/pydocs_eval/agent_track/_runner.py`:
- Wall timeout enforced in `run` via `asyncio.wait_for(self._spawn(...), timeout=task_timeout_seconds)`
  (`:112-115`). On `TimeoutError` → **returns `None`** (a "half-pair" the orchestrator discards),
  never raises (`:116-117`, docstring `:19-23`).
- `_spawn` launches with **`start_new_session=True`** (`:129-135`) so the child is its own process
  group; on `asyncio.CancelledError` it calls `_kill_process_group` → `os.killpg(os.getpgid(pid),
  SIGKILL)` (`:182-193`), swallowing `ProcessLookupError/PermissionError/OSError` so a timeout
  always resolves to a clean `None`. This kills the whole `claude` tree (it spawns the MCP server +
  tool subprocesses), preventing orphans.
- `task_timeout_seconds` is the sole bound (`ClaudeAgentRunner.task_timeout_seconds`, `:92`); it
  bounds ANY `_spawn` impl including a hanging fake. There is **no per-API-call timeout knob** in
  the runner — only the outer wall clock. (Ask-runner side has its own `task_timeout_seconds` on
  `AskRunnerSettings`, `run_config.py:126`.)
- The rollout driver mirrors this seam: `RolloutTimeoutError` (`rollout.py:74-76`) for a spawn that
  exceeds the wall budget; the driver depends on the `SpawnSeam` Protocol (`rollout.py:79-89`), not
  the concrete adapter.

### 5.4 The "unrecorded_by_client" gap (relevant to retry/sampling determinism)
Headless `claude` exposes **no temperature / top_p / seed knob** — the rollout stamps these `null`
and names them in `unrecorded_by_client` rather than omitting them (`rollout.py:24-27, 61-64`
`_UNRECORDED_SAMPLING = ("temperature", "top_p", "seed")`; ADR 0009 R2 "verified gap"). Consequence
for Phase 3/4: **sampling is not pinnable through the loop** — run-to-run nondeterminism (and any
retry) cannot be suppressed via a seed. This is the deepest limitation for reproducibility of the
Claude-Code arm and should frame any variance-reduction plan.

---

## 6. Evidence commands (reproducible, 0-cost)

```
# OpenRouter model catalog shape + Claude pricing (metadata, no key)
curl -s https://openrouter.ai/api/v1/models            # 338 models; anthropic/claude-sonnet-5 pricing block
curl -s https://openrouter.ai/api/v1/models/anthropic/claude-sonnet-4.5/endpoints   # 8 endpoints, quantization="unknown"

# OpenRouter Anthropic-format route existence (0-cost 401 probe)
curl -s -o /dev/null -w "%{http_code}" -X POST https://openrouter.ai/api/v1/messages -d '{...}'          # 401 (route exists)
curl -s -o /dev/null -w "%{http_code}" -X POST https://openrouter.ai/api/v1/chat/completions -d '{...}'  # 401

# Docs fetched (via WebFetch, redirect docs.claude.com -> code.claude.com)
#   code.claude.com/docs/en/settings         (ANTHROPIC_* env vars, --model aliases)
#   code.claude.com/docs/en/llm-gateway       ("doesn't support routing ... to non-Claude models")
#   code.claude.com/docs/en/llm-gateway-protocol  (API formats table, /v1/models Claude-gated discovery)
#   openrouter.ai/docs/features/provider-routing  (provider object fields incl. quantizations)
#   openrouter.ai/docs/features/prompt-caching    (Claude explicit caching, ×1.25/×2/×0.1, prompt_tokens_details)
#   WebSearch: LiteLLM Anthropic /v1/messages passthrough + non-Anthropic-models tutorial
```

## 7. Open questions for the paid D3 probe / Phase 4
1. Does OpenRouter's `/api/v1/messages` accept the full Claude-Code request shape (`output_config`,
   `context_management` beta, adaptive `thinking`) or only a reduced subset? (401 blocked
   verification.)
2. On a real 2-request identical-prefix Claude run, do `cache_creation_input_tokens` /
   `cache_read_input_tokens` populate as expected, and does `total_cost_usd` reconcile with
   token×multiplier?
3. If a non-Claude target is in scope, is a LiteLLM Anthropic-Messages shim acceptable given
   Anthropic's explicit "unsupported" stance — and does the Phase-2 stream/usage parser survive the
   translated stream shape (esp. cache-token field renaming to `prompt_tokens_details.*`)?
4. Exact `system/api_retry` billing behavior on a mid-stream failure: is the streamed partial billed
   and then re-billed on retry, or only the successful attempt? (Determines whether summed usage
   over-counts.)
