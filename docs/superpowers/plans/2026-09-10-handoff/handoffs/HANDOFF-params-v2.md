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
