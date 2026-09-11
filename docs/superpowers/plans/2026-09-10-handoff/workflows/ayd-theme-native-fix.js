export const meta = {
  name: 'ayd-theme-native-fix',
  description: 'ask-your-docs light/dark fix: native Streamlit light+dark palettes, drop the in-app toggle, contrast tests, visual check',
  phases: [{ title: 'Implement' }, { title: 'Review' }, { title: 'Gates' }],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
// Change S in a new session (the old scratch dir may be gone).
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/ui-release'
const H = '/Users/msobroza/pydocs-handoffs/2026-09-10'
const GROUND = `Work ONLY in worktree ${WT} (branch feat/ask-your-docs-activity-panel; if missing: \`git -C ${R} worktree add ${WT} feat/ask-your-docs-activity-panel\`; venv: \`cd ${WT} && ~/.local/bin/uv sync --frozen --all-extras --python cpython-3.11-macos-aarch64-none\`). Never touch the main checkout or other worktrees; never push, tag, open PRs or publish; commit with the existing identity and NO trailers; stage explicit paths; restore complexipy-snapshot.json before staging; follow ${WT}/CLAUDE.md. Before returning, append a dated entry to ${H}/HANDOFF-ui-release.md (what you finished with SHAs, what is left, the exact next step). Never print secrets.
BUG (owner report, reproduced on 0.6.1): in Light mode the chat text is unreadable (~1.1:1), inline code chips stay black, code-block syntax stays dark, sidebar dropdown/radio bits stay dark.
ROOT CAUSE: python/pydocs_mcp/harness/ask_your_docs/theme.py streamlit_theme_flags() pins Streamlit's native theme to DARK (--theme.base dark + dark colors) and the in-app "Light mode" toggle only swaps a partial CSS overlay (theme_css), so every native element it misses keeps dark-theme colors. Streamlit cannot switch its theme from Python.
OWNER DECISIONS: (1) remove the in-app Light-mode toggle; users switch with Streamlit's own theme menu (⋮ → Settings → Theme: Light / Dark / System) — make sure that entry is reachable (theme.py hides parts of the toolbar chrome); (2) this ships in the UI release branch.
VERIFIED (Streamlit 1.63): config supports [theme.light], [theme.dark], [theme.light.sidebar], [theme.dark.sidebar] (CLI flags --theme.light.<option> …); st.context.theme.type is "light"/"dark" (read-only, inferred from the background; can lag on first load or during a switch).`

phase('Implement')
const impl = await agent(`${GROUND}
TASK, test-first, one commit per step:
1. streamlit_theme_flags(): emit BOTH native palettes from THEMES (--theme.light.* and --theme.dark.*, sidebar variants where they help: primaryColor, backgroundColor, secondaryBackgroundColor, textColor, and link/code colors if 1.63 has them — check \`streamlit config show\`); stop pinning --theme.base. Tests: both palettes emitted; every text-on-background pair in THEMES is >= 4.5:1 (WCAG) in both palettes.
2. Remove render_appearance_toggle and the Light-mode session keys with their call sites (app.py, pages/2_Graph.py, tests). current_palette() follows st.context.theme.type (dark when unknown). Make Streamlit's theme picker reachable (un-hide only what is needed of the main menu; keep deploy/status hidden).
3. theme_css(): keep only accent/brand/bubble styling; it must never be needed for text readability.
4. examples/harness/ask_your_docs_agent/README.md: how to switch the theme; CHANGELOG [Unreleased] Fixed bullet.
Run tests/harness after each step. Return SHAs, RED/GREEN lines and deviations.`, { label: 'implement:theme', phase: 'Implement', model: 'opus', effort: 'high' })

phase('Review')
const review = await agent(`${GROUND}
REVIEW + VERIFY: (a) review the theme commits (\`git -C ${WT} log -p\` since the implement step) for correctness, leftovers of the toggle, CSS that still overrides text colors, lazy-import and line-budget rules; fix confirmed problems test-first and commit. (b) VISUAL CHECK: launch the app from ${WT}/.venv on port 8511 (\`harness-ask-your-docs --workspace ~/pydocs-openrouter/index --config ~/pydocs-openrouter/config.yaml --port 8511 -- --server.headless true\` with OPENROUTER_API_KEY loaded from ${R}/.env, never printed). If browser tools are available (ToolSearch "Claude_Browser"), switch Light and Dark through Streamlit's menu, ask one question, and confirm chat text, inline code, code blocks and the sidebar are readable in both; otherwise say the visual check is pending for the main session. Stop the server afterwards.
Implementation report: ${impl}`, { label: 'review:theme', phase: 'Review', model: 'opus', effort: 'high' })

phase('Gates')
const gates = await agent(`${GROUND}
Full ci.yml gate set from ${WT}: ruff format --check + ruff check (python/ tests/ benchmarks/ scripts/), mypy python/pydocs_mcp, complexipy python/pydocs_mcp --max-complexity-allowed 15 (restore the snapshot), vulture python/pydocs_mcp --min-confidence 80, pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90 -q, ~/.local/bin/uv lock --check. Return the summary lines, \`git -C ${WT} log --oneline origin/main..HEAD\`, a clean status and GO/NO-GO.`, { label: 'gates:theme', phase: 'Gates', model: 'opus', effort: 'high' })
return { impl, review, gates }
