# HANDOFF — params-v2 (design only, no code, no commits)

## 2026-09-10 — final v2 synthesis written
Finished (scratch files only; no repo changes, no commits):
- /private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/ayd-params-v2/PROPOSAL.md
  — judgement of presets-first (43/60) vs compact-raw (45/60) + final design: one Thinking segmented
  control mapped per provider + Temperature + Max output tokens (blank = model default), Top p/Seed under More;
  masked per provider (no disabled rows, no captions); 5 profiles (openai/openrouter/vllm/litellm/generic)
  with wire-profile (declared|host, eval) vs display-profile (+owned_by vllm, /model_group/info probe,
  llamacpp/Ollama cap masking); phase-1 wire identical across profiles; P2 extra_body route table defined
  (OpenRouter reasoning{}, declared-vLLM chat_template_kwargs.enable_thinking) for a later PR.
- .../ayd-params-v2/mockup_spec.json — dialog states A–E (the five endpoints) + F (More) + G (rejection) + H (starvation).
- Verified vs origin/main 7c2d7ce and vendored sources in ayd-params-v2/src (vLLM enable_thinking injection,
  Ollama/llama.cpp ignore max_completion_tokens, llama.cpp/Ollama map effort none -> thinking off).

Left:
- Owner decisions D0–D10 in PROPOSAL.md §10 (D0: confirm P1–P6 mapping — only P2 was found defined;
  D1 drop presets; D3 wire fingerprint in arm identity; D4 confirm P2 route table; D5/D6 checks C1/C2).
- Optional: render mockup_spec.json into an HTML mockup (older mockup_spec_v2*.json files there belong to the drafts).
- Implementation (plan + TDD list in PROPOSAL.md §9) — not started.

Exact next step: present PROPOSAL.md §10 decisions to the owner; after ratification, write the implementation
plan from §9 on a new branch off origin/main (start with the move-only prep commit 0).


## 2026-09-10 — owner accepted every recommendation (D1–D10)

- D1 raw fields + hints · D2 drop penalties (5 params) · D3 sent-settings fingerprint in arm identity · D4 two extra_body
  routes (OpenRouter reasoning object; vLLM enable_thinking only for provider: vllm) as a separate PR after phase 1 ·
  D5 no vLLM access → hide Thinking Off on vLLM · D7 hidden saved values not sent, no caption · D8 On = medium ·
  D9 langchain-openai>=0.2.14,<2 · D10 document drop_params + one log line when LiteLLM is detected.
- D6 done (~$0.0006): OpenRouter enforces max_completion_tokens even when the listing reports only max_tokens
  (cap 20 → finish_reason=length on mistral-nemo, l3-lunaris-8b, qwen3.8-27b) → never mask Max output tokens on OpenRouter.
  reasoning {enabled:false} gave 0 reasoning tokens on qwen3.8-27b (D4 route works).
- Next: implement per the proposal §9 on a new branch after the UI release merges (shared connection/llm files).

## 2026-09-10 — implementation plan (planning agent; no code, no commits)

Done:
- Worktree created: <scratch>/params-v2 on branch feat/ask-your-docs-model-params from
  origin/feat/ask-your-docs-activity-panel (head ded9b556). Venv synced (--frozen --group dev
  --extra harness-ask-your-docs, cpython-3.11 aarch64): langchain-openai 1.1.9, streamlit 1.59.1. Disk ~3.8 GB free.
- Read proposal v2 §0-§10, mockup spec (8 states), and the branch code. Budgets that force splits first:
  llm_connection.py 496/500, binding.py 499/500, app.py 453/500, ask_your_docs_models.py 188/200.
- Returned a 6-stage TDD plan: S1 move-only prep + config model + floor bump; S2 pure provider
  profiles + control support (D5 vLLM no Off, D6 OpenRouter never masks max tokens); S3 wire +
  LiteLLM probe/log (D10) + build_chat_model(wire=) + P3 reformulation bind; S4 dialog/page (mask,
  Test "sent ...", learned rejection, starvation, connection_key); S5 eval binding P4 refusal +
  raise-before-spend + D3 sent-settings fingerprint via a new HarnessBridge path; S6 docs/CHANGELOG + full gates.
- D4 (extra_body routes) is NOT in phase 1: the only phase-1 trace is WireParams.extra_body=None and THINKING_MAP_VERSION=1.

Left: every stage (no code written). One owner question raised: P3 scope (pin temperature 0 always vs
only when a temperature is sent; the proposal says the latter to keep no-params arms byte-identical).

Exact next step: in the params-v2 worktree, S1 commit 1 = byte-identical move-only prep
(connection_test.py, binding_llm_block.py, page_connection_actions.py, keep re-exports), full ask_your_docs suite green.

## 2026-09-10 — S1 implemented (move-only prep + params config model + langchain-openai floor)

