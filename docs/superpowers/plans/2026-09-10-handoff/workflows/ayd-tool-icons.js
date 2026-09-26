export const meta = {
  name: 'ayd-tool-icons',
  description: 'Activity panel: a distinct, theme-aware Material icon per tool step (owner request), test-first, reviewed, gated — on the UI-release branch after the light-mode fix',
  phases: [
    { title: 'Implement', detail: 'TOOL_ICONS single source + render after plain_markdown, TDD' },
    { title: 'Review', detail: 'injection/escaping + tests · UX/visual in light and dark' },
    { title: 'Fix' },
    { title: 'Gates', detail: 'full CI gate set' },
  ],
}
// Run ONLY after the ayd-theme-native-fix workflow has finished on the same worktree.
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/ui-release'
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const HANDOFF = '/Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-ui-release.md'
const GROUND = `Repo pydocs-mcp. Work ONLY in worktree ${WT} (branch feat/ask-your-docs-activity-panel = draft PR #244; venv ${WT}/.venv, streamlit 1.59.1). Never touch the main checkout or other worktrees; never push, tag or open PRs; commit with the existing identity, NO trailers; stage explicit paths; restore complexipy-snapshot.json before staging; follow ${WT}/CLAUDE.md (functions 4-20 lines, the harness line budgets in tests/harness/ask_your_docs/test_module_line_budgets.py — activity_view.py is near its 400-line budget, so put new code in activity_labels.py or a small new module), single source of truth, WHY comments). TMPDIR=${S}/icons-tmp for pytest. Append a dated entry to ${HANDOFF} before returning.
OWNER REQUEST (2026-09-10): "a different icon for each tool that I call" in the activity panel.
DESIGN (approved direction): Streamlit Material Symbols (":material/<name>:"), NOT emoji — monochrome, inherit the text colour, readable in light and dark. One single-source mapping TOOL_ICONS (name → shortcode) next to the label phrases in activity_labels.py:
  search_codebase→search · get_symbol→data_object · get_context→account_tree · get_references→hub · get_overview→map · get_why→lightbulb · grep→manage_search · glob→folder_open · read_file→description · the vision/image-analysis step→image · thinking steps→psychology · any unknown tool name→build.
Rules: (1) the icon is a trusted constant prepended AFTER plain_markdown() escaping of the label — model-supplied text (tool args, reasoning) must never be able to render an icon or markdown (a label containing ":material/x:" from args stays literal); (2) keep the existing status glyph (_GLYPH per StepStatus) — order "<icon> <status glyph> <label>" or "<status glyph> <icon> <label>", pick the one the mockup/tests make most readable and say why; (3) both the live panel and the saved-turn redraw (and the thinking expander teaser if it has a label) use the same helper; (4) no YAML toggle (YAGNI) unless an existing ui setting naturally covers it.`

phase('Implement')
const impl = await agent(`${GROUND}
IMPLEMENT test-first: tests (in the existing activity label/view test files) that fail first — every tool in the frozen nine-tool list (take the names from the server/tool_docs single source, not a hand-copied list, so a tenth tool fails the parity test) has a Material icon; unknown tool → build; thinking → psychology; vision → image; icon applied after escaping (an arg containing ":material/bolt:" and "**x**" renders literally); live and saved panels both show the icon (AppTest on the page fixtures, asserting the rendered markdown); icon names are valid Material Symbols (check they render as icons in Streamlit 1.59 — e.g. inspect streamlit's material icon name set if it ships one, else document the check). Then implement the smallest change. Run tests/harness; ruff check + format on changed files; line budgets. One commit "feat(ask-your-docs): a Material icon per tool in the activity panel". Update the CHANGELOG [Unreleased] Added bullet for the panel (one clause) and the example README panel description if it lists what a step shows. Return SHA, RED/GREEN, the final line format.`, { label: 'implement:icons', phase: 'Implement', model: 'opus', effort: 'high' })

phase('Review')
const FIND = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' }, problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } },
  required: ['severity', 'file', 'problem', 'evidence', 'fix'] } } }, required: ['findings'] }
const [sec, ux] = await parallel([
  () => agent(`${GROUND}
REVIEW — injection, correctness, tests: \`git -C ${WT} show HEAD\`. Try to make model-controlled text render an icon, a link, or markdown in any step line (live, saved, thinking teaser, failure line, citation chips); check the parity test really derives from the tool single source; check unknown/vision/thinking paths; check nothing else in the label changed byte-for-byte except the prefix. Verified findings only.
Implementation: ${impl}`, { label: 'review:injection', phase: 'Review', schema: FIND, model: 'opus', effort: 'high' }).then(x => (x ? x.findings : [])),
  () => agent(`${GROUND}
REVIEW — UX / visual: launch the app from ${WT}/.venv on port 8511 (\`harness-ask-your-docs --workspace ~/pydocs-openrouter/index --config ~/pydocs-openrouter/config.yaml --port 8511 -- --server.headless true\`, OPENROUTER_API_KEY loaded silently from ${R}/.env with \`export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' ${R}/.env | cut -d= -f2-)"\` — never print it). If browser tools are available (ToolSearch "Claude_Browser"), ask ONE question (≈ $0.01), then check in BOTH light and dark themes (Streamlit menu → Settings → Theme) that each tool step shows its icon, the icons are distinct, aligned with the text, readable, and that failed/running states still read clearly; take screenshots. Otherwise do an AppTest-level check of the rendered markdown and say the visual check is pending. Stop the server afterwards. Verified findings only.
Implementation: ${impl}`, { label: 'review:ux', phase: 'Review', schema: FIND, model: 'opus', effort: 'high' }).then(x => (x ? x.findings : [])),
])
const findings = [...(sec || []), ...(ux || [])]

phase('Fix')
const fixed = findings.length === 0 ? 'no findings' : await agent(`${GROUND}
FIX every confirmed blocker/major and cheap minor finding test-first (re-verify each; skip with reason if not reproducible). Commit "fix(ask-your-docs): address tool-icon review findings".
FINDINGS: ${JSON.stringify(findings, null, 1)}`, { label: 'fix:icons', phase: 'Fix', model: 'opus', effort: 'high' })

phase('Gates')
const gates = await agent(`${GROUND}
Full ci.yml gate set from ${WT}/CLAUDE.md "Tests & Lint": ruff format --check + ruff check (python/ tests/ benchmarks/ scripts/), mypy python/pydocs_mcp, complexipy --max-complexity-allowed 15 (restore the snapshot), vulture 80, pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90, ~/.local/bin/uv lock --check, README audit grep. Return summary lines, \`git -C ${WT} log --oneline -5\`, clean status, GO/NO-GO.
Fix report: ${fixed}`, { label: 'gates:icons', phase: 'Gates', model: 'opus', effort: 'medium' })
return { impl, findings, fixed, gates }
