# sentence-transformers torchvision-hint fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement spec `docs/superpowers/specs/2026-07-11-sentence-transformers-torchvision-bug-spec.md` — Alternative D (catch-and-rethrow with `_TORCHVISION_HINT`) + B3 (`transformers>=4.48,<6.0` on both ST-family extras) + docs, shipped as one PR.

**Architecture:** The change lives entirely inside one existing strategy class (`SentenceTransformersEmbedder`): a module-level `_TORCHVISION_HINT` constant + `_mentions_torchvision` chain-walking helper, and a widened `(ImportError, AttributeError)` construction guard that checks torchvision-mentioning failures first (any backend), then preserves today's raw-propagation (torch) and backend-extra-hint (non-torch) paths. Dependency metadata gains an upper bound on `transformers`; docs gain the torchvision-not-included caveat at four audited sites. No MCP surface, YAML config, or new modules.

**Tech Stack:** Python 3.11+, pytest (fake-ST-module pattern, default venv — no torch/ST/torchvision), tomllib for the metadata guard test, uv for the relock.

**Spec:** `docs/superpowers/specs/2026-07-11-sentence-transformers-torchvision-bug-spec.md` (authoritative; ACs quoted below by number).

---

## File structure

| File | Action | Responsibility |
|---|---|---|
| `python/pydocs_mcp/extraction/strategies/embedders/sentence_transformers.py` | Modify | `_TORCHVISION_HINT` + `_mentions_torchvision` + widened guard + `_INSTALL_HINT` extension (spec §3.2) |
| `tests/extraction/strategies/embedders/test_sentence_transformers_embedder.py` | Modify | New tests AC1–AC4(b), AC6, AC7; AC4(a)/AC5 preserved unmodified |
| `tests/test_repository_hygiene.py` | Modify | AC8 metadata guard (tomllib precedent already in this file) |
| `pyproject.toml` | Modify | Extras bounds + rationale comment (spec §3.4 exact diff) |
| `uv.lock` | Relock | `uv lock` — metadata-only change expected (all pins already satisfy the bound) |
| `README.md` | Modify | :304 payload caveat, :345 range, :351-361 air-gap note (AC10) |
| `INSTALL.md` | Modify | GPU section torchvision caveat (AC10) |

