# Derived-row cache folds (#347) — Implementation Plan

> **For agentic workers:** execute task by task, test-first. Each task ends with one
> commit (no Co-Authored-By trailer — owner rule). Run everything through
> `uv run --no-sync`.

**Goal:** A changed member-extraction, reference-capture or structuring-LLM setting
reaches every package it affects, once, and then settles, while a stock deployment's
stored package hashes stay byte-identical.

**Issue:** #347, the follow-up audit to #263 / #345. The issue lists three gaps:

1. `extraction.members.*`, `--depth` and `--no-inspect` reach no cache key, and member
   extraction runs *after* the package cache check.
2. `reference_graph.capture.{enabled, kinds}` reaches no cache key. Capture runs
   *before* the check, and its output is discarded on a cache hit.
3. The `llm:` identity under `decision_capture.llm_structuring.enabled` is not folded.

**Grounding:** a read-only mapping pass over `main` at `cff8c7a2`. Four readers plus a
critic checked each load-bearing claim at its file:line. The decisions below are its
reconciled output, reviewed by the main session.

---

## Decisions

**D1 — The member fold applies to DEPENDENCY targets only.** Every setting in the issue
still gets a fold, but "every package" is narrowed to dependencies:

- Project members always come from `AstMemberExtractor`, whatever `use_inspect`,
  `--depth` or the caps say (`InspectMemberExtractor` delegates the project to its
  `static_fallback`). A project fold could therefore never change output; it would
  only re-extract.
- That needless re-extract would hit every static-mode deployment, and every benchmark
  or harness rollout that serves a canonical index built with `--no-inspect`.
- A guard test pins the invariant: project `module_members` are byte-identical under
  inspect and static mode, whatever the depth and caps.

**D2 — Transport.** The effective member identity is known only inside
`storage/factories.py::build_project_indexer`, where `use_inspect` and the CLI-resolved
`depth` live. The ingestion pipeline is built there too, so the value travels as a plain
string:

- `BuildContext.member_extraction_token: str = ""` (in `retrieval/serialization.py`),
  following the `pipeline_hash` wiring precedent. A string keeps
  `retrieval/serialization.py` free of any extraction-type import.
- `build_ingestion_pipeline` / `load_ingestion_pipeline` (`extraction/factories.py`)
  take a keyword `member_extraction_token=""` and pass it into `BuildContext`.
- `""` is the no-fold path for hand-wired roots (the eval suite's `PydocsMcpSystem`,
  the test harnesses).
- `build_project_indexer` keeps its signature. It hoists `members_cfg` and `depth`
  above the pipeline build and computes the token from the same locals it builds the
  extractor from.

**D3 — Member token and stock pin.** A helper builds the token:
`member_extraction_token(*, use_inspect, depth, members)`, in a new leaf module under
`extraction/strategies/members/`.

- It returns `"static"` when `use_inspect` is false. `AstMemberExtractor` ignores the
  depth and all three caps.
- Otherwise it returns `"inspect|depth=D|cap=C|sig=S|doc=K"`.
- The pin is the readable literal
  `_STOCK_MEMBER_EXTRACTION_TOKEN = "inspect|depth=1|cap=120|sig=200|doc=1024"`,
  placed in `content_hash.py` next to `_STOCK_DECISION_CAPTURE_DIGEST`. It is pinned
  for the same reason as that digest: a later default change must fold by itself.
- The fold is `"members:<token>"`, applied only to DEPENDENCY targets whose token is
  non-empty and differs from the pin.
- A static-mode deployment with dependencies re-extracts those dependencies once on
  upgrade. The CHANGELOG says so.

**D4 — Reference capture reads `app_config`.** Both the stage and the hash take the
setting from `app_config.reference_graph.capture`:

- `ReferenceCaptureStage` gains `config: ReferenceCaptureConfig | None = None`.
  `from_dict` binds `app_config.reference_graph.capture` when an app_config is
  present, and `run()` uses `self.config or _get_capture_config()`.
- `to_dict` is unchanged, so no pipeline YAML byte moves.
- Side effect, recorded in `benchmarks/CHANGELOG.md`: hand-wired roots that never
  pushed the module global now capture what their YAML asks for. A MENTIONS benchmark
  config therefore captures MENTIONS.
