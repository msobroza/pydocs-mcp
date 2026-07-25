# PR #214 — correction proposals

Review of `claude/crosscommitvuln-dataset-integration-b78c57` @ `a1219f3`
(+6938 / −6, 23 commits). 29 findings raised, **18 confirmed, 11 refuted** by
adversarial verifiers that default to refuting. This document covers the **12
confirmed against #214**; the 6 against #215/#216 are already fixed
(`a6801e7`, `f8527cf`).

> **Note on gold tokens.** Finding H1 is that real gold answers sit in indexable
> markdown. This document therefore refers to records as `<record-A>` and to gold
> paths as `<gold-path>` rather than restating them — writing the gold into a new
> `docs/` file would reproduce the exact defect being reported.

## Summary

| # | Severity | Finding | Recommended option |
|---|---|---|---|
| H1 | HIGH | Gold answers in indexable `.md` outside the exclusion floor | B — relocate + widen the sweep |
| H2 | HIGH | `gold.file_set` is the raw union of `files_changed` | C — rank + cap, keep full set for recall |
| H3 | HIGH | `CombinedDataset` yields members serially | A — interleave |
| H4 | HIGH | Airgap bundle consulted only when the base clone is absent | B — bundle-aware `_ensure_sha` |
| H5 | HIGH | `exact_id` never evaluated against real vendored gold | A — parametrize over real records |
| M1 | MEDIUM | Leak gate has no flaw-class words for 5 shipped CWE classes | A — derive from the shipped corpus |
| M2 | MEDIUM | Count gate skipped when empty; build tool overwrites unconditionally | A — write-guard + non-empty floor |
| M3 | MEDIUM | "Queries are distinct" test cannot detect its own regression | A — assert the fallback did not fire |
| M4 | MEDIUM | Gold-leak sweep only classifies JSON lines | folded into H1/B |
| L1 | LOW | `fix_commit_date` resolved after the leak check, so never bannable | A — resolve before |
| L2 | LOW | `_repo_name` keys off the URL tail only | B — hash the full URL |

**These interlock.** H2 alone makes every ccv sample score `0.0`; H3 means a
default combined run admits no ccv tasks at all; H1 means that when they *do*
run, the answer may be retrievable. Fixing any one still leaves the slice
without signal — H2 and H3 are the minimum viable pair.

---

## H1 — Gold answers in indexable markdown

`docs/superpowers/specs/…-design.md` and `…/plans/…-integration.md` contain the
complete gold for `<record-A>` (CVE id, CWE id, every `<gold-path>`, the sink
symbol, the flaw-class words) and the CVE→CWE pair for 9 of 25 records. Neither
path has a `crosscommitvuln` component, so `_EXCLUDED_DIRS` does not cover them,
and `ProjectFileDiscoverer` returns both (`.md` is in the default
`include_extensions`).

The exposure is concrete: `optimize_ask_prompt_combined.yaml` runs with
`workspace: ~/pydocs-index`, documented as "the same index the interactive agent
reads". One `search_docs` on the package name can return the gold chunk, and the
`used_indexed_tools` anti-memorization gate still passes — a tool *was* used.

### Option A — redact the docs to synthetic examples

```python
# in the design doc, replace the worked example with the synthetic form the
# loader fixtures already use
- CVE-XXXX-NNNNN | CWE-NN | app/<real path>.py
+ CVE-2099-0001  | CWE-00 | app/example.py     # synthetic; see tests/fixtures/
```

| Pros | Cons |
|---|---|
| Smallest diff; docs stay where they are | Loses the worked example's explanatory value |
| No new machinery | Nothing stops the next doc reintroducing gold |

### Option B — relocate under the floor + widen the sweep ★

Move the gold-bearing sections into the floor-covered package dir, and make the
invariant test actually able to see a violation:

```python
# tests/extraction/test_config.py — today it globs only *.jsonl / *.json
_TEXTUAL = ("*.md", "*.rst", "*.txt", "*.jsonl", "*.json")

def test_no_gold_token_appears_outside_the_crosscommitvuln_floor() -> None:
    """Gold read from records.jsonl, not hardcoded — so new records extend it."""
    gold_tokens = _tokens_from_shipped_records()   # cve ids, cwe ids, gold paths
    offenders = [
        (path, tok)
        for pattern in _TEXTUAL
        for path in REPO_ROOT.rglob(pattern)
        if "crosscommitvuln" not in path.parts
        for tok in gold_tokens
        if tok in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == [], f"gold leaked outside the floor: {offenders[:5]}"
```

