export const meta = {
  name: 'ayd-model-params-redesign',
  description: 'Redesign ask-your-docs model parameters: simple controls, provider-aware (OpenRouter, OpenAI, vLLM, LiteLLM, generic)',
  phases: [
    { title: 'Research', detail: 'vLLM, LiteLLM, OpenAI/generic parameter + metadata behaviour' },
    { title: 'Options', detail: 'two simplicity-first designs' },
    { title: 'Judge', detail: 'pick, synthesize, new mockup spec + decisions' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const OUT = S + '/ayd-params-v2'
const GROUND = `
Product: pydocs-mcp "ask your docs" Streamlit harness (python/pydocs_mcp/harness/ask_your_docs/). The chat model is a LangChain
ChatOpenAI against any OpenAI-compatible endpoint, configured by YAML ask_your_docs.llm {base_url, model, auth, vision} with a
session-only Connection dialog (connection_dialog.py, model_listing.py, llm_connection.py). Read code via \`git -C ${R} show origin/main:<path>\`.
A first design exists: ${S}/ayd-params/PROPOSAL.md and ${S}/ayd-params/mockup_spec.json (7 typed first-class params under
ask_your_docs.llm.params, support resolved from OpenRouter listing metadata → model-family table → unverified → learned
rejection; eval params only from arm YAML, raise before spend). Read both first.
OWNER FEEDBACK on that design (verbatim intent): (1) "These configs should not be only for OpenRouter settings but also for vLLM,
OpenAI or LiteLLM." (2) "Model parameters in the Connection dialog should be simpler and easy to use."
Constraints that still hold: MCP surface frozen (no new tool/param); tunables live in YAML (AppConfig, extra=forbid,
single-source defaults); the eval binding stays sealed (arm YAML only, deterministic, part of arm identity); G8 secrets never in
YAML/argv/UI/logs; prior owner decisions O1 (no timeout/max_retries keys) and O2 (no raw vision.extra_body) — a typed,
factory-built extra_body (never raw passthrough) is allowed ONLY if flagged as a decision needing owner ratification.
The companion activity-panel design captures reasoning from reasoning_content / reasoning fields — keep the two consistent.
READ-ONLY for repos. Scratch outputs only under ${OUT}. Never print secrets.`
const withFallback = (p, o) => agent(p, { ...o, model: 'fable' }).then(r => r ?? agent(p, { ...o, model: 'opus', label: o.label + ':opus' }))

const HANDOFF = `HANDOFF RULE (the owner may run out of credits mid-run): before you return, append a short dated entry to /Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-params-v2.md — what you finished (commit SHAs, files), what is left, and the exact next step — so a fresh session can continue without this workflow. Never write secrets there.`
phase('Research')
const [vllm, litellm, openaiGeneric] = await parallel([
  () => agent(`${GROUND}
TASK (vLLM OpenAI-compatible server, current releases): which request parameters /v1/chat/completions accepts (temperature,
top_p, top_k, min_p, repetition_penalty, presence/frequency_penalty, seed, max_tokens vs max_completion_tokens, stop), how
non-OpenAI params must be sent through the openai client (extra_body), how reasoning models behave (reasoning parser →
reasoning_content in the message/stream; Qwen3 thinking on/off via chat_template_kwargs {"enable_thinking": false}; does vLLM
accept reasoning_effort?), what /v1/models returns (owned_by "vllm", max_model_len; no per-param metadata), and how a client can
auto-detect a vLLM endpoint. Use official docs/source (WebFetch/WebSearch, or pip download the source into ${OUT}/src); cite URLs
or file:line. State what is verified vs inferred.`, { label: 'research:vllm', phase: 'Research', model: 'opus' }),
  () => agent(`${GROUND}
TASK (LiteLLM proxy): what GET /v1/models and GET /model/info (and /v1/model/info) return per model — especially
model_info.supported_openai_params, supports_reasoning, max_tokens/max_input_tokens, mode; how LiteLLM maps reasoning_effort
and "thinking" across providers; drop_params (server-side dropping of unsupported params) and whether a client can rely on it;
how reasoning text comes back (reasoning_content, thinking_blocks); auth header conventions; how a client can auto-detect a
LiteLLM proxy (endpoint probe, headers). Use official docs/source (WebFetch/WebSearch or pip download litellm source into
${OUT}/src); cite. State verified vs inferred.`, { label: 'research:litellm', phase: 'Research', model: 'opus' }),
  () => agent(`${GROUND}
TASK (OpenAI + generic OpenAI-compatible): api.openai.com /v1/models metadata (none per param); which models reject temperature
/top_p (o-series, gpt-5 family unless reasoning_effort "none"), reasoning_effort values per family, max_completion_tokens;
what installed langchain-openai ChatOpenAI does (reasoning_effort field, validate_temperature dropping, max_tokens rename) —
check ${S}/smoke/venv-mcp1 or ~/venvs/ayd-openrouter site-packages; llama.cpp server and Ollama OpenAI-compat param support
(top_k, min_p via extra_body; think flags); and a recap of OpenRouter (supported_parameters, reasoning object vs reasoning_effort,
include_reasoning) from ${S}/ayd-params/. Output: one provider capability matrix (rows = OpenRouter, OpenAI, vLLM, LiteLLM,
llama.cpp/Ollama, unknown; columns = metadata source, temperature/top_p, max tokens field, reasoning control and how to send it,
thinking off, top_k/min_p, how reasoning text returns, auto-detection signal) with evidence.`, { label: 'research:openai-generic', phase: 'Research', model: 'opus' }),
])

phase('Options')
const ANGLES = [
  ['presets-first', 'Plain-language presets: the dialog shows at most three controls — Response style (Precise / Balanced / Creative → mapped to temperature/top_p per provider), Thinking (Off / Low / Medium / High → mapped per provider to reasoning_effort, OpenRouter reasoning, vLLM enable_thinking, LiteLLM reasoning_effort/thinking), and Answer length (Short / Normal / Long → max tokens with reasoning headroom) — plus one collapsed "Advanced" with raw numbers. Controls a model cannot honour are simply absent, with one sentence saying why.'],
  ['compact-raw', 'Compact raw: the dialog shows only the parameters the chosen model/provider supports, as a short form of real parameter names with sensible defaults and no badges; unsupported ones are hidden; a provider profile (auto-detected, overridable in YAML) translates each value to the right wire field; one "Advanced" disclosure for the rest.'],
]
const options = await parallel(ANGLES.map(([k, angle]) => () => withFallback(`${GROUND}
Design the feature with SIMPLICITY as the top criterion and multi-provider support as the second, using this angle: ${angle}
Must cover: provider profiles (openrouter, openai, vllm, litellm, generic) — auto-detection order and a YAML override
(e.g. ask_your_docs.llm.provider: auto|openrouter|openai|vllm|litellm|generic), how each control maps to wire fields per profile
(first-class ChatOpenAI kwargs vs a typed, factory-built extra_body — flag the O2 departure for ratification), where support
information comes from per profile (OpenRouter supported_parameters, LiteLLM /model/info supported_openai_params, vLLM/OpenAI/
generic: static profile table + learned rejection), the YAML schema (small, typed, validated; what the presets store), eval binding
behaviour (YAML only, deterministic mapping, arm identity, raise before spend), the dialog in ASCII for: OpenRouter qwen/qwen3.8-27b,
OpenAI gpt-5-mini, local vLLM Qwen/Qwen3-8B, LiteLLM proxy model, unknown endpoint — each must be visibly simpler than the v1
mockup, and the implementation sketch (files, functions, tests) with its size compared to v1.
=== RESEARCH: vLLM ===
${vllm}
=== RESEARCH: LiteLLM ===
${litellm}
=== RESEARCH: OpenAI + generic ===
${openaiGeneric}`, { label: `option:${k}`, phase: 'Options', effort: 'high' })))

phase('Judge')
const proposal = await withFallback(`${GROUND}
${HANDOFF}
OWNER RULES added 2026-09-10 (binding): MASK — hide entirely — any parameter the chosen model/provider cannot honour (no disabled rows, no captions); keep the dialog simple; include ONE Thinking control that maps per provider; the owner ACCEPTED all recommendations P1–P6 (P2: typed, factory-built extra_body phase ratified as a separate later change).
Judge the two redesigns (1-10 on: simplicity for a first-time user, correctness across the five provider profiles, honesty when a
control can't be honoured, eval-arm safety, implementation size/risk, consistency with the activity panel's reasoning capture), then
synthesize ONE final proposal: summary (what the user sees in one sentence); provider profiles + detection; the control set and
per-profile mapping table; YAML schema; dialog behaviour with ASCII for the five endpoints above; eval rules; what changed versus
v1 and why; implementation plan + TDD list; decisions for the owner (short, each with a recommendation — include the extra_body/O2
ratification if needed). Write ${OUT}/PROPOSAL.md (the full proposal) and ${OUT}/mockup_spec.json (dialog states for the five
endpoints as structured data for an HTML mockup: controls shown, values, the one-line notes).
=== OPTIONS ===
${options.filter(Boolean).map((o, i) => `--- ${ANGLES[i][0]} ---\n${o}`).join('\n')}`, { label: 'judge:synthesize', phase: 'Judge', effort: 'high' })
return { proposal, options, vllm, litellm, openaiGeneric }