- Salt: `"refs:disabled"` when capture is off. Otherwise it is
  `"refs:" + ",".join(sorted(set(kinds)))`, so kind order and duplicates never move
  the hash.
- The salt is `None` when it equals the pin
  `_STOCK_REFERENCE_CAPTURE_TOKEN = "refs:calls,imports,inherits"`. Verify the stock
  kinds against `_DEFAULT_CAPTURE_KINDS` before pinning.
- It applies to EVERY target kind, because capture does not gate on target kind.

**D5 — The LLM identity rides inside the decision token.** It is appended as
`"|llm:" + md5("provider|model_name|temperature|max_tokens")[:16]`, where the fields
come from `LlmConfig` in `retrieval/config/embedder_models.py`:

- only when `decision_capture.enabled` AND `llm_structuring.enabled` AND the target is
  PROJECT, which is exactly where structuring runs today;
- from an explicit field allowlist that never includes `api_key` or any endpoint
  field;
- stock still folds nothing, because `llm_structuring.enabled: true` already moves the
  decision digest off its pin.

**D6 — Layout and order.** `_hash` stops taking one positional argument per salt. It
folds an ordered tuple of optional salts in one loop, so the new salts need no new
positional parameters and complexity stays within the 20-line and complexipy limits.

- The order becomes: exclusion → module-id rule (project) → decisions, including the
  LLM identity (project) → members (dependencies) → refs (all) → grammars →
  chunk-tree → identity.
- The narrowest scopes still come first.
- Every new fold is conditional, so every stock hash is byte-identical and **no
  existing golden may move**. A golden that moves under stock settings means the
  design is violated: fix the code, never the golden.

**D7 — Constraints.**

- Never edit `pipelines/ingestion*.yaml`, not even a comment. Their raw bytes feed
  `ingestion_pipeline_hash`, so an edit re-embeds every deployment.
- Never add a field to `DecisionCaptureConfig`. It would move the pinned stock digest
  and re-extract every project.

---

## Tasks

### Task 1 — `_hash` over ordered optional salts, and the reference-capture fold

- **Refactor, behaviour-preserving:** `_hash` folds an ordered tuple of optional
  salts. Every existing content-hash suite stays green unchanged.
- **`ReferenceCaptureStage.config`** (D4): add the field and the `from_dict` binding.
  Test that `from_dict` binds `app_config`'s capture config, and that a bare stage
  still reads the global.
- **`ContentHashStage.reference_capture`** (default factory), read in `from_dict` with a
  tolerant `getattr` so `object()` and `SimpleNamespace` contexts keep working. Add the
  pin and `_reference_capture_salt`.
- **Tests, unit:**
  - the shipped `AppConfig.load().reference_graph.capture == ReferenceCaptureConfig()`,
    isolated from the environment like the #345 suite's `_clean_config_env`;
  - a pinned-token golden;
  - kind order and duplicates do not move the hash;
  - `enabled: false` moves it;
  - a dependency moves too;
  - a fresh-interpreter `PYTHONHASHSEED=random` determinism check;
  - `to_dict` is unchanged;
  - the oracle (`tests/extraction/_content_hash_oracle.py`) gains
    `reference_capture_token` / `reference_capture_folded`;
  - the fold-composition suite (`tests/extraction/test_content_hash_fold_composition.py`)
    gains the new fold in its documented position, with permutations and a
    "stock drops exactly this fold" test.
- **Tests, integration:** `tests/integration/test_reference_capture_fold_settles.py`,
  mirroring `tests/integration/test_decision_capture_fold_settles.py`. Use a README with
  a backticked dotted name.
  - Adding `mentions` creates MENTIONS rows once, then settles.
  - `enabled: false` leaves zero calls/imports/inherits rows (GOVERNS excluded).
  - Returning to stock restores the exact stock hash.
  - Restore the capture global afterwards.
- **Commit:** `fix(cache): fold reference_graph.capture into every package hash (#347)`

### Task 2 — the member-extraction token and the dependency-only member fold

- **Token and wiring:** add the leaf helper (D3), `BuildContext.member_extraction_token`,
  and the factory kwargs (D2). Have `build_project_indexer` compute the token from the
  same locals that build the extractor.
