# CrossCommitVuln-Bench → QA Dataset Integration — Implementation Plan

> **Redaction note.** Identifiers and gold file paths in this document are
> SYNTHETIC. Real gold (CVE ids, contributing files) lives only under the
> `crosscommitvuln` package dir, which the `_EXCLUDED_DIRS` floor makes
> un-indexable — otherwise anyone indexing this checkout could retrieve the
> answer to an eval task from the docs. See
> `tests/extraction/test_config.py::test_no_shipped_cve_id_appears_in_an_indexable_text_file`.
> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** Integrate the CrossCommitVuln-Bench CVE annotation corpus as a QA-shaped needle-search dataset across two repositories — `pydocs-mcp-eval` (runtime loader, vendored records, combined dataset, optimizer config, leak guards, an exactness gate) and `coding-agent-playbook` (a non-ML-gated needle-search prompt plus paraphrase fixtures) — while keeping every model-facing string framing-free and the frozen nine-tool MCP surface untouched.

**Architecture:** On the `pydocs-mcp-eval` side: pure network-free construction helpers transform annotation JSON into vendored records (unbiased single-snapshot query + structured gold + banned-token ledger); a one-time network build tool pins pre-fix snapshots and runs the co-resident ancestry drop; a runtime loader reads the vendored records via `importlib.resources` and materializes a history-less corpus; a `CombinedDataset` unions swe-qa-pro + crosscommitvuln under prefixed task ids; an additive `gold_substring_all` gate + a combined optimizer config + per-prefix reporting keep the small slice visible; a `_EXCLUDED_DIRS` floor entry guarantees the gold answers are never indexed. On the `coding-agent-playbook` side (independent repo): a non-ML-gated `find-injected-vulnerability` prompt plus ten original paraphrase fixtures (review-only gate + 3-group rubric) with a shared CC BY attribution file.

**Tech Stack:** Python 3.11+ (`pydocs-mcp-eval`, `pytest`/`ruff`); Python 3.12+ (`coding-agent-playbook`, `uv`/`pytest`/`ruff`/`mypy --strict`); `importlib.resources` vendoring; git-checkout corpus via `RepoCache`; the shipped `skillopt` optimizer path.

## Global Constraints

- pydocs-mcp-eval: Python 3.11+; gates = `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q` + `ruff check python/ tests/ benchmarks/`; product-side floor tests under `tests/`. Vendored data ships in wheel AND sdist (release gate). NO third-party repo source in the wheel. The frozen nine-tool MCP surface is untouched (no new tool/param).
- coding-agent-playbook: Python 3.12+; gates = `uv run ruff check`, `uv run mypy --strict`, `uv run pytest --cov-fail-under=80`; hermetic eval run `playbook eval <id> --run --runner fake`.
- Dataset name / vendored dir / floor entry are the single identifier `crosscommitvuln` (never model-facing).
- Combined registry name `swe-qa-pro+crosscommitvuln`.
- Record count is a BOUND: 24 <= len(records) <= 33 (co-resident ancestry drop over the 9 multi-CVE-repo candidates).
- CC BY 4.0 attribution (Arunabh Majumdar; license URL; "transformed to QA"; arXiv:2604.21917; DOI 10.5281/zenodo.19338596) ships with the vendored data (NOTICE) and the playbook ATTRIBUTIONS.md.
- Every model-facing string (query template, prompt_args) is framing-free: no commit/temporal/benign/SAST language.
- The four locked decisions and the single-repo/single-commit invariant are inviolable.

## Rollout order

Land the three groups in order: **Group A** (pydocs-mcp construction helpers, vendored data, runtime loader, packaging, leak-floor, and the `gold_substring_all` gate) ships first because it defines the `crosscommitvuln` loader and the gate that later work consumes. **Group B** (`CombinedDataset`, the combined optimize config, and per-prefix reporting) depends on Group A having landed the registered `crosscommitvuln` dataset and the `gold_substring_all` gate. **Group C** (coding-agent-playbook prompt + fixtures) is fully independent — a different repository sharing no code — and can proceed in parallel with A/B; it lands as its own PR.

## Group A — pydocs-mcp crosscommitvuln dataset (loader + construction helpers + build tool + vendored data + packaging + leak-floor + gold_substring_all gate)

**Repo/cwd for ALL Group A tasks:** `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a` (the pydocs-mcp worktree).
**Gate commands for this repo:** benchmarks tests `PYTHONPATH=benchmarks/src pytest benchmarks/tests/… -q`; product tests `pytest tests/… -q`; lint `ruff check python/ tests/ benchmarks/` + `ruff format --check python/ tests/ benchmarks/`.

---

### Task A1 — Pure construction helpers `_crosscommitvuln_build.py`

**Files**
- Create: `benchmarks/src/pydocs_eval/datasets/_crosscommitvuln_build.py`
- Test: `benchmarks/tests/datasets/test_crosscommitvuln_build.py`

