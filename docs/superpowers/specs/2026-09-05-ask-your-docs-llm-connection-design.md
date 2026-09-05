# Ask-your-docs LLM connection: one endpoint, a renewable bearer, model discovery — Design

| | |
|---|---|
| **Version** | 0.5 (draft; review findings of 2026-09-05 applied; reconciled with the owner decisions and with the implementation plan the same day) |
| **Status** | Proposed — encodes the ten owner decisions D1–D10 ratified 2026-09-05 as normative rules (§3) plus four review-driven hardening rules H1–H4 (§3) that narrow none of them. No code written. |
| **Date** | 2026-09-05 |
| **Owner** | msobroza |
| **Audience** | The implementer of the follow-up plan; reviewers of the ask-your-docs harness. |
| **Component** | `python/pydocs_mcp/harness/ask_your_docs/` (agent, app, cli, binding, multimodal, architectures), `python/pydocs_mcp/retrieval/config/ask_your_docs_models.py`, `python/pydocs_mcp/defaults/default_config.yaml`, `examples/harness/ask_your_docs_agent/`. |
| **Anchors** | Every claim about existing code cites `file:line` at worktree HEAD `bf21e8a`. Toolkit claims and their line anchors refer to the versions locked in `uv.lock` — `openai` 1.109.1, `langchain-openai` 1.1.9, `httpx` 0.28.1, `streamlit` 1.59.1, `langchain-core` 1.4.9 — as installed in the main checkout's `.venv` (`~/Projects/pyctx7-mcp/.venv`). The plan's first step is `uv sync --frozen` in the worktree (its `.venv` did not hold the locked set at review time), and AC-38 pins the SDK behaviors the design leans on against the installed, locked SDK so a future bump cannot silently disable renewal. |
| **Companions** | `docs/superpowers/plans/2026-09-04-ask-your-docs-branch-scope-ui.md` (pending; this design coexists with it, §4.1 and §7.4), `docs/superpowers/specs/2026-07-14-ask-your-docs-vision-sidecar-spec.md` (commit `6d2ee9e`, not on this branch; its `describe_images` seam and `vision_llm` context field are adopted, §4.8), `docs/tool-contracts.md` (frozen nine-tool surface; this design adds nothing to it). |

**Goal:** Let the ask-your-docs chat agent talk to one OpenAI-format endpoint
whose bearer comes from an internal token service (renewed on demand) or from
an environment variable, discover the models that endpoint serves, and route
image work to the main model or to a second model on the same endpoint — all
configured in one YAML block, overridable per launch and per session, without
a secret ever landing in YAML, argv, a log line or the UI, and without adding
a tool, a parameter or an envelope field to the frozen MCP surface. A
deployment with no `ask_your_docs.llm` block behaves byte for byte as today.

---

## Abstract

Today the chat model is built as `ChatOpenAI(model=model, base_url=base_url)`
with no credential of its own (`agent.py:340`): the key is whatever
`OPENAI_API_KEY` holds when the process starts, forever. The two capability
probes and the `/models` lookup open their own unauthenticated clients
(`multimodal.py:168-179`, `:182-201`), and nothing lists the models an
endpoint serves — the sidebar has a free-text "Model" field
(`app.py:103`). This design adds one value object, `LlmConnection`, resolved
once per session by a pure precedence function (YAML < environment < CLI <
dialog, §4.3), and one **bearer source** behind a Protocol with a Null
Object for the no-auth case (§4.4). The bearer follows the effective
endpoint at every tier (D3); when an override points the session at a
different origin than the YAML `base_url`, the status line says so and one
warning is logged (H1, §4.3). Every LLM-bound HTTP call — the agent, reformulation,
every image call, both probes, the model listing, the "Test connection"
action — goes through one client factory (§4.5) that installs the bearer as
a callable `api_key` (the SDK re-reads it before every attempt) and, for a
token service, an `httpx.Auth` wrapper on both httpx clients that renews the
bearer on a `401` (deduplicated across concurrent requests, rate-limited)
and retries the same request exactly once; every auth failure that reaches a
person, a tool result or a trace is passed through one redaction helper
(§4.4). The sidebar's four text inputs collapse into one status line
and a "Connection" dialog (§4.9). `vision: true | false | null | {model}`
replaces the guesswork for deployments that know their model (§4.7); a second
model on the same endpoint takes every image call through the
`describe_images` seam (§4.8). The shipped `preferred_architecture` flips
from `vision_subagent` to `inline` (§4.7, a dated default change). The eval
binding keeps `model` and `base_url` and picks auth and vision up from the
same YAML file (§4.11); its delivery-map digest and control-arm byte identity
are unchanged.

---

## 1. Context and problem statement

### 1.1 What exists today

- **One implicit credential, read once.** `build_agent` constructs
  `llm = ChatOpenAI(model=model, base_url=base_url)` (`agent.py:340`) — no
  `api_key`, no `timeout`, no `max_retries`, no custom HTTP client. The
  `langchain-openai` field default reads `OPENAI_API_KEY` from the
  environment at construction (`langchain_openai/chat_models/base.py:533-541`);
  tests set the variable because "ChatOpenAI requires a credential at
  construction" (`tests/harness/ask_your_docs/test_prompt_seam.py:86-87`).
  The same `llm` object serves reformulation (`agent.py:391-416`), the
  ReAct loop, the vision node (`architectures/vision_subagent.py:24-78`) and
  the `reinspect_images` tool (`reinspect.py:30-91`).
- **Two probes on their own unauthenticated clients.** Rung 3 of the
  capability ladder GETs `{base_url}/models` through a bare
  `httpx.AsyncClient(timeout=timeout)` (`multimodal.py:168-179`); rung 4
  builds a second `ChatOpenAI(model=model, base_url=base_url,
  timeout=timeout, max_retries=0)` (`multimodal.py:187`). Neither carries a
  bearer that did not come from `OPENAI_API_KEY`, so behind a token service
  rung 3 returns `401` (falls through silently, `multimodal.py:239-242`) and
  rung 4 fails as a server error (falls through, `:257-260`).
- **No model discovery.** The sidebar renders `Model` as a free-text input
  defaulting to `gpt-4o-mini` (`app.py:103`); nothing asks the endpoint what
  it serves.
- **Connection settings scattered across four sidebar inputs.** Workspace,
  Model, Base URL, pydocs config (`app.py:102-105`), prefilled from
  `PYDOCS_WORKSPACE`, `LLM_MODEL`, `OPENAI_BASE_URL`, `PYDOCS_CONFIG`
  (`app.py:1-12`, `cli.py:18-23`), plus a `vision: yes (static)` caption
  (`app.py:106-110`). The CLI maps `--model` / `--base-url` onto those
  environment variables (`cli.py:70-73`) and re-executes `streamlit run`
  (`cli.py:66-90`).
- **The agent cache key ignores auth.** `get_agent` is `@st.cache_resource`
  on `(workspace, model, base_url, config)` (`app.py:80-91`); `get_capabilities`
  on `(model, base_url, config)` (`app.py:71-77`).
- **The send loop has no exception boundary.** `app.py:218-276` wraps no
  `try/except` around `reformulate` / `ask`; the `st.error` at `:231-233` is
  the reject-verdict path, so any exception from the LLM call lands in
  Streamlit's uncaught-exception renderer with its full message and
  traceback in the browser.
- **The YAML block has no connection keys.** `AskYourDocsConfig` holds
  `architecture`, `multimodal`, `images` (`ask_your_docs_models.py:70-79`);
  the shipped block is `default_config.yaml:345-362`. The pending UI plan
  appends `scope` (plan Task 1).
- **The eval binding passes `model` and `base_url` straight through.**
  `AskYourDocsRunnerSettings(workspace, model, trace_root, base_url,
  pydocs_config, architecture, tool_names, max_agent_turns, harness)` with
  `extra="forbid"` (`binding.py:150-163`); `_build_and_execute` calls
  `build_agent(settings.workspace, settings.model, base_url=settings.base_url,
  ...)` and unpacks a 2-tuple (`binding.py:352-356`). `settings.harness`
  defaults to `AskYourDocsConfig()` (`binding.py:163`) and is populated only
  from the arm's opaque `settings` mapping
  (`benchmarks/src/pydocs_eval/optimize/arms.py:80`; every shipped arm uses
  `settings: {workspace, model}`). The binding never calls
  `AppConfig.load`: `pydocs_config` is used only to build the serve
  subprocess argv (`binding.py:323`, `:356`), so nothing in the file it
  names reaches `build_agent` today.
- **The retrieval-side LLM client is a different thing.** The
  `llm:` tree-reasoning client (`retrieval/llm_clients/openai.py`, bounded by
  `_with_retry_async` at `:57`) and the OpenAI embedder are separate
  composition roots and are out of scope (D1).

### 1.2 The three gaps

1. **No auth seam.** A bearer that is not `OPENAI_API_KEY`-at-startup — a
   short-lived token from an internal service, a key under another variable
   name, or no bearer at all for a local server — has no place to enter. When
   a token expires mid-session the only remedy is a restart.
2. **Unauthenticated probes.** Both probes and the `/models` lookup bypass
   whatever credential the agent would use, so behind an authenticated
   endpoint the ladder always lands on `default` or a false `static` verdict.
3. **No model discovery.** The person must know the model id by heart; a typo
   is discovered on the first question.

### 1.3 What this design does not touch

The `llm:` tree-reasoning client, the OpenAI embedder, the embedding
pipeline (in-process sentence-transformers Qwen3-Embedding-4B, GPU at index
time via `configs/index_gpu.yaml`, OpenVINO CPU at serve time via
`configs/serve_cpu_openvino.yaml`), the MCP surface, the `pydocs-mcp serve`
subprocess, the trace contract, and the skill delivery map.

### 1.4 Terms

- **Connection**: the frozen `LlmConnection` value object (§4.2) — endpoint,
  model, auth mode and vision rule for one session, resolved once by
  precedence (§4.3). "The connection" is always this object, never the
  YAML block or the dialog's widgets.
- **Bearer**: the value sent as `Authorization: Bearer <value>` — a
  token-service token or an external key. A **bearer source** is the
  `BearerSource` object that holds it (§4.4); `NoBearer` is the Null Object
  for the no-auth case.
- **Auth identity**: `(auth_mode, token_url or api_key_env or "",
  block_present)` — the part of the connection that names *where* the
  bearer comes from without carrying it; the agent cache key and the bearer
  registry are keyed on it (§4.9). The third element keeps the lenient
  no-block bearer and an explicit `auth.api_key_env: OPENAI_API_KEY` bearer
  apart in one process. Never a token or a key value.
- **Origin change**: the effective `base_url`'s `(scheme, hostname, port)`
  differs from the YAML `base_url`'s; the bearer still follows the
  effective endpoint, and the UI plus one log line flag it (H1).
- **Renew**: replace the cached token by fetching the token service again —
  on a status in `renew_on_status`, or manually from the dialog. Distinct
  from the SDK's own **retry** (408/409/429/5xx), which re-sends with
  whatever the bearer source currently holds.
- **Tier**: one level of the precedence fold — YAML, environment, CLI,
  dialog (§4.3). The **winning tier** of a field is descriptive (a log
  line), not a field of the connection.
- **Listing**: the `ModelListing` record from `GET {base_url}/models`
  (§4.6); a failed listing is a record with `error` set, never an exception.
- **Vision rule**: how the `vision` key resolves — `DETECT`, `MULTIMODAL`,
  `TEXT_ONLY`, `SEPARATE_MODEL` (§4.2, §4.7). The **image model** is the
  model that receives image blocks: the main model, or `vision.model`.
- **Capability source**: where a multimodal verdict came from
  (`CapabilitySource`, §4.7); `CONFIGURED` is the source for every verdict
  declared by the `vision` key.
- **Byte identity**: with no `ask_your_docs.llm` block, the same
  `ChatOpenAI(...)` kwargs, the same requests, the same prompt and the same
  eval digest as at `bf21e8a` (§7.2). The one sanctioned difference is
  listed in R2.

---

## 2. Goals and non-goals

### 2.1 Goals

- G1 One YAML block, `ask_your_docs.llm`, for endpoint, auth, vision and a
  preselected model; every key single-sourced; unknown keys rejected.
- G2 One bearer source per session shared by every LLM-bound HTTP call.
- G3 A token that renews itself on the first `401` and is retried once with
  the new bearer, transparently to the SDK's own retry loop.
- G4 Model discovery through `GET {base_url}/models` with the same bearer,
  cached briefly, failing soft.
- G5 One status line plus one dialog in the UI; dialog choices live for the
  session only.
- G6 Vision declared (`true` / `false`), detected (`null`) or delegated
  (`{model}`); `auto` routes inline by default.
- G7 Byte identity without the block: the same `ChatOpenAI` construction,
  the same requests, the same prompt, the same eval digest.
- G8 Secrets never in YAML, argv, logs, the UI or the cache key.

### 2.2 Non-goals

- No profile list, no per-model endpoints, no second base URL (D2): one
  endpoint per session.