Done (worktree <scratch>/params-v2, branch feat/ask-your-docs-model-params, NOT pushed):
- 307be872 refactor(ask-your-docs): move-only prep — connection_test.py (run_connection_test + _TEST_*),
  binding_llm_block.py (block source for the eval binding), page_connection_actions.py (PageConnectionActions +
  dialog_actions; app.py injects PageConnectionHooks(run, resolve_connection, page_bearer)). Suite green on it (560 passed).
  Deviation: two tests retargeted private seams that moved (binding_llm_block.AppConfig monkeypatch;
  connection_test._TEST_CONNECTION_TIMEOUT_SECONDS) — re-exporting a private constant/AppConfig from the old module was worse.
- e1450c13 feat(ask-your-docs): ChatParamsConfig (5 keys, Field-only bounds, param_bounds(), refused-key table) +
  LlmConnectionConfig.provider/.params + fold (dialog snapshot replaces YAML whole; launch tier carries none) +
  langchain-openai>=0.2.14,<2 (uv.lock one-line edit, lock --check green). tests/harness + config tests: 823 passed, 2 skipped.
- error_redaction.py: comment only — params messages already survive redaction with the input blanked (pinned by test).
- Design notes: thinking 'auto' normalizes to None (one canonical 'not sent'); numeric params via env must use the JSON
  form PYDOCS_ASK_YOUR_DOCS__LLM__PARAMS='{"temperature":0.2}' (env leaves are strings; numeric strings refused by design,
  the message says so). Arm hashing (arms.to_canonical) uses the raw settings mapping, so new defaults move no hash.

Left / watch-outs:
- Budgets are tight: llm_connection.py 494/500, ask_your_docs_params_models.py 199/200, ask_your_docs_models.py 195/200.
  S3 must split before adding build_chat_model(wire=) (e.g. move connection_auth_kwargs + httpx client helpers out).
- D4 (extra_body routes) stays out of phase 1 per proposal §9 (phase-1 trace is only WireParams.extra_body=None).
- default_config.yaml commented params template is S6 (docs).

Exact next step: S2 — harness/ask_your_docs/provider_profiles.py (wire_profile pure, display_profile, FAMILY_TABLE,
THINKING_MAP_VERSION=1) + control_support.py (D5 vLLM hides Off; D6 OpenRouter never masks Max output tokens), TDD.

## 2026-09-10 — S2 implemented (provider profiles + per-control support, pure)

Done (worktree <scratch>/params-v2, branch feat/ask-your-docs-model-params, NOT pushed):
- a3927f36 feat(ask-your-docs): provider profiles + per-control support for model settings
  - provider_profiles.py (134 lines): ProviderProfile, DisplayProfile(profile, ignores_output_cap),
    wire_profile(declared, base_url) (pure), display_profile(wire, listing_entry, group_info) (refines only a
    generic wire; decided wire never re-routed), SamplingRule, FamilyRow, FAMILY_TABLE (9 rows keyed off the shared
    REASONING_MODEL_PREFIXES + gpt-5. / gpt-4o / gpt-4.1 + vllm-only qwen3 / gpt-oss), family_row(), THINKING_MAP_VERSION=1.
  - control_support.py (191): ControlSupport (thinking_options, show_*, max_tokens_ceiling, thinking_on_off, sampling;
    thinking_labels / temperature_shown(thinking) / top_p_shown(thinking)), support_for(display, model, entry,
    group_info, learned), openrouter_support, litellm_support, family_support (D5 strip on vLLM), learned hiding.
  - param_rejections.py (57): rejected_control_from_error(exc, sent) -> control name; re-exported from control_support.
  - retrieval/llm_clients/reasoning_models.py: REASONING_MODEL_PREFIXES promoted; openai.py imports it.
  - Fakes: FakeModelsEndpoint.{openrouter,vllm,ollama,llamacpp}_entry + FakeModelGroupInfo (row/payload/async seam).
  - Gates: tests/harness + llm_clients + config tests 1009 passed / 2 skipped; ruff, mypy, complexipy, vulture clean.
Deviations: parser split into param_rejections.py (control_support would be 218 > 200); family (non-vllm) rows act as a
hide layer on EVERY profile (so LiteLLM/generic gpt-5-mini hides Temperature); OpenRouter/LiteLLM with no entry/row
keep the generic baseline; a learned max_tokens rejection is ignored on OpenRouter (D6 absolute).
Left: D10 LiteLLM log line belongs with detection wiring (S5, fetch_litellm_group_info); placeholders
(default_parameters) are form-stage; D4 extra_body stays out (proposal §9 phase 2).

Exact next step: S3 — harness/ask_your_docs/chat_wire.py (WireParams hashable, extra_body=None in phase 1,
resolve_wire(support, params) honouring D7 drop-hidden, On -> medium, max_completion_tokens) — split
llm_connection.py (494/500) before build_chat_model(wire=).
