# HANDOFF — ci-fresh-install (nightly fresh-install job + pre-publish wheel gate)

## 2026-09-10 — steps 1-4 done, local only (not pushed)

Worktree: `/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/ci-fresh`
Branch: `ci/fresh-install-gate` off origin/main 5461d8e. No push, no PR, no tag.

Finished (4 commits, author msobroza, no trailers):
- 74f6056 `scripts/fresh_install_smoke.py` + `tests/test_fresh_install_smoke.py` (10 tests): index a one-module temp project,
  real MCP stdio (initialize, nine tools via list_tools, search_codebase must mention `fibonacci`), `--with-agent` imports
  `pydocs_mcp.harness.ask_your_docs.agent`, `--timeout` default 120 s per stage, `FRESH-INSTALL SMOKE FAILED:` + exit 1.
- 5ae8fa2 `.github/workflows/fresh-install.yml`: nightly cron `23 4 * * *` + workflow_dispatch, ubuntu, Python 3.11/3.13,
  fresh venv, `pip install ".[harness-ask-your-docs]"` (no lock or constraints, Rust via dtolnay/rust-toolchain@stable),
  smoke `--with-agent`, then `pip freeze --exclude pydocs-mcp` into pip-audit `--strict --disable-pip --no-deps` from its own venv,
  ignoring only the langchain-openai pair (GHSA-r7w7-9xr2-qq2r / PYSEC-2026-76; the WHY is in ci.yml).
- e10403b `release.yml`: new `smoke-wheel` job (needs `linux`) downloads `wheels-linux-x86_64`, installs
  `<wheel>[harness-ask-your-docs]` into a fresh unpinned venv and runs the smoke `--with-agent`; `publish` now needs it.
- b731bb1 CLAUDE.md Tests & Lint line + CHANGELOG `## [Unreleased]` / `### CI` entry.

Evidence (local, macOS arm64): both YAMLs pass yaml.safe_load; unit tests pass; ruff check/format are clean;
the smoke passes against the worktree's locked venv (index 5.2 s, mcp 2.0 s). A negative probe with an unmatched query fails
with a clear SmokeFailure line. An unpinned `pip install "<worktree>[harness-ask-your-docs]"` into a fresh 3.11 venv resolved
mcp 1.30.0 / langchain-openai 1.1.9; the smoke `--with-agent` passes there, and pip-audit on its freeze reports
"No known vulnerabilities found, 2 ignored".

Left:
- Neither workflow has run on GitHub yet: the Linux runner, Python 3.13 and the release job's abi3 single-wheel assertion
  are unproven.
- Pushing and opening a PR need the owner's word.

Exact next step: once the owner says to, push `ci/fresh-install-gate` and open a PR, then trigger
`fresh-install.yml` with workflow_dispatch on that branch (`gh workflow run fresh-install.yml --ref ci/fresh-install-gate`)
and check both matrix cells go green before merging.

## 2026-09-10 — review + fix pass

Reviewed `origin/main...HEAD` (YAML, Actions semantics, lock bypass, loud failure, secrets, publish gating, lint, tests).
Finished: cc49cba `ci: address review findings` (author msobroza, no trailer) on `ci/fresh-install-gate`:
- `find_pydocs_cli` now resolves only from this interpreter's scripts dir (`sysconfig.get_path("scripts")`); the old PATH
  fallback could let the gate pass against a different install. Two new tests pin it (16 passed with test_smoke_gate.py).
- `timeout-minutes` added: fresh-install job 45, release `smoke-wheel` 30 (an unpinned backtracking resolve no longer holds
  the job, and the publish it gates, for the 6h default).
Verified with no change needed: artifact name `wheels-linux-x86_64` matches linux's `wheels-linux-${{ matrix.target }}`;
`publish` needs `smoke-wheel`, which needs `linux`; cron valid; permissions `contents: read`; no secrets; pip ignores
`uv.lock` and `[tool.uv] constraint-dependencies`; abi3-py311 is set in Cargo.toml, so there is one wheel per target.
The search check is not vacuous: a probe over a corpus with no `fibonacci` got dense hits back (no query echo) and failed with
exit 1. ruff format --check + ruff check (python/ tests/ benchmarks/ scripts/) are green. The locked-venv smoke passes
(index 1.6 s, mcp 1.7 s); `--with-agent` fails loudly there only because that venv lacks the extra, which is expected.
Left: first GitHub runs (Linux, Python 3.13, the release single-wheel check); a push/PR needs the owner's word.
Non-blocking note: the 120 s per-stage default covers the first-run embedder download on CI; raise it with `--timeout` in the
workflows only if the first runs show it is tight.
Exact next step: on the owner's word, push `ci/fresh-install-gate`, open a PR, run
`gh workflow run fresh-install.yml --ref ci/fresh-install-gate` and confirm both matrix cells are green before merging.

## 2026-09-11 — merged origin/main (8c90bd55) into ci/fresh-install-gate (local only, not pushed)
- Merge commit e2958a8cc0c77ea41501b79d9a94069c9b898dba (parents fa8e1e10 + 8c90bd55), worktree scratchpad/ci-fresh.
- Only conflict: CHANGELOG.md. [Unreleased] = main's headline + Added/Changed/Deprecated byte-identical, then this branch's
  `### CI` fresh-install entry last (v0.5.0 heading order). Released sections byte-identical to main (diff verified).
- CLAUDE.md auto-merged: main's text + the fresh-install lines in "Tests & Lint". pyproject.toml/uv.lock came from main cleanly.
- Merge vs origin/main diff = only this branch's 6 files; complexipy-snapshot.json equals main.
- After `uv sync --frozen --group dev`: pytest tests/test_fresh_install_smoke.py tests/test_smoke_gate.py -q → 16 passed;
  `python scripts/fresh_install_smoke.py` → "fresh-install smoke passed" (mcp stdio ok 2.2s).
- Next: on the owner's word, push ci/fresh-install-gate to update PR #243.

## 2026-09-11 — merge verification (HEAD e2958a8c)
- Hunk audit, files changed on both sides since 5461d8e (CHANGELOG.md, CLAUDE.md): every +/- line from fa8e1e10 and from origin/main 8c90bd55 is present in diff(5461d8e,HEAD), with no extra lines. Branch-only files are byte-identical to fa8e1e10. HEAD differs from main only in the 6 files this branch owns. No follow-up commit needed.
- Gates: ruff format --check (1280 files) ok; ruff check ok; mypy ok (276 files); complexipy 15 rc=0 (snapshot restored); vulture 80 rc=0; pytest 4263 passed / 47 skipped / 1 xfailed, cov 97.02%; uv lock --check ok; README audit clean; both workflow YAMLs parse.
- Verdict: GO for a fast-forward push of ci/fresh-install-gate to #243 (owner's word required).
