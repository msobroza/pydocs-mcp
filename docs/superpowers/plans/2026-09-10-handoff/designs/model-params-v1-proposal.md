# Judgment and final design: model parameters for ask-your-docs (`ask_your_docs.llm.params`)

## 0. What I checked myself against origin/main 0.6.0

These are the facts I verified before scoring. Several of them correct claims made in the two input designs.

- **langchain-openai.** The lock pins 1.1.9, but the `pyproject.toml` floor is still `>=0.2`.
  - The installed `ChatOpenAI` has these typed fields: `temperature`, `top_p`, `max_tokens`, `reasoning_effort`, `seed`, `frequency_penalty`, `presence_penalty`, `stop`, `verbosity`, `extra_body`, `reasoning`.
  - A `>=0.2` install can predate the `reasoning_effort` field. The metadata-driven design noticed this; typed-static did not.
- **`max_tokens` rename.** `_get_request_payload` renames `max_tokens` to `max_completion_tokens` every time (`base.py:3054`), not only for some models.
- **gpt-5 temperature.** `validate_temperature` (`base.py:873-900`) silently removes any temperature other than 1 for gpt-5 models (except `-chat` models), unless `reasoning_effort == "none"`.
  - So "gpt-5 does not support temperature" is conditional on effort, and the client drops the value without saying so.
  - Separately, `.bind(temperature=…)` skips this validator entirely.
- **OpenRouter listing (436 models).** `supported_parameters` counts:

  | Parameter | Models listing it |
  |---|---|
  | `max_tokens` | 425 |
  | `temperature` | 351 |
  | `seed` | 330 |
  | `reasoning` | 305 |
  | `top_k` | 216 |
  | `reasoning_effort` | 167 |
  | `max_completion_tokens` | 60 |
  | `verbosity` | 21 |

  - 138 models list `reasoning` but not `reasoning_effort` (qwen 30, google 15, openai 12, anthropic 10, and others).
  - 101 models have `reasoning.mandatory: true`.
  - 115 models offer a set of efforts that does not include `none`.
- **typed-static's Anthropic example is wrong.** `anthropic/claude-sonnet-5` does list `reasoning_effort`. The underlying concern is still real for 138 other models, for example `qwen/qwen3.8-flash`.
- **Probe results.**
  - Sending `max_completion_tokens` together with `provider.require_parameters` returns a 404 on qwen3.8-27b.
  - A 100-token cap on a reasoning model gave `finish=length` with empty content (probes A and B).
  - `reasoning:{enabled:false}` works (probes C and D).
  - `usage_metadata.output_token_details.reasoning` survives the LangChain round trip.
- **What O1 and O2 actually say (spec §10, lines 1671-1672).**
  - O1: no `timeout_seconds`/`max_retries` "in this iteration … add later if a deployment needs them (A/B-testable, hence YAML)".
  - O2: no `vision.extra_body` knob; "revisit when a provider needs a per-model body field".
  - O2 is therefore narrow: it covers the vision `extra_body` knob and leaves the question open.
- **Eval binding.**
  - `binding.py` lives at `harness/ask_your_docs/binding.py`.
  - `connection_block_for_binding` returns the arm's `harness.llm` block whole, or loads it from the `pydocs_config` file with `PYDOCS_ASK_YOUR_DOCS__LLM__*` layered on top (warn only).
  - `arms.py:to_canonical` hashes `settings` whole.
- **Line counts.** `ask_your_docs_models.py` is 199 lines (cap <200). `llm_connection.py` and `app.py` are both 492, `agent.py` is 489, `model_listing.py` is 370.
- **`ModelListing`** keeps only `model_ids`. Raw entries are dropped after id extraction.

## 1. Scores

