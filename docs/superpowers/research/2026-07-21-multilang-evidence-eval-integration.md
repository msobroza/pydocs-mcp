# Multilanguage indexing — evaluation-side integration evidence

**Date:** 2026-07-21
**Branch / worktree:** `claude/multilanguage-indexing` @ `aaed02e` (= current main + Phase 0–4 no-spend work)
**Scope:** the measurement hooks that make multilanguage a MEASURED feature, and the campaign-freeze interactions.
**Method:** every claim below is file:line + executed-command evidence read directly from the tree. Labels: **VERIFIED** = read in source; **UNVERIFIED** = inference/gap statement not directly asserted by a line.

---

## Item 1 — The stratum hook (`gold_touches_non_python`) and the exact join

### Where strata are computed today

**The aggregator does NOT compute strata — it CONSUMES an `instance_id → stratum key` map passed in.**

- `benchmarks/src/pydocs_eval/campaign/aggregator.py:204-229` — `strata_contrasts(name, a, b, stratum_of, *, seed)` takes `stratum_of: Mapping[str, str]`, groups the shared instance list (`sorted(set(a.hard) | a.infra_ids)`) by `stratum_of.get(iid, "unknown")`, and emits one `paired_contrast` per stratum. (VERIFIED)
- `aggregator.py:246-265` — `campaign_report(..., stratum_of: Mapping[str, str] | None = None, ...)`: strata are OPTIONAL. When `None`, no `"strata"` block is emitted (`_contrast_block`, `aggregator.py:268-279`). (VERIFIED)
- `aggregator.py:232-234` — the only shipped stratum helper is `difficulty_stratum(difficulty_files) -> "single_file"|"multi_file"`. A repo stratum is `stratum_of = {iid: repo}`. (VERIFIED)

**The campaign CLI does NOT currently pass any `stratum_of`.** `benchmarks/src/pydocs_eval/campaign/__main__.py:47` calls `campaign_report(args.campaign_id, cells, contrasts)` with no stratum argument. So BOTH `difficulty` and `repo` strata already exist in the library layer but are **unwired at the `aggregate` subcommand** — there is no `--stratum-map` / `--strata` flag. (VERIFIED — `__main__.py:99-104` `agg` subparser has only `--campaign-id`, `--cell`, `--contrast`, `--out`.)

### Where the gold-file extensions come from (the exact join)

The gold **file COUNT** is in the flat SWE records, but gold **file PATHS** (needed for extensions) are NOT there:

- `benchmarks/src/pydocs_eval/datasets_swe/records.py:16-28` — `LiveRecord` carries `difficulty_files: int` only (the `difficulty.files` count of the `{files, hunks, lines}` struct), plus `repo`, `created_at_year`. No paths. (VERIFIED)
- `benchmarks/src/pydocs_eval/datasets_swe/download.py:66` — `read_live_records` reads columns `["instance_id", "repo", "difficulty", "created_at"]` only. The parquet `patch` / `test_patch` columns (which carry the diffs) are **NOT read** by the datasets_swe edge. (VERIFIED)

The gold file PATHS live in the **trajectory facts**, produced by the gold-patch parser:

- `benchmarks/src/pydocs_eval/trajectory/gold_diff.py:44-56` — `GoldPatch.gold_files: frozenset[str]` = workspace-relative POSIX paths (`a/`/`b/` stripped) the instance `patch` modifies; `parse_gold_patch(instance_id, patch, test_patch)` (`gold_diff.py:93-108`) builds it via `modified_files` (unidiff `PatchSet`). (VERIFIED)
- `benchmarks/src/pydocs_eval/trajectory/compute_metrics_cli.py:59` — `gold_files` is a **REQUIRED** fact key: `_REQUIRED_FACT_KEYS = ("trajectory_id", "instance_id", "workspace_root", "gold_files")`. It is parsed at `compute_metrics_cli.py:118` (`gold_files=frozenset(raw["gold_files"])`). So **every rollout's `facts.json` already carries the gold file paths, keyed by `instance_id`.** (VERIFIED)

### The exact join to add a `gold_touches_non_python` stratum

```
instance_id
  → facts.json["gold_files"]           (frozenset of workspace-relative paths; already required)
  → any(not p.endswith(".py") for p in gold_files)   (extension test; mirror difficulty_stratum)
  → "gold_touches_non_python" | "gold_python_only"    (the stratum label)
```

This is a pure `Mapping[str, str]` the existing `campaign_report(stratum_of=…)` consumes unchanged — **the aggregator needs ZERO change to accept it.** (VERIFIED for the consumer signature; the mapping-builder is the new code.)

