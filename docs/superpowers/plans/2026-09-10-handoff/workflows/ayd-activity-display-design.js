export const meta = {
  name: 'ayd-activity-display-design',
  description: 'Design a tool-call + reasoning activity display for the ask-your-docs chat UI (research, 3 options, judge)',
  phases: [
    { title: 'Research', detail: 'UI audit, streaming mechanics, reasoning capture' },
    { title: 'Options', detail: 'three independent display designs' },
    { title: 'Judge', detail: 'score and synthesize the proposal' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const VENV = '/Users/msobroza/venvs/ayd-openrouter'
const GROUND = `
Product: pydocs-mcp's "ask your docs" Streamlit chat harness (python/pydocs_mcp/harness/ask_your_docs/: app.py, agent.py,
architectures/*, connection_dialog.py, llm_connection.py, multimodal.py, theme.py, pages/2_Graph.py). A LangGraph ReAct agent
calls the pydocs-mcp MCP tools (search_codebase, get_symbol, get_context, get_references, get_why, get_overview, grep, glob,
read_file) over a stdio serve child and answers with citations. READ-ONLY task: read code via \`git -C ${R} show origin/main:<path>\`
(origin/main = released 0.6.0; a 0.6.1 fix in flight moves serve_connection into serve_spawn.py and adds launcher env names —
do not depend on those internals). Installed 0.6.0 + deps for experiments: ${VENV} (pydocs-mcp 0.6.0, langgraph, langchain-core,
langchain-openai, langchain-mcp-adapters, streamlit 1.63). A live example workspace/config exists: ~/pydocs-openrouter/index and
~/pydocs-openrouter/config.yaml (OpenRouter chat model qwen/qwen3.8-27b, which exposes reasoning; embeddings qwen/qwen3-embedding-4b).
Never edit repos. Never print secrets. Scratch files only under ${S}/ayd-ui.
USER REQUEST (verbatim intent): "in the UI I cannot see the tools that were called and the reasoning; propose a good display
for users to understand [what the agent did], [showing reasoning] when the model makes it available."`

const withFallback = (p, o) => agent(p, { ...o, model: 'fable' }).then(r => r ?? agent(p, { ...o, model: 'opus', label: o.label + ':opus' }))

phase('Research')
const [audit, streaming, reasoning] = await parallel([
  () => agent(`${GROUND}
TASK (UI audit): describe exactly what the chat page renders today per turn and how: message history shape in st.session_state,
how ask()/the agent is invoked (ainvoke vs streaming), what intermediate data exists and is discarded (AIMessage.tool_calls,
ToolMessage content, reasoning fields, token usage), the "searching your docs…" status, error/redaction paths (the G8 secret
rules: no secrets in UI/logs), the sidebar status line (host · model · bearer · vision verdict), the Graph page, theme. Note
constraints: app.py/agent.py line budgets (<500, tests/harness/ask_your_docs/test_module_line_budgets.py), AppTest-based UI tests
(tests/harness/ask_your_docs/*page*/_page_fixtures.py), lazy imports, frozen MCP surface. Cite file:line.`,
    { label: 'research:ui-audit', phase: 'Research', model: 'opus' }),
  () => agent(`${GROUND}
TASK (streaming mechanics): with the INSTALLED versions in ${VENV} (report them), determine the best way to surface agent activity
live in Streamlit: LangGraph astream(stream_mode=["updates","messages"]) vs astream_events(version="v2"); how to get tool-call start
(name + args), tool result (content, truncated), model text tokens, and per-step timing; how that maps onto Streamlit 1.63 widgets
(st.chat_message, st.status with state running/complete/error, st.expander, st.write_stream, st.empty placeholders, st.popover,
badges) inside an asyncio call from a Streamlit script (event-loop handling already used by app.py). Write a tiny offline prototype
under ${S}/ayd-ui using a FakeListChatModel / fake tool so it costs nothing, run it, and report what events arrive in what order.`,
    { label: 'research:streaming', phase: 'Research', model: 'opus' }),
  () => agent(`${GROUND}
TASK (reasoning capture): how can the harness obtain the model's reasoning text, provider-agnostically, via langchain-openai
ChatOpenAI against OpenAI-compatible endpoints? Check the installed langchain-openai source: which response fields are mapped into
AIMessage (additional_kwargs / content blocks — e.g. "reasoning_content", "reasoning", OpenAI Responses-API reasoning summaries),
and what OpenRouter returns for qwen/qwen3.8-27b ("reasoning" / "reasoning_details" when include_reasoning / reasoning:{...} is
sent). Run a SMALL paid probe (≤3 calls, short prompts, max_tokens ≤ 200; key: \`export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' ${R}/.env | cut -d= -f2-)"\`)
with raw httpx AND with ChatOpenAI (streaming and non-streaming, with and without a tool bound) to see where reasoning lands and
whether ChatOpenAI drops it. Also: local servers (vLLM/llama.cpp "reasoning_content"), models that emit <think>…</think> inline.
Propose: (a) an extraction strategy (e.g. a small ChatOpenAI subclass or response hook mapping reasoning into a content block), (b)
a capability signal — how the UI can tell the user "this model exposes reasoning" vs "not available" (static table vs detect on
first response vs endpoint /models metadata such as OpenRouter's supported_parameters containing "reasoning"/"include_reasoning"),
(c) cost/latency and privacy implications (reasoning may be long; never log it with secrets). Report exact findings with evidence.`,
    { label: 'research:reasoning', phase: 'Research', model: 'opus' }),
])

phase('Options')
const ANGLES = [
  ['inline-steps', 'Chat-native and minimal: activity lives inside the assistant bubble as a compact, collapsible step list that streams live and collapses to a one-line summary when the answer arrives.'],
  ['timeline-panel', 'Trace-first: a per-turn timeline (reasoning → tool call → result → answer) with durations, args and result previews, plus a turn-level summary chip row; optimized for users learning how the agent searches.'],
  ['progressive-disclosure', 'Novice-to-expert: plain-language status for everyone ("Searched code for …", "Opened src/…:39"), expandable to raw tool args/results and full reasoning; explicit availability badge for reasoning per model.'],
]
const options = await parallel(ANGLES.map(([k, angle]) => () => withFallback(`${GROUND}
You are a product designer + engineer. Using the research below, design the activity display with this angle: ${angle}
Cover: layout per turn (ASCII mockup of running, finished, error states), what each tool call shows (friendly label per tool, args,
result preview, citations linking to files), how reasoning is shown when available and what the UI says when the model does not
expose it (and where the user sees that — sidebar status line vs per turn), persistence in chat history (re-rendered on rerun),
performance (streaming, no blocking), privacy (G8: no secrets, redaction, reasoning never logged), accessibility, dark/light theme,
config (YAML-only knobs under ask_your_docs.ui.* with defaults — never MCP params), what data must be captured from the agent run
and the implementation sketch (files/functions, line budgets, tests with AppTest + fakes), and risks.
=== UI AUDIT ===
${audit}
=== STREAMING ===
${streaming}
=== REASONING ===
${reasoning}`, { label: `option:${k}`, phase: 'Options', effort: 'high' })))

phase('Judge')
const proposal = await withFallback(`${GROUND}
You are the judge. Score the three options below (1-10) on: user comprehension (does a user understand what the agent did and why),
signal-to-noise (answer stays primary), honesty about reasoning availability, implementation risk/size within the repo's rules,
performance, privacy. Then SYNTHESIZE one final proposal from the winner, grafting the best ideas from the others. Output markdown:
1) one-paragraph summary; 2) the final display with ASCII mockups for: turn running, turn finished (collapsed + expanded), reasoning
not available, tool error; 3) per-tool friendly labels table; 4) reasoning capture + availability signal (with the evidence from
research); 5) YAML knobs + defaults; 6) implementation plan (files, functions, line budgets, TDD test list) and how it composes with
the planned "one serve session per page" change; 7) risks and open questions for the owner. Also write a structured JSON spec of the
mockup states to ${S}/ayd-ui/mockup_spec.json (for a later HTML mockup).
=== OPTIONS ===
${options.filter(Boolean).map((o, i) => `--- ${ANGLES[i][0]} ---\n${o}`).join('\n')}`, { label: 'judge:synthesize', phase: 'Judge', effort: 'high' })

return { proposal, options, audit, streaming, reasoning }
