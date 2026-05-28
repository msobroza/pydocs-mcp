# Final P2 follow-ups (88 → 95+) — design

**Status:** spec — ready for implementation planning
**Audit:** `IMPROVEMENT_SPEC_pydocs-mcp.md` (against `9e4143a`, score 88/100)
**Locked decisions:** Path A for P2-5; minimal scope for P2-4
**Related work:** PR #51 (16 audit findings, 32→88), PR #53 (audit follow-ups including the audit's P2-1 `uv sync --frozen`)

---

## 1. Goal

Close the remaining 4 P2 findings from the latest audit. Score target: 88 → ~96 (each P2 worth −1; the unfixable P2-5 hardcoded-version stays a known item documented elsewhere — though we partially address it via the release.yml sync).

The audit had 5 P2 findings. Its P2-1 (`uv sync --frozen`) was already landed by PR #53 commit `68b3f35`; the audit predates that. This spec covers the 4 remaining:

- **P2-2** — pip-audit in CI
- **P2-3** — `NullHandler` at package root
- **P2-4** — CONTRIBUTING.md + SECURITY.md
- **P2-5** — release.yml syncs version from git tag

## 2. Context

The repo just shipped 16 audit findings (PR #51) + 4 audit follow-ups (PR #53). These last 4 P2s are quick hygiene items, mostly mechanical.

Architectural touch surface is small and additive:
- `python/pydocs_mcp/__init__.py` (+2 lines)
- `.github/workflows/ci.yml` (new `security` job)
- `.github/workflows/release.yml` (new sync step)
- 2 new markdown files at repo root (CONTRIBUTING + SECURITY)
- ~50 LOC of new hygiene tests

No production code in `python/pydocs_mcp/` other than the `__init__.py` line changes.

## 3. Locked-in decisions

### Decision A — Path A for P2-5 version-from-tag

`release.yml` syncs version from the git tag at release time (`refs/tags/v*`). `sed -i` updates both `Cargo.toml` and `pyproject.toml` in the workflow's working tree. No new build deps; files stay 'unaware' between releases.

Alternatives considered + rejected:
- **Path B (dynamic version from Cargo.toml via maturin)** — cleaner long-term but requires touching every place that reads version metadata + verifying maturin 1.5+ behavior. Not worth the change cost when Path A handles the release-time process friction.
- **Path C (defer P2-5)** — drift class is already solved by `importlib.metadata` (`__version__` reads from installed metadata, PR #51). But the underlying friction (two files needing coordinated bumps) remains. Path A closes it.

### Decision B — Minimal scope for P2-4

Just `CONTRIBUTING.md` + `SECURITY.md`. Skip `CODE_OF_CONDUCT.md` and `.github/dependabot.yml`.

Rationale: the two essential docs cover the "external contributor first-touch" and "security researcher first-touch" entry points. CoC + dependabot are real value but additive; defer to a follow-up when there's contributor pressure to add them.

### Decision C — `pip-audit` as a sibling job, not a step

`security` job runs on `ubuntu-latest` alone, in parallel with `python` and `rust`. Doesn't bottleneck the matrix; doesn't depend on `setup-uv` + project install for the python job.

Two-step `uv export` + `uvx pip-audit` — avoids bash process substitution so it would Windows-compatible if we ever cross-OS this job.

`--strict` makes "no advisories found" the only passing outcome; advisories on indirect/transitive deps fail too.

### Decision D — `NullHandler` block placement

After the `importlib.metadata` `__version__` block, before the exception re-exports. Underscore-prefixed alias (`import logging as _logging`) so `logging` doesn't leak into `from pydocs_mcp import *`.

### Decision E — Authorship policy

Every commit on this branch is sole-authored by `msobroza`. NO `Co-Authored-By:` trailers. Standing global rule.

## 4. Scope

### 4.1 In scope

1. **pip-audit `security` job** in `ci.yml`. Two-step (`uv export` → `uvx pip-audit --strict --requirement requirements-audit.txt`).
2. **`NullHandler` block** in `python/pydocs_mcp/__init__.py` — 2 lines after the version block.
3. **`CONTRIBUTING.md`** at repo root. Sections: development setup (commands from Makefile), style + checks, PR expectations, link to SECURITY.md, link to Code of Conduct (placeholder).
4. **`SECURITY.md`** at repo root. Sections: supported versions, vulnerability-reporting flow (private GitHub advisories), response SLAs (72h ack / 7d confirm / 30d fix on high+).
5. **`release.yml` version sync step** — first step in each build job: extract tag, `sed -i` both files, gated on `if: startsWith(github.ref, 'refs/tags/v')`.
6. **New hygiene tests** pinning all 5 deliverables above.

### 4.2 Out of scope

- `CODE_OF_CONDUCT.md` — defer to a follow-up.
- `.github/dependabot.yml` — defer to a follow-up.
- mypy override graduation — still pending from PR #53; not part of this PR.
- The "PR-#47/#48/#52 open" backlog — separate efforts.
- Actually bumping the next release version — this PR ships the *infrastructure* for a future tag-driven release; the next tag push is the proving ground.

## 5. Components touched

| Component | Change |
|---|---|
| `python/pydocs_mcp/__init__.py` | +2 lines (NullHandler attach + underscored logging import) |
| `.github/workflows/ci.yml` | new `security` job (~12 lines) |
| `.github/workflows/release.yml` | new sync step in each build job (~10 lines per job) |
| `CONTRIBUTING.md` | NEW (~60 lines) |
| `SECURITY.md` | NEW (~25 lines) |
| `tests/test_repository_hygiene.py` | +5 new hygiene tests (~50 lines) |
| `CHANGELOG.md` | one "Unreleased → Added" block update |

Approximate LOC: **~160 total** (additive only).

## 6. Risks

### R1 — `pip-audit` may surface a real CVE on first CI run

The repo has 9 runtime deps + a Rust binding chain (turbovec, fastembed, openai, jinja2, pillow indirectly). One of these COULD have a known advisory.

**Mitigation:** if `pip-audit --strict` fails on the first CI run, the fix is one of:
- Bump the affected dep (preferred for direct deps where a patch exists).
- `--ignore-vuln <GHSA-...>` with an inline WHY comment for advisories that don't materially affect us (e.g., a vulnerability in a code path the project doesn't exercise).
- Pin a lower version (rare).

The audit explicitly accepted this as a known risk — landing pip-audit is the priority; cleaning up surfaced advisories is part of the regular maintenance cycle.

### R2 — `release.yml` sync only fires on tag pushes

Manual `workflow_dispatch` reruns of `release.yml` (e.g., to rebuild wheels for an existing release) won't trigger the sync because `if: startsWith(github.ref, 'refs/tags/v')` is false.

**Mitigation:** desired behavior — manual reruns SHOULDN'T mutate files; they should rebuild against whatever the files already say. Documented in a comment near the sync step.

### R3 — `NullHandler` is added eagerly at import time

Adding the handler runs on every `import pydocs_mcp`. Negligible cost (~microseconds) but worth pinning the test to confirm it lands without breaking the import lifecycle.

**Mitigation:** the new test imports `pydocs_mcp` and inspects the package logger; if anything goes wrong with the eager logging setup, the test fails loudly.

### R4 — CONTRIBUTING.md references `Makefile` targets that must exist

The doc says `make install`, `make test`, `make lint`, etc. All landed in PR #51 (Task 12 dev tooling bundle), so this is safe — but if the Makefile is ever refactored, the docs need updating.

**Mitigation:** the test pinning CONTRIBUTING.md presence also asserts the doc mentions `make install` and `make test`, so a future Makefile refactor that drops those targets fails the test.

## 7. Acceptance criteria

1. **AC-1 — pip-audit job in ci.yml** — sibling job on `ubuntu-latest`, uses `uv export --frozen` + `uvx pip-audit --strict`.
2. **AC-2 — NullHandler installed** — `logging.getLogger("pydocs_mcp").handlers` contains a `NullHandler` after `import pydocs_mcp`.
3. **AC-3 — CONTRIBUTING.md present** — mentions `make install`, `make test`, links to SECURITY.md.
4. **AC-4 — SECURITY.md present** — mentions GitHub advisories URL, response SLAs.
5. **AC-5 — release.yml syncs version from tag** — contains `refs/tags/v` guard + `sed -i` for both `Cargo.toml` and `pyproject.toml`.
6. **AC-6 — No internal jargon** in the new docs (no `PR #N`, `sub-PR`, `Task N of`, `RRF`, `FTS5`, `TurboQuant`).
7. **AC-7 — Authorship audit clean** — every commit sole-authored by `msobroza`, no `Co-Authored-By:` trailers.
8. **AC-8 — Full suite green** — pytest + ruff + ruff format --check + mypy + cargo all clean.
9. **AC-9 — pip-audit job actually runs on CI** — verified post-push (the new job appears in `gh pr checks` output).
10. **AC-10 — CHANGELOG.md updated** — `[Unreleased]` block lists the 4 new items.

## 8. Next step

Invoke `superpowers:writing-plans` to produce a 6-task TDD plan, then ship via `superpowers:subagent-driven-development` with the same workflow as PRs #49, #50, #51, #53. Per-task: implementer + one combined reviewer pass; final code review over the entire BASE..HEAD diff.
