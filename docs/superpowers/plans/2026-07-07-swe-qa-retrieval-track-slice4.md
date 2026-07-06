# SWE-QA / SWE-QA-Pro Retrieval Track (Slice 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two new benchmark datasets — SWE-QA-Pro (primary: 260 QA, 26 Python repos, per-row commit pins, near-regular `(path.py: line N-M)` answer citations) and SWE-QA (secondary: 720 QA, 15 Python repos, noisier citations) — evaluated on the existing harness with file-level pseudo-qrels and per-category reporting, per spec §D14.

**Architecture:** Both datasets become adapters under `benchmarks/src/benchmarks/eval/datasets/` satisfying the existing `Dataset` Protocol (`base_dataset.py:46-57`) and `@dataset_registry.register` convention. Gold labels are **file-level pseudo-qrels** extracted from answer citations into `GoldAnswer.file_set` (the field already exists); a small relevance extension makes `file_set` drive the existing metrics via `RetrievedItem.source_path` matching. Corpora are the pinned repo checkouts, cloned once into the adapter cache and materialized per task via the existing `corpus_source` pattern. Existing metrics (`recall@k`, `ndcg@k`, `mrr`, `precision@1`) are reused unchanged; a report extension breaks results out by SWE-QA-Pro's `qa_type` (What/Where/How/Why). **Spec §D14 corrections discovered during research land with this plan** (see Task 7): the SWE-QA HF release has NO per-row taxonomy (720 rows/15 repos vs the paper's 576/11, unexplained), so the Why-category probe comes from SWE-QA-Pro's `qa_type` field; SWE-QA commit pins live only in the companion GitHub repo (`peng-weihan/SWE-QA-Bench`, `repo_commit.txt`).

**Verified dataset facts (2026-07-06):**
- `TIGER-Lab/SWE-QA-Pro-Bench` @ revision `596892dac60b6f500f01a7dc2becb9f66593b7b7`, MIT. Single file `data/test.jsonl`, 260 rows, 26 repos × 10 QA, one full 40-hex `commit_id` per repo. Columns: `repo` ("org/name"), `commit_id`, `cluster` {id, name} (48 ids), `qa_type` {class_name ∈ What 51/Where 77/How 67/Why 65, sub_class_name — 12 values}, `question`, `answer`. Python-only (paper Limitations + full repo census). 250/260 answers cite file paths, 237/260 with line ranges, pattern ≈ `(src/qibo/models/variational.py: line 583-590)`; ~10 rows citation-free → excluded WITH a logged count (no-silent-caps rule).
- `swe-qa/SWE-QA-Benchmark` @ revision `07e206aa29fdad0cf3f1d532ff077f9705387348`, Apache-2.0. Config `default`, splits: `default` (720) + 15 per-repo splits of 48 (`scikit_learn` split ↔ `scikit-learn.jsonl`). Columns: `question`, `answer` ONLY — repo inferred from split name; NO commits in the data. Pins (short SHAs) from `github.com/peng-weihan/SWE-QA-Bench` `repo_commit.txt`: astropy 0a041d3, django 14fc2e9, flask 85c5d93, matplotlib a5e1f60, pylint 44740e5, pytest 5989efe, requests 46e939b, scikit-learn adb1ae7, sphinx 6c9e320, sqlfluff db9801b, sympy 3c817ed, xarray 40119bf, conan 52f43d9, reflex fe0f946, streamlink ab1f365. 607/720 answers cite a file (548 slash-qualified; ~8% bare filenames needing unique-basename resolution); UNVERIFIED that the 720-row release matches these pins → file-level labels only (safe under line drift), plus a spot-check step in Task 4.

