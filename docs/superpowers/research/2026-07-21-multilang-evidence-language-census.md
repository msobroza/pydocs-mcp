# Multilanguage indexing — the measurement that drives Tier 3

**Research task:** the actual file-type / language distribution across the evaluation
dev pool and typical Python projects, to rank the three approved multilang tiers
(T1 widened `include_extensions`; T2 language-agnostic text/config chunker; T3
tree-sitter grammars behind an optional extra).

**Date:** 2026-07-21 · **Branch:** `claude/multilanguage-indexing` @ `aaed02e` ·
**Author:** research subagent (no paid model calls).

**Bottom line up front:** The multilang need is **overwhelmingly text (docs +
config), not other programming languages.** Across 1,887 SWE-bench-Live gold patches,
the agent's EDIT surface is **72.0% Python, 13.3% docs, 8.8% config, 2.0% web,
0.2% native (C/JS/TS/Rust) code.** 44% of instances touch ≥1 non-`.py` file, but
that non-`.py` mass is `.rst`/`.md`/`.yaml`/`.json`/`.toml` — the exact set a
plain-text/config chunker (T1+T2) covers. Real second-language *source* (C/C++,
JS/TS) shows up only on the READ side, concentrated in vendored trees and UI
sub-projects. **T1+T2 capture the evidence; T3 grammars are a low-priority, narrow
follow-on.**

---

## Method & provenance (all evidence executed, not cited from memory)

- **Dataset:** pinned SWE-bench-Live `full` snapshot via the in-repo machinery
  (`benchmarks/src/pydocs_eval/datasets_swe/download.py` + `pins.py`), HF revision
  `a637bd46829f3132e12938c8a0ca93173a977b8e`, split `full`, two parquet shards
  `data/full-0000{0,1}-of-00002.parquet`. Deduped per the committed rule (drop the
  second `conan-io__conan-18153`): **1888 raw → 1887 working rows** (matches
  `pins.LIVE_RAW_ROWS`/`LIVE_DISTINCT_ROWS`).
- **Dev split:** `benchmarks/data/swe/splits/dev.txt`, **1323 instance IDs**, all
  1323 matched a parquet row. 149 distinct repos.
- **Throwaway venv:** `/private/tmp/swe_census_venv` (Python 3.11.4), `[datasets-swe]`
  deps `huggingface_hub==1.24.0` + `pyarrow==25.0.0`. No repo source modified; no
  network beyond the pinned HF fetch + 12 `git fetch --depth 1 <base_commit>`.
- **EDIT census** = parse `diff --git a/… b/…` headers of the `patch` field →
  per-instance modified-file set → extension. **READ census** = clone 12 stratified
  dev-pool repos at their exact `base_commit` and walk the working tree (excluding
  `.git`, virtualenvs, `node_modules`, `__pycache__`, `dist`/`build`, obvious
  vendored dirs left IN so the vendored-vs-first-party split is measurable).
- Parquet schema confirmed to carry `patch`, `base_commit`, `repo`, `instance_id`
  (executed `pq.read_schema`).

---

## 1. EDIT census — "what the agent must MODIFY" (gold-patch file extensions)

### Category rollup (the headline table)

Dotted extensions grouped: python `{.py .pyi .pyx .pxd}`, docs `{.rst .md .txt .mdx .po}`,
config `{.yaml .yml .toml .json .ini .cfg .lock}`, web/frontend
`{.html .css .js .ts .tsx .jsx .mjs}`, native `{.c .h .cpp .cc .hpp .rs .go}`.

| category      | ALL: %files | ALL: %instances | DEV: %files | DEV: %instances |
|---------------|------------:|----------------:|------------:|----------------:|
| **python**    |   **72.0%** |       **97.9%** |   **71.0%** |       **97.4%** |
| **docs**      |   **13.3%** |       **28.7%** |   **14.3%** |       **28.7%** |
| **config**    |    **8.8%** |       **12.3%** |   **10.0%** |       **14.7%** |
| web/frontend  |        2.0% |            1.6% |        0.7% |            1.0% |
| native code   |    **0.2%** |        **0.4%** |        0.1% |            0.2% |
| other         |        3.7% |            9.1% |        3.9% |           10.1% |

- ALL instances (n=1887): **44.0% touch ≥1 non-`.py` file**; 56.0% touch only `.py`.
- DEV split (n=1323): **47.2% touch ≥1 non-`.py` file**; 52.8% only `.py`.
- Total gold files: 6451 (ALL) / 4271 (DEV).