### The gap (what T1–T3 must add to make it MEASURED)

Neither the cell `aggregate.json` NOR the per-trajectory derived record persists `gold_files`:
- `compute_metrics_cli.py:240-249` — `_index_row` (the per-trajectory row in `aggregate.json`) emits only `{trajectory_id, instance_id, hard, soft, label, cost_usd}`. No gold_files. (VERIFIED)
- `benchmarks/src/pydocs_eval/trajectory/consumers.py:70-88` — `DerivedRecord.to_dict()` emits no `gold_files` either (it is consumed for taxonomy inputs at `consumers.py:97-110`, not stored). (VERIFIED)

So making `gold_touches_non_python` measured requires ONE of (additive, eval-side only):
- **(a)** a `--stratum-map instance_id→label.json` (or `--stratum gold_lang`) flag on the `aggregate` subcommand + a small builder that scans the run's `facts.json` files (which already hold `gold_files`) to emit the map, then thread it into `campaign_report(stratum_of=…)`; OR
- **(b)** add `gold_touches_non_python` (a bool, or the raw `gold_files`) to `_index_row` so `aggregate.json` self-carries it, and build the map inside the aggregator.

Both are the SAME unwiring already true for `difficulty`/`repo` — the plumbing gap is generic, not specific to multilang. (VERIFIED gap; (a)/(b) are design options = UNVERIFIED-as-chosen.)

**Caveat (dataset composition):** the Phase 3 corpus is Python-gated (ADR 0013 selects `repo_language == "python"`, `download.py:88-96`). On the baseline corpus the `gold_touches_non_python` slice may be near-empty; the stratum is *expressible and measurable* but becomes *informative* only once multilang instances enter the corpus (a different campaign ID — see Item 2/4). (VERIFIED corpus gating; population-size claim = UNVERIFIED, not measured here.)

---

## Item 2 — Pre-registration interaction: can a stratum be added without touching the frozen prereg?

**YES — a new reporting stratum does NOT touch the frozen pre-registration and needs NO dated ADR amendment.**

Evidence the frozen prereg has no stratum slot:
- `benchmarks/src/pydocs_eval/optimize/configs/campaign_preregistration.yaml` — the FROZEN fixed slots are exactly: `alpha`, `delta_min`, `k_plateau`, `n_val`, `gate_rule`, `explore_fraction`, `stopping_rules`, plus 7 measured `[TO BE MEASURED]` slots. **No `strata` / `reporting_dimensions` field exists.** Strata appear only in a PROSE comment (lines 28-31) describing minibatch-panel composition ("strata proportional to the discriminative subset's composition"), which is about *which instances go in the panel*, not a reporting breakdown. (VERIFIED)
- `benchmarks/src/pydocs_eval/optimize/prereg/config.py:175-186` — `registration_hash` digests `PreRegistration.to_dict()`. The `_from_raw` builder (`config.py:206-214+`) reads only the slots above; there is no stratum field, so **adding a reporting stratum cannot change `registration_hash`.** (VERIFIED)

Evidence strata are reporting dimensions, not acceptance inputs:
- ADR 0016 §Pre-registered analysis plan (`docs/adr/0016-…md:230-244`) defines the acceptance-relevant items — primary comparison, secondary comparisons, effect sizes, "does not earn its place", multiple-comparison stance. **None reference strata.** The stance is explicit: *"report ALL comparisons with paired CIs; only the primary supports a headline claim; secondaries are exploratory"* (`:243-244`). A stratum breakdown is exploratory reporting BY CONSTRUCTION and cannot support a headline claim — so it does not open a garden-of-forking-paths problem the prereg guards. (VERIFIED)
- ADR 0018 acceptance rule (`docs/adr/0018-…md:164-227`) consumes only `run_gate` output (`:221-223`); strata never enter acceptance. (VERIFIED)

**The ONE thing that WOULD need a new campaign ID / prereg** is changing the corpus itself: `n_val` is a frozen slot (`campaign_preregistration.yaml:16`, `n_val: 559`), and R5 makes any lockfile field change a new campaign ID (`lockfile.py:241-244`). Adding non-Python INSTANCES to the val/discriminative split changes `n_val` + `split_hashes` (`lockfile.py:173-175`) → new ID. But *slicing the existing frozen corpus by a new reporting stratum* changes nothing frozen. (VERIFIED)

The 2026-07-20 amendment block at the top of ADR 0018 (`:7-22`) is the model for when a dated amendment IS needed (it reconciled the *acceptance predicate*, an acceptance input) — a reporting stratum is categorically not that. (VERIFIED as precedent.)

---

