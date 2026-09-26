export const meta = {
  name: 'ci-fresh-merge-main',
  description: 'Bring PR #243 (ci/fresh-install-gate) up to date with main 8c90bd55 via a merge commit — resolve the CLAUDE.md/CHANGELOG conflicts, verify no hunk dropped, full gates',
  phases: [
    { title: 'Merge', detail: 'recreate worktree + venv, merge origin/main, resolve conflicts' },
    { title: 'Verify', detail: 'hunk check vs both parents + full CI gates + smoke script' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/ci-fresh'
const HANDOFF = '/Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-ci-fresh-install.md'
const GROUND = `Repo pydocs-mcp. Branch ci/fresh-install-gate = PR #243 (head fa8e1e10, pushed; based on old main 5461d8e; adds scripts/fresh_install_smoke.py, .github/workflows/fresh-install.yml, a release.yml smoke-wheel gate, tests/test_fresh_install_smoke.py, one CLAUDE.md "Tests & Lint" line and a CHANGELOG [Unreleased] CI entry). main is now 8c90bd55 (#245 CLAUDE.md mcp pin; #225 multilanguage reference graph with its own CHANGELOG entries and CLAUDE.md edits). Work ONLY in worktree ${WT} (create it if missing: \`git -C ${R} worktree prune && git -C ${R} worktree add ${WT} ci/fresh-install-gate\`; venv: \`cd ${WT} && ~/.local/bin/uv sync --frozen --group dev --python cpython-3.11-macos-aarch64-none\`). Never touch the main checkout or other worktrees; never push, tag, force or rewrite history — use a MERGE commit; commit with the existing identity, NO trailers; restore complexipy-snapshot.json before staging; follow ${WT}/CLAUDE.md. TMPDIR=${S}/cifresh-tmp (mkdir). Append a dated entry to ${HANDOFF} before returning.`

phase('Merge')
const merged = await agent(`${GROUND}
Set up the worktree + venv, then \`git fetch -q origin && git merge --no-ff --no-commit origin/main\`. Resolve every conflict: CHANGELOG.md → one "## [Unreleased]" section with BOTH sides' entries in the file's heading style (this branch's CI entry under a CI heading), released sections byte-identical to main; CLAUDE.md → main's text + this branch's fresh-install line in "Tests & Lint"; anything else → union, fixing only genuine overlaps. If uv.lock/pyproject conflict (unexpected), take main's and explain. Re-sync the venv (\`~/.local/bin/uv sync --frozen --group dev\`), run \`pytest tests/test_fresh_install_smoke.py tests/test_smoke_gate.py -q\` and \`python scripts/fresh_install_smoke.py\`, then commit the merge ("Merge origin/main (8c90bd55) into ci/fresh-install-gate"). Return conflicted files + resolutions, test lines, merge SHA.`, { label: 'merge', phase: 'Merge', model: 'opus', effort: 'medium' })

phase('Verify')
const verified = await agent(`${GROUND}
VERIFY the merge (HEAD): (a) for each file changed on both sides since 5461d8e, prove no hunk from either parent was lost (compare \`git diff 5461d8e fa8e1e10 -- f\`, \`git diff 5461d8e origin/main -- f\` with \`git diff 5461d8e HEAD -- f\`); fix losses in a follow-up commit. (b) Full CI gate set from ${WT}/CLAUDE.md "Tests & Lint" (ruff format --check + ruff check on python/ tests/ benchmarks/ scripts/, mypy, complexipy 15 + restore snapshot, vulture 80, pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90, ~/.local/bin/uv lock --check, README audit grep); YAML-parse both workflow files. Return per-file verdicts, gate lines, \`git -C ${WT} log --oneline -3\`, clean status, GO/NO-GO for a fast-forward push to #243.
Merge report:\n${merged}`, { label: 'verify', phase: 'Verify', model: 'opus', effort: 'medium' })
return { merged, verified }
