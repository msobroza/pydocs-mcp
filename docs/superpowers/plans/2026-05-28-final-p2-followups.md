# Final P2 follow-ups — TDD plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan.

**Goal:** Close the remaining 4 P2 findings from the latest library audit
(`IMPROVEMENT_SPEC_pydocs-mcp.md`, base score 88/100, baseline at `1df4485`).
Target final score ~96/100. Spec at
`docs/superpowers/specs/2026-05-28-final-p2-followups-design.md`.

The four findings:

- **P2-2** — `pip-audit` job in `.github/workflows/ci.yml` (CVE coverage).
- **P2-3** — `NullHandler` attached at `python/pydocs_mcp/__init__.py` so the
  package-namespace logger is library-polite.
- **P2-4** — `CONTRIBUTING.md` + `SECURITY.md` at repo root (external-contributor
  + security-researcher first-touch surfaces).
- **P2-5** — `release.yml` syncs `pyproject.toml` + `Cargo.toml` `version`
  from the git tag (Path A from the audit; no maturin dynamic-version churn).

**Architecture:**

Touch surface is small and additive — no production logic in `python/pydocs_mcp/`
beyond a 2-line `__init__.py` block. The other artifacts live at the repo root
(`CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md` update) or in
`.github/workflows/`. All assertions land in the existing
`tests/test_repository_hygiene.py` so the hygiene test surface stays in one place
(established in PR #51).

**Tech Stack:**

- Python 3.11+, `pytest`, `pydocs_mcp` standard imports for the test side.
- GitHub Actions YAML for CI / release wiring (`astral-sh/setup-uv@v4`,
  `uvx pip-audit`, `PyO3/maturin-action@v1`).
- Plain Markdown for `CONTRIBUTING.md` / `SECURITY.md`.
- The standard repo gauntlet — `pytest -q`, `ruff check`, `ruff format --check`,
  `mypy`, `cargo fmt --check`, `cargo clippy -- -D warnings`, `cargo test`.

**Authorship policy (applies to every commit in this plan):** every commit on
this branch is sole-authored by `msobroza`. **NO `Co-Authored-By:` trailers** in
any commit message — global rule from `~/.claude/CLAUDE.md` overrides any
template that suggests otherwise.

**Worktree:** `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/audit-followups-2/`
on branch `feature/audit-followups-2`. Base: `main` at `1df4485` (PR #53 merge).

---

## Task 1 — Plan + spec commit (baseline)

**Why first:** records the implementation intent on the branch before any code
moves, so the very first diff has a stable reference point in `git log`. Also
audits the gauntlet baseline so later tasks can compare clean state vs.
regressions.

**Risks acknowledged:** none specific. This task only writes / commits Markdown.

### Step 1.1 — Verify baseline gauntlet (informational)

Run the lint + format + typecheck quartet to make sure `1df4485` itself is
green on this machine. If anything red surfaces, STOP and report — the rest of
the plan assumes a clean baseline.

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/audit-followups-2
uv run ruff check python/ tests/ benchmarks/
uv run ruff format --check python/ tests/ benchmarks/
uv run mypy python/pydocs_mcp
uv run pytest -q tests/
cargo fmt --check
cargo clippy -- -D warnings
cargo test --quiet
```

Expected: everything green.

If `uv sync --frozen` is required first because the `.venv` is stale, run
that, then retry the gauntlet. The `Cargo.lock` already exists; rust commands
do not need extra setup.

### Step 1.2 — Stage the spec + plan

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/audit-followups-2
git add docs/superpowers/specs/2026-05-28-final-p2-followups-design.md
git add docs/superpowers/plans/2026-05-28-final-p2-followups.md
git status
```

### Step 1.3 — Commit (NO `Co-Authored-By` trailer)

```bash
git commit -m "$(cat <<'COMMIT'
docs(plan): final P2 follow-ups — pip-audit, NullHandler, CONTRIBUTING/SECURITY, release.yml version sync

Spec + 6-task TDD plan to close the remaining 4 P2 findings from the
latest library audit (P2-2, P2-3, P2-4, P2-5). No production code in
this commit — plan + spec only. Each subsequent task lands one P2 item
behind a failing-test-first gate.
COMMIT
)"
```

**No `Co-Authored-By` trailer.** Verify with `git log -1 --format=%B HEAD`.

### Step 1.4 — Acceptance

- `git log -1 --format=%s` shows the docs commit.
- `git log -1 --format=%B HEAD` contains no `Co-Authored-By:` line.
- Both files appear in `git show --stat HEAD`.

---

## Task 2 — NullHandler in `__init__.py` (P2-3)

**Why this one is small and goes second:** smallest change in the plan
(2 lines of code, 1 test). Establishes the TDD rhythm — failing test FIRST,
verify it FAILS, then add the 2-line impl, verify it PASSES, commit.

**Risks acknowledged (R3 from the spec):** the handler is attached eagerly at
import time. Negligible cost (~microseconds), but the pinning test will catch
any future churn that breaks the import lifecycle. Asserting `NullHandler`
identity by class — not by checking `len(handlers) > 0` — keeps the test robust
if some external test fixture adds a handler later in the run.

### Step 2.1 — Write the failing test

Append to `tests/test_repository_hygiene.py` (existing file at line 331; the
new test goes at the end of the file). Exact text:

```python


def test_null_handler_attached() -> None:
    """P2-3: `pydocs_mcp` namespace logger has a NullHandler attached.

    Library hygiene — a logger without any handler emits a
    "no handlers could be found" warning when callers haven't configured
    logging themselves. Adding NullHandler keeps the package polite by
    default; downstream apps still see their own handlers.
    """
    import logging

    import pydocs_mcp  # noqa: F401 — import triggers handler attach

    logger = logging.getLogger("pydocs_mcp")
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers), (
        "pydocs_mcp namespace logger must have a NullHandler attached "
        "(P2-3 — library logging hygiene)"
    )
```

### Step 2.2 — Verify FAIL

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/audit-followups-2
uv run pytest tests/test_repository_hygiene.py::test_null_handler_attached -v
```

Expected: `FAILED` because `python/pydocs_mcp/__init__.py` does not yet attach
a NullHandler.

### Step 2.3 — Implement the 2-line attach

Edit `python/pydocs_mcp/__init__.py`. Insert immediately AFTER the
`importlib.metadata` `__version__` try/except block AND BEFORE the
`from pydocs_mcp.application.mcp_errors import (...)` line. Exact text to add:

```python

# Library-logging hygiene: attach a NullHandler so callers that haven't
# configured logging don't see "no handlers could be found" warnings. The
# underscored alias keeps `logging` out of `from pydocs_mcp import *`.
import logging as _logging

_logging.getLogger(__name__).addHandler(_logging.NullHandler())
```

The resulting file shape is: docstring → `__version__` block → blank line →
NullHandler block → blank line → exception re-exports → `__all__`.

### Step 2.4 — Verify PASS

```bash
uv run pytest tests/test_repository_hygiene.py::test_null_handler_attached -v
uv run pytest tests/test_repository_hygiene.py -q
uv run pytest -q tests/
```

Expected: green across the board.

### Step 2.5 — Lint + format + mypy gate

```bash
uv run ruff check python/ tests/
uv run ruff format --check python/ tests/ benchmarks/
uv run mypy python/pydocs_mcp
```

Expected: green.

### Step 2.6 — Commit (NO `Co-Authored-By`)

```bash
git add python/pydocs_mcp/__init__.py tests/test_repository_hygiene.py
git commit -m "$(cat <<'COMMIT'
chore(logging): attach NullHandler at pydocs_mcp package root (P2-3)

Library-logging hygiene — the package namespace logger now has a
NullHandler so callers that haven't configured logging never see the
"no handlers could be found" warning. Underscored alias keeps `logging`
out of `from pydocs_mcp import *`.

Test pins the attach so a future churn that drops it fails CI loudly.
COMMIT
)"
```

**No `Co-Authored-By` trailer.**

### Step 2.7 — Acceptance

- `tests/test_repository_hygiene.py::test_null_handler_attached` PASSES.
- `python/pydocs_mcp/__init__.py` shows the NullHandler block between
  the version block and the exception re-exports.
- AC-2 (NullHandler installed) is satisfied.

---

## Task 3 — `CONTRIBUTING.md` + `SECURITY.md` (P2-4)

**Why second-smallest:** two new Markdown files at repo root, plus two failing
tests pinning their presence + content. No production code touched.

**Risks acknowledged (R4 from the spec):** `CONTRIBUTING.md` references
`make install`, `make test`, etc. — those targets all exist (confirmed via
`grep -E "^[a-z-]+:" Makefile`: `install`, `test`, `test-rust`, `lint`,
`lint-rust`, `format`, `typecheck`, `build`, `clean`). The hygiene test
asserts both `make install` and `make test` appear in `CONTRIBUTING.md` so a
future Makefile refactor that drops those targets reds the test loudly.

**Jargon audit:** neither file may contain `PR #N`, `sub-PR`, `Task N of`,
`RRF`, `FTS5`, `TurboQuant`, `trilogy`. The hygiene test enforces this
(forbidden-substring scan).

### Step 3.1 — Write two failing tests

Append to `tests/test_repository_hygiene.py`. Exact text (both tests):

```python


def test_contributing_md_present() -> None:
    """P2-4: CONTRIBUTING.md exists at repo root and covers dev setup
    + style + PR expectations + security link.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    contrib = root / "CONTRIBUTING.md"
    assert contrib.is_file(), "CONTRIBUTING.md required at repo root (P2-4)"
    text = contrib.read_text(encoding="utf-8")

    # Dev setup commands (Makefile targets landed under repo-tooling work).
    assert "make install" in text, "CONTRIBUTING.md must document `make install`"
    assert "make test" in text, "CONTRIBUTING.md must document `make test`"
    # Style + checks pointers.
    assert "make lint" in text or "ruff" in text.lower()
    assert "make format" in text or "ruff format" in text.lower()
    # Security disclosure pointer.
    assert "SECURITY.md" in text, "CONTRIBUTING.md must link to SECURITY.md"

    # No internal jargon — CONTRIBUTING.md is end-user facing.
    forbidden = ("PR #", "sub-PR", "trilogy", "Task ", "RRF", "FTS5", "TurboQuant")
    for token in forbidden:
        assert token not in text, (
            f"CONTRIBUTING.md must not contain internal jargon {token!r}"
        )


def test_security_md_present() -> None:
    """P2-4: SECURITY.md exists at repo root with reporting flow + SLAs."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sec = root / "SECURITY.md"
    assert sec.is_file(), "SECURITY.md required at repo root (P2-4)"
    text = sec.read_text(encoding="utf-8")

    # GitHub private advisories URL anchors the reporting flow.
    assert "security/advisories" in text, (
        "SECURITY.md must reference the GitHub security advisories URL"
    )
    # Supported-versions section.
    lowered = text.lower()
    assert "supported versions" in lowered, "SECURITY.md must declare supported versions"
    # Response SLAs — the spec calls out 72h / 7d / 30d.
    assert "72" in text and "30" in text, (
        "SECURITY.md must declare the 72h / 30d response SLAs"
    )

    # No internal jargon.
    forbidden = ("PR #", "sub-PR", "trilogy", "Task ", "RRF", "FTS5", "TurboQuant")
    for token in forbidden:
        assert token not in text, (
            f"SECURITY.md must not contain internal jargon {token!r}"
        )
```

### Step 3.2 — Verify FAIL

```bash
uv run pytest tests/test_repository_hygiene.py::test_contributing_md_present \
              tests/test_repository_hygiene.py::test_security_md_present -v
```

Expected: both FAILED, since neither file exists yet.

### Step 3.3 — Write `CONTRIBUTING.md` at repo root

Create `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/audit-followups-2/CONTRIBUTING.md`
with the following exact content (uses the actual Makefile targets surveyed
in the baseline: `install`, `test`, `test-rust`, `lint`, `lint-rust`,
`format`, `typecheck`, `build`, `clean`):

```markdown
# Contributing to pydocs-mcp

Thanks for considering a contribution. This document covers the local
development loop, code style, and what we expect on a pull request.

## Development setup

```bash
# One-shot install (creates .venv, installs project + dev group from uv.lock).
make install

# Run the Python suite (1300+ tests).
make test

# Run the Rust suite (cargo test + clippy + fmt).
make test-rust
```

The project supports Python 3.11, 3.12, and 3.13 on Linux, macOS, and
Windows. CI tests the full matrix on every PR.

Optional acceleration: `maturin develop --release` compiles the Rust
extension in-place. The pure-Python fallback is functional without
Rust, so a missing compiler should never block contributing.

## Style and quality checks

```bash
# Format Python sources (ruff format).
make format

# Lint Python (ruff check) and Rust (cargo clippy + fmt).
make lint
make lint-rust

# Type-check the package.
make typecheck
```

All three must be clean before a PR can land. CI re-runs every check
on every push, so a green local run is the fast path; CI is the
authority.

## Pull request expectations

- **Tests.** Every behavior change ships with a failing test first,
  then the smallest change that makes it pass. Coverage gate stays at
  `--cov-fail-under=90`.
- **Cross-platform.** CI runs on Linux, macOS-13, macOS-14, and
  Windows. A PR that only proves it on Linux is incomplete — the
  matrix surfaces the rest.
- **`CHANGELOG.md`.** Add an entry under `## [Unreleased]` in the
  appropriate `Added` / `Changed` / `Fixed` block. Voice is past-tense
  and reader-facing — describe the behavior, not the internal task ID.
- **Commit authorship.** Commits are sole-authored by the contributor.
  No co-author trailers.

## Security

For vulnerability reports, see [SECURITY.md](SECURITY.md). Do not
open public issues for security problems — use the private advisory
flow instead.

## Code of Conduct

Until a separate Code of Conduct document lands, the short version is:
be respectful, focus on the code, and assume good intent. Security
disclosures still go through the SECURITY.md flow regardless of any
unrelated conduct issue.
```

### Step 3.4 — Write `SECURITY.md` at repo root

Create `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/audit-followups-2/SECURITY.md`
with the following exact content:

```markdown
# Security policy

## Supported versions

pydocs-mcp ships from the `main` branch and tags releases on PyPI.
Security patches land against:

- **Latest published minor** on PyPI — receives all fixes.
- **Previous minor** — receives critical-severity fixes for up to
  6 months after a new minor ships.

Older minors are out of support; upgrade to the latest published
release for security coverage.

## Reporting a vulnerability

**Do not open public GitHub issues for security reports.**

Use the private advisory flow:
<https://github.com/msobroza/pydocs-mcp/security/advisories>

Include: a description of the issue, the affected version, a
reproduction (or proof-of-concept), and any logs that help us confirm
the impact.

## Response SLAs

We aim for the following on reports filed via the advisory flow:

- **72 hours** — initial acknowledgement.
- **7 days** — triage confirmation (severity, affected versions,
  whether we can reproduce).
- **30 days** — fix shipped on `main` and tagged on PyPI for
  high-severity and critical issues. Lower-severity items follow the
  normal release cadence.

## Credit

Reporters are credited in the published advisory and in the
`CHANGELOG.md` entry that ships the fix, unless they ask to stay
anonymous. We coordinate disclosure timing with reporters before
making the advisory public.
```

### Step 3.5 — Verify PASS

```bash
uv run pytest tests/test_repository_hygiene.py::test_contributing_md_present \
              tests/test_repository_hygiene.py::test_security_md_present -v
uv run pytest -q tests/
```

Expected: both green.

### Step 3.6 — Lint + format + mypy gate

```bash
uv run ruff check python/ tests/
uv run ruff format --check python/ tests/ benchmarks/
uv run mypy python/pydocs_mcp
```

Expected: green. (Markdown files aren't linted by ruff, so the gate is
purely about not regressing the test additions.)

### Step 3.7 — Commit (NO `Co-Authored-By`)

```bash
git add CONTRIBUTING.md SECURITY.md tests/test_repository_hygiene.py
git commit -m "$(cat <<'COMMIT'
docs: add CONTRIBUTING.md + SECURITY.md (P2-4)

External-contributor first-touch + security-researcher first-touch
surfaces. CONTRIBUTING.md documents the make-based dev loop, style
gate, and PR expectations. SECURITY.md declares supported versions,
GitHub private-advisory flow, and 72h / 7d / 30d response SLAs.

Hygiene tests pin presence + reject internal jargon so neither file
can drift into being PR-history-flavoured documentation.
COMMIT
)"
```

**No `Co-Authored-By` trailer.**

### Step 3.8 — Acceptance

- `CONTRIBUTING.md` exists at repo root; mentions `make install`,
  `make test`, `make format`, links to `SECURITY.md`.
- `SECURITY.md` exists at repo root; mentions the GitHub advisories
  URL, supported-versions section, and the 72h / 30d SLAs.
- Two new hygiene tests PASS.
- AC-3, AC-4, AC-6 satisfied for these two files.

---

## Task 4 — `release.yml` version sync from git tag (P2-5, Path A)

**Why fourth:** larger YAML change than Task 3 but still purely additive.
Sets the stage for the next release-tag push to demonstrate the sync end-to-end
(R2 from the spec — the sync only fires on tag pushes, by design).

**Risks acknowledged (R2 from the spec):** manual `workflow_dispatch` reruns
of `release.yml` won't trigger the sync (the `if:` guard returns false).
That's the desired behavior — manual reruns rebuild against whatever the
in-tree files already say. The `if:` guard + an inline `# WHY:` comment near
each sync step document this.