## Item 3 — Rollout-side config: is a "multilang on/off" serve overlay even expressible?

### What EXISTS

- **Product supports a serve overlay:** `python/pydocs_mcp/__main__.py:71` — top-level `--config` flag (`pydocs-mcp --config x.yaml serve .`), resolved via `AppConfig.load(explicit_path=getattr(args,"config",None))` (`__main__.py:851, 898, 939, …`). (VERIFIED)
- **`include_extensions` is a YAML-tunable field:** `python/pydocs_mcp/extraction/config.py:141` — `DiscoveryScopeConfig.include_extensions: list[str]`, default `[".py", ".md", ".ipynb"]`. (VERIFIED)
- **The env pass-through seam:** `benchmarks/src/pydocs_eval/agent_track/_command.py:122-149` — `render_mcp_config(*, corpus_dir, python, env=None)` boots `pydocs_mcp serve <corpus_dir>` and can inject an `"env"` block (currently used only for `PYDOCS_TRACE__*` correlation vars). AppConfig layers env vars as its highest-priority layer (CLAUDE.md §MCP-vs-YAML "(4) env vars"). (VERIFIED for the env seam; env-var override of a list field = UNVERIFIED as tested.)
- **The overlay-selection precedent:** `benchmarks/src/pydocs_eval/campaign/cells.py:44-69` — `CellConfig.suggestion_overlay: str | None` is a NAMED serve-YAML overlay reference, hashed into the lockfile via `to_dict` (`cells.py:62-69`). This is the exact pattern a `multilang` cell dimension would follow. (VERIFIED)

### What is MISSING (must be added, but is NOT multilang-specific)

- **`suggestion_overlay` has NO consumer yet.** `grep` across `campaign/` + `agent_track/` + `trajectory/rollout.py` shows `suggestion_overlay` is only DEFINED and lockfile-hashed in `cells.py` — nothing resolves the name → an actual serve `--config` path or env block. (VERIFIED — the only hits are `cells.py`.)
- **`render_mcp_config` does not thread `--config`.** `_command.py:145` emits args `[*_SERVE_ARGS_PREFIX, str(corpus_dir)]` = `("-m","pydocs_mcp","serve",corpus_dir)` — no `--config`. Adding a multilang overlay means (i) an overlay-name→config resolver (shared with suggestions), and (ii) either a `--config` arg or an env entry threaded through `render_mcp_config`. (VERIFIED)
- **T1 gate:** `python/pydocs_mcp/extraction/config.py:29` — `ALLOWED_EXTENSIONS = frozenset({".py", ".md", ".ipynb"})`, enforced narrow-only by the `_enforce_allowlist` validator (`config.py:148-158`: `bad = set(v) - ALLOWED_EXTENSIONS` → raises). Widening `include_extensions` beyond this set is **rejected at config load today** — T1 must widen/relax this set (the docstring at `config.py:135` states "`include_extensions` remains narrow-only"). (VERIFIED)

**Conclusion:** the config PATH exists end-to-end at the product layer (`--config` + `include_extensions` YAML). The eval-harness seam to *select* a serve overlay per cell is a stub (`suggestion_overlay` defined, not consumed) — building it once serves BOTH suggestions and a future multilang dimension. The ablation "multilang on/off" is not a new campaign axis now; it is *expressible later* once (a) T1 relaxes `ALLOWED_EXTENSIONS`, and (b) the overlay-name→serve-config resolver is wired through `render_mcp_config`. (VERIFIED for the components; "expressible later" = design conclusion.)

---

## Item 4 — Freeze timing: what freezes when the paid arc starts, and the landing deadline

### What the lockfile freezes (the campaign identity)