**Harness facts (verified):** `Dataset` Protocol {name, revision, def tasks() -> AsyncIterator[EvalTask]} at `base_dataset.py:46-57`; `EvalTask(task_id, query, gold, corpus_source, metadata)`; `GoldAnswer(ast_body=None, file_set=(), extra={})`; relevance dispatch in `metrics/_relevance.py:35-63` (ast_body → AST match; extra["resolved_chunk_ids"] → membership); registry via `serialization.py` `_Registry` + decorator; adapter registration fires from the import in `datasets/__init__.py`; runner `--dataset` flag + `dataset_registry.build(name, **kwargs)` (sweep.py:438); `run_sweep(...)` programmatic entry (sweep.py); metrics registered: recall@k, ndcg@k, mrr, precision@1, pass@1-needle, coverage, library_resolution@1; configs = AppConfig overlay YAMLs in `benchmarks/configs/` whose `pipelines.chunk[0].pipeline_path` selects a blueprint (config-relative); `PydocsMcpSystem` indexes corpora via the composite UoW factory; conftest autouse fixtures mock the embedder + LLM (offline suite); adapters cache under `~/.cache/pydocs-mcp/<name>` with `fixture_path` for hermetic tests; corpus materialization via `corpus.py:22` `materialize_corpus` + default-arg-closure pattern; tests: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`, asyncio_mode=auto, ad-hoc systems registered by direct `_items` insert with finally-pop.

**Conventions:** same as plans 2a/2b (venv python, ruff check+format, complexipy ≤15 then restore snapshot, plain commits, no trailers). All new code lives under `benchmarks/` — zero overlap with slices 2/3.

---

### Task 1: Citation extraction — pure functions + fixtures

**Files:**
- Create: `benchmarks/src/benchmarks/eval/datasets/_citations.py`
- Test: `benchmarks/tests/eval/test_citation_extraction.py`

- [ ] **Step 1: Failing tests** (table-driven over VERBATIM answer excerpts from both datasets):

```python
"""Pseudo-qrel citation extraction (spec §D14): answers → file-level labels."""

from benchmarks.eval.datasets._citations import extract_path_citations, resolve_bare_filenames

_PRO_ANSWER = (
    "First, it calls the parent constructor super().__init__() to inherit QAOA's "
    "initialization (src/qibo/models/variational.py: line 583-590). It also overrides "
    "the minimize method (src/qibo/models/variational.py: lines 601-640)."
)
_SWEQA_ANSWER = (
    "Implementation details (from `backend_pgf.py` lines 240-252):\n"
    "1. **Character-by-character reading**: reads from `self.latex.stdout`."
)


def test_extracts_relative_paths_with_line_ranges() -> None:
    cites = extract_path_citations(_PRO_ANSWER)
    assert ("src/qibo/models/variational.py", 583, 590) in cites
    assert ("src/qibo/models/variational.py", 601, 640) in cites


def test_extracts_bare_filenames_and_backticked_paths() -> None:
    cites = extract_path_citations(_SWEQA_ANSWER)
    assert ("backend_pgf.py", 240, 252) in cites


def test_dedupes_paths_keeps_first_range() -> None:
    cites = extract_path_citations(_PRO_ANSWER + " " + _PRO_ANSWER)
    assert len([c for c in cites if c[0] == "src/qibo/models/variational.py"]) == 2


def test_no_citation_returns_empty() -> None:
    assert extract_path_citations("Pure prose answer with no files.") == ()


def test_resolve_bare_filenames_by_unique_basename() -> None:
    tree = ("lib/matplotlib/backends/backend_pgf.py", "lib/matplotlib/pyplot.py")
    resolved, dropped = resolve_bare_filenames((("backend_pgf.py", 240, 252),), tree)
    assert resolved == (("lib/matplotlib/backends/backend_pgf.py", 240, 252),)
    assert dropped == ()


def test_ambiguous_basename_is_dropped_and_reported() -> None:
    tree = ("a/util.py", "b/util.py")
    resolved, dropped = resolve_bare_filenames((("util.py", 1, 2),), tree)
    assert resolved == () and dropped == ("util.py",)
