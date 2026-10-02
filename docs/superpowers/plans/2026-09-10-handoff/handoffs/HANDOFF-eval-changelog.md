
## 2026-09-10 — reviewer pass (read-only) on docs/eval-changelog-020

Worktree: /private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/eval-cl
Commits reviewed (off origin/main f9a1535): b415958 c1a3bcd b71e088 83ab170 805700b e4c28b9. Nothing pushed/tagged.

Verified OK:
- Bullet diff script (rv/bullets.py in scratchpad): exactly 21 eval bullets left the root CHANGELOG (20 from [0.6.0] + 1 from [0.6.1]); no product bullet was lost; older sections unchanged; link refs intact.
- Replacements match plan §3a/§3b; the pointers are in [0.6.0] (before ### Security) and in [0.6.1] (before ### Fixed).
- Independent sdist build from `git archive HEAD` gives CHANGELOG.md in the sdist, no tests/, a 220-file wheel, 4 Project-URL lines, and twine check --strict passes on both.
- Preflight: 16 tests pass; ruff check and format are clean.
- Vendor grep: the only added hits are `claude -p` / `claude --append-system-prompt` (moved verbatim from the released root text) and "OpenAI-compatible".

Findings (all minor, none blocking):
1. benchmarks/CHANGELOG.md L360-361 says "(0.1.x: `>=0.5.1`); `[ask]` already did". The `[ask]` extra is new in 0.2.0 (eval-v0.1.1 pyproject has no ask extra), so reword to "the new `[ask]` extra declares it too".
2. The [0.2.0] heading is dated 2026-09-10 and its compare link points at the eval-v0.2.0 tag. Both are correct only if the tag is cut today; otherwise re-date at tag time.
3. test_missing_default_fixture_names_path_and_flag uses monkeypatch.setattr with functools.partial, which bends the CLAUDE.md "named fakes, not monkey-patches" rule. Owner's call.

Next step: owner decides on findings 1-3 (fix 1 is a one-line edit plus an amend or new commit on the branch), then push/PR only on the owner's word.

## 2026-09-10 — review-findings fix pass on docs/eval-changelog-020

Worktree: /private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/eval-cl (base f9a1535). Nothing pushed/tagged/PR'd.

Finished — commit 8d298f2 "fix(eval): address review findings" (no trailers):
- F1 fixed: benchmarks/CHANGELOG.md floor entry now says "the new `[ask]` extra declares the same floor" (eval-v0.1.1 had no [ask]).
- F3 fixed: benchmarks/tests/optimize/preflight/test_report_and_cli.py test_missing_default_fixture_names_path_and_flag calls default_rollout_dir(anchor=...) under pytest.raises(FileNotFoundError); monkeypatch/functools/preflight_main import removed. Exit-2 mapping still covered by test_cli_bad_rollout_dir_exits_two.
- F2 NOT changed (owner's call): [0.2.0] dated 2026-09-10 + compare link to eval-v0.2.0 (tag not cut yet). Re-date or revert to Unreleased/...main if tagging slips.

Gates (all green): ruff format --check + ruff check (python/ tests/ benchmarks/ scripts/); product pytest 4243 passed/3 skipped/1 xfailed; eval pytest 2202 passed/1 skipped; uv build benchmarks/ + twine check --strict PASSED x2 (sdist has CHANGELOG.md, 0 tests/ files); README audit grep 0 hits. git status clean; branch = 7 commits over origin/main.

Notes: origin/main moved to 7c2d7ce (#237, touches CLAUDE.md audit lines only); `git merge-tree` HEAD vs origin/main is clean — rebase/update-branch before opening the PR. Disk had ~3 GB free (an ENOSPC hit once); scratchpad/evalbuild holds the built dists.

Next step: owner decides F2, then on the owner's word rebase onto origin/main, re-run gates, push and open the PR.