| Criterion | typed-static | metadata-driven |
|---|---|---|
| User value | **6** — 5 params. No thinking-off, no penalties, and no warning when reasoning tokens exhaust the cap. | **8** — broad set, `top_k`, thinking-off, starvation warning, detects values the client drops. |
| Correctness across providers | **6** — the Anthropic "reasoning-only" example is false. The name table ignores gpt-5's effort=none exception. `reasoning.mandatory` is not handled. It does handle `max_completion_tokens` correctly. | **7** — handles mandatory, drops (never coerces) unsupported effort values, routes `max_tokens` per metadata. But `stop` can truncate the ReAct agent's tool calls. `verbosity` is sent top-level on Chat Completions with unverified gateway support. Request-body routes change with the metadata. |
| Honesty when support is unknown | **8** — labels unreported support, learns rejections per session, never auto-retries. It misses the client's silent drop. | **8** — labels unverified rows and reads the built client to catch dropped values. But at page start it sends config values that are known to be unsupported, with only a caption. |
| Fit with 0.6.0 and O1/O2 | **9** — only first-class fields, one explicit `params=` keyword on the factory, the vision model and probe structurally excluded, O1 keys refused. | **5** — its request-body channel (for `top_k`, `min_p`, `reasoning.enabled`, the `max_tokens` alias) re-opens O2 within this feature. The 13-param registry is broader than the request needs (YAGNI). |
| Eval-arm safety | **9** — params come only from the arm. Raises before any spend when a param is known unsupported. Refuses file-sourced params. | **8** — sends exactly what the arm says, refuses file- and env-sourced params, records values per rollout. But nothing stops the client silently dropping a param before spend, so the arm could measure something different from its identity. |
| Implementation size and risk | **7** — about 480 new lines plus 3 refactors that only move code; low risk. | **5** — larger: validators generated from a registry, request-body routing that depends on metadata, a floor bump and relock, 38 tests. |
| **Total** | **45** | **41** |

**Synthesis.** Take typed-static's skeleton: first-class fields only, an explicit factory keyword, a snapshot fold, raise-before-spend in the binding, and learned rejections. Graft on metadata-driven's strengths:
- `reasoning.mandatory` and dropping (never coercing) effort values;
- detecting values the client silently drops;
- the reasoning-token starvation warning;
- the langchain-openai floor bump;
- recording the effective params in each run record;
- the two penalties.

Anything that needs the request body (`extra_body`) goes to a separate phase 2 that the owner must ratify.

## 2. Final proposal

### 2.1 Summary

- Add an optional, typed, validated `ask_your_docs.llm.params` block with 7 fields. All of them are first-class `ChatOpenAI` fields. Nothing goes through `extra_body`, `model_kwargs` or `reasoning=`.
- A missing or null key means "not sent" (the provider default applies).
- After a model is picked, the Connection dialog shows those fields, enabled or disabled per model. Support comes from listing metadata, then a model-family table, then "not reported", then rejections learned during the session.
- Params reach only the main chat model (including the rewrite step and same-model image descriptions). They never reach the separate vision model or the image probe.
- In eval, params come only from the arm's own `harness.llm.params`, so they are part of arm identity automatically. The binding refuses params it cannot honour **before any spend**.
- Unchanged: no new MCP tool or parameter, no launcher flags, O1 and O2 as written, and G8 (values are not secrets; keys stay out of logs and messages).

### 2.2 YAML schema and defaults

```yaml
ask_your_docs:
  llm:
    base_url: https://openrouter.ai/api/v1
    model: qwen/qwen3.8-27b
    auth: {api_key_env: OPENROUTER_API_KEY}
    params:                    # every key optional; absent/null = provider default (not sent)
      temperature: 0.2         # float [0, 2]
      top_p: 0.95              # float (0, 1]
      max_tokens: 2048         # int >= 1; on the wire: max_completion_tokens
      reasoning_effort: low    # none|minimal|low|medium|high|xhigh|max
      seed: 7                  # int [0, 2**63-1]; best-effort reproducibility
      frequency_penalty: 0.0   # float [-2, 2]
      presence_penalty: 0.0    # float [-2, 2]
```

