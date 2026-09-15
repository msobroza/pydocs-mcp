export const meta = {
  name: 'ci-fresh-install-gate',
  description: 'Nightly fresh-install (no-lock) CI job + pre-publish wheel smoke gate in release.yml',
  phases: [{ title: 'Implement' }, { title: 'Review' }],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/ci-fresh'
const H = '/Users/msobroza/pydocs-handoffs/2026-09-10'
const HANDOFF = `Before returning, append a dated entry to ${H}/HANDOFF-ci-fresh-install.md: what you finished (SHAs), what is left, the exact next step. Never write secrets there.`
const GROUND = `Repo ${R} (pydocs-mcp). Work ONLY in worktree ${WT} on branch ci/fresh-install-gate, off origin/main:
  \`[ -d ${WT} ] || git -C ${R} worktree add -b ci/fresh-install-gate ${WT} origin/main\`; venv if needed: \`cd ${WT} && ~/.local/bin/uv sync --frozen --group dev --python cpython-3.11-macos-aarch64-none\`.
Never touch the main checkout or other worktrees; never push, tag, open PRs or publish; commit with the existing identity and NO trailers; stage explicit paths; restore complexipy-snapshot.json before staging; follow ${WT}/CLAUDE.md. Use \`rtk proxy git\` when a git failure must be visible.
WHY (owner-approved): CI installs only from uv.lock, so the mcp 2.x break (mcp 2.x removed mcp.server.fastmcp; a fresh \`pip install pydocs-mcp\` crashed serve) reached PyPI unseen. The owner chose: a NIGHTLY fresh-install job AND a PRE-PUBLISH gate. ${HANDOFF}`

phase('Implement')
const impl = await agent(`${GROUND}
TASK (read .github/workflows/ci.yml and release.yml first; match their style, pinned action versions and WHY comments):
1. scripts/fresh_install_smoke.py — stdlib + the installed package only, ~<150 lines, functions 4-20 lines: create a tiny temp project
   (one documented module), run \`pydocs-mcp index <tmp> --skip-deps --cache-dir <tmp>/cache\`, then a real MCP stdio handshake with
   \`mcp.client.stdio.stdio_client\` + \`ClientSession\` against \`pydocs-mcp serve <tmp> --skip-deps --cache-dir <tmp>/cache\`
   (initialize, list_tools must return the nine tools, call search_codebase and require non-empty text), 120 s timeout, exit non-zero with a
   clear message on any failure; optional flag --with-agent that imports pydocs_mcp.harness.ask_your_docs.agent (needs the extra).
   Add a unit test for the pure helpers (tests/test_fresh_install_smoke.py) and put the script under the ruff gate like other scripts/.
2. .github/workflows/fresh-install.yml — schedule nightly (cron) + workflow_dispatch; ubuntu-latest, Python 3.11 and 3.13; fresh venv;
   \`pip install ".[harness-ask-your-docs]"\` from the checkout WITHOUT uv.lock or constraints (latest resolvable deps; maturin builds the
   Rust extension — follow how ci.yml provisions Rust); run the smoke script with --with-agent; then pip-audit --strict on that resolved
   environment (\`pip freeze\` → requirements). Failures are the signal; no auto-issue creation.
3. release.yml — a new job that needs the Linux x86_64 wheel build, downloads that artifact, installs the built wheel
   (\`<wheel>[harness-ask-your-docs]\`) into a fresh venv with NO constraints, checks out the repo only for the smoke script, and runs it with
   --with-agent; make the publish job depend on it so a broken wheel can never publish.
4. CLAUDE.md "Tests & Lint": one line naming the nightly job and the gate; CHANGELOG "## [Unreleased]" (create above [0.6.1] if absent)
   with a short CI entry.
Validate YAML (python -c yaml.safe_load), run the new unit test and \`python scripts/fresh_install_smoke.py\` locally against the worktree's
own install to prove it passes, then ruff. One commit per step. Return SHAs and evidence.`, { label: 'implement:ci-fresh', phase: 'Implement', model: 'opus', effort: 'high' })

phase('Review')
const review = await agent(`${GROUND}
REVIEW then FIX: check the diff (\`git -C ${WT} diff origin/main...HEAD\`) — YAML validity and GitHub Actions semantics (needs:, artifact names
match release.yml's upload names, permissions, cron syntax, matrix), that the install truly bypasses uv.lock, that the smoke fails loudly on a
missing tool or empty result, no secrets needed, publish really blocked by the gate, lint/format green, the unit test meaningful. Fix confirmed
problems and commit ("ci: address review findings"). Finally run ruff format --check + ruff check (python/ tests/ benchmarks/ scripts/) and the
new test; return summary lines, \`git -C ${WT} log --oneline origin/main..HEAD\`, clean status, GO/NO-GO for a PR.
Implementation report: ${impl}`, { label: 'review:ci-fresh', phase: 'Review', model: 'opus', effort: 'high' })
return { impl, review }