**Survey result from baseline:** `release.yml` has four jobs that build
artifacts and need the sync — `linux`, `macos`, `windows`, `sdist`. The
`publish` job does not build, it only downloads + uploads, so it does NOT
need the sync step. Each of the four build jobs gets the same sync step as
its FIRST step after `actions/checkout@v4`.

### Step 4.1 — Write the failing test

Append to `tests/test_repository_hygiene.py`. Exact text:

```python


def test_release_yml_syncs_version_from_tag() -> None:
    """P2-5: release.yml extracts the version from refs/tags/v* and
    rewrites pyproject.toml + Cargo.toml in each build job's working tree.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    rel_yml = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    # The sync step name pins a stable anchor.
    assert "Sync version from tag" in rel_yml, (
        "release.yml must contain a named `Sync version from tag` step (P2-5)"
    )
    # The guard prevents non-tag workflow_dispatch reruns from rewriting files.
    assert "refs/tags/v" in rel_yml, (
        "release.yml sync step must be gated on `refs/tags/v`"
    )
    # Both files participate in the sync.
    assert "Cargo.toml" in rel_yml, "release.yml sync must touch Cargo.toml"
    assert "pyproject.toml" in rel_yml, "release.yml sync must touch pyproject.toml"
    # `sed` is the documented Path A mechanism.
    assert "sed" in rel_yml, "release.yml sync uses sed (Path A from the audit)"

    # The sync must appear in every build job (linux, macos, windows, sdist) —
    # otherwise one build artifact ships with the in-tree version while the
    # others ship the tag version. Count occurrences of the step name.
    occurrences = rel_yml.count("Sync version from tag")
    assert occurrences >= 4, (
        f"release.yml must run the sync step in every build job (linux, macos, "
        f"windows, sdist); found {occurrences} occurrences of `Sync version from tag`"
    )
```