**Model.** New module `retrieval/config/ask_your_docs_params_models.py`.
- `ReasoningEffort(StrEnum)`.
- A `CHAT_PARAM_SPECS` registry: a tuple of frozen `ChatParamSpec(name, kind, lo, lo_open, hi, choices)`. It is the single source of ranges; pydantic validators and dialog widgets both read it.
- `ChatModelParamsConfig(BaseModel, extra="forbid", frozen=True)`, with every field `X | None = None`.
- `LlmConnectionConfig.params: ChatModelParamsConfig = Field(default_factory=ChatModelParamsConfig)`.
- `default_config.yaml` keeps `llm: null` plus a commented template, so the default-config parity test stays green.

**Validation.** A before-validator puts the offending value in the message, because `error_redaction` blanks `input` under `ask_your_docs.llm`. Values are not secrets. It rejects:
- `bool`;
- numeric strings;
- NaN and ±inf;
- non-integer values for `max_tokens` and `seed`.

Example messages:
- `ask_your_docs.llm.params.temperature: got 2.5, expected a number in [0, 2] (omit it for the provider default)`
- `…reasoning_effort: got 'hgih', expected one of none, minimal, low, medium, high, xhigh, max`
- `ask_your_docs.llm.params: unknown key 'top_k'; supported: temperature, top_p, max_tokens, reasoning_effort, seed, frequency_penalty, presence_penalty` (echoes the key, never the value)
- `…: 'timeout' is not a model parameter; timeout/max_retries are not configurable in this iteration (owner decision O1)`
- The same kind of message for `max_retries`, `extra_body`, `model_kwargs`, `reasoning`, `stop`, `n`, `logit_bias`, `tools`, `response_format`.

### 2.3 Precedence

| Tier | Carries params? |
|---|---|
| YAML `llm.params` | yes — the baseline |
| AppConfig env `PYDOCS_ASK_YOUR_DOCS__LLM__PARAMS__*` | yes, through the existing pydantic-settings nesting (not a new tier) |
| Harness env (`OPENAI_BASE_URL`, `LLM_MODEL`) | no |
| Launcher flags | no |
| Connection dialog (session) | yes — a **complete snapshot** that replaces the YAML set |

- `fold_params(yaml, dialog) = dialog if dialog is not None else yaml`.
- The dialog pre-fills every row from the effective values. Clearing a field means "provider default".
- "Revert to YAML" sets the override back to `None`.
- `ConnectionOverride.params: ChatModelParams | None = None` is added as the **last** field, so the three positional call sites are unchanged.

**Effective params**, computed at every build:

```
filter(fold(yaml, dialog), support(model), rejected[(base_url, model)])
```

- The result is `EffectiveChatParams(sent, dropped)`: hashable, and part of `connection_key`. That key feeds the process-wide `get_agent` cache, so without it one session would get another session's params.
- Switching model in the dialog keeps the snapshot. The filter decides what gets sent.

### 2.4 Where params are sent

`build_chat_model(connection, bearer, *, params: EffectiveChatParams = NO_CHAT_PARAMS)` takes params only as this explicit keyword; they never ride implicitly on the connection object. With the default, the call shape stays exactly `{model, base_url}` (AC-19).