| Pros | Cons |
|---|---|
| Closes the hole **and** the blind spot that hid it | Test walks the repo — a few seconds |
| Derives tokens from the corpus, so new records are covered automatically | Needs a deny-list for legitimate mentions (e.g. this file) |
| Keeps the worked example intact, just relocated | |

### Option C — drop `.md` from `include_extensions`

| Pros | Cons |
|---|---|
| One-line | Breaks doc retrieval, the product's core use case. **Not viable.** |

**Recommendation: B.** A is a point fix that leaves the detector blind; the
reason this shipped is that the test could not see `.md`, and B fixes that
directly. Note this contradicts the earlier "ACCEPT" lean recorded in the
handoff — the difference is the reproduction of the retrieval path.

---

## H2 — `gold.file_set` is the raw union of `files_changed`

`build_file_set` takes the union of every contributing commit's changed files;
`apply_gold_file_gate` only trims non-`.py`/absent paths. Measured on the shipped
corpus: one record carries **128** gold files (27 of them `__init__.py`), another
103, another 31 of which 19 are release-version-bump files. 23 of 25 records have
more than one gold file.

`gold_substring_all` requires **every** gold path verbatim in the answer, so it
is unsatisfiable for 24/25 records → `fail_fast` short-circuits → `verdict = 0.0`
for every ccv sample, always. **The slice contributes no optimization gradient.**

### Option A — restrict the gate with `keys`

```yaml
gates:
  - {name: exact_id, kind: gold_substring_all, params: {keys: [cve_id, cwe_id_0]}}
```

| Pros | Cons |
|---|---|
| Zero code; the `keys` filter already exists | Gold stays wrong for `recall@k`, which is still dominated by `__init__.py` noise |
| Immediately unblocks the slice | Treats the symptom |

### Option B — cap cardinality at build time

```python
_MAX_GOLD_FILES = 5

def build_file_set(annotation: dict) -> tuple[str, ...]:
    files = _ordered_distinct(...)
    return files[:_MAX_GOLD_FILES]
```

| Pros | Cons |
|---|---|
| Simple, bounded | An arbitrary cut — the first 5 are not the most relevant 5 |

### Option C — rank by vulnerability relevance, cap, keep the full set separately ★

```python
#: Files that co-change with almost any edit and carry no vulnerability signal.
_NOISE = re.compile(r"(^|/)(__init__|conftest|version|_version|gapic_version)\.py$")

def build_file_set(annotation: dict) -> tuple[str, ...]:
    """The files a correct answer must name — ranked, de-noised, capped.

    WHY not the raw union: a contributing commit is a whole refactor, so its
    files_changed is dominated by __init__.py / version-bump churn. An answer
    cannot name 128 paths, so `gold_substring_all` over the union is unwinnable
    and every sample scores 0.
    """
    files = _ordered_distinct(f for c in commits for f in (c.get("files_changed") or []))
    ranked = sorted(
        (f for f in files if not _NOISE.search(f)),
        key=lambda f: (-_sink_symbol_hits(annotation, f), len(f.split("/")), f),
    )
    return tuple(ranked[:_MAX_GOLD_FILES])
```
with the untruncated union preserved for recall metrics:
```python
extra["all_changed_files"] = files          # recall@k can still use the full set
```

| Pros | Cons |
|---|---|
| Fixes the gate **and** the metric noise | Needs a corpus rebuild + re-review of the new gold |
| Keeps full provenance in `extra` | Ranking heuristic needs spot-checking per record |
| `gold_substring_all` becomes a real exact-identification test | Most work of the three |

**Recommendation: A now, C before the dataset is used in anger.** A is a
one-line config change that unblocks the slice today; C is what makes the gold
correct. Pair either with H5 so the regression cannot return.

---

## H3 — `CombinedDataset` yields members serially

