export const meta = {
  name: 'pydocs-tool-reference',
  description: 'Build an exact reference of the nine pydocs-mcp tools (MCP + CLI): schemas from the contract/code, real input/output from the example_needle index, verified',
  phases: [
    { title: 'Gather', detail: 'schemas from contract + code · live MCP and CLI runs' },
    { title: 'Verify', detail: 'cross-check examples vs contract, completeness' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const SRC = S + '/symbol-resolve'
const GROUND = `READ-ONLY task for pydocs-mcp. Source to read: ${SRC} (a clean checkout of origin/main 5461d8e — do NOT edit it; another workflow may commit a spec file there, ignore it). The released package the owner uses is pydocs-mcp 0.6.1 from PyPI in ~/venvs/ayd-openrouter (CLI: ~/venvs/ayd-openrouter/bin/pydocs-mcp). The indexed example is the owner's demo project example_needle: bundle dir ~/pydocs-openrouter/index, config ~/pydocs-openrouter/config.yaml (embeddings qwen/qwen3-embedding-4b via OpenRouter), project root /Users/msobroza/Projects/example_needle. Command shape: \`pydocs-mcp --config ~/pydocs-openrouter/config.yaml <subcommand> ... --workspace ~/pydocs-openrouter/index\` (--config BEFORE the subcommand). The embedder needs OPENROUTER_API_KEY to construct: \`export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' ${R}/.env | cut -d= -f2-)"\` — NEVER print, echo, log or write the key anywhere. Only search_codebase and get_why embed the query (tiny paid calls): run at most 3 of those in total. Never write inside ${R} or any worktree; scratch files go under ${S}/toolref/. Do not modify any index.`

const SCHEMA_OUT = {
  type: 'object',
  properties: {
    envelope: { type: 'string', description: 'response envelope / meta fields / error shape, from tool-contracts §2, concise' },
    grammar: { type: 'string', description: 'dotted-target grammar and corpus selectors (project/package/scope) summary' },
    tools: { type: 'array', items: { type: 'object', properties: {
      name: { type: 'string' }, purpose: { type: 'string' }, when_to_use: { type: 'string' },
      params: { type: 'array', items: { type: 'object', properties: {
        name: { type: 'string' }, type: { type: 'string' }, default: { type: 'string' }, constraints: { type: 'string' }, description: { type: 'string' } },
        required: ['name', 'type', 'default', 'constraints', 'description'] } },
      cli_subcommand: { type: 'string' }, cli_aliases: { type: 'array', items: { type: 'string' } },
      cli_flags: { type: 'string', description: 'how each param maps to CLI flags/positionals' },
      output_shape: { type: 'string' }, meta_fields: { type: 'string' }, errors: { type: 'string' }, source_refs: { type: 'string' } },
      required: ['name', 'purpose', 'when_to_use', 'params', 'cli_subcommand', 'cli_aliases', 'cli_flags', 'output_shape', 'meta_fields', 'errors', 'source_refs'] } },
    other_cli: { type: 'string', description: 'non-tool CLI commands (index, serve, watch, lookup [deprecated], global flags like -v/--config/--workspace/--cache-dir) with one-line purpose each' },
  },
  required: ['envelope', 'grammar', 'tools', 'other_cli'],
}
const RUN_OUT = {
  type: 'object',
  properties: {
    setup_notes: { type: 'string' },
    runs: { type: 'array', items: { type: 'object', properties: {
      tool: { type: 'string' }, mcp_arguments_json: { type: 'string' }, mcp_result_text_excerpt: { type: 'string' },
      mcp_meta_json: { type: 'string' }, is_error: { type: 'boolean' },
      cli_command: { type: 'string' }, cli_output_excerpt: { type: 'string' }, notes: { type: 'string' } },
      required: ['tool', 'mcp_arguments_json', 'mcp_result_text_excerpt', 'mcp_meta_json', 'is_error', 'cli_command', 'cli_output_excerpt', 'notes'] } },
    paid_calls: { type: 'integer' },
  },
  required: ['setup_notes', 'runs', 'paid_calls'],
}

phase('Gather')
const [schemas, runs] = await parallel([
  () => agent(`${GROUND}
SCHEMAS: read ${SRC}/docs/tool-contracts.md in full (normative) and the tool definitions in ${SRC}/python/pydocs_mcp/server.py, the input models (mcp_inputs.py or wherever ReferencesInput/SymbolInput etc. live), ${SRC}/python/pydocs_mcp/application/tool_docs.py (TOOL_DOCS), and the CLI parser in ${SRC}/python/pydocs_mcp/__main__.py (+ any cli module). For EACH of the nine tools (get_overview, search_codebase, get_symbol, get_context, get_references, get_why, grep, glob, read_file) return exact params (name, type, default, allowed values / bounds, description), the CLI subcommand + aliases + flag mapping, output shape (text body; get_symbol summary/tree JSON exception), meta fields incl. meta.suggestion (which tools) and get_references meta.resolution, and error behaviour. Also the envelope (§2), the dotted-target grammar + corpus selectors (project/package/scope), and the non-tool CLI commands. Cite path:line. Where 0.6.1 might differ from main, say so only if you verify it (you may run \`~/venvs/ayd-openrouter/bin/pydocs-mcp <sub> --help\`).`, { label: 'gather:schemas', phase: 'Gather', schema: SCHEMA_OUT, model: 'opus', effort: 'high' }),
  () => agent(`${GROUND}
LIVE RUNS: produce one realistic example per tool against example_needle, BOTH ways:
(a) MCP: write ${S}/toolref/mcp_calls.py using ~/venvs/ayd-openrouter/bin/python with mcp.client.stdio (StdioServerParameters(command=~/venvs/ayd-openrouter/bin/pydocs-mcp, args=["--config", CONFIG, "serve", "--workspace", BUNDLE], env={**os.environ})) + ClientSession: initialize, list_tools (record the nine names), then call each tool once with good arguments and capture result.content[0].text, result.isError, and any structured/meta part (print the raw result object's fields). Suggested calls (adjust to real names in the index; discover them with get_overview/glob first):
  get_overview {} (and/or with package) · search_codebase {"query":"late interaction maxsim scoring"} · get_symbol {"target":"needle.scoring.strategies.MaxSimScorer"} (also depth "source" if cheap) · get_context {"targets":[...two related symbols...]} · get_references {"target":"<a method>", "direction":"callers"} · get_why {"query":"why late interaction scoring"} · grep {"pattern":"class .*Scorer", "glob":"*.py", "output_mode":"content"} · glob {"pattern":"**/*.py"} · read_file {"file_path":"src/needle/scoring/strategies.py","offset":1,"limit":30}
  plus ONE error example (get_symbol {"target":"MaxSimScorer"} → isError) to show the error shape.
(b) CLI: the equivalent \`pydocs-mcp --config ... <subcommand> ... --workspace ...\` for each, capturing stdout (strip INFO log lines).
Use the exact parameter names the server exposes (read list_tools inputSchema — record it). Keep each excerpt ≤ 25 lines (trim the middle with "…" and say so). Max 3 embedding calls total (search/why). Never print the key. Report exact commands and argument JSON.`, { label: 'gather:live-runs', phase: 'Gather', schema: RUN_OUT, model: 'opus', effort: 'high' }),
])

phase('Verify')
const verified = await agent(`${GROUND}
VERIFY + COMPLETENESS: cross-check the live examples against the schemas and the contract. For each tool: are the argument names/values valid per the contract and the live inputSchema; do the outputs show the documented envelope/meta; any mismatch between 0.6.1 behaviour and origin/main docs; anything missing (a tool, an important param like depth/direction/output_mode/scope/project/limit, the error shape, meta.suggestion, meta.resolution)? If an example is wrong or missing, re-run a corrected FREE example (no more embedding calls) and supply it. Return a JSON-ish report: per tool {ok|fixed|issue, corrections, extra_examples}, plus a list of caveats for a user-facing reference.
SCHEMAS:\n${JSON.stringify(schemas).slice(0, 60000)}\n\nRUNS:\n${JSON.stringify(runs).slice(0, 60000)}`, { label: 'verify:reference', phase: 'Verify', model: 'opus', effort: 'high' })

return { schemas, runs, verified }