`benchmarks/src/pydocs_eval/campaign/lockfile.py:178-244` — `CampaignLockfile.to_dict()` → `campaign_id = sha256(canonical_json(...))`. Frozen fields: `dataset_pins`, `split_hashes` (per-split-file sha256), `cells` (incl. each cell's `suggestion_overlay`), `host`, `provider`/`billing_mode`/`provider_pin`, `caps`, `cost_ceiling_usd`, `assumed_cost_on_raise`, `schema/score/taxonomy/metric versions`, and `artifact_hash`. **Any field change ⇒ new campaign ID (R5).** (VERIFIED)

**Critical:** the lockfile does NOT currently carry `include_extensions` or any index-corpus/pipeline identity field — only a single `artifact_hash`, which (see Item 5) tracks the DESCRIPTIONS surface, not the index corpus. (VERIFIED — no `include_extensions`/`pipeline_hash`/`ingestion` key in `lockfile.py`.)

### What `pipeline_hash` freezes (the product-side index identity)

`python/pydocs_mcp/retrieval/config/app_config.py:354-419` — `ingestion_pipeline_hash` = `sha256(embedder-identity | backend-identity | [LI-identity] | ingestion.yaml-bytes)`. It folds the embedder, the search backend, and the raw ingestion-YAML bytes. **It does NOT fold `include_extensions`** — that field lives in `extraction/config.py` (`DiscoveryScopeConfig`), not in the ingestion.yaml pipeline file. (VERIFIED)

Consequence: widening `include_extensions` (T1) does NOT change `pipeline_hash`. New non-Python files still get indexed on a *fresh* index (package-level content hash = xxh3 of `(path,mtime)` pairs changes when discovery scope admits new files — CLAUDE.md §Cache), but the pipeline identity is UNCHANGED, so nothing at the identity/comparability layer records that a different corpus was built. (VERIFIED for pipeline_hash composition; content-hash re-extraction = per CLAUDE.md, not re-derived here.)

### ADR-stated scope + purity freeze

- ADR 0016 §Out of scope (`docs/adr/0016-…md:34-37`): "optimizer adapters…, candidate text mutation, any frozen-test-set evaluation, **multi-language**, changes to frozen Phase 0–2 artifacts." (VERIFIED)
- ADR 0018 §Out of scope (`docs/adr/0018-…md:84-86`): "…**multi-language**, release engineering…, and any modification to the frozen upstream artifacts (R7 — P0 contracts, P1 format/renderer, P2…)." (VERIFIED)
- ADR 0014 purity finding (`docs/adr/0014-…md:52-57`): "The PROJECT index is a pure function of the repo at base commit" — project extraction never imports (static AST reads), only the embedder identity folds into `pipeline_hash`. The campaign indexes with `--skip-deps --no-inspect` (`index_cache.py:36`) to hold this purity. (VERIFIED)

### Landing deadline (precise)

Multi-language is **out of scope for both the baseline (0016) and optimization (0018) campaigns**. Therefore the R5/R7-comparability rule is: **all T1–T3 code must be merged BEFORE the `campaign.lock.json` is written (the launch/freeze moment), so it is either (a) fully absent from the campaign or (b) fully captured in the campaign_id.** Concretely:

1. **Before the baseline `prebuild-index` step** (the first paid-arc host action, `campaign/__main__.py:33-41`): if any canonical index is built with multilang extensions, EVERY paired cell in that campaign must share that corpus, else R5 "one corpus/config per cell" (`aggregator.py:69-73`) is violated across cells that reused a differently-built cache slot (see Item 5). The safe rule: **do not land a default-on `include_extensions` widening into the interpreter the campaign's `prebuild-index` uses, unless it is folded into the lockfile identity.**
2. **Before `write_lockfile`** (`lockfile.py:247-258`): any multilang setting that affects the served corpus MUST be represented in a lockfile field (new `index_scope`/`include_extensions` entry, or a corpus-identity `artifact_hash` that actually reflects the index — Item 5), or two campaigns differing only in multilang are indistinguishable by `campaign_id`. (VERIFIED mechanism; the "must add a field" is the required-fix conclusion.)

**Deadline statement:** multilang must land (merged + defaulted-OFF, or fully lockfile-captured) **before the first `prebuild-index` of the paid arc**. Landing it AFTER a campaign's index cache is built but reusing the same `--cache-root` silently voids R5/R7 comparability (Item 5). The paid arc's measured-slot fill (prereg `[TO BE MEASURED]`, `campaign_preregistration.yaml:32-38`) and the `campaign.lock.json` write are the two freeze events; multilang code changes after either are a new-campaign-ID event, never an in-place edit. (Design conclusion grounded in VERIFIED R5 mechanics.)

---

## Item 5 — Index-cache interaction: do cached slots key on pipeline identity?

**NO. Cached index slots key on PATH ONLY (repo@commit + abs-path md5). A multilang-enabled campaign pointed at a `--cache-root` that already holds Python-only canonical indexes would REUSE them, NOT rebuild.** This is the sharpest comparability hazard.

Key-derivation evidence:
- `benchmarks/src/pydocs_eval/campaign/index_cache.py:54-58` — `canonical_checkout_dir(cache_root, repo, commit) = <cache_root>/<repo_slug>@<commit>`. Keyed on `(repo, commit)` only. (VERIFIED)
- `index_cache.py:79-90` — `canonical_index_paths` derives the db name from `cache_path_for_project(checkout_dir).name`. (VERIFIED)
- `python/pydocs_mcp/db.py:171-181` — `cache_path_for_project` = `CACHE_DIR / f"{name}_{md5(abs_path)[:10]}.db"`. **Purely a function of the absolute path.** No embedder, no pipeline_hash, no include_extensions. (VERIFIED)
- `index_cache.py:138-156` — `index_checkout`: `db, tq = canonical_index_paths(...)`; **`if db.exists(): return db, tq`** — an already-built `.db` at that path short-circuits and is returned WITHOUT ever invoking the indexer. (VERIFIED)

Consequence: the short-circuit happens at the FILE-EXISTENCE level, BEFORE the product's `pipeline_hash` chunk-invalidation logic could ever run. So even though `pipeline_hash` would (partially) invalidate chunks on an embedder/ingestion change, the campaign cache never gives it the chance for a pre-existing canonical slot. And since `include_extensions` isn't in `pipeline_hash` anyway (Item 4), even a fresh in-product re-index wouldn't distinguish multilang-on from multilang-off by pipeline identity. (VERIFIED: short-circuit at `:153`; pipeline_hash composition at `app_config.py:354-419`.)

**The corpus-identity stamp does NOT catch this either.** The trace-header `artifact_hash` that the aggregator's R5 guard checks (`aggregator.py:68-73`, reject >1 `artifact_hash` per cell) is the DESCRIPTIONS artifact, not the index corpus:
- `python/pydocs_mcp/observability/trace_recorder.py:136-142` — the server trace header sets `"artifact_hash": current_artifact_hash()`. (VERIFIED)
- `python/pydocs_mcp/application/description_source.py:450-469` — `current_artifact_hash()` hashes `SERVER_INSTRUCTIONS + TOOL_DOCS + SESSION_START_PREAMBLE + RENDERER_VERSION` — the tool-description surface (the Phase 4 optimization SEED), **entirely corpus-independent.** (VERIFIED)
- `benchmarks/src/pydocs_eval/trajectory/merge.py:333` — merge reads `server_header.get("artifact_hash",...)` verbatim; `build_run_config` (`rollout.py:280-298`) folds model/arm/versions/instance but NO serve/index-corpus identity. (VERIFIED)

So a multilang-on cell and a multilang-off cell would carry the **same `artifact_hash`** (the pinned descriptions seed) → the aggregator's "one artifact_hash per cell" guard cannot detect that the two cells actually ran different corpora. The comparison would look valid and be silently contaminated. (VERIFIED chain: artifact_hash = descriptions-only; guard keys on artifact_hash.)

**What T1–T3 must add for a valid "multilang on/off" ablation (required fix, not optional):** give the INDEX corpus its own identity that (i) makes cache slots rebuild rather than reuse across pipeline identities, and (ii) is visible to the campaign guard. Options (all additive, none touch the frozen descriptions seed):
- fold `include_extensions` (or a `DiscoveryScopeConfig` hash) into `pipeline_hash` AND into the `canonical_checkout_dir` slug (so the slot path differs per pipeline identity); and/or
- add a distinct `index_artifact_hash` to the trace header + a `lockfile` field, so multilang-on/off cells carry distinguishable corpus identities; and/or
- operationally, run multilang-on and multilang-off in SEPARATE `--cache-root`s (the runner already takes `--cache-root`, `campaign/__main__.py:94`) — the cheapest interim lever, but it relies on operator discipline, not a structural guard.

The pre-seed COPY (`index_cache.py:165-212`, never hardlink) is orthogonal — it protects WAL write-back poisoning, not pipeline-identity keying. (VERIFIED distinction.)

---

## Cross-cutting confirmations

- **Nine-tool MCP surface / `docs/tool-contracts.md` frozen:** none of the eval-side changes above touch the MCP surface — all are YAML config (`include_extensions`), registry/aggregator plumbing, and campaign-config fields. (VERIFIED by construction — no server.py tool signature is implicated.)
- **`defaults/descriptions.md` seed unchanged:** the corpus-identity fix must NOT ride on `current_artifact_hash()` (that IS the pinned seed, `description_source.py:450-469`); Item 5's fix explicitly adds a SEPARATE index identity. (VERIFIED that artifact_hash = descriptions seed; separation = required-fix.)
- **Index purity (ADR 0014) survives T3:** tree-sitter parses repo file bytes → AST without importing or executing the project, matching the "static AST/file reads" purity discipline (`docs/adr/0014-…md:52-57`) the campaign's `--no-inspect` mode already enforces (`index_cache.py:36`). Grammar set must be pinned/deterministic (not env-discovered) to keep the index a pure function of repo files. (VERIFIED purity statement; tree-sitter compatibility = design conclusion.)