`_iter_tasks` drains member 0 (~260 swe-qa-pro tasks) before the first `ccv/`
task, and `run_agent_track` breaks at `max_tasks` (default **48**). A default
combined run pays for 48 rollouts, admits **zero** ccv tasks, and still emits a
report headed with the combined name.

### Option A — round-robin interleave ★

```python
def _iter_tasks(self):
    """Interleave members so ANY prefix is representative.

    WHY: consumers truncate at max_tasks, so serial yielding makes the second
    member invisible whenever the first is larger than the cap.
    """
    iterators = [aiter(m.tasks()) for m in self.members]
    while iterators:
        for it in list(iterators):
            try:
                yield await anext(it)
            except StopAsyncIteration:
                iterators.remove(it)
```

| Pros | Cons |
|---|---|
| Fixes every truncating consumer at once | Equal-rate interleave over-represents the small member early |
| ~10 lines, no config | Changes task order → any order-dependent snapshot moves |

### Option B — proportional interleave (reuse `multitask.sampling`)

Use `StratifiedSampler.order` from [#217](https://github.com/msobroza/pydocs-mcp/pull/217), which puts every type at the head then emits proportionally.

| Pros | Cons |
|---|---|
| One definition of "stratified" across the codebase | Couples the dataset layer to the optimize layer |
| Prefixes stay proportional, not just non-empty | Depends on #216/#217 landing |

### Option C — make truncation refuse to silently drop a member

| Pros | Cons |
|---|---|
| Loud rather than wrong | Doesn't fix it; just reports |

**Recommendation: A**, plus C's assertion as a guard. B is tempting but inverts
the dependency — the dataset should not import from `optimize`.

---

## H4 — Bundle consulted only when the base clone is absent

`_clone_source` is reachable only from `_clone`, which `_base_clone` calls only
when the base directory does not exist. A host that already has a base clone
(from swe-qa-pro, or an earlier ccv release) never consults the bundle; a
newly-pinned sha goes to `_ensure_sha`, which attempts a network fetch and fails
on an airgapped box.

**Reproduced** after the two fix rounds already on this branch — those improved
the error message but did not close this path.

### Option A — always re-clone when a bundle exists

| Pros | Cons |
|---|---|
| Trivially correct | Throws away a valid cache; expensive and surprising |

### Option B — make `_ensure_sha` bundle-aware ★

```python
def _ensure_sha(self, base: Path, url: str, sha: str) -> None:
    if self._has(base, sha):
        return
    # Try the local bundle BEFORE the network: on an airgapped host it is the
    # only source, and on a networked one it is strictly cheaper.
    bundle = self.bundle_dir and bundle_path(self.bundle_dir, url)
    if bundle and bundle.exists():
        _git("fetch", str(bundle), "--tags", "+refs/heads/*:refs/remotes/bundle/*", cwd=base)
        if self._has(base, sha):
            return
    ...existing network fetch, whose failure now means "not in the bundle either"
```

| Pros | Cons |
|---|---|
| Keeps the cache; bundle becomes a first-class source | Adds a remote namespace (`refs/remotes/bundle/*`) |
| Works for the shared-root case that broke swe-qa-pro | Slightly more git surface to reason about |

### Option C — separate cache root per dataset

| Pros | Cons |
|---|---|
| Removes cross-dataset interference entirely | Duplicates object stores — the thing worktrees exist to avoid |

**Recommendation: B.** It is the only option that makes the bundle authoritative
without discarding cache or duplicating storage.

---

## H5 — `exact_id` never evaluated against real vendored gold

The combined-config test asserts the gate is *configured*, using synthetic gold.
That is why an unsatisfiable production config shipped green.

### Option A — parametrize the gate test over the real records ★

```python
@pytest.mark.parametrize("task", _shipped_ccv_tasks(), ids=lambda t: t.task_id)
def test_exact_id_gate_is_satisfiable_for_every_shipped_record(task) -> None:
    """A gate no answer can pass makes its whole slice score zero.

    Constructs the BEST POSSIBLE answer — every gold candidate verbatim — and
    asserts the gate accepts it. If this fails, the config is unwinnable.
    """
    best = " ".join(_all_gate_candidates(task, None))
    assert evaluate_gate(_exact_id_gate(), task, _Transcript(answer=best)) is True
```

| Pros | Cons |
|---|---|
| Catches H2 and any future gold-shape regression | Needs the real corpus at test time (already vendored) |
| Expresses the actual invariant: *a gate must be winnable* | |

**Recommendation: A**, and add the same shape for any future gate.

---

## M1 — Leak gate has no flaw-class words for 5 shipped CWE classes

`_CWE_CLASS_KEYWORDS` omits classes present in the corpus, so for those records
the flaw-class half of the query leak check is a no-op and a generated query may
name the vulnerability class outright.

```python
def _class_words_for(cwe_ids: Sequence[str]) -> tuple[str, ...]:
    """Class words for every CWE the corpus actually ships.

    Derived from the shipped records rather than a hand-list, so a new record
    cannot silently arrive without leak coverage.
    """
    missing = sorted(set(cwe_ids) - set(_CWE_CLASS_KEYWORDS))
    if missing:
        raise KeyError(
            f"no flaw-class words for {missing}; add them to _CWE_CLASS_KEYWORDS "
            "or the query leak-gate is a no-op for those records"
        )
    return tuple(w for c in cwe_ids for w in _CWE_CLASS_KEYWORDS[c])
```
Plus a test asserting every CWE in `records.jsonl` has an entry.

**Recommendation:** fail loud on an unmapped CWE. A silent no-op in a leak gate
is the worst failure mode available.

---

## M2 — Count gate skipped when empty; build tool overwrites unconditionally

A rate-limited or offline rebuild drops every record, and the tool truncates the
committed `records.jsonl` to empty. CI stays green because the vendored-pin tests
skip on an empty file.

```python
_MIN_RECORDS = 24

def write_records(path: Path, records: list[dict]) -> None:
    """Never replace a good corpus with a degraded one."""
    if len(records) < _MIN_RECORDS:
        raise SystemExit(
            f"refusing to write {len(records)} record(s) over {path} "
            f"(minimum {_MIN_RECORDS}); re-run with network access"
        )
```
and in the vendored test, replace the skip-on-empty with an assertion that the
file is non-empty.

**Recommendation:** both halves. The write-guard prevents the damage; the test
change removes the reason CI stayed green.

---

## M3 — "Queries are distinct" test cannot detect its regression

If `_claude_generate` breaks, every record falls back to the deterministic
template and the LLM-varied-query feature is silently dead — the distinctness
test still passes because the template interpolates the repo name.

```python
def test_no_shipped_query_is_a_template_fallback() -> None:
    """Distinctness is not the invariant — provenance is."""
    for rec in _shipped_records():
        assert rec["metadata"]["query_source"] == "llm", (
            f"{rec['task_id']} shipped a template fallback; the LLM path is dead"
        )
```
requires recording `query_source` at build time.

**Recommendation:** record provenance and assert on it. Distinctness is a proxy;
provenance is the thing.

---

## L1 — `fix_commit_date` resolved after the leak check

Resolve git metadata **before** `generate_clean_query`, so temporal tokens can
join the ban list. One-line reorder; no design question.

## L2 — `_repo_name` keys off the URL tail

Two repos sharing a trailing segment share a base clone and a `.bundle`.

```python
def _repo_name(url: str) -> str:
    """`<tail>-<8 hex of sha256(url)>` — collision-free across orgs."""
    digest = hashlib.sha256(url.encode()).hexdigest()[:8]
    return f"{_NAME_RE.sub('-', _tail(url)).strip('-')}-{digest}"
```

| Pros | Cons |
|---|---|
| Removes the collision class entirely | **Invalidates every existing cache dir and bundle name** |

**Recommendation:** adopt when the bundle format is next touched (e.g. alongside
H4), not on its own — the rename cost is real and the collision is latent (0 in
the current 24 URLs).

---

## Suggested order

1. **H2 (option A)** — one config line; the slice stops scoring 0.
2. **H3 (option A)** — ~10 lines; ccv tasks actually get admitted.
3. **H5** — pins H2 so it cannot return.
4. **H1 (option B)** — closes the leak and the blind spot.
5. **H4 (option B)** — restores the airgap guarantee.
6. M1, M2, M3 — cheap, and each removes a silent-failure mode.
7. L1 with any of the above; L2 deferred to the next bundle-format change.
