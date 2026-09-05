# Phase 3 — D1 Dataset Plumbing Evidence: SWE-bench-Live × SWE-bench Pro distribution mechanics + the R2 repo-overlap check

**Researcher scope:** D1 — the two datasets' distribution mechanics and THE repo-overlap check (R2 hinges on this).
**Date:** 2026-07-20. **Worktree:** `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/phase-3-evaluation` (branch `claude/phase-3-evaluation` @ 061d967).
**Evidence method:** HuggingFace API + datasets-server + downloaded parquet analyzed with anaconda python3 (pandas 2.0.3 / pyarrow 9.0.0); DockerHub v2 API; targeted WebFetch/WebSearch. Every claim below is API-response-, downloaded-data-, or fetched-URL-backed unless labeled UNVERIFIED.

---

## TL;DR — the load-bearing results

1. **THE OVERLAP CHECK (R2): CLEAN. Exact repo intersection of (Live full, 223 repos) × (Pro public Python, 3 repos) = ∅ (empty). Zero Live instances are excluded under a repo-disjoint R2 constraint.** The only org-level near-miss is the `ansible` org: Live has `ansible/ansible-lint` (3 inst) + `ansible/molecule` (2 inst); Pro-Python has `ansible/ansible`. If R2 were tightened to *org*-disjoint, cost = **5 Live instances** (0.26% of 1888). Different repos, so informational only.
2. **SWE-bench Pro public Python subset is TINY and CONCENTRATED: 266 instances across only 3 repos** (`ansible/ansible` 96, `internetarchive/openlibrary` 91, `qutebrowser/qutebrowser` 79). This is the entire Python surface of the public Pro set.
3. **SWE-bench-Live cadence has STALLED.** README still claims "+50/month" but the last data-bearing HF commit is **2025-09-18** — ~10 months stale as of 2026-07-20. Full split = **1888 rows / 1887 distinct** (dup `conan-io__conan-18153`), matching Phase 2 exactly. No drift since Phase 2 (2026-07-18) — none since Sept 2025.
4. **Container images: both datasets ship per-instance prebuilt images, both amd64-ONLY.** Live → DockerHub `starryzhang` (2966 repos, `sweb.eval.x86_64.*`). Pro → DockerHub `jefzda/sweap-images` (1002 tags, keyed by the dataset's `dockerhub_tag` field). No arm64 published on either — matters for Apple-Silicon execution.

---

## 1. SWE-bench-Live snapshot mechanics

### Dataset identity + PIN mechanism
`GET https://huggingface.co/api/datasets/SWE-bench-Live/SWE-bench-Live`:
- `id`: `SWE-bench-Live/SWE-bench-Live`, `license: mit`, `downloads: 6243`, not gated.
- **Current `main` revision SHA (the concrete pin): `a637bd46829f3132e12938c8a0ca93173a977b8e`**, `lastModified: 2025-09-18T07:36:47Z`.
- **PIN mechanism = HF commit SHA.** `load_dataset("SWE-bench-Live/SWE-bench-Live", revision="a637bd46829f3132e12938c8a0ca93173a977b8e")` names the exact snapshot the ADR should cite. NOTE: `a637bd46…` is a *README-only* commit; the last **data-bearing** commit is `64120924a57d` (`"Upload dataset"`, `2025-09-18T07:30:31Z`) — both are 2025-09-18 so pinning `main`'s `a637bd46…` captures the current data. There is also a machine-generated parquet-convert branch `refs/convert/parquet` @ `250ddcc17d377ae00893b7c70fea5f14aa37696a` (this is what `/parquet` endpoints serve).
- Branches: `main` (`a637bd46…`) and `update-from-karina` (`c31eca73…`, a staging branch — do NOT pin to it). **No git tags** (`"tags": []` in `/refs`).

### Update cadence — DOCUMENTED vs ACTUAL (drift finding)
- **Documented** (README, fetched verbatim): *"Each month, we will add 50 newly verified, high-quality issues to the dataset."* `lite`/`verified` are frozen: *"The `lite` and `verified` splits will remain frozen, ensuring fair leaderboard comparisons…"*
- **Actual** commit history (`/commits/main`) — data-bearing `"Upload dataset"` commits: 2025-05-15 (init), 2025-07-02, 2025-07-18, 2025-08-05, 2025-08-31, 2025-09-01, 2025-09-10, **2025-09-18 (last)**. **No dataset upload in ~10 months** (as of 2026-07-20). The "+50/month" cadence has stopped; treat the README claim as aspirational, not current. This *helps* Phase 3: the snapshot is de-facto frozen, so a pinned SHA will not silently drift under a re-index.

### Config / splits / sizes (re-verified against Phase 2)
Single config `default`. From `cardData.dataset_info.splits` + downloaded parquet row counts:

| Split | Instances (API) | num_bytes | Notes |
|-------|-----------------|-----------|-------|
| `test` | 1000 | 294,155,734 | the SWE-bench-Live "test" leaderboard split |
| `lite` | 300 | 80,984,624 | frozen |
| `verified` | 500 | 141,184,915 | frozen; LLM-filtered; spans 2024-07→2025-04 (per README) |
| `full` | **1888** | 539,773,127 | "contains latest issues" |

- **`full` = 1888 confirmed by direct parquet load** (`live_full_0.parquet` + `_1.parquet`): `ROWS: 1888, distinct instance_id: 1887, DUP: ['conan-io__conan-18153']`. **Exact match to Phase 2's verified numbers. No drift.**
- 18 fields confirmed present: `repo, pull_number, instance_id, issue_numbers, base_commit, patch, test_patch, problem_statement, hints_text, all_hints_text, commit_urls, created_at, commit_url, test_cmds, log_parser, difficulty, FAIL_TO_PASS, PASS_TO_PASS` (matches Phase 2's "18 fields incl. per-instance test_cmds/log_parser/difficulty").
- Historical context (arXiv 2505.23419 / GitHub): initial release was **1,319 instances / 93 repos**. Current full is **1,888 / 223 repos** — the dataset grew ~570 instances / +130 repos across the 2025 monthly uploads before stalling.

---

## 2. Per-instance complexity metadata for stratification (full split)

### `difficulty` — exact subfields
`difficulty` is a **struct with exactly 3 int subfields: `{files, hunks, lines}`** (verified from a live row: `{'files': 2, 'hunks': 2, 'lines': 27}`). No `difficulty` string label — it is the raw gold-patch size triple, computed from `patch`. Distributions over all 1888:

| Metric | min | 25% | 50% | 75% | 90% | max | mean |
|--------|-----|-----|-----|-----|-----|-----|------|
| `files` | 1 | 1 | 2 | 3 | — | 262 | 3.42 |
| `hunks` | 1 | 2 | 4 | 8 | — | 1754 | 9.07 |
| `lines` | 1 | 9 | 25 | 71 | 178 | 26199 | — |

Heavy right tail (max 262 files / 26k lines). For stratification, bucket on `lines` quartiles (≤9 / ≤25 / ≤71 / >71) or `files` (1 / 2-3 / >3).

### `created_at` — year histogram (full split, 1888 rows)
`created_at` dtype = `datetime64[ns]`. min `2021-07-22`, max `2025-09-02`:

| Year | Count |
|------|-------|
| 2021 | 1 |
| 2022 | 1 |
| 2023 | 2 |
| 2024 | 975 |
| 2025 | 909 |

Effectively a 2024–2025 corpus (99.8%). The README's "after 2024" cutoff is approximate — 4 pre-2024 stragglers exist. No 2026 instances (consistent with the Sept-2025 freeze).

### Per-repo instance counts (full split) — 223 repos, HIGH concentration
This decides what repo-disjoint splitting has to work with. **Top-10 repos = 750 / 1888 = 39.7%.** Head of the table (full 223-row table saved to `/private/tmp/live_repos.txt`):

```
165 conan-io/conan          62 pylint-dev/pylint       44 reflex-dev/reflex
109 aws-cloudformation/cfn-lint  52 instructlab/instructlab  41 streamlink/streamlink
102 matplotlib/matplotlib   48 keras-team/keras        39 sphinx-doc/sphinx
 88 deepset-ai/haystack     ...                        35 pdm-project/pdm
```
Long tail: **~90 repos have exactly 1 instance**; median repo has 2-3. **Implication for R2 / repo-disjoint splits:** removing a single heavy repo (e.g. `conan-io/conan`) drops 165 instances (8.7%) at once — repo-disjoint train/test partitioning of Live must account for this skew, but (see §4) R2 vs Pro removes *nothing*.

---

## 3. SWE-bench Pro — public subset distribution

### Dataset identity
- **Public dataset id: `ScaleAI/SWE-bench_Pro`** (NOT gated). `sha: 7ab5114912baf22bb098818e604c02fe7ad2c11f`, `lastModified: 2026-02-23T20:54:47Z`, `downloads: 60512`. Single config `default`, single split `test` = **731 examples** (23.7 MB). Only branch is `main` (no tags). (Tried `ScaleAI/SWE-bench_Pro-Public` / `_Public` → 404; the plain id is the public release.)
- **Full benchmark framing** (Scale AI blog + arXiv 2509.16941, via WebSearch): SWE-bench Pro = **1,865 total instances across 41 repos**, split **731 public (11 repos) / 858 held-out (12 repos) / 276 commercial (18 enterprise repos)**. **Only the 731 public / 11-repo set is on HuggingFace.** The 858 held-out + 276 commercial sets are NOT published (confirmed: `ScaleAI/SWE-bench_Pro_Commercial` and `-Commercial` variants → 404). Public repos are strong-copyleft (GPL-family) — chosen as unlikely-to-be-in-training-data.

### Instance format (16 fields — richer than Live)
`repo, instance_id, base_commit, patch, test_patch, problem_statement, requirements, interface, repo_language, fail_to_pass, pass_to_pass, issue_specificity, issue_categories, before_repo_set_cmd, selected_test_files_to_run, dockerhub_tag`. Notable extras vs Live: `interface` (target function signature+path), `requirements` (NL acceptance criteria), `before_repo_set_cmd` (git reset/clean/checkout setup), `selected_test_files_to_run` (JSON list), `issue_specificity`/`issue_categories` (JSON tag lists), and **`dockerhub_tag`** (the prebuilt image key — see §5).

### Language + Python-repo breakdown (public 731)
`repo_language` over 731: **`go` 280, `python` 266, `js` 165, `ts` 20** (11 repos total). All 11 public repos: `NodeBB/NodeBB, ansible/ansible, element-hq/element-web, flipt-io/flipt, future-architect/vuls, gravitational/teleport, internetarchive/openlibrary, navidrome/navidrome, protonmail/webclients, qutebrowser/qutebrowser, tutao/tutanota`.

**PUBLIC PYTHON SUBSET = 266 instances / 3 repos:**

| Repo | Python instances |
|------|------------------|
| `ansible/ansible` | 96 |
| `internetarchive/openlibrary` | 91 |
| `qutebrowser/qutebrowser` | 79 |

### Harness mechanics
- Evaluation harness repo: **`github.com/scaleapi/SWE-bench_Pro-os`** (WebFetch of the dataset card). Structure mirrors SWE-bench Verified. UNVERIFIED (not fetched at file level): exact harness entrypoint / report dialect — the execution researcher should confirm against the repo.
- Images: `dockerhub_tag` field → `jefzda/sweap-images` (see §5).

---

## 4. THE OVERLAP CHECK (R2) — computed exactly

Computed in-process over the downloaded parquet (Live full 1888 rows; Pro public 731 rows):

- **Live full: 223 distinct repos. Pro-Python: 3 distinct repos. Pro-all-languages: 11 distinct repos.**
- **EXACT repo intersection (Live ∩ Pro-Python) = ∅ (empty set).**
- **EXACT repo intersection (Live ∩ Pro-all-11-repos) = ∅ (empty set).**
- **⇒ Live instances excluded under a repo-disjoint R2 constraint = 0.** R2 (repo-disjointness between the retrieval-eval Live corpus and the Pro held-out benchmark) holds at zero cost at repo granularity. This is the key green light: **no dedup/exclusion is required to keep Live and Pro-public repo-disjoint.**

### Org-level near-misses (informational — same GitHub org, different repo)
The only org collision is `ansible`:

| Live repo | Live instances | Colliding Pro repo (org) |
|-----------|----------------|--------------------------|
| `ansible/ansible-lint` | 3 | `ansible/ansible` (org `ansible`) |
| `ansible/molecule` | 2 | `ansible/ansible` (org `ansible`) |

No org collisions against the other 2 Pro-Python orgs (`internetarchive`, `qutebrowser`) nor against the 8 non-Python Pro orgs. **If R2 is later tightened to org-disjoint, the cost is 5 Live instances (0.26%).** `ansible-lint`/`molecule` are genuinely distinct codebases from `ansible/ansible`, so this is a policy choice, not a leak.

---

## 5. Container-image availability + architecture (per dataset)

### SWE-bench-Live → DockerHub `starryzhang`
- `GET https://hub.docker.com/v2/repositories/starryzhang/?page_size=100` → **`count: 2966`** per-instance repos. Naming: **`sweb.eval.x86_64.<repo>_1776_<name>-<pr>`** (e.g. `sweb.eval.x86_64.pallets_1776_flask-5014`; the `_1776_` token is SWE-bench's `__`→`_1776_` tag-safe encoding of the instance_id). 2966 ≥ 1888 full — every full-split instance is covered (plus older/other-split builds).
- **Architecture: amd64 ONLY.** Tag `latest` of `starryzhang/sweb.eval.x86_64.pallets_1776_flask-5014` → `"images": [{"architecture": "amd64"}]`. No arm64 manifest. (Phase 2's "starryzhang" mention CONFIRMED current.)
- Harness: `github.com/microsoft/SWE-bench-Live` (fork of SWE-bench + the `RepoLaunch` agentic env-builder). Run via `python -m swebench.harness.run_evaluation`.

### SWE-bench Pro → DockerHub `jefzda/sweap-images`
- `GET https://hub.docker.com/v2/repositories/jefzda/sweap-images/tags` → **`count: 1002`** tags. Tag names are exactly the dataset's `dockerhub_tag` field (verified: dataset row's `dockerhub_tag = qutebrowser.qutebrowser-qutebrowser__qutebrowser-f91ace9…` resolves to a real tag, `full_size ≈ 575 MB`). So one shared repo, one tag per instance (1002 tags ≥ 731 public — extra tags likely cover held-out/versions).
- **Architecture: amd64 ONLY** (every tag inspected: `"architecture": "amd64"`; no arm64).
- Namespace `jefzda` is the SWE-bench Pro image host (matches the harness repo `scaleapi/SWE-bench_Pro-os`; `jefzda/sweap-images` = "SWE-bench Pro images"). Retrieval-eval consumers should read the image key from the dataset's `dockerhub_tag` column, not reconstruct it.

**Cross-cutting arch note for the execution researcher:** BOTH datasets are amd64-only on DockerHub. On Apple-Silicon dev machines every container runs under emulation (qemu/Rosetta) — the *availability* is green, the *measuring* (speed/flakiness under emulation) is the execution researcher's call.

---

## Reproduction commands (all run 2026-07-20)
```bash
# Live: identity, refs, splits, history
curl -s https://huggingface.co/api/datasets/SWE-bench-Live/SWE-bench-Live | jq '{sha,lastModified,cardData}'
curl -s https://huggingface.co/api/datasets/SWE-bench-Live/SWE-bench-Live/refs
curl -s "https://huggingface.co/api/datasets/SWE-bench-Live/SWE-bench-Live/commits/main?limit=15"
# Live full parquet (2 shards) + Pro parquet
curl -sL .../parquet/default/full/{0,1}.parquet -o live_full_{0,1}.parquet
curl -sL https://huggingface.co/api/datasets/ScaleAI/SWE-bench_Pro/parquet/default/test/0.parquet -o pro_test_0.parquet
# analysis: anaconda python3 (pandas 2.0.3 / pyarrow 9.0.0) — see /private/tmp scratch
# Images
curl -s https://hub.docker.com/v2/repositories/starryzhang/?page_size=100 | jq .count   # 2966
curl -s https://hub.docker.com/v2/repositories/jefzda/sweap-images/tags?page_size=1 | jq .count  # 1002
```
Scratch artifacts (not committed): `/private/tmp/live_full_{0,1}.parquet`, `/private/tmp/pro_test_0.parquet`, `/private/tmp/live_repos.txt`, `/private/tmp/pro_python_repos.txt`, `/private/tmp/pro_all_repos.txt`.

## Open items / UNVERIFIED
- Pro harness entrypoint + report/JSON dialect not read at file level (only the card's repo name `scaleapi/SWE-bench_Pro-os` is confirmed) — execution researcher should confirm the report keying.
- `starryzhang` image *coverage completeness* per specific full-split instance_id not exhaustively enumerated (spot-checked; count 2966 ≥ 1888 is the availability signal, not a 1:1 join).
- The 858 held-out / 276 commercial Pro sets are NON-PUBLIC (confirmed absent from HF) — R2 can only be verified against the 731-public / 11-repo surface; a future Pro public release could add repos, so R2 should be re-run whenever `ScaleAI/SWE-bench_Pro` `sha` changes (pin it too: `7ab5114912baf22bb098818e604c02fe7ad2c11f`).