```

- [ ] **Step 2:** Run (`PYTHONPATH=benchmarks/src pytest benchmarks/tests/eval/test_citation_extraction.py -q`) — FAIL.

- [ ] **Step 3: Implement** `_citations.py`:

```python
"""Answer-text → file citations, the §D14 pseudo-qrel extractor.

File-LEVEL labels only: line ranges are captured for provenance but relevance
is path membership (SWE-QA's pins are unverified against the HF release, so
line-level labels would be false precision; spec §D14 documents this).
"""

from __future__ import annotations

import re

# path or bare filename, optionally backticked, with optional 'line N[-M]' tail.
_CITE_RE = re.compile(
    r"`?(?P<path>[A-Za-z0-9_\-./]+\.py)`?"
    r"(?:\s*[:,(]?\s*lines?\s+(?P<start>\d+)(?:\s*[-–]\s*(?P<end>\d+))?)?"
)


def extract_path_citations(answer: str) -> tuple[tuple[str, int, int], ...]:
    """Every distinct (path, start, end) cited in an answer; (0, 0) when unranged."""
    seen: dict[tuple[str, int, int], None] = {}
    for m in _CITE_RE.finditer(answer):
        start = int(m.group("start") or 0)
        end = int(m.group("end") or start)
        seen.setdefault((m.group("path"), start, end))
    return tuple(seen)


def resolve_bare_filenames(
    citations: tuple[tuple[str, int, int], ...],
    repo_tree: tuple[str, ...],
) -> tuple[tuple[tuple[str, int, int], ...], tuple[str, ...]]:
    """Map bare filenames to repo-relative paths by UNIQUE basename; ambiguous → dropped.

    Returns (resolved citations, dropped filenames) so callers can log drops
    (no-silent-caps rule).
    """
    by_basename: dict[str, list[str]] = {}
    for path in repo_tree:
        by_basename.setdefault(path.rsplit("/", 1)[-1], []).append(path)
    resolved: list[tuple[str, int, int]] = []
    dropped: list[str] = []
    tree_set = set(repo_tree)
    for path, start, end in citations:
        if path in tree_set:
            resolved.append((path, start, end))
            continue
        if "/" in path:
            # slash-qualified but not in tree: try suffix match (answers often
            # cite paths relative to a subdir, e.g. src/... vs repo root)
            matches = [t for t in repo_tree if t.endswith("/" + path) or t == path]
        else:
            matches = by_basename.get(path, [])
        if len(matches) == 1:
            resolved.append((matches[0], start, end))
        else:
            dropped.append(path)
    return tuple(resolved), tuple(dropped)
```

- [ ] **Step 4:** Green. **Step 5: Commit** `feat(bench): citation extractor for SWE-QA pseudo-qrels`.

---

### Task 2: Pinned repo checkouts — shared corpus cache

**Files:**
- Create: `benchmarks/src/benchmarks/eval/datasets/_repo_cache.py`
- Test: `benchmarks/tests/eval/test_repo_cache.py`

- [ ] **Step 1: Failing tests** — build a local origin repo in tmp_path (git init + 2 commits), then:

```python
def test_checkout_at_commit_materializes_and_caches(tmp_path) -> None:
    cache = RepoCache(root=tmp_path / "cache")
    path1 = cache.checkout("file://" + str(origin), sha_of_first_commit)
    assert (path1 / "a.py").exists() and not (path1 / "b.py").exists()  # first commit only
    path2 = cache.checkout("file://" + str(origin), sha_of_first_commit)
    assert path1 == path2                       # cached, no re-clone


def test_short_sha_accepted(tmp_path) -> None:
    path = cache.checkout("file://" + str(origin), sha_of_first_commit[:7])
    assert (path / "a.py").exists()


def test_missing_git_or_bad_sha_raises_with_context(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="deadbeef"):
        cache.checkout("file://" + str(origin), "deadbeef")


def test_file_tree_lists_tracked_files(tmp_path) -> None:
    tree = cache.file_tree("file://" + str(origin), sha_of_first_commit)
    assert "a.py" in tree