### Step 4.2 — Verify FAIL

```bash
uv run pytest tests/test_repository_hygiene.py::test_release_yml_syncs_version_from_tag -v
```

Expected: FAILED — `release.yml` does not yet contain the sync step.

### Step 4.3 — Edit `release.yml`: add sync as the first step after checkout in EACH build job

Apply the exact step block below to `.github/workflows/release.yml`, inserting
it immediately AFTER `- uses: actions/checkout@v4` in each of the four build
jobs (`linux`, `macos`, `windows`, `sdist`). The step is identical in every
job. Inline `# WHY:` comment documents R2.

Block to insert (with one leading blank line for readability between the
checkout and the new step):

```yaml
      # WHY: sync the in-tree version from the pushed tag so the wheel /
      # sdist artifact ships with the tag's version, not whatever value
      # `pyproject.toml` and `Cargo.toml` happen to have at HEAD. Gated on
      # refs/tags/v so manual workflow_dispatch reruns (used to rebuild
      # wheels for an existing release) leave the files alone.
      - name: Sync version from tag
        if: startsWith(github.ref, 'refs/tags/v')
        run: |
          VERSION=${GITHUB_REF#refs/tags/v}
          sed -i.bak "s/^version = .*/version = \"$VERSION\"/" Cargo.toml
          sed -i.bak "s/^version = .*/version = \"$VERSION\"/" pyproject.toml
          rm -f *.bak
        shell: bash
```

