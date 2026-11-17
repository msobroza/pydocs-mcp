export const meta = {
  name: 'ayd-model-params-design',
  description: 'Design per-model generation parameters (temperature, reasoning effort, ...) for the ask-your-docs Connection dialog + YAML',
  phases: [
    { title: 'Research', detail: 'connection code audit + provider parameter support' },
    { title: 'Options', detail: 'two designs: typed static vs metadata-driven' },
    { title: 'Judge', detail: 'score and synthesize' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const VENV = '/Users/msobroza/venvs/ayd-openrouter'
const GROUND = `
Product: pydocs-mcp's "ask your docs" Streamlit chat harness (python/pydocs_mcp/harness/ask_your_docs/). The LLM connection
(0.6.0) is one YAML block ask_your_docs.llm {base_url, model, auth{token_url|api_key_env,...}, vision} resolved by
llm_connection.py with precedence YAML < environment < launcher flags < Connection dialog (session-only), a Connection dialog
(connection_dialog.py) that lists the endpoint's models (model_listing.py), and the eval binding (binding.py) that resolves the
same block from the arm YAML but SEALS the environment tier. Design record: docs/superpowers/specs/2026-09-05-ask-your-docs-llm-connection-design.md
— owner decisions there include O1 "no timeout/max_retries keys" and O2 "no vision.extra_body"; respect them (prefer typed,
validated parameters over raw passthrough) or state explicitly why a proposal departs and flag it for owner ratification.
Config models: python/pydocs_mcp/retrieval/config/ask_your_docs_models.py (extra=forbid). Repo rules: MCP surface frozen;
tunables via YAML; single-source defaults; G8 secrets never in YAML/argv/UI/logs. READ-ONLY: read code via
\`git -C ${R} show origin/main:<path>\` (origin/main = released 0.6.0). Installed 0.6.0 + deps: ${VENV}. Scratch only under ${S}/ayd-params.
USER REQUEST: "I would like to configure model parameters such as effort, temperature and others if available when I choose the model."`
const withFallback = (p, o) => agent(p, { ...o, model: 'fable' }).then(r => r ?? agent(p, { ...o, model: 'opus', label: o.label + ':opus' }))

phase('Research')
const [codeAudit, providers] = await parallel([
  () => agent(`${GROUND}
TASK (code audit): how the chat model is constructed today (ChatOpenAI kwargs, where temperature/max_tokens/etc. are or are not set —
e.g. llm_connection.py ~412-424, agent.py build path, reformulation.py, the vision model), how the Connection dialog works (fields,
session_state keys, ConnectionOverride, the model listing and its cache, AppTest-based tests), how the eval binding builds the same
model (must stay YAML-driven and sealed), line budgets of the touched files, and every place a new parameter would have to flow
(chat model, reformulation call, vision sub-model, token-service renewal/retry path). Cite file:line.`,
    { label: 'research:code-audit', phase: 'Research', model: 'opus' }),
  () => agent(`${GROUND}
TASK (provider parameter support): (1) OpenRouter GET /api/v1/models (public, no key) — for qwen/qwen3.8-27b and 3-4 other popular
models, list supported_parameters and any default_parameters / per-parameter metadata; document what "reasoning", "reasoning_effort",
"include_reasoning", "verbosity", "top_k", "min_p", "repetition_penalty" mean and their valid ranges/values. (2) OpenAI-style
/v1/models (no parameter metadata) and local servers (vLLM / llama.cpp / Ollama OpenAI-compat): what can be detected, what a safe
fallback set is. (3) Installed langchain-openai ChatOpenAI: which params are first-class (temperature, top_p, max_tokens/
max_completion_tokens, reasoning_effort, frequency/presence_penalty, seed, stop) and which must go through extra_body/model_kwargs
(OpenRouter's reasoning:{effort|max_tokens|exclude}, top_k, min_p); how reasoning models reject temperature (OpenAI o-series) and how
to avoid sending unsupported params. (4) A SMALL paid probe (≤4 short calls, max_tokens ≤ 100) against OpenRouter qwen/qwen3.8-27b
via ChatOpenAI proving temperature + reasoning effort are accepted and change behavior (e.g. reasoning token count differs by effort);
key: \`export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' ${R}/.env | cut -d= -f2-)"\`, never print it. Report evidence.`,
    { label: 'research:providers', phase: 'Research', model: 'opus' }),
])

phase('Options')
const ANGLES = [
  ['typed-static', 'Typed and conservative: a small typed ask_your_docs.llm.params YAML block (temperature, top_p, max_tokens, reasoning_effort, seed) validated by pydantic; the dialog shows exactly these, greys out ones the chosen model is known not to support.'],
  ['metadata-driven', 'Metadata-driven: the dialog reads the chosen model\'s supported_parameters (when the endpoint publishes them) and renders only those controls with correct widgets/ranges from a parameter registry; YAML holds a typed params block that the registry validates; unknown endpoints fall back to a safe set.'],
]
const options = await parallel(ANGLES.map(([k, angle]) => () => withFallback(`${GROUND}
Design the feature with this angle: ${angle}
Cover: YAML schema (names, types, defaults = unset → provider default, validation ranges, error messages carrying the offending
value), precedence (YAML < dialog, session-scoped; launcher flags?), how params reach every model call (chat, reformulation, vision
sub-model) and which must NOT (e.g. reformulation may pin temperature 0), unsupported-parameter handling (never send a param the model
rejects; degrade with a caption), eval binding behavior (params from arm YAML only; do they enter arm identity? — they change what an
arm measures, so they must be hashed if the arm hash covers model settings; check binding/arm identity code), UI mockup (ASCII) of the
Connection dialog after choosing a model (supported vs unsupported params, reset-to-default, "not reported by this endpoint" state),
how it composes with a reasoning/tool-activity display (reasoning effort ↔ reasoning shown), implementation sketch (files/functions,
line budgets, TDD test list with AppTest + fakes), and owner decisions needed (O1/O2 interplay).
=== CODE AUDIT ===
${codeAudit}
=== PROVIDERS ===
${providers}`, { label: `option:${k}`, phase: 'Options', effort: 'high' })))

phase('Judge')
const proposal = await withFallback(`${GROUND}
Judge the two designs (1-10 each on: user value, correctness across providers, honesty when support is unknown, fit with the
0.6.0 LLM-connection design and owner decisions, eval-arm safety, implementation size/risk), then synthesize ONE final proposal:
summary; YAML schema + defaults; dialog behavior with ASCII mockups (model with rich metadata, endpoint without metadata, param
rejected at runtime); parameter registry table (name, widget, range, where sent: first-class vs extra_body); eval-binding and
arm-identity rules; implementation plan + TDD list; open questions for the owner. Write the dialog mockup states as JSON to
${S}/ayd-params/mockup_spec.json for a later HTML mockup.
=== OPTIONS ===
${options.filter(Boolean).map((o, i) => `--- ${ANGLES[i][0]} ---\n${o}`).join('\n')}`, { label: 'judge:synthesize', phase: 'Judge', effort: 'high' })

return { proposal, options, codeAudit, providers }