### Top non-Python extensions (file-level, ALL 1887 instances)

| ext     | files | %all gold files | instances | %instances |
|---------|------:|----------------:|----------:|-----------:|
| `.rst`  |   470 |           7.3%  |       299 |     15.8%  |
| `.md`   |   313 |           4.9%  |       221 |     11.7%  |
| `.yaml` |   211 |           3.3%  |       114 |      6.0%  |
| `.json` |   199 |           3.1%  |        42 |      2.2%  |
| `.toml` |    80 |           1.2%  |        68 |      3.6%  |
| `.html` |    64 |           1.0%  |        19 |      1.0%  |
| `.yml`  |    51 |           0.8%  |        26 |      1.4%  |
| `.txt`  |    41 |           0.6%  |        32 |      1.7%  |
| `.pyi`  |    39 |           0.6%  |        32 |      1.7%  |
| `.js`   |    17 |           0.3%  |        14 |      0.7%  |
| `.tsx`  |    12 |           0.2%  |         — |         —  |
| `.ts`   |    10 |           0.2%  |         — |         —  |

DEV split mirrors this (top non-`.py`: `.rst` 18.5% of instances, `.md` 9.3%,
`.yaml` 7.7%, `.toml` 3.9%, `.json` 2.8%, `.pyi` 2.3%).

### The "other" bucket is mostly test fixtures + repo-specific data (NOT languages)

`(noext)` 53, `.bugfix` 23 / `.false_positive` 20 / `.false_negative` 9 / `.new_check` 5
(pylint test-message fixtures), `.ipynb` 21, `.feature` 9 (gherkin), `.gitignore` 9,
`.sh` 9, `.csv` 8, `.ttx`/`.fea` 13 (fontTools), `.textfsm` 7, `.jinja2`/`.scss` 9.
None is a general-purpose programming language warranting a grammar.

**Read of §1:** the non-Python EDIT mass is docs (`.rst`+`.md` = 12.2% of all gold
files, touched by ~28% of instances) and config (`.yaml`+`.yml`+`.toml`+`.json`+
`.ini`+`.cfg`). Native/second-language *source* is 0.2% of files / 0.4% of instances —
i.e. a tree-sitter C/JS/TS grammar would help the agent EDIT roughly **1 in 250
instances**.

---

## 2. READ census — "what the agent must READ" (working-tree of 12 dev repos)

Stratified sample (top-count head + web + scientific + mids + singletons), cloned at
exact `base_commit`, working tree walked (vendored dirs left in, measured separately):

| repo (dev instance count) | files | size | %`.py` | %docs(.md/.rst) |
|---------------------------|------:|-----:|-------:|----------------:|
| aws-cloudformation/cfn-lint (109) | 6931 | 26 MB | 34.5% | 0.2% |
| matplotlib/matplotlib (102)       | 4618 | 68 MB | 19.6% | 8.7% |
| deepset-ai/haystack (88)          | 1120 | 11 MB | 33.2% | 3.2% |
| pylint-dev/pylint (62)            | 3771 |  5 MB | 59.9% | 7.8% |
| reflex-dev/reflex (44)            |  571 |  7 MB | 60.9% | 4.2% |
| sphinx-doc/sphinx (39)            | 1850 | 21 MB | 35.2% | 24.9% |
| Delgan/loguru (6)                 |  294 |  1 MB | 55.1% | 6.1% |
| BerriAI/litellm (5)               | 2645 | 197 MB| 49.4% | 15.5% |
| Flexget/Flexget (4)               | 1200 | 29 MB | 53.9% | 2.8% |
| FreeOpcUa/opcua-asyncio (1)       |  259 | 21 MB | 64.9% | 9.7% |
| MechanicalSoup (1)                |   47 |  0.3 MB| 42.6%| 19.1% |
| NVIDIA/NeMo-Guardrails (1)        | 1299 | 24 MB | 36.0% | 18.2% |