Critical placement detail: the `Sync version from tag` step must come BEFORE
`PyO3/maturin-action@v1` (and before `command: sdist` in the sdist job),
because maturin reads the version from `Cargo.toml` / `pyproject.toml` at
build time. Putting the sync after the build would be a no-op.

Specific lines to insert at (matching the surveyed `release.yml`):

- `linux` job: after the `actions/checkout@v4` line (~line 19).
- `macos` job: after the `actions/checkout@v4` line (~line 44).
- `windows` job: after the `actions/checkout@v4` line (~line 61).
- `sdist` job: after the `actions/checkout@v4` line (~line 78).

Use the Edit tool four times — once per job. The unique anchor in each
case is the line just below `- uses: actions/checkout@v4` (which differs by
job). If the Edit tool reports a collision (the checkout line is not unique
across jobs), use `replace_all=true` ONLY if every checkout occurrence in
the file is followed by a `setup-python` step (it is) and the desired
behavior is to insert the same sync block after every checkout — but
prefer adding more surrounding context (e.g., the next `setup-python`
line) to disambiguate per-job.

The Windows job needs `shell: bash` because the runner defaults to PowerShell
and the `sed` invocation uses bash-only `${GITHUB_REF#refs/tags/v}` parameter
expansion. The block above already pins `shell: bash` for all four jobs to
keep the step text identical.