Branch: `fix/st-torchvision-hint` off `origin/main`. Commits carry NO `Co-Authored-By` trailers (user's global authorship policy).

---

### Task 1: Widened construction guard + AC1–AC7 tests

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/embedders/sentence_transformers.py:38-44` (`_INSTALL_HINT`), `:102-116` (guard), new module constants after `_INSTALL_HINT`
- Test: `tests/extraction/strategies/embedders/test_sentence_transformers_embedder.py` (append after `test_torch_backend_construction_failure_propagates_raw`, line 223)

- [ ] **Step 1: Write the failing tests (AC1, AC2, AC3) + characterization tests (AC4b, AC6, AC7)**

Append to `tests/extraction/strategies/embedders/test_sentence_transformers_embedder.py` (import `_TORCHVISION_HINT` in the existing import block at the top):

```python
from pydocs_mcp.extraction.strategies.embedders.sentence_transformers import (
    SentenceTransformersEmbedder,
    _TORCHVISION_HINT,
)
```

```python
# ── torchvision-mentioning construction failures get an actionable hint ──
# (spec docs/superpowers/specs/2026-07-11-sentence-transformers-torchvision-bug-spec.md §5)


def test_torchvision_failure_gets_hint_on_torch_backend(monkeypatch) -> None:
    """AC1: a torchvision-mentioning ImportError during construction on the
    default torch backend is re-raised as _TORCHVISION_HINT, chained."""
    records: list[dict] = []
    original = ImportError(
        "`SomeImageProcessorFast` requires the Torchvision library but it "
        "was not found in your environment"
    )
    _install_fake_st_module(monkeypatch, records, fail=original)
    with pytest.raises(ImportError) as excinfo:
        SentenceTransformersEmbedder(model_name="Qwen/Qwen3-Embedding-0.6B", dim=1024)
    msg = str(excinfo.value)
    assert msg == _TORCHVISION_HINT
    assert "torchvision" in msg
    assert "transformers>=5.10" in msg
    assert "exact-pins" in msg
    assert "sentence-transformers[image]" in msg
    assert excinfo.value.__cause__ is original


def test_chained_torchvision_failure_detected_via_cause(monkeypatch) -> None:
    """AC2: the torchvision marker one level down the __cause__ chain (the
    exact shape ST's suggest_extra_on_exception produces — outer message is
    torchvision-free, forcing a genuine chain walk) is still detected."""
    inner = ImportError(
        "`SomeImageProcessorFast` requires the Torchvision library but it "
        "was not found in your environment"
    )
    outer = ImportError(
        "To install the required dependencies, run: "
        'pip install -U "sentence-transformers[image]"'
    )
    outer.__cause__ = inner
    records: list[dict] = []
    _install_fake_st_module(monkeypatch, records, fail=outer)
    with pytest.raises(ImportError) as excinfo:
        SentenceTransformersEmbedder(model_name="m", dim=_DIM)
    assert str(excinfo.value) == _TORCHVISION_HINT
    assert excinfo.value.__cause__ is outer


def test_torchvision_failure_on_nontorch_backend_gets_torchvision_hint(monkeypatch) -> None:
    """AC3: torchvision check runs FIRST — an openvino-backend torchvision
    failure must NOT be misdiagnosed as a missing-optimum problem."""
    records: list[dict] = []
    _install_fake_st_module(
        monkeypatch,
        records,
        fail=ImportError("X requires the Torchvision library but it was not found"),
    )
    with pytest.raises(ImportError) as excinfo:
        SentenceTransformersEmbedder(model_name="m", dim=_DIM, backend="openvino")
    assert str(excinfo.value) == _TORCHVISION_HINT
    assert "sentence-transformers[openvino]" not in str(excinfo.value)


def test_non_torchvision_import_error_on_torch_backend_propagates_raw(monkeypatch) -> None:
    """AC4(b): a plain ImportError (no torchvision mention) on the torch
    backend escapes un-rewrapped — the widened guard reroutes exactly this
    case, so the existing RuntimeError test alone cannot pin it."""
    err = ImportError("No module named 'foo'")
    records: list[dict] = []
    _install_fake_st_module(monkeypatch, records, fail=err)
    with pytest.raises(ImportError) as excinfo:
        SentenceTransformersEmbedder(model_name="m", dim=_DIM)
    assert excinfo.value is err


def test_non_torchvision_attribute_error_propagates_raw(monkeypatch) -> None:
    """AC6: the catch widens to AttributeError only to INSPECT, never to
    swallow — an unrelated AttributeError escapes untouched."""
    err = AttributeError("module 'transformers' has no attribute 'Whatever'")
    records: list[dict] = []
    _install_fake_st_module(monkeypatch, records, fail=err)
    with pytest.raises(AttributeError) as excinfo:
        SentenceTransformersEmbedder(model_name="m", dim=_DIM)
    assert excinfo.value is err


def test_module_import_never_touches_heavy_stack() -> None:
    """AC7: importing the provider module in the default venv must not pull
    torch / torchvision / sentence_transformers (import-time cleanliness)."""
    import importlib
    import sys

    importlib.import_module("pydocs_mcp.extraction.strategies.embedders.sentence_transformers")
    assert "torchvision" not in sys.modules
    assert "torch" not in sys.modules
    assert "sentence_transformers" not in sys.modules
```

- [ ] **Step 2: Run — AC1/AC2/AC3 must FAIL, AC4b/AC6/AC7 must PASS (characterization)**

Run: `pytest tests/extraction/strategies/embedders/test_sentence_transformers_embedder.py -q`
Expected: 3 failures (AC1: ImportError propagates raw with the upstream message, not `_TORCHVISION_HINT`; AC2 same; AC3: gets the `[openvino]` hint instead) — plus an ImportError collecting `_TORCHVISION_HINT` until the constant exists; add the constant stub is NOT allowed — instead expect `ImportError: cannot import name '_TORCHVISION_HINT'` as the initial red state. AC4b/AC6/AC7 pass once the import resolves.

- [ ] **Step 3: Implement spec §3.2 exactly**

In `python/pydocs_mcp/extraction/strategies/embedders/sentence_transformers.py`:

(a) Replace `_INSTALL_HINT` (lines 38-44) with:

```python
_INSTALL_HINT = (
    "The 'sentence_transformers' embedding provider requires the "
    "'sentence-transformers' extra. Install with: "
    "pip install 'pydocs-mcp[sentence-transformers]' (pulls "
    "sentence-transformers + torch + transformers; expect ~1-5 GB "
    "depending on CUDA wheel selection). torchvision is NOT included; "
    "if a model load demands it, see the error raised at construction "
    "time for remedies."
)
```

(b) Add after `_INSTALL_HINT` the constant + helper from spec §3.2 verbatim (`_TORCHVISION_HINT`, `_mentions_torchvision`).

(c) Replace the construction `try/except` (lines 102-116) with the spec §3.2 widened guard verbatim.

- [ ] **Step 4: Run the module's tests — ALL pass**

Run: `pytest tests/extraction/strategies/embedders/test_sentence_transformers_embedder.py -q`
Expected: all pass, including unmodified AC4(a) `test_torch_backend_construction_failure_propagates_raw` and AC5 `test_nontorch_backend_construction_failure_gets_install_hint`.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/sentence_transformers.py \
        tests/extraction/strategies/embedders/test_sentence_transformers_embedder.py
git commit -m "fix(embedders): actionable hint for torchvision-mentioning ST construction failures"
```

### Task 2: AC8 metadata guard + pyproject bounds + relock

**Files:**
- Test: `tests/test_repository_hygiene.py` (append)
- Modify: `pyproject.toml:71` + `:75` (spec §3.4 exact diff)
- Relock: `uv.lock`

- [ ] **Step 1: Write the failing AC8 test**

Append to `tests/test_repository_hygiene.py`:

```python
def test_st_family_extras_bound_transformers_below_6() -> None:
    """Both ST-family extras pin transformers>=4.48,<6.0 — guards against a
    future edit silently re-unbounding the float (spec
    docs/superpowers/specs/2026-07-11-sentence-transformers-torchvision-bug-spec.md AC8)."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]
    for extra in ("sentence-transformers", "openvino"):
        transformers_reqs = [r for r in extras[extra] if r.startswith("transformers")]
        assert transformers_reqs == ["transformers>=4.48,<6.0"], (
            f"[{extra}] must pin 'transformers>=4.48,<6.0'; got {transformers_reqs}"
        )
```

- [ ] **Step 2: Run — must FAIL**

Run: `pytest tests/test_repository_hygiene.py::test_st_family_extras_bound_transformers_below_6 -q`
Expected: FAIL — got `["transformers>=4.48"]`.

- [ ] **Step 3: Apply the spec §3.4 pyproject diff verbatim** (both extras gain `,<6.0`; the `sentence-transformers` extra's comment block gains the seven rationale lines).

- [ ] **Step 4: Relock + verify**

Run: `uv lock && uv lock --check`
Expected: lock succeeds, `--check` green, `git diff uv.lock` shows metadata-only bound change (no version bumps — all pins already satisfy `<6.0`). If versions move: STOP, investigate (spec §3.4 predicts metadata-only).

- [ ] **Step 5: Run the test — PASS; commit**

Run: `pytest tests/test_repository_hygiene.py -q` → all pass.

```bash
git add pyproject.toml uv.lock tests/test_repository_hygiene.py
git commit -m "fix(deps): cap transformers <6.0 on both sentence-transformers-family extras"
```

### Task 3: Docs (AC10)

**Files:**
- Modify: `README.md:304`, `README.md:345`, `README.md:351-361`, `INSTALL.md:86-88`

- [ ] **Step 1: README.md:304** — extra payload caveat. Replace:

```
  (`pip install 'pydocs-mcp[sentence-transformers]'`, ~1-5 GB with torch),
```

with:

```
  (`pip install 'pydocs-mcp[sentence-transformers]'`, ~1-5 GB with torch;
  torchvision is **not** included — if a model load demands it, the
  construction-time error message walks through the remedies),
```

- [ ] **Step 2: README.md:345** — replace `` `transformers` (≥ 4.48). `` with `` `transformers` (≥ 4.48, < 6). ``

- [ ] **Step 3: README.md air-gap bullet (:351-361)** — after the sentence ending "`openai` rejects a local path.", insert:

```
  Package mirrors: the `[sentence-transformers]` extra never pulls
  torchvision, so offline package mirrors need no torchvision wheel; if a
  model load does demand it, the mirror must add a torchvision wheel whose
  version exactly matches the mirrored torch wheel (e.g. torchvision
  0.26.0 ↔ torch 2.11.0, 0.28.0 ↔ torch 2.13.0) — a skewed pair is
  unresolvable offline.
```

- [ ] **Step 4: INSTALL.md GPU bullet** — replace:

```
- `sentence_transformers` dense provider: a CUDA build of torch (pulled by the
  `[sentence-transformers]` extra on a CUDA host).
```

with:

```
- `sentence_transformers` dense provider: a CUDA build of torch (pulled by the
  `[sentence-transformers]` extra on a CUDA host). The extra does **not** pull
  torchvision; if a model load demands it, install a torchvision build that
  exactly matches your installed torch (torchvision exact-pins its torch
  sibling — e.g. torchvision 0.26.0 ↔ torch 2.11.0), or upgrade
  `transformers` to ≥ 5.10, which falls back to Pillow for image processors.
```

- [ ] **Step 5: README jargon audit** (CLAUDE.md §README rule):

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
    -not -path "*/node_modules/*" -not -path "*/.git/*" | \
    xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
```

Expected: no matches. Also verify no third-party AI coding-assistant product names in touched hunks (vendor neutrality).

- [ ] **Step 6: Commit**

```bash
git add README.md INSTALL.md
git commit -m "docs: torchvision-not-included caveat at every site naming the ST extra payload"
```

### Task 4: Adversarial spec-compliance review (ultracode)

- [ ] Workflow: fan out independent verifiers — one per AC group (AC1–AC7 code+tests, AC8 metadata, AC10 docs), each reading the spec section and the diff, prompted to REFUTE compliance; plus a completeness critic checking §2 non-goals aren't violated (no MCP/YAML change, no torchvision dep, no `[late-interaction]` change). Fix anything confirmed; re-verify.

### Task 5: Full CI gate set (AC9), push, PR

- [ ] **Step 1: Run every gate from CLAUDE.md §Tests & Lint:**

```bash
ruff check python/ tests/ benchmarks/
ruff format --check python/ tests/ benchmarks/
mypy python/pydocs_mcp
complexipy python/pydocs_mcp --max-complexity-allowed 15
vulture python/pydocs_mcp --min-confidence 80
pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
uv lock --check
uv export --frozen --no-emit-project --no-group docs --format requirements-txt > requirements-audit.txt
uvx pip-audit --strict --requirement requirements-audit.txt
cargo fmt --check && cargo clippy -- -D warnings && cargo test
```

Expected: all green (Rust untouched; cargo gates confirm no accidental drift).

- [ ] **Step 2: Push + PR** (base `main`, head `fix/st-torchvision-hint`; no Co-Authored-By trailer; do NOT merge — user gives explicit go):

```bash
git push -u origin fix/st-torchvision-hint
gh pr create --base main --title "fix(embedders): actionable torchvision hint + transformers<6.0 cap (spec 2026-07-11)" --body "..."
```

---

## Self-review notes

- **Spec coverage:** AC1–AC8 → Tasks 1–2; AC9 → Task 5; AC10 → Task 3; §6.2 step 1 (single PR) → Task 5. §6.2 steps 2–4 (user traceback request, benchmark-box check, escalation) are post-merge human actions recorded in the PR body, not code tasks.
- **Non-goals honored:** no torchvision dep, no MCP/YAML surface change, no `requires_backends` shim, `[late-interaction]` untouched (verified in Task 4).
- **AC2 nuance:** outer message is torchvision-free (`"sentence-transformers[image]"` contains no `torchvision` substring), so the chain walk is genuinely exercised — the spec's parenthetical is satisfied without modification.
- **Red-state nuance (Task 1 Step 2):** the new tests import `_TORCHVISION_HINT`, which doesn't exist yet, so the initial red is a collection-time ImportError — acceptable red per TDD (the test file fails); after §3.2 lands, red→green is verified per-test.