**Aggregate: 24,605 files / 410 MB.** By file count: `.py` **39.4%**, `.json` 17.9%
(dominated by cfn-lint's CloudFormation fixtures — 4414 of 4414 mostly one repo),
`.rst` 5.2%, `.txt` 3.9%, `.yaml` 3.7%, `.svg` 3.1%, `.md` 2.8%, `.pdf` 2.5%,
`.js` 1.6%, `.pyi` 0.8%, `.h` 0.7%, `.tsx` 0.6%, `.cpp` 0.2%. By bytes the tail is
binary assets (`.png` 23.1%, `.svg`, `.pdf`, `.ttf`, `.mkv`) — noise for a doc index.

### Second-language SOURCE, and how much is first-party vs vendored

Non-Python code files, aggregate: **JS 398, TS 163, C/C++ headers 162, C++ 60,
Shell 22, Cython 1, C 1, Rust 0, Go 0.** Per-repo, with a vendored/first-party
spot-check:

- **JS/TS (web):** litellm 235 (`.tsx`=139/`.ts`=24 — **first-party** Next.js
  dashboard under `ui/litellm-dashboard/src/components`); Flexget 182 `.js`
  (**first-party** Angular UI under `flexget/ui/v1/src`); sphinx 117 (**mostly
  vendored** — `sphinx/search/minified-js` + `non-minified-js` + themes' jquery,
  ~4 first-party test JS); reflex 11, NeMo 11, matplotlib 4.
- **C/C++ (native):** matplotlib 222 — but **127 are vendored** `extern/agg24-svn`
  + `extern/ttconv`; only **~27 first-party** under `src/` (the actual extension
  modules Python calls into). sphinx 2 (`.h`=1, `.pyx`=1).
- **Rust:** **zero** in the sampled dev pool.

**Read of §2:** even on the READ side, second-language source is a minority and
skews toward (a) vendored libraries a doc index should skip and (b) self-contained UI
sub-projects (`ui/…`, `flexget/ui/…`). The Python + docs + config triple is the
signal; everything else is long-tail or vendored.

---

## 3. Evidence-ranked tier recommendation

### T1 — widen `include_extensions` (text-searchable via `grep`/`glob`/`read_file` + FTS)

**Ship this first; highest ROI, near-zero risk.** Add the docs + config text set:

```
.rst  .md  .mdx  .txt          # docs — 12–14% of gold files, ~28% of instances
.toml .yaml .yml .json .ini .cfg .lock   # config — 9–10% of gold files, ~12–15% of instances
```

Justification: these extensions account for **~22% of all gold-patch files and are
touched by ~40%+ of the delta** once docs+config are combined, and they dominate the
non-`.py` READ mass (`.rst` 5.2%, `.yaml`+`.yml` 5.3%, `.json` 17.9%, `.md` 2.8%).
They are already plain text — indexing them as text needs no parser. (`.pyi` is
`.py`-adjacent and should ride along; 1.7% of instances edit stubs.)

### T2 — language-agnostic plain-text / config chunker (index as text)

**Ship together with / right after T1.** Route the T1 config+docs set through a
heading/blank-line/size-window chunker so the content is *retrievable* (dense + BM25),
not just greppable. Priority order by evidence weight:

1. `.rst`, `.md` (docs — the single largest non-Python edit+read category)
2. `.yaml`/`.yml`, `.toml` (config the agent edits: CI, pyproject, project settings)
3. `.json` (huge READ volume via fixtures — chunk with a size cap to avoid fixture
   flooding; `.json` is only 2.2% of *edits*, so weight retrieval toward docs/config)
4. `.txt`, `.ini`, `.cfg` (long tail)

**Do NOT** add binary/asset extensions (`.png .svg .pdf .ttf .mkv .mo`) — they are
23%+ of READ *bytes* but zero retrieval value and would bloat the index.

### T3 — tree-sitter grammars behind an optional extra (LOWEST priority)

The census does **not** justify T3 as a near-term need — native/second-language
source is 0.2% of gold edits and mostly vendored on the read side. If/when T3 is
built, the **evidence-ranked grammar priority** is:

| rank | language | grammar | evidence (dev pool) | verdict |
|------|----------|---------|---------------------|---------|
| 1 | **JavaScript/TypeScript** | `tree-sitter-{javascript,typescript,tsx}` | 561 first-party+vendored files; **first-party** UIs in litellm (TS/TSX dashboard), Flexget (Angular), reflex | Only grammar with real first-party source across multiple repos — but confined to `ui/` sub-projects |
| 2 | **C / C++** | `tree-sitter-{c,cpp}` | 222 in matplotlib (scientific-stack extension modules), but **~57% vendored** (`extern/agg`); ~27 first-party | Justified only for the scientific/native-extension slice; heavy vendored-dir filtering required |
| 3 | **Cython** | `tree-sitter` (no stable grammar) | 39 `.pyx` edits across ALL (0.6% files); 1 in dev pool | Rare; `.pyx` is Python-ish — a text chunker (T2) covers it acceptably |
| — | **Rust** | `tree-sitter-rust` | **0** in dev pool; only this repo's `src/lib.rs` | Not justified by the eval pool (dogfood-only, §4) |

**T3 guidance:** gate behind the extra; ship JS/TS first if at all; make
vendored-directory exclusion (`extern/`, `node_modules/`, `minified-js/`,
`third_party/`) a hard prerequisite or the index fills with library code the agent
never edits.

---

## 4. Dogfood — this repo (pydocs-mcp @ `aaed02e`)

Walked with the same exclusions (`.git .venv node_modules __pycache__ dist build
target .claude …`). **1511 files, 23.4 MB.**

| ext | files | %files | %bytes |
|-----|------:|-------:|-------:|
| `.py`   | 1078 | 71.3% | 28.8% |
| `.md`   |  193 | 12.8% | 26.0% |
| `.yaml` |  102 |  6.8% |  0.6% |
| `.json` |   45 |  3.0% |  3.6% |
| `.png`  |   17 |  1.1% | 34.0% |
| `.jsonl`|   17 |  1.1% |  1.4% |
| `.j2`   |    8 |  0.5% |  —    |
| `.ipynb`|    8 |  0.5% |  —    |
| `.toml` |    7 |  0.5% |  —    |
| `.rst`  |    4 |  0.3% |  —    |

Plus **exactly 1 `.rs` file** (`src/lib.rs`, 23.4 KB, the Rust acceleration core) +
`Cargo.toml` — below the top-20 cut at 0.07% of files. This repo is the *only* place
Rust appears anywhere in the study, and it is a single file: strong confirmation that
**Rust grammar support is dogfood-vanity, not eval-driven.** The dogfood profile
(71% Python + 13% Markdown + 7% YAML) is a near-perfect match for the eval-pool edit
census — the T1/T2 text+config set indexes ~99% of this repo's meaningful files.

---

## Reproduction

```bash
# venv
/opt/homebrew/bin/python3.11 -m venv /private/tmp/swe_census_venv
/private/tmp/swe_census_venv/bin/pip install "huggingface_hub>=0.20" "pyarrow>=15.0"
# EDIT census: PYTHONPATH=benchmarks/src, download_parquet(LIVE_PIN), parse `patch`
#   diff --git headers → ext → per-instance sets (dedupe conan-io__conan-18153)
# READ census: git init + fetch --depth 1 origin <base_commit> for the 12 repos in
#   /private/tmp/clone_sample.json, then os.walk with the EXCLUDE set above
```

Intermediate artifacts (throwaway, not committed): `/private/tmp/clone_sample.json`,
`/private/tmp/read_census.json`, `/private/tmp/swe_clones/` (12 repos, ~410 MB),
HF parquet cache `~/.cache/pydocs-mcp/swe-bench-snapshots/a637bd46…/`.

## Design-constraint check (verified, not violated)

- **Nine-tool MCP surface frozen:** T1/T2/T3 land entirely as
  `include_extensions` widening + a registered chunker + a grammar registry behind an
  extra — **no new tool, param, or meta.** `grep`/`glob`/`read_file` already operate
  over the discovery scope, so T1 files become searchable with zero surface change.
- **`defaults/descriptions.md` seed untouched:** none of the tiers requires editing
  it; recommendation explicitly scopes changes to registries + YAML config.
- **Index purity (ADR 0014):** the entire census is a pure function of repo *files*
  (extensions from the working tree / diff text) — no imports, no env. T3 must keep
  tree-sitter parsing file-content-only to preserve this; flagged as a T3 prerequisite.

## Open items / caveats

- READ census is a 12-repo sample (8% of 149 dev repos); the `.json` share is
  inflated by one repo (cfn-lint). The EDIT census is the **full** 1887 instances and
  is the load-bearing measurement — treat READ as corroborating texture, not census.
- SWE-bench-Live is Python-task-selected by construction (`repo_language` gating
  upstream), so it *under*-counts polyglot repos. This biases toward Python — but the
  tiers are for a **Python**-doc indexer, so the bias matches the product's scope.
- `.po`/`.mo` (gettext) and `.ipynb` recur in the tail; both are text-extractable and
  fall to T2 if desired, but neither clears a meaningful edit-frequency bar.