```

- [ ] **Step 2:** FAIL. **Step 3: Implement** `RepoCache` (plain dataclass): `root: Path` (default `~/.cache/pydocs-mcp/swe-qa-repos`); `checkout(url, sha) -> Path` clones once per repo (`git clone <url> <root>/<name>` then `git -C ... fetch --all` when the sha is unknown locally, `git worktree`-free plain `git -C <clone> checkout <sha>` into a per-sha copy via `git worktree add <root>/<name>@<sha12> <sha>` — pick ONE mechanism: per-sha `git worktree add` from a bare-ish base clone, which avoids duplicate object stores; document the choice); `file_tree(url, sha)` = `git -C <checkout> ls-files` split. All subprocess via `subprocess.run(..., check=True, capture_output=True, timeout=600)` — this is SYNC code called from adapters through `asyncio.to_thread` (the adapters' existing convention), so no async here; errors re-raise as `RuntimeError` carrying the sha + stderr tail. Every function ≤15 complexity.

- [ ] **Step 4:** Green (tests use `file://` origins — no network). **Step 5: Commit** `feat(bench): pinned repo checkout cache for SWE-QA corpora`.

---

### Task 3: File-set relevance

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/metrics/_relevance.py`
- Test: `benchmarks/tests/eval/test_file_set_relevance.py`

- [ ] **Step 1: Failing tests**

```python
def test_retrieved_item_relevant_when_source_path_in_file_set() -> None:
    task = _task(gold=GoldAnswer(file_set=("src/pkg/mod.py",)))
    hit = RetrievedItem(rank=1, text="...", source_path="/tmp/corpus123/src/pkg/mod.py")
    assert is_relevant(hit, task) is True


def test_suffix_match_tolerates_materialized_corpus_prefix() -> None:
    # corpus dirs are tmp copies; source_path carries the tmp prefix — the
    # repo-relative gold path must match by suffix on path-segment boundary.
    task = _task(gold=GoldAnswer(file_set=("pkg/mod.py",)))
    assert is_relevant(RetrievedItem(rank=1, text="", source_path="/x/y/pkg/mod.py"), task)
    assert not is_relevant(RetrievedItem(rank=1, text="", source_path="/x/y/otherpkg/mod.py"), task)


def test_existing_ast_and_chunk_id_paths_unchanged() -> None:
    # regression: RepoQA (ast_body) and DS-1000 (resolved_chunk_ids) dispatch first.
    ...
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** in `_relevance.py`'s dispatch (35-63), add the third branch — when `task.gold.file_set` is non-empty and neither ast_body nor resolved_chunk_ids applies: relevant iff `retrieved.source_path` ends with any gold path at a `/` boundary (`sp == g or sp.endswith("/" + g)`). Read the existing dispatch order first and slot file_set AFTER the two existing signals (they are more precise), with a WHY comment. Extend `NDCGAtK`'s `n_gt` derivation to `len(file_set)` when that branch is active (mirror how it derives n_gt for resolved_chunk_ids — ndcg_at_k.py:23-57).

- [ ] **Step 4:** Green incl. existing metric suites. **Step 5: Commit** `feat(bench): file-set relevance for citation-derived gold labels`.

---

### Task 4: The two dataset adapters

**Files:**
- Create: `benchmarks/src/benchmarks/eval/datasets/swe_qa_pro.py`, `benchmarks/src/benchmarks/eval/datasets/swe_qa.py`
- Modify: `benchmarks/src/benchmarks/eval/datasets/__init__.py` (imports fire the registration)
- Fixtures: `benchmarks/tests/eval/fixtures/swe_qa_pro_mini.jsonl` (5 rows), `swe_qa_mini.jsonl` (5 rows) — hand-built rows with the real column shapes and synthetic tiny answers citing files that exist in a fixture corpus dir
- Test: `benchmarks/tests/eval/test_swe_qa_loaders.py`

- [ ] **Step 1: Failing tests** (hermetic via `fixture_path` + a fixture corpus dir + a fake RepoCache injected as a constructor field):