### Step 4.4 — Verify PASS

```bash
uv run pytest tests/test_repository_hygiene.py::test_release_yml_syncs_version_from_tag -v
uv run pytest -q tests/
```

Expected: green.

### Step 4.5 — Lint + format + mypy gate

```bash
uv run ruff check python/ tests/
uv run ruff format --check python/ tests/ benchmarks/
uv run mypy python/pydocs_mcp
```

Expected: green (no Python source changed; the gate is the regression check).

### Step 4.6 — YAML syntax sanity-check

```bash
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```

Expected: no exception. (`yaml` is a transitive dep of `pydocs_mcp` via
`pydocs_mcp.config` so it's already installed in the venv.)

### Step 4.7 — Commit (NO `Co-Authored-By`)

```bash
git add .github/workflows/release.yml tests/test_repository_hygiene.py
git commit -m "$(cat <<'COMMIT'
ci(release): sync version from git tag in every build job (P2-5)

A pushed `v*` tag is now the single source of truth for the published
version. Each build job (linux, macos, windows, sdist) runs a `Sync
version from tag` step before maturin reads the in-tree files, so the
artifact ships with the tag's version regardless of what pyproject.toml
and Cargo.toml say at HEAD.

Path A from the audit — sed-based rewrite, no maturin dynamic-version
churn. Gated on refs/tags/v so manual workflow_dispatch reruns rebuild
against the in-tree files without rewriting them.

Hygiene test pins the four-job coverage so a future PR can't silently
drop the sync from one job and ship a stale version on that platform.
COMMIT
)"
```

**No `Co-Authored-By` trailer.**

### Step 4.8 — Acceptance

- `.github/workflows/release.yml` contains four `Sync version from tag`
  steps (one per build job).
- Each step is gated on `if: startsWith(github.ref, 'refs/tags/v')`.
- Each step uses `sed -i.bak` to rewrite both `Cargo.toml` and
  `pyproject.toml`.
- AC-5 satisfied.

---

## Task 5 — `pip-audit` `security` job in `ci.yml` (P2-2)

**Why fifth:** depends on knowing the `uv.lock` is canonical (PR #51's
P2-8 work; PR #53's `uv sync --frozen` work). New sibling job in `ci.yml`,
parallel to `python` and `rust`. Per spec Decision C: a separate job, not a
step, so the per-OS python matrix doesn't carry the audit weight 12 times
over.

**Risks acknowledged (R1 from the spec):** `pip-audit --strict` may surface
a real CVE on first run. Three remediation paths in the spec:

1. Bump the affected dep (preferred for direct deps with a patch).
2. `--ignore-vuln <GHSA-...>` with an inline WHY comment for advisories that
   don't materially affect us.
3. Pin a lower version (rare).

The audit explicitly accepts this as a known risk — landing the job is
priority; cleaning surfaced advisories is part of the regular maintenance
cycle. If `pip-audit` reds on the first push, the implementer should record
the GHSA IDs surfaced + recommended mitigation in the PR description and
defer the actual fix to a follow-up PR (this PR ships the *infrastructure*).

### Step 5.1 — Write the failing test

Append to `tests/test_repository_hygiene.py`. Exact text:

```python


def test_pip_audit_job_present() -> None:
    """P2-2: ci.yml defines a `security` job that runs pip-audit against
    the locked deps (uv export + uvx pip-audit --strict).
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ci_yml = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    # Job header — pin both the canonical name and the runner choice.
    assert "\n  security:\n" in ci_yml or "security:\n    runs-on:" in ci_yml, (
        "ci.yml must define a top-level `security` job (P2-2)"
    )
    # Sibling-to-python pattern: ubuntu-latest, no OS matrix expansion.
    assert "pip-audit" in ci_yml, "ci.yml must invoke pip-audit"
    assert "uvx pip-audit" in ci_yml, (
        "ci.yml must invoke `uvx pip-audit` (matches the established uv tooling)"
    )
    assert "--strict" in ci_yml, (
        "pip-audit must run with `--strict` so every advisory reds the job"
    )
    # Two-step pattern from spec Decision C: export the locked deps, then audit.
    assert "uv export" in ci_yml, (
        "ci.yml security job must export the locked deps before auditing"
    )
    assert "--frozen" in ci_yml, "uv export must use --frozen for lockfile fidelity"
```

### Step 5.2 — Verify FAIL

```bash
uv run pytest tests/test_repository_hygiene.py::test_pip_audit_job_present -v
```

Expected: FAILED — `security:` job does not yet exist in `ci.yml`.

### Step 5.3 — Append the `security` job to `ci.yml`

Edit `.github/workflows/ci.yml`. Add a new top-level job AFTER the existing
`rust:` job (~line 197 in the baseline). Exact text to append (keep the
single trailing newline that the file already ends with):

```yaml

  security:
    # WHY: pip-audit on every CI run pins the dependency security surface.
    # `--strict` makes "no advisories found" the only passing outcome — any
    # advisory on a direct or transitive dep fails the job. A separate
    # sibling job (not a per-row step in `python`) keeps the multi-OS
    # matrix from carrying the audit cost 12 times.
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      # WHY: `uv export --frozen --no-emit-project` produces a
      # requirements-txt feed from uv.lock without the project itself
      # (pip-audit only cares about the declared deps, not the package
      # being built). `--no-emit-project` is uv's flag for that.
      - name: Export locked deps for audit
        run: |
          uv export --frozen --no-emit-project --format requirements-txt > requirements-audit.txt

      # WHY: uvx fetches pip-audit on demand; no need to add it to the
      # project's dev group just for CI. `--requirement` consumes the
      # exported feed; `--strict` makes any advisory a hard failure.
      - name: Run pip-audit
        run: uvx pip-audit --strict --requirement requirements-audit.txt
```

### Step 5.4 — Verify PASS

```bash
uv run pytest tests/test_repository_hygiene.py::test_pip_audit_job_present -v
uv run pytest -q tests/
```

Expected: green.

### Step 5.5 — Lint + format + mypy gate

```bash
uv run ruff check python/ tests/
uv run ruff format --check python/ tests/ benchmarks/
uv run mypy python/pydocs_mcp
```

Expected: green.

### Step 5.6 — YAML syntax sanity-check

```bash
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

Expected: no exception.

### Step 5.7 — Local dry-run of the export + audit (best-effort)

Optional but valuable: simulate what CI will do. If this surfaces a CVE,
record it for the PR description per R1.

```bash
uv export --frozen --no-emit-project --format requirements-txt > /tmp/requirements-audit-local.txt
uvx pip-audit --strict --requirement /tmp/requirements-audit-local.txt || true
rm -f /tmp/requirements-audit-local.txt
```

The `|| true` is intentional for the local dry-run — we record the result
but don't block this commit if a CVE surfaces. The CI run is the gate.
If CVEs surface, file an issue / note them in the PR description.

### Step 5.8 — Commit (NO `Co-Authored-By`)

```bash
git add .github/workflows/ci.yml tests/test_repository_hygiene.py
git commit -m "$(cat <<'COMMIT'
ci(security): add pip-audit job over uv-locked deps (P2-2)

A new `security` job runs alongside `python` and `rust` on every CI
run. It exports the locked deps from uv.lock and runs `uvx pip-audit
--strict --requirement requirements-audit.txt` so any known advisory
on a direct or transitive dep reds the job.

Sibling job (not a per-row step in the python matrix) keeps the
ubuntu-latest x python matrix from paying the audit cost 12 times.
COMMIT
)"
```

**No `Co-Authored-By` trailer.**

### Step 5.9 — Acceptance

- `.github/workflows/ci.yml` contains a top-level `security:` job.
- The job uses `astral-sh/setup-uv@v4`, `uv export --frozen
  --no-emit-project`, and `uvx pip-audit --strict --requirement
  requirements-audit.txt`.
- New hygiene test PASSES.
- AC-1 satisfied. AC-9 will be confirmed on PR push (gauntlet task).

---

## Task 6 — Verification gauntlet, CHANGELOG, AC matrix, PR

**Why last:** the only task that ships the work. Runs the full gauntlet
end-to-end across the whole BASE..HEAD diff, updates the changelog with all
four items (R1's potential CVE caveat included if applicable), pushes the
branch, opens the PR, and pins the AC matrix in the PR description.

**Risks acknowledged:** R1's CVE-surface gamble. If `pip-audit` reds the CI
on the first push, this task's verification gate exposes it. Two-paths:

1. If the CVE surface is trivially fixable (e.g., bump one dep that has a
   safe patch), land the fix on this branch as a 7th commit before merging.
2. If the CVE surface needs investigation / a `--ignore-vuln` annotation,
   note it in the PR description and ship; the audit explicitly accepted
   this as a known risk.

### Step 6.1 — Full Python gauntlet

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/audit-followups-2
uv run ruff check python/ tests/ benchmarks/
uv run ruff format --check python/ tests/ benchmarks/
uv run mypy python/pydocs_mcp
uv run pytest -q tests/
```

Expected: every check green. If any reds, FIX before continuing.

### Step 6.2 — Rust gauntlet

```bash
cargo fmt --check
cargo clippy -- -D warnings
cargo test --quiet
```

Expected: green. (Rust unchanged in this PR but the gauntlet still proves
no regressions snuck through dependency churn.)

### Step 6.3 — Benchmark suite smoke check

```bash
PYTHONPATH=benchmarks/src uv run pytest benchmarks/tests/ -q
```

Expected: green or "deselected" — the benchmark suite must continue to
pass since we touched nothing it depends on.

### Step 6.4 — Authorship audit

```bash
git log --format="%an <%ae> | %s" 1df4485..HEAD
git log --format="%B" 1df4485..HEAD | grep -i "co-authored-by" && echo "VIOLATION" || echo "clean"
```

Expected: every commit attributed to `msobroza`; the `grep` returns no
match and the line prints `clean`.

If any commit shows a `Co-Authored-By:` line, STOP and ask the user
before rewriting history.

### Step 6.5 — README jargon audit (project rule from CLAUDE.md)

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
    -not -path "*/node_modules/*" -not -path "*/.git/*" | \
    xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+" || \
    echo "README jargon audit: clean"
```

Expected: `README jargon audit: clean`. (This audit is unchanged from
PR #51 — we did not touch any README, but re-running it confirms.)

### Step 6.6 — Update `CHANGELOG.md` `[Unreleased]` block

Edit `CHANGELOG.md`. Under the existing `## [Unreleased]` section's `### Added`
block (which currently lists items like "MIT LICENSE file..." through
"This CHANGELOG.md"), add four new bullets immediately after the existing
`- This CHANGELOG.md.` line so the block reads chronologically. New
bullets to append:

```markdown
- `CONTRIBUTING.md` at the repository root — dev setup, style gate, PR
  expectations, link to `SECURITY.md`.
- `SECURITY.md` at the repository root — supported versions, GitHub
  private-advisory reporting flow, 72h / 7d / 30d response SLAs.
- `pip-audit` security job in CI — exports the uv-locked deps and runs
  `pip-audit --strict` so any known advisory on a direct or transitive
  dep reds the build.
- `release.yml` syncs the in-tree version from the pushed `v*` tag in
  every build job, so wheels and sdist always ship with the tag's
  version regardless of what `pyproject.toml` and `Cargo.toml` hold at
  HEAD.
```

And add one entry under `### Changed` (the NullHandler is a behavior
change, not a new feature) immediately after the existing
`- Ruff `select` expanded with ...` line:

```markdown
- `pydocs_mcp` namespace logger now has a `NullHandler` attached at
  package import, suppressing the "no handlers could be found" warning
  for callers that haven't configured logging.
```

### Step 6.7 — Re-run gauntlet after changelog edit

```bash
uv run ruff check python/ tests/ benchmarks/
uv run ruff format --check python/ tests/ benchmarks/
uv run mypy python/pydocs_mcp
uv run pytest -q tests/
```

Expected: green (Markdown changes don't affect any of these gates, but
the rerun confirms).

### Step 6.8 — Commit the changelog (NO `Co-Authored-By`)

```bash
git add CHANGELOG.md
git commit -m "$(cat <<'COMMIT'
docs(changelog): record final P2 follow-ups under [Unreleased]

CONTRIBUTING.md / SECURITY.md, pip-audit CI job, release.yml version
sync, and NullHandler attach — each lands its own behavior entry under
the appropriate Added / Changed block.
COMMIT
)"
```

**No `Co-Authored-By` trailer.**

### Step 6.9 — Push the branch

```bash
git push -u origin feature/audit-followups-2
```

### Step 6.10 — Open the PR

```bash
gh pr create \
  --title "chore(audit): close remaining 4 P2 findings (88 -> ~96)" \
  --body "$(cat <<'BODY'
## Summary

Closes the final four P2 findings from the latest library audit
(`IMPROVEMENT_SPEC_pydocs-mcp.md`, baseline 88/100 at `1df4485`).
Target final score ~96/100.

- **P2-2** — new `security` job in `.github/workflows/ci.yml` runs
  `pip-audit --strict` over the uv-locked dep set on every CI run.
- **P2-3** — `python/pydocs_mcp/__init__.py` attaches a `NullHandler`
  at the package namespace logger, killing the "no handlers could be
  found" warning for callers that haven't configured logging.
- **P2-4** — `CONTRIBUTING.md` + `SECURITY.md` at the repository root,
  covering external-contributor first-touch and security-researcher
  first-touch surfaces respectively.
- **P2-5** — `.github/workflows/release.yml` syncs the in-tree
  version from the pushed `v*` tag in every build job (linux, macos,
  windows, sdist), so wheels and sdist always ship with the tag's
  version regardless of what the in-tree files hold at HEAD.

Touch surface: ~160 LOC, additive only. No production code changed
beyond the 2-line NullHandler block.

## Acceptance criteria

| AC | Description | Status |
|----|-------------|--------|
| AC-1 | `security` job in ci.yml uses `uv export --frozen` + `uvx pip-audit --strict` | DONE |
| AC-2 | `NullHandler` attached at `pydocs_mcp.__init__` | DONE |
| AC-3 | `CONTRIBUTING.md` present, mentions `make install`/`make test`, links to SECURITY.md | DONE |
| AC-4 | `SECURITY.md` present, mentions advisories URL + 72h/7d/30d SLAs | DONE |
| AC-5 | `release.yml` syncs version from `refs/tags/v` in every build job | DONE |
| AC-6 | No internal jargon (no `PR #N`, `sub-PR`, `Task N of`, `RRF`, `FTS5`, `TurboQuant`) in new docs | DONE (hygiene tests enforce) |
| AC-7 | Authorship audit clean (no `Co-Authored-By:` trailers) | DONE |
| AC-8 | Full suite green (pytest + ruff + format-check + mypy + cargo) | DONE locally; CI on this PR is the canonical gate |
| AC-9 | `pip-audit` job actually runs on CI | Verified post-push via `gh pr checks` |
| AC-10 | `CHANGELOG.md` `[Unreleased]` updated with all four items | DONE |

## Risks acknowledged

- **R1** — `pip-audit --strict` may surface a real CVE on first CI run.
  See `pip-audit` job logs on this PR; if any advisory surfaces, the
  spec authorizes bump / `--ignore-vuln` with WHY comment / pin as
  remediation paths. The audit explicitly accepts surfacing as part
  of landing the job.
- **R2** — `release.yml` sync only fires on `refs/tags/v*` pushes.
  Manual `workflow_dispatch` reruns are intentionally exempt so they
  can rebuild against the in-tree files. Documented inline in the
  workflow step.
- **R3** — `NullHandler` attach runs on every `import pydocs_mcp`.
  ~microsecond cost; the pinning test catches any future churn that
  breaks the import lifecycle.
- **R4** — `CONTRIBUTING.md` references `make` targets that must
  exist. All four referenced targets (`install`, `test`, `lint`,
  `format`) are present in the Makefile; the hygiene test asserts
  `make install` and `make test` appear in the doc so a Makefile
  refactor that drops them reds CI.

## Test plan

- [x] `pytest -q tests/` — full Python suite green locally.
- [x] `ruff check` + `ruff format --check` — clean.
- [x] `mypy python/pydocs_mcp` — clean.
- [x] `cargo fmt --check` + `cargo clippy -- -D warnings` + `cargo test` — clean.
- [x] Authorship audit (`git log --format=%B 1df4485..HEAD | grep
      'Co-Authored-By' || echo clean`) — clean.
- [ ] CI matrix (Linux x86_64 + macOS x86_64/arm64 + Windows x64,
      Python 3.11 / 3.12 / 3.13) green on this PR.
- [ ] New `security` job green on this PR (R1 — see logs if it reds).
- [ ] `release.yml` syntax validates (next tag push is the proving ground).
BODY
)"
```

### Step 6.11 — Watch the CI

```bash
gh pr checks --watch
```

Expected: every check green. The new `security` job should appear in
the list — that's AC-9.

If `pip-audit` reds (R1), follow the spec's authorized remediation
paths or note + ship per the PR description and file the CVE as a
follow-up.

### Step 6.12 — Merge (after approval / clean CI)

```bash
gh pr merge --squash --delete-branch
```

### Step 6.13 — Final acceptance

- All 10 ACs satisfied (AC-1 through AC-10).
- All four spec deliverables landed: pip-audit job, NullHandler,
  CONTRIBUTING+SECURITY, release.yml sync.
- CHANGELOG `[Unreleased]` lists all four behaviors.
- PR merged, branch deleted, score moved from 88 → ~96.

---

## Coverage table (finding to task)

| Audit finding | Lands in task | Test that pins it |
|---------------|---------------|-------------------|
| P2-2 (pip-audit in CI) | Task 5 | `test_pip_audit_job_present` |
| P2-3 (NullHandler at __init__) | Task 2 | `test_null_handler_attached` |
| P2-4 (CONTRIBUTING + SECURITY) | Task 3 | `test_contributing_md_present`, `test_security_md_present` |
| P2-5 (release.yml version sync) | Task 4 | `test_release_yml_syncs_version_from_tag` |

## Per-task authorship pin (NO `Co-Authored-By:` trailer)

| Task | Commits | Authorship reminder embedded? |
|------|---------|-------------------------------|
| 1 | docs(plan) | YES — Step 1.3 commit message HEREDOC + post-commit verify |
| 2 | chore(logging) | YES — Step 2.6 + Step 6.4 audit |
| 3 | docs (CONTRIBUTING+SECURITY) | YES — Step 3.7 + Step 6.4 audit |
| 4 | ci(release) | YES — Step 4.7 + Step 6.4 audit |
| 5 | ci(security) | YES — Step 5.8 + Step 6.4 audit |
| 6 | docs(changelog) | YES — Step 6.8 + Step 6.4 final audit |

Step 6.4 is the final authorship audit over `1df4485..HEAD` and will fail
loudly if any commit carries a trailer.

