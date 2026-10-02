# ask-your-docs model settings, v2 (final): one Thinking switch and two numbers, hidden per provider

**What the user sees, in one sentence.** Below the model picker there is a **Thinking** switch plus **Temperature** and **Max output tokens** fields (blank means the model's default), with Top p and Seed under a collapsed **More**. Any control the chosen model or endpoint cannot honour is hidden entirely: no disabled rows, no explanatory captions.

The owner rules of 2026-09-10 are binding here: MASK, keep it simple, ONE Thinking control that maps per provider, and P1–P6 accepted. Under P2, the typed, factory-built `extra_body` is a separate, later change.

---

## 0. What I verified (origin/main 7c2d7ce, vendored sources in `ayd-params-v2/src/`, the OpenRouter listing)

**Line budgets.** The new logic has to live in new modules.

| File | Lines | Cap |
|---|---|---|
| `binding.py` | 499 | 500 |
| `app.py` | 497 | 500 |
| `llm_connection.py` | 492 | 500 |
| `ask_your_docs_models.py` | 199 | 200 |
| `connection_dialog.py` | 274 | 500 |
| `model_listing.py` | 370 | 500 |

**Code facts.**
- `connection_key` is at `app.py:149`. The family table to reuse is `_REASONING_MODEL_PREFIXES = ("gpt-5","o1","o3","o4")` at `retrieval/llm_clients/openai.py:48`. The arm hash is built by `arms.py:to_canonical` (settings hashed whole) and `fingerprint(...)`, which already folds resolved hashes (guidance, delivery map, rubric), so there is a precedent for a resolved-wire fingerprint.
- Dependency floors are `streamlit>=1.43`, which has `st.segmented_control`, and `langchain-openai>=0.2`. The lock has 1.1.9, and typed `reasoning_effort` first appears in 0.2.14.

**vLLM** (`vllm_chat_protocol.py:245, 594-598`).
- `reasoning_effort` accepts `none|minimal|low|medium|high|xhigh|max`.
- When the user has not set `enable_thinking`, vLLM injects `enable_thinking = (effort != "none")` into the chat-template kwargs. The template side of "Off" is therefore verified.
- The Qwen3 reasoning parser reads `chat_template_kwargs.enable_thinking` from the request (`qwen3_parser.py:229-230`). Whether it sees the injected value has not been verified (check C1).
- Older vLLM without the injection may reject `none` with a 400, which the learned-rejection backstop catches.

**Ollama** (`ollama_openai.go:536-555, 669, 706-718`).
- `reasoning_effort: none` maps to `think:false`, `minimal` to `low`, and `xhigh` to `max`. It also accepts OpenRouter's `reasoning.effort`.
- It reads **only `max_tokens`**, while LangChain always sends `max_completion_tokens`. The output cap is therefore silently ignored.
- `owned_by` is the model namespace: `"library"` for official models.

**llama.cpp** (`server_common.cpp:1314-1329`, `server-task.cpp:68,122`, `server-context.cpp:4552`).
- `reasoning_effort: "none"` sets `enable_thinking=false`.
- The cap is read from `max_tokens`/`n_predict` only, so it is ignored as with Ollama.
- `owned_by` is `"llamacpp"`.

**LiteLLM** (probe log, `litellm-1.100.1/litellm/types/router.py:648`).
- `/model_group/info` rows carry `supported_openai_params` and `supported_reasoning_efforts: tuple | None`.
- For an `openai/`-prefixed deployment, `reasoning_effort` gives a 400 `UnsupportedParamsError`.
- For `anthropic/…`, `reasoning_effort` becomes `thinking.budget_tokens`, and `temperature=0.2` passes through alongside thinking. Anthropic itself refuses that combination.
- For `gpt-5-mini`, `temperature=0.2` gives a 400 while reasoning is active.

**OpenRouter** (436 models: 305 list `reasoning`, 167 list `reasoning_effort`).

| Model | Temperature | Reasoning metadata | Notes |
|---|---|---|---|
| `qwen/qwen3.8-27b` | yes | efforts `[xhigh, medium, low]`, mandatory no | defaults: temperature 1, top_p 0.95 |
| `qwen/qwen3.8-flash` | yes | `reasoning` only, no `reasoning_effort` | |
| `openai/gpt-5-mini` | no | mandatory, efforts `[high, medium, low, minimal]` | |
| `anthropic/claude-sonnet-5` | no | lists `reasoning_effort` | |

**Assumption.** I could not find P1–P6 defined in any file I read (the v1 proposal uses O-P1..O-P10; the two v2 drafts use R-numbers). I apply the brief's reading: P2 is the typed `extra_body` phase, ratified as a separate later change. The rest of this design is compatible with the v1 recommendations O-P1 (`params`), O-P4 (the vision model gets none), O-P5 (pin reformulation temperature), O-P7 (refuse file- or env-sourced params in eval), O-P9 (trim) and O-P10 (floor bump). If P1–P6 meant something else, D0 in §9 asks for the mapping.

---

## 1. Judgement of the two redesigns

| Criterion (1–10) | presets-first | compact-raw |
|---|---|---|
| Simplicity for a first-time user | **9**: three plain-language segmented rows | **6**: raw names, a 2×2 grid, a 5–7-value `reasoning_effort` dropdown including `minimal`/`xhigh`/`max` |
| Correctness across the 5 profiles | **7**: sound profile ladder and hide-only rule, but the headroom heuristic is a guess, "Creative = 1.0" is arbitrary, and Ollama/llama.cpp cap loss is only half handled | **8**: per-profile support sources, LiteLLM `None` vs `()`, the gpt-5.1 effort-none rule, and a LiteLLM proxy in front of vLLM detected correctly |
| Honesty when a control can't be honoured | **5**: the "This model doesn't offer Off or High" sentence and page-start drop captions break the new MASK rule; the "On = medium" relabel is hidden | **7**: hides rows, but "not sent for gpt-5-mini: temperature=0.2" and "hidden after a rejection: x" are captions for masked params; its generic caption is fine |
| Eval-arm safety | **8**: arm-only, host-only wire profile, raise before spend, preset-table version recorded; but preset numbers become measured arm values | **8**: arm-only, host-only, raise before spend, wire recorded; no preset table to freeze |
| Implementation size and risk | **6**: about 550 lines, with a second vocabulary (`style`/`length` vs raw), mutual-exclusion validators and a frozen preset table | **7**: about 540 lines, one vocabulary, `param_bounds()` instead of a registry |
| Consistency with the activity panel | **8**: `thinking_off` hint, never suppresses reasoning text | **9**: the panel's subclass receives the params intact, never `exclude`, display hints |
| **Total** | **43** | **45** |

**Synthesis.** I keep compact-raw's engine:
- five profiles;
- the invariant that detection may only *hide* and never *adds or re-routes*;
- a wire profile (declared or host) that is kept separate from the display profile;
- one real-unit vocabulary in the YAML;
- arm-only, host-only eval.

I graft presets-first's face onto it:
- **one plain-language Thinking segmented control**, which is the owner's "ONE Thinking control that maps per provider";
- no badges;
- the refused-key messages and the YAML `off` trap guard.

Both designs' captions are removed to satisfy MASK. The only honesty channels left are:
- the existing status line under the model picker;
- the Test-connection result line, which lists exactly what was sent;
- the JSON log;
- in eval, raising before any spend.

I drop the Style and Answer-length presets (D1). They would add a frozen preset table that becomes part of arm identity, a second vocabulary with mutual exclusions, and a headroom heuristic. Two number fields whose blank value means "model default" are just as easy to ignore for a first-time user. Reasoning-token starvation is handled by detecting it at runtime instead of guessing headroom (§5).

---

## 2. Provider profiles and detection

YAML key: `ask_your_docs.llm.provider: auto | openai | openrouter | vllm | litellm | generic`. The default is `auto`.

```
1. declared provider (≠ auto)                               → that profile   ┐ WIRE profile: pure, network-free,
2. host openrouter.ai                                       → openrouter     │ used by eval and by the phase-2
3. host api.openai.com, or base_url null (vendor default)   → openai         ┘ extra_body routes; else generic
4. cached /models entries: owned_by == "vllm"               → vllm           ┐
5. GET <base_url minus trailing /v1>/model_group/info                          │ DISPLAY profile: dialog + page only;
   → 200 with data[].model_group                            → litellm        │ decides which controls are hidden
6. otherwise                                                → generic        ┘
   generic sub-flavour from owned_by: "llamacpp" | "library" (Ollama) → cap-ignoring server
```

**Wire profile versus display profile.**
- In phase 1 every profile produces the **same wire** for the same settings (§3), so the display profile only ever hides controls.
- Only the wire profile may select a phase-2 `extra_body` route. The app and eval therefore send identical bytes for the same YAML.
- A vLLM user who wants the phase-2 route writes `provider: vllm`, one line.

**The LiteLLM probe.**
- It reuses `_listing_client`: same bearer and auth decision, a 10 s timeout, 1 bounded retry, and the listing's TTL cache keyed by `(base_url, auth identity)`.
- It runs only when steps 1–4 are undecided, and only from the dialog: never at page start, never in eval.
- On failure the profile is generic, and one JSON log line `{"event":"litellm_probe_failed","status":…}` is written. Keys and bodies are never logged.

**Why `owned_by` is safe for vLLM.** vLLM hard-codes `owned_by: "vllm"`, and LiteLLM rewrites it. A LiteLLM proxy in front of vLLM therefore detects as litellm, which is correct: the gateway's rules apply.

---

## 3. Controls and the per-profile mapping

### 3.1 The control set

| Control | Widget | Values | YAML key | Wire (every profile, phase 1) |
|---|---|---|---|---|
| **Thinking** | `st.segmented_control` | Auto · Off · Low · Medium · High; on/off families show Auto · Off · **On** | `thinking` | Auto sends nothing; otherwise `reasoning_effort=<value>` (Off sends `"none"`, On sends `"medium"`) |
| **Temperature** | `st.number_input(value=None, placeholder="model default")`, step 0.05, 0–2 | blank or number | `temperature` | `temperature` |
| **Max output tokens** | `st.number_input(value=None)`, min 1, max = known ceiling | blank or int | `max_tokens` | `max_tokens` (LangChain always sends `max_completion_tokens`) |
| More → Top p | number_input, (0, 1] | blank or number | `top_p` | `top_p` |
| More → Seed | number_input, [0, 2^63−1] | blank or int | `seed` | `seed` |

- **On/off families.** "On" is a display label for the stored value `medium`, so a label never creates a second arm hash for the same wire. It applies to families that only switch thinking on or off, such as Qwen3 on vLLM.
- **Never offered:** `minimal`, `xhigh` and `max` efforts, `verbosity`, the penalties (D2), and `stop` (it can truncate the ReAct agent's tool calls).
- **Placeholders.** They show a known default: OpenRouter `default_parameters`, or the ceiling from `max_model_len` / `max_output_tokens`. Otherwise they read "model default". A placeholder is never sent.

### 3.2 Thinking: one control, mapped per provider

| Choice | openai | openrouter | vllm | litellm | generic (Ollama, llama.cpp, other) |
|---|---|---|---|---|---|
| Auto | nothing | nothing | nothing | nothing | nothing |
| Off | `reasoning_effort="none"` | **P1:** `reasoning_effort="none"`. **P2:** `extra_body.reasoning={"enabled":false}` *instead of* top-level effort | **P1:** `reasoning_effort="none"`, which auto-injects `enable_thinking=false` (C1). **P2 (declared vllm):** adds `extra_body.chat_template_kwargs={"enable_thinking":false}` | `reasoning_effort="none"` (LiteLLM translates it per backend) | `reasoning_effort="none"` (Ollama → `think:false`; llama.cpp → `enable_thinking=false`) |
| Low / Medium / High (On = Medium) | `reasoning_effort=x` | **P1:** `reasoning_effort=x`. **P2:** `extra_body.reasoning={"effort":x}` *instead of* top-level effort | `reasoning_effort=x` (auto `enable_thinking=true`) | `reasoning_effort=x` (→ Anthropic `thinking.budget_tokens`, Gemini `thinkingConfig`) | `reasoning_effort=x` |

**When each option is shown.** An option that is not shown is hidden entirely.

| Profile | Thinking options shown | Source |
|---|---|---|
| openai | Family table:<br>• gpt-5 (original): Auto/Low/Medium/High (no `none`)<br>• gpt-5.1+: Auto/Off/Low/Medium/High<br>• o1/o3/o4: Auto/Low/Medium/High<br>• gpt-4o/gpt-4.1: control hidden | static table, longest prefix wins |
| openrouter | Only if `reasoning_effort` is listed (P1) or `reasoning` is listed (P2).<br>Off: P1 only when `none` ∈ `supported_efforts` and not `mandatory`; P2 whenever not `mandatory`.<br>Low/Med/High: intersected with `supported_efforts` when present.<br>Never coerced (qwen3.8-27b has no `high`, so High is hidden). | listing entry |
| vllm | Family table on the model id: qwen3 → Auto/Off/On; gpt-oss → Auto/Low/Medium/High (Harmony gives a 400 on `none`); otherwise all five | static table |
| litellm | Hidden unless `reasoning_effort` ∈ `supported_openai_params`. The options come from `supported_reasoning_efforts`: `None` = unknown, show all five; `()` = hide the control; a tuple = intersect | `/model_group/info` |
| generic | All five | nothing known |
| all | Hidden for `(base_url, model)` this session after a 400 naming `reasoning_effort` | learned rejection |

The family table matches the model id's **last path segment**, so `openai/gpt-5-mini` on OpenRouter or a LiteLLM model id hits the same row. It holds about 8 rows and reuses the promoted `REASONING_MODEL_PREFIXES`.

### 3.3 The other controls: when each is shown

| Control | openai | openrouter | vllm | litellm | generic |
|---|---|---|---|---|---|
| Temperature | gpt-4o/4.1: yes. gpt-5.1+: **only while Thinking = Off** (mirrors LangChain's `validate_temperature`). gpt-5/o-series: hidden | `temperature` listed | yes | `temperature` listed; if `providers` includes anthropic, only while Thinking is Auto or Off | yes |
| Max output tokens | yes | `max_tokens` or `max_completion_tokens` listed (C2) | yes; max = `max_model_len` | a max-token param listed; max = `max_output_tokens` | yes, **hidden on llamacpp / Ollama `library`** (they ignore `max_completion_tokens`) |
| Top p | same rule as Temperature | `top_p` listed | yes | `top_p` listed (same anthropic rule) | yes |
| Seed | yes | `seed` listed | yes | `seed` listed | yes |

A learned rejection (a 400 naming a param that was sent) hides that control for `(base_url, model)` for the rest of the session, on every profile.

**Rejection parser.** First match wins:
1. `BadRequestError.param`;
2. LiteLLM's `does not support parameters: ['x']` or `doesn't support x=`;
3. a quoted name.

It matches only names that were actually sent, and it never retries automatically.

---

## 4. YAML schema

This is a new module, `retrieval/config/ask_your_docs_params_models.py`. It holds `ProviderName` (Literal), `ThinkingLevel` (StrEnum) and `ChatParamsConfig(extra="forbid", frozen=True)`. Ranges live only in pydantic `Field(ge/gt/le)`, and the widgets read the same bounds through `param_bounds(name)`.

```yaml
ask_your_docs:
  llm:
    base_url: http://localhost:8000/v1
    model: Qwen/Qwen3-8B
    provider: auto          # auto|openai|openrouter|vllm|litellm|generic  (omit = auto)
    params:                 # every key optional; absent = not sent (model default)
      thinking: off         # auto|off|low|medium|high
      temperature: 0.6      # [0, 2]
      max_tokens: 4096      # int >= 1; on the wire: max_completion_tokens
      top_p: 0.95           # (0, 1]
      seed: 7               # int [0, 2**63-1]
```

**Validation.** Messages carry the value and the expected shape. Values are not secrets, and `error_redaction` would otherwise blank them. The validator:
- rejects `bool` in numeric fields, numeric strings, NaN and ±inf, and non-integer `max_tokens`/`seed`;
- handles the YAML 1.1 trap: a bare `thinking: off` loads as `False`. A before-validator maps `False` to `"off"` and rejects `True` with "thinking: true is ambiguous; use low, medium or high";
- rejects unknown keys and lists the allowed set, echoing the key but never the value.

**Refused keys**, each with its own message:

| Key | Message |
|---|---|
| `timeout`, `max_retries` | not configurable in this iteration (O1) |
| `extra_body`, `model_kwargs`, `reasoning`, `chat_template_kwargs` | never raw; the factory builds any request body from `thinking` (O2/P2) |
| `reasoning_effort` | "use params.thinking" |
| `max_completion_tokens` | "use params.max_tokens" |
| `frequency_penalty`, `presence_penalty`, `stop`, `verbosity`, `top_k`, `min_p`, `repetition_penalty` | "not configurable" |

**Precedence** (unchanged from v1):
1. YAML;
2. the existing `PYDOCS_ASK_YOUR_DOCS__LLM__PARAMS__*` env layer;
3. the dialog, which stores a **complete snapshot** that replaces the YAML set: `ConnectionOverride.params: ChatParamsConfig | None = None`, added as the **last** field.

There are no launcher flags, and the harness env (`OPENAI_BASE_URL`, `LLM_MODEL`) carries no params. `default_config.yaml` keeps `llm: null` plus a commented template.

---

## 5. Dialog behaviour

**What is on screen.**
- The section sits between `_render_model_picker` and `ConnectionOverride(...)` in `connection_dialog.py`.
- Visible: at most 3 controls, plus a collapsed **More** holding at most 2 fields and an optional button.
- Nothing is rendered for a hidden control.
- The existing status line gains one word, the provider, and for generic "settings unverified". That is the only standing honesty text.

**Rules.**
1. **Pre-fill.** Controls are pre-filled from the effective snapshot. A blank field or Auto means "not sent".
2. **A saved value for a hidden control** is not sent. There is no caption, per MASK. Instead:
   - the Test result line shows exactly what is sent;
   - one JSON log line records the drop: `{"event":"chat_params_effective","sent":[…],"not_sent":[…]}`;
   - in eval the same situation raises (§6).
3. **Changing the model** keeps the snapshot. The hide rules decide what is sent.
4. **Test connection sends exactly what Apply would send** and reports it: `test passed · sent reasoning_effort=low, temperature=0.2, max_completion_tokens=4096`.
5. **Runtime 400 naming a sent field.** The turn fails with one chat message: "The endpoint rejected Thinking for Qwen/Qwen3-8B, so it's off for this session. Send your question again." The control is hidden for `(base_url, model)`, and **More** shows a `Restore hidden settings (1)` button (a button, not a caption). There is no automatic retry.
6. **Starvation.** A turn that ends with `finish_reason == "length"` and empty content while Thinking is not Off shows one chat message: "The reply ran out of tokens while thinking. Raise Max output tokens or turn Thinking down." Test connection reports the same. This replaces v1's `<1024` threshold warning and presets-first's headroom table.
7. **Where params are sent.**
   - Main model: yes.
   - Test connection: yes.
   - Same-model image description: yes (shared object).
   - Reformulation: inherits, with `.bind(temperature=0)` only when a temperature is sent and not client-dropped (O-P5).
   - Separate vision model, image probe, `/models` and `/model_group/info`: **never**.
8. **Agent cache.** `connection_key` gains the hashable `WireParams`, so two sessions with different settings build two agents.

### 5.1 ASCII: the five endpoints

**A. OpenRouter · qwen/qwen3.8-27b** (efforts `xhigh/medium/low`, not mandatory)
```
+ Connection ----------------------------------------------------+
| Base URL  [https://openrouter.ai/api/v1                      ] |
| Auth      $OPENROUTER_API_KEY set                              |
| Model     [qwen/qwen3.8-27b                            v] [R]  |
| 436 models listed · vision: no (listing) · OpenRouter          |
|                                                                |
| Thinking            Auto  [Low]  Medium                        |
| Temperature         [0.2          ]                            |
| Max output tokens   [4096         ]                            |
| > More                                                         |
|                                                                |
| [Test connection]  test passed · sent reasoning_effort=low,    |
|                    temperature=0.2, max_completion_tokens=4096 |
|                                                     [Apply]    |
+----------------------------------------------------------------+
  after P2: Thinking gains Off (sent as reasoning:{enabled:false});
  qwen/qwen3.8-flash (reasoning-only) gains the Thinking row at all.
```

**B. OpenAI · gpt-5-mini** (family gpt-5: no `none`, sampling hidden)
```
| Model     [gpt-5-mini                                  v] [R]  |
| 94 models listed · vision: yes (table) · OpenAI                |
|                                                                |
| Thinking           [Auto]  Low  Medium  High                   |
| Max output tokens   [             ]                            |
| > More                                                         |
  More holds Seed only. A YAML temperature: 0.2 is not sent (logged; the Test
  line shows "sent reasoning_effort=…"). On gpt-5.1, choosing Off makes the
  Temperature row appear.
```

**C. Local vLLM · Qwen/Qwen3-8B** (display profile vllm via `owned_by`; `max_model_len` 40960)
```
| Base URL  [http://localhost:8000/v1                          ] |
| Auth      no auth                                              |
| Model     [Qwen/Qwen3-8B                               v] [R]  |
| 1 model listed · vision: no (table) · vLLM                     |
|                                                                |
| Thinking            Auto  [Off]  On                            |
| Temperature         [model default]                            |
| Max output tokens   [1024         ]   (max 40960)              |
| > More                                                         |
  sends: reasoning_effort=none, max_completion_tokens=1024
  (declared provider: vllm + P2 also sends chat_template_kwargs.enable_thinking=false)
```

**D. LiteLLM proxy · team-sonnet** (`/model_group/info`: providers `[anthropic]`, no seed, efforts unknown)
```
| Base URL  [https://llm.corp.example/v1                       ] |
| Model     [team-sonnet                                 v] [R]  |
| 12 models listed · vision: yes (gateway) · LiteLLM             |
|                                                                |
| Thinking            Auto  Off  Low  [Medium]  High             |
| Max output tokens   [8192         ]   (max 64000)              |
| > More                                                         |
  Temperature/Top p hidden while Thinking is Low/Medium/High (Anthropic refuses
  sampling with thinking); they reappear on Auto/Off. More is empty → not rendered.
  openai/-prefixed deployment: the Thinking row is absent.
```

**E. Unknown endpoint**
```
| Model     [acme-chat-7b                                v] [R]  |
| 3 models listed · vision: ? · provider unknown — settings unverified |
|                                                                |
| Thinking           [Auto]  Off  Low  Medium  High              |
| Temperature         [model default]                            |
| Max output tokens   [model default]                            |
| > More                                                         |
  Ollama (owned_by "library") / llama.cpp ("llamacpp"): same, minus Max output tokens.
```

**More opened** (generic)
```
| v More                                                         |
|   Top p   [model default]      Seed  [model default]           |
|   [Use YAML settings]   [Restore hidden settings (1)]          |
```
The second button appears only after a learned rejection.

**Runtime rejection, in chat**
```
| ! The endpoint rejected Thinking for Qwen/Qwen3-8B (400), so it's |
|   off for this session. Send your question again.              |
```

---

## 6. Eval rules

1. **Source.** Params come only from the arm's `harness.llm.params` and `harness.llm.provider`.
   - Params resolved from a `pydocs_config` file are refused. That file block already includes the env layer, so this one rule seals the env path too.
   - The message names the keys, never the values.
2. **Determinism.** The wire profile is the declared provider or the host only. `auto` resolves by host, then generic. The binding never fetches a listing or probes, and `resolve_wire(params, wire_profile, model)` is pure: params, model id and frozen tables in, wire out.
3. **Hidden means raise before spend.** Where the app would hide a control or option, the binding **raises**. It checks the static tables only: the family table, which also applies to OpenRouter/LiteLLM ids by last segment, plus LangChain's client drop. For example: `arm harness.llm.params.thinking='off' is not honoured by model 'gpt-5-mini' (no 'none' effort); remove it or change the model`. Unknown support is sent as configured, and a 400 fails the rollout loudly and is never learned.
4. **Identity.** `to_canonical` hashes the settings whole, so `provider` and `params` are arm identity. Arms without the keys keep byte-identical hashes. **D3:** also fold a resolved-wire fingerprint into `fingerprint()` when `params` is non-empty, following the precedent of the resolved hashes it already takes. Then a future mapping change moves only the affected arms' identity:
   - P2 changes the wire for `openrouter` and declared `vllm`;
   - `THINKING_MAP_VERSION` goes 1 → 2.
5. **Rollout record.** It carries `{"provider": <wire profile>, "sent": {…wire incl. extra_body…}, "thinking_map": N}`.
6. **LiteLLM `drop_params`** can silently remove arm values, and a client-only binding cannot see that. This is documented as the operator's responsibility; the record carries the profile.

---

## 7. Consistency with the activity panel

- **One construction site.** `build_chat_model(connection, bearer, *, wire=NO_WIRE_PARAMS)`. The panel's `reasoning_chat_model_class(ChatOpenAI)` decides the class, and these kwargs are its constructor arguments. A pin test checks that the subclass receives `reasoning_effort` and (phase 2) `extra_body` unchanged. With no params, the AC-19 spy still sees exactly `{model, base_url}`.
- **The panel's extractor stays profile-independent**: `reasoning_content`, then `reasoning`, then `reasoning_details`, with the opt-in `<think>` splitter. This matches every profile:

  | Provider | Where reasoning arrives |
  |---|---|
  | LiteLLM, vLLM 0.11, llama.cpp | `reasoning_content` |
  | OpenRouter, vLLM ≥0.16, Ollama | `reasoning` |
  | OpenAI Chat Completions | not exposed (the token count only) |

- **Reasoning text is never suppressed.** Neither phase sends OpenRouter `exclude`, `include_reasoning:false`, `drop_params` or `allowed_openai_params`, and phase 2 never uses `ChatOpenAI(reasoning=)`, which switches to `/responses`.
- **One link between the two designs.** `WireParams.thinking_off` lets the panel's sidebar say "Reasoning: off (your setting)" immediately instead of waiting for its two-turn "not shared" rule.

---

## 8. What changed versus v1, and why

| v1 | v2 final | Why |
|---|---|---|
| 7 raw rows, origin and support badges, disabled rows with captions, an "unverified" banner | 3 visible controls + 2 under More; hidden controls are absent; honesty lives in the status line, the Test line and the log | owner rules "simpler" and MASK |
| 7-value `reasoning_effort` dropdown | one **Thinking** control (≤5 options, "On" for on/off families), mapped per provider | owner: one Thinking control mapped per provider |
| OpenRouter metadata as the only rich source | 5 profiles: OpenRouter listing, LiteLLM `/model_group/info`, OpenAI and vLLM family tables, generic; plus Ollama/llama.cpp cap masking | owner: not only OpenRouter; vLLM, OpenAI and LiteLLM too |
| penalties under "More"; "also reported, not configurable" footer | dropped | simplicity; YAGNI (D2) |
| `<1024` starvation warning | runtime detection of `finish_reason=length` with empty content | concrete, not a heuristic, and not a standing caption |
| `extra_body` as an open O-P3 question | P2 route table fixed now, shipped later, wire-profile-only | owner ratified P2 as a separate change |
| identity = settings hash | + resolved-wire fingerprint when params are set (D3) | a mapping change must not hide inside an unchanged hash |
| `reasoning_effort`/`max_completion_tokens` accepted as keys | refused, pointing to `thinking`/`max_tokens` | one vocabulary |

---

## 9. Implementation plan (one commit each, TDD)

0. **Move-only prep** (byte-identical): `connection_test.py` ← `run_connection_test`; the page connection actions move out of `app.py`; `binding_llm_block.py` ← `connection_block_for_binding` + `_llm_block_from_config_file` + the env warning; trim `ask_your_docs_models.py` by 3 lines.
1. **Config** — `retrieval/config/ask_your_docs_params_models.py` (~80 lines) + `LlmConnectionConfig.provider`/`.params`. Raise the floor to `langchain-openai>=0.2.14` and relock with `~/.local/bin/uv`.
2. **`harness/ask_your_docs/provider_profiles.py`** (~120): `ProviderProfile`; `wire_profile(declared, base_url)` (pure); `display_profile(wire, listing, group_info)`; `FAMILY_TABLE` (promote `REASONING_MODEL_PREFIXES`); `THINKING_MAP_VERSION = 1`.
3. **`harness/ask_your_docs/control_support.py`** (~130): `ControlSupport` (frozen: thinking options, the 4 visibility bools, `max_tokens_ceiling`); `support_for(...)`; `openrouter_support`, `litellm_support`, `family_support`; `rejected_control_from_error(exc, sent)`.
4. **`harness/ask_your_docs/chat_wire.py`** (~90): `WireParams` (hashable: `first_class`, `extra_body=None` in phase 1, `thinking_off`); `resolve_wire(...)`; `unhonoured_by_tables(...)` for eval; `wire_summary()`.
5. **`model_listing.py`** (+~45): `ModelListing.entries_by_id` (last field, empty default); `fetch_litellm_group_info` on `_listing_client` with cache, timeout, 1 retry and a JSON log.
6. **Wiring** (~40):
   - `build_chat_model(wire=)` and `ConnectionOverride.params`;
   - `agent.py`: the main model gets the params, the vision model is passed `NO_WIRE_PARAMS` explicitly;
   - `app.py` / moved actions: `connection_key`, the rejection catch and the starvation message;
   - `reformulation.py`: the bind rule.
7. **`harness/ask_your_docs/model_settings_form.py`** (~100): `render_model_settings(support, snapshot) -> ChatParamsConfig`. `connection_dialog.py` gains about 12 lines.
8. **Binding**: file/env refusal, raise before spend, rollout record, wire fingerprint (D3).
9. **Docs**: `default_config.yaml` template, example README, line-budget entries for the 4 new modules.

**Phase 2 (P2, a separate PR):** `thinking_extra_body(wire_profile, thinking) -> Mapping | None` (~25 lines) + `THINKING_MAP_VERSION = 2` + 4 tests.

**Size:** about 520 new production lines (v1 ~600, presets-first ~550, compact-raw ~540) and about 32 tests (v1 40).

### TDD list (headless; named fakes `FakeModelsEndpoint` with OpenRouter/vLLM/Ollama/llama.cpp entry shapes, `FakeModelGroupInfo`, a request-recording transport)

**Config (5)**
1. An empty block dumps nothing, and default parity holds.
2. Range edges pass, and values survive `AppConfig.load` redaction in messages.
3. `bool`, numeric strings, NaN and inf are rejected; `thinking: off` (YAML False) becomes `"off"`; `true` is rejected.
4. Unknown and refused keys give their messages (O1/O2/"use thinking").
5. The env layer `PARAMS__THINKING` applies.

**Detection (5)**
6. The declared provider beats the host.
7. The host rules.
8. `owned_by` vllm.
9. The LiteLLM probe gives 200 → litellm and 404/timeout → generic plus one log line; it fires only when undecided.
10. A LiteLLM proxy in front of vLLM detects as litellm; `llamacpp`/`library` set the cap-ignoring flavour.

**Support (7)**
11. OpenRouter efforts are intersected and never coerced (qwen3.8-27b hides High).
12. `mandatory` hides Off.
13. A reasoning-only model hides Thinking in phase 1.
14. LiteLLM efforts: `None` shows all, `()` hides, a tuple intersects; the anthropic rule couples Temperature/Top p to Thinking.
15. OpenAI family: gpt-5 has no Off and no Temperature; gpt-5.1 shows Temperature only with Off; gpt-4o hides Thinking; longest prefix wins; the last path segment matches.
16. vLLM: qwen3 gives Auto/Off/On, gpt-oss has no Off, and the ceiling is `max_model_len`.
17. llama.cpp/Ollama hide Max output tokens.

**Wire (6)**
18. No params gives exactly `{model, base_url}` (AC-19).
19. Each Thinking choice maps to `reasoning_effort`, and On sends `medium`.
20. `max_completion_tokens` is on the wire.
21. The phase-1 wire is byte-identical across all 5 profiles.
22. Negative pin: `reasoning=` hits `/responses`; never `extra_body` in phase 1.
23. `WireParams` is hashable, and two sessions build two agents.

**Call sites and page (5)**
24. The main-model spy gets the params; the vision and probe spies get none.
25. Test connection sends Apply's wire and reports it.
26. The reformulation bind rule.
27. AppTest: the rendered controls for states A–E equal `mockup_spec.json`, with no caption text rendered for hidden controls.
28. A 400 hides the control → Restore brings it back; the starvation message.

**Binding (4)**
29. Arm params reach the factory.
30. A table conflict raises before spend.
31. File- and env-sourced params are refused.
32. A no-block arm's hash is byte-identical; params move the hash; the record carries the wire and `thinking_map`.

**P2 (+4)**
- OpenRouter `reasoning` shapes, replacing top-level effort.
- Declared-vLLM `chat_template_kwargs`.
- None for openai/litellm/generic.
- A display-only (live-detected) vllm never gets `extra_body`.

---

## 10. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| D0 | Confirm how P1–P6 map onto this design. I found only P2 defined, via the brief. | Confirm; otherwise the design follows v1 O-P1/4/5/7/9/10 |
| D1 | Drop the Style/Answer-length presets in favour of Temperature + Max output tokens (blank = model default) | **Yes**: one vocabulary, no frozen preset table in arm identity |
| D2 | Drop the frequency/presence penalties (v1 kept them) | **Drop**; re-adding one later is an additive YAML key |
| D3 | Fold a resolved-wire fingerprint into the arm `fingerprint()` when `params` is non-empty | **Yes**; no existing hash moves |
| D4 | **P2 / O2 departure (ratified in principle; confirm the table):** a typed, factory-built `extra_body`, built only from `thinking` + the *wire* profile. Exactly two routes: OpenRouter `reasoning{enabled:false \| effort}` (replaces top-level effort), and declared vLLM `chat_template_kwargs.enable_thinking`. There is no raw passthrough, `extra_body` stays a refused YAML key, O2 stays closed for openai/litellm/generic and for the vision model, and it never sends `exclude`/`include_reasoning` | **Confirm**; ship as its own PR after phase 1 |
| D5 | Check C1: does `reasoning_effort="none"` turn Qwen3 thinking off end to end on the target vLLM (the parser sees the injected kwarg)? | Run locally before vLLM Off ships; if it fails, hide Off on vllm until P2 |
| D6 | Check C2 (tiny paid, <$0.001): does OpenRouter honour `max_completion_tokens` for models that list only `max_tokens`? | Run; if not, hide Max output tokens on those models |
| D7 | A saved value for a hidden control is not sent, with no caption (MASK); honesty is the Test line + log, and eval raises | **Accept** (follows the MASK rule) |
| D8 | "On" for on/off families stores `medium` | **Accept** |
| D9 | Raise the floor to `langchain-openai>=0.2.14` + relock; trim `ask_your_docs_models.py` | **Yes** |
| D10 | LiteLLM `drop_params` can silently remove arm values | Document it as the operator's responsibility |

Unchanged: no MCP tool or param; YAML via `AppConfig` with `extra="forbid"` and single-source defaults; O1; and G8: params are not secrets, the probe reuses the existing auth path, and keys and bodies are never logged.