```python
async def test_pro_yields_tasks_with_file_set_and_metadata() -> None:
    ds = SweQaProDataset(fixture_path=_PRO_FIXTURE, repo_cache=_FakeRepoCache(...))
    tasks = [t async for t in ds.tasks()]
    assert len(tasks) == 4                      # 5 fixture rows, 1 citation-free → excluded
    t0 = tasks[0]
    assert t0.gold.file_set and t0.metadata["qa_type"] in {"What", "Where", "How", "Why"}
    assert t0.metadata["repo"] and t0.task_id.startswith("swe_qa_pro/")


async def test_pro_excluded_rows_are_counted(caplog) -> None:
    ...  # the exclusion count is logged (no-silent-caps)


async def test_swe_qa_infers_repo_from_split_and_resolves_bare_names() -> None:
    ds = SweQaDataset(fixture_path=_SWEQA_FIXTURE, split="matplotlib",
                      repo_cache=_FakeRepoCache(tree=("lib/matplotlib/backends/backend_pgf.py",)))
    tasks = [t async for t in ds.tasks()]
    assert tasks[0].gold.file_set == ("lib/matplotlib/backends/backend_pgf.py",)


def test_both_registered() -> None:
    assert dataset_registry.build("swe-qa-pro", fixture_path=_PRO_FIXTURE, repo_cache=...)
    assert dataset_registry.build("swe-qa", fixture_path=_SWEQA_FIXTURE, repo_cache=...)
```

- [ ] **Step 2:** FAIL. **Step 3: Implement** — both adapters follow the repoqa.py shape exactly (mutable dataclass, `_rows_cache`, `cache_dir` default, `fixture_path` escape hatch, downloads via `asyncio.to_thread`, registration decorator, `__init__.py` import):

- `SweQaProDataset` (`@dataset_registry.register("swe-qa-pro")`): `name="swe-qa-pro"`, `revision="596892dac60b6f500f01a7dc2becb9f66593b7b7"` (pinned HF revision — download URL `https://huggingface.co/datasets/TIGER-Lab/SWE-QA-Pro-Bench/resolve/<revision>/data/test.jsonl`); per row: citations = `extract_path_citations(answer)` → `resolve_bare_filenames(..., repo_cache.file_tree(f"https://github.com/{repo}.git", commit_id))` → file_set = distinct resolved paths; skip rows with empty file_set, counting + `log.info` the exclusion total; `EvalTask(task_id=f"swe_qa_pro/{repo}/{i}", query=question, gold=GoldAnswer(file_set=...), corpus_source=<default-arg closure over repo_cache.checkout(url, commit_id) + materialize_corpus>, metadata={"repo": repo, "qa_type": class_name.split(" ")[0], "sub_class": sub_class_name, "cluster": cluster_id})`.
- `SweQaDataset` (`@dataset_registry.register("swe-qa")`): `revision="07e206aa29fdad0cf3f1d532ff077f9705387348"`; constructor `split: str = "default"`; module-constant `_REPO_PINS: dict[str, tuple[str, str]]` mapping split name → (github url, short sha) with the 15 verified pins verbatim and a WHY comment stating their provenance (`peng-weihan/SWE-QA-Bench repo_commit.txt`, fetched 2026-07-06) and the unverified-pairing caveat; `default` split iterates all 15; repo per row = split (or filename stem for `default` — the per-repo jsonl name); same citation → resolve → file_set pipeline; drops logged.
- `repo_cache: RepoCache | _FakeRepoCache`-shaped field (Protocol-typed `RepoCacheLike` with `checkout`/`file_tree`) so tests inject fakes — define the small Protocol in `_repo_cache.py`.

- [ ] **Step 4:** Green, offline. **Step 5: Commit** `feat(bench): SWE-QA and SWE-QA-Pro dataset adapters with file-level pseudo-qrels`.

---

### Task 5: Configs + end-to-end smoke