| Call | Params |
|---|---|
| Main model (`agent.py:367`: text_react, inline, vision_subagent's ReAct node) | yes |
| Reformulation | inherits the main model; `.bind(temperature=0)` **only if** the chat params already send a temperature that the client did not drop (`.bind` skips `validate_temperature`); effort and max_tokens are inherited |
| Image description on the same model | shared (same object); the `max_tokens` row says "also caps image descriptions" |
| Separate vision model (`agent.py:371-373`) | **no** |
| Image probe (`multimodal.py`) | **no** — a param 400 would otherwise downgrade vision silently; the detection cache key is unchanged |
| Test connection | yes — the candidate's effective params |
| `/models` listing | no |

### 2.5 Support resolution

Implemented in `harness/ask_your_docs/chat_params.py`. The first source that answers wins, per `(base_url, model, param)`:

1. **Listing metadata** (`LISTING`). `ModelListing` gains `param_support: Mapping[str, ModelParamSupport]`, parsed from the same payload with the same cache and TTL. Malformed metadata counts as "not reported" and writes one JSON log line.
   - `max_tokens` is supported if the model lists `max_tokens` or `max_completion_tokens`.
   - `reasoning_effort` is supported only if `reasoning_effort` itself is listed. If only `reasoning` is listed, the row is disabled with the caption "takes `reasoning` — not configurable here yet" (O-P3).
   - Effort choices are the enum intersected with `reasoning.supported_efforts`. `none` is disabled when `mandatory` is true.
   - `default_parameters` and `default_effort` become placeholders only.
2. **Model-family table** (`MODEL_NAME`). It reuses `REASONING_MODEL_PREFIXES`, promoted to a public name in `retrieval/llm_clients/openai.py`.
   - Reasoning prefixes: sampling params (temperature, top_p, penalties) are marked unsupported unless effort is `none`.
   - `gpt-4o`, `gpt-4.1`, `gpt-4-turbo`: effort is marked unsupported.
3. **Unreported** (`NONE`). Every row is enabled and labelled "unverified — run Test connection". Values are sent as set.
4. **Learned rejection** (session only).
   - A `BadRequestError` whose `.param` names a param that was sent marks it rejected in `st.session_state`. If `.param` is absent, a quoted-name match is used, restricted to the params that were sent.
   - There is no auto-retry.
   - The dialog offers "Clear rejection".
5. **Client-drop detection.** After building, compare the built `ChatOpenAI` fields with what was requested. For example, gpt-5 with temperature 0.2 gets the caption "ignored by the client unless effort is 'none'". The value goes into `dropped` and is reported honestly.

**Page start, before the dialog is opened.**
- Values from YAML are filtered with `drop` using the model-family table, any listing already cached for this URL, and learned rejections. The page does not fetch the listing just to filter.
- It emits one caption and one JSON log: `{"event":"chat_params_effective","model":…,"support_source":…,"sent":[…],"dropped":[{"param":…,"reason":…}]}`.

**Starvation warning.** Shown when `max_tokens` is below 1024 and reasoning is supported and not `none`.

### 2.6 Dialog mockups

The full state set (A–F) is in `mockup_spec.json`. The three states the brief asked for:

**Model with rich metadata** (OpenRouter, qwen/qwen3.8-27b)

```
│ Model     [qwen/qwen3.8-27b                               ▾] [⟳] │
│ 436 models listed · vision: no (listing)                         │
│ Model parameters              support: reported by this endpoint │
│  Reasoning effort [low                 ▾]  from YAML             │
│                    offered: xhigh · medium · low (default xhigh) │
│  Temperature      [ 0.2     ]  from YAML    (provider default 1) │
│  Top p            [         ]  provider default (0.95)           │
│  Max tokens       [ 512     ]  custom                            │
│    ⚠ reasoning tokens count against this cap; below ~1024 a      │
│      reasoning model can return an empty reply                   │
│  Seed             [         ]  provider default                  │
│  ▸ More: frequency penalty, presence penalty                     │
│  Also reported, not configurable here: top_k, min_p, rep. pen.   │
│  [Reset all to provider defaults]  [Revert to YAML]              │
│ [Test connection]  ok · sent temperature, max_tokens, effort     │
│                                                       [Apply]    │
```

The same state with `openai/gpt-5.6-sol`: the Temperature row is disabled, with the caption "Not supported by openai/gpt-5.6-sol — YAML value 0.2 will not be sent".

**Endpoint without metadata** (vLLM, Ollama, llama.cpp, OpenAI `/v1/models`)

```
│ Model parameters        support: not reported by this endpoint   │
│  All rows editable · unverified. Run Test connection before      │
│  Apply; values are sent exactly as set.                          │
│  Reasoning effort [(provider default)   ▾]  all 7 levels         │
│  Temperature      [ 0.2     ]  from YAML · unverified            │
│  Top p [     ]  Max tokens [     ]  Seed [     ]                 │
│  [Test connection]  ok · sent temperature                        │
```

**Param rejected at runtime**

```
│ temperature 0.2 · effort high · max 2048                         │
│ you: how does include_router work?                               │
│ ⚠ The endpoint rejected reasoning_effort for Qwen/Qwen3-8B (400).│
│   It will not be sent for this model in this session; send your  │
│   question again.                    [Open Connection]           │
── dialog afterwards ──
│  Reasoning effort [high   ▾] (disabled)                          │
│    rejected by the endpoint (400) · not sent this session        │
│    [Clear rejection]                                             │
```

### 2.7 Parameter registry

| Name | Widget | Range / choices | Where sent | Phase |
|---|---|---|---|---|
| `temperature` | number_input, step 0.05 | [0, 2] | first-class `ChatOpenAI(temperature=)` | 1 |
| `top_p` | number_input, step 0.01 | (0, 1] | first-class | 1 |
| `max_tokens` | number_input | ≥ 1 | first-class; always sent as `max_completion_tokens` | 1 |
| `reasoning_effort` | selectbox, narrowed per model | the 7 values ∩ `supported_efforts`; `none` hidden when mandatory | first-class, top-level on `/chat/completions` (never `reasoning=`, which switches to `/responses`) | 1 |
| `seed` | number_input | [0, 2^63−1] | first-class | 1 |
| `frequency_penalty` | number_input (under "More") | [−2, 2] | first-class | 1 |
| `presence_penalty` | number_input (under "More") | [−2, 2] | first-class | 1 |
| `verbosity` | selectbox | low / medium / high | first-class, but gateway behaviour on Chat Completions unverified (21 of 436 models list it) | later |
| `reasoning_enabled` | toggle | bool | `extra_body.reasoning.enabled`, built by the factory | 2 (O-P3) |
| `top_k`, `min_p`, `repetition_penalty` | number_input | [0,1000] / [0,1] / (0,2] | `extra_body`, built by the factory from typed fields | 2 (O-P3) |
| `stop` | — | — | refused; it can truncate the ReAct agent's tool calls | never |
| `timeout`, `max_retries` | — | — | refused (O1) | never (O1) |

### 2.8 Eval binding and arm identity

1. **Source.** Params come only from the arm's inline `harness.llm.params`. The environment tier stays sealed and the dialog tier empty.
   - The binding **refuses** a non-empty `params` resolved from a `pydocs_config` file:
     `ask_your_docs.llm.params from pydocs_config <path> are not arm identity; declare them under the arm's harness.llm.params (found: temperature, top_p)`.
   - The resolved file block already includes the `PYDOCS_ASK_YOUR_DOCS__LLM__PARAMS__*` env layer, so this one rule seals the env path too. The refusal names the keys, never the values.
2. **Raise, don't drop.** The binding uses `on_unsupported="raise"`, checked against the model-family table plus client-drop detection, before any spend. Example:
   `arm harness.llm.params.temperature=0.2 is not honoured for model 'gpt-5-mini' (the client drops it unless reasoning_effort is 'none'); remove it or change the model`.
   - The binding never fetches listings, so the arm's behaviour is a pure function of the arm file.
   - Models with no support report send the params as configured. A 400 fails the rollout loudly and is never learned.
3. **Identity.** `to_canonical()` hashes `settings` whole, so the params are part of identity with no code change.
   - Arms without the key keep byte-identical hashes, including the no-block control arm (AC-27).
   - Documented caveat: `1` and `1.0`, and a key written as `null` versus left out, hash differently. Leave keys out rather than writing `null`; do not canonicalise (that would move every existing hash).
4. **Rollout record.** Each record carries `EffectiveChatParams.sent` as names and values.
5. **Reformulation.** The temperature-0 pin is deterministic from the arm alone; it has no dependency on metadata.
6. The typed builder `build_ask_harness_runner` gets a `chat_params` argument as a later follow-up.

### 2.9 Implementation plan

Each step is its own commit.

1. **Prep refactors** (behaviour unchanged, byte-identical).
   - Move `run_connection_test` out of `llm_connection.py` into `connection_test.py`.
   - Move the page connection actions out of `app.py`.
   - Move `connection_block_for_binding`, `_llm_block_from_config_file` and the env warning into `binding_llm_block.py`.
   - Trim 2 lines from `ask_your_docs_models.py`, or the owner raises its cap (O-P9).
2. **Config.** `ask_your_docs_params_models.py` (~120 lines) and the `params` field. Raise the `langchain-openai` floor to the first release that has typed `reasoning_effort` (verify the version in the changelog), then relock with `~/.local/bin/uv`.
3. **Core logic.** `chat_params.py` (~220 lines):
   - types: `ChatModelParams`, `ModelParamSupport`, `SupportSource`;
   - functions: `support_from_listing_entry`, `support_from_model_name`, `fold_params`, `effective_chat_params(mode)`, `as_chat_openai_kwargs`, `client_dropped_params`, `rejected_param_from_error`, `reformulation_overrides`.
   - Also promote `REASONING_MODEL_PREFIXES` to a public name.
4. **Factory and wiring.**
   - `build_chat_model(params=)`, `LlmConnection.chat_params`, `ConnectionOverride.params`.
   - `agent.py`: the main model gets params; the vision model passes an explicit empty set.
   - `reformulation.py`: the bind rule.
   - `app.py`: `connection_key`, and the rejection catch at the page boundary.
5. **Listing.** `ModelListing.param_support`, parsed in `model_listing.py`.
6. **Dialog.** `params_dialog.py` (~180 lines). Rendered between the model picker and the override build; Test connection sends the candidate's params.
7. **Binding.** File/env refusal, raise mode, rollout record.
8. **Docs and budgets.** Template in `default_config.yaml`, example README, `serve_cpu_openvino.yaml`; `test_module_line_budgets.py` entries for the new modules.

### 2.10 TDD list

Every test is headless and uses named fakes: `FakeModelsEndpoint` entries with `supported_parameters`, `default_parameters` and `reasoning`, plus a transport that records requests.

**Config**
1. An empty block dumps nothing; default parity holds.
2. Range edges are accepted and every rejection is caught, parametrised from `CHAT_PARAM_SPECS`. The message keeps the offending value after `AppConfig.load` redaction.
3. `bool`, numeric strings, NaN and inf are rejected; an int is accepted for a float field.
4. An unknown key lists the allowed set; each refused name gets its O1 message.
5. The env var `PARAMS__TEMPERATURE` layers in.
6. The registry's names equal the model's fields.

**Support**
7. `max_completion_tokens` in the listing counts as `max_tokens` support.
8. A model listing only `reasoning` gets effort disabled. Fixture: `qwen/qwen3.8-flash`; `claude-sonnet-5` is a negative control that lists `reasoning_effort`.
9. Efforts are intersected with the model's list; `mandatory` hides `none`; a value outside the list is dropped, not coerced.
10. Name table: gpt-5 sampling params are marked unsupported unless effort is `none`; gpt-4o effort is marked unsupported; the longest prefix wins.
11. Malformed metadata counts as unreported, writes one log line, and the ids are still listed.

**Effective params and the wire**
12. No params gives exactly `{model, base_url}` (AC-19).
13. Set keys only; never `reasoning`, `extra_body` or `model_kwargs`.
14. `max_tokens` goes on the wire as `max_completion_tokens`.
15. `reasoning_effort` stays top-level on `/chat/completions`.
16. Negative pin: `reasoning=` hits `/responses`.
17. `client_dropped_params` flags a gpt-5 temperature of 0.2, and does not flag it when effort is `none`.
18. Drop mode records reasons; raise mode's message carries the param, value and model.
19. `EffectiveChatParams` is hashable and stable.

**Call sites**
20. The main-model spy receives the params.
21. The vision spy receives none.
22. The probe call shape and its cache key are unchanged.
23. Test connection sends the candidate's params.
24. Reformulation binds `temperature=0` only when a temperature is sent and not dropped.
25. `rejected_param_from_error` handles `.param`, the fallback match, and an unrelated 400 (returns None).

**Page (AppTest)**
26. Rows pre-fill from YAML.
27. A gpt-5.6-sol-like entry disables temperature with the "will not be sent" caption.
28. An unreported endpoint shows the unverified banner.
29. The effort options match the model.
30. Reset and Revert work.
31. Apply stores the snapshot (assert right after Apply, then check on a fresh page).
32. The starvation warning appears.
33. A 400 with `param=reasoning_effort` gives the caption, the next build omits the key, and Clear rejection works.
34. Two sessions with different params get two agents.

**Binding**
35. Arm params reach `build_chat_model`.
36. A known-unsupported or client-dropped param raises before spend.
37. File-sourced params are refused, and so are env-sourced params (the message names the keys, never the values).
38. The no-block arm is byte-identical; adding a param moves the hash; an arm without the key keeps its old hash.
39. The rollout record carries the sent params.

**Budgets**
40. New modules are listed in `test_module_line_budgets.py`; `binding.py`, `app.py` and `llm_connection.py` stay under 500.

## 3. Open questions for the owner

| # | Question | Recommendation |
|---|---|---|
| O-P1 | Key name: `params` or `parameters`? | `params` |
| O-P2 | Phase 1 field set: 5 fields, or 7 (with the penalties)? | 7 — both penalties are first-class fields and widely listed (240 and 233 models) |
| **O-P3** | A **departure from O2's current answer; needs ratification.** Add a typed phase 2 that the factory routes into `extra_body` from validated fields — `reasoning_enabled` (thinking off: 115 models offer no `none` effort), plus `top_k`, `min_p`, `repetition_penalty` — never a raw passthrough. O2 says to "revisit when a provider needs a per-model body field"; qwen thinking-off is that case. | Ratify phase 2 as a separate change; phase 1 ships without it |
| O-P4 | Typed params for the separate vision model (`vision.params`)? | Not now; the vision model gets none, structurally |
| O-P5 | Reformulation: pin temperature to 0 (only when a temperature is sent), or inherit everything? Should effort be lowered for the rewrite? | Pin temperature; inherit effort, because lowering it would depend on metadata and break eval determinism |
| O-P6 | `max_completion_tokens` on OpenRouter without `require_parameters`, and on Ollama and llama.cpp, is **unverified**. | Owner-gated tiny paid check (<$0.001) plus a local Ollama check; routing `max_tokens` through the request body is only a phase-2 fallback |
| O-P7 | File- and env-sourced params in eval: refuse (no hashes move) or fold a hash of the resolved block into identity (hashes move)? | Refuse |
| O-P8 | Launcher flags for params? | None |
| O-P9 | `ask_your_docs_models.py` is at 199 of <200 lines: trim 2 lines or raise the cap to 210? | Trim |
| O-P10 | Raise the `langchain-openai` floor, currently `>=0.2`? | Yes, to the first version with typed `reasoning_effort` (verify the exact version) |
| O1 | Unchanged: params refuse `timeout`/`max_retries`, and the message says "in this iteration", matching O1's "add later if needed". | — |

Files are in `/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/ayd-params/`:
- `mockup_spec.json` — the 6 dialog states as data for the HTML mockup
- `or_models.json` — the OpenRouter listing the counts come from
- `probe_v2.log` — the paid probe results