- **`ContentHashStage`:** add `member_extraction_token: str = ""` (from `from_dict`'s
  context), the pin, and `_member_extraction_salt(token, target_kind)`.
- **Tests, unit:**
  - the token for static, inspect at defaults, and each cap changed;
  - `AppConfig.load().extraction.members == MembersConfig()`;
  - a golden for the pin;
  - the member fold moves a dependency and never the project;
  - `""` folds nothing;
  - a wiring pin in the `build_project_indexer` tests: the stage's token equals what
    the built extractor implies (static; an explicit `--depth`; the YAML fallback);
  - the invariant (D1): project members are identical under inspect and static.
- **Tests, integration:** extend `tests/_index_fixture.run_pass_with_embedder` with
  keyword-only `use_inspect=False`, `inspect_depth=None` and `dependency_names=()`.
  When names are given, swap in a `FakeDependencyResolver(names)` (`tests/_fakes.py`)
  and set `include_dependencies=True`. Existing callers must be unaffected.
  - New `tests/integration/test_member_extraction_fold_settles.py`, using a small real
    installed dependency (e.g. `sniffio` if present in the dev venv), inspect mode, and
    `members_per_module_cap` 120 → 1, then static ↔ inspect.
  - Each changed setting gives one `indexed` pass, then `cached`, and `failed == 0` on
    every pass.
  - Assert on the stored `module_members` rows.
  - Returning to stock restores the stock dependency hash.
- **Commit:** `fix(cache): fold the effective member-extraction settings into dependency hashes (#347)`

### Task 3 — the structuring-LLM identity in the decision token

- Extend `_decision_capture_salt` per D5, with a leaf predicate
  `llm_structuring_applies(config, target_kind)` that the salt uses (put it in a leaf
  module, e.g. `extraction/decisions/capture_gates.py`, importing only config and
  `TargetKind`; check for import cycles). `StructureDecisionsStage` keeps its own gate
  unchanged in this task.
- `ContentHashStage` receives `llm: LlmConfig` via `from_dict` (tolerant `getattr`,
  default factory).
- **Tests:**
  - switching `llm.model_name` A → B moves the project hash;
  - `api_key` alone does not move it;
  - `llm_structuring.enabled: false` ignores `llm:` entirely;
  - a dependency never carries the LLM part.
- **Integration:** `tests/integration/test_llm_identity_fold_settles.py`. Patch
  `build_llm_client` with a named fake returning `{"decisions": []}`. Changing the model
  re-extracts once and settles; an `api_key` change re-extracts nothing.
- **Commit:** `fix(cache): fold the structuring LLM's identity into the decision token (#347)`

### Task 4 — docs

- **`CHANGELOG.md` `[Unreleased]` `### Fixed`:** one bullet in the file's voice, placed
  after the #263 bullet. It says:
  - dependency member settings, `--depth` and `--no-inspect` now reach the cache;
  - `reference_graph.capture` does too, for every package;
  - so does the structuring LLM's identity;
  - stock deployments re-extract nothing;
  - tuned or static-mode deployments re-extract the affected packages once.
- **`benchmarks/CHANGELOG.md`:** in-process capture honours the YAML (D4).
- **`CLAUDE.md` Cache bullet:** add the new folds in code order with their scopes.
- **`default_config.yaml`:** comment-only notes on the `members` and
  `reference_graph.capture` blocks; values unchanged.
- **`DOCUMENTATION.md`:** at most one sentence in the cache section.
- Commit this plan file as `docs/superpowers/plans/2026-09-23-derived-row-cache-folds.md`
  in the same commit.
- **Commit:** `docs: the derived-row cache folds — changelog, cache notes, plan (#347)`

---

## Follow-ups (not in this PR — file as issues)

- `reference_graph.resolver.{include_stdlib, strict_suffix}` are applied after the
  cache check.
- `reference_graph.similar_edges.{enabled, top_m}` are a module global, and their stage
  runs before `content_hash`.
- The eval suite's bench-cache and campaign `scope_id` key only on
  `ingestion_pipeline_hash`.
- Multi-branch P1's `file_extractions` key (#261) must also carry these settings
  components once it is read.