**Interfaces**
- Consumes: `GoldAnswer` from `pydocs_eval.datasets.base_dataset` (frozen dataclass: `ast_body: str|None`, `file_set: tuple[str,...]`, `extra: Mapping[str,object]`).
- Produces (LOCKED names): `is_included(annotation: dict) -> bool`, `mine_banned_tokens(annotation: dict) -> tuple[str, ...]`, `build_query(annotation: dict) -> str`, `assert_query_clean(query: str, banned: Sequence[str]) -> None` (raises `ValueError`), `build_file_set(annotation: dict) -> tuple[str, ...]`, `build_gold_extra(annotation: dict) -> dict[str, str]`, `build_mechanism(annotation: dict) -> str`, `gold_from_record(rec: dict) -> GoldAnswer`. Plus non-locked helper `repo_slug(annotation: dict) -> str` (used by the A5 build tool).
- **NOTE (review finding):** `build_gold_extra` exists purely as the tested spec of the `gold.extra` shape — it pins the `cve_id` + `cwe_id_N` mapping that the runtime `gold_from_record` mirrors. Neither the runtime loader (A4 uses `gold_from_record`, reconstructing `extra` from `gold.cwe_ids`) nor the network build tool (A5 `build_record` writes `cwe_ids` directly into the record's `gold`) calls `build_gold_extra` at runtime; it is exercised only by its own A1 unit test. This is deliberate: it is the single tested source of truth for the `cwe_id_N` mapping shape. (Vulture runs on `python/pydocs_mcp` only, not `benchmarks`, so this is not a gate failure.)

- [ ] **Step 1: Write the failing test file**

Create `benchmarks/tests/datasets/test_crosscommitvuln_build.py`:

```python
"""Pure construction-helper tests for the CrossCommitVuln QA transform
(design §5) — hermetic, synthetic annotation dicts only, no network."""

from __future__ import annotations

import copy
import logging

import pytest

from pydocs_eval.datasets._crosscommitvuln_build import (
    assert_query_clean,
    build_file_set,
    build_gold_extra,
    build_mechanism,
    build_query,
    gold_from_record,
    is_included,
    mine_banned_tokens,
    repo_slug,
)

# A tiny synthetic annotation shaped like a real CrossCommitVuln-Bench
# annotation.json (design §4.1). Fake CVE id on purpose: this dict is NOT
# vendored gold, so it may live inline in a .py test file (design §6.6).
_ANNOTATION: dict = {
    "cve_id": "CVE-2099-0001",
    "ghsa_id": "GHSA-xxxx-yyyy-zzzz",
    "repo": "https://github.com/exampleorg/exampleproj",
    "ecosystem": "PyPI",
    "cwe_ids": ["CWE-78"],
    "severity_combined": "high",
    "summary": "Exampleproj OS Command Injection via exec_cmd() with user input",
    "fix_commit": "a" * 40,
    "annotation_status": "complete+sast",
    "contributing_commits": [
        {
            "hash": "b" * 40,
            "short_hash": "bbbbbbbb",
            "date": "2024-01-15",
            "subject": "refactor exec_cmd path handling",
            "role": "SINK — exec_cmd(f-string) with user-controlled mailbox path",
            "files_changed": ["app/jobs.py", "app/models/mailbox.py"],
        },
        {
            "hash": "c" * 40,
            "short_hash": "cccccccc",
            "date": "2024-11-23",
            "subject": "wire additional inputs",
            "role": "SOURCE EXPANSION — wires user data into exec_cmd paths",
            "files_changed": ["app/sysutils.py"],
        },
    ],
    "vulnerability_chain": {
        "description": (
            "User-controlled mailbox path flows into exec_cmd which runs "
            "subprocess with shell=True."
        ),
        "attack_vector": "network",
    },
}


def _annotation(**overrides: object) -> dict:
    a = copy.deepcopy(_ANNOTATION)
    a.update(overrides)
    return a


def test_is_included_accepts_filled_chain() -> None:
    assert is_included(_ANNOTATION)


def test_is_included_rejects_skeleton_status() -> None:
    assert not is_included(_annotation(annotation_status="skeleton — pending"))
    assert not is_included(_annotation(annotation_status="SKIP — documented negative"))


def test_is_included_rejects_todo_or_empty_description() -> None:
    assert not is_included(_annotation(vulnerability_chain={"description": "TODO: fill"}))
    assert not is_included(_annotation(vulnerability_chain={"description": ""}))
    assert not is_included(_annotation(vulnerability_chain=None))


def test_mine_banned_tokens_covers_ids_files_symbols_dates_and_framing() -> None:
    tokens = mine_banned_tokens(_ANNOTATION)
    # cve/ghsa/cwe ids (CWE-78 AND bare 78)
    for expected in ("CVE-2099-0001", "GHSA-xxxx-yyyy-zzzz", "CWE-78", "78"):
        assert expected in tokens
    # fix + contributing hashes, full and short
    for expected in ("a" * 40, "a" * 8, "b" * 40, "bbbbbbbb", "c" * 40):
        assert expected in tokens
    # file paths AND basenames
    for expected in ("app/jobs.py", "jobs.py", "mailbox.py", "sysutils.py"):
        assert expected in tokens
    # sink/API symbols from role + subject + chain description
    for expected in ("exec_cmd", "shell=True", "subprocess"):
        assert expected in tokens
    # flaw-class keywords from summary + CWE names
    assert "command injection" in tokens
    # every commit date
    assert "2024-01-15" in tokens and "2024-11-23" in tokens
    # v2 framing vocabulary (design §5.2)
    for expected in ("commit", "commits", "multi-commit", "benign", "sast", "scanner"):
        assert expected in tokens


def test_mine_banned_tokens_drops_slug_collisions_and_logs(caplog) -> None:
    # Framing ban "across" occurs inside the repo slug -> repo identity wins:
    # the token is dropped (logged), NOT raised on later (design §5.2).
    a = _annotation(repo="https://github.com/across-net/across")
    with caplog.at_level(logging.INFO):
        tokens = mine_banned_tokens(a)
    assert "across" not in tokens
    assert any("across" in r.getMessage() and "slug" in r.getMessage() for r in caplog.records)
    # And the generated query (which contains the slug) stays clean.
    assert_query_clean(build_query(a), tokens)


def test_build_query_interpolates_only_slug_ecosystem_severity() -> None:
    query = build_query(_ANNOTATION)
    assert query == (
        "This snapshot of the PyPI project exampleorg/exampleproj contains a "
        "high-severity security vulnerability. Locate the exploitable "
        "condition: identify where untrusted input enters, the dangerous "
        "operation it reaches, and classify the vulnerability by its exploit."
    )
    assert repo_slug(_ANNOTATION) == "exampleorg/exampleproj"


def test_generated_query_is_clean_against_its_own_banned_tokens() -> None:
    assert_query_clean(build_query(_ANNOTATION), mine_banned_tokens(_ANNOTATION))


def test_assert_query_clean_raises_naming_the_leaking_token() -> None:
    banned = mine_banned_tokens(_ANNOTATION)
    leaking = "The flaw is exec_cmd in app/jobs.py, introduced gradually."
    with pytest.raises(ValueError, match="exec_cmd"):
        assert_query_clean(leaking, banned)


def test_assert_query_clean_is_word_boundary_aware_and_case_insensitive() -> None:
    with pytest.raises(ValueError, match="benign"):
        assert_query_clean("A Benign looking change", ("benign",))
    # "commit" must NOT fire inside "committee" (boundary), nor "78" inside "1978".
    assert_query_clean("The committee met in 1978.", ("commit", "78")) 


def test_build_file_set_unions_ordered_distinct() -> None:
    assert build_file_set(_ANNOTATION) == (
        "app/jobs.py",
        "app/models/mailbox.py",
        "app/sysutils.py",
    )


def test_build_file_set_falls_back_to_role_paths_then_raises() -> None:
    a = _annotation(
        contributing_commits=[
            {"hash": "d" * 40, "role": "SINK — taint reaches app/runner.py", "files_changed": []}
        ]
    )
    assert build_file_set(a) == ("app/runner.py",)
    empty = _annotation(
        contributing_commits=[{"hash": "d" * 40, "role": "SINK — no file", "files_changed": []}]
    )
    with pytest.raises(ValueError, match="CVE-2099-0001"):
        build_file_set(empty)


def test_build_gold_extra_keys() -> None:
    a = _annotation(cwe_ids=["CWE-78", "CWE-88"])
    assert build_gold_extra(a) == {
        "cve_id": "CVE-2099-0001",
        "cwe_id_0": "CWE-78",
        "cwe_id_1": "CWE-88",
    }


def test_build_mechanism_normalizes_whitespace() -> None:
    a = _annotation(vulnerability_chain={"description": "  source \n flows  to sink.  "})
    assert build_mechanism(a) == "source flows to sink."


def test_gold_from_record_maps_fields() -> None:
    rec = {
        "gold": {
            "cve_id": "CVE-2099-0001",
            "cwe_ids": ["CWE-78"],
            "mechanism": "source flows to sink.",
            "files": ["app/jobs.py", "app/sysutils.py"],
        }
    }
    gold = gold_from_record(rec)
    assert gold.ast_body == "source flows to sink."
    assert gold.file_set == ("app/jobs.py", "app/sysutils.py")
    assert gold.extra == {"cve_id": "CVE-2099-0001", "cwe_id_0": "CWE-78"}
```

- [ ] **Step 2: Run it, see it fail**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_crosscommitvuln_build.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'pydocs_eval.datasets._crosscommitvuln_build'`.

- [ ] **Step 3: Minimal implementation**

Create `benchmarks/src/pydocs_eval/datasets/_crosscommitvuln_build.py`:

```python
"""Pure construction helpers for the CrossCommitVuln QA dataset (design §5).

Deterministic, network-free transforms from a CrossCommitVuln-Bench
``annotation.json`` dict to vendored-record parts: the inclusion filter
(design §4.3), banned-token mining + query leak-check (design §5.2), the
unbiased single-snapshot query template, and the gold builders (design
§5.3). The network build tool (``benchmarks/tools/build_crosscommitvuln.py``)
composes these at construction time; the runtime loader reuses only
:func:`gold_from_record`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence

from .base_dataset import GoldAnswer

log = logging.getLogger(__name__)

# v2 framing bans (design §5.2): provenance / detection-difficulty vocabulary.
# Any of these in the query would leak that the flaw was assembled across
# commits or that it evades scanners — biasing the needle-search measurement.
_FRAMING_BANS: tuple[str, ...] = (
    "commit",
    "commits",
    "multiple",
    "multi-commit",
    "gradually",
    "over time",
    "across",
    "benign",
    "static analysis",
    "sast",
    "per-commit",
    "individually",
    "scanner",
)

# CWE id -> flaw-class phrases banned from the query (design §5.2). Unknown
# ids simply add no phrases; extend as construction meets new CWE classes.
_CWE_CLASS_KEYWORDS: dict[str, tuple[str, ...]] = {
    "CWE-22": ("path traversal", "directory traversal"),
    "CWE-73": ("path traversal", "file inclusion"),
    "CWE-78": ("os command injection", "command injection"),
    "CWE-79": ("cross-site scripting", "xss"),
    "CWE-89": ("sql injection",),
    "CWE-94": ("code injection",),
    "CWE-306": ("missing authentication",),
    "CWE-321": ("hard-coded key", "hardcoded key"),
    "CWE-347": ("signature bypass", "signature verification"),
    "CWE-502": ("deserialization", "unsafe deserialization"),
    "CWE-770": ("resource exhaustion",),
    "CWE-915": ("mass assignment",),
    "CWE-918": ("server-side request forgery", "ssrf"),
    "CWE-943": ("query injection", "nosql injection"),
}

# Single-word sink names that the code-shape regex below cannot catch
# (no underscore / dot); scanned against role + subject + chain text.
_SINK_VOCAB: tuple[str, ...] = (
    "subprocess",
    "eval",
    "exec",
    "pickle",
    "marshal",
    "os.system",
    "yaml.load",
    "shell",
)

# Code-like symbol tokens inside prose: dotted names (pickle.loads),
# snake_case calls (exec_cmd, doveadm_cmd), kwarg literals (shell=True).
_SYMBOL_RE = re.compile(
    r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b"
    r"|\b[A-Za-z]\w*_\w+\b"
    r"|\b\w+=(?:True|False|\w+)\b"
)

# ISO-ish and slashed date literals: 2024-01-15, 2024/01/15, 15-01-2024.
_DATE_RE = re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b")

# File-path tokens inside role prose — the build_file_set fallback source.
_FILE_TOKEN_RE = re.compile(r"[\w./-]+\.py\b")

# LOCKED template (design §5.2): interpolates ONLY ecosystem / slug / severity.
# Nothing temporal, nothing about commit structure or flaw class.
_QUERY_TEMPLATE = (
    "This snapshot of the {ecosystem} project {repo_slug} contains a "
    "{severity}-severity security vulnerability. Locate the exploitable "
    "condition: identify where untrusted input enters, the dangerous "
    "operation it reaches, and classify the vulnerability by its exploit."
)


def is_included(annotation: dict) -> bool:
    """First construction gate (design §4.3): filled-chain positives only."""
    desc = (annotation.get("vulnerability_chain") or {}).get("description", "") or ""
    return (
        annotation.get("annotation_status") == "complete+sast"
        and bool(desc)
        and not desc.startswith("TODO")
    )


def repo_slug(annotation: dict) -> str:
    """``org/name`` slug from the annotation's GitHub URL."""
    url = str(annotation.get("repo", "")).rstrip("/")
    parts = url.removesuffix(".git").split("/")
    if len(parts) < 2 or not parts[-1] or not parts[-2]:
        raise ValueError(f"invalid repo url: got {url!r}, expected …github.com/<org>/<name>")
    return f"{parts[-2]}/{parts[-1]}"


def mine_banned_tokens(annotation: dict) -> tuple[str, ...]:
    """Mine every token banned from this record's query (design §5.2)."""
    tokens: list[str] = []
    tokens += _id_tokens(annotation)
    tokens += _commit_tokens(annotation)
    tokens += _file_tokens(annotation)
    tokens += _symbol_tokens(annotation)
    tokens += _flaw_class_tokens(annotation)
    tokens += _date_tokens(annotation)
    tokens += _FRAMING_BANS
    return _drop_slug_collisions(_ordered_distinct(tokens), repo_slug(annotation))


def build_query(annotation: dict) -> str:
    """The unbiased single-snapshot question — LOCKED template (design §5.2)."""
    return _QUERY_TEMPLATE.format(
        ecosystem=annotation.get("ecosystem", "PyPI"),
        repo_slug=repo_slug(annotation),
        severity=str(annotation.get("severity_combined", "high")).lower(),
    )


def assert_query_clean(query: str, banned: Sequence[str]) -> None:
    """Raise ``ValueError`` naming the first banned token found in ``query``.

    Case-insensitive, word-boundary aware. Slug/token collisions never reach
    here: :func:`mine_banned_tokens` already dropped slug-colliding tokens
    (repo identity wins, design §5.2 precedence rule).
    """
    for token in banned:
        if _token_in_text(token, query):
            raise ValueError(
                f"query leaks banned token {token!r} in {query!r} — "
                "regenerate the query or fix the template (design §5.2)"
            )


def build_file_set(annotation: dict) -> tuple[str, ...]:
    """Gold files: union of ``contributing_commits[].files_changed``.

    Fallback (locked contract): file paths named in SOURCE/SINK role prose.
    Empty gold is a construction error — this dataset's gold is always
    non-empty (design §6.5), so fail loud with the offending cve_id.
    """
    commits = annotation.get("contributing_commits") or []
    primary = _ordered_distinct(f for c in commits for f in (c.get("files_changed") or []))
    if primary:
        return primary
    role_files = _ordered_distinct(
        m.group(0) for c in commits for m in _FILE_TOKEN_RE.finditer(str(c.get("role", "")))
    )
    if role_files:
        return role_files
    raise ValueError(
        f"no gold files derivable for {annotation.get('cve_id')!r}: "
        "contributing_commits[].files_changed empty and no .py path in role text"
    )


def build_gold_extra(annotation: dict) -> dict[str, str]:
    """Gate-candidate strings only — NO prose (both gates tokenize every value)."""
    extra = {"cve_id": str(annotation["cve_id"])}
    for i, cwe in enumerate(annotation.get("cwe_ids") or []):
        extra[f"cwe_id_{i}"] = str(cwe)
    return extra


def build_mechanism(annotation: dict) -> str:
    """The source→sink sentence — rides ``GoldAnswer.ast_body``, never ``extra``."""
    desc = (annotation.get("vulnerability_chain") or {}).get("description", "") or ""
    if not desc:
        raise ValueError(
            f"empty vulnerability_chain.description for {annotation.get('cve_id')!r}; "
            "is_included should have excluded this annotation"
        )
    return " ".join(desc.split())


def gold_from_record(rec: dict) -> GoldAnswer:
    """Vendored record -> ``GoldAnswer`` (runtime loader seam, design §5.3)."""
    gold = rec["gold"]
    extra: dict[str, object] = {"cve_id": str(gold["cve_id"])}
    for i, cwe in enumerate(gold.get("cwe_ids") or []):
        extra[f"cwe_id_{i}"] = str(cwe)
    return GoldAnswer(
        ast_body=gold["mechanism"],
        file_set=tuple(gold["files"]),
        extra=extra,
    )


def _id_tokens(annotation: dict) -> list[str]:
    tokens = [str(annotation.get("cve_id", "")), str(annotation.get("ghsa_id", "") or "")]
    for cwe in annotation.get("cwe_ids") or []:
        cwe_str = str(cwe)
        tokens += [cwe_str, cwe_str.removeprefix("CWE-")]
    return [t for t in tokens if t]


def _commit_tokens(annotation: dict) -> list[str]:
    tokens: list[str] = []
    fix = str(annotation.get("fix_commit", ""))
    if fix:
        tokens += [fix, fix[:8]]
    for commit in annotation.get("contributing_commits") or []:
        full = str(commit.get("hash", ""))
        if full:
            tokens += [full, full[:8]]
        if commit.get("short_hash"):
            tokens.append(str(commit["short_hash"]))
    return tokens


def _file_tokens(annotation: dict) -> list[str]:
    tokens: list[str] = []
    for commit in annotation.get("contributing_commits") or []:
        for path in commit.get("files_changed") or []:
            tokens += [path, path.rsplit("/", 1)[-1]]
    return tokens


def _symbol_tokens(annotation: dict) -> list[str]:
    commits = annotation.get("contributing_commits") or []
    text = " ".join(
        [str(c.get("role", "")) for c in commits]
        + [str(c.get("subject", "")) for c in commits]
        + [str((annotation.get("vulnerability_chain") or {}).get("description", ""))]
    )
    tokens = [m.group(0) for m in _SYMBOL_RE.finditer(text)]
    tokens += [w for w in _SINK_VOCAB if _token_in_text(w, text)]
    return tokens


def _flaw_class_tokens(annotation: dict) -> list[str]:
    summary = str(annotation.get("summary", "")).lower()
    tokens: list[str] = []
    for cwe in annotation.get("cwe_ids") or []:
        tokens += _CWE_CLASS_KEYWORDS.get(str(cwe), ())
    for phrases in _CWE_CLASS_KEYWORDS.values():
        tokens += [p for p in phrases if p in summary]
    return tokens


def _date_tokens(annotation: dict) -> list[str]:
    raw = [str(annotation.get("fix_commit_date", "") or "")]
    raw += [str(c.get("date", "") or "") for c in annotation.get("contributing_commits") or []]
    tokens: list[str] = []
    for value in raw:
        if value:
            tokens.append(value)
            tokens += _DATE_RE.findall(value)
    return tokens


def _drop_slug_collisions(tokens: tuple[str, ...], slug: str) -> tuple[str, ...]:
    # Precedence rule (design §5.2): the query must carry the repo slug; a
    # banned token that matches inside the slug would fail every query, so
    # repo identity wins — drop the token, log it, and let the mandatory
    # manual review pass inspect the record.
    kept: list[str] = []
    for token in tokens:
        if _token_in_text(token, slug):
            log.info(
                "crosscommitvuln build: banned token %r collides with repo slug %r "
                "— repo identity wins, token dropped from the ban list",
                token,
                slug,
            )
            continue
        kept.append(token)
    return tuple(kept)


def _token_in_text(token: str, text: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _ordered_distinct(items: Iterable[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        if item:
            seen.setdefault(item)
    return tuple(seen)
```

- [ ] **Step 4: Run, see it pass**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_crosscommitvuln_build.py -q
```

Expected: `14 passed`.

- [ ] **Step 5: Lint + format**

```bash
ruff check benchmarks/ && ruff format --check benchmarks/
```

Expected: `All checks passed!` (run `ruff format benchmarks/` first if formatting differs).

- [ ] **Step 6: Commit**

```bash
git add benchmarks/src/pydocs_eval/datasets/_crosscommitvuln_build.py \
        benchmarks/tests/datasets/test_crosscommitvuln_build.py
git commit -m "feat(eval): crosscommitvuln pure construction helpers — inclusion gate, banned-token mining, unbiased query template, leak-check, gold builders"
```

---

### Task A2 — Additive `gold_substring_all` gate

**Files**
- Modify: `benchmarks/src/pydocs_eval/optimize/rubric/gates.py`
- Test: `benchmarks/tests/optimize/test_rubric_gates.py`

**Interfaces**
- Consumes: `gate_registry: _Registry[GatePredicate]`, `EvalTask`, `TranscriptLike` (attrs `answer/tool_calls/turns/wall_seconds`), `params: Mapping[str, object]`.
- Produces (LOCKED): class `GoldSubstringAll`, registered `@gate_registry.register("gold_substring_all")`, `__call__(self, task: EvalTask, transcript: TranscriptLike, params: Mapping[str, object]) -> bool` — ALL candidates (`gold.file_set` + string values of `gold.extra`; optional `params["keys"]` restricts) verbatim in `transcript.answer`; empty candidates → `True`.

- [ ] **Step 1: Write failing tests**

In `benchmarks/tests/optimize/test_rubric_gates.py`, replace the `_SHIPPED_KINDS` tuple and the registry test (the registry returns `tuple(sorted(...))`, so the new kind sorts between `gold_substring` and `max_turns`):

```python
_SHIPPED_KINDS = (
    "answer_regex",
    "gold_substring",
    "gold_substring_all",
    "max_turns",
    "max_wall_seconds",
    "min_answer_chars",
    "used_indexed_tools",
)


def test_registry_ships_exactly_the_seven_kinds() -> None:
    assert gate_registry.names() == _SHIPPED_KINDS
```

(Delete the old `test_registry_ships_exactly_the_six_kinds`.) Then append after `TestGoldSubstring`:

```python
class TestGoldSubstringAll:
    """ALL-candidates exactness gate (design §6.5) — mirrors GoldSubstring."""

    _GOLD = {
        "file_set": ("app/jobs.py", "app/sysutils.py"),
        "extra": {"cve_id": "CVE-2099-0001", "cwe_id_0": "CWE-78"},
    }

    def test_all_candidates_present_passes(self) -> None:
        task = _task(**self._GOLD)
        answer = (
            "CVE-2099-0001 (CWE-78): taint enters app/jobs.py and reaches "
            "the shell wrapper in app/sysutils.py"
        )
        assert evaluate_gate(_check("gold_substring_all"), task, _Transcript(answer=answer))

    def test_one_missing_candidate_fails(self) -> None:
        task = _task(**self._GOLD)
        answer = "CVE-2099-0001 (CWE-78): the flaw is in app/jobs.py"  # sysutils missing
        assert not evaluate_gate(_check("gold_substring_all"), task, _Transcript(answer=answer))

    def test_any_gate_would_pass_where_all_fails(self) -> None:
        # The exactness contrast with the shipped ANY gate, pinned side by side.
        task = _task(**self._GOLD)
        transcript = _Transcript(answer="see app/jobs.py")
        assert evaluate_gate(_check("gold_substring"), task, transcript)
        assert not evaluate_gate(_check("gold_substring_all"), task, transcript)

    def test_keys_param_restricts_candidates(self) -> None:
        task = _task(**self._GOLD)
        check = _check("gold_substring_all", {"keys": ["file_set", "cve_id"]})
        answer = "CVE-2099-0001: app/jobs.py and app/sysutils.py"  # no CWE cited
        assert evaluate_gate(check, task, _Transcript(answer=answer))
        assert not evaluate_gate(check, task, _Transcript(answer="app/jobs.py only"))

    def test_empty_candidates_pass_vacuously(self) -> None:
        assert evaluate_gate(_check("gold_substring_all"), _task(), _Transcript(answer="anything"))

    def test_non_list_keys_param_fails_loud(self) -> None:
        check = _check("gold_substring_all", {"keys": "file_set"})
        with pytest.raises(TypeError, match="file_set"):
            evaluate_gate(check, _task(**self._GOLD), _Transcript())
```

- [ ] **Step 2: Run, see it fail**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/optimize/test_rubric_gates.py -q
```

Expected: `KeyError`/failures — `gold_substring_all` not registered, plus the registry-names test failing.

- [ ] **Step 3: Minimal implementation**

In `benchmarks/src/pydocs_eval/optimize/rubric/gates.py`, insert directly after the `GoldSubstring` class:

```python
@gate_registry.register("gold_substring_all")
@dataclass(frozen=True, slots=True)
class GoldSubstringAll:
    """EVERY gold candidate appears verbatim in the answer (design §6.5).

    Exact-identification sibling of :class:`GoldSubstring` (ANY): candidates
    are ``gold.file_set`` plus every string value in ``gold.extra``. Optional
    ``params["keys"]`` restricts tokenization — ``"file_set"`` selects the
    file set, any other entry selects that ``gold.extra`` key. An empty
    candidate set passes vacuously, mirroring the sibling (never reachable
    for crosscommitvuln, whose gold is always non-empty).
    """

    def __call__(
        self, task: EvalTask, transcript: TranscriptLike, params: Mapping[str, object]
    ) -> bool:
        candidates = _all_gate_candidates(task, params.get("keys"))
        return all(candidate in transcript.answer for candidate in candidates)


def _all_gate_candidates(task: EvalTask, keys: object) -> list[str]:
    """Resolve the gold_substring_all candidate list, honoring the keys filter."""
    if keys is None:
        candidates = list(task.gold.file_set)
        candidates += [v for v in task.gold.extra.values() if isinstance(v, str)]
        return candidates
    if not isinstance(keys, (list, tuple)):
        raise TypeError(
            f"gold_substring_all params['keys']: got {keys!r}, expected a list "
            "of gold keys ('file_set' or gold.extra key names)"
        )
    selected: list[str] = []
    for key in keys:
        if key == "file_set":
            selected += list(task.gold.file_set)
            continue
        value = task.gold.extra.get(str(key))
        if isinstance(value, str):
            selected.append(value)
    return selected
```

- [ ] **Step 4: Run, see it pass**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/optimize/test_rubric_gates.py -q
```

Expected: `23 passed` (17 pre-existing + 6 new).

- [ ] **Step 5: Full benchmarks suite + lint (guards other pins on gate kinds)**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q && ruff check benchmarks/ && ruff format --check benchmarks/
```

Expected: all passed; `All checks passed!`. If any other test pins the gate-kind list (e.g. a rubric-config test), update its expected tuple to include `gold_substring_all` in the same commit.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/src/pydocs_eval/optimize/rubric/gates.py benchmarks/tests/optimize/test_rubric_gates.py
git commit -m "feat(eval): additive gold_substring_all gate — ALL gold candidates verbatim, optional keys filter, vacuous-empty mirrors gold_substring"
```

---

### Task A3 — `_EXCLUDED_DIRS` leak-guard floor edit (product side)

**Files**
- Modify: `python/pydocs_mcp/extraction/config.py`
- Test: `tests/extraction/test_config.py`

**Interfaces**
- Consumes: `_EXCLUDED_DIRS: frozenset[str]`, `path_under_excluded(filepath: str, excluded: frozenset[str] = _EXCLUDED_DIRS) -> bool` (both already in `pydocs_mcp.extraction.config`).
- Produces: `"crosscommitvuln"` as a new floor member; three product tests including the repo-invariant scan.

- [ ] **Step 1: Write failing tests**

Append to `tests/extraction/test_config.py` (add `path_under_excluded` to the existing `from pydocs_mcp.extraction.config import …` line, and `import json` / `from pathlib import Path` at the top if not already imported):

```python
def test_crosscommitvuln_in_excluded_dirs_floor():
    """Design §6.6: the CrossCommitVuln QA dataset's vendored gold answers
    (records.jsonl / banned_tokens.jsonl) live under a ``crosscommitvuln``
    dir component; this floor entry makes them structurally un-indexable
    regardless of how the extension ceiling moves."""
    assert "crosscommitvuln" in _EXCLUDED_DIRS


def test_path_under_excluded_covers_vendored_crosscommitvuln_records():
    vendored = "benchmarks/src/pydocs_eval/datasets/data/crosscommitvuln/records.jsonl"
    assert path_under_excluded(vendored, excluded=_EXCLUDED_DIRS)
    # The naming gap the floor does NOT cover (design §6.6): a bare filename
    # component is not a dir component — fixtures must sit under the dir.
    assert not path_under_excluded("tests/fixtures/crosscommitvuln_mini.jsonl")


def test_gold_bearing_jsonl_files_sit_under_crosscommitvuln_component():
    """Repo invariant (design §6.6 fixture-placement rule): every JSONL file
    shaped like a vendored gold record (task_id + prefix_sha + gold keys)
    must live under a ``crosscommitvuln`` path component so the floor covers it."""
    repo_root = Path(__file__).resolve().parents[2]
    skip = {".git", ".venv", "node_modules", "__pycache__", ".claude", "target"}
    offenders: list[str] = []
    for path in repo_root.rglob("*.jsonl"):
        parts = set(path.relative_to(repo_root).parts)
        if parts & skip:
            continue
        first_line = _first_nonblank_line(path)
        if first_line is None:
            continue
        try:
            row = json.loads(first_line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        is_gold_record = isinstance(row, dict) and {"task_id", "prefix_sha", "gold"} <= row.keys()
        if is_gold_record and "crosscommitvuln" not in path.parts:
            offenders.append(str(path.relative_to(repo_root)))
    assert not offenders, f"gold-bearing JSONL outside a crosscommitvuln dir: {offenders}"


def _first_nonblank_line(path):
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip():
                    return line
    except OSError:
        return None
    return None
```

- [ ] **Step 2: Run, see the floor tests fail**

```bash
pytest tests/extraction/test_config.py -q
```

Expected: `2 failed` (`test_crosscommitvuln_in_excluded_dirs_floor`, `test_path_under_excluded_covers_vendored_crosscommitvuln_records`), invariant test passes vacuously (no gold JSONL exists yet), all pre-existing tests pass.

- [ ] **Step 3: Minimal implementation — the floor edit**

In `python/pydocs_mcp/extraction/config.py`, inside the `_EXCLUDED_DIRS` frozenset literal, add after `"site-packages",`:

```python
        # eval gold-answer key — never index; data-leak guard for the
        # CrossCommitVuln QA dataset. The vendored gold answers
        # (cve/cwe/mechanism/files) must never enter any pydocs-mcp index or
        # they would leak needle answers into retrieval results. ADR 0021
        # keeps widening ALLOWED_EXTENSIONS, so the extension ceiling is not
        # a durable guarantee — this floor is (design §6.6).
        "crosscommitvuln",
```

- [ ] **Step 4: Run product tests, see pass**

```bash
pytest tests/extraction/test_config.py -q && ruff check python/ tests/
```

Expected: all passed (including the 3 new tests); `All checks passed!`. Also run the full product gate before pushing this file: `pytest tests/ --ignore=tests/test_parity.py -q`.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/config.py tests/extraction/test_config.py
git commit -m "feat: add crosscommitvuln to the _EXCLUDED_DIRS floor — structural leak guard for vendored eval gold answers (design §6.6)"
```

---

### Task A4 — Runtime loader `crosscommitvuln.py` + registration + mini fixture + loader tests

**Files**
- Create: `benchmarks/src/pydocs_eval/datasets/crosscommitvuln.py`
- Create: `benchmarks/tests/fixtures/crosscommitvuln/mini.jsonl` (floor-covered dir — NEVER `crosscommitvuln_mini.jsonl`)
- Create: `benchmarks/tests/fixtures/crosscommitvuln/banned_tokens.jsonl`
- Modify: `benchmarks/src/pydocs_eval/datasets/__init__.py`
- Test: `benchmarks/tests/datasets/test_crosscommitvuln_loader.py`

**Interfaces**
- Consumes: `RepoCacheLike` (Protocol: `checkout(self, url: str, sha: str) -> Path`, `file_tree(self, url, sha) -> tuple[str, ...]`), `RepoCache`, `read_checkout_files(root: Path) -> dict[str, str]`, `materialize_corpus(files: Mapping[str, str], parent: Path | None = None) -> Path`, `EvalTask`, `gold_from_record` (A1), `dataset_registry`.
- Produces (LOCKED): `CrossCommitVulnDataset` registered `@dataset_registry.register("crosscommitvuln")` with fields `name: str = "crosscommitvuln"`, `revision: str = "1.0"`, `fixture_path: Path | None = None`, `repo_cache: RepoCacheLike = field(default_factory=RepoCache)`, `cache_dir: Path = field(default_factory=lambda: Path("~/.cache/pydocs-eval").expanduser())`, and `async def tasks(self) -> AsyncIterator[EvalTask]`.

- [ ] **Step 1: Commit the mini fixture (hand-written, 3 records: 2 valid + 1 malformed for the drop path)**

Create `benchmarks/tests/fixtures/crosscommitvuln/mini.jsonl` (one JSON object per line; `prefix_sha` values are fabricated fixture-only 40-hex — the fake repo cache never resolves them):

```jsonl
{"task_id": "cve-2026-27602", "repo_url": "https://github.com/modoboa/modoboa", "prefix_sha": "82d64bb9c1e2a3b4d5e6f708192a3b4c5d6e7f80", "fix_commit": "27a7aa133d3608fe8c25ae39125d1012c333cbfa", "query": "This snapshot of the PyPI project modoboa/modoboa contains a high-severity security vulnerability. Locate the exploitable condition: identify where untrusted input enters, the dangerous operation it reaches, and classify the vulnerability by its exploit.", "gold": {"cve_id": "CVE-2099-00018", "cwe_ids": ["CWE-78"], "mechanism": "A user-controlled mailbox path / email address flows into a custom shell wrapper that executes string arguments via subprocess with shell enabled; additional user-controlled inputs are wired into that same wrapper, widening the tainted surface.", "files": ["examplepkg/admin/jobs.py", "examplepkg/admin/models/inbox.py", "examplepkg/lib/sysutils.py", "examplepkg/webmail/models.py"]}, "metadata": {"ecosystem": "PyPI", "severity": "high", "commit_span_days": "313", "intro_window": "2024-01-15..2024-11-23", "fix_commit_date": "2025-01-08", "co_resident_cves": "", "source": "CrossCommitVuln-Bench (CC BY 4.0, Arunabh Majumdar); transformed to QA"}}
{"task_id": "cve-2026-26198", "repo_url": "https://github.com/collerek/ormar", "prefix_sha": "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567", "fix_commit": "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432", "query": "This snapshot of the PyPI project collerek/ormar contains a critical-severity security vulnerability. Locate the exploitable condition: identify where untrusted input enters, the dangerous operation it reaches, and classify the vulnerability by its exploit.", "gold": {"cve_id": "CVE-2099-00016", "cwe_ids": ["CWE-89"], "mechanism": "User-supplied filter values are interpolated into a raw query string instead of bound parameters, so tainted input reaches the database query executor unescaped.", "files": ["ormar/queryset.py"]}, "metadata": {"ecosystem": "PyPI", "severity": "critical", "commit_span_days": "120", "intro_window": "2023-05-02..2023-08-30", "fix_commit_date": "2023-11-12", "co_resident_cves": "", "source": "CrossCommitVuln-Bench (CC BY 4.0, Arunabh Majumdar); transformed to QA"}}
{"task_id": "cve-2099-9999", "repo_url": "https://github.com/example/broken", "prefix_sha": "deadbeef", "fix_commit": "deadbeef", "query": "malformed on purpose: short prefix_sha exercises the loader drop path", "gold": {"cve_id": "CVE-2099-9999", "cwe_ids": ["CWE-1"], "mechanism": "n/a", "files": ["a.py"]}, "metadata": {}}
```

Create `benchmarks/tests/fixtures/crosscommitvuln/banned_tokens.jsonl`:

```jsonl
{"task_id": "cve-2026-27602", "banned": ["CVE-2099-00018", "CWE-78", "78", "examplepkg/admin/jobs.py", "jobs.py", "mailbox.py", "sysutils.py", "exec_cmd", "shell=True", "subprocess", "command injection", "27a7aa133d3608fe8c25ae39125d1012c333cbfa", "27a7aa13", "2024-01-15", "2024-11-23", "2025-01-08", "commit", "commits", "multiple", "multi-commit", "gradually", "over time", "across", "benign", "static analysis", "sast", "per-commit", "individually", "scanner"]}
{"task_id": "cve-2026-26198", "banned": ["CVE-2099-00016", "CWE-89", "89", "ormar/queryset.py", "queryset.py", "sql injection", "filter_query", "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432", "9f8e7d6c", "2023-05-02", "2023-08-30", "2023-11-12", "commit", "commits", "multiple", "multi-commit", "gradually", "over time", "across", "benign", "static analysis", "sast", "per-commit", "individually", "scanner"]}
```

- [ ] **Step 2: Write the failing loader tests** (mirrors `test_swe_qa_loaders.py`, incl. `_FakeRepoCache`; `asyncio_mode="auto"` — no decorator needed)

Create `benchmarks/tests/datasets/test_crosscommitvuln_loader.py`:

```python
"""CrossCommitVuln dataset loader tests — hermetic via ``fixture_path`` +
a fake ``RepoCache`` (no network, no git). Gold-bearing fixtures live under
the floor-covered dir tests/fixtures/crosscommitvuln/ (design §6.6)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_eval.datasets._crosscommitvuln_build import assert_query_clean
from pydocs_eval.datasets.base_dataset import Dataset
from pydocs_eval.datasets.crosscommitvuln import CrossCommitVulnDataset
from pydocs_eval.registries import dataset_registry

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_MINI = _FIXTURES / "crosscommitvuln" / "mini.jsonl"
_BANNED = _FIXTURES / "crosscommitvuln" / "banned_tokens.jsonl"
# Reuse the existing checked-in fake corpus tree (no gold inside it).
_CORPUS_DIR = _FIXTURES / "swe_qa_corpus"

_SHA40 = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class _FakeRepoCache:
    """Stand-in for ``RepoCache`` — no git, no network."""

    corpus_dir: Path = field(default=_CORPUS_DIR)

    def checkout(self, url: str, sha: str) -> Path:
        return self.corpus_dir

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return ()


def _dataset() -> CrossCommitVulnDataset:
    return CrossCommitVulnDataset(fixture_path=_MINI, repo_cache=_FakeRepoCache())


def _raw_records() -> list[dict]:
    return [json.loads(line) for line in _MINI.read_text().splitlines() if line.strip()]


async def test_satisfies_dataset_protocol() -> None:
    assert isinstance(_dataset(), Dataset)


async def test_registered_and_buildable_with_fixture() -> None:
    ds = dataset_registry.build(
        "crosscommitvuln", fixture_path=_MINI, repo_cache=_FakeRepoCache()
    )
    assert isinstance(ds, CrossCommitVulnDataset)
    assert ds.name == "crosscommitvuln" and ds.revision == "1.0"


async def test_yields_tasks_with_gold_and_metadata() -> None:
    tasks = [t async for t in _dataset().tasks()]
    assert [t.task_id for t in tasks] == ["cve-2026-27602", "cve-2026-26198"]
    t0 = tasks[0]
    assert t0.gold.file_set == (
        "examplepkg/admin/jobs.py",
        "examplepkg/admin/models/inbox.py",
        "examplepkg/lib/sysutils.py",
        "examplepkg/webmail/models.py",
    )
    assert t0.gold.extra["cve_id"] == "CVE-2099-00018"
    assert t0.gold.extra["cwe_id_0"] == "CWE-78"
    assert t0.gold.ast_body and "shell" in t0.gold.ast_body
    assert t0.metadata["intro_window"] == "2024-01-15..2024-11-23"
    assert t0.metadata["fix_commit_date"] == "2025-01-08"
    assert t0.metadata["co_resident_cves"] == ""


async def test_malformed_record_dropped_and_counts_logged(caplog) -> None:
    with caplog.at_level(logging.INFO):
        tasks = [t async for t in _dataset().tasks()]
    assert len(tasks) == 2  # 3 fixture rows, 1 short-sha row -> excluded
    assert any(
        "1" in rec.getMessage() and "exclud" in rec.getMessage().lower()
        for rec in caplog.records
    )


async def test_single_repo_single_commit_invariant() -> None:
    # §5.0 hard invariant: one record <-> one repo_url <-> one 40-hex sha,
    # and the loader emits exactly one task per well-formed record.
    tasks = {t.task_id: t async for t in _dataset().tasks()}
    for rec in _raw_records():
        if not _SHA40.fullmatch(str(rec["prefix_sha"])):
            assert rec["task_id"] not in tasks  # malformed row never becomes a task
            continue
        assert isinstance(rec["repo_url"], str) and rec["repo_url"].count("github.com") == 1
        assert rec["task_id"] in tasks


async def test_temporal_metadata_never_in_query() -> None:
    async for task in _dataset().tasks():
        for key in ("intro_window", "fix_commit_date", "commit_span_days"):
            value = task.metadata[key]
            assert value and value not in task.query


async def test_query_clean_against_stored_banned_tokens() -> None:
    banned_by_id = {
        row["task_id"]: row["banned"]
        for row in (json.loads(line) for line in _BANNED.read_text().splitlines() if line.strip())
    }
    count = 0
    async for task in _dataset().tasks():
        assert_query_clean(task.query, banned_by_id[task.task_id])  # raises on any leak
        count += 1
    assert count == 2


async def test_mechanism_rides_ast_body_not_extra_and_gold_non_empty() -> None:
    async for task in _dataset().tasks():
        assert task.gold.file_set and task.gold.extra["cve_id"]  # never vacuous (design §6.5)
        assert task.gold.ast_body not in task.gold.extra.values()  # no prose in extra


async def test_corpus_source_materializes_history_less_checkout() -> None:
    tasks = [t async for t in _dataset().tasks()]
    corpus = tasks[0].corpus_source()
    assert (corpus / "src/qibo/models/variational.py").exists()
    assert not (corpus / ".git").exists()  # §5.0: history-less snapshot
```

- [ ] **Step 3: Run, see it fail**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_crosscommitvuln_loader.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'pydocs_eval.datasets.crosscommitvuln'`.

- [ ] **Step 4: Minimal implementation — the loader**

Create `benchmarks/src/pydocs_eval/datasets/crosscommitvuln.py`:

```python
"""CrossCommitVuln QA dataset loader (design §6.1) — vendored records,
lazy git-checkout corpus.

Mirrors ``swe_qa_pro.py`` with two deliberate differences: (a) records are
VENDORED and read via ``importlib.resources`` (resolves in a built wheel,
not just a source checkout) — no download, no on-disk cache; (b) the
checkout SHA is the pinned pre-fix parent (``prefix_sha``) stored in the
vendored record. Hard invariant (design §5.0): one record <-> one
``repo_url`` <-> one 40-hex ``prefix_sha``; the corpus is materialized
HISTORY-LESS (``read_checkout_files`` -> ``materialize_corpus``, no
``.git``), so no commit signal is observable by the model.
"""

from __future__ import annotations

import importlib.resources as ir
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..registries import dataset_registry
from ._crosscommitvuln_build import gold_from_record
from ._repo_cache import RepoCache, RepoCacheLike, read_checkout_files
from .base_dataset import EvalTask
from .corpus import materialize_corpus

log = logging.getLogger(__name__)

_SHA40 = re.compile(r"^[0-9a-f]{40}$")


@dataset_registry.register("crosscommitvuln")
@dataclass
class CrossCommitVulnDataset:
    """CrossCommitVuln-Bench transformed to QA (CC BY 4.0; see vendored NOTICE)."""

    name: str = "crosscommitvuln"
    revision: str = "1.0"
    fixture_path: Path | None = None
    # WHY: injected so tests pass a no-git/no-network fake; production wiring
    # gets the real ``RepoCache`` by default (one clone per repo across rounds).
    repo_cache: RepoCacheLike = field(default_factory=RepoCache)
    cache_dir: Path = field(
        default_factory=lambda: Path("~/.cache/pydocs-eval").expanduser(),
    )

    async def tasks(self) -> AsyncIterator[EvalTask]:
        records = self._read_records()
        vendored, dropped = 0, 0
        for rec in records:
            task = self._record_to_task(rec)
            if task is None:
                dropped += 1
                continue
            vendored += 1
            yield task
        # No-silent-caps: an operator must see the vendored count and how
        # many records were dropped as malformed (design §4.3).
        log.info(
            "crosscommitvuln: vendored %d task(s), excluded %d malformed record(s)",
            vendored,
            dropped,
        )

    def _read_records(self) -> list[dict[str, Any]]:
        if self.fixture_path is not None:
            text = self.fixture_path.read_text()
        else:
            text = (
                ir.files("pydocs_eval.datasets.data.crosscommitvuln")
                .joinpath("records.jsonl")
                .read_text()
            )
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def _record_to_task(self, rec: dict[str, Any]) -> EvalTask | None:
        url = rec.get("repo_url")
        sha = rec.get("prefix_sha")
        # §5.0 invariant, enforced by shape: exactly one repo URL string and
        # exactly one full 40-hex pinned snapshot per record.
        if not isinstance(url, str) or not url:
            log.info("crosscommitvuln: dropping %r — repo_url %r", rec.get("task_id"), url)
            return None
        if not isinstance(sha, str) or not _SHA40.fullmatch(sha):
            log.info(
                "crosscommitvuln: dropping %r — prefix_sha %r is not 40-hex",
                rec.get("task_id"),
                sha,
            )
            return None
        return EvalTask(
            task_id=rec["task_id"],
            query=rec["query"],
            gold=gold_from_record(rec),
            # Default-arg closure captures this record's pin; checkout + copy
            # happen lazily so a task that's never scored costs no clone.
            corpus_source=lambda u=url, c=sha: materialize_corpus(
                read_checkout_files(self.repo_cache.checkout(u, c))
            ),
            metadata=dict(rec.get("metadata", {})),
        )
```

- [ ] **Step 5: Register in `datasets/__init__.py`** (without this, `dataset_registry.build("crosscommitvuln")` raises `KeyError` — the `_populate_datasets()` seam only imports the package)

Modify `benchmarks/src/pydocs_eval/datasets/__init__.py`:

```python
from .base_dataset import Dataset
from .crosscommitvuln import CrossCommitVulnDataset
from .ds1000 import Ds1000Dataset
from .repoqa import RepoQADataset
from .structural_recall import StructuralRecallDataset
from .swe_qa import SweQaDataset
from .swe_qa_pro import SweQaProDataset

__all__ = [
    "CrossCommitVulnDataset",
    "Dataset",
    "Ds1000Dataset",
    "RepoQADataset",
    "StructuralRecallDataset",
    "SweQaDataset",
    "SweQaProDataset",
]
```

- [ ] **Step 6: Run, see it pass**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_crosscommitvuln_loader.py -q
```

Expected: `9 passed`.

- [ ] **Step 7: Full benchmarks suite + product repo-invariant re-run + lint**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
pytest tests/extraction/test_config.py -q   # mini.jsonl is a gold-bearing file: A3's invariant test must still pass (it sits under crosscommitvuln/)
ruff check benchmarks/ && ruff format --check benchmarks/
```

Expected: all passed — the invariant test now exercises its non-vacuous path against `benchmarks/tests/fixtures/crosscommitvuln/mini.jsonl`.

- [ ] **Step 8: Commit**

```bash
git add benchmarks/src/pydocs_eval/datasets/crosscommitvuln.py \
        benchmarks/src/pydocs_eval/datasets/__init__.py \
        benchmarks/tests/fixtures/crosscommitvuln/mini.jsonl \
        benchmarks/tests/fixtures/crosscommitvuln/banned_tokens.jsonl \
        benchmarks/tests/datasets/test_crosscommitvuln_loader.py
git commit -m "feat(eval): CrossCommitVulnDataset runtime loader — vendored records via importlib.resources, single-repo/single-commit invariant, lazy history-less corpus, floor-covered mini fixture"
```

---

### Task A5 — Packaging + NOTICE + network build tool + vendored data + count gate

**Files**
- Create: `benchmarks/src/pydocs_eval/datasets/data/__init__.py`, `benchmarks/src/pydocs_eval/datasets/data/crosscommitvuln/__init__.py`, `benchmarks/src/pydocs_eval/datasets/data/crosscommitvuln/NOTICE`
- Create: `benchmarks/tools/build_crosscommitvuln.py` (NOT shipped in the wheel)
- Create (by the build run): `benchmarks/src/pydocs_eval/datasets/data/crosscommitvuln/records.jsonl`, `…/banned_tokens.jsonl`
- Modify: `benchmarks/pyproject.toml`; Create: `benchmarks/MANIFEST.in`
- Test: `benchmarks/tests/tools/__init__.py`, `benchmarks/tests/tools/test_build_crosscommitvuln_tool.py`, `benchmarks/tests/datasets/test_crosscommitvuln_vendored.py`

**Interfaces**
- Consumes: A1 helpers (`is_included`, `mine_banned_tokens`, `build_query`, `assert_query_clean`, `build_file_set`, `build_gold_extra` (record shape uses `gold.cwe_ids` directly), `build_mechanism`, `repo_slug`), `RepoCache` (`checkout(url, sha) -> Path`; worktrees answer `git -C <dir> …`).
- Produces: `python benchmarks/tools/build_crosscommitvuln.py <clone-path>` writing the two vendored JSONLs; `[tool.setuptools.package-data]` entry `"pydocs_eval.datasets.data.crosscommitvuln" = ["*.jsonl", "NOTICE"]`; sdist coverage via `MANIFEST.in`; the `24 <= len(records) <= 33` gate.

- [ ] **Step 1: Create the vendored package dir + NOTICE (release-gate compliance item, design §4.2)**

Create `benchmarks/src/pydocs_eval/datasets/data/__init__.py` and `benchmarks/src/pydocs_eval/datasets/data/crosscommitvuln/__init__.py`, each containing:

```python
"""importlib.resources-addressable data package (design §6.2)."""
```

Create `benchmarks/src/pydocs_eval/datasets/data/crosscommitvuln/NOTICE`:

```
CrossCommitVuln-Bench — QA-transformed annotations
==================================================

The records in this directory (records.jsonl, banned_tokens.jsonl) are
derived from the CrossCommitVuln-Bench dataset annotations.

  Copyright 2026 Arunabh Majumdar
  License: Creative Commons Attribution 4.0 International (CC BY 4.0)
           https://creativecommons.org/licenses/by/4.0/

Changes (CC BY 4.0 change indication): the source annotations were
transformed into QA form — each record pairs an unbiased single-snapshot
question with a structured gold answer (CVE id, CWE ids, source-to-sink
mechanism, contributing files) and a pinned pre-fix commit snapshot.
No upstream repository source code is included.

Citation:
  Paper: arXiv:2604.21917
  Data DOI: 10.5281/zenodo.19338596
```

- [ ] **Step 2: Packaging — wheel AND sdist coverage**

In `benchmarks/pyproject.toml`, extend `[tool.setuptools.package-data]`:

```toml
[tool.setuptools.package-data]
"pydocs_eval.optimize.artifacts" = ["*.md"]
"pydocs_eval.optimize.configs" = ["*.yaml"]
"pydocs_eval.trajectory.configs" = ["*.yaml"]
"pydocs_eval.campaign.overlays" = ["*.yaml"]
"pydocs_eval.datasets.data.crosscommitvuln" = ["*.jsonl", "NOTICE"]
```

Create `benchmarks/MANIFEST.in` (sdist coverage is a release gate — setuptools does not reliably include package-data in sdists without it):

```
recursive-include src/pydocs_eval/datasets/data/crosscommitvuln *.jsonl NOTICE
```

- [ ] **Step 3: Write the failing tool unit tests**

Create `benchmarks/tests/tools/__init__.py` (empty) and `benchmarks/tests/tools/test_build_crosscommitvuln_tool.py`:

```python
"""Unit tests for the pure decision logic of the network build tool.
The tool is NOT an installed module — load it from its file path. Git and
network are stubbed; the heavy construction run is a documented one-time
step (design §6.3), not a test."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_TOOL = Path(__file__).parents[2] / "tools" / "build_crosscommitvuln.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("build_crosscommitvuln", _TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _annotation(cve_id: str) -> dict:
    return {
        "cve_id": cve_id,
        "repo": "https://github.com/exampleorg/exampleproj",
        "ecosystem": "PyPI",
        "cwe_ids": ["CWE-78"],
        "severity_combined": "high",
        "summary": "OS command injection via exec_cmd()",
        "fix_commit": "a" * 40,
        "annotation_status": "complete+sast",
        "contributing_commits": [
            {
                "hash": "b" * 40,
                "short_hash": "bbbbbbbb",
                "date": "2024-01-15",
                "subject": "refactor",
                "role": "SINK — exec_cmd with user input",
                "files_changed": ["app/jobs.py"],
            }
        ],
        "vulnerability_chain": {"description": "taint flows to exec_cmd sink."},
    }


def test_co_resident_cves_excludes_self_and_uses_predicate() -> None:
    tool = _load_tool()
    record = _annotation("CVE-2099-0001")
    siblings = [record, _annotation("CVE-2099-0002"), _annotation("CVE-2099-0003")]
    co = tool.co_resident_cves(
        record, siblings, is_assembled=lambda other: other["cve_id"] == "CVE-2099-0002"
    )
    # The drop path (design §5.2): CVE-0002 co-resides -> the caller DROPS
    # this record; self and non-assembled CVE-0003 are never reported.
    assert co == ("CVE-2099-0002",)


def test_build_record_shape_and_leak_check_wired() -> None:
    tool = _load_tool()
    record, banned_row = tool.build_record(_annotation("CVE-2099-0001"), "f" * 40)
    assert record["task_id"] == "cve-2099-0001"
    assert record["repo_url"] == "https://github.com/exampleorg/exampleproj"
    assert record["prefix_sha"] == "f" * 40
    assert record["gold"]["cve_id"] == "CVE-2099-0001"
    assert record["gold"]["cwe_ids"] == ["CWE-78"]
    assert record["gold"]["files"] == ["app/jobs.py"]
    assert record["gold"]["mechanism"] == "taint flows to exec_cmd sink."
    assert record["metadata"]["co_resident_cves"] == ""
    assert banned_row["task_id"] == "cve-2099-0001" and "exec_cmd" in banned_row["banned"]
    # The query the record carries already passed assert_query_clean inside
    # build_record; verify it re-passes against the stored ban list.
    from pydocs_eval.datasets._crosscommitvuln_build import assert_query_clean

    assert_query_clean(record["query"], banned_row["banned"])
```

Run, see it fail:

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/tools/ -q
```

Expected: failure — `benchmarks/tools/build_crosscommitvuln.py` does not exist.

- [ ] **Step 4: Implement the network build tool**

Create `benchmarks/tools/build_crosscommitvuln.py`:

```python
#!/usr/bin/env python3
"""Build the vendored CrossCommitVuln QA records (design §6.3). NOT shipped.

ONE-TIME NETWORK-HEAVY construction step run by the implementer: clones each
included CVE's repo once via RepoCache (~28 distinct repos; pytorch-scale
worst case), resolves and pins ``prefix_sha = fix_commit^``, verifies the
contributing commits are ancestors, runs the co-resident ancestry drop over
multi-CVE repos, generates + leak-checks every query, and writes
``records.jsonl`` + ``banned_tokens.jsonl`` into the floor-protected vendored
dir. The written artifacts are COMMITTED; re-running is idempotent.

Usage:
    cd <repo root>
    PYTHONPATH=benchmarks/src python benchmarks/tools/build_crosscommitvuln.py \
        /path/to/CrossCommitVuln-Bench
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from pydocs_eval.datasets._crosscommitvuln_build import (
    assert_query_clean,
    build_file_set,
    build_mechanism,
    build_query,
    is_included,
    mine_banned_tokens,
    repo_slug,
)
from pydocs_eval.datasets._repo_cache import RepoCache

log = logging.getLogger("build_crosscommitvuln")

_VENDORED_DIR = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "pydocs_eval"
    / "datasets"
    / "data"
    / "crosscommitvuln"
)
_GIT_TIMEOUT = 600
_SOURCE_ATTRIBUTION = (
    "CrossCommitVuln-Bench (CC BY 4.0, Arunabh Majumdar); transformed to QA"
)


def _git(checkout: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    return result.stdout.strip()


def load_annotations(source: Path) -> list[dict]:
    paths = sorted(source.glob("dataset/*/annotation.json"))
    if not paths:
        raise ValueError(
            f"no dataset/*/annotation.json under {source} — "
            "expected a CrossCommitVuln-Bench clone root"
        )
    return [json.loads(p.read_text()) for p in paths]


def resolve_prefix_sha(cache: RepoCache, url: str, fix_commit: str) -> tuple[Path, str]:
    """Checkout at fix_commit and pin its parent — the assembled pre-fix state."""
    checkout = cache.checkout(url, fix_commit)
    return checkout, _git(checkout, "rev-parse", f"{fix_commit}^")


def is_ancestor(checkout: Path, ancestor: str, descendant: str) -> bool:
    try:
        _git(checkout, "merge-base", "--is-ancestor", ancestor, descendant)
    except subprocess.CalledProcessError:
        return False
    return True


def contributing_hashes_present(checkout: Path, prefix_sha: str, annotation: dict) -> bool:
    return all(
        is_ancestor(checkout, commit["hash"], prefix_sha)
        for commit in annotation.get("contributing_commits") or []
    )


def chain_assembled_at(checkout: Path, prefix_sha: str, other: dict) -> bool:
    """True iff OTHER's full chain is present AND still unfixed at prefix_sha."""
    if not contributing_hashes_present(checkout, prefix_sha, other):
        return False
    return not is_ancestor(checkout, other["fix_commit"], prefix_sha)


def co_resident_cves(
    record_annotation: dict,
    siblings: list[dict],
    is_assembled: Callable[[dict], bool],
) -> tuple[str, ...]:
    """Other included CVEs of the same repo fully assembled at this snapshot."""
    return tuple(
        s["cve_id"]
        for s in siblings
        if s["cve_id"] != record_annotation["cve_id"] and is_assembled(s)
    )


def build_record(annotation: dict, prefix_sha: str) -> tuple[dict, dict]:
    """One annotation + pinned sha -> (vendored record, banned-token row)."""
    query = build_query(annotation)
    banned = mine_banned_tokens(annotation)
    assert_query_clean(query, banned)  # build-failing leak check (design §5.2)
    task_id = str(annotation["cve_id"]).lower()
    record = {
        "task_id": task_id,
        "repo_url": str(annotation["repo"]).removesuffix(".git"),
        "prefix_sha": prefix_sha,
        "fix_commit": annotation["fix_commit"],
        "query": query,
        "gold": {
            "cve_id": annotation["cve_id"],
            "cwe_ids": list(annotation.get("cwe_ids") or []),
            "mechanism": build_mechanism(annotation),
            "files": list(build_file_set(annotation)),
        },
        "metadata": _metadata(annotation),
    }
    return record, {"task_id": task_id, "banned": list(banned)}


def _metadata(annotation: dict) -> dict[str, str]:
    commits = annotation.get("contributing_commits") or []
    dates = sorted(str(c.get("date", "")) for c in commits if c.get("date"))
    return {
        "ecosystem": str(annotation.get("ecosystem", "")),
        "severity": str(annotation.get("severity_combined", "")),
        "commit_span_days": str(annotation.get("commit_span_days", "")),
        # Temporal fields are METADATA ONLY — never interpolated into the query.
        "intro_window": f"{dates[0]}..{dates[-1]}" if dates else "",
        "fix_commit_date": "",  # resolved from git in main()
        "co_resident_cves": "",  # empty by construction after the ancestry drop
        "source": _SOURCE_ATTRIBUTION,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(argv) != 2:
        print("usage: build_crosscommitvuln.py <path-to-CrossCommitVuln-Bench-clone>")
        return 2
    annotations = load_annotations(Path(argv[1]))
    included = [a for a in annotations if is_included(a)]
    log.info(
        "gate 1 (inclusion filter): %d included of %d annotation(s), %d excluded",
        len(included),
        len(annotations),
        len(annotations) - len(included),
    )
    by_repo: dict[str, list[dict]] = {}
    for a in included:
        by_repo.setdefault(repo_slug(a), []).append(a)

    cache = RepoCache()
    records: list[dict] = []
    banned_rows: list[dict] = []
    dropped_ancestry: list[str] = []
    dropped_broken: list[str] = []
    for a in included:
        cve = a["cve_id"]
        try:
            checkout, prefix_sha = resolve_prefix_sha(cache, str(a["repo"]), a["fix_commit"])
        except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.info("drop %s: prefix resolution failed (%s)", cve, exc)
            dropped_broken.append(cve)
            continue
        if not contributing_hashes_present(checkout, prefix_sha, a):
            log.info("drop %s: contributing commit missing at prefix_sha", cve)
            dropped_broken.append(cve)
            continue
        co = co_resident_cves(
            a,
            by_repo[repo_slug(a)],
            is_assembled=lambda other: chain_assembled_at(checkout, prefix_sha, other),
        )
        if co:  # gate 2 — co-resident ancestry DROP (design §5.2)
            log.info("drop %s: co-resident CVE(s) %s at %s", cve, ", ".join(co), prefix_sha[:12])
            dropped_ancestry.append(cve)
            continue
        record, banned_row = build_record(a, prefix_sha)
        record["metadata"]["fix_commit_date"] = _git(
            checkout, "show", "-s", "--format=%cs", a["fix_commit"]
        )
        records.append(record)
        banned_rows.append(banned_row)

    _write_jsonl(_VENDORED_DIR / "records.jsonl", records)
    _write_jsonl(_VENDORED_DIR / "banned_tokens.jsonl", banned_rows)
    log.info(
        "vendored %d record(s); ancestry-dropped %d (%s); broken-dropped %d (%s)",
        len(records),
        len(dropped_ancestry),
        ", ".join(dropped_ancestry) or "-",
        len(dropped_broken),
        ", ".join(dropped_broken) or "-",
    )
    log.info(
        "MANUAL REVIEW (hard v1 step, design §5.2): read every query in %s "
        "before committing — automated token mining has synonym blind spots",
        _VENDORED_DIR / "records.jsonl",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 5: Run the tool unit tests, see pass**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/tools/ -q
```

Expected: `2 passed`.

- [ ] **Step 6: Run the one-time NETWORK build (documented run — clones ~28 repos, pytorch-scale worst case; expect tens of minutes on first run; `RepoCache` reuses clones on re-runs)**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
PYTHONPATH=benchmarks/src python benchmarks/tools/build_crosscommitvuln.py \
    /path/to/CrossCommitVuln-Bench   # implementer supplies the local clone path
```

Expected log shape: `gate 1 (inclusion filter): 33 included of 71 annotation(s), 38 excluded`, per-drop lines for any ancestry/broken drops, then `vendored N record(s); ancestry-dropped M (…)` with `24 <= N <= 33` and `N + M + broken == 33`. Then perform the **mandatory manual review pass** (hard v1 step): read all N `query` values in `benchmarks/src/pydocs_eval/datasets/data/crosscommitvuln/records.jsonl` and confirm none names a file, sink, flaw class, date, or commit/scanner framing. If any does, fix the miner/template, re-run, re-review.

- [ ] **Step 7: Write the vendored-corpus pin tests (green only AFTER the build run — that is why they land in this task, after Step 6)**

Create `benchmarks/tests/datasets/test_crosscommitvuln_vendored.py`:

```python
"""Pins on the REAL packaged vendored corpus (design §9.1) — these read
``pydocs_eval.datasets.data.crosscommitvuln`` via importlib.resources and go
green only after the one-time network build run
(``benchmarks/tools/build_crosscommitvuln.py``) has produced + committed the
records. The count is a BOUND (24 always-clean single-CVE-repo records + up
to 9 multi-CVE-repo records surviving the ancestry drop), not a hard pin."""

from __future__ import annotations

import importlib.resources as ir
import json
import re

from pydocs_eval.datasets._crosscommitvuln_build import assert_query_clean

_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _rows(name: str) -> list[dict]:
    text = ir.files("pydocs_eval.datasets.data.crosscommitvuln").joinpath(name).read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_vendored_count_within_construction_bound() -> None:
    assert 24 <= len(_rows("records.jsonl")) <= 33


def test_every_record_single_repo_single_commit() -> None:
    for rec in _rows("records.jsonl"):
        assert isinstance(rec["repo_url"], str) and rec["repo_url"], rec["task_id"]
        assert _SHA40.fullmatch(rec["prefix_sha"]), rec["task_id"]


def test_all_records_banned_token_sweep() -> None:
    banned_by_id = {row["task_id"]: row["banned"] for row in _rows("banned_tokens.jsonl")}
    for rec in _rows("records.jsonl"):
        assert_query_clean(rec["query"], banned_by_id[rec["task_id"]])  # raises on any leak


def test_gold_always_non_empty_and_co_residence_cleared() -> None:
    for rec in _rows("records.jsonl"):
        gold = rec["gold"]
        assert gold["files"] and gold["cve_id"] and gold["cwe_ids"], rec["task_id"]
        assert rec["metadata"]["co_resident_cves"] == "", rec["task_id"]


def test_notice_ships_with_the_vendored_data() -> None:
    notice = ir.files("pydocs_eval.datasets.data.crosscommitvuln").joinpath("NOTICE").read_text()
    for required in ("Arunabh Majumdar", "CC BY 4.0", "arXiv:2604.21917", "10.5281/zenodo.19338596"):
        assert required in notice
```

Run:

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_crosscommitvuln_vendored.py -q
```

Expected: `5 passed` (fails with `FileNotFoundError` on `records.jsonl` if Step 6 was skipped — by design).

- [ ] **Step 8: Verify wheel AND sdist carry the vendored dir (release gate, design §4.2)**

```bash
cd benchmarks
python -m build --sdist --wheel --outdir /private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp--claude-worktrees-dreamy-joliot-f7830a/4546602a-1b31-498e-b5bd-ddba4ff24728/scratchpad/ccv-dist  # pip install build, if absent
tar -tzf /private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp--claude-worktrees-dreamy-joliot-f7830a/4546602a-1b31-498e-b5bd-ddba4ff24728/scratchpad/ccv-dist/pydocs_mcp_eval-0.2.0.tar.gz | grep crosscommitvuln
unzip -l /private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp--claude-worktrees-dreamy-joliot-f7830a/4546602a-1b31-498e-b5bd-ddba4ff24728/scratchpad/ccv-dist/pydocs_mcp_eval-0.2.0-*.whl | grep crosscommitvuln
cd ..
```

Expected: BOTH listings show `datasets/data/crosscommitvuln/__init__.py`, `NOTICE`, `records.jsonl`, `banned_tokens.jsonl`. If the sdist listing misses any, the `MANIFEST.in` from Step 2 is wrong — fix it before committing; this is a release gate.

- [ ] **Step 9: Full gate set + commit (vendored artifacts are committed by design)**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
pytest tests/extraction/test_config.py -q   # repo-invariant now also covers the vendored records.jsonl
ruff check python/ tests/ benchmarks/ && ruff format --check python/ tests/ benchmarks/
git add benchmarks/src/pydocs_eval/datasets/data/ \
        benchmarks/tools/build_crosscommitvuln.py \
        benchmarks/tests/tools/ \
        benchmarks/tests/datasets/test_crosscommitvuln_vendored.py \
        benchmarks/pyproject.toml benchmarks/MANIFEST.in
git commit -m "feat(eval): vendor crosscommitvuln QA records — network build tool (prefix-sha pinning + co-resident ancestry drop), CC BY 4.0 NOTICE, wheel+sdist package-data, 24..33 vendored-count gate"
```

## Group B — CombinedDataset + optimize config + per-prefix reporting

> **Repo/cwd for all Group B tasks:** the pydocs-mcp worktree — `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a`. Gate commands: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/… -q` (benchmarks suite, `asyncio_mode="auto"` — async tests need no decorator) and `ruff format benchmarks/ && ruff check benchmarks/`. Group B depends on Group A having landed `CrossCommitVulnDataset` (registered `"crosscommitvuln"`, imported from `datasets/__init__.py`) and the `GoldSubstringAll` gate (registered `"gold_substring_all"` in `optimize/rubric/gates.py`). All tasks here are hermetic — no network, no paid calls.

### Task B1: `CombinedDataset` — prefixed union of swe-qa-pro + crosscommitvuln

**Files:**
- Create: `benchmarks/src/pydocs_eval/datasets/combined.py`
- Modify: `benchmarks/src/pydocs_eval/datasets/__init__.py`
- Test: `benchmarks/tests/datasets/test_combined_dataset.py`

**Interfaces:**
- Consumes: `Dataset`, `EvalTask`, `GoldAnswer` (`pydocs_eval.datasets.base_dataset`); `dataset_registry` (`pydocs_eval.registries`) — `build("swe-qa-pro")` and `build("crosscommitvuln")` for production members; `task_split(task_id: str) -> Literal["train", "holdout"]` and `partition_task_ids(task_ids: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]` (`pydocs_eval.optimize._split`).
- Produces: `class CombinedDataset` registered `@dataset_registry.register("swe-qa-pro+crosscommitvuln")` with fields `name: str = "swe-qa-pro+crosscommitvuln"`, `revision: str = "1.0"`, `fixture_path: Path | None = None`, `members: tuple[tuple[str, Dataset], ...] = ()`; `def tasks(self) -> AsyncIterator[EvalTask]` returning `self._iter_tasks()`; `async def _iter_tasks(self) -> AsyncIterator[EvalTask]` yielding `replace(task, task_id=f"{prefix}/{task.task_id}")`; `def _members(self) -> tuple[tuple[str, Dataset], ...]` (raises `ValueError` on non-None `fixture_path`).

- [ ] **Step 1: Write the failing test file**

Create `benchmarks/tests/datasets/test_combined_dataset.py`:

```python
"""CombinedDataset tests — hermetic via injected fake member datasets (no
network, no git; the production member wiring is exercised only up to the
registry build, which constructs but never iterates the members)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import Dataset, EvalTask, GoldAnswer
from pydocs_eval.datasets.combined import CombinedDataset
from pydocs_eval.optimize._split import partition_task_ids, task_split
from pydocs_eval.registries import dataset_registry


def _make_member_task(raw_id: str) -> EvalTask:
    return EvalTask(
        task_id=raw_id,
        query=f"q-{raw_id}",
        gold=GoldAnswer(),
        corpus_source=lambda: Path("."),
    )


@dataclass
class _FakeMemberDataset:
    """Stand-in member — yields tasks with caller-chosen (colliding) raw ids."""

    name: str
    raw_ids: tuple[str, ...]
    revision: str = "0"

    async def tasks(self) -> AsyncIterator[EvalTask]:
        for raw_id in self.raw_ids:
            yield _make_member_task(raw_id)


# The SAME raw ids in both members — prefixing must keep them disjoint.
_COLLIDING_IDS = tuple(f"t-{i:03d}" for i in range(16))


def _combined_with_fake_members() -> CombinedDataset:
    return CombinedDataset(
        members=(
            ("sweqapro", _FakeMemberDataset(name="swe-qa-pro", raw_ids=_COLLIDING_IDS)),
            ("ccv", _FakeMemberDataset(name="crosscommitvuln", raw_ids=_COLLIDING_IDS)),
        )
    )


async def test_satisfies_dataset_protocol() -> None:
    assert isinstance(CombinedDataset(), Dataset)


def test_registered_under_the_plus_name() -> None:
    ds = dataset_registry.build("swe-qa-pro+crosscommitvuln")
    assert isinstance(ds, CombinedDataset)
    assert ds.name == "swe-qa-pro+crosscommitvuln"
    assert ds.revision == "1.0"


async def test_colliding_member_ids_are_prefixed_disjoint_and_all_present() -> None:
    tasks = [t async for t in _combined_with_fake_members().tasks()]
    ids = [t.task_id for t in tasks]
    assert len(ids) == 2 * len(_COLLIDING_IDS)
    assert len(set(ids)) == len(ids)  # prefixing keeps colliding raw ids unique
    assert {i.split("/", 1)[0] for i in ids} == {"sweqapro", "ccv"}
    assert "sweqapro/t-000" in ids and "ccv/t-000" in ids  # both members' tasks appear


async def test_non_none_fixture_path_raises_naming_value_and_members() -> None:
    ds = CombinedDataset(fixture_path=Path("/tmp/combined.jsonl"))
    with pytest.raises(ValueError) as excinfo:
        _ = [t async for t in ds.tasks()]
    message = str(excinfo.value)
    assert "combined.jsonl" in message  # names the offending value
    assert "members=" in message  # points at the test seam


async def test_prefixed_ids_split_non_empty_on_both_sides() -> None:
    ids = [t.task_id async for t in _combined_with_fake_members().tasks()]
    train, holdout = partition_task_ids(ids)  # raises loudly if a side is empty
    assert train and holdout
    # Every id lands on exactly one pinned side (sha256 % 2 determinism).
    assert {task_split(i) for i in ids} == {"train", "holdout"}
```

- [ ] **Step 2: Run it, see it fail on the missing module**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_combined_dataset.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'pydocs_eval.datasets.combined'`.

- [ ] **Step 3: Minimal implementation**

Create `benchmarks/src/pydocs_eval/datasets/combined.py`:

```python
"""Combined swe-qa-pro + crosscommitvuln dataset (crosscommitvuln design §6.4).

Unions the two member iterators with task_id prefixing (``sweqapro/…``,
``ccv/…``) so ids stay disjoint and the sha256-pinned optimizer split
(``optimize/_split.py``) sees one merged pool. Satisfies the ``Dataset``
Protocol by attribute + method; plugs into the shipped ``skillopt``
optimizer path with zero changes to existing fitness/gate code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path

from ..registries import dataset_registry
from .base_dataset import Dataset, EvalTask


@dataset_registry.register("swe-qa-pro+crosscommitvuln")
@dataclass
class CombinedDataset:
    """Union of swe-qa-pro + crosscommitvuln with disjoint prefixed task ids."""

    name: str = "swe-qa-pro+crosscommitvuln"
    revision: str = "1.0"
    # WHY: accepted for registry-build signature parity ONLY —
    # optimize/__main__.py builds every dataset with fixture_path=…; a
    # non-None value raises in _members because the two members read
    # incompatible JSONL shapes (design §6.4).
    fixture_path: Path | None = None
    members: tuple[tuple[str, Dataset], ...] = ()

    def tasks(self) -> AsyncIterator[EvalTask]:
        """Protocol method — a plain ``def`` returning the async generator."""
        return self._iter_tasks()

    async def _iter_tasks(self) -> AsyncIterator[EvalTask]:
        for prefix, ds in self._members():
            async for task in ds.tasks():
                yield replace(task, task_id=f"{prefix}/{task.task_id}")

    def _members(self) -> tuple[tuple[str, Dataset], ...]:
        if self.members:  # test injection of fakes
            return self.members
        if self.fixture_path is not None:  # combined fixture dry-run unsupported by design
            raise ValueError(
                "CombinedDataset does not support a top-level fixture_path "
                f"(got {self.fixture_path!r}): its members read incompatible JSONL shapes. "
                "Inject fake members= for tests, or fixture-test each member loader separately."
            )
        return (  # production: each member resolves its own data
            ("sweqapro", dataset_registry.build("swe-qa-pro")),
            ("ccv", dataset_registry.build("crosscommitvuln")),
        )
```

- [ ] **Step 4: Wire the registration seam in `datasets/__init__.py`**

`registries.py::_populate_datasets()` only imports `pydocs_eval.datasets`, so the decorator fires only if `__init__.py` imports the module. Edit `benchmarks/src/pydocs_eval/datasets/__init__.py` — add the import (alphabetical, right after `base_dataset`; Group A already added the `crosscommitvuln` line):

```python
from .base_dataset import Dataset
from .combined import CombinedDataset
from .crosscommitvuln import CrossCommitVulnDataset
from .ds1000 import Ds1000Dataset
from .repoqa import RepoQADataset
from .structural_recall import StructuralRecallDataset
from .swe_qa import SweQaDataset
from .swe_qa_pro import SweQaProDataset

__all__ = [
    "CombinedDataset",
    "CrossCommitVulnDataset",
    "Dataset",
    "Ds1000Dataset",
    "RepoQADataset",
    "StructuralRecallDataset",
    "SweQaDataset",
    "SweQaProDataset",
]
```

- [ ] **Step 5: Run the new tests, see them pass**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_combined_dataset.py -q
```

Expected: `5 passed`.

- [ ] **Step 6: Full benchmarks suite + lint (no regressions, no duplicate-registration `ValueError` at import)**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
ruff format benchmarks/src/pydocs_eval/datasets/ benchmarks/tests/datasets/
ruff check benchmarks/
```

Expected: all tests pass, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add benchmarks/src/pydocs_eval/datasets/combined.py \
        benchmarks/src/pydocs_eval/datasets/__init__.py \
        benchmarks/tests/datasets/test_combined_dataset.py
git commit -m "feat(eval): CombinedDataset — prefixed swe-qa-pro+crosscommitvuln union (design §6.4)"
```

### Task B2: combined optimize config + per-prefix reporting seam

**Files:**
- Create: `benchmarks/src/pydocs_eval/optimize/configs/optimize_ask_prompt_combined.yaml`
- Create: `benchmarks/src/pydocs_eval/optimize/_prefix_report.py`
- Modify: `benchmarks/src/pydocs_eval/optimize/__main__.py`
- Test: `benchmarks/tests/optimize/test_combined_optimize_config.py`

**Interfaces:**
- Consumes: `load_run_config(path: Path) -> OptimizeRunConfig` (`optimize/run_config.py` — its `_assert_registry_keys` already runs `validate_rubric_config(cfg.ask_rubric.rubric_config, registered_gate_kinds=gate_registry.names())`, so a successful load proves `gold_substring_all` is registered); `dataset_registry.build(cfg.dataset.name, fixture_path=cfg.dataset.fixture_path)` (the `optimize/__main__.py:187` build shape); `CombinedDataset` (Task B1).
- Produces: the shipped YAML with `dataset: {name: swe-qa-pro+crosscommitvuln}` and the added gate `{name: exact_id, kind: gold_substring_all, params: {}}`; pure helpers `task_id_prefix(task_id: str) -> str`, `count_by_task_prefix(task_ids: Iterable[str]) -> dict[str, int]`, `mean_score_by_task_prefix(scores: Mapping[str, float]) -> dict[str, float]`; CLI seam `_print_per_prefix_split(train: Sequence[str], holdout: Sequence[str]) -> None` called from `_print_split_determinism`.

- [ ] **Step 1: Write the failing test file**

Create `benchmarks/tests/optimize/test_combined_optimize_config.py`:

```python
"""Combined-corpus optimize config + the per-prefix reporting seam (design
§6.4). At ≤33 ccv vs ~260 swe-qa-pro tasks the ccv slice is ~8-11% of a
blended score and can silently regress — metrics must be groupable by the
task_id prefix (``task_id.split("/", 1)[0]``). Hermetic: config load + pure
helpers + captured CLI echo; no network, no paid calls."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from pydocs_eval.datasets.combined import CombinedDataset
from pydocs_eval.optimize._prefix_report import (
    count_by_task_prefix,
    mean_score_by_task_prefix,
    task_id_prefix,
)
from pydocs_eval.optimize.run_config import load_run_config
from pydocs_eval.registries import dataset_registry


def _shipped(name: str) -> Path:
    """Resolve a shipped ``optimize/configs/<name>`` YAML to a filesystem path."""
    return Path(str(files("pydocs_eval.optimize.configs").joinpath(name)))


def test_combined_config_resolves_and_builds_the_combined_dataset() -> None:
    cfg = load_run_config(_shipped("optimize_ask_prompt_combined.yaml"))
    # The "+" is a valid YAML plain scalar — it survives parsing unquoted.
    assert cfg.dataset.name == "swe-qa-pro+crosscommitvuln"
    assert cfg.dataset.fixture_path is None
    ds = dataset_registry.build(cfg.dataset.name, fixture_path=cfg.dataset.fixture_path)
    assert isinstance(ds, CombinedDataset)


def test_combined_config_gates_include_exactness_and_grounding() -> None:
    # load_run_config already ran validate_rubric_config against
    # gate_registry.names() — a successful load PROVES gold_substring_all is
    # a registered gate kind (the AC-7 KeyError fires otherwise).
    cfg = load_run_config(_shipped("optimize_ask_prompt_combined.yaml"))
    assert cfg.ask_rubric is not None
    kinds = [g.kind for g in cfg.ask_rubric.gates]
    assert "gold_substring_all" in kinds
    assert "used_indexed_tools" in kinds
    exact = next(g for g in cfg.ask_rubric.gates if g.kind == "gold_substring_all")
    assert exact.name == "exact_id"
    assert exact.params == {}


def test_task_id_prefix_is_the_leading_slash_component() -> None:
    assert task_id_prefix("ccv/cve-2026-27602") == "ccv"
    assert task_id_prefix("sweqapro/swe_qa_pro/0001") == "sweqapro"
    # Un-prefixed ids group under themselves (single-dataset runs degrade).
    assert task_id_prefix("swe-qa-pro:0001") == "swe-qa-pro:0001"


def test_count_by_task_prefix_groups_counts() -> None:
    counts = count_by_task_prefix(["sweqapro/a", "sweqapro/b", "ccv/x"])
    assert counts == {"sweqapro": 2, "ccv": 1}


def test_mean_score_by_task_prefix_reports_each_dataset_separately() -> None:
    means = mean_score_by_task_prefix(
        {"sweqapro/a": 1.0, "sweqapro/b": 0.0, "ccv/x": 0.25}
    )
    assert means == {"sweqapro": 0.5, "ccv": 0.25}


def test_split_echo_reports_per_prefix_counts(capsys) -> None:
    from pydocs_eval.optimize.__main__ import _print_per_prefix_split

    _print_per_prefix_split(
        train=("sweqapro/a", "sweqapro/b", "ccv/x"), holdout=("sweqapro/c", "ccv/y")
    )
    out = capsys.readouterr().out
    assert "sweqapro" in out and "ccv" in out
    assert "'sweqapro': 2" in out  # train-side count is broken down per prefix


def test_split_echo_is_silent_for_a_single_prefix(capsys) -> None:
    from pydocs_eval.optimize.__main__ import _print_per_prefix_split

    _print_per_prefix_split(train=("swe-qa-pro:0001",), holdout=("swe-qa-pro:0002",))
    assert capsys.readouterr().out == ""
```

- [ ] **Step 2: Run it, see it fail**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
PYTHONPATH=benchmarks/src pytest benchmarks/tests/optimize/test_combined_optimize_config.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'pydocs_eval.optimize._prefix_report'`.

- [ ] **Step 3: Create the shipped combined config YAML**

Create `benchmarks/src/pydocs_eval/optimize/configs/optimize_ask_prompt_combined.yaml` (copy of `optimize_ask_prompt.yaml` with the combined dataset + the two-gate addition; the `+` in the dataset name is a YAML plain scalar — locked, no quoting needed):

```yaml
# Ask-prompt optimization over the COMBINED corpus (crosscommitvuln design
# §6.4): swe-qa-pro train + crosscommitvuln train via the CombinedDataset's
# hash-derived split. Values restate the Python field defaults for user-facing
# clarity (the sanctioned YAML duplication); the pydantic Field defaults are
# canonical. Metrics MUST be reported per task_id prefix (sweqapro/ vs ccv/)
# so the small ccv slice cannot silently regress inside the blended score.
artifact: ask_prompt
optimizer: skillopt                  # or critique_refine (low-budget, interpretable)
ladder:                              # small-N screening, then finals — degenerate
  - [ask_rubric, 12, 4]              # two-rung halving over the SAME paid fitness
  - [ask_rubric, 24, 1]              # (a prompt cannot move retrieval metrics, so
                                     # the free retrieval rung is reserved for the
                                     # architecture / retrieval-config campaigns)
ask_rubric:
  runner:
    model: claude-sonnet-5           # judge + agent model id (agent-track single source)
    architecture: text_react         # prompt campaigns pin ONE architecture
    base_url: null
    workspace: ~/pydocs-index
    task_timeout_seconds: 900.0
  gates:
    - {name: non_empty, kind: min_answer_chars, params: {n: 40}}
    - {name: grounded, kind: gold_substring, params: {}}       # weak ANY-match screen
    - {name: exact_id, kind: gold_substring_all, params: {}}   # ALL gold tokens verbatim (§6.5)
    - {name: used_tools, kind: used_indexed_tools, params: {n: 1}}  # anti-memorization (G7)
  criteria:
    - {name: correctness,  weight: 0.4, description: "Factually correct against the repository."}
    - {name: grounding,    weight: 0.3, description: "Claims traceable to retrieved symbols/paths."}
    - {name: completeness, weight: 0.2, description: "Covers every part of the question."}
    - {name: conciseness,  weight: 0.1, description: "No filler; do not reward verbosity."}
  fail_fast: true                    # a lost gate spares the judge
  gate_weight: 0.3
  rubric_weight: 0.7
budget:
  max_trials: 20
  max_usd: 40.0
  max_judge_calls: 200
  wall_timeout_seconds: 14400.0
dataset:
  name: swe-qa-pro+crosscommitvuln   # the "+" is a YAML plain scalar (locked)
rng_seed: 0
```

- [ ] **Step 4: Implement the per-prefix reporting helpers**

Create `benchmarks/src/pydocs_eval/optimize/_prefix_report.py`:

```python
"""Per-dataset-prefix metric breakdown for combined-dataset runs (design §6.4).

``CombinedDataset`` tags every task_id with its member prefix (``sweqapro/…``,
``ccv/…``). At ≤33 ccv vs ~260 swe-qa-pro tasks the ccv slice is ~8-11% of a
blended score and can silently regress — so run reporting groups counts and
mean scores by ``task_id.split("/", 1)[0]``. Pure functions, no I/O; callers
(the CLI split echo today; holdout-metric reporting as it lands) do the
formatting. ccv-slice deltas at N≈12-16 are directional only (design §10).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping


def task_id_prefix(task_id: str) -> str:
    """The dataset prefix of a combined task id (``"ccv/cve-x"`` → ``"ccv"``).

    An un-prefixed id (no ``"/"``) groups under itself, so single-dataset
    runs degrade to one group instead of raising.

    Example:
        >>> task_id_prefix("ccv/cve-2026-27602")
        'ccv'
    """
    return task_id.split("/", 1)[0]


def count_by_task_prefix(task_ids: Iterable[str]) -> dict[str, int]:
    """Task counts grouped by dataset prefix — the split-echo breakdown.

    Example:
        >>> count_by_task_prefix(["ccv/a", "ccv/b", "sweqapro/x"])
        {'ccv': 2, 'sweqapro': 1}
    """
    return dict(Counter(task_id_prefix(task_id) for task_id in task_ids))


def mean_score_by_task_prefix(scores: Mapping[str, float]) -> dict[str, float]:
    """Mean of per-task scores grouped by dataset prefix.

    ``scores`` maps task_id → score (verdicts, recall@k, …); reporting each
    prefix's mean side by side keeps a ccv regression visible under an
    improving blended score (design §6.4).

    Example:
        >>> mean_score_by_task_prefix({"ccv/a": 1.0, "ccv/b": 0.0})
        {'ccv': 0.5}
    """
    grouped: dict[str, list[float]] = {}
    for task_id, score in scores.items():
        grouped.setdefault(task_id_prefix(task_id), []).append(score)
    return {prefix: sum(vals) / len(vals) for prefix, vals in grouped.items()}
```

- [ ] **Step 5: Wire the seam into the CLI split echo**

Edit `benchmarks/src/pydocs_eval/optimize/__main__.py`. First add the import (the file uses absolute `pydocs_eval.…` imports; place it in the existing sorted block, directly after the `pydocs_eval.optimize._agent_track_binding` import):

```python
from pydocs_eval.optimize._prefix_report import count_by_task_prefix
```

Then extend `_print_split_determinism` — exact edit, old:

```python
    ids = _probe_task_ids(cfg)
    train, holdout = partition_task_ids(ids)
    print(
        f"  split: deterministic sha256 % 2 over {len(ids)} id(s) -> "
        f"train={len(train)}, holdout={len(holdout)}"
    )
```

new:

```python
    ids = _probe_task_ids(cfg)
    train, holdout = partition_task_ids(ids)
    print(
        f"  split: deterministic sha256 % 2 over {len(ids)} id(s) -> "
        f"train={len(train)}, holdout={len(holdout)}"
    )
    _print_per_prefix_split(train, holdout)


def _print_per_prefix_split(train: Sequence[str], holdout: Sequence[str]) -> None:
    """Echo per-dataset-prefix split counts (design §6.4, small-N safety).

    Combined datasets prefix every task_id (``sweqapro/…``, ``ccv/…``); the
    ccv slice is small enough to skew silently, so both sides are broken
    down by prefix whenever any id is actually prefixed. Silent for
    single-dataset runs (no id carries a ``"/"``) — the plain counts above
    already cover them.
    """
    if not any("/" in task_id for task_id in (*train, *holdout)):
        return
    train_counts = count_by_task_prefix(train)
    holdout_counts = count_by_task_prefix(holdout)
    print(f"  split by prefix: train={train_counts}, holdout={holdout_counts}")
```

- [ ] **Step 6: Run the new tests, see them pass**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/optimize/test_combined_optimize_config.py -q
```

Expected: `7 passed`.

- [ ] **Step 7: Full benchmarks suite + lint (existing config tests, dry-run tests, and the gate registry must all stay green)**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
ruff format benchmarks/src/pydocs_eval/optimize/ benchmarks/tests/optimize/
ruff check benchmarks/
```

Expected: all tests pass (in particular `tests/optimize/test_run_config.py` and `tests/optimize/test_cli_dry_run.py` are unaffected — the new YAML is additive, the `_print_split_determinism` output gains no line for single-prefix probes), ruff clean.

- [ ] **Step 8: Commit**

```bash
git add benchmarks/src/pydocs_eval/optimize/configs/optimize_ask_prompt_combined.yaml \
        benchmarks/src/pydocs_eval/optimize/_prefix_report.py \
        benchmarks/src/pydocs_eval/optimize/__main__.py \
        benchmarks/tests/optimize/test_combined_optimize_config.py
git commit -m "feat(eval): combined optimize config + per-prefix metric reporting seam (design §6.4)"
```

## Group C — coding-agent-playbook fixtures + prompt (different repo)

All Group C tasks run in repo **`/Users/msobroza/Projects/coding-agent-playbook/coding-agent-playbook`** (package `src/coding_agent_playbook`). This group is fully independent of Groups A/B (different repo, no shared code — spec §11(C)). Gate commands for this repo: `uv run pytest tests/… -q`, `uv run ruff check`, `uv run mypy --strict`, and per-fixture `uv run playbook eval <id> --run --runner fake`.

**Within-group dependency (read before starting):** the playbook validator enforces bidirectional prompt↔task back-refs (`evals/validator.py` Rule 6 & 7) — a prompt's `eval_tasks:` entry must resolve to an on-disk task, and each task's `prompt_id` must appear in that prompt's `eval_tasks:`. Therefore the whole-suite integration test (`test_shipped_eval_tasks_c7.py`) is only green once **all** ten fixtures are authored. Each task below is gated on its own **focused** test; the cross-cutting `validate_tasks` + count test go green in **C4**, the group's PR-able deliverable. This mirrors spec §11: piece C lands as one PR.

The ten fixture ids this group authors (task-id convention `find-injected-vuln-<slug>`, spec §7.2; `changedetection.io` appears twice so its slugs are disambiguated by exploit class):

| Fixture id | CVE | CWE |
|---|---|---|
| `find-injected-vuln-modoboa-cmdi` | CVE-2099-00018 | CWE-78 |
| `find-injected-vuln-ormar-sqli` | CVE-2099-00016 | CWE-89 |
| `find-injected-vuln-changedetection-ssrf` | CVE-2026-27696 | CWE-918 |
| `find-injected-vuln-langroid-codeinj` | CVE-2099-00004 | CWE-94 |
| `find-injected-vuln-pytorch-deserial` | CVE-2099-00003 | CWE-502 |
| `find-injected-vuln-changedetection-pathtraversal` | CVE-2099-00023 | CWE-22 |
| `find-injected-vuln-authlib-sigbypass` | CVE-2099-00022 | CWE-347 |
| `find-injected-vuln-graphiti-queryinj` | CVE-2026-32247 | CWE-943 |
| `find-injected-vuln-pydash-massassign` | CVE-2099-00008 | CWE-915 |
| `find-injected-vuln-aiplatform-xss` | CVE-2099-00014 | CWE-79 |

So `N = 10` and the C4 count bump is `10 → 20`. **The count assertion in C4 must equal the number actually authored** — if any fixture is dropped (spec §7.2: drop any whose real chain resists faithful small-fixture paraphrase), lower the expected count and the `eval_tasks:` list in lock-step.

---

### Task C1 — the `find-injected-vulnerability` prompt + back-ref validity test

**Repo/cwd:** `/Users/msobroza/Projects/coding-agent-playbook/coding-agent-playbook`

**Files**
- Create: `src/coding_agent_playbook/resources/prompts/find-injected-vulnerability.md.j2`
- Test: `tests/integration/test_find_injected_vuln_prompt.py`

**Interfaces**
- Consumes: `coding_agent_playbook.types.Prompt.from_markdown(path: Path, *, source_package: str) -> Prompt` → `prompt.frontmatter.eval_tasks: list[str] | None`, `prompt.frontmatter.applies_if` ({} ⇒ non-ML-gated — `PromptFrontmatter.applies_if` is a `dict[str, Any]` defaulting to `{}`; a prompt omitting `applies_if:` yields `{}`, never `None`), `prompt.frontmatter.id: str`.
- Produces: the shipped prompt resource `find-injected-vulnerability` (non-ML-gated, `eval_tasks:` listing all ten fixture ids), consumed by every C2/C3 fixture's `prompt_id` back-ref.

- [ ] **Step 1: Write the failing back-ref/frontmatter test.** Create `tests/integration/test_find_injected_vuln_prompt.py`:

  ```python
  """Focused unit checks on the find-injected-vulnerability prompt resource.

  Runs BEFORE all ten fixtures exist, so it parses the prompt frontmatter and
  body directly rather than going through validate_tasks (which needs every
  eval_tasks: entry to resolve on-disk — that whole-suite green lands in C4).
  """

  from __future__ import annotations

  from pathlib import Path

  from coding_agent_playbook.types import Prompt

  _SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "coding_agent_playbook"
  _PROMPT = _SRC_ROOT / "resources" / "prompts" / "find-injected-vulnerability.md.j2"

  _EXPECTED_EVAL_TASKS = [
      "find-injected-vuln-modoboa-cmdi",
      "find-injected-vuln-ormar-sqli",
      "find-injected-vuln-changedetection-ssrf",
      "find-injected-vuln-langroid-codeinj",
      "find-injected-vuln-pytorch-deserial",
      "find-injected-vuln-changedetection-pathtraversal",
      "find-injected-vuln-authlib-sigbypass",
      "find-injected-vuln-graphiti-queryinj",
      "find-injected-vuln-pydash-massassign",
      "find-injected-vuln-aiplatform-xss",
  ]

  # v2 framing bans (spec §5.2 / §7.1) — none may appear in the model-facing body.
  _FRAMING_BANS = (
      "commit", "commits", "multiple", "multi-commit", "gradually", "over time",
      "across", "benign", "static analysis", "sast", "per-commit", "scanner",
      "individually",
  )


  def _prompt() -> Prompt:
      return Prompt.from_markdown(_PROMPT, source_package="coding_agent_playbook")


  def test_prompt_is_non_ml_gated() -> None:
      # NON-ML-gated: no applies_if any_ml_stack (spec locked contract).
      # PromptFrontmatter.applies_if defaults to {} and __post_init__ coerces
      # None -> {}, so a prompt omitting applies_if: yields {} (never None).
      assert _prompt().frontmatter.applies_if == {}


  def test_prompt_id_is_find_injected_vulnerability() -> None:
      assert _prompt().frontmatter.id == "find-injected-vulnerability"


  def test_prompt_lists_every_fixture_id() -> None:
      assert _prompt().frontmatter.eval_tasks == _EXPECTED_EVAL_TASKS


  def test_prompt_body_has_no_commit_or_temporal_framing() -> None:
      body = _PROMPT.read_text(encoding="utf-8").lower()
      # Strip the frontmatter (fixture ids legitimately contain no framing words).
      body = body.split("---", 2)[-1]
      offenders = [w for w in _FRAMING_BANS if w in body]
      assert offenders == [], f"framing language leaked into prompt body: {offenders}"
  ```

- [ ] **Step 2: Run it, see it fail.** `uv run pytest tests/integration/test_find_injected_vuln_prompt.py -q` → fails (`FileNotFoundError` — prompt does not exist).

- [ ] **Step 3: Author the prompt.** Create `src/coding_agent_playbook/resources/prompts/find-injected-vulnerability.md.j2`. Frontmatter carries **no `applies_if`** and lists every fixture id; the body is Visa-harness source→sink framing with **zero** commit/benign/temporal/scanner language (spec §7.1):

  ```markdown
  ---
  id: find-injected-vulnerability
  name: Find the injected vulnerability
  description: Given one project snapshot and only a vague symptom, locate the exploitable condition — trace untrusted input to a dangerous operation and classify the exploit.
  slash: find-injected-vulnerability
  version: 1
  eval_tasks:
    - find-injected-vuln-modoboa-cmdi
    - find-injected-vuln-ormar-sqli
    - find-injected-vuln-changedetection-ssrf
    - find-injected-vuln-langroid-codeinj
    - find-injected-vuln-pytorch-deserial
    - find-injected-vuln-changedetection-pathtraversal
    - find-injected-vuln-authlib-sigbypass
    - find-injected-vuln-graphiti-queryinj
    - find-injected-vuln-pydash-massassign
    - find-injected-vuln-aiplatform-xss
  ---

  # Find the injected vulnerability

  This project snapshot contains one real, exploitable security vulnerability. You are told nothing about where it is, what class it belongs to, or how it got there — only that a needle exists. Your job is to find it.

  Work like an attacker mapping an unfamiliar codebase: start at the attack surface, not at line 1.

  ## Method

  1. **Map the untrusted sources.** Find every point where bytes from outside this process become live values — request payloads, function arguments carrying user-supplied paths or addresses, deserialized inputs, fetched URLs, query parameters. That set is your attack surface; begin there.
  2. **Follow the taint to a dangerous operation.** Trace each untrusted value forward until it reaches a sink — a shell/`subprocess` execution, a string-built SQL or query, a filesystem path, a URL fetch, a deserialization call, a dynamic `eval`/`exec`, or an unescaped output. The vulnerability is a *reachable* source→sink chain.
  3. **Gate the finding.** Report a chain only if it is (a) **reachable** from an untrusted source, (b) **unmitigated** — no validation, escaping, or safe API stands between source and sink, and (c) backed by a **concrete exploit** you can state in one sentence. Cite the exact file(s) where the source and the sink live.
  4. **Classify by exploit, not by bug-class habit.** Name the vulnerability by what the exploit actually does (e.g. an injected command runs, a traversed path escapes the root, an untrusted object is deserialized), and give the matching CWE.

  ## Output

  State the vulnerability class and its CWE, trace the untrusted source to the dangerous operation in one or two sentences, and cite the specific file(s) that carry the source and the sink. If — after genuinely tracing the attack surface — you conclude there is no reachable, unmitigated, concretely exploitable chain, say so plainly rather than inventing one.
  ```

- [ ] **Step 4: Run the focused test, see it pass.** `uv run pytest tests/integration/test_find_injected_vuln_prompt.py -q` → 4 passed.

- [ ] **Step 5: Lint.** `uv run ruff check tests/integration/test_find_injected_vuln_prompt.py` → clean. (No `mypy` target changes — the prompt is a resource file, not Python.)

  > NOTE: `uv run pytest tests/integration/test_shipped_eval_tasks_c7.py -q` is now **RED** — the prompt references ten fixtures that do not yet exist (`validate_tasks` Rule 7). That is expected and resolves in C4. Do not "fix" it by trimming `eval_tasks:`.

- [ ] **Step 6: Commit.**
  ```
  git add src/coding_agent_playbook/resources/prompts/find-injected-vulnerability.md.j2 \
          tests/integration/test_find_injected_vuln_prompt.py
  git commit -m "feat(playbook): add non-ML-gated find-injected-vulnerability needle-search prompt"
  ```

---

### Task C2 — one fully worked fixture: `find-injected-vuln-modoboa-cmdi`

**Repo/cwd:** `/Users/msobroza/Projects/coding-agent-playbook/coding-agent-playbook`

**Files** (all Create — complete contents below)
- `src/coding_agent_playbook/resources/eval_tasks/find-injected-vuln-modoboa-cmdi/task.toml`
- `src/coding_agent_playbook/resources/eval_tasks/find-injected-vuln-modoboa-cmdi/setup/app/jobs.py`
- `src/coding_agent_playbook/resources/eval_tasks/find-injected-vuln-modoboa-cmdi/setup/app/sysutils.py`
- `src/coding_agent_playbook/resources/eval_tasks/find-injected-vuln-modoboa-cmdi/setup/pyproject.toml`

**Interfaces**
- Consumes: `evals/loader.py::load_eval_task_from_dir`, `evals/runner.py::{materialize, capture}`, `evals/scoring.py::score`, `evals/judge.py::StubJudge`, the `fake` runner (`evals/fake_runner.py`), `[gate].file_unchanged` check, `[rubric]` `{fail: float, rubric: [{judge, weight}]}`.
- Produces: the worked-example fixture referenced by the C1 prompt's first `eval_tasks:` entry. Authored **solely from CVE-2099-00018's CC BY annotation/reproduction — upstream modoboa source NOT consulted** (spec §7.4 binding authorship rule); files are original paraphrase, not copies.

- [ ] **Step 1: Write the failing hermetic run test.** Create `tests/integration/test_modoboa_cmdi_fixture.py`:

  ```python
  """Hermetic C2 check: the worked-example fixture runs green under the fake runner."""

  from __future__ import annotations

  import subprocess
  from pathlib import Path
  from typing import Any

  import pytest

  from coding_agent_playbook.evals.judge import StubJudge
  from coding_agent_playbook.evals.loader import load_eval_task_from_dir
  from coding_agent_playbook.evals.runner import capture, materialize
  from coding_agent_playbook.evals.scoring import score
  from coding_agent_playbook.evals.task import Verdict

  _TASKS_ROOT = (
      Path(__file__).resolve().parents[2]
      / "src" / "coding_agent_playbook" / "resources" / "eval_tasks"
  )
  _FIXTURE = _TASKS_ROOT / "find-injected-vuln-modoboa-cmdi"


  @pytest.fixture
  def stub_uv_sync(monkeypatch: pytest.MonkeyPatch) -> None:
      real_run = subprocess.run

      def _stub_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
          if args and args[0] == "uv":
              return subprocess.CompletedProcess(args, 0, "", "")
          return real_run(args, **kwargs)

      monkeypatch.setattr("coding_agent_playbook.evals.runner.subprocess.run", _stub_run)
      monkeypatch.setattr(
          "coding_agent_playbook.evals.runner.shutil.which", lambda _b: "/fake/uv"
      )


  def _task() -> Any:
      return load_eval_task_from_dir(_FIXTURE, source_package="coding_agent_playbook")


  def test_fixture_compiles_gate_and_rubric() -> None:
      task = _task()
      compiled = task.compiled()
      assert compiled.layers == frozenset({"gate", "rubric"})
      assert task.verdict == Verdict(mode="boolean", trial="gate and rubric")


  def test_fixture_runs_with_stub_judge(tmp_path: Path, stub_uv_sync: None) -> None:
      task = _task()
      baseline = materialize(task, tmp_path / "scratch")
      candidate = capture(baseline.repo_root, baseline, "transcript")
      report = score(task, baseline, candidate, judge=StubJudge(default=0.9))
      gate = next(c for c in report.checks if c.check_id == "gate")
      rubric = next(c for c in report.checks if c.check_id == "rubric")
      assert gate.passed is True          # no-op fake runner leaves files unchanged
      assert rubric.status == "run"
      assert rubric.passed is True        # composite 0.9 >= 0.5 fail floor
  ```

- [ ] **Step 2: Run it, see it fail.** `uv run pytest tests/integration/test_modoboa_cmdi_fixture.py -q` → fails (fixture dir missing).

- [ ] **Step 3: Write the paraphrased sink file.** Create `…/find-injected-vuln-modoboa-cmdi/setup/app/sysutils.py` — original paraphrase of the shell-wrapper sink (authored from the CC BY annotation `role="SINK — exec_cmd(f-string)…"`, not from upstream source):

  ```python
  """System utilities: thin wrappers around shell command execution.

  Derived (paraphrased) from CVE-2099-00018 (CrossCommitVuln-Bench, CC BY 4.0,
  A. Majumdar). Original fixture code; upstream project source was not consulted.
  """

  from __future__ import annotations

  import subprocess


  def exec_cmd(command: str) -> tuple[int, bytes, bytes]:
      """Run a shell command string and return (returncode, stdout, stderr).

      The whole ``command`` is handed to the shell, so any shell metacharacters in
      it are interpreted rather than treated as literal argument text.
      """
      proc = subprocess.Popen(
          command,
          shell=True,
          stdout=subprocess.PIPE,
          stderr=subprocess.PIPE,
      )
      stdout, stderr = proc.communicate()
      return proc.returncode, stdout, stderr
  ```

- [ ] **Step 4: Write the paraphrased source file.** Create `…/find-injected-vuln-modoboa-cmdi/setup/app/jobs.py` — the tainted-source side (user-controlled mailbox path / address flowing into `exec_cmd`):

  ```python
  """Background maintenance jobs for mailbox storage.

  Derived (paraphrased) from CVE-2099-00018 (CrossCommitVuln-Bench, CC BY 4.0,
  A. Majumdar). Original fixture code; upstream project source was not consulted.
  """

  from __future__ import annotations

  from app.sysutils import exec_cmd


  def relocate_mailbox(operation: dict[str, str]) -> int:
      """Move a user's mailbox directory to a new on-disk location.

      ``operation['argument']`` and ``operation['full_address']`` arrive straight
      from the account-administration request payload and are not sanitized here.
      """
      old_path = operation["argument"]
      full_address = operation["full_address"]
      new_path = f"/var/vmail/{full_address}"
      returncode, _out, _err = exec_cmd(f"mv {old_path} {new_path}")
      return returncode


  def create_mailbox(full_address: str) -> int:
      """Provision the on-disk maildir for a newly created address."""
      target = f"/var/vmail/{full_address}"
      returncode, _out, _err = exec_cmd(f"maildirmake {target}")
      return returncode
  ```

- [ ] **Step 5: Write the minimal setup pyproject.** Create `…/find-injected-vuln-modoboa-cmdi/setup/pyproject.toml`:

  ```toml
  [project]
  name = "find-injected-vuln-modoboa-cmdi-fixture"
  version = "0.1.0"
  requires-python = ">=3.12"
  dependencies = ["pytest"]
  ```

- [ ] **Step 6: Write the task.toml.** Create `…/find-injected-vuln-modoboa-cmdi/task.toml`. Neutral single-snapshot `prompt_args` (spec §5.2 bans apply — no CWE/sink/file/commit/temporal language), **review-only** gate via `file_unchanged` on both paraphrase files, and a 3-group rubric encoding CWE-class + source→sink + file-citation, weights summing to 1.0:

  ```toml
  id = "find-injected-vuln-modoboa-cmdi"
  prompt_id = "find-injected-vulnerability"
  description = "OS command injection paraphrase; derived from CVE-2099-00018 (CrossCommitVuln-Bench, CC BY 4.0, A. Majumdar). See resources/eval_tasks/ATTRIBUTIONS.md."
  version = 1

  [setup]
  files = ["app/jobs.py", "app/sysutils.py", "pyproject.toml"]
  project_kind = "library"

  [invocation]
  prompt_args = "This project contains a security vulnerability. Find where untrusted input reaches a dangerous operation and classify the exploit."
  input_variables = {}

  [gate]
  # Review-only: the agent must find the flaw by reading, not by editing. Deterministic, token-free.
  expr = "jobs_untouched and sysutils_untouched"

  [gate.bind]
  jobs_untouched     = { check = "file_unchanged", path = "app/jobs.py" }
  sysutils_untouched = { check = "file_unchanged", path = "app/sysutils.py" }

  [rubric]
  expr = "identifies_exact_vuln"

  [rubric.bind]
  identifies_exact_vuln = { fail = 0.5, rubric = [
    { judge = "The answer names OS command injection (CWE-78) as the vulnerability class.", weight = 0.34 },
    { judge = "The answer traces the untrusted source (a user-controlled mailbox path / email address) to the shell-executing sink that runs it via subprocess with shell=True.", weight = 0.33 },
    { judge = "The answer cites the specific fixture files where the source and the sink live (app/jobs.py and app/sysutils.py).", weight = 0.33 },
  ] }

  [verdict]
  trial = "gate and rubric"
  ```

- [ ] **Step 7: Run the focused test, see it pass.** `uv run pytest tests/integration/test_modoboa_cmdi_fixture.py -q` → 2 passed.

- [ ] **Step 8: Run the fixture end-to-end via the CLI.** `uv run playbook eval find-injected-vuln-modoboa-cmdi --run --runner fake` → runs hermetically; the review-only gate passes (fake runner is a no-op, files unchanged) and the deterministic path completes without error.

- [ ] **Step 9: Lint + typecheck the test.** `uv run ruff check tests/integration/test_modoboa_cmdi_fixture.py` and `uv run mypy --strict tests/integration/test_modoboa_cmdi_fixture.py` → clean.

- [ ] **Step 10: Commit.**
  ```
  git add src/coding_agent_playbook/resources/eval_tasks/find-injected-vuln-modoboa-cmdi \
          tests/integration/test_modoboa_cmdi_fixture.py
  git commit -m "feat(playbook): add worked find-injected-vuln-modoboa-cmdi fixture (CWE-78, CVE-2099-00018 paraphrase)"
  ```

---

### Task C3 — author the remaining nine fixtures from the §7.2 template + `ATTRIBUTIONS.md`

**Repo/cwd:** `/Users/msobroza/Projects/coding-agent-playbook/coding-agent-playbook`

**Files** (Create)
- `src/coding_agent_playbook/resources/eval_tasks/ATTRIBUTIONS.md`
- Nine fixture directories, each `{task.toml, setup/pyproject.toml, setup/<paraphrase files>}`:
  `find-injected-vuln-ormar-sqli` (CWE-89), `find-injected-vuln-changedetection-ssrf` (CWE-918), `find-injected-vuln-langroid-codeinj` (CWE-94), `find-injected-vuln-pytorch-deserial` (CWE-502), `find-injected-vuln-changedetection-pathtraversal` (CWE-22), `find-injected-vuln-authlib-sigbypass` (CWE-347), `find-injected-vuln-graphiti-queryinj` (CWE-943), `find-injected-vuln-pydash-massassign` (CWE-915), `find-injected-vuln-aiplatform-xss` (CWE-79).

**Interfaces**
- Consumes: same schema as C2's `task.toml` (verified against `security-review-pickle-load/task.toml`) and the C1 prompt's `eval_tasks:` list.
- Produces: the nine fixtures that complete the prompt's back-refs, plus the shared CC BY attribution file (spec §4.2, §7.4).

**Per-fixture authoring checklist (apply to each of the nine — this is the template, not a placeholder; every field is concrete per the table below):**

1. **Directory:** `resources/eval_tasks/find-injected-vuln-<slug>/`.
2. **`task.toml` header:** `id = "find-injected-vuln-<slug>"`, `prompt_id = "find-injected-vulnerability"`, `version = 1`, `description` = one line naming the CWE class **+ its CVE + "(CrossCommitVuln-Bench, CC BY 4.0, A. Majumdar). See resources/eval_tasks/ATTRIBUTIONS.md."**
3. **`[setup]`:** `files = [<each paraphrase file>, "pyproject.toml"]`, `project_kind = "library"`.
4. **`[invocation].prompt_args`:** the **neutral single-snapshot query, byte-identical** to C2 — `"This project contains a security vulnerability. Find where untrusted input reaches a dangerous operation and classify the exploit."` It must contain none of that CVE's banned tokens (spec §5.2: cve/cwe ids, file/basenames, sink symbols, flaw-class words, commit hashes/dates, and the v2 framing vocabulary). Using the shared neutral string guarantees this.
5. **`[gate]` (review-only):** `expr` = the AND of one `file_unchanged` handle **per authored paraphrase file** (never the pyproject); each `[gate.bind]` handle = `{ check = "file_unchanged", path = "<file>" }`.
6. **`[rubric]`:** `expr = "identifies_exact_vuln"`; `[rubric.bind].identifies_exact_vuln = { fail = 0.5, rubric = [ …3 groups… ] }` with weights **summing to exactly 1.0** — group 1 = names the CWE class, group 2 = traces source→sink for this exploit, group 3 = cites the specific fixture file(s). Reuse the `0.34 / 0.33 / 0.33` split.
7. **`[verdict]`:** `trial = "gate and rubric"`.
8. **`setup/` files:** hand-authored ORIGINAL paraphrase isolating **one** source→sink pattern in a single self-contained snapshot (spec §5.0) + a minimal `setup/pyproject.toml` (`name = "find-injected-vuln-<slug>-fixture"`, `version = "0.1.0"`, `requires-python = ">=3.12"`, `dependencies = ["pytest"]`). **Authored ONLY from the CVE's CC BY `annotation.json` + `reproduction.md` — the upstream repository source is NOT consulted** (spec §7.4 binding rule), so no close paraphrase of possibly-GPL/AGPL upstream code can occur. Each fixture header docstring states "Original fixture code; upstream project source was not consulted."

**Per-fixture concrete parameters (source→sink shape and files — spec §7.2):**

| slug | CWE / class | source → sink to paraphrase | setup files |
|---|---|---|---|
| `ormar-sqli` | CWE-89 SQL injection | user-supplied filter value → f-string-built raw SQL passed to the DB executor | `app/queries.py`, `app/db.py` |
| `changedetection-ssrf` | CWE-918 SSRF | user-supplied watch URL → server-side `requests.get(url)` with no allowlist | `app/fetch.py`, `app/watch.py` |
| `langroid-codeinj` | CWE-94 code injection | user-supplied expression string → `eval()`/`exec()` | `app/tools.py`, `app/runner.py` |
| `pytorch-deserial` | CWE-502 deserialization | untrusted checkpoint path → `torch.load` / `pickle.load` without `weights_only` | `app/loader.py`, `app/registry.py` |
| `changedetection-pathtraversal` | CWE-22 path traversal | user-supplied filename → `open(base + name)` escaping the root via `..` | `app/storage.py`, `app/views.py` |
| `authlib-sigbypass` | CWE-347 signature bypass | token verification that accepts `alg=none` / skips the signature check | `app/jwt_verify.py`, `app/auth.py` |
| `graphiti-queryinj` | CWE-943 (non-SQL) query injection | user string → Cypher/graph query built by concatenation | `app/graph.py`, `app/search.py` |
| `pydash-massassign` | CWE-915 mass assignment | attacker-controlled key path → deep `set_(obj, path, value)` reaching `__class__`/protected attrs | `app/merge.py`, `app/model.py` |
| `aiplatform-xss` | CWE-79 XSS | user text → HTML rendered without escaping | `app/render.py`, `app/page.py` |

- [ ] **Step 1: Write `ATTRIBUTIONS.md`.** Create `src/coding_agent_playbook/resources/eval_tasks/ATTRIBUTIONS.md` (spec §4.2 — the single CC BY vehicle for the whole fixture tree):

  ```markdown
  # Attributions

  The `find-injected-vuln-*` evaluation fixtures in this directory are **derived
  works** built from the **CrossCommitVuln-Bench** dataset.

  - **Source:** CrossCommitVuln-Bench — a curated corpus of real CVEs in real
    Python projects.
  - **Author / copyright:** Copyright 2026 Arunabh Majumdar.
  - **License:** Creative Commons Attribution 4.0 International (CC BY 4.0) —
    <https://creativecommons.org/licenses/by/4.0/>
  - **Paper:** arXiv:2604.21917
  - **DOI:** 10.5281/zenodo.19338596

  ## Indication of changes (required by CC BY 4.0 §3(a))

  The dataset's per-CVE annotations were **transformed into question-answer
  scenario fixtures**. Each fixture's source files are **original code that
  paraphrases only the source→sink pattern** described in the CVE's CC BY
  annotation and reproduction notes. **The upstream project source code was not
  consulted** when authoring any fixture, so no upstream (potentially
  GPL/AGPL-licensed) code is reproduced or adapted here. The neutral,
  needle-hiding task prompt was authored independently.

  ## Per-fixture provenance

  Each fixture's `task.toml` `description` cites the specific CVE it derives from.
  The fixtures and their originating CVEs:

  | Fixture | CVE | CWE |
  |---|---|---|
  | find-injected-vuln-modoboa-cmdi | CVE-2099-00018 | CWE-78 |
  | find-injected-vuln-ormar-sqli | CVE-2099-00016 | CWE-89 |
  | find-injected-vuln-changedetection-ssrf | CVE-2026-27696 | CWE-918 |
  | find-injected-vuln-langroid-codeinj | CVE-2099-00004 | CWE-94 |
  | find-injected-vuln-pytorch-deserial | CVE-2099-00003 | CWE-502 |
  | find-injected-vuln-changedetection-pathtraversal | CVE-2099-00023 | CWE-22 |
  | find-injected-vuln-authlib-sigbypass | CVE-2099-00022 | CWE-347 |
  | find-injected-vuln-graphiti-queryinj | CVE-2026-32247 | CWE-943 |
  | find-injected-vuln-pydash-massassign | CVE-2099-00008 | CWE-915 |
  | find-injected-vuln-aiplatform-xss | CVE-2099-00014 | CWE-79 |
  ```

- [ ] **Step 2: Author each of the nine fixtures** by applying the checklist above with its row's parameters. Every `task.toml` follows the exact C2 shape; only `id`, `description`, `[setup].files`, the `[gate.bind]` handles/paths, and the three rubric `judge=` strings change per fixture. Example — `find-injected-vuln-ormar-sqli/task.toml`:

  ```toml
  id = "find-injected-vuln-ormar-sqli"
  prompt_id = "find-injected-vulnerability"
  description = "SQL injection paraphrase; derived from CVE-2099-00016 (CrossCommitVuln-Bench, CC BY 4.0, A. Majumdar). See resources/eval_tasks/ATTRIBUTIONS.md."
  version = 1

  [setup]
  files = ["app/queries.py", "app/db.py", "pyproject.toml"]
  project_kind = "library"

  [invocation]
  prompt_args = "This project contains a security vulnerability. Find where untrusted input reaches a dangerous operation and classify the exploit."
  input_variables = {}

  [gate]
  expr = "queries_untouched and db_untouched"

  [gate.bind]
  queries_untouched = { check = "file_unchanged", path = "app/queries.py" }
  db_untouched      = { check = "file_unchanged", path = "app/db.py" }

  [rubric]
  expr = "identifies_exact_vuln"

  [rubric.bind]
  identifies_exact_vuln = { fail = 0.5, rubric = [
    { judge = "The answer names SQL injection (CWE-89) as the vulnerability class.", weight = 0.34 },
    { judge = "The answer traces the untrusted source (a user-supplied filter value) to the sink where it is concatenated into a raw SQL string and executed.", weight = 0.33 },
    { judge = "The answer cites the specific fixture files where the source and the sink live (app/queries.py and app/db.py).", weight = 0.33 },
  ] }

  [verdict]
  trial = "gate and rubric"
  ```

  And its two paraphrase files (original, upstream not consulted) — `find-injected-vuln-ormar-sqli/setup/app/db.py`:

  ```python
  """Minimal raw-SQL execution helper.

  Derived (paraphrased) from CVE-2099-00016 (CrossCommitVuln-Bench, CC BY 4.0,
  A. Majumdar). Original fixture code; upstream project source was not consulted.
  """

  from __future__ import annotations


  class Database:
      def __init__(self) -> None:
          self._rows: list[dict[str, str]] = []

      def execute(self, sql: str) -> list[dict[str, str]]:
          """Execute a raw SQL string verbatim (no parameter binding)."""
          # The caller is expected to pass a complete SQL statement.
          return [row for row in self._rows if sql]
  ```

  `find-injected-vuln-ormar-sqli/setup/app/queries.py`:

  ```python
  """User-facing query builders.

  Derived (paraphrased) from CVE-2099-00016 (CrossCommitVuln-Bench, CC BY 4.0,
  A. Majumdar). Original fixture code; upstream project source was not consulted.
  """

  from __future__ import annotations

  from app.db import Database


  def find_users_by_name(db: Database, name: str) -> list[dict[str, str]]:
      """Look up users whose name matches the request-supplied ``name``.

      ``name`` comes straight from the request and is interpolated into the SQL
      text with no escaping or parameter binding.
      """
      sql = f"SELECT * FROM users WHERE name = '{name}'"
      return db.execute(sql)
  ```

  Repeat this exact pattern for the other eight fixtures using their table rows. Keep every paraphrase small (2 files, ~15–25 lines each), one isolated source→sink chain per fixture, header docstring stating upstream was not consulted.

- [ ] **Step 3: Sanity-load each new fixture.** Run per-fixture end-to-end hermetically:
  ```
  for s in ormar-sqli changedetection-ssrf langroid-codeinj pytorch-deserial \
           changedetection-pathtraversal authlib-sigbypass graphiti-queryinj \
           pydash-massassign aiplatform-xss; do
    uv run playbook eval find-injected-vuln-$s --run --runner fake || echo "FAILED: $s"
  done
  ```
  Each must complete with the review-only gate passing under the no-op fake runner (no `FAILED:` line).

- [ ] **Step 4: Validate the whole prompt↔task graph now that all ten fixtures exist.** `uv run pytest tests/integration/test_find_injected_vuln_prompt.py -q` → still green; `uv run ruff check src/coding_agent_playbook/resources` is not applicable to TOML, so run `uv run playbook eval --list 2>/dev/null | grep find-injected-vuln | wc -l` → **10**.

- [ ] **Step 5: Commit.**
  ```
  git add src/coding_agent_playbook/resources/eval_tasks/ATTRIBUTIONS.md \
          src/coding_agent_playbook/resources/eval_tasks/find-injected-vuln-*
  git commit -m "feat(playbook): add nine find-injected-vuln fixtures (CWE-89/918/94/502/22/347/943/915/79) + CC BY ATTRIBUTIONS.md"
  ```

---

### Task C4 — bump the shipped-count test to 20, add needle-leak + attribution + per-fixture hermetic assertions (PR-able deliverable)

**Repo/cwd:** `/Users/msobroza/Projects/coding-agent-playbook/coding-agent-playbook`

**Files**
- Modify: `tests/integration/test_shipped_eval_tasks_c7.py` (bump `test_ships_ten_tasks` count `10 → 20`)
- Create: `tests/integration/test_crosscommitvuln_fixtures.py` (needle-leak on every fixture's `prompt_args`, attribution-file-exists, hermetic `--runner fake` per fixture)

**Interfaces**
- Consumes: `discover_eval_task_dirs`, `load_eval_task_from_dir`, `validate_tasks`, `materialize`/`capture`/`score`, `StubJudge`, the `fake` runner.
- Produces: the green whole-suite gate — this task's passing state is the group's PR deliverable.

- [ ] **Step 1: Bump the count assertion.** Edit `tests/integration/test_shipped_eval_tasks_c7.py`. The count must equal the number actually authored: **10 original + 10 new = 20**. (If any §7.2 fixture was dropped in C3, set this to the real total and trim C1's `eval_tasks:` to match in the same commit.)

  ```python
  def test_ships_ten_tasks() -> None:
      tasks = _shipped_tasks()
      # 10 original fixtures + 10 find-injected-vuln-* crosscommitvuln fixtures (C1-C3).
      assert len(tasks) == 20
  ```

- [ ] **Step 2: Run the whole shipped-suite test, see it pass.** `uv run pytest tests/integration/test_shipped_eval_tasks_c7.py -q` → green: the count is 20, every fixture compiles to `{gate, rubric}` with verdict `"gate and rubric"`, and `test_shipped_tasks_validate_clean` now passes because all ten prompt back-refs resolve on-disk (the C1 red is cleared).

- [ ] **Step 3: Write the needle-leak + attribution + per-fixture hermetic test.** Create `tests/integration/test_crosscommitvuln_fixtures.py`:

  ```python
  """Group-C invariants over the find-injected-vuln-* fixtures (spec §9.2).

  - needle-leak: each fixture's prompt_args contains none of its CVE's banned
    tokens, including the v2 framing vocabulary and commit dates (§5.2);
  - the shared CC BY ATTRIBUTIONS.md exists and carries the license URL,
    author credit, and DOI (§4.2);
  - each fixture runs hermetically under the fake runner with a StubJudge.
  """

  from __future__ import annotations

  import subprocess
  from pathlib import Path
  from typing import Any

  import pytest

  from coding_agent_playbook.evals.judge import StubJudge
  from coding_agent_playbook.evals.loader import load_eval_task_from_dir
  from coding_agent_playbook.evals.runner import capture, materialize
  from coding_agent_playbook.evals.scoring import score

  _TASKS_ROOT = (
      Path(__file__).resolve().parents[2]
      / "src" / "coding_agent_playbook" / "resources" / "eval_tasks"
  )

  # Per-fixture banned tokens: CVE id, CWE id (both forms), sink symbols, flaw-class
  # words, contributing-commit short hashes, and every commit date (spec §5.2). The
  # v2 framing bans below are shared across all fixtures.
  _FRAMING_BANS = (
      "commit", "commits", "multiple", "multi-commit", "gradually", "over time",
      "across", "benign", "static analysis", "sast", "per-commit", "scanner",
      "individually",
  )
  _PER_FIXTURE_BANS: dict[str, tuple[str, ...]] = {
      "find-injected-vuln-modoboa-cmdi": (
          "cve-2026-27602", "cwe-78", "78", "exec_cmd", "command injection",
          "shell=true", "subprocess", "jobs.py", "sysutils.py", "mailbox",
          "43ace1de", "a81ba437",
      ),
      "find-injected-vuln-ormar-sqli": (
          "cve-2026-26198", "cwe-89", "89", "sql injection", "sql", "queries.py",
          "db.py",
      ),
      "find-injected-vuln-changedetection-ssrf": (
          "cve-2026-27696", "cwe-918", "918", "ssrf", "requests.get", "fetch.py",
          "watch.py",
      ),
      "find-injected-vuln-langroid-codeinj": (
          "cve-2025-46724", "cwe-94", "94", "code injection", "eval", "exec",
          "tools.py", "runner.py",
      ),
      "find-injected-vuln-pytorch-deserial": (
          "cve-2025-32434", "cwe-502", "502", "deserialization", "pickle",
          "torch.load", "loader.py", "registry.py",
      ),
      "find-injected-vuln-changedetection-pathtraversal": (
          "cve-2026-29065", "cwe-22", "22", "path traversal", "traversal",
          "storage.py", "views.py",
      ),
      "find-injected-vuln-authlib-sigbypass": (
          "cve-2026-27962", "cwe-347", "347", "signature bypass", "alg=none",
          "jwt_verify.py", "auth.py",
      ),
      "find-injected-vuln-graphiti-queryinj": (
          "cve-2026-32247", "cwe-943", "943", "query injection", "cypher",
          "graph.py", "search.py",
      ),
      "find-injected-vuln-pydash-massassign": (
          "cve-2025-58367", "cwe-915", "915", "mass assignment", "set_",
          "merge.py", "model.py",
      ),
      "find-injected-vuln-aiplatform-xss": (
          "cve-2026-2472", "cwe-79", "79", "xss", "cross-site scripting",
          "render.py", "page.py",
      ),
  }

  _FIXTURE_IDS = sorted(_PER_FIXTURE_BANS)


  @pytest.fixture
  def stub_uv_sync(monkeypatch: pytest.MonkeyPatch) -> None:
      real_run = subprocess.run

      def _stub_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
          if args and args[0] == "uv":
              return subprocess.CompletedProcess(args, 0, "", "")
          return real_run(args, **kwargs)

      monkeypatch.setattr("coding_agent_playbook.evals.runner.subprocess.run", _stub_run)
      monkeypatch.setattr(
          "coding_agent_playbook.evals.runner.shutil.which", lambda _b: "/fake/uv"
      )


  def _task(fixture_id: str) -> Any:
      return load_eval_task_from_dir(
          _TASKS_ROOT / fixture_id, source_package="coding_agent_playbook"
      )


  @pytest.mark.parametrize("fixture_id", _FIXTURE_IDS)
  def test_prompt_args_leak_no_needle(fixture_id: str) -> None:
      query = _task(fixture_id).invocation.prompt_args.lower()
      banned = _PER_FIXTURE_BANS[fixture_id] + _FRAMING_BANS
      offenders = [tok for tok in banned if tok in query]
      assert offenders == [], f"{fixture_id}: banned token(s) leaked into query: {offenders}"


  def test_attributions_file_present() -> None:
      attributions = (_TASKS_ROOT / "ATTRIBUTIONS.md").read_text(encoding="utf-8")
      assert "creativecommons.org/licenses/by/4.0" in attributions
      assert "Arunabh Majumdar" in attributions
      assert "10.5281/zenodo.19338596" in attributions


  @pytest.mark.parametrize("fixture_id", _FIXTURE_IDS)
  def test_fixture_runs_hermetically(
      fixture_id: str, tmp_path: Path, stub_uv_sync: None
  ) -> None:
      task = _task(fixture_id)
      baseline = materialize(task, tmp_path / "scratch")
      candidate = capture(baseline.repo_root, baseline, "transcript")
      report = score(task, baseline, candidate, judge=StubJudge(default=0.9))
      gate = next(c for c in report.checks if c.check_id == "gate")
      rubric = next(c for c in report.checks if c.check_id == "rubric")
      assert gate.passed is True        # review-only gate: no-op fake runner => unchanged
      assert rubric.status == "run"
      assert rubric.passed is True      # composite 0.9 >= 0.5 fail floor
  ```

  > NOTE: the `_PER_FIXTURE_BANS` paths/symbols must match whatever files/sinks C3 actually authored. If a C3 fixture used different file names, update its row here so the leak test stays honest.

- [ ] **Step 4: Run the new invariants, see them pass.** `uv run pytest tests/integration/test_crosscommitvuln_fixtures.py -q` → all parametrized needle-leak, attribution, and hermetic-run cases green.

- [ ] **Step 5: Run the full playbook gate set (PR gate).**
  ```
  uv run pytest tests/ -q
  uv run ruff check
  uv run mypy --strict
  ```
  All green. Spot-check two fixtures end-to-end: `uv run playbook eval find-injected-vuln-modoboa-cmdi --run --runner fake` and `uv run playbook eval find-injected-vuln-aiplatform-xss --run --runner fake`.

- [ ] **Step 6: Commit.**
  ```
  git add tests/integration/test_shipped_eval_tasks_c7.py \
          tests/integration/test_crosscommitvuln_fixtures.py
  git commit -m "test(playbook): bump shipped-task count to 20 + needle-leak, attribution, and hermetic run guards for find-injected-vuln fixtures"
  ```

- [ ] **Step 7: Open the PR (group deliverable).** This is piece (C) from spec §11 — fully independent of Groups A/B. Branch off `main`, push, and open a PR titled *"feat(playbook): CrossCommitVuln find-injected-vulnerability prompt + 10 needle-search fixtures"* with its green gate set (`uv run pytest`, `uv run ruff check`, `uv run mypy --strict`, and per-fixture `playbook eval … --runner fake`) recorded in the PR body.