**Files:**
- Create: `benchmarks/configs/swe_qa_pro_{bm25,dense,hybrid_rrf_k60,graph}.yaml` (each two lines pointing at the EXISTING pipeline blueprints used by the repoqa configs — e.g. `pipelines/exp_bm25.yaml`, `exp_dense.yaml`, `exp_hybrid_rrf_k60.yaml`; for `graph`, point at the shipped `chunk_search_graph.yaml` semantics via the closest existing exp blueprint or add `exp_graph.yaml` mirroring `python/pydocs_mcp/pipelines/chunk_search_graph.yaml`'s step list)
- Test: `benchmarks/tests/eval/test_swe_qa_runner_smoke.py`

- [ ] **Step 1: Failing smoke test** — mirror `test_runner_smoke_pydocs_jsonl_fixture` verbatim shape: `run_sweep(systems=("pydocs-mcp",), config_paths=(overlay,), dataset_name="swe-qa-pro", dataset_kwargs={"fixture_path": _PRO_FIXTURE, "repo_cache": <fake pointing corpus at a tmp fixture corpus>}, metric_specs=("recall@5", "ndcg@10", "mrr"), tracker_names=("jsonl",), ...)` asserting `tasks_ran == 4` and each result row carries the three metric keys.

- [ ] **Step 2-4:** FAIL → add configs → green. **Step 5: Commit** `feat(bench): SWE-QA retrieval configs + runner smoke`.

---

### Task 6: Per-category report breakout

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/report.py`
- Test: `benchmarks/tests/eval/test_report_category_breakout.py`

- [ ] **Step 1: Failing test:** feed `format_report` sweep results whose tasks carry `metadata["qa_type"]` and assert the report contains a `## By qa_type` section with one row per category and the same metric columns; results without the key render no such section (RepoQA unchanged — golden regression assertion on an existing fixture report).

- [ ] **Step 2-4:** Read `format_report` (report.py:29-34) + its result-row shape first; group task-level rows by `metadata.get("qa_type")` when ≥2 distinct values are present; means per group. Keep the existing top table byte-identical when the key is absent.

- [ ] **Step 5: Commit** `feat(bench): per-category report breakout (qa_type)`.

---

### Task 7: Spec §D14 corrections + docs

**Files:**
- Modify: `docs/superpowers/specs/2026-07-06-task-shaped-surface-decisions-swe-qa-design.md` (§D14)
- Modify: `benchmarks/README.md` (+ `benchmarks/EXPERIMENTS.md` if the config zoo is documented there)

- [ ] **Step 1:** Amend §D14 with the verified facts (rev note at the top of the spec): SWE-QA HF release = 720 rows/15 repos, `question`+`answer` columns only — **no per-row taxonomy**, so per-category breakouts come from SWE-QA-Pro's `qa_type` (the Why-category `get_why` probe moves to Pro); SWE-QA pins come from the companion GitHub repo (short SHAs, pairing with the HF release unverified → file-level labels only); SWE-QA-Pro = 260 rows/26 Python repos, MIT, per-row full commit pins, `(path.py: line N-M)` citations (96% coverage) → primary corpus; licenses Apache-2.0/MIT, both redistributed-by-download only. README: one paragraph per dataset (what it measures, how to run, the pseudo-qrel caveat verbatim from §D14).
- [ ] **Step 2:** Run the README jargon audit grep from CLAUDE.md (no PR/task jargon). **Step 3: Commit** `docs(spec): §D14 corrections from dataset verification; benchmark docs`.

---

### Task 8: Full gates

- [ ] `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q` (expect +~25 tests) and the main suite untouched (`pytest -q tests/`); ruff check + format --check on `benchmarks/`; complexipy on new files (restore snapshot); mypy is not configured for benchmarks/ (verify — if `mypy python/` config excludes benchmarks, skip); commit fixups as `fix(slice4): gate fixups`.

---

## Out of scope (explicit)

- Symbol/line-level qrels (Pro's line ranges make them feasible later via AST mapping; file-level first — §D14's honest-approximation caveat).
- The paired agent-efficiency harness (slice 5 / §D15).
- Downloading corpora in CI (adapters download on demand; tests are fixture-hermetic).
- Any change under `python/pydocs_mcp/` (this slice is benchmarks-only by design — parallel-safe with slices 2b/3).