- No periodic token refresh timer (D4).
- No `--api-key` flag and no key field in the dialog (D2).
- No change to the retrieval `llm:` client or the embedders (D1).
- No new MCP tool, parameter or envelope field (CLAUDE.md §"MCP API surface
  vs YAML configuration").
- No persistence of dialog choices to disk.
- No adoption of the sidecar spec's `VISION_*` environment variables, its
  `--vision-model` / `--vision-base-url` flags, or its sidebar fields (D6).

---

## 3. Decisions (normative)

Each rule restates an owner decision of 2026-09-05; the rationale column is
the reason the decision was taken, not an invitation to re-open it.

| Rule | Statement | Rationale |
|---|---|---|
| **R1 Scope** (D1) | The design covers the agent chat model, reformulation, every image call (vision node, reinspect tool, image probe), the endpoint probe, model listing, the CLI, the Streamlit UI and the eval binding — all under `harness/ask_your_docs/`. The retrieval tree-reasoning client and the OpenAI embedder are untouched; embeddings stay in-process. | One composition root per concern; the harness is a consumer of the backbone, not the other way round. |
| **R2 Config** (D2) | One block `ask_your_docs.llm` (§5). Keys: `base_url`, `model`, `auth` (exactly one of `token_url` / `api_key_env`; block absent = no bearer), `token_field`, `renew_on_status`, `vision`. `auth.api_key_env` with `base_url: null` is a valid combination (D2: vendor default endpoint, external key). No `llm` block ⇒ today's behavior byte for byte (`OPENAI_API_KEY` read by the SDK). The single sanctioned no-block difference is that rung 3 and the model listing now carry `Authorization: Bearer $OPENAI_API_KEY` when that variable is set (D5); when it is unset they stay unauthenticated, as today. Secrets come from the environment or the token service only. | A single endpoint keeps the identity of "the connection" one tuple; YAML is the A/B-testable layer. |
| **R3 Precedence** (D3) | `YAML ask_your_docs.llm` < environment (`OPENAI_BASE_URL`, `LLM_MODEL`) < CLI (`--base-url`, `--model`) < the connection dialog (session only). `auth`, `token_field`, `renew_on_status` and `vision` are read from the `AppConfig` layer only (the file plus its `PYDOCS_ASK_YOUR_DOCS__LLM__*` env overlay); the `OPENAI_BASE_URL` / `LLM_MODEL`, CLI and dialog tiers override only `base_url` and `model`. | Mirrors `AppConfig`'s defaults → overlay → env layering and today's CLI → env → sidebar prefill chain (`cli.py:66-90`, `app.py:102-105`). |
| **R4 Token lifecycle** (D4) | Fetched lazily on the first request, cached in-process; on a status in `renew_on_status` (default `[401]`) the bearer is renewed and the same request is retried once with the new bearer; a second such status surfaces the error. No timer. A manual Renew action in the dialog. The token-service fetch is bounded: 3 attempts, 2 s / 4 s backoff (the values of `multimodal.py:99-101`, defined once more in `bearer_tokens.py`). The token is never logged or written; the UI shows only the renewal time and the last four characters, inline in the status line and in the dialog's auth row (D4). | The SDK never retries `401` (`openai/_base_client.py:750-786`), so the renew-and-retry must live below the SDK: an `httpx.Auth` flow. The SDK does retry 408/409/429/5xx and re-reads a callable `api_key` before every attempt (`openai/_client.py:304-311`, `_base_client.py:963-965`), so those attempts carry the current cached bearer for free. |
| **R5 Model discovery** (D5) | `GET {base_url}/models` with the same bearer; `{"data": [{"id": ...}]}`; listed in the dialog with a refresh action; cached per `(base_url, auth identity)` for a short TTL held in one constant; failure is non-fatal (text-field fallback plus the error). Both probes use the authenticated client. | The listing is a convenience, not a gate; the probes were already best-effort. |
| **R6 Routing** (D6) | `auto` builds `inline` when the main model is multimodal, `vision_subagent` only when `vision.model` names a separate model, `text_react` otherwise. Shipped `multimodal.preferred_architecture` flips `vision_subagent` → `inline`. `reinspect_images` unchanged (`max_reinspect_per_turn: 0` removes it). A separate vision model adopts the sidecar spec's `describe_images` seam and `vision_llm` context field, but its identity lives in `ask_your_docs.llm.vision` and it shares the endpoint and bearer. `vision.model` equal to the main model ⇒ treated as `vision: true` with a warning. | A multimodal main model answers and sees in one prompt; a second describe hop is only worth it when a cheaper or better vision model exists. |
| **R7 UI** (D7) | The sidebar Connection section is one status line (endpoint host, model, auth state, vision verdict with source) plus a "Connection" button opening a `st.dialog` with Base URL, auth status row (+ Renew for a token service), Model selectbox fed by the listing (refresh action, text-field fallback), a status caption, "Test connection", Apply. Dialog choices live in `st.session_state`, never persisted. The agent cache key is `(workspace, model, base_url, config path, auth identity)`; renewing never rebuilds the agent. | Four always-visible inputs became noise once the endpoint is configured; a dialog keeps them one click away. |
| **R8 Eval binding** (D8) | `AskYourDocsRunnerSettings` keeps `model` and `base_url`; auth and vision come from the `pydocs_config` file's `ask_your_docs.llm` — the binding loads that block itself through `AppConfig.load(explicit_path=...)` when the arm's `harness` settings carry none (§4.11) — and reach the agent through the same client factory; delivery maps, digests and control-arm byte identity are unchanged when no block is present. | The binding is a settings-in, trajectory-out contract; auth is deployment, not an experiment variable. |
| **R9 Errors** (D9) | Token service unreachable ⇒ loud error naming the URL (credentials stripped, H4) and the attempt count. Two consecutive renew statuses ⇒ the error surfaces with the bearer's last four characters. Listing failure ⇒ non-fatal fallback. Unknown key under `llm` ⇒ rejected at config load. | CLAUDE.md: fail loudly with context; degrade only at sanctioned boundaries (the listing is one). |
| **R10 Documents** (D10) | This spec; `configs/index_gpu.yaml` gains no `llm` block and is unchanged byte for byte; `configs/serve_cpu_openvino.yaml` gains the block (`base_url: http://llm.internal/v1`, `auth.token_url: http://localhost:8899/access-token`, `vision: true`, §5.3); the example README paragraph; a CHANGELOG entry. The plan is written afterwards by the main session. | — |

Four hardening rules came out of the 2026-09-05 review. They are not owner
decisions, they narrow none, and they bind the plan the same way:

| Rule | Statement | Rationale |
|---|---|---|
| **H1 Origin change is visible** | The bearer follows the effective `base_url` at every tier (D3). When a YAML `base_url` is set and the effective `base_url`'s `(scheme, hostname, port)` differs, `resolve_llm_connection` logs one `bearer_origin_changed` JSON warning with both origins in `display_url` form, the status line's auth cell appends `⚠ endpoint differs from ask_your_docs.llm.base_url`, and the dialog's Base URL help text says the bearer is sent to whatever endpoint is entered. With `base_url: null` (`api_key_env` only, D2) there is no origin to compare against, so no origin change is flagged (H2 still applies) — the key follows `OPENAI_BASE_URL` exactly as the SDK does today. `auth.token_url` with `base_url: null` stays rejected at config load (E14): a token service authenticates one internal endpoint, so the block must name it. | `auth` is YAML-only but `base_url` is overridable from the page; the person must see where the bearer goes. |
| **H2 Cleartext is visible** | A bearer attached to an `http://` endpoint on a non-loopback host logs one `bearer_over_cleartext` JSON warning at resolution and the status auth cell appends `⚠ http`; loopback hosts (`localhost`, `127.0.0.1`, `::1`) are exempt; no config key, never an error. The rule covers the bearers this design introduces (`block_present`); the no-block path stays byte-identical to today (R2). | The token crosses the network in the clear; the person must see it, and the owner's example endpoint is plain http. |
| **H3 Renewal discipline** | Renewal is compare-and-swap on the token the caller actually sent, so concurrent `401`s cost one fetch; a renew within `_MIN_RENEW_INTERVAL_SECONDS` of the last successful one returns the cache; `renew_on_status ⊆ {401, 403, 407}`; bearer failures (`TokenServiceError`, `BearerUnavailableError`, `BearerRejectedError`) are never retried by the probe ladder and never cached as a capability verdict. | Two in-flight questions on one expired token must not fetch two tokens; a persistently rejecting endpoint must not turn every SDK attempt into a token fetch; a token service that is down at build time must fail loudly, not degrade to text-only for the process lifetime. |
| **H4 Redaction** | One `redact_bearer` helper and one `translate_auth_errors` boundary (all auth modes) scrub every auth failure before it reaches `st.error`, a tool-result string, a trace or a caption; `display_url` strips userinfo and query from every URL in a message or the UI; `last_four` is the last four characters of the bearer (`token[-4:]`, `""` only for an empty bearer), shown inline in the UI (D4) and never logged; E2/E3 never quote body bytes. | Gateways echo the presented credential in `401` bodies; `token_url` may carry credentials. |

Repository rules that also bind the implementation (CLAUDE.md): `StrEnum`
vocabularies with UPPER_SNAKE members, plain-English names, files under 500
lines, functions of 4–20 lines, at most two indentation levels, error
messages carrying the offending value and the expected shape, structured
JSON logs, timeouts and bounded retries on every network call, the Null
Object pattern for optional service dependencies, vendor-neutral docs ("an
OpenAI-format endpoint", never another vendor's product name), and the README
jargon audit.

### 3.1 Review proposals not adopted

The 2026-09-05 review proposed three narrowings of ratified decisions. None
is adopted; each is recorded here so it is not re-proposed:

| Proposal | Why not |
|---|---|
| Refusing the bearer to any endpoint whose origin differs from the YAML `base_url`. | Rejected: D3 ratified the override tiers for the endpoint and the owner wants one simple rule; a visible warning (H1) is enough. |
| An `auth` key that opts into cleartext, with a config error for plain-http non-loopback endpoints. | Rejected: not in D2's key list, and the owner's own example endpoint is plain http; a visible warning (H2) is enough. |
| A floor of twelve characters under which the last four characters are hidden. | Rejected: D4 asks for the last four inline, whatever the token length. |

### 3.2 Approaches considered

**A periodic refresh timer for the token (rejected, D4).** A background
thread renewing the token every N minutes needs a lifetime (Streamlit
reruns, the binding's per-sample loop), a clock the token service does not
publish (no expiry in a plain-text body), and still cannot avoid the `401`
that arrives between two ticks. Renew-on-`401` needs no lifetime and no
clock; the manual Renew covers the rest.

**An `openai` SDK subclass overriding `_should_retry` / `_prepare_options`
(rejected).** It would put the renewal inside the SDK's own retry loop
(where `401` is deliberately not retried, `_base_client.py:750-786`), tie
the design to private methods that move between SDK releases, and still
need a custom client for `langchain-openai` to honor it. An `httpx.Auth` on
the two clients is the public seam the SDK dispatches through (`send` with
client auth, `_base_client.py:971-986`; AC-38 pins it).

**A callable `api_key` alone (rejected as insufficient).** The SDK re-reads
a callable before every *attempt* (`_client.py:304-311`) but never retries
a `401`, so the first expired token costs the question. Kept as the
*source* of the header, paired with the Auth flow that does the one retry
(§4.4 "why both mechanisms").

**A raw `GET {base_url}/models` through a hand-built `httpx.AsyncClient`
(rejected, D5).** Today's rung 3 does this unauthenticated
(`multimodal.py:168-179`); keeping it would mean a second copy of the
bearer, timeout and retry wiring next to the factory's. The SDK's
`models.list()` on an `openai.AsyncOpenAI` built from the factory's own auth
decision gets the effective base URL, the bearer, the timeout and the
renewing Auth for free, and returns the same `{"data": [{"id": ...}]}` items
(`_entry_hints_vision` keeps reading the extras).

**Profiles / a per-model endpoint list (rejected, D2).** One session talks
to one endpoint; a second model for images is a model id on the same
endpoint (`vision.model`), not a second connection. Profiles would make
"the connection" a list and the cache key, the bearer registry and the
status line would each need a selector.

**Keeping the four sidebar inputs and adding a fifth for the token (rejected,
D2 + D7).** Secrets never enter the UI; and once the endpoint is configured
in YAML the inputs are noise on every page load. One status line plus a
dialog keeps them one click away and gives the model list and Test
connection a home.

---

## 4. Architecture

### 4.1 Module map and line budgets

New modules under `python/pydocs_mcp/harness/ask_your_docs/` (all lazy
about heavy imports, like the rest of the subpackage; the subpackage stays
`mypy`-excluded):

| Module | Budget | Owns |
|---|---|---|
| `llm_connection.py` (new) | ~260 | `AuthMode` / `VisionRule` re-exported from `ask_your_docs_models.py`, where they live (`bearer_tokens.py` needs `AuthMode` without importing this module, and the UI plan's precedent keeps harness enums in the mypy-checked config module); the frozen `LlmConnection` value object (with `configured_base_url` and the derived `origin_changed` / `cleartext_bearer` flags, H1/H2); the `ConnectionOverride` record (one shape for the CLI and dialog tiers); `resolve_llm_connection` (pure precedence, §4.3, emits the `bearer_origin_changed` / `bearer_over_cleartext` warnings); `connection_identity` (cache key, §4.9); `bearer_for_connection` / `clear_bearer_registry` (the per-identity registry lives here, not in `bearer_tokens.py`, so the import direction stays one-way); `connection_auth_kwargs` (the one auth decision the factory and the listing share, §4.5); `run_connection_test` (pure async, §4.9 item 5 — the dialog only renders its result; not named `test_*`, which pytest would collect); `build_chat_model` (the client factory, §4.5; imports `langchain_openai` and `httpx` function-locally); `resolve_vision_capabilities` (the single §4.7 call site). |
| `bearer_tokens.py` (new) | ~300 | `BearerSource` Protocol; `NoBearer` (Null Object); `EnvironmentKeyBearer` (lenient for the no-block path, strict for `auth.api_key_env`); `TokenServiceBearer` (lazy fetch, cache, compare-and-swap renew, rate limit — H3); `RenewOnStatusAuth(httpx.Auth)` and `StripAuthorizationAuth(httpx.Auth)`; `redact_bearer`, `translate_auth_errors`, `display_url`, `display_host` (H4); `peek()` on every source and `TokenServiceBearer.last_error` (the failed-renewal cause); the three token-fetch constants; the three exception classes. |
| `model_listing.py` (new) | ~110 | `ModelListing` record; `fetch_models_payload` (`GET /models` through an `openai.AsyncOpenAI` built from `connection_auth_kwargs` — the same bearer, timeout and renewing `Auth` as the chat model — one path for a configured and for the vendor-default `base_url`, §4.6); `fetch_model_ids`; `cached_model_listing` with the TTL constant; rung 3 of the ladder calls `fetch_models_payload` so the probe and the listing share one call. |
| `connection_dialog.py` (new, Streamlit-only) | ~270 | `ConnectionActions` (the page-injected callbacks), `auth_cell` / `vision_cell`, `render_connection_status_line` (an `st.caption`), `open_connection_dialog` (the `@st.dialog` body), widget and session-state keys (§4.9); rendering only — `run_connection_test` lives in `llm_connection.py`. |
| `reformulation.py` (new) | ~50 | `reformulate` and `_history_line`, moved verbatim from `agent.py:373-416` (44 lines) — the line-budget extraction that keeps `agent.py` under 500 (see the `agent.py` row). Reformulation is already a distinct consumer of the chat model (R1) with no other coupling to `build_agent`; the two importers move with it: `app.py:18` (`from ...agent import ask, build_agent, reformulate, weave_attachments` loses `reformulate` and gains `from ...reformulation import reformulate`) and `tests/harness/ask_your_docs/test_image_attachment.py:139` (`_history_line`). No re-export is left in `agent.py`. |

Edits:

| File | Today | Change |
|---|---|---|
| `agent.py` (468) | `ChatOpenAI(...)` at `:340`; `detect_capabilities` at `:359-361`; `_build_architecture` at `:152-180` | New keyword `connection: LlmConnection \| None = None` on `build_agent` (and on the UI plan's `build_agent_with_scope_capabilities` when it lands); `llm = build_chat_model(...)`; capabilities via `resolve_vision_capabilities` when `capabilities is None` (§4.7); `_build_architecture` gains `vision_llm` / `vision_capabilities` / `bearer` (§4.8). Net +25 here. The budget closes as follows: 468 today, ≤487 after the UI plan (its Task 7), +25 from this design = 512, which is over the 500-line rule; the UI plan's Task 4 already moves `scope_prefix` (`:140-149`, 10 lines) to `question_scope.py` (that move is inside its ≤487), so this design names its own extraction: `reformulate` + `_history_line` (`:373-416`, 44 lines) move to the new `reformulation.py` (row above), landing `agent.py` at ~468 with both designs in — the connection helpers live in `llm_connection.py`, never here. AC-29 pins the number. Whichever of the two designs lands second performs the `reformulation.py` move if it has not happened yet. |
| `app.py` (276) | Four sidebar inputs `:102-105`, badge `:106-110`, caches `:55-91`, send loop `:218-276` | Status line + dialog opener; `page_bearer(connection)` (the registry's object, or the `connection_bearer` test seam); `get_agent(workspace, model, base_url, config, identity, _connection, _bearer)` — the plan's order with `identity` appended and the two unhashed objects after it (§4.9); the bearer preflight before capabilities; a `try/except` boundary around `reformulate` / `ask` that renders the redacted error (§4.9). |
| `cli.py` (94) | `_ENV` map `:18-23`, flag→env copy `:70-73` | Unchanged flags; `--model` help text loses its `gpt-4o-mini` default wording (the default now comes from precedence, §4.10). |
| `binding.py` (396) | `build_agent(...)` call `:352-369` | `connection_block_for_binding(settings)` reads `settings.harness.llm` or, when that is `None`, `AppConfig.load(explicit_path=Path(settings.pydocs_config)).ask_your_docs.llm`; passes `connection=` resolved from that block + `settings.model` + `settings.base_url` (§4.11). |
| `multimodal.py` (293) | `_with_rung_retry :133-148`, `_default_http_get :168-179`, `_default_probe_llm :182-201`, `DetectionSource :22`, rungs `:234-260` | Both seams take the connection + bearer and go through the factory / `model_listing`; `_with_rung_retry` and both rungs let `TokenServiceError` / `BearerUnavailableError` propagate and `detect_capabilities` does not cache on them (H3); `DetectionSource` becomes the `CapabilitySource` StrEnum with a new `CONFIGURED` member (§4.7). |
| `architectures/base.py` | `AgentBuildContext :24-49` | `vision_llm`, `vision_capabilities`, `bearer` with identity / Null Object defaults; `ImageModelRoute` StrEnum ClassVar (§4.8). |
| `architectures/auto.py` (42) | routes on `ctx.capabilities.multimodal :31` | Routing table of §4.7. |
| `architectures/vision_subagent.py`, `reinspect.py` | inline `llm.ainvoke` | Both call `describe_images` on the image model (§4.8). |
| `attachments.py` | — | `describe_images` (adopted from the sidecar spec §3.3). |
| `ask_your_docs_models.py` (87) | three sub-models | `LlmAuthConfig`, `VisionModelConfig`, `LlmConnectionConfig`; `AskYourDocsConfig.llm`; `preferred_architecture` default → `inline` (§5). Stays under 200 even with the UI plan's `ScopeDefaultsConfig`. |
| `defaults/default_config.yaml` | block `:345-362` | `llm:` keys appended; `preferred_architecture: inline`. |
| `pyproject.toml` | `streamlit>=1.43 :143` | The UI plan raises the floor to `>=1.57`; `st.dialog` (1.34+) needs nothing beyond it. No new dependency: `httpx` is already transitive via `openai`. |

Coexistence with the pending UI plan: the connection is resolved before the
agent is built, so the plan's `build_agent_with_scope_capabilities` simply
gains the same `connection=` keyword and `build_agent` stays the 2-tuple
wrapper; `BuiltAgent` gains one field, `vision_capabilities` (§4.8);
`get_agent` keeps the plan's parameter order and appends `identity` (§4.9);
the plan's `page_scope_capabilities(workspace, model, base_url, config)`
(Task 9) becomes `page_scope_capabilities(workspace, connection, config)` —
this is a signature change to the plan's page entry point, recorded as an
edit to plan Task 9 in §7.4, not a silent rebase; the `llm:` block sits
beside the plan's `scope:` block under `ask_your_docs:`. Whichever lands
second rebases onto the other; the shared lines are `AskYourDocsConfig`'s
field list and the YAML block (both additive) and the Task 9 signature.

### 4.2 The value object and its vocabularies

```python
class AuthMode(StrEnum):
    NONE = "none"                    # no Authorization header at all
    ENV_KEY = "env_key"              # bearer = os.environ[api_key_env]
    TOKEN_SERVICE = "token_service"  # bearer fetched from token_url, renewable


class VisionRule(StrEnum):
    DETECT = "detect"            # vision: null  -> run the ladder as today
    MULTIMODAL = "multimodal"    # vision: true  -> main model sees, no probe
    TEXT_ONLY = "text_only"      # vision: false -> main model never sees
    SEPARATE_MODEL = "separate_model"  # vision: {model: ...}


@dataclass(frozen=True, slots=True)
class ConnectionOverride:        # one shape for the CLI tier and the dialog tier
    base_url: str | None = None  # None = tier unset
    model: str | None = None


@dataclass(frozen=True, slots=True)
class LlmConnection:
    base_url: str | None          # None = the SDK's vendor default, as today
    model: str | None             # None = not chosen yet (block present, no tier set it)
    auth_mode: AuthMode           # as configured
    token_url: str | None         # TOKEN_SERVICE only
    api_key_env: str | None       # ENV_KEY only
    token_field: str | None       # None = whole body, stripped, is the token
    renew_on_status: tuple[int, ...]
    vision_rule: VisionRule
    vision_model: str | None      # SEPARATE_MODEL only
    config_path: str | None       # the pydocs YAML the block came from
    block_present: bool           # False = no ask_your_docs.llm block
    configured_base_url: str | None   # the YAML base_url, kept for the origin-change check (H1); None = no block or null

    @property
    def origin_changed(self) -> bool:   # H1: configured_base_url set and its (scheme, hostname, port) differs from base_url's
        ...

    @property
    def cleartext_bearer(self) -> bool:   # H2: block_present, auth_mode is not NONE and base_url is http:// on a non-loopback host
        ...
```

`block_present` is what the factory keys byte identity on (§4.5): it is the
only field that distinguishes "no block, `OPENAI_API_KEY` as today" from an
explicit `auth.api_key_env: OPENAI_API_KEY`. It is also the third element of
`connection_identity` (§1.4, §4.9), so the `get_agent` /
`get_capabilities` caches, `page_bearer` and the status line's lenient no-block rendering
(§4.9) tell the two apart as well; besides those readers, only the factory,
`bearer_for_connection` and the `cleartext_bearer` property look at it.

Origins are compared as `urlsplit(...).scheme / .hostname / .port`, never
`netloc` (which carries userinfo). `origin_changed` and `cleartext_bearer`
are pure properties the status line reads, and `resolve_llm_connection`
logs each once (§4.3 rules 6–7). Both flags are scoped to the bearers this
design introduces: for the no-block path `configured_base_url` is `None`
and `block_present` is `False`, so neither flag is ever raised and
`OPENAI_API_KEY` follows `OPENAI_BASE_URL` exactly as the SDK does today
(R2 byte identity, §7.2).

`_DEFAULT_MODEL = "gpt-4o-mini"` replaces the two literals at `app.py:103`
and `cli.py:52` and applies **only when `block_present=False`** (the
byte-identity path); with a block present and no tier naming a model, the
connection resolves `model=None` (§4.3 rule 1). Together with
`_DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"` and `_DEFAULT_RENEW_ON_STATUS =
(401,)` it lives in `ask_your_docs_models.py` (the light config module,
which the pydantic `Field` defaults of §5 read) and `llm_connection.py`
imports the three from there — one source each. Provenance (which tier won
each field) is one `connection_resolved` JSON log line at resolution time
carrying the tier names, not a field on the value object.

### 4.3 Precedence resolution

One pure function, no I/O, no Streamlit:

```python
def resolve_llm_connection(
    yaml_block: LlmConnectionConfig | None,   # AppConfig.load(...).ask_your_docs.llm
    environment: Mapping[str, str],           # os.environ or {} (binding)
    launch: ConnectionOverride,               # CLI flags: base_url, model (None = unset)
    dialog: ConnectionOverride,               # session choices: base_url, model (None = unset)
    *,
    config_path: str | None,
) -> LlmConnection
```

Rules:

1. `base_url` and `model` are resolved field by field as a fold over
   `(yaml, environment, launch, dialog)`, lowest tier first: YAML →
   `OPENAI_BASE_URL` / `LLM_MODEL` → `--base-url` / `--model` → dialog. An
   empty string at any tier means "unset" (today's `base_url or None` at
   `app.py:86` and the `if value` guard at `cli.py:70-73`), so the tier is
   skipped, not applied. `base_url` unset everywhere ⇒ `None` (vendor
   default). `model` unset everywhere ⇒ `_DEFAULT_MODEL` when
   `block_present=False` (today's page) and **`None` when the block is
   present** (D2: `model: null = pick in the dialog`): the status line reads
   `model: not chosen`, no agent is built, and the dialog's selectbox opens
   on the first listed id — Apply is what first sets it.
2. `auth`, `token_field`, `renew_on_status` and `vision` are read from the
   `AppConfig` layer only (R3). The `PYDOCS_ASK_YOUR_DOCS__LLM__…` env
   overlay (`retrieval/config/app_config.py:210`, `env_prefix="PYDOCS_"`)
   is part of that layer — it is not a new
   knob and it is not the `OPENAI_BASE_URL` / `LLM_MODEL` tier; the dialog
   and CLI never touch these four.
3. No block ⇒ `block_present=False`, `auth_mode=ENV_KEY`,
   `api_key_env="OPENAI_API_KEY"`, `configured_base_url=None`,
   `vision_rule=DETECT`, `renew_on_status=(401,)`. The bearer built for this
   connection is the **lenient** `EnvironmentKeyBearer` (§4.4): an unset
   variable yields an empty bearer and no header, never an error — the same
   rule the SDK's own `auth_headers` applies, rendering the header only for
   a non-empty key (`openai/_client.py:315-320`).
4. Block present, `auth` absent ⇒ `AuthMode.NONE`. Block present, `auth`
   present ⇒ the configured mode; `configured_base_url` is the YAML
   `base_url` in both cases (config load already rejected `token_url`
   without `base_url`, E14).
5. `vision.model == model` (after step 1) ⇒ `vision_rule=MULTIMODAL`,
   `vision_model=None`, and one `vision_model_equals_main` warning log (R6).
6. Origin change (H1): when `configured_base_url` is set and the resolved
   `base_url`'s `(scheme, hostname, port)` differs, one
   `bearer_origin_changed` JSON warning is logged with both origins
   (`display_url` form); the bearer is still sent to the resolved endpoint
   (D3), and `origin_changed` is what the status line and the dialog read.
7. Cleartext (H2): when a bearer this design introduces will be attached
   (`block_present` and `auth_mode` is not `NONE`), the scheme is `http`
   and the host is not loopback ⇒ one `bearer_over_cleartext` warning is
   logged and `cleartext_bearer` is true; no error, no config key. The
   no-block path is never flagged (§4.2).

Examples (`Y` = YAML block, `E` = environment, `C` = CLI, `D` = dialog; the
"winning tier" column is descriptive — it is what the `connection_resolved`
log line names, not a field):

| Y `base_url` | E `OPENAI_BASE_URL` | C `--base-url` | D | resolved `base_url` | winning tier |
|---|---|---|---|---|---|
| — | — | — | — | `None` (vendor default) | none |
| `https://llm.internal/v1` | — | — | — | `https://llm.internal/v1` | YAML |
| `https://llm.internal/v1` | `http://localhost:8000/v1` | — | — | `http://localhost:8000/v1` (origin differs: warning, bearer still sent) | environment |
| `https://llm.internal/v1` | `http://localhost:8000/v1` | `http://gpu-box:8000/v1` | — | `http://gpu-box:8000/v1` (origin differs: warning, bearer still sent) | CLI |
| any | any | any | `http://other/v1` | `http://other/v1` (warning unless the origin equals Y's) | dialog |
| `https://llm.internal/v1` | `""` | — | — | `https://llm.internal/v1` | YAML (empty skipped) |
| — (`auth.api_key_env` set) | — | — | — | `None` (vendor default; the key follows the SDK's default endpoint) | none |
| — (`auth.api_key_env` set) | `http://gpu-box:8000/v1` | — | — | `http://gpu-box:8000/v1` (no YAML `base_url` to compare: no origin change flagged; the H2 cleartext warning still applies) | environment |

`model` follows the same table with `LLM_MODEL` / `--model`; the bottom of
the fold is `_DEFAULT_MODEL` without a block and `None` with one. The
dialog's Model selectbox writes `ConnectionOverride.model`; the YAML `model`
key is the preselection the selectbox opens on when no higher tier set one.

Note that the CLI already copies its flags into `LLM_MODEL` /
`OPENAI_BASE_URL` before re-executing Streamlit (`cli.py:70-73`), so inside
the app the CLI tier is observed through the environment; the function still
takes both so the binding (which has flags but no environment tier) and unit
tests can express every row of the table.

### 4.4 Bearer sources and the token lifecycle

```python
@runtime_checkable
class BearerSource(Protocol):
    def current(self) -> str: ...                            # cached value; fetches lazily on first call
    def peek(self) -> str: ...                               # cached value WITHOUT fetching (redaction, the UI)
    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str: ...
                                                             # compare-and-swap: fetch only if the cache still equals `rejected`
    def describe(self) -> BearerStatus: ...                  # for the UI: mode, renewed_at, last_four


@dataclass(frozen=True, slots=True)
class BearerStatus:
    auth_mode: AuthMode
    renewed_at: datetime | None
    last_four: str        # "" only when there is no bearer
```

- `NoBearer` (Null Object): `current()` returns `""`, `renew()` returns `""`,
  `describe()` reports `AuthMode.NONE`. It is the bearer for `AuthMode.NONE`
  only. The factory does **not** rely on an empty `api_key` to suppress the
  header — an empty `api_key` is SDK-version-fragile (a newer SDK release
  rejects an empty key at construction), so the factory passes the static,
  non-secret placeholder `_NO_AUTH_PLACEHOLDER = "no-auth"` as `api_key`
  and installs `StripAuthorizationAuth` on both httpx clients (§4.5 rule
  3); AC-3 asserts through `RecordingTransport` that no `Authorization`
  header leaves.
- `EnvironmentKeyBearer(var_name, *, required: bool)`: `current()` reads
  `os.environ.get(var_name, "")` every call (so a rotated variable is picked
  up by the next request without a restart). `required=False` is the
  **lenient** form built for the no-block path: an unset variable yields
  `""`, a header is sent only when the value is non-empty, and nothing ever
  raises — the rule the SDK's own `auth_headers` applies to a non-empty key
  (`openai/_client.py:315-320`); construction with an empty key is what the
  §4.5 rule-1 carve-out avoids by routing it to rule 3. `required=True` is
  the strict form for an
  explicit `auth.api_key_env`: an unset variable ⇒ `BearerUnavailableError`
  naming the variable (E5). `renew()` is `current()` in both forms.
- `TokenServiceBearer(token_url, token_field, timeout_seconds, attempts,
  backoff_seconds)`: `current()` returns the cached token or fetches it,
  double-checking the cache **inside** the lock so two concurrent first
  requests cost one fetch. `renew(rejected)` is compare-and-swap under the
  same `threading.Lock`: if the cache already differs from `rejected` (another
  flow renewed first) it returns the cache without fetching; if the last
  successful renew is younger than `_MIN_RENEW_INTERVAL_SECONDS = 5.0` it
  returns the cache (a persistently rejecting endpoint cannot turn every SDK
  attempt into a token fetch); otherwise it fetches and replaces. The fetch
  is a synchronous `httpx.Client.get` bounded by
  `_TOKEN_FETCH_TIMEOUT_SECONDS = 5.0`, `_TOKEN_FETCH_ATTEMPTS = 3` and
  `_TOKEN_FETCH_BACKOFF_SECONDS = (2.0, 4.0)` — defined once in
  `bearer_tokens.py` with a comment citing `multimodal.py:99-101` as the
  precedent for the values; they are **not** aliases of the probe constants,
  so a later probe tuning cannot silently change token fetching. Body
  handling per R2: `token_field is None` ⇒ `response.text.strip()` is the
  token (the owner's `requests.get(url).text` snippet); a string ⇒
  `response.json()[token_field]`. A missing field or non-JSON body ⇒
  `TokenServiceError` naming the URL (`display_url` form), the field and
  the body's *shape* — content type, byte length and, for a JSON object,
  its top-level key names — never body bytes (E2; in exactly this failure
  the body usually IS the token under another key). Exhausted attempts ⇒
  `TokenServiceError` naming the URL and the attempt count (R9, E1).
- `RenewOnStatusAuth(httpx.Auth)`: `requires_response_body = False`; the flow
  is written once as a generator over a tiny `renew` callback and installed
  through both `sync_auth_flow` (calls `bearer.renew(sent)` inline) and
  `async_auth_flow` (calls `await asyncio.to_thread(bearer.renew, sent)` so
  the bounded fetch never blocks the event loop — CLAUDE.md §Async
  patterns). `sent` is the token parsed from the request's own
  `Authorization` header (`request.headers["Authorization"].removeprefix("Bearer ")`),
  **not** `bearer.current()`, so the compare-and-swap sees the token the
  endpoint actually rejected.
- `StripAuthorizationAuth(httpx.Auth)`: `auth_flow` deletes `Authorization`
  from the request and yields it once; installed for `AuthMode.NONE`
  (§4.5 rule 3).
- `redact_bearer(text: str, bearer: BearerSource) -> str` replaces the
  bearer's current cached value and any `Bearer <token>` pattern in `text`
  with `…{last_four}` (or `…` when `last_four` is empty). `display_url(url)
  -> str` returns `scheme://host[:port]/path` with userinfo and query
  stripped. `translate_auth_errors(bearer)` is a context manager that
  converts `openai.AuthenticationError` / `openai.PermissionDeniedError`
  into `BearerRejectedError(status, host, last_four, detail)` built **without**
  the response body and **without** the SDK cause (`raise … from None`);
  `detail` is the bearer's `last_error` — a renewal that fails inside the
  Auth flow (token service down) does not raise out of `send`, where the
  SDK would retry the whole request and multiply the token fetches: the
  flow returns the rejected response and its cause rides here as `; renew
  failed: …`. The renew rate limit counts attempts, not successes, so that
  case is bounded to one fetch burst per interval; it wraps every `build_chat_model` consumer for **all**
  auth modes (`ENV_KEY` and `NONE` have no renewing Auth, so a 401 there is
  a raw SDK error that would otherwise carry the body).
- `bearer_for_connection(connection)` (in `llm_connection.py` beside
  `connection_identity`, so the import direction stays one-way) is a
  module-level registry keyed on `connection_identity(connection)` (the same
  key the page's `page_bearer` reads) with a `clear_bearer_registry()` test seam, so one process shares one
  bearer per identity regardless of entry point — the eval binding's
  `build_agent`-per-sample loop issues one token per campaign, not one per
  record. It returns `NoBearer()` when `auth_mode is NONE`; the key is
  `connection_identity` (now including `block_present`, §1.4).
- `last_four = token[-4:]` (`""` for an empty bearer); it appears in
  `describe()` for the UI and in `BearerRejectedError` only, never in a log
  record.

The sequence, per request:

```
first request      SDK _prepare_options -> api_key callable -> bearer.current()
                   └─ TokenServiceBearer: lock; cache empty -> fetch (≤3 attempts) -> cache
                      (a second concurrent first request waits on the lock, then reads the cache)
                   SDK builds "Authorization: Bearer <cached>"; httpx Auth passes it through
cached             every later attempt re-reads the cache (no network)
401 on attempt N   Auth flow: status ∈ renew_on_status and not yet renewed in THIS flow
                   -> bearer.renew(rejected=<token from the request header>)
                      cache == rejected and last renew older than the interval -> fetch, replace
                      cache != rejected (another flow renewed first)        -> return cache
                   -> request.headers["Authorization"] = "Bearer <cache>"
                   -> yield the SAME request once more
second 401         Auth flow returns the response; the SDK raises AuthenticationError
                   -> translate_auth_errors -> BearerRejectedError(status, host, last_four) (R9)
SDK retry (5xx…)   SDK loop re-enters _prepare_options -> bearer.current() (the renewed
                   value) -> a fresh Auth flow (its once-per-flow budget resets)
manual Renew       dialog button -> bearer.renew(rejected=bearer.current()); the cached agent
                   keeps the SAME bearer object, so nothing is rebuilt (R7)
service down       bearer.current() raises TokenServiceError(url, attempts) before any
                   LLM request leaves the process — on the build path too (§4.7, H3);
                   the UI shows it in the status row
```

Why both mechanisms and how they stay consistent (closes the research's
open question): the callable `api_key` is the *source* of the header on
every attempt — the SDK re-evaluates it at the top of each retry iteration
(`openai/_base_client.py:963-965`, async `:1510-1512`;
`langchain_openai/chat_models/_client_utils.py:115-148` wraps a sync
callable for the async client). The `httpx.Auth` wrapper *never sets the
header on the first pass*; it only rewrites it after it has renewed inside a
flow. Both read the same `TokenServiceBearer` cache, so the two mechanisms
can never disagree on which token is sent. The flow renews at most once per
`client.send()` (a `renewed_once` local), so a persistently rejecting
endpoint costs exactly two HTTP round-trips per SDK attempt and the SDK
performs no attempts of its own on `401` (`openai/_base_client.py:750-786`).
Because `renew_on_status` is confined to `{401, 403, 407}` (§5.1), the flow
never re-sends a successful non-idempotent completion and never composes
with a status the SDK itself retries; the rate limit above bounds the
remaining case (SDK attempt × Auth renew) to one token fetch per interval.
`x-stainless-retry-count` is unchanged by the inner retry (probe-verified),
which is fine: the SDK's idempotency key is reused across its own retries
(`_base_client.py:955-957`) and the inner retry re-sends the same
already-read `ByteStream` body (`httpx/_models.py:405-422`).

Logging: `bearer_fetched` and `bearer_renewed` JSON records carry
`{"event", "auth_mode", "token_url_host", "attempts", "renewed_at",
"reason": "first_request" | "rejected_status" | "manual" | "cache_hit"}` — never
the token, never `last_four`, never the full URL (`token_url_host` is
`urlsplit(token_url).hostname`). `bearer_origin_changed` and
`bearer_over_cleartext` records carry the two origins / the endpoint in
`display_url` form. AC-9 greps every log record, every exception text, the
reinspect tool-result string and the `openai` / `httpx` loggers at `DEBUG`
for the token value.

### 4.5 The client factory

```python
def build_chat_model(
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    model: str | None = None,       # override for the vision model / probes
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    tolerate_missing_key: bool = False,   # the rule-1 carve-out (the listing, rung 3)
    transport: "httpx.BaseTransport | None" = None,  # test seam: both httpx clients get it
) -> "ChatOpenAI":
```

Construction rules, in order:

1. `not connection.block_present` ⇒ **exactly today's call**:
   `ChatOpenAI(model=model or connection.model, base_url=connection.base_url)`
   plus `timeout=` / `max_retries=` only when the caller passed them (the
   image probe passes both, as today at `multimodal.py:187`). No callable, no
   custom clients — the SDK reads `OPENAI_API_KEY` itself (and, as today,
   raises at construction when it is unset: that is why
   `test_prompt_seam.py:86-87` sets it). This is the byte identity of R2 and
   it is pinned by a kwargs spy (AC-19). **One carve-out:** the rung-3 /
   listing client built by `fetch_models_payload` (§4.6) must keep working
   with the variable unset, as today's bare GET does (R2), so on the
   no-block path it takes rule 2 when the lenient bearer's `current()` is
   non-empty and rule 3 when it is empty (`tolerate_missing_key=True`) — the
   agent's own client and the rung-4 probe stay on rule 1. The decision
   itself is one function, `connection_auth_kwargs(connection, bearer, *,
   tolerate_missing_key) -> (api_key, httpx auth)` (`api_key` `None` = rule
   1, a sync callable = rules 2 and 4, the placeholder = rule 3), which the
   listing's `openai.AsyncOpenAI` (§4.6) reads too. With `transport=`
   (tests only) both httpx clients are always built, carrying that transport.
2. `auth_mode is ENV_KEY` ⇒
   `ChatOpenAI(model=..., base_url=..., api_key=bearer.current)` (a sync
   callable; `langchain-openai` 1.1.9 wraps it for the async client). No
   custom httpx clients: nothing renews, so no `Auth` wrapper is needed and
   the SDK's default client pooling stays. (For the strict form an unset
   variable raises at the first request, E5; the lenient no-block form
   reaches here only through the rule-1 carve-out, with a non-empty value.)
3. `auth_mode is NONE` (no `auth` block) ⇒
   `ChatOpenAI(model=..., base_url=..., api_key=_NO_AUTH_PLACEHOLDER,
   http_client=DefaultHttpxClient(auth=StripAuthorizationAuth()),
   http_async_client=DefaultAsyncHttpxClient(auth=StripAuthorizationAuth()))`.
   The placeholder satisfies the SDK's constructor check; the `Auth`
   deletes the header on the wire (AC-3 asserts the wire on the locked SDK).
4. `auth_mode is TOKEN_SERVICE` ⇒
   `ChatOpenAI(model=..., base_url=..., api_key=bearer.current,
   http_client=DefaultHttpxClient(auth=RenewOnStatusAuth(bearer, statuses)),
   http_async_client=DefaultAsyncHttpxClient(auth=RenewOnStatusAuth(bearer,
   statuses)))`. Both clients are required: `ainvoke` goes through
   `root_async_client` / `async_client`
   (`langchain_openai/chat_models/base.py:1625-1668`), `invoke` through the
   sync pair, and `langchain-openai` only attaches a custom client where one
   is passed (`base.py:953-1025`; the default clients are `lru_cache`d per
   `(base_url, timeout)` and cannot carry an `Auth`,
   `_client_utils.py:22-112`). `openai.DefaultHttpxClient` /
   `DefaultAsyncHttpxClient` keep the SDK's own timeout and connection limits
   (`openai/_base_client.py:789-808`, sync; the async alias follows it).
5. The SDK's bounds stay the bounds: `DEFAULT_TIMEOUT` 600 s / connect 5 s
   and `DEFAULT_MAX_RETRIES` 2 (`openai/_constants.py:9-14`) — every LLM call
   is therefore already timed out and retry-bounded; exposing them as YAML
   keys is O1 (§10).
6. `stream_usage`: no change for the `ainvoke` path the agent uses. Its
   auto-enable is suppressed whenever custom clients are passed
   (`base.py:921-940` checks `http_client` / `http_async_client`), the same
   as when `base_url` is set — so a streaming caller on the vendor default
   endpoint with a token service would lose usage metadata; nothing in the
   harness streams.
7. The factory installs no error boundary itself; every *consumer* wraps its
   call (`reformulate`, `ask`, the vision node, the reinspect tool, the image
   probe, `run_connection_test`): `with translate_auth_errors(bearer): await
   llm.ainvoke(...)`. The factory guarantees only that the bearer it
   installed is the one the consumer passes to the boundary.

Every consumer takes a built model, never constructs one:

| Consumer | Today | After |
|---|---|---|
| agent + reformulation | `ChatOpenAI(model, base_url)` `agent.py:340` | `build_chat_model(connection, bearer)` |
| vision node, reinspect tool | `ctx.llm` | `ctx.vision_llm` (= `ctx.llm` unless `SEPARATE_MODEL`) |
| image probe (rung 4) | own `ChatOpenAI(..., timeout, max_retries=0)` `multimodal.py:187` | `build_chat_model(connection, bearer, model=model, timeout_seconds=..., max_retries=0)` |
| endpoint probe (rung 3) | bare `httpx.AsyncClient` `multimodal.py:168-179` | `model_listing.fetch_models_payload(connection, bearer)` |
| model listing, Test connection | — | `fetch_model_ids(...)`, `build_chat_model(...).ainvoke("Reply with OK.")` — both on a *candidate* connection resolved from the dialog's current values; its bearer is `bearer_for_connection(candidate)` — the registry's object for the (unchanged) auth identity, so a Base URL typed into the dialog receives the same bearer the page uses (D3; H1 makes the origin change visible) |

`build_agent` gains `connection: LlmConnection | None = None`; when `None`
it resolves `resolve_llm_connection(cfg.llm, {}, ConnectionOverride(base_url,
model), ConnectionOverride(), config_path=pydocs_config)` from its own
positional `model` / `base_url` arguments — no environment read inside
`build_agent`, so the binding stays deterministic (§4.11) and the existing
call sites (`binding.py:352`, `test_prompt_seam.py`) need no change. The
bearer is `bearer_for_connection(connection)` (the registry, so the app and
the binding get the same object for the same identity) unless the caller
passes `bearer=` (a test seam). A `connection.model is None` is rejected at
the top of `build_agent` with `AgentArchitectureError("no model chosen; set
ask_your_docs.llm.model, LLM_MODEL, --model or pick one in the Connection
dialog")` — the page never reaches this because it builds no agent on an
unresolved model (§4.9).

### 4.6 Model listing

```python
@dataclass(frozen=True, slots=True)
class ModelListing:
    model_ids: tuple[str, ...]
    error: str | None          # non-fatal: shown in the dialog caption
    fetched_at: float          # time.monotonic()


async def fetch_models_payload(connection, bearer, *, list_models=None, transport=None) -> list[dict]
async def fetch_model_ids(connection, bearer, *, list_models=None, transport=None) -> ModelListing
async def cached_model_listing(connection, bearer, *, now=time.monotonic, list_models=None) -> ModelListing
```

- **One path, the SDK's.** `fetch_models_payload` builds an
  `openai.AsyncOpenAI(base_url=connection.base_url, api_key=…,
  http_client=DefaultAsyncHttpxClient(auth=…), timeout=_LISTING_TIMEOUT_SECONDS,
  max_retries=_LISTING_MAX_RETRIES)` from the factory's one auth decision
  (`connection_auth_kwargs(..., tolerate_missing_key=True)`, §4.5) — not a
  `ChatOpenAI`, whose `model` would have to be a placeholder while no model
  is chosen — and calls `await client.models.list()`. The async SDK client
  **awaits** its `api_key` provider (`openai/_client.py:649-651`), so the
  sync `bearer.current` is wrapped as `async def: return await
  asyncio.to_thread(bearer.current)` — the first lazy token fetch never
  blocks the Streamlit background loop. That client holds the effective
  base URL (the vendor default when `connection.base_url is None`), the
  listing timeout, the callable bearer and — for a token service — the same
  `RenewOnStatusAuth` (renew once, retry once). No
  hand-written `httpx.AsyncClient`, no manual `Authorization` header, no
  second copy of the retry helper: `_with_rung_retry` stays in
  `multimodal.py` and is not duplicated. Each `Model` becomes
  `{"id": m.id, **(m.model_extra or {})}` so `_entry_hints_vision` keeps
  reading the extra fields it reads today.
- `list_models` is the injectable seam (`Callable[[LlmConnection,
  BearerSource], Awaitable[list[dict]]]`); production passes `None`.
- `data[].id` sorted, deduplicated; a page whose items carry no `id` ⇒
  `error="unexpected /models payload: expected {'data': [{'id': ...}]}, got
  keys=[...]"`.
- Bearer failures are not listing failures: `TokenServiceError` and
  `BearerUnavailableError` propagate out of `fetch_models_payload` (H3);
  `fetch_model_ids` catches only SDK / transport errors into
  `ModelListing(error=...)` (E6).
- Cache: one process-level dict keyed on `(base_url, auth identity)`;
  `_MODEL_LISTING_TTL_SECONDS = 60.0` is the single-source TTL; the dialog's
  refresh action and a Renew both evict the entry through
  `clear_model_listing_cache(connection)` (one entry; called with no
  argument it clears the dict — the test seam, mirroring
  `clear_detection_cache()`, `multimodal.py:114-116`). AC-45 pins both
  eviction paths.
- Rung 3 of the capability ladder becomes: `fetch_models_payload` → the
  entry whose `id == model` → `_entry_hints_vision` (`multimodal.py:151-165`
  unchanged). The `FakeModelsEndpoint` fake of
  `tests/harness/ask_your_docs/test_multimodal_detection.py:20-54` keeps its
  `(base_url, model, timeout)` signature by wrapping the new seam.

### 4.7 Capability resolution and routing

`DetectionSource` (`multimodal.py:22`, a `Literal`) becomes:

```python
class CapabilitySource(StrEnum):
    OVERRIDE = "override"
    STATIC = "static"
    ENDPOINT = "endpoint"
    PROBE = "probe"
    DEFAULT = "default"
    CONFIGURED = "configured"   # vision: true | false | {model}
```

The member values are the strings already compared in tests
(`test_multimodal_detection.py:56-152`) and rendered in the badge
(`app.py:110`), so nothing observable changes; `StrEnum` equals its string.
The migration is decided here, not open: the CLAUDE.md `StrEnum` rule
applies to any vocabulary a change touches, `multimodal.py` is edited by
this design anyway, and `CONFIGURED` is a new member — adding it to a
`Literal` would be new code under the old shape. (§10 no longer lists it.)

Resolution lives in **one pure helper**, the single call site for the table
below:

```python
async def resolve_vision_capabilities(
    connection: LlmConnection,
    bearer: BearerSource,
    detection: MultimodalDetectionConfig,           # cfg.multimodal.detection
) -> tuple[ModelCapabilities, ModelCapabilities]:   # (main, vision)
```

in `llm_connection.py`, called by **both** the app's `get_capabilities`
(widened key, §4.9) and `build_agent` when `capabilities is None`. Today the
app calls `detect_capabilities` directly and injects the result as
`build_agent(capabilities=...)` (`app.py:71-77`, `:90`), so a short-circuit
placed only inside `build_agent` would never apply on the Streamlit path —
the ladder would still run and `vision: true` would be ignored by the status
line, `text_only_policy` and the agent alike. With the helper, all three see
the same verdict.

| `vision_rule` | main `capabilities` | `vision_llm` | `vision_capabilities` |
|---|---|---|---|
| `DETECT` | `detect_capabilities(model, base_url, detection, ...)` — today's ladder with authenticated probes | `llm` | = main |
| `MULTIMODAL` | `(True, CONFIGURED)`; no probe runs | `llm` | = main |
| `TEXT_ONLY` | `(False, CONFIGURED)`; no probe runs | `llm` | = main |
| `SEPARATE_MODEL` | `(False, CONFIGURED)`; **no ladder** — the main model is by construction not the image model, and nothing consumes a main verdict under this rule | `build_chat_model(connection, bearer, model=vision_model)` | `(True, CONFIGURED)`; no probe on the vision model |

The `capabilities=` keyword of `build_agent` keeps its meaning (an injected
override of the main verdict, `agent.py:357-361`) and `vision_capabilities=`
is added beside it for tests; when either is passed the helper is not
called. `detect_capabilities`' cache key gains nothing: the rule does not
reach it because every rule but `DETECT` short-circuits before the call.

Bearer failures on the build path (H3): `_with_rung_retry`
(`multimodal.py:133-148`) re-raises `TokenServiceError` /
`BearerUnavailableError` / `BearerRejectedError` immediately (they are already bounded internally —
today it retries any exception, which would cost 3 × 3 = 9 token fetches
and ~24 s of sleeping); `_endpoint_rung` (`:234-245`) and `_image_probe_rung`
(`:248-260`) let those three classes propagate instead of falling through to
`None`; `detect_capabilities` (`:211-231`) does not write `_detection_cache`
when they are raised. Without this a token service that is down when the
agent is first built would land the ladder on `(False, default)`, cache it
in `_detection_cache`, in `st.cache_resource get_capabilities` and in a
`text_react` agent cached by `get_agent`, and — since Renew never rebuilds
the agent (R7) — keep the deployment text-only for the process lifetime with
only a `vision: no (default)` badge as a hint. Instead the E1 error surfaces
at the status line (the app preflights `bearer.current()` before
`get_capabilities`, §4.9), nothing is cached (an exception inside
`st.cache_resource` is not cached), and the next click after the service
recovers builds with the real verdict (AC-34).

`AutoArchitecture.build` (`architectures/auto.py:30-40`) becomes the table:

| `ctx.vision_llm is ctx.llm` | `ctx.capabilities.multimodal` | builds |
|---|---|---|
| yes | no | `text_react` (today's fallback) |
| yes | yes | `cfg.multimodal.preferred_architecture` — shipped default now `inline` |
| no (separate model) | any | `vision_subagent` on `ctx.vision_llm` |

A deployment that wants the describe hop on its multimodal main model sets
`preferred_architecture: vision_subagent` back; a deployment with a separate
model and `preferred_architecture: inline` still gets `vision_subagent`
under `auto`, because inline cannot route image blocks to a second model —
this is logged once as `auto_routing` with the chosen architecture and the
reason (E12). An **explicitly** selected `architecture: inline` under
`SEPARATE_MODEL` is a different case: `inline` routes images to the main
model, whose capabilities are `(False, CONFIGURED)` under that rule, so
`_build_architecture` rejects it with the E13 message pointing at
`ask_your_docs.llm.vision` — the honest outcome, not a silent re-route.

The default flip is a dated change: `MultimodalConfig.preferred_architecture`
default `"vision_subagent"` → `"inline"` (`ask_your_docs_models.py:43`),
`default_config.yaml:349`, the two assertions at
`tests/test_config_ask_your_docs.py:18` and `:45`, the comment at
`ask_your_docs_models.py:41-42`, and the `configs/*.yaml` comment blocks.
`text_only_policy` (`app.py:227`) consults the vision half of
`get_capabilities` (§4.8, §4.9), so `reject` / `describe` fires only when
the image step genuinely cannot see.

### 4.8 The separate vision model

Adopted from the sidecar spec (commit `6d2ee9e`, §3.2–§3.3, §4.1) with the
path drift corrected (`pydocs_mcp/harness/ask_your_docs/`,
`tests/harness/ask_your_docs/`) and its connection surface replaced:

- `AgentBuildContext` (`architectures/base.py:24-32`) gains
  `vision_llm: Any = None`, `vision_capabilities: ModelCapabilities | None
  = None` and `bearer: BearerSource = NoBearer()` with a `__post_init__`
  that sets the first two to `llm` / `capabilities` when `None`
  (`object.__setattr__` on the frozen instance), so every existing
  five-field construction site keeps working (`test_architectures.py:20-35`,
  `test_reinspect_tool.py`). These are not optional service dependencies —
  the identity default and `NoBearer` are the Null Objects; `bearer` exists
  so the reinspect tool can redact (H4).
- `ImageModelRoute(StrEnum)` with `MAIN = "main"`, `VISION = "vision"`
  replaces the sidecar spec's `Literal` ClassVar (the StrEnum rule postdates
  that spec); `AgentArchitecture.image_model_route: ClassVar[ImageModelRoute]
  = MAIN`; `VisionSubagentArchitecture` sets `VISION`.
- `effective_tools(ctx, route)` (`base.py:35-49`) gates on
  `ctx.vision_capabilities` when `route is VISION` and binds
  `build_reinspect_tool(ctx.vision_llm, ...)`; `inline` and `text_react` keep
  `MAIN`.
- `describe_images(image_llm, question, image_blocks, *, render=None) -> str`
  lands in `attachments.py` with function-local `HumanMessage` /
  `render_shared` imports; the vision node
  (`architectures/vision_subagent.py:44-60`) and the reinspect tool
  (`reinspect.py:70-82`) both call it inside `translate_auth_errors(bearer)`.
  The node lets a failure propagate (the person attached the image on
  purpose; the app's send-loop boundary renders it redacted, §4.9); the tool
  returns `f"Image re-inspection failed: {redact_bearer(str(exc),
  ctx.bearer)}"` as a tool result — that string enters the model's context
  and, in the binding, the `messages` that feed `Trajectory` and the traces
  on disk, so it is a redaction boundary (H4).
- `_build_architecture` (`agent.py:152-180`) gains `vision_llm=`,
  `vision_capabilities=` and `bearer=`; for a `requires_multimodal` architecture it gates on
  the capabilities of its `image_model_route`; the vision-route error message
  points at `ask_your_docs.llm.vision`, the main-route message keeps today's
  text (`agent.py:165-171`) and adds "or set `ask_your_docs.llm.vision: true`".
- **Not adopted:** `--vision-model`, `--vision-base-url`, `VISION_MODEL`,
  `VISION_BASE_URL`, `VISION_API_KEY`, the sidebar vision fields, the
  `multimodal.vision_llm.extra_body` key and the 3-tuple return. The vision
  model's identity is `ask_your_docs.llm.vision.model`; it uses the same
  `base_url` and the same bearer object (R6). Whether an `extra_body` knob is
  wanted at all is O2 (§10).
- `BuiltAgent` (from the UI plan's Task 5, or created here if this lands
  first) gains `vision_capabilities: ModelCapabilities`; `build_agent` keeps
  returning `(graph, llm)` (`agent.py:370`, `binding.py:352`,
  `test_binding.py:251`).

### 4.9 The UI

**Status line** (replaces `app.py:102-110`), rendered by
`render_connection_status_line(connection, bearer_status, capabilities)`
through **`st.caption`** (with `help=` carrying only the E1 text when the
token is unavailable) — the existing
`test_app_image_attachment.py:24-25` scans `at.caption` values for
`"vision: yes (static)"`, and that assertion stays on `at.caption`:

```
llm.internal · model-a · token …abcd 12:03 · vision: yes (configured)   [Connection]
api.example.test · gpt-4o-mini · $OPENAI_API_KEY set · vision: yes (static)
localhost:8000 · model-b · no auth · vision: no (default)
llm.internal · model-a · token unavailable ⚠ · vision: yes (configured)
llm.internal · model: not chosen · token …abcd 12:03 · vision: yes (configured)
gpu-box:8000 · model-a · token …abcd 12:03 ⚠ endpoint differs from ask_your_docs.llm.base_url · vision: yes (configured)
llm.internal · model-a · token …abcd 12:03 ⚠ http · vision: yes (configured)
vendor default · model-a · $LLM_KEY missing · vision: no (default)
```

(`model-a` / `model-b` are placeholder ids; `gpt-4o-mini` is the literal
`_DEFAULT_MODEL` of the no-block path, §4.2.)

Host is `display_url(base_url)`'s `hostname[:port]` (never `netloc`, which
carries userinfo) or `vendor default`; the model cell is the id or `model:
not chosen` (§4.3 rule 1); the auth cell is one of `token …abcd 12:03`
(last four inline, then the last renewal in local `HH:MM`; D4), `$VAR set`
/ `$VAR missing` (strict form only; the lenient no-block form shows
`$OPENAI_API_KEY set` or `no auth`), `no auth`, `token unavailable ⚠` (the
`TokenServiceError` text, already in `display_url` form, goes in `help=`),
with a trailing `⚠ http` when `cleartext_bearer` (H2) and, always last,
`⚠ endpoint differs from ask_your_docs.llm.base_url` when `origin_changed`
(H1) — a cell that carries both ends with the origin note; the vision cell is today's
badge text (`app.py:110`) with its source, so the existing
`"vision: yes (static)"` assertion
(`tests/harness/ask_your_docs/test_app_image_attachment.py:9-29`) keeps
passing on the merged line. Workspace and pydocs-config inputs stay where
they are (`app.py:102`, `:105`): they are not part of the connection.

**Dialog** — `open_connection_dialog(connection, bearer_status, actions, *, vision_text, bearer_error)` (`actions: ConnectionActions` carries the page-injected `resolve` / `list_models` / `refresh_models` / `test` / `renew` callbacks, so the module never touches the event loop; the listing is fetched inside the dialog for the current Base URL)
decorated `@st.dialog("Connection", width="small")`, containing in order:

1. `st.text_input("Base URL", key="connection_dialog_base_url")` prefilled
   from `connection.base_url or ""` with `help="session only; the bearer
   is sent to whatever endpoint you enter"`.
2. Auth status row: the status line's auth cell **expanded inline** — for
   a token service `token …abcd · renewed 12:03` (D4: renewal time and last
   four both visible), for an environment key `$VAR set` / `$VAR
   missing`, else `no auth` — plus
   `st.button("Renew", key="connection_renew")` **only** when
   `auth_mode is TOKEN_SERVICE` (D7/R7 fix Renew to a configured
   token service; there is no "Re-read $VAR" button for `ENV_KEY`, whose
   bearer already re-reads the variable on every request, §4.4). Renew calls
   `bearer.renew(rejected=bearer.current())`, evicts the listing entry
   (`clear_model_listing_cache(connection)`), and re-renders the row
   (fragment rerun).
3. `st.selectbox("Model", options=listing.model_ids,
   key="connection_dialog_model")` when the listing succeeded — preselected
   on the resolved model when it is set and listed, else the **first id**
   (this is the branch a fresh D10 deployment lands on: block present, no
   `model`, so `connection.model is None`) — with
   `st.button("↻", key="connection_refresh_models")`; on a failed listing,
   `st.text_input("Model", key="connection_dialog_model_text")` prefilled
   with the resolved model or empty.
4. `st.caption` status: `"{n} models listed"` or `"listing failed: {error}"`
   (the error already in `display_url` form), plus the vision verdict.
5. `st.button("Test connection", key="connection_test")`: resolves a
   *candidate* connection from the dialog's current values (the same fold
   with the dialog tier replaced), obtains its bearer through
   `bearer_for_connection(candidate)` — the same registry object as the
   page's, since the auth identity is unchanged — and
   awaits one `ainvoke("Reply with the single word OK.")` inside
   `translate_auth_errors` (`max_retries=0`,
   `_TEST_CONNECTION_TIMEOUT_SECONDS`); the caption reads `test passed:
   {reply text, stripped, first 40 characters}` on success and `test
   failed: {exc.__class__.__name__}: {redacted message}` on failure (E11),
   and appends `⚠ endpoint differs from ask_your_docs.llm.base_url` when
   the candidate's `origin_changed` is true (H1);
   the caption text is stored under `st.session_state["connection_test_result"]`
   so AppTest can read it after the run (AC-43). Refresh (item 3) uses the
   same candidate and evicts that candidate's listing entry before
   re-fetching (AC-45).
6. `st.button("Apply", key="connection_apply")`: writes
   `st.session_state["connection_override"] = ConnectionOverride(base_url=...,
   model=...)`, sets `st.session_state["connection_dialog_open"] = False`
   and calls `st.rerun()` so the main page re-resolves.

**Session-state keys:** `connection_override: ConnectionOverride`,
`connection_dialog_open: bool`, plus the widget keys above. Nothing is
written to disk; a browser reload starts from precedence again.

**Opener** (state-driven, AppTest-compatible — `streamlit/testing/v1/app_test.py:368-380`
always runs the full script, so a transient `if st.button(...)` opener would
never re-enter the dialog on the next run):

```python
if st.button("Connection", key="connection_open"):
    st.session_state["connection_dialog_open"] = True
if st.session_state.get("connection_dialog_open"):
    open_connection_dialog(
        connection, bearer.describe(), actions, vision_text=vision_cell(vision_caps), bearer_error=bearer_error
    )
```

**Caches** (`app.py:55-91`):

```python
def page_bearer(connection) -> BearerSource               # the registry's object for the identity (or the
                                                         # connection_bearer test seam); no st cache — the
                                                         # registry IS the per-process cache

@st.cache_resource
def get_capabilities(model, base_url, config, identity, _connection, _bearer)  # -> (main, vision)

@st.cache_resource
def get_agent(workspace, model, base_url, config, identity, _connection, _bearer) -> BuiltAgent
```

`get_agent` keeps the UI plan's `(workspace, model, base_url, config)`
order and appends `identity` plus the two underscore-prefixed objects
Streamlit does not hash (`_connection`, `_bearer`); `identity = connection_identity(connection) =
(auth_mode, token_url or api_key_env or "", block_present)` — never a token,
never a key value, and without `config_path` (the key already carries
`config`). `get_capabilities` returns the `(main, vision)` pair of
`resolve_vision_capabilities` (§4.7) so the status line, `text_only_policy`
and the agent read one verdict; `get_agent` passes both halves as
`capabilities=` / `vision_capabilities=`. Before calling
`get_capabilities`, the page preflights `bearer.current()` on
`page_bearer(connection)` at render time (token-service connections only) — a token service that is down raises
`TokenServiceError` there, nothing is cached (an exception inside
`st.cache_resource` is not cached), and the status line shows `token
unavailable ⚠` at once (H3). Because `get_agent` receives the
`page_bearer(connection)` object, Renew mutates that object's cache and the
agent is untouched (R7); changing Base URL or Model in the dialog changes
the key and builds a new agent, exactly as editing the old text inputs did.
With `connection.model is None` no agent is built:
`page_scope_capabilities` returns `NO_SCOPE_CAPABILITIES`, exactly as the
UI plan has it do today for an empty `model`.

**Send-loop boundary** (`app.py:218-276`): `reformulate` and `ask` run inside
one explicit `try/except (PydocsMCPError, openai.APIError) as exc:` that
renders `st.error(redact_bearer(str(exc), bearer))` and
`st.info(f"Your question (not sent): {question}")` — the same shape as the
reject-verdict path at `app.py:231-233`, which is a policy check and not an
exception boundary. Without it `BearerRejectedError` /
`AuthenticationError` / `TokenServiceError` propagate to Streamlit's
uncaught-exception renderer, which prints the full message and traceback
in the browser (H4).

**AppTest strategy** (`streamlit` 1.59.1 verified):

- No page test sends a question, so no agent is built (the UI plan's seeded
  `scope_capabilities` keeps that true once it lands). Three session-state
  seams keep the network out: `connection_bearer` (a `BearerSource` the page
  uses instead of the registry), `connection_list_models` (the listing seam
  `cached_model_listing` receives) and `connection_transport` (the `httpx`
  transport handed to `run_connection_test`).
- The dialog lands under the event container (`at._tree[2]`,
  `element_tree.py:2532-2544`); its widgets are globally queryable
  (`at.text_input(key="connection_dialog_base_url")`,
  `at.button(key="connection_apply")`, `element_tree.py:2020-2027`).
- A test that clicks Apply asserts `at.session_state["connection_override"]`
  right after that `.run()` and does **not** chain another `.run()` on the
  same `AppTest` (the `st.rerun()` inside a dialog leaves stale dialog widget
  state that breaks the next run under `LocalScriptRunner`,
  `forward_msg_queue.py:67-108`); it builds a fresh `AppTest` for follow-up
  assertions. The plan carries this as a comment on the test.
- `at.session_state` has no `.get()` (`safe_session_state.py:101-125`);
  tests use `key in at.session_state`.

### 4.10 CLI and environment

`cli.py` keeps its five flags and the `_ENV` map (`cli.py:18-23`); the only
edits are the `--model` help text ("OpenAI-format model id; overrides
`ask_your_docs.llm.model` and `LLM_MODEL`") and `--base-url` help ("OpenAI-format
base URL; overrides `ask_your_docs.llm.base_url` and `OPENAI_BASE_URL`"), so
`tests/harness/ask_your_docs/test_cli_parser.py:10-34` and
`tests/test_doc_conformance.py:206-262` stay green. No `--api-key` (R2;
AC-24 asserts the parser rejects it). `_EXTRA_MODULES` (`cli.py:28`) is
unchanged — `httpx` arrives with `openai`.

Environment variables read: `OPENAI_BASE_URL`, `LLM_MODEL` (precedence tier),
the variable named by `auth.api_key_env` (bearer), `OPENAI_API_KEY` (no
block only: through the SDK for the agent and the rung-4 probe, through the
lenient bearer for rung 3 and the listing), `PYDOCS_ASK_YOUR_DOCS__LLM__*`
(the `AppConfig` env layer for every YAML key, e.g.
`PYDOCS_ASK_YOUR_DOCS__LLM__VISION=true`).

### 4.11 The eval binding

`AskYourDocsRunnerSettings` (`binding.py:150-163`) is unchanged: `model` and
`base_url` stay, nothing about auth is added (R8, `extra="forbid"` keeps
rejecting `mystery_knob`, `test_binding.py:88-100`). The block does **not**
travel an existing path: `settings.harness` is populated only from the arm's
`settings` mapping (§1.1), and the binding never loads the file named by
`pydocs_config`. So `_build_and_execute` (`binding.py:345-369`) resolves the
block itself:

```python
def connection_block_for_binding(settings: AskYourDocsRunnerSettings) -> LlmConnectionConfig | None:
    if settings.harness.llm is not None:      # an arm may carry the block under harness: {llm: ...}
        return settings.harness.llm
    if settings.pydocs_config is None:
        return None
    return AppConfig.load(explicit_path=Path(settings.pydocs_config)).ask_your_docs.llm

connection = resolve_llm_connection(
    connection_block_for_binding(settings), {},
    ConnectionOverride(settings.base_url, settings.model), ConnectionOverride(),
    config_path=settings.pydocs_config,
)
```

and passes `connection=connection` to `build_agent`. The arm's `harness.llm`
wins over the file so an experiment can pin a block explicitly; the file is
the D8 default. `AppConfig.load` is the same loader the serve subprocess
already runs on that file, so nothing new is read; the `{}` environment
tier keeps `OPENAI_BASE_URL` / `LLM_MODEL` out of the binding as before.
With no block in either place the connection has `block_present=False` and
the factory takes rule 1 of §4.5 — the same `ChatOpenAI(model, base_url)`
call as today, so the control arm's requests, prompt and
`delivery_map_digest()` (`binding.py:75-120`, pinned literal at
`test_binding.py:187-201`) are unchanged (AC-27). The bearer comes from
`bearer_for_connection`'s registry (§4.4), so a 1300-record campaign shares
one token per identity instead of fetching one per sample. The trace
contract (`binding.py:249-306`) is untouched; the reinspect tool's redacted
result string (§4.8) is the only place an auth failure can enter a trace.

---

## 5. Configuration

### 5.1 Models (`ask_your_docs_models.py`)

```python
class LlmAuthConfig(BaseModel):
    """Exactly one of token_url / api_key_env (R2)."""
    model_config = ConfigDict(extra="forbid")
    token_url: str | None = Field(default=None)     # no userinfo, no query credentials (H4)
    api_key_env: str | None = Field(default=None)

    @model_validator(mode="after")
    def _exactly_one(self) -> "LlmAuthConfig":
        given = [k for k in ("token_url", "api_key_env") if getattr(self, k)]
        if len(given) != 1:
            raise ValueError(
                f"ask_your_docs.llm.auth: got {given or 'neither'}, "
                "expected exactly one of token_url / api_key_env"
            )
        return self


class VisionModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1)


class LlmConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str | None = Field(default=None)
    model: str | None = Field(default=None)
    auth: LlmAuthConfig | None = Field(default=None)        # None = no bearer
    token_field: str | None = Field(default=None)           # None = whole body
    renew_on_status: tuple[int, ...] = Field(default=_DEFAULT_RENEW_ON_STATUS)
    vision: bool | VisionModelConfig | None = Field(default=None)


class AskYourDocsConfig(BaseModel):
    ...
    llm: LlmConnectionConfig | None = Field(default=None)   # None = today
```

`auth: LlmAuthConfig | None` and `llm: LlmConnectionConfig | None` are
configuration values with a documented "absent" meaning, not service
dependencies; the Null Object lives one layer down (`NoBearer`,
`block_present=False`). Validation on the two models (`renew_on_status` and
the `auth`/`base_url` pairing on `LlmConnectionConfig`, `token_url` on
`LlmAuthConfig`):

- `renew_on_status` items must be in `_RENEWABLE_STATUSES = frozenset({401,
  403, 407})` (H3); the message carries the offending value and the allowed
  set. `[200]` would re-send every successful, non-idempotent completion;
  `408/409/429/5xx` are statuses the SDK itself retries, and listing them
  would multiply the two bounds instead of composing them (E17).
- `auth.token_url` with `base_url: null` is rejected (E14): a token
  service authenticates one internal endpoint, so the block must name it.
  `auth.api_key_env` with `base_url: null` is **accepted** (D2: an external
  key on the vendor default endpoint).
- `auth.token_url` with userinfo or a query string is rejected (E16):
  credentials do not belong in the URL, and `display_url` would strip them
  from every message anyway.

The three `_DEFAULT_*` constants and `_RENEWABLE_STATUSES` are defined here
(the module must stay light — its docstring, `ask_your_docs_models.py:1-11`)
and `llm_connection.py` imports them (§4.2).

### 5.2 The shipped block (`default_config.yaml`, appended after `images:`)

```yaml
ask_your_docs:
  architecture: auto
  multimodal:
    preferred_architecture: inline    # what "auto" picks on a vision model
                                      # (2026-09-05: was vision_subagent; set it
                                      # back for a separate describe hop on the
                                      # main model)
    detection:
      override: null
      static_table: true
      endpoint_probe: false
      image_probe: false
    text_only_fallback: reject
  images:
    max_per_turn: 3
    max_bytes: 5000000
    session_retention: 12
    max_reinspect_per_turn: 2
  # The chat model's endpoint. Omit the whole block (or leave it null) for
  # the vendor default endpoint with OPENAI_API_KEY, exactly as before.
  # Precedence: this block < OPENAI_BASE_URL / LLM_MODEL < --base-url /
  # --model < the Connection dialog (session only). Secrets never go here:
  # keys come from the environment variable named by auth.api_key_env,
  # tokens from auth.token_url.
  llm: null
  # llm:
  #   base_url: https://llm.internal/v1  # OpenAI-format endpoint; null = vendor default (required with auth.token_url)
  #   model: null                        # preselected model id; null = pick in the dialog
  #   auth:                              # exactly one of token_url / api_key_env; omit
  #                                      # `auth` for no bearer
  #     token_url: http://localhost:8899/access-token   # no credentials in the URL
  #     api_key_env: null                # e.g. OPENAI_API_KEY
  #   token_field: null                  # null = the whole response body is the token;
  #                                      # a name reads that field of a JSON body
  #   renew_on_status: [401]             # renew the bearer and retry the request once;
  #                                      # only 401 / 403 / 407 are accepted here
  #   vision: null                       # true | false | null (detect) | {model: <id>}
```

`llm: null` keeps `AppConfig.load().ask_your_docs == AskYourDocsConfig()`
(`tests/test_config_ask_your_docs.py:55-60`); the key list test at `:113`
grows by `llm`. The UI plan's `scope:` block is appended after `images:` too;
order between `scope:` and `llm:` is immaterial.

### 5.3 Example configs (R10)

`examples/harness/ask_your_docs_agent/configs/index_gpu.yaml` gains no
`llm` block and is unchanged byte for byte (R10, D10).

`examples/harness/ask_your_docs_agent/configs/serve_cpu_openvino.yaml` —
the header's stale usage comment (`:4-6`, `python agent.py … --pydocs-config`)
is replaced by the real command, and the block is added:

```yaml
# SERVE-time config — CPU inference via OpenVINO. Only the QUERY is embedded
# at serve time (one short text per search), so CPU is plenty.
#
# Usage:
#   harness-ask-your-docs --workspace ~/pydocs-index --config configs/serve_cpu_openvino.yaml
#
# DO NOT INDEX with this file: `backend` folds into the chunk cache identity,
# so re-indexing under it would re-embed everything on CPU. Index with
# index_gpu.yaml; serve with this one.
embedding:
  provider: sentence_transformers
  model_name: Qwen/Qwen3-Embedding-4B   # must match index_gpu.yaml
  dim: 2560                             # must match index_gpu.yaml
  max_seq_length: 2048                  # must match index_gpu.yaml
  backend: openvino          # sentence-transformers auto-exports on first load
  query_prompt_name: query   # Qwen3 embeddings are asymmetric — use its query prompt

# The chat model: one OpenAI-format endpoint behind an internal token service.
# The token is fetched on the first question, renewed on a 401, and never
# written to disk or shown in full. Override the endpoint or model per launch
# with --base-url / --model, or per session in the Connection dialog; the
# status line says when the endpoint differs from this file.
ask_your_docs:
  llm:
    base_url: http://llm.internal/v1
    auth:
      token_url: http://localhost:8899/access-token
    vision: true                        # the served model is multimodal; no probe
  # architecture: auto                  # auto | text_react | inline | vision_subagent
  # multimodal:
  #   preferred_architecture: inline
  #   text_only_fallback: reject        # reject | describe
  # images:
  #   max_per_turn: 3
  #   max_bytes: 5000000
  #   max_reinspect_per_turn: 2
```

(The embedding comments about the 0.6B sibling at `:13-16` are kept; they
are elided above for length.)

### 5.4 README paragraph (`examples/harness/ask_your_docs_agent/README.md`)

Replaces the flags/env paragraph at `:100-106` and extends the config
paragraph at `:108-121`:

> `harness-ask-your-docs` launches the Streamlit UI. The chat model's
> endpoint comes from the `ask_your_docs.llm` block of the same YAML the
> `--config` flag points at: `base_url` (an OpenAI-format endpoint),
> `auth.token_url` (an internal token service whose response body is the
> bearer; set `token_field` when the body is JSON) or `auth.api_key_env`
> (the name of the environment variable holding a key), and `vision`
> (`true`, `false`, `null` to detect, or `{model: …}` to send images to a
> second model on the same endpoint). `OPENAI_BASE_URL` / `LLM_MODEL`
> override the YAML, `--base-url` / `--model` override those, and the
> sidebar's Connection dialog overrides everything for the session — it
> lists the endpoint's models, renews the token and tests the connection.
> With no `llm` block the agent uses the vendor default endpoint and
> `OPENAI_API_KEY`, as before. Keys are never put in YAML or on the command
> line.

The same edit fixes `:52` ("the six pydocs-mcp tools" → "the nine
pydocs-mcp tools") and rewrites `:31-32` vendor-neutrally ("any model served
over the OpenAI API protocol, hosted or local, via the base URL"). The README
audit grep of CLAUDE.md §README runs before the commit.

### 5.5 CHANGELOG entry (under `## [0.6.0] — Unreleased`, `### Added` and `### Changed`)

> **Added — ask-your-docs LLM connection.** One `ask_your_docs.llm` YAML block
> configures the chat model's OpenAI-format endpoint, its bearer (an internal
> token service renewed on `401`, or a named environment variable), and
> vision (`true` / `false` / detect / a second model on the same endpoint).
> The sidebar's four connection inputs become one status line and a
> Connection dialog that lists the endpoint's models, renews the token and
> tests the connection. Both capability probes now use the agent's
> credential (without a block, the endpoint probe therefore carries
> `OPENAI_API_KEY` when that variable is set). No block ⇒ otherwise unchanged
> behavior.
>
> **Changed — `ask_your_docs.multimodal.preferred_architecture` default
> `vision_subagent` → `inline`.** A multimodal main model now answers and
> sees in one prompt; set `vision_subagent` back for the separate describe
> hop.

---

## 6. Error handling

| # | Condition | Where | Behavior | Message shape |
|---|---|---|---|---|
| E1 | Token service unreachable / non-2xx after 3 attempts | `TokenServiceBearer.current/renew` — on the chat path **and** on the build path (`get_agent` preflight, the ladder, the listing; H3) | `TokenServiceError`; the LLM request never leaves; nothing is cached; UI status row shows `token unavailable ⚠` with the text in `help=`; a question fails at the send-loop boundary (§4.9) with `st.error` and the question kept | `token service {display_url(url)} unreachable after {attempts} attempts (last: {status or exc.__class__.__name__})` |
| E2 | Token body unparsable (`token_field` set, non-JSON or missing field) | same | `TokenServiceError`; **no body bytes** — in this failure the body usually *is* the token under another key (H4) | `token service {display_url(url)}: expected JSON body with field 'access_token', got keys=['token']` / `got non-JSON body (text/html, 512 bytes)` |
| E3 | Empty token body | same | `TokenServiceError` | `token service {display_url(url)}: expected a non-empty token body, got empty (content-type {ct})` |
| E4 | Two consecutive renew statuses on one request — or any `401` / `403` under `ENV_KEY` / `NONE`, which have no renewing Auth | `RenewOnStatusAuth` → SDK `AuthenticationError` / `PermissionDeniedError` → `translate_auth_errors` at every consumer | `BearerRejectedError` built **without** the response body, cause message scrubbed by `redact_bearer` | `endpoint {host} rejected bearer …{last_four} (status {status}); renew the token or check ask_your_docs.llm.auth` |
| E5 | `api_key_env` variable unset (strict form only; the no-block form is lenient, §4.4) | `EnvironmentKeyBearer.current` | `BearerUnavailableError` at first request | `environment variable {var} is unset; ask_your_docs.llm.auth.api_key_env names it` |
| E6 | Listing fails (network, 4xx/5xx, bad payload) — bearer failures are E1/E5, not E6 | `fetch_model_ids` | non-fatal: `ModelListing(error=...)`; dialog falls back to a text field; JSON log `model_listing_failed` | caption `listing failed: {error}` (URLs in `display_url` form) |
| E7 | Unknown key under `llm` / `auth` / `vision` | config load | pydantic `extra="forbid"` error at startup | pydantic's `Extra inputs are not permitted` naming the key |
| E8 | `auth` with both or neither key | config load | `ValueError` from the validator | `ask_your_docs.llm.auth: got ['token_url', 'api_key_env'], expected exactly one of token_url / api_key_env` |
| E9 | `vision.model == model` | `resolve_llm_connection` | treated as `vision: true`; JSON warning `vision_model_equals_main` | `ask_your_docs.llm.vision.model {m!r} equals the main model; treating as vision: true` |
| E10 | `vision: false` with an attached image | `text_only_policy` (`app.py:227`) | today's `reject` / `describe` policy, source `configured` | unchanged text |
| E11 | Test connection fails | dialog | caption with the exception class and the message passed through `redact_bearer` | `test failed: {exc.__class__.__name__}: {redacted message}` |
| E12 | `preferred_architecture: inline` with a separate vision model under `auto` | `AutoArchitecture.build` | builds `vision_subagent`; JSON info `auto_routing` | `inline cannot route images to {vision_model}; building vision_subagent` |
| E13 | Vision-route architecture on a text-only image model, including an explicit `architecture: inline` under `SEPARATE_MODEL` (§4.7) | `_build_architecture` | `AgentArchitectureError` | `architecture {name!r} needs a vision-capable image model; {model} is text-only (source={source}); set ask_your_docs.llm.vision: true or vision.model` |
| E14 | `auth.token_url` with `base_url: null` (`api_key_env` with `null` is valid, D2: an external key on the vendor default endpoint) | config load | `ValueError` from the validator: a token service authenticates one internal endpoint, so the block must name it | `ask_your_docs.llm.auth.token_url needs base_url; got null` |
| E15 | Bearer over `http://` to a non-loopback host (block present) | `resolve_llm_connection` | not an error: one `bearer_over_cleartext` JSON warning and `⚠ http` in the status line (H2) | log `{"event": "bearer_over_cleartext", "endpoint": <display_url form>}` |
| E16 | `auth.token_url` carries userinfo or a query string | config load | `ValueError` (H4) | `ask_your_docs.llm.auth.token_url must not carry credentials in userinfo or query; got {display_url(url)}` |
| E17 | `renew_on_status` outside `{401, 403, 407}` | config load | `ValueError` (H3) | `ask_your_docs.llm.renew_on_status: got 503, expected a subset of {401, 403, 407}` |
| E18 | Effective `base_url` origin differs from the YAML `base_url` origin | `resolve_llm_connection` | not an error: one `bearer_origin_changed` JSON warning, `⚠ endpoint differs from ask_your_docs.llm.base_url` in the status line; the bearer is still sent (D3, H1) | log `{"event": "bearer_origin_changed", "configured_origin": ..., "resolved_origin": ...}` |
| E19 | `connection.model is None` reaches `build_agent` | `build_agent` | `AgentArchitectureError` (the page never gets here, §4.9) | `no model chosen; set ask_your_docs.llm.model, LLM_MODEL, --model or pick one in the Connection dialog` |

All exception classes derive from `PydocsMCPError` (`exceptions.py`);
`TokenServiceError`, `BearerUnavailableError` and `BearerRejectedError` are
defined in `bearer_tokens.py`; none carries the token in `args`, `__str__`
or `__notes__`, and every URL they name is in `display_url` form (AC-9,
AC-36).

---

## 7. Contract guarantees

### 7.1 No MCP change

No tool, parameter or envelope field is added or altered. Everything in this
design runs in the harness process; the `pydocs-mcp serve` subprocess and
`docs/tool-contracts.md` are untouched. The golden registration surface
(`tests/fixtures/goldens/mcp_registration_surface.json`) is not modified.

### 7.2 Byte identity without the block

With `ask_your_docs.llm` absent:

- `build_chat_model` issues exactly `ChatOpenAI(model=model,
  base_url=base_url)` for the agent and the rung-4 probe (§4.5 rule 1) —
  the same kwargs as `agent.py:340`; only the rung-3 / listing client takes
  the rule-1 carve-out, because that call is unauthenticated today and must
  stay buildable without the variable.
- The prompt assembled by `_assemble_prompt` (`agent.py:188-215`) is
  unchanged; the golden `ask_your_docs_system_v1.txt` of the UI plan is
  unaffected.
- The no-block bearer is the **lenient** `EnvironmentKeyBearer` (§4.4): with
  `OPENAI_API_KEY` unset, rung 3 and the listing stay unauthenticated
  exactly as today — a local server with `endpoint_probe: true` and no
  variable keeps working, the status line shows `no auth`, and nothing
  raises. With the variable set, rung 3 and the listing carry
  `Authorization: Bearer $OPENAI_API_KEY` (rung 4's SDK path already did).
  This is the single sanctioned no-block difference (R2, D5) and it is
  listed in the CHANGELOG entry.
- `auto` on a multimodal main model now builds `inline` instead of
  `vision_subagent` — the dated default flip of R6, also in the CHANGELOG.
  A deployment that pins `preferred_architecture: vision_subagent` observes
  no change.
- The eval binding's control arm: same `build_agent` call, same digest
  (§4.11).

### 7.3 The eval binding

`AskYourDocsRunnerSettings` field set unchanged; `make_harness_runner`
unchanged; `delivery_map_digest()` literal unchanged
(`5072aa2e926f9c508233ce84b21edd937a3efe9706a00408f3ff84a998115e6d`);
`Trajectory.cost_usd == 0.0` unchanged; `_intercept` and `serve_connection`
keep their names and signatures (`binding.py:320`).

### 7.4 The pending UI plan

`build_agent` keeps returning a 2-tuple; `build_agent_with_scope_capabilities`
gains `connection=` and `bearer=` keywords with `None` defaults; `BuiltAgent`
gains `vision_capabilities`; the seeded
`st.session_state["scope_capabilities"]` and the `connection_bearer` /
`connection_list_models` / `connection_transport` seams keep
AppTests network-free; the `streamlit>=1.57` floor covers `st.dialog`.
The `agent.py` line budget closes only with both extractions in (§4.1):
the plan's `scope_prefix` → `question_scope.py` (its Task 4) and this
design's `reformulate` / `_history_line` → `reformulation.py`; whichever
lands second performs the move that is still missing, and AC-29 is the
gate.

One plan edit is required, recorded here for whichever lands second
(**plan Task 9**): the plan's `page_scope_capabilities(workspace, model,
base_url, config)` has no auth identity to hand to `get_agent`, so its
signature becomes `page_scope_capabilities(workspace, connection, config)`
— it reads `connection.model` / `connection.base_url`, computes
`identity = connection_identity(connection)`, returns
`NO_SCOPE_CAPABILITIES` when `connection.model is None` (the same branch it
already takes for an empty `model`), and otherwise calls
`get_agent(workspace, connection.model, connection.base_url, config,
identity)`. `get_agent` keeps the plan's parameter order with `identity`
appended (§4.9), so no other call site in the plan moves.

---

## 8. Acceptance criteria

Each criterion is a test the plan must ship; the file column is the intended
home.

| AC | Statement | Test home |
|---|---|---|
| AC-1 | `resolve_llm_connection` reproduces every row of the §4.3 table for `base_url` and for `model` as a fold over `(yaml, environment, launch, dialog)`; the `connection_resolved` log names the winning tier; empty strings at any tier are skipped; with a block present and no tier naming a model, `model is None`; without a block, `model == _DEFAULT_MODEL`. | `tests/harness/ask_your_docs/test_llm_connection.py` (core deps only) |
| AC-2 | No block ⇒ `block_present=False`, `auth_mode=ENV_KEY`, `api_key_env="OPENAI_API_KEY"`, `vision_rule=DETECT`, `renew_on_status=(401,)`, and `bearer_for_connection` returns the lenient `EnvironmentKeyBearer` (`required=False`); block with no `auth` ⇒ `AuthMode.NONE`. | same |
| AC-3 | `auth_mode is NONE` ⇒ the built client carries `StripAuthorizationAuth` on both httpx clients and the recorded request has no `Authorization` header (verified through `RecordingTransport` on the wire on the locked SDK, not by inspecting kwargs); no reliance on an empty `api_key`. | `test_bearer_tokens.py` (`importorskip("langchain_openai")`) |
| AC-4 | `TokenServiceBearer.current()` fetches once and caches: two calls, one HTTP GET against `FakeTokenService`; `token_field=None` uses the stripped body; `token_field="access_token"` reads the JSON field. | same |
| AC-5 | Callable key refresh per attempt: with `max_retries=2` and a `RecordingTransport` answering 500, 500, 200, the three requests carry the bearer returned by `current()` at each attempt (a rotating fake proves re-evaluation). | same |
| AC-6 | 401 renew + retry exactly once: transport answers 401 then 200; the Auth flow calls `renew(rejected=<the token parsed from the request header>)`; the second request's `Authorization` is `Bearer <renewed>`; `FakeTokenService.calls == 2` (initial + renew); the SDK sees one attempt (`x-stainless-retry-count` = `0` on both). | same |
| AC-7 | No retry on a second 401: transport answers 401, 401; exactly two requests; `BearerRejectedError` whose message contains the last four characters and the `display_url` host, and not the token nor the response body. | same |
| AC-8 | SDK retries reuse the cached token: after AC-6's renewal, a following 429-then-200 pair sends the renewed bearer on both attempts with no further token-service call. | same |
| AC-9 | The token never appears in any log record (`caplog` at `DEBUG` on the `pydocs_mcp`, `openai` and `httpx` loggers, JSON fields), in any exception text, or in the reinspect tool-result string across AC-4–AC-8 and AC-35; `describe()` exposes only `last_four` and `renewed_at`; `bearer_fetched` / `bearer_renewed` records carry no `last_four`. | same |
| AC-10 | Token service down: 3 attempts with `_TOKEN_FETCH_BACKOFF_SECONDS` zeroed, `TokenServiceError` naming the `display_url` and `3`. | same |
| AC-11 | `EnvironmentKeyBearer` reads the variable on every call (rotating the variable rotates the header); the strict form (`required=True`) raises `BearerUnavailableError` naming the variable when unset; the lenient form returns `""` and the built client sends no header. | same |
| AC-12 | Listing with auth: `fetch_model_ids` issues `GET /models` through an `openai.AsyncOpenAI` built from `connection_auth_kwargs` and sends the bearer; returns sorted unique ids from `{"data":[{"id":…}]}`; `NoBearer` ⇒ no header; the no-block lenient bearer with `OPENAI_API_KEY` unset ⇒ the rule-3 client, no header and no construction error (the §4.5 rule-1 carve-out), with it set ⇒ the header; `base_url=None` exercises the same path against the SDK's vendor-default URL on the `RecordingTransport`. | `test_model_listing.py` |
| AC-13 | Listing failure is non-fatal: 500 / connection error / items without `id` ⇒ `ModelListing(model_ids=(), error=…)`, no exception, one `model_listing_failed` log; `TokenServiceError` / `BearerUnavailableError` propagate instead. | same |
| AC-14 | Listing cache: two calls within the TTL make one request; advancing the injected clock past `_MODEL_LISTING_TTL_SECONDS` makes a second; `clear_model_listing_cache()` evicts. | same |
| AC-15 | Rung 3 goes through the authenticated seam: `FakeModelsEndpoint` receives the bearer; the existing AC13 pins (`test_multimodal_detection.py`) still pass. | `test_multimodal_detection.py` |
| AC-16 | Rung 4 builds its probe model through `build_chat_model` with `max_retries=0` and the probe timeout (kwargs spy). | same |
| AC-17 | Capability resolution (§4.7): `resolve_vision_capabilities` is the single call site — a spy proves `build_agent` (with `capabilities=None`) and the app's `get_capabilities` both call it and that `build_agent` skips it when `capabilities=` is injected; `MULTIMODAL` / `TEXT_ONLY` ⇒ `(True/False, CapabilitySource.CONFIGURED)` with `detect_capabilities` not called; `DETECT` calls it once; `SEPARATE_MODEL` does **not** call it, yields main `(False, CONFIGURED)` and vision `(True, CONFIGURED)`, and builds a second `ChatOpenAI` for `vision_model` (spy records two constructions with the same `base_url` and the same bearer object). | `test_agent_connection.py` (new) |
| AC-18 | `auto` routing table (§4.7): text-only main ⇒ `text_react`; multimodal main ⇒ `inline` by default and `vision_subagent` when preferred; separate vision model ⇒ `vision_subagent` whose `vision_extract` node calls `FakeVisionLlm`, not `FakeLlm`; `inline` preferred + separate model ⇒ `vision_subagent` plus one `auto_routing` log; explicit `architecture: inline` + separate model ⇒ `AgentArchitectureError` with the E13 text. | `test_architectures.py` |
| AC-19 | Byte identity: with no block, the `ChatOpenAI` spy records kwargs `{"model": m, "base_url": b}` and nothing else; `build_agent` still returns a 2-tuple; `inspect.signature(build_agent).parameters["prompts"]` stays keyword-only. | `test_prompt_seam.py` |
| AC-20 | `vision.model == model` ⇒ `vision_rule=MULTIMODAL`, `vision_model=None`, one `vision_model_equals_main` warning. | `test_llm_connection.py` |
| AC-21 | Default flip pinned: `AskYourDocsConfig().multimodal.preferred_architecture == "inline"`, the YAML agrees, `AppConfig.load().ask_your_docs == AskYourDocsConfig()` still holds with `llm: null`. | `tests/test_config_ask_your_docs.py` |
| AC-22 | Config validation: unknown key under `llm` / `auth` rejected; `auth` with both / neither key rejected with the E8 message; `vision: {model: ""}` rejected; `auth.token_url` with `base_url: null` rejected with the E14 message while `auth.api_key_env` with `base_url: null` loads (D2); `token_url: http://u:p@host/t?k=v` rejected with the E16 message; `PYDOCS_ASK_YOUR_DOCS__LLM__VISION=true` sets `vision` through the env layer. | same |
| AC-23 | `describe_images` sends one `HumanMessage` with the rendered `vision_extraction_v1` text plus the image blocks, normalizes list content to text parts, strips; the reinspect tool wraps its failure as a redacted tool-result string and the vision node lets it propagate. | `test_image_attachment.py` (existing; already imports `attachments` — the `describe_images` rows), `test_reinspect_tool.py` (the tool-result row), `test_architectures.py` (the node row) |
| AC-24 | CLI: the five flags parse as today (`test_cli_parser.py`); `--api-key` is rejected by the parser; importing `cli` leaves `streamlit` / `langgraph` / `httpx` out of `sys.modules`. | `test_cli_parser.py` |
| AC-25 | Dialog states via AppTest with the `connection_bearer` / `connection_list_models` seams: (a) the status line is an `at.caption` entry rendering host, model, auth cell and `vision: yes (static)` for the no-block default model; (b) clicking "Connection" opens a dialog block under `at._tree[2]` with a Base URL input, a Model selectbox listing the seeded ids and Apply; (c) a `connection_list_models` seam that raises renders the text-field fallback and the error caption; (d) Renew appears only for a token-service connection; (e) Apply writes `connection_override` (asserted on a fresh `AppTest` afterwards, per §4.9); (f) a block without `model` and no `LLM_MODEL` renders `model: not chosen`, builds no agent (`get_agent` spy uncalled), and the selectbox opens on the first seeded id. | `test_app_connection_dialog.py` |
| AC-26 | Cache key: `get_agent` is called with `(workspace, model, base_url, config, identity, _connection, _bearer)`; calling `page_bearer(connection).renew()` leaves `get_agent.cache_info()`-equivalent untouched (the same `BuiltAgent` object is returned); `identity == (auth_mode, token_url or api_key_env or "", block_present)` and contains no token, key value or config path. | same |
| AC-27 | Binding: the settings contract suite passes; `delivery_map_digest()` equals the pinned literal; `_build_and_execute` passes a `connection` whose `block_present` is `False` when `settings.harness.llm is None` and the file named by `pydocs_config` has no block (the control arm); `TOKEN_SERVICE` when that file (written to `tmp_path`) carries `auth.token_url`; an arm-level `harness={"llm": {...}}` wins over the file. | `test_binding.py` |
| AC-28 | Every network-bound helper has an explicit timeout and a bounded attempt count: a static test asserts `_TOKEN_FETCH_TIMEOUT_SECONDS`, `_LISTING_TIMEOUT_SECONDS`, `_TEST_CONNECTION_TIMEOUT_SECONDS`, `_MIN_RENEW_INTERVAL_SECONDS` are finite and `_TOKEN_FETCH_ATTEMPTS`, `_LISTING_MAX_RETRIES` ≤ 3; the token-fetch constants are defined in `bearer_tokens.py`, not aliased from `multimodal.py`. | `test_llm_connection.py` |
| AC-29 | Line budgets: `agent.py`, `app.py` and every new module (`llm_connection.py`, `bearer_tokens.py`, `model_listing.py`, `connection_dialog.py`, `reformulation.py`) stay under 500 lines; `ask_your_docs_models.py` under 200; `reformulate` / `_history_line` are importable from `reformulation` and absent from `agent`. | `tests/harness/ask_your_docs/test_module_line_budgets.py` (new — no file-size guard exists at `bf21e8a`; a `pathlib` line count over the listed modules, core deps only) |
| AC-30 | README audit grep (CLAUDE.md §README) and `tests/test_doc_conformance.py` pass on the edited README; no vendor product name appears in the new paragraphs. | `test_doc_conformance.py` |
| AC-31 | Origin change (H1): YAML `base_url: https://llm.internal/v1` + `token_url`; a dialog / `OPENAI_BASE_URL` / `--base-url` value on another origin ⇒ the `RecordingTransport` on that endpoint **receives** `Authorization: Bearer <token>` (the bearer follows the endpoint, D3), exactly one `bearer_origin_changed` log with both origins in `display_url` form, `connection.origin_changed` is `True` and the status line ends its auth cell with `⚠ endpoint differs from ask_your_docs.llm.base_url`; the same origin with another path flags nothing. | `test_llm_connection.py` (resolution half), `test_bearer_tokens.py` (wire half), `test_app_connection_dialog.py` (status-line half) |
| AC-32 | Concurrent renewal is deduplicated (H3): two requests in flight on T1, both answered 401 ⇒ `FakeTokenService.calls == 2` (initial + one renew) and both retries carry the same T2; two concurrent first requests ⇒ one fetch. | `test_bearer_tokens.py` |
| AC-33 | Renew rate limit and status set (H3): `renew_on_status: [200]` and `[503]` are rejected at config load with the E17 message; a transport answering 401, 401, 401, 401 across two SDK attempts (`max_retries=1`, a `RenewOnStatusAuth` per attempt) makes exactly one token fetch after the initial one, with `_MIN_RENEW_INTERVAL_SECONDS` at its default. | `test_bearer_tokens.py`, `tests/test_config_ask_your_docs.py` |
| AC-34 | Token service down at build time (H3): `FakeTokenService(fail_first=3)` under `vision: null` with `endpoint_probe: true` ⇒ `build_agent` (and the app's `get_agent`) raises `TokenServiceError`, `_detection_cache` stays empty, exactly one burst of 3 token-service calls (not 9), no sleep beyond the fetch backoff; after the fake recovers the next build succeeds with the ladder's real verdict. | `test_agent_connection.py`, `test_multimodal_detection.py` |
| AC-35 | Redaction (H4): a `RecordingTransport` whose 401 body echoes the presented token, under each of `TOKEN_SERVICE`, `ENV_KEY` and `NONE` ⇒ `translate_auth_errors` yields a `BearerRejectedError` whose `str()` contains neither the token nor the body; the reinspect tool-result string contains neither; the app's send-loop `st.error` text contains neither; `caplog` at `DEBUG` on `openai` and `httpx` contains no token. | `test_bearer_tokens.py`, `test_reinspect_tool.py`, `test_app_connection_dialog.py` |
| AC-36 | `display_url` (H4): an E1 raised for `token_url="http://u:p@host/t?k=v"` (built directly, bypassing config validation) contains neither `p` nor `v`; the status line and every `help=` show `host[:port]` only. | `test_bearer_tokens.py` |
| AC-37 | E2 body shape (H4): `FakeTokenService(body_shape="json", json_key="token")` with `token_field="access_token"` ⇒ `TokenServiceError` naming `keys=['token']` and containing no token bytes; a `text/html` body ⇒ `non-JSON body (text/html, N bytes)`; `last_four` is the last four characters of the token. | same |
| AC-38 | SDK pin (Anchors): one test module, run on the installed `openai` (the locked 1.109.1 by `uv sync --frozen`), asserts that (a) a callable `api_key` is re-evaluated on every attempt, (b) `StripAuthorizationAuth` removes the header on the wire with the placeholder key, (c) an httpx client-level `auth` is honored by the SDK's `send` — so a future SDK bump goes red instead of silently disabling renewal. | `test_sdk_pins.py` (new) |
| AC-39 | Cleartext (H2): `base_url: http://llm.internal/v1` + `token_url` ⇒ the connection resolves (no error), `connection.cleartext_bearer` is `True`, exactly one `bearer_over_cleartext` log and `⚠ http` in the status line; `http://localhost:8000/v1` and `http://127.0.0.1:8000/v1` ⇒ `False` and nothing logged; no block with `OPENAI_BASE_URL=http://gpu-box:8000/v1` ⇒ `False` (the no-block path is outside H2). | `test_llm_connection.py` (resolution half), `test_app_connection_dialog.py` (status-line half) |
| AC-40 | Bearer registry: two `build_agent` calls with the same connection identity (the binding's per-sample loop) share one `TokenServiceBearer` and make one token fetch; `clear_bearer_registry()` resets; the app's `page_bearer(connection)` returns the registry's object. | `test_chat_model_factory.py`, `test_binding.py` |
| AC-41 | E3: `FakeTokenService(tokens=[""])` (and a whitespace-only body) with `token_field=None` ⇒ `TokenServiceError` whose message names the `display_url` and `expected a non-empty token body, got empty`; with `token_field="access_token"` and `{"access_token": ""}` the same; no LLM request leaves (`RecordingTransport` sees none). | `test_bearer_tokens.py` |
| AC-42 | E19: `build_agent(workspace, model=None, ...)` with a block present and no tier naming a model (`connection.model is None`) raises `AgentArchitectureError` with the `no model chosen; set ask_your_docs.llm.model, LLM_MODEL, --model or pick one in the Connection dialog` text before any tool or LLM construction (`ChatOpenAI` spy uncalled, `serve_connection` spy uncalled). | `test_agent_connection.py` |
| AC-43 | Test connection (§4.9 item 5, E11): AppTest clicks `connection_test` with a seeded candidate transport — (a) a 200 with `OK` ⇒ `connection_test_result` starts with `test passed: OK` and the caption is present; (b) a 401 whose body echoes the presented token ⇒ `connection_test_result` starts with `test failed: BearerRejectedError:` and contains neither the token nor the body; (c) a candidate Base URL on another origin ⇒ the transport records the `Authorization` header (the bearer follows the endpoint) and the caption carries the origin note. | `test_app_connection_dialog.py` |
| AC-44 | Status-line auth cells (§4.9): AppTest renders (a) `token …abcd 12:03` in the caption text itself for a `TOKEN_SERVICE` connection whose seeded `BearerStatus.renewed_at` is a fixed datetime (last four inline, D4; nothing in `help=` but the E1 text of case (b)); (b) `token unavailable ⚠` when the seeded bearer's `current()` raises `TokenServiceError`, with the E1 text in `help=` and `get_agent` uncalled; (c) `$LLM_KEY missing` for `auth.api_key_env: LLM_KEY` with the variable unset (strict form), `$LLM_KEY set` with it set; (d) the dialog's auth row shows `token …abcd · renewed HH:MM` inline (D4). | `test_app_connection_dialog.py` |
| AC-45 | Listing-cache eviction (§4.6): with a seeded listing inside the TTL, clicking `connection_refresh_models` makes a second `list_models` call and replaces the seeded ids; clicking `connection_renew` evicts the entry (`cached_model_listing` calls `list_models` again on the next run) and `FakeTokenService.calls` grows by one; neither click rebuilds the agent (`get_agent` spy count unchanged). | `test_app_connection_dialog.py` |

---

## 9. Testing plan

### 9.1 Named fakes (`tests/harness/ask_your_docs/_connection_fakes.py`)

- `FakeTokenService(tokens: list[str], *, fail_first: int = 0, body_shape:
  "text" | "json", json_key: str = "access_token")` — an
  `httpx.MockTransport` handler serving `/access-token`; records `calls`
  (and their timestamps, for AC-32/AC-33); `fail_first` answers 503 that
  many times and then recovers (AC-34); `json_key` puts the token under
  another key to provoke E2 (AC-37). Installed into `TokenServiceBearer`
  through an injectable `transport=` keyword (the production default is
  `None` ⇒ real transport).
- `RecordingTransport(script: list[int | Exception], *, echo_token_in_401:
  bool = False)` — an `httpx.MockTransport` for the chat endpoint (and the
  `/models` path) that answers the scripted statuses in order with a
  minimal chat-completion or model-list body on 200, records every request's
  `Authorization`, `x-stainless-retry-count` and body, and — with
  `echo_token_in_401` — puts the presented bearer into the 401 body (AC-35).
  `build_chat_model` accepts `transport=` for tests only; in production the
  factory passes none.
- `FakeModelsEndpoint(ids: list[str], *, status: int = 200, payload=None)` —
  the listing seam (`list_models: Callable[[LlmConnection, BearerSource],
  Awaitable[list[dict]]]`), recording `seen_bearer = bearer.current()` and
  `seen_base_url = connection.base_url` on every call; the existing fake of
  the same name in `test_multimodal_detection.py:20-54` (today called as
  `(base_url, model, timeout)`) is folded into it — its `entry` / `error`
  constructor keywords stay so the AC13 pins there construct it unchanged,
  and rung 3 reads `model` and `timeout` from the ladder's own arguments,
  not from the seam.
- `FakeProbeLlm` (`test_multimodal_detection.py:35-47`) — the rung-4 seam
  widens from `(model, base_url, timeout)` to `(connection, bearer, model,
  timeout)`: `_default_probe_llm` builds the probe model through
  `build_chat_model(connection, bearer, model=model, timeout_seconds=timeout,
  max_retries=0)` (§4.5). The fake keeps its `outcome` constructor keyword
  and its three outcomes, gains the two leading parameters, records
  `seen_bearer = bearer.current()` (so AC-15/AC-34 can assert the bearer
  reached rung 4 and that a raising bearer propagates), and moves into
  `_connection_fakes.py` beside `FakeModelsEndpoint`; the ladder's
  `probe_llm=` keyword name is unchanged.
- `FakeSlowClock` — an injectable `now` for `_MIN_RENEW_INTERVAL_SECONDS`
  and the listing TTL, so AC-14 and AC-33 never sleep.
- `FakeLlm` / `FakeVisionLlm` from `_agent_fakes.py:18-50` are reused for the
  routing and describe tests.
- `RotatingBearer` — a `BearerSource` whose `current()` returns `k1, k2, k3…`
  to prove per-attempt re-evaluation (AC-5).

### 9.2 New test modules

All under `tests/harness/ask_your_docs/`; each `importorskip`s the extra
it needs, as the sibling modules do (`test_attachment.py:3-5`).

| Module | Skips without | Covers |
|---|---|---|
| `_connection_fakes.py` | — (helpers, not a test module) | §9.1 |
| `test_llm_connection.py` | nothing (core deps only: `resolve_llm_connection` is pure) | AC-1, AC-2, AC-20, AC-28, AC-31 (resolution half), AC-39 (resolution half) |
| `test_bearer_tokens.py` | nothing (core deps only: `httpx` ships with the required `openai`) | AC-4, AC-9 (bearer half), AC-10, AC-11, AC-32, AC-33 (bearer half), AC-36, AC-37, AC-41 |
| `test_chat_model_factory.py` | `langchain_openai` | AC-3, AC-5–AC-8, AC-9 (SDK half), AC-19, AC-31 (wire half: `build_chat_model` + `RecordingTransport`), AC-33 (transport half), AC-35 (translation half), AC-40 (registry half) |
| `test_model_listing.py` | `langchain_openai` | AC-12, AC-13, AC-14 |
| `test_agent_connection.py` | `langgraph` | AC-17, AC-34 (build half), AC-42 |
| `test_app_connection_dialog.py` | `streamlit` | AC-25, AC-26, AC-31 (status-line half), AC-35 (send-loop half), AC-39 (status-line half), AC-43, AC-44, AC-45 |
| `test_sdk_pins.py` | `openai` | AC-38 (installed, locked SDK) |
| `test_module_line_budgets.py` | nothing | AC-29 |

### 9.3 Which existing tests move or change

| Test | Change |
|---|---|
| `test_multimodal_detection.py` | `FakeModelsEndpoint` / `FakeProbeLlm` widen to the new seams (§9.1) and move to `_connection_fakes.py`; assertions unchanged; `source` comparisons keep working against the StrEnum; AC-15, AC-16 and the ladder half of AC-34 land here. |
| `test_prompt_seam.py` | `_capture_build` accepts `vision_llm` / `vision_capabilities`; the `ChatOpenAI` monkeypatch becomes a kwargs spy (AC-19); the `OPENAI_API_KEY` setenv stays (no-block path). |
| `test_architectures.py` | `_ctx` unchanged (identity defaults); new rows for the routing table. |
| `test_app_image_attachment.py` | the `vision: yes (static)` scan of `at.caption` (`:24-25`) is unchanged — the status line is an `st.caption`; seeds nothing new (the page lists models only when the dialog opens). |
| `test_app_attachment.py` | seeds nothing new (the page lists models only when the dialog opens). |
| `test_config_ask_your_docs.py` | `preferred_architecture` assertions at `:18` and `:45` flip to `inline`; the key-list test grows `llm`; new validation cases (E8, E14, E16, E17). |
| `test_binding.py` | `_fake_build_agent` accepts `connection=`; AC-27 and AC-40 rows (a `tmp_path` pydocs config with and without the block; an arm-level `harness={"llm": ...}`). |
| `test_cli_parser.py` | AC-24 rows. |
| `test_reinspect_tool.py` | the failure-path assertion checks the redacted tool-result string (AC-23, AC-35). |
| `test_image_attachment.py` | `_history_line` import at `:139` moves to `reformulation`; gains the `describe_images` rows of AC-23. |

### 9.4 Commands

The plan's first step is `uv sync --frozen` in the worktree, so every test
runs on the locked `openai` 1.109.1 / `langchain-openai` 1.1.9 (Anchors
row). Then the documented commands only: `pytest -q`, `ruff check python/
tests/ benchmarks/`, `ruff format --check …`, `complexipy …
--max-complexity-allowed 15`, `vulture … --min-confidence 80`, the 90 %
coverage gate. The AppTests `importorskip("streamlit")` as today.

---

## 10. Open decisions for the owner

| # | Question | Proposal |
|---|---|---|
| O1 | Should `ask_your_docs.llm` expose `timeout_seconds` and `max_retries` for the chat model? Today the SDK's 600 s / 2 retries apply implicitly (`openai/_constants.py:9-14`). | Not in this iteration: the SDK bounds already satisfy the timeouts-and-bounded-retries rule; add the two keys later if a deployment needs them (they would be A/B-testable, hence YAML). |
| O2 | Should the sidecar spec's `extra_body` knob for the vision model be carried over as `ask_your_docs.llm.vision.extra_body`? | No — nothing in D6 asks for it; revisit when a provider needs a per-model body field. |

Closed since 0.2 (decided in the body, no longer open): the
`DetectionSource` → `CapabilitySource` StrEnum migration is decided in §4.7
(migrate now); Renew is shown only for a configured token service per
D7/R7 (§4.9 item 2) — there is no "Re-read $VAR" variant, because
`EnvironmentKeyBearer` already re-reads the variable on every request; the
origin-binding, cleartext-key and last-four-floor proposals are recorded as
not adopted in §3.1.

---

## 11. References

- Owner decisions D1–D10, 2026-09-05 (this document §3).
- `python/pydocs_mcp/harness/ask_your_docs/agent.py` — `build_agent`
  signature `:269-287`, `ChatOpenAI` construction `:340`, capability
  detection `:357-361`, `_build_architecture` `:152-180`, `_assemble_prompt`
  `:188-215`, `serve_connection` `:242-266`, `reformulate` `:391-416`,
  return `:370`.
- `python/pydocs_mcp/harness/ask_your_docs/app.py` — caches `:55-91`
  (`get_capabilities` `:71-77`, `get_agent` `:80-91`, `base_url or None`
  `:86`), sidebar Connection block `:97-110` (inputs `:102-105`, badge
  `:106-110`), `text_only_policy` call `:227`, reject-verdict `st.error`
  `:231-233`, send loop `:218-276`.
- `python/pydocs_mcp/harness/ask_your_docs/cli.py` — `_ENV` `:18-23`,
  `_EXTRA_MODULES` `:28`, parser `:42-63`, `main` `:66-90` (flag→env copy
  `:70-73`, `streamlit run` argv `:77-87`).
- `python/pydocs_mcp/harness/ask_your_docs/binding.py` —
  `AskYourDocsRunnerSettings` `:150-163` (`harness` default `:163`),
  delivery map and digest `:75-120`, `_serve_session_tools` `:309-327`
  (`pydocs_config` in the serve argv `:323`), `_build_and_execute`
  `:330-369` (`config=settings.harness` `:358`);
  `benchmarks/src/pydocs_eval/optimize/arms.py` `:80` (the arm `settings`
  mapping that populates `harness`).
- `python/pydocs_mcp/harness/ask_your_docs/multimodal.py` —
  `DetectionSource` `:22`, constants `:99-101`, cache `:105-116`,
  `_with_rung_retry` `:133-148`, `_entry_hints_vision` `:151-165`,
  `_default_http_get` `:168-179`, `_default_probe_llm` `:182-201`,
  `detect_capabilities` `:211-231`, rungs `:234-260`.
- `python/pydocs_mcp/retrieval/config/app_config.py` `:210`
  (`env_prefix="PYDOCS_"`) — the env overlay that makes
  `PYDOCS_ASK_YOUR_DOCS__LLM__*` part of the `AppConfig` layer.
- `python/pydocs_mcp/harness/ask_your_docs/architectures/base.py` `:19-85`,
  `auto.py` `:23-40`, `vision_subagent.py` `:24-78`, `reinspect.py` `:30-91`.
- `python/pydocs_mcp/retrieval/config/ask_your_docs_models.py` `:1-87`;
  `python/pydocs_mcp/defaults/default_config.yaml` `:340-362`.
- `examples/harness/ask_your_docs_agent/README.md` `:31-32`, `:52-54`,
  `:68-83`, `:95-121`; `configs/index_gpu.yaml` `:9-31`;
  `configs/serve_cpu_openvino.yaml` `:1-34`.
- `pyproject.toml` `:139-144` (extra), `:182` (script).
- Tests: `tests/harness/ask_your_docs/test_multimodal_detection.py`
  `:20-152`, `test_prompt_seam.py` `:35-203`, `test_binding.py` `:27-273`,
  `test_cli_parser.py` `:10-34`, `test_app_attachment.py` `:8-21`,
  `test_app_image_attachment.py` `:9-29`, `test_architectures.py` `:20-115`,
  `test_agent_registry.py` `:18-77`, `_agent_fakes.py` `:18-50`;
  `tests/test_config_ask_your_docs.py` `:14-137`;
  `tests/test_doc_conformance.py` `:35-46`, `:94-117`, `:206-262`.
- Toolkit (the versions locked in `uv.lock`, verified in the main checkout's
  `.venv` at `~/Projects/pyctx7-mcp/.venv`; see Anchors):
  `openai` 1.109.1 — callable `api_key` `_client.py:100,132-143`,
  `auth_headers` rendering the header only for a non-empty key
  `_client.py:315-320`, `_refresh_api_key` per attempt `_client.py:304-311` +
  `_base_client.py:963-965` (async `:1510-1512`), `_validate_headers`
  no-op base `_base_client.py:663-672`, `_should_retry`
  `_base_client.py:750-786`, retry constants `_constants.py:9-14`,
  custom clients `_base_client.py:825-868`, `DefaultHttpxClient`
  `_base_client.py:789-808` (sync; the async alias follows it), `send` with
  client auth `_base_client.py:971-986`, `APIStatusError` message embedding
  the parsed body `_base_client.py:416-427`;
  `langchain-openai` 1.1.9 — `api_key` callable `chat_models/base.py:533-541`,
  `_resolve_sync_and_async_api_keys` `_client_utils.py:115-148`, client
  wiring `base.py:953-1025`, cached default clients `_client_utils.py:22-112`,
  `ainvoke` path `base.py:1625-1668`, `stream_usage` `base.py:921-940`;
  `httpx` 0.28.1 — `Auth` contract `_auth.py:22-56`, flow dispatch
  `_client.py:879-962`, `DigestAuth` re-yield precedent `_auth.py:193-221`,
  header replacement `_models.py:304-326`;
  `streamlit` 1.59.1 — `st.dialog` decorator `elements/dialog_decorator.py:62-111`,
  one dialog per run `elements/lib/dialog.py:176-199`, AppTest tree
  `testing/v1/element_tree.py:2532-2544`, `:2020-2027`, full-script reruns
  `testing/v1/app_test.py:368-380`, forward queue trap
  `runtime/forward_msg_queue.py:67-108`, `SafeSessionState`
  `runtime/state/safe_session_state.py:101-125`.
- Companions: `docs/superpowers/plans/2026-09-04-ask-your-docs-branch-scope-ui.md`
  (Tasks 1, 5, 7, 9); `docs/superpowers/specs/2026-09-04-ask-your-docs-branch-scope-ui-design.md`
  §6.12–§6.13, §7; `docs/superpowers/specs/2026-07-14-ask-your-docs-vision-sidecar-spec.md`
  (commit `6d2ee9e`) §3.1–§3.5, §4.1–§4.3; `docs/tool-contracts.md`;
  CLAUDE.md §"MCP API surface vs YAML configuration", §"Default values",
  §"Null Object pattern", §"Coding Rules for AI Agents", §"README files",
  §"Async Patterns".
