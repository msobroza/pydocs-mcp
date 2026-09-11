export const meta = {
  name: 'ui-release-merge-main',
  description: 'Bring draft PR #244 (feat/ask-your-docs-activity-panel) up to date with main 8c90bd55 (#225 multilang reference graph, #245) via a merge commit — resolve CHANGELOG/README/CLAUDE.md/pyproject/uv.lock/default_config conflicts per repo rules, then verify and run the full gates',
  phases: [
    { title: 'Merge', detail: 'venv, merge origin/main, resolve conflicts, targeted relock' },
    { title: 'Verify', detail: 'no hunk dropped from either parent + full CI gates' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/ui-release'
const HANDOFF = '/Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-ui-release.md'
const GROUND = `Repo pydocs-mcp. Work ONLY in worktree ${WT} (branch feat/ask-your-docs-activity-panel = draft PR #244, head 07525986, pushed; based on the old main 5461d8e). main is now 8c90bd55: #245 (CLAUDE.md mcp pin) and #225 (multilanguage reference graph: tree-sitter analyzers, ADR 0022, contract amendments, new [multilang] grammar deps, default_config/CHANGELOG/README/CLAUDE.md edits). Never touch the main checkout or other worktrees; never push, tag, force anything or rewrite history (NO rebase — use a MERGE commit so the PR updates fast-forward); commit with the existing identity, NO trailers; restore complexipy-snapshot.json before staging; follow ${WT}/CLAUDE.md. TMPDIR=${S}/uimerge-tmp (mkdir). The /private/tmp scratchpad can be wiped on a restart — commit promptly. Append a dated entry to ${HANDOFF} before returning.
LOCKFILE RULES (repo memory): relock only with ~/.local/bin/uv (never anaconda uv); prefer the smallest diff; \`~/.local/bin/uv lock --check\` must pass. This branch's own lock/pyproject changes are only: streamlit floor >=1.43 → >=1.59 and langchain-openai >=0.2 → >=0.2,<2 (both in the ask-your-docs harness extra) — owner-approved.`

phase('Merge')
const merged = await agent(`${GROUND}
1. SETUP: \`mkdir -p ${S}/uimerge-tmp && cd ${WT} && [ -x .venv/bin/python ] || ~/.local/bin/uv sync --frozen --all-extras --python cpython-3.11-macos-aarch64-none\` (the venv was wiped; rebuild BEFORE the merge so it matches the branch, then re-sync after).
2. \`git fetch -q origin && git merge --no-ff --no-commit origin/main\`. Resolve every conflict:
   - CHANGELOG.md: ONE "## [Unreleased]" section containing BOTH sides' entries, grouped under Added/Changed/Fixed/CI headings as the file's style dictates; keep released sections byte-identical to main.
   - README.md, CLAUDE.md, DOCUMENTATION.md, default_config.yaml, tests/test_pyproject_extras.py: keep both sides' content (union), fixing only genuine overlaps; the README jargon rule applies.
   - pyproject.toml: main's content + this branch's two harness-extra pins.
   - uv.lock: take origin/main's uv.lock (\`git checkout --theirs\` is WRONG in a merge — use \`git show origin/main:uv.lock > uv.lock\`), then \`~/.local/bin/uv lock\` so only the two requires-dist lines (and anything they force) change; show \`git diff origin/main -- uv.lock | head -60\` and justify every changed line; \`~/.local/bin/uv lock --check\`.
3. Re-sync the venv to the merged lock (\`~/.local/bin/uv sync --frozen --all-extras --inexact\`), run tests/harness + tests/test_pyproject_extras.py + tests touching default_config and the analyzers quickly, then commit the merge ("Merge origin/main (8c90bd55) into feat/ask-your-docs-activity-panel").
Return: the conflicted files and how each was resolved, the uv.lock diff summary, test summary lines, the merge SHA.`, { label: 'merge', phase: 'Merge', model: 'opus', effort: 'high' })

phase('Verify')
const verified = await agent(`${GROUND}
VERIFY the merge commit (HEAD) independently:
(a) For every file changed on BOTH sides since the merge base 5461d8e, prove no hunk from either parent was dropped or mangled: compare \`git diff 5461d8e 07525986 -- <f>\` and \`git diff 5461d8e origin/main -- <f>\` against \`git diff 5461d8e HEAD -- <f>\`; report any loss and fix it in a follow-up commit ("fix: restore … lost in the main merge").
(b) Full CI gate set from ${WT}/CLAUDE.md "Tests & Lint": ruff format --check + ruff check (python/ tests/ benchmarks/ scripts/), mypy python/pydocs_mcp, complexipy --max-complexity-allowed 15 (restore the snapshot), vulture 80, pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90, ~/.local/bin/uv lock --check, the README audit grep; plus cargo fmt --check / clippy -D warnings / cargo test if src/ changed on main. If a gate fails because of the merge, fix minimally and commit; if it also fails on clean origin/main, prove it in a throwaway worktree ${S}/uimerge-main (remove after) and don't fix it.
Return: the per-file hunk verdicts, gate summary lines, \`git -C ${WT} log --oneline -4\`, clean status, GO/NO-GO for a fast-forward push to PR #244.
Merge report:\n${merged}`, { label: 'verify', phase: 'Verify', model: 'opus', effort: 'high' })
return { merged, verified }
