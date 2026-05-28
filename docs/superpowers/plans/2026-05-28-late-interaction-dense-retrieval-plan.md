# Late-Interaction Dense Retrieval Implementation Plan

> **For agentic workers:** This is a **multi-PR roadmap**, not a single-PR
> bite-sized plan. Each PR section below is intended to be re-planned via
> `superpowers:writing-plans` into a per-PR task list once the prior PR
> merges. Inside each PR follow the standard rhythm: failing test FIRST,
> smallest change to green, full-suite gate, commit; `/code-review` +
> `/review` after every task; one final code-reviewer subagent over the
> full PR diff before merge.

**Goal:** Ship late-interaction (ColBERT / PyLate) multi-vector dense
retrieval as an **opt-in** extension of the sklearn-shaped retrieval
pipeline — a new embedder Protocol + PyLate concrete, a dedicated
multi-vector SQLite store, a MaxSim re-ranker step, and one new chunk-search
preset YAML. The default install stays ~90 MB (no torch); the existing
hybrid + RRF preset is unchanged; no MCP tool params change.

**Spec:** `docs/superpowers/specs/2026-05-28-late-interaction-dense-retrieval-design.md`
(1073 lines, 10 locked Decisions A–J, 18 ACs).

**Architecture (inside-out — matches PR order):** new `MultiVectorEmbedder`
Protocol + `LateInteractionConfig` sub-model (contracts) → `chunk_vectors`
schema v6 + `SqliteMultiVectorUnitOfWork` (storage adapter) → `PyLateEmbedder`
+ `build_multi_vector_embedder(cfg)` lazy factory (embedder adapter) →
`EmbedChunksMultiVectorStage` + `ingestion_late_interaction.yaml` (write
path) → `LateInteractionScorerStep` + `BuildContext.multi_vector_embedder`
+ `_maxsim` (retrieval step) → `build_sqlite_plus_multi_vector_uow_factory`
+ `server.py` / `__main__.py` wiring (composition root) →
`chunk_search_late_interaction{,_ranked}.yaml` + `PydocsLateInteractionSystem`
+ docs (end-to-end + smoke benchmark).

**Hard constraints (inherited from CLAUDE.md and the spec):**

- Default install ships ~90 MB with no `pylate` / `torch` /
  `sentence_transformers` in `sys.modules` (AC-13).
- `pylate` is an **optional extra**; the actionable `ImportError` fires
  only when the late-interaction preset is selected (Decision E, AC-14).
- The MCP surface stays fixed at `search` + `lookup`. No new MCP params.
  Every knob below is YAML, gated through `AppConfig` (CLAUDE.md §"MCP API
  surface vs YAML configuration").
- New application services depend on a single `uow_factory:
  Callable[[], UnitOfWork]` per CLAUDE.md §"Creating new application
  services"; no direct `*Repository` kwargs.
- Frozen + slots dataclasses; `dataclasses.replace` for state mutation;
  scratch built fresh inside any step that may run in a `ParallelStep`
  branch (CLAUDE.md §"`RetrieverState.scratch` mutation discipline").
- All default values live in ONE place — module-level `_DEFAULT_X` or
  pydantic `Field(default=...)` (CLAUDE.md §"Default values: single source
  of truth").
- TDD: failing test FIRST per PR task; full-suite gate after each commit;
  every commit authored solely by
  `Max Raphael Sobroza Marques <max.raphael@gmail.com>`, zero
  `Co-authored-by:` trailers (mirrors AC-17 carried by every recent PR).
- README jargon audit-grep returns zero matches before any README change
  merges (CLAUDE.md §"README files: no internal PR / sub-PR / task jargon").

---

## 1. PR sequencing — dependency DAG

The work splits into **seven** atomic PRs. Inside-out ordering means each
PR leaves the codebase green even though earlier PRs introduce contracts
that no production code uses yet — exactly the pattern shipped by the
hybrid-search and chunk-cache plans.

```
                    ┌──────────────────────────────────┐
                    │ PR-1: contracts                  │
                    │  MultiVectorEmbedder Protocol    │
                    │  LateInteractionConfig sub-model │
                    │  FakeMultiVectorEmbedder         │
                    └──────────────┬───────────────────┘
                                   │
            ┌──────────────────────┼──────────────────────┐
            ▼                      ▼                      ▼
  ┌──────────────────┐   ┌──────────────────────┐   (PR-1 lands first;
  │ PR-2: storage    │   │ PR-3: embedder       │   PR-2 + PR-3 are
  │  chunk_vectors   │   │  PyLateEmbedder      │   independent of each
  │  schema v6       │   │  lazy factory        │   other and can land
  │  SqliteMulti-    │   │  pylate optional     │   in either order)
  │  VectorUnitOfWork│   │  extra in pyproject  │
  └────────┬─────────┘   └──────────┬───────────┘
           │                        │
           └────────────┬───────────┘
                        ▼
          ┌──────────────────────────────┐
          │ PR-4: ingestion              │
          │  EmbedChunksMultiVectorStage │
          │  ingestion_late_interaction  │
          │   .yaml preset               │
          │  pipeline_hash fold (Dec J)  │
          └──────────────┬───────────────┘
                         │
                         ▼
        ┌────────────────────────────────────┐
        │ PR-5: retrieval step               │
        │  LateInteractionScorerStep (MaxSim)│
        │  BuildContext.multi_vector_embedder│
        │  _maxsim core + step registry      │
        └──────────────┬─────────────────────┘
                       │
                       ▼
       ┌─────────────────────────────────────────┐
       │ PR-6: composition root                  │
       │  build_sqlite_plus_multi_vector_uow_    │
       │   factory                               │
       │  server.py / __main__.py / factories.py │
       │   gated wiring (no torch import on      │
       │   default path — AC-13)                 │
       └──────────────┬──────────────────────────┘
                      │
                      ▼
      ┌────────────────────────────────────────────┐
      │ PR-7: presets + benchmark + docs           │
      │  chunk_search_late_interaction.yaml        │
      │  chunk_search_late_interaction_ranked.yaml │
      │  PydocsLateInteractionSystem               │
      │  default_config.yaml commented block       │
      │  CLAUDE.md retrieval-step enumeration      │
      └────────────────────────────────────────────┘
```

**Critical-path length:** 6 (PR-1 → PR-2 OR PR-3 → PR-4 → PR-5 → PR-6 →
PR-7). PR-2 and PR-3 can be developed in parallel after PR-1 lands; the
rest is strictly serial.

**Why inside-out?** Contracts (Protocols + typed config) land before
adapters (storage + embedder concrete) so the adapters have a fixed target
and can be unit-tested via fakes. Adapters land before the write path so
the ingestion stage can route real matrices into a real store with a
fake-embedder unit test. The write path lands before the retrieval step so
the step can be integration-tested against a seeded `chunk_vectors`
fixture. Composition-root wiring lands before the preset YAMLs so the
YAMLs round-trip against a real `BuildContext`. This is the same shape
the hybrid-search plan shipped — and the same shape the spec's "Suggested
commit shape" suggests.

---

## 2. PR-1 — contracts (`MultiVectorEmbedder` Protocol + `LateInteractionConfig`)

**Scope (1-2 sentences):** Add the abstractions every other PR depends on:
the `MultiVectorEmbedder` Protocol next to `Embedder`, the
`LateInteractionConfig` sub-model on `AppConfig`, and a
`FakeMultiVectorEmbedder` test double. No concrete embedder, no storage
change, no production code path uses these yet.

**Spec refs:** Decision A (Protocol shape), Decision F
(`LateInteractionConfig`), AC-1 (Protocol shape & L2-norm), AC-2 (config
shape).

**Files touched:**

- Modify: `python/pydocs_mcp/storage/protocols.py` — add
  `MultiVectorEmbedder` Protocol next to `Embedder`. Imports
  `MultiVector` + `is_multi_vector` from `models.py` (both already
  present).
- Modify: `python/pydocs_mcp/retrieval/config.py` — add
  `LateInteractionConfig` (twin of `EmbeddingConfig`) + the
  `AppConfig.late_interaction` field. The `compute_pipeline_hash` method
  on `LateInteractionConfig` lands here; the fold into
  `ingestion_pipeline_hash` is **deferred to PR-4** so this PR doesn't
  touch the cache invalidation path.
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` — append a
  commented-out `late_interaction:` block (mirroring the `llm:` block).
- Modify: `tests/_fakes.py` — add `FakeMultiVectorEmbedder` (deterministic
  seeded-random `(n_tokens, dim)` matrices, L2-normalized per row).
- Create: `tests/storage/test_multi_vector_embedder_protocol.py`
- Create: `tests/retrieval/test_config_late_interaction.py`
- Create: `tests/test_fake_multi_vector_embedder.py`

**Acceptance criteria (mapped to spec ACs):**

- AC-1 covered for the Protocol shape and L2-normalization invariant
  (verified against `FakeMultiVectorEmbedder`). `PyLateEmbedder`-specific
  assertions defer to PR-3.
- AC-2 covered in full.

**Test plan (TDD — failing tests first):**

- `tests/storage/test_multi_vector_embedder_protocol.py`:
  - `test_multi_vector_embedder_protocol_runtime_checkable` — `FakeMultiVectorEmbedder()` passes `isinstance(obj, MultiVectorEmbedder)`.
  - `test_multi_vector_embedder_has_embed_query_async` — `inspect.iscoroutinefunction(MultiVectorEmbedder.embed_query)`.
  - `test_multi_vector_embedder_has_embed_documents_async` — same for `embed_documents`.
  - `test_multi_vector_embedder_declares_dim_and_model_name` — source-scan assertion (mirrors `test_llm_client_protocol_has_model_name`).
- `tests/retrieval/test_config_late_interaction.py`:
  - `test_late_interaction_config_defaults` — `provider="pylate"`, `model_name="lightonai/LateOn-Code"`, `dim=128`, `document_length=180`, `query_length=32`, `pool_factor=1`, `candidate_limit=100`.
  - `test_app_config_late_interaction_field_present` — `AppConfig().late_interaction` is a `LateInteractionConfig`.
  - `test_app_config_yaml_overlay_for_late_interaction` — overlay edits `model_name` + `pool_factor` and they round-trip via `AppConfig.load(explicit_path=...)`.
  - `test_app_config_env_overlay_for_late_interaction` — `PYDOCS_LATE_INTERACTION__POOL_FACTOR=2` env overlay applies.
  - `test_late_interaction_config_extra_forbid` — unknown key raises at load.
  - `test_late_interaction_compute_pipeline_hash_changes_on_model_swap` — `model_name="A"` vs `"B"` produce different hashes; `pool_factor=1` vs `2` produce different hashes; `candidate_limit` change does NOT change the hash (retrieval-time knob, not storage identity).
- `tests/test_fake_multi_vector_embedder.py`:
  - `test_fake_satisfies_multi_vector_protocol` — `isinstance` check.
  - `test_fake_embed_query_returns_2d_float32_l2_normalized` — shape `(nq, dim)`, dtype `float32`, every row's L2-norm within `1e-5` of `1.0`.
  - `test_fake_embed_documents_returns_tuple_of_matrices` — input `("a", "b")`, output is a 2-tuple of `(nd, dim)` matrices.
  - `test_fake_seed_determinism` — same seed + same input ⇒ byte-identical output.

**Dependencies:** none (this is the root PR).

**Risks / open questions:**

- Spec Open Item #1: confirm `lightonai/LateOn-Code` projection dim is 128
  at implementation time by reading `1_Dense/config.json`. If it differs,
  update the `Field(default=...)` in this PR (single source of truth).
  Surface the value in the PR description.
- The `LateInteractionConfig.compute_pipeline_hash` test is forward-looking
  (used in PR-4); landing it here is safe — `ingestion_pipeline_hash`
  doesn't read it yet.

**Estimated size:** small. ~120 LOC of production code + ~150 LOC of
tests across three test files.

---

## 3. PR-2 — multi-vector storage (`chunk_vectors` schema v6 + `SqliteMultiVectorUnitOfWork`)

**Scope (1-2 sentences):** Add the `chunk_vectors` SQLite table (one BLOB
row per chunk = `(n_tokens, dim)` float32 matrix), bump
`SCHEMA_VERSION` from 5 to 6, and ship `SqliteMultiVectorUnitOfWork` — a
sibling of `TurboQuantUnitOfWork` that satisfies the same `uow.vectors`
surface so `CompositeUnitOfWork` dispatches to it identically.

**Spec refs:** Decision B (B2 — dedicated SQLite BLOB store), AC-4 (schema
migration), AC-5 (round-trip).

**Files touched:**

- Modify: `python/pydocs_mcp/db.py` — add `chunk_vectors` DDL inside
  `SCHEMA`; bump `SCHEMA_VERSION` from 5 to 6; add `chunk_vectors` to
  `_KNOWN_TABLES`. Composes with the existing wipe-and-recreate-on-mismatch
  migration; no additive migration helper is needed because v5→v6 is
  table-add only and `_drop_all_known_tables` covers it.
- Create: `python/pydocs_mcp/storage/multi_vector_store.py` —
  `SqliteMultiVectorUnitOfWork` (`@dataclass(frozen=True, slots=True)`).
  Surface: `__aenter__` / `__aexit__` / `commit` / `rollback` mirror
  `TurboQuantUnitOfWork`; `add_vectors(ids, embeddings)` validates each
  emb is a `MultiVector` (2-D ndarray), serializes via
  `mat.astype(np.float32, copy=False).tobytes()` into the BLOB column;
  `load_matrices(ids) -> dict[int, np.ndarray]` reads + `np.frombuffer` +
  reshape; `remove_vectors(ids)` and `clear_all()` are straightforward
  SQL.
- Modify: `python/pydocs_mcp/storage/__init__.py` — re-export the new UoW
  if any other module re-exports `TurboQuantUnitOfWork` (sniff before
  editing).
- Modify: `tests/_fakes.py` — extend `make_fake_uow_factory(...)` to
  accept a `vectors=` kwarg pointing at a fake `MultiVectorStore` (a
  thin in-memory dict-backed double); existing single-vector-store
  call sites keep working.
- Create: `tests/storage/test_multi_vector_store.py`
- Create: `tests/storage/test_schema_v6_chunk_vectors.py`

**Acceptance criteria:**

- AC-4 in full: fresh DB has `chunk_vectors`; a synthesized v5 DB triggers
  the wipe-and-recreate path on open (existing behavior — no new code);
  `chunk_vectors` appears in `_KNOWN_TABLES`.
- AC-5 in full: round-trip equality (`np.array_equal`), atomic commit /
  rollback semantics (commit persists, exception rolls back).

**Test plan (TDD — failing tests first):**

- `tests/storage/test_schema_v6_chunk_vectors.py`:
  - `test_schema_version_bumped_to_6` — `from pydocs_mcp.db import SCHEMA_VERSION; assert SCHEMA_VERSION == 6`.
  - `test_fresh_db_has_chunk_vectors_table` — open a fresh `.db`, query `sqlite_master` for `chunk_vectors`.
  - `test_chunk_vectors_in_known_tables` — `"chunk_vectors" in _KNOWN_TABLES`.
  - `test_v5_db_triggers_wipe_and_recreate` — write `PRAGMA user_version=5` to a tmp DB, open via the schema helper, assert tables get recreated at v6.
- `tests/storage/test_multi_vector_store.py`:
  - `test_add_vectors_then_load_matrices_round_trip` — write one `(80, 128)` matrix, read it back, `np.array_equal`.
  - `test_add_vectors_multiple_ids_independent` — two chunks, two distinct matrices, `load_matrices([id1, id2])` returns both.
  - `test_remove_vectors_deletes_row` — add then remove, `load_matrices` returns empty dict for that id.
  - `test_clear_all_empties_table` — add N matrices, `clear_all()`, table count is 0.
  - `test_add_vectors_rejects_single_vector_input` — passing a 1-D ndarray raises (the inverse of the existing `TurboQuantUnitOfWork` guard).
  - `test_commit_persists_rollback_discards` — within `async with`, add + commit persists; add + raise + reopen-and-read shows the row absent.
  - `test_model_name_column_stamped` — `add_vectors` writes the embedder `model_name` into the row (defense-in-depth per Decision J risk row).

**Dependencies:** PR-1 (uses `MultiVector` from `models.py`, but that
union has been there since the hybrid PR — strictly speaking PR-2 could
land before PR-1, but the test fixture `FakeMultiVectorEmbedder` from
PR-1 makes the integration test in PR-4 cleaner if PR-1 lands first; the
DAG above reflects that ordering choice).

**Risks / open questions:**

- The `model_name` column choice (Decision B's table sketch) is
  defense-in-depth against a model swap producing mismatched matrices.
  AC-5 doesn't explicitly require it; but Decision J's risk row names it,
  so we land it now. No spec gap — flagging for review.
- BLOB column type: SQLite's `BLOB` is the right type, but on-disk size
  per row is `n_tokens * dim * 4` bytes — for a worst-case 180×128×4 =
  ~92 KB per chunk. This is within SQLite's per-row limits (1 GB) but
  is the storage budget the spec §9 risk row flags. No mitigation in this
  PR; `pool_factor` (PR-3) is the lever.
- The `add_vectors` signature must match `TurboQuantUnitOfWork.add_vectors`
  exactly (same `Sequence[int]` + `Sequence[Embedding]`) so the existing
  `IndexingService._maybe_write_vectors` call site is a no-op swap. Verify
  signature parity in a test (`test_signature_parity_with_turboquant_uow`).

**Estimated size:** medium. ~150 LOC of production code + ~200 LOC of
tests across two test files + ~20 LOC of `_fakes.py` extension.

---

## 4. PR-3 — embedder concrete (`PyLateEmbedder` + lazy factory + optional extra)

**Scope (1-2 sentences):** Ship `PyLateEmbedder` (wraps PyLate's
`models.ColBERT`), the `build_multi_vector_embedder(cfg)` factory branch,
and the `late-interaction` optional extra in `pyproject.toml`. The PyLate
import is lazy — a default install with the extra absent never imports
torch, and the actionable `ImportError` fires only when the factory is
called for an unconfigured environment.

**Spec refs:** Decision A (concrete + factory), Decision E (optional
extra, lazy import), AC-1 (PyLate-specific shape), AC-3 (factory + lazy
extra), AC-14 (actionable ImportError when extra absent).

**Files touched:**

- Modify: `pyproject.toml` — add
  `[project.optional-dependencies] late-interaction = ["pylate>=3.0,<4.0"]`.
  Confirm `[tool.maturin] include` already covers `pipelines/*.yaml`
  (it does — no change needed for the YAML presets shipped in PR-7).
- Create: `python/pydocs_mcp/extraction/strategies/embedders/pylate.py` —
  `PyLateEmbedder` dataclass. Top-of-module `try / except ImportError`
  raises the actionable error spelled out in Decision E. `__post_init__`
  constructs `models.ColBERT(model_name_or_path=..., embedding_size=...,
  document_length=..., query_length=...)`. `embed_query` calls
  `model.encode([text], is_query=True, convert_to_numpy=True,
  normalize_embeddings=True)` wrapped in `asyncio.to_thread`;
  `embed_documents` does the batch equivalent with `is_query=False` +
  `pool_factor=...`.
- Modify: `python/pydocs_mcp/extraction/strategies/embedders/__init__.py` —
  add `build_multi_vector_embedder(cfg: LateInteractionConfig) ->
  MultiVectorEmbedder` next to `build_embedder`. Defers the PyLate import
  to inside the `if cfg.provider == "pylate":` branch.
- Modify: `tests/test_pyproject_extras.py` (or a new sibling) — assert
  the `late-interaction` extra is declared.
- Create:
  `tests/extraction/strategies/embedders/test_pylate_embedder.py` — uses
  `unittest.mock.patch` on `pylate.models.ColBERT` so the test runs
  without torch installed.
- Create: `tests/extraction/strategies/embedders/test_build_multi_vector_embedder.py`
- Create: `tests/test_pyproject_late_interaction_extra.py`

**Acceptance criteria:**

- AC-1 in full (PyLate-specific): `embed_query` / `embed_documents` shape
  + L2-norm verified against a mocked `ColBERT` returning canned arrays.
- AC-3 in full: factory returns a `PyLateEmbedder` for `provider="pylate"`;
  raises `ValueError` for unknown providers; the `pylate` import is lazy
  (a `sys.modules` snapshot before + after the factory call demonstrates
  no `pylate` import unless the branch fires).
- AC-14 in full: monkeypatched-absent `pylate` (block the import via
  `sys.modules["pylate"] = None` or a custom finder) raises the actionable
  `ImportError` only when the factory is called.

**Test plan (TDD — failing tests first):**

- `tests/extraction/strategies/embedders/test_pylate_embedder.py`:
  - `test_pylate_embedder_satisfies_protocol_with_mocked_colbert` — patch `pylate.models.ColBERT` to a `MagicMock` that returns deterministic L2-normalized arrays from `.encode(...)`; `isinstance(obj, MultiVectorEmbedder)`.
  - `test_pylate_embed_query_returns_2d_l2_normalized` — mocked `.encode([text], is_query=True, ...)` returns a `(nq, dim)` array; the embedder unwraps the `[0]`th item.
  - `test_pylate_embed_documents_passes_pool_factor` — instantiate with `pool_factor=2`; mocked `.encode(..., pool_factor=2)` is asserted via `call_args.kwargs["pool_factor"] == 2`.
  - `test_pylate_embed_documents_passes_is_query_false` — assert `call_args.kwargs["is_query"] is False`.
  - `test_pylate_embedder_lazy_import_error_actionable` — block `pylate` from `sys.modules`, importing `pylate.py` raises the actionable `ImportError` naming `pip install 'pydocs-mcp[late-interaction]'`.
- `tests/extraction/strategies/embedders/test_build_multi_vector_embedder.py`:
  - `test_factory_returns_pylate_embedder_for_pylate_provider` (with mocked ColBERT).
  - `test_factory_raises_on_unknown_provider` — `ValueError` matching `"Unknown late-interaction provider"`.
  - `test_factory_does_not_import_pylate_until_called` — snapshot `sys.modules` before + after `import pydocs_mcp.extraction.strategies.embedders`; assert `"pylate" not in sys.modules`. (Calling the factory with a fake provider literal stays at the `ValueError` branch — no PyLate import.)
- `tests/test_pyproject_late_interaction_extra.py`:
  - `test_late_interaction_extra_declared` — `tomllib.loads(pyproject)["project"]["optional-dependencies"]["late-interaction"]` contains a `pylate` pin.

**Dependencies:** PR-1 (`MultiVectorEmbedder` Protocol, `LateInteractionConfig`).

**Risks / open questions:**

- The PyLate kwarg names in Decision A's code example (`embedding_size`,
  `document_length`, `query_length`, `is_query`, `pool_factor`,
  `normalize_embeddings`, `convert_to_numpy`) are pinned in the spec's
  closing note. If a real PyLate-installed environment surfaces a
  mismatch (e.g. `normalize_embeddings` is rejected by `ColBERT.encode`),
  it's a real bug in the spec — surface as an open question via a `gh
  issue comment`. The mocked test runs without PyLate so can't catch
  this; the OPENAI_API_KEY-style integration test in PR-7 (gated on the
  extra being installed) is the catch.
- The Decision A code example uses a top-level `from pylate import
  models`. This MUST move into the `try / except` block per Decision E
  or the lazy-import promise breaks; AC-3's `sys.modules` assertion will
  catch a regression.

**Estimated size:** small-medium. ~80 LOC of production code + ~120 LOC
of tests + a one-line `pyproject.toml` edit.

---

## 5. PR-4 — ingestion (`EmbedChunksMultiVectorStage` + `ingestion_late_interaction.yaml` + `pipeline_hash` fold)

**Scope (1-2 sentences):** Add the multi-vector ingestion stage (the SRP
sibling of `EmbedChunksStage`), ship `ingestion_late_interaction.yaml`
that swaps the two stages, and fold
`late_interaction.compute_pipeline_hash()` into
`AppConfig.ingestion_pipeline_hash` when the active ingestion pipeline
references the multi-vector stage.

**Spec refs:** Decision I (new stage), Decision J (pipeline_hash fold),
AC-10 (stage behavior), AC-11 (hash invalidation), partial AC-12
(`ingestion_late_interaction.yaml` round-trip).

**Files touched:**

- Create:
  `python/pydocs_mcp/extraction/pipeline/stages/embed_chunks_multi_vector.py` —
  `EmbedChunksMultiVectorStage` (`@stage_registry.register("embed_chunks_multi_vector")`).
  Mirror `EmbedChunksStage`'s shape: same `existing_chunk_hashes` skip
  set, same batch loop, same `state.package.embedding_model` stamp, same
  `pipeline_hash` interaction. Replaces `Embedder.embed_chunks` with
  `MultiVectorEmbedder.embed_documents`; splices each `(n_tokens, dim)`
  matrix onto `Chunk.embedding`. `from_dict` raises if
  `context.multi_vector_embedder is None`.
- Create: `python/pydocs_mcp/pipelines/ingestion_late_interaction.yaml` —
  copy of `ingestion.yaml` with the `embed_chunks` entry swapped for
  `embed_chunks_multi_vector`.
- Modify: `python/pydocs_mcp/retrieval/serialization.py` — extend
  `BuildContext` with `multi_vector_embedder: "MultiVectorEmbedder | None"
  = None` (lands here so PR-4 + PR-5 share the same field; PR-5 uses it
  for the retrieval step).
- Modify: `python/pydocs_mcp/retrieval/config.py` — fold
  `late_interaction.compute_pipeline_hash()` into `ingestion_pipeline_hash`
  conditionally on `_ingestion_uses_multi_vector(ingestion_path)`. The
  helper does a cheap one-pass YAML scan for the literal
  `"embed_chunks_multi_vector"` token; the result is cached at the
  AppConfig instance level via the existing `cached_property`.
- Modify: `python/pydocs_mcp/extraction/pipeline/stages/__init__.py` — if
  the existing module re-exports `EmbedChunksStage`, add a sibling re-export.
- Create:
  `tests/extraction/pipeline/stages/test_embed_chunks_multi_vector.py`
- Create: `tests/retrieval/test_pipeline_hash_late_interaction_fold.py`
- Create:
  `tests/extraction/test_ingestion_late_interaction_yaml_round_trip.py`

**Acceptance criteria:**

- AC-10 in full.
- AC-11 in full.
- Partial AC-12: `ingestion_late_interaction.yaml` loads via the existing
  factory with a `BuildContext(multi_vector_embedder=Fake…)`, and the
  loaded pipeline contains the `embed_chunks_multi_vector` stage in the
  expected position.

**Test plan (TDD — failing tests first):**

- `tests/extraction/pipeline/stages/test_embed_chunks_multi_vector.py`:
  - `test_embed_chunks_multi_vector_splices_matrix_onto_chunk_embedding` — feed chunks + a `FakeMultiVectorEmbedder`, assert each output `Chunk.embedding.shape == (n_tokens, dim)` (the MultiVector arm).
  - `test_embed_chunks_multi_vector_honors_skip_set` — chunks with `content_hash in existing_chunk_hashes` are not re-embedded (assert via call-count on the fake).
  - `test_embed_chunks_multi_vector_stamps_package_embedding_model` — output `state.package.embedding_model == fake.model_name`.
  - `test_embed_chunks_multi_vector_from_dict_requires_context` — `from_dict({"type": "embed_chunks_multi_vector"}, BuildContext())` raises `ValueError` pointing at `multi_vector_embedder`.
  - `test_embed_chunks_multi_vector_round_trips_to_dict_from_dict` — `to_dict` then `from_dict(... , context=BuildContext(multi_vector_embedder=fake))` rebuilds an equal stage.
- `tests/retrieval/test_pipeline_hash_late_interaction_fold.py`:
  - `test_hash_unchanged_with_default_ingestion_pipeline` — default `ingestion.yaml` + change `late_interaction.model_name` ⇒ `ingestion_pipeline_hash` unchanged.
  - `test_hash_changes_on_late_interaction_model_name_swap` — point `extraction.ingestion.pipeline_path` at `ingestion_late_interaction.yaml`, change `late_interaction.model_name`, assert hash differs.
  - `test_hash_changes_on_pool_factor_change` — same setup, change `pool_factor`.
  - `test_hash_changes_on_document_length_change` — same setup, change `document_length`.
  - `test_hash_unaffected_by_candidate_limit_change` — `candidate_limit` is a retrieval-time knob, not a storage identity.
- `tests/extraction/test_ingestion_late_interaction_yaml_round_trip.py`:
  - `test_ingestion_late_interaction_loads_with_multi_vector_context` — `build_ingestion_pipeline(... , context=BuildContext(multi_vector_embedder=fake))` succeeds.
  - `test_ingestion_late_interaction_swaps_embed_chunks_for_multi_vector` — inspect the loaded pipeline; the embedder stage is the multi-vector variant.

**Dependencies:** PR-1 (`LateInteractionConfig` + Protocol), PR-2
(`SqliteMultiVectorUnitOfWork` is referenced via the `uow.vectors` surface
in tests that round-trip a chunk through ingest), PR-3 (`build_multi_vector_embedder`
is referenced in the YAML round-trip composition).

**Risks / open questions:**

- The cheap YAML scan in `_ingestion_uses_multi_vector` is a substring
  match for `"embed_chunks_multi_vector"`. A YAML comment containing that
  literal would trigger a false positive. Acceptable for v1 — surfaces as
  over-invalidation (re-embed unnecessarily), not under-invalidation
  (corrupt cache). Test:
  `test_ingestion_uses_multi_vector_substring_match_with_comments` — if
  this becomes a real issue, swap to a proper parsed-YAML walk.
- `EmbedChunksMultiVectorStage` must use the **same** `_DEFAULT_BATCH_SIZE`
  constant as `EmbedChunksStage` (single source of truth — import from
  the existing module). If the existing module makes it private, lift it
  to a sibling `_constants.py` and re-export.

**Estimated size:** medium. ~150 LOC of production code + ~200 LOC of
tests + ~30 LOC of YAML.

---

## 6. PR-5 — retrieval step (`LateInteractionScorerStep` + `_maxsim` + `BuildContext` plumbing)

**Scope (1-2 sentences):** Ship the MaxSim re-ranking step that closes
the read path: reads `state.candidates`, calls
`MultiVectorEmbedder.embed_query` for the query matrix, loads stored
matrices via `uow.vectors.load_matrices`, computes `_maxsim`, overwrites
`relevance`, sorts, and optionally publishes to `state.scratch[publish_to]`
for downstream fusion via the shipped `rrf_fusion` or
`weighted_score_interpolation` step.

**Spec refs:** Decision C (re-ranker, not first-stage fetcher), Decision
H (no new fuser — reuse shipped fusers), AC-6 (`_maxsim` math), AC-7
(happy path), AC-8 (`publish_to` + scratch hygiene), AC-9 (strict gate).

**Files touched:**

- Create: `python/pydocs_mcp/retrieval/steps/late_interaction_scorer.py` —
  `_maxsim(query_mat, doc_mat) -> float` core helper +
  `LateInteractionScorerStep` (`@step_registry.register("late_interaction_scorer")`),
  frozen+slots dataclass with `embedder: MultiVectorEmbedder` +
  `uow_factory: Callable[[], UnitOfWork]` + kw-only `publish_to: str | None
  = None` + kw-only `name: str = "late_interaction_scorer"`. The step body
  follows the Decision C code example exactly. Scratch hygiene: build a
  fresh `new_scratch = dict(state.scratch)` so the step is safe inside a
  `ParallelStep` branch (CLAUDE.md §"`RetrieverState.scratch` mutation
  discipline").
- Modify: `python/pydocs_mcp/retrieval/steps/__init__.py` — re-export
  `LateInteractionScorerStep`.
- Create: `tests/retrieval/steps/test_late_interaction_scorer.py`
- Create: `tests/retrieval/steps/test_late_interaction_scorer_maxsim.py`
- Create: `tests/retrieval/steps/test_late_interaction_scorer_strict_gate.py`
- Create: `tests/retrieval/steps/test_late_interaction_scorer_parallel_safety.py`

(`BuildContext.multi_vector_embedder` already landed in PR-4 — no edit
here.)

**Acceptance criteria:**

- AC-6 in full (`_maxsim` correctness against hand-computed L2-normalized
  small matrices; identical-matrix yields `nq`; orthogonal yields ~0).
- AC-7 in full (happy path).
- AC-8 in full (`publish_to` + scratch-fresh-dict).
- AC-9 in full (strict gate: `from_dict` raises when either
  `context.multi_vector_embedder` OR `context.uow_factory` is None,
  message pointing at the composition root).

**Test plan (TDD — failing tests first):**

- `tests/retrieval/steps/test_late_interaction_scorer_maxsim.py`:
  - `test_maxsim_identical_matrices_yields_nq` — `q == d`, `_maxsim(q, d) == n_query_tokens` (each query row's max dot to itself is 1.0).
  - `test_maxsim_orthogonal_matrices_yields_near_zero` — `q` and `d` are orthogonal rows; result `< 1e-5`.
  - `test_maxsim_handcrafted_small_inputs_match_reference` — `nq=2, nd=3, dim=4` with explicit numbers; compare to a numpy reference one-liner.
  - `test_maxsim_assumes_l2_normalized_inputs_documented` — source-scan assertion that the docstring names the precondition.
- `tests/retrieval/steps/test_late_interaction_scorer.py`:
  - `test_happy_path_rewrites_relevance_and_sorts` — three candidates with mocked matrices; assert relevance values and descending sort.
  - `test_retriever_name_stamped` — every scored chunk has `retriever_name == "late_interaction"`.
  - `test_chunks_without_stored_matrix_pass_through_unchanged` — one candidate whose id is not in `load_matrices` return; assert its relevance is unchanged.
  - `test_empty_candidates_returns_state_unchanged` — `state.candidates is None` or `items=()`; no embedder / UoW calls.
  - `test_round_trip_to_dict_from_dict` — `from_dict(step.to_dict(), context=BuildContext(multi_vector_embedder=fake, uow_factory=fake_factory))` reproduces the step.
- `tests/retrieval/steps/test_late_interaction_scorer_strict_gate.py`:
  - `test_from_dict_raises_when_multi_vector_embedder_missing` — `BuildContext(uow_factory=fake_factory)` (no `multi_vector_embedder`) raises `ValueError` mentioning the composition root path.
  - `test_from_dict_raises_when_uow_factory_missing` — `BuildContext(multi_vector_embedder=fake)` (no `uow_factory`) raises `ValueError`.
- `tests/retrieval/steps/test_late_interaction_scorer_parallel_safety.py`:
  - `test_does_not_mutate_input_state_scratch_in_place` — run the step with `publish_to="late.ranked"`; assert the input `state.scratch` reference is unchanged (`is` identity); the new state's `scratch` is a different dict.
  - `test_safe_inside_parallel_step_branch` — minimal `ParallelStep` smoke that runs two branches both containing the step, verify scratch isolation.

**Dependencies:** PR-1 (Protocol), PR-2 (real
`SqliteMultiVectorUnitOfWork` for end-to-end test fixtures), PR-4
(`BuildContext.multi_vector_embedder` field; preset YAMLs come in PR-7).

**Risks / open questions:**

- The Decision C code example uses `state.query.terms` for the embed
  input. Confirm `SearchQuery.terms` is the canonical text field (it is
  in the existing `dense_fetcher` / `dense_scorer`); if a refactor has
  moved it, update both the step and the example in lock-step.
- The `uow_factory()` call inside `run` opens a SQLite connection per
  step invocation. For a hot retrieval path this is fine — one query =
  one matrix-load — but if a benchmark sweeps many queries serially, the
  connection-open overhead may dominate. Not in scope for this PR;
  surface as a follow-up if benchmarks show it.

**Estimated size:** medium. ~120 LOC of production code + ~250 LOC of
tests across four test files.

---

## 7. PR-6 — composition root (uow factory + server/CLI wiring, gated so default install never imports torch)

**Scope (1-2 sentences):** Wire the multi-vector composite UoW factory
and the optional multi-vector embedder through `storage/factories.py`,
`retrieval/factories.py`, `server.py`, and `__main__.py`. The wiring is
**gated on the active config** so a default install (no `late_interaction:`
block, default `ingestion.yaml`) never imports PyLate, torch, or
sentence-transformers (AC-13).

**Spec refs:** Spec §5 "Write-path wiring (composition root only)" and
§5 "`BuildContext` extension", AC-13 (default-install lightness).

**Files touched:**

- Create: `python/pydocs_mcp/storage/factories.py` —
  `build_sqlite_plus_multi_vector_uow_factory(...)` (sibling of the
  existing `build_sqlite_plus_turboquant_uow_factory`). Wraps a
  `CompositeUnitOfWork(SqliteUnitOfWork(...), SqliteMultiVectorUnitOfWork(...))`.
- Modify: `python/pydocs_mcp/retrieval/factories.py` —
  `build_retrieval_context(db_path, config)` becomes config-aware: when
  the active pipeline references late-interaction (cheap YAML scan
  mirroring PR-4's `_ingestion_uses_multi_vector` for the retrieval
  YAML), it calls `build_multi_vector_embedder(config.late_interaction)`
  and threads the result into `BuildContext.multi_vector_embedder`.
  Default path: stays at `None`.
- Modify: `python/pydocs_mcp/server.py` — select between
  `build_sqlite_plus_turboquant_uow_factory` and
  `build_sqlite_plus_multi_vector_uow_factory` based on the same
  ingestion-YAML scan PR-4 added.
- Modify: `python/pydocs_mcp/__main__.py` — same selection logic on the
  index path; ensures `pydocs-mcp index . --config
  ingestion_late_interaction.yaml` routes vectors into the new store.
- Create: `tests/test_composition_root_late_interaction.py`
- Create: `tests/test_default_install_no_torch_import.py` (AC-13 guard).
- Modify: `tests/_fakes.py` — confirm `make_fake_uow_factory(vectors=...)`
  covers both store flavors; extend if needed.

**Acceptance criteria:**

- AC-13 in full: `python -c "import pydocs_mcp.server; assert 'torch' not
  in sys.modules and 'sentence_transformers' not in sys.modules and
  'pylate' not in sys.modules"` succeeds after building a default-config
  composition (no `late_interaction:` block, default `ingestion.yaml`).
- Strict-gate ACs for the retrieval step (AC-9) and the ingestion stage
  (AC-10 strict-gate clause) hold in a real composition root: the
  step / stage receive a non-None `multi_vector_embedder` when the
  YAML asks for it.

**Test plan (TDD — failing tests first):**

- `tests/test_default_install_no_torch_import.py`:
  - `test_default_serve_composition_does_not_import_torch` — build a default `BuildContext` + `uow_factory` via the production helpers; assert `"torch" not in sys.modules`, same for `"sentence_transformers"` and `"pylate"`.
  - `test_default_index_composition_does_not_import_torch` — same for the indexing-side helpers.
- `tests/test_composition_root_late_interaction.py`:
  - `test_late_interaction_preset_routes_to_multi_vector_uow` — when the active ingestion pipeline path is `ingestion_late_interaction.yaml`, the composite UoW is the multi-vector variant.
  - `test_late_interaction_preset_threads_multi_vector_embedder` — when the active retrieval pipeline references `late_interaction_scorer`, `BuildContext.multi_vector_embedder` is non-None.
  - `test_default_preset_threads_no_multi_vector_embedder` — default `chunk_search.yaml` ⇒ `BuildContext.multi_vector_embedder is None`.
  - `test_composition_root_invokes_build_multi_vector_embedder_lazily` — patch `build_multi_vector_embedder` and assert it's called only when the preset opts in.

**Dependencies:** PR-2, PR-3, PR-4, PR-5 (all four must land first — this
is the "wire everything together" PR).

**Risks / open questions:**

- The composition-root choice "is the active pipeline multi-vector?" is
  done via a YAML substring scan in PR-4. PR-6 reuses the same helper
  for both the ingestion path (already covered) and the retrieval path
  (new scan for `"late_interaction_scorer"`). Single source of truth:
  factor the helper into `retrieval/serialization.py` so both sides
  call it.
- `tests/test_default_install_no_torch_import.py` is the load-bearing
  AC-13 guard. If pytest somehow loads PyLate via a fixture imported
  earlier in the session, the assertion would false-fail. Mitigate via a
  subprocess invocation (`subprocess.run([sys.executable, "-c", "..."],
  ...)`) so the assertion runs in a clean Python.
- `build_retrieval_context` likely already has a `pipeline_path` argument
  it scans. Confirm the existing signature and reuse it; don't add a new
  config-flag argument (CLAUDE.md §"MCP API surface vs YAML configuration"
  applies even at the composition root — config is the single source).

**Estimated size:** medium. ~100 LOC of production wiring + ~200 LOC of
tests + the subprocess-isolated AC-13 guard.

---

## 8. PR-7 — presets, benchmark variant, docs

**Scope (1-2 sentences):** Ship the two end-to-end preset YAMLs
(`chunk_search_late_interaction.yaml` + a `_ranked` benchmark twin), the
`PydocsLateInteractionSystem` benchmark variant, the OPENAI_API_KEY-style
gated integration smoke test, and the docs updates (`CLAUDE.md`
retrieval-steps enumeration, commented `late_interaction:` block in
`default_config.yaml`, README jargon audit).

**Spec refs:** Decision G (opt-in preset, defaults untouched), Decision H
(fuse via shipped `rrf_fusion` / `weighted_score_interpolation`),
AC-12 (preset YAML round-trip), AC-13 (defaults byte-unchanged), AC-15
(benchmark variant), AC-16 (full suite green), AC-17 (authorship),
AC-18 (docs + jargon audit).

**Files touched:**

- Create:
  `python/pydocs_mcp/pipelines/chunk_search_late_interaction.yaml` — the
  retrieve-then-MaxSim-rerank-then-fuse shape sketched in Decision H.
  Three branches in a `ParallelStep`: BM25 recall (k=100), single-vector
  dense recall (k=100), late-interaction re-rank of the BM25 set (k=100,
  scored via `late_interaction_scorer`). Outer `rrf_fusion` fuses
  `[bm25.ranked, dense.ranked, late.ranked]`. Terminates with
  `limit(max_results=8)` and `token_budget_formatter` (production
  preset).
- Create:
  `python/pydocs_mcp/pipelines/chunk_search_late_interaction_ranked.yaml` —
  benchmark twin without the `token_budget_formatter` (mirrors the
  existing `chunk_search_hybrid_ranked.yaml`).
- Modify: `benchmarks/src/benchmarks/eval/systems/pydocs.py` — add
  `PydocsLateInteractionSystem` (mirrors `PydocsHybridSystem`); points
  at `chunk_search_late_interaction_ranked.yaml`. The benchmark CLI
  skips it cleanly when the `late-interaction` extra is absent (mirrors
  the existing `pydocs_dense` / `pydocs_hybrid` skip pattern).
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` — append a
  commented-out `late_interaction:` block (architecturally identical to
  the existing commented `llm:` block).
- Modify: `CLAUDE.md` — append `late_interaction_scorer` to the
  retrieval-steps enumeration; add a one-line note that
  `python/pydocs_mcp/storage/multi_vector_store.py` is the dedicated
  multi-vector backend; reference the `late-interaction` extra in the
  "Key Technical Details" section.
- Create: `tests/pipelines/test_late_interaction_preset_round_trip.py`
- Create: `tests/test_default_chunk_search_byte_unchanged.py` (AC-13
  byte-stability guard — uses a pinned SHA-256 of the shipped
  `chunk_search.yaml` + `ingestion.yaml` + relevant `embedding:` keys
  of `default_config.yaml`).
- Create: `benchmarks/tests/eval/test_late_interaction_system.py`
- Create: `tests/integration/test_late_interaction_end_to_end.py` —
  skip-unless the `late-interaction` extra is importable; uses a small
  fixture corpus, indexes with the multi-vector pipeline, runs a query
  through the late-interaction preset, asserts non-empty results +
  re-ranked relevance values.
- Create: `tests/test_docs_updated_for_late_interaction.py` (mirrors
  `tests/test_docs_updated_for_tree_reasoning.py`).

**Acceptance criteria:**

- AC-12 in full: all three new YAMLs round-trip via the existing
  factories (with `BuildContext.multi_vector_embedder=Fake…` + fake
  multi-vector UoW), execute against a seeded SQLite + `chunk_vectors`
  fixture, and produce non-empty output.
- AC-13 in full: default `chunk_search.yaml`, the default `embedding:`
  config keys, and `ingestion.yaml` are byte-unchanged (asserted via
  pinned SHA-256). `sys.modules` check still clean.
- AC-15 in full: `pydocs_late_interaction` is runnable via the benchmark
  CLI; produces mrr / recall@k comparable to `pydocs_hybrid`; RepoQA
  smoke test skips cleanly when the extra is absent.
- AC-16 in full: full suite green (`pytest -q` + benchmark tests +
  `ruff` + `cargo fmt --check && cargo clippy -- -D warnings && cargo
  test`).
- AC-17: every commit on the PR branch authored solely by Max; no
  `Co-authored-by:` trailers.
- AC-18: `CLAUDE.md` enumeration extended; `default_config.yaml` block
  in place; README jargon audit-grep returns zero matches.

**Test plan (TDD — failing tests first):**

- `tests/pipelines/test_late_interaction_preset_round_trip.py`:
  - `test_chunk_search_late_interaction_loads_and_executes` — load + execute against a seeded fixture (`FakeMultiVectorEmbedder` + in-memory `SqliteMultiVectorUnitOfWork`); assert non-empty `state.result`.
  - `test_chunk_search_late_interaction_ranked_loads_and_executes` — same against the ranked variant; asserts non-empty `state.candidates`.
  - `test_late_interaction_preset_uses_rrf_fusion` — load the preset, inspect the steps; assert the outer fuser is `rrf_fusion` (Decision H).
- `tests/test_default_chunk_search_byte_unchanged.py`:
  - `test_default_chunk_search_yaml_sha256_matches_pinned` — pinned hash; failing means an accidental edit.
  - `test_default_ingestion_yaml_sha256_matches_pinned`.
  - `test_default_embedding_keys_in_default_config_yaml_unchanged` — parse YAML, assert `embedding:` block matches the pinned snapshot.
- `benchmarks/tests/eval/test_late_interaction_system.py`:
  - `test_pydocs_late_interaction_system_registered` — discoverable by the benchmark CLI.
  - `test_pydocs_late_interaction_skips_when_extra_absent` — patch `sys.modules["pylate"] = None`; assert skipped cleanly.
- `tests/integration/test_late_interaction_end_to_end.py`:
  - `test_index_then_query_returns_reranked_results` — skip unless `import pylate` succeeds; tiny corpus, full ingest + retrieve cycle; assert top hit's relevance is a MaxSim score (positive float).
- `tests/test_docs_updated_for_late_interaction.py`:
  - `test_claude_md_enumerates_late_interaction_scorer`.
  - `test_default_config_has_commented_late_interaction_block`.
  - `test_claude_md_mentions_multi_vector_store_path`.
  - `test_readme_jargon_audit_returns_no_matches` — runs the audit-grep from CLAUDE.md §"README files" against the actual file tree; failing means a violation slipped in.

**Dependencies:** PR-2 through PR-6 (every prior PR; this is the
"everything works together" capstone).

**Risks / open questions:**

- The end-to-end integration test downloads the
  `lightonai/LateOn-Code` model on first run; CI must either (a) cache
  the HuggingFace model dir between runs or (b) skip the test when
  `HF_HUB_OFFLINE=1` or the cache dir is empty. The existing pattern for
  the OpenAI integration test is "skip unless env present" — adopt the
  same pattern here, gated on a new `PYDOCS_RUN_LATE_INTERACTION_INTEGRATION=1`
  env var so CI can decide.
- The benchmark variant must point at the shipped
  `chunk_search_late_interaction_ranked.yaml`. Confirm the benchmark
  CLI accepts a YAML preset name (it does for `pydocs_hybrid`); the same
  one-line addition suffices.
- The README jargon audit grep in CLAUDE.md is the gate. Run it locally
  before the final commit and again as a pytest assertion so a future
  README edit that smuggles in jargon fails fast.

**Estimated size:** medium-large. ~100 LOC of YAML + ~80 LOC of benchmark
wiring + ~250 LOC of tests + ~30 LOC of docs.

---

## 9. Cross-cutting concerns

### 9.1 Schema migration v5 → v6

The bump is **purely additive** (one new table, `chunk_vectors`); no
column ALTERs, no data migration. PR-2 takes the cheapest path: bump
`SCHEMA_VERSION` from 5 to 6, add `chunk_vectors` to `_KNOWN_TABLES`, and
let the existing wipe-and-recreate-on-mismatch in `db.py` handle the
transition. Users with a pre-existing v5 DB will see a one-time full
reindex on first open with the new build — same behavior the project has
shipped at every prior schema bump.

**Composition with `pipeline_hash` cache invalidation (Decision J + chunk-cache
work):** the v6 schema change does **not** require touching
`AppConfig.ingestion_pipeline_hash`. The cache invalidation that fires when
a user enables the multi-vector ingestion pipeline is driven by the
`_ingestion_uses_multi_vector(ingestion_path)` branch added in PR-4 —
which folds in `late_interaction.compute_pipeline_hash()`, which in turn
captures `model_name`, `dim`, `document_length`, `query_length`, and
`pool_factor` (all the knobs that change the stored matrix identity). A
user switching from the default ingestion pipeline to the multi-vector
one therefore gets a clean re-embed via the existing diff-merge in
`IndexingService.reindex_package` — no new "force re-embed" code path.

**Defense-in-depth:** the `chunk_vectors.model_name` column (Decision B
table sketch) guards against a stale matrix from a different model
slipping through if a user manually edits the cache. PR-2's `add_vectors`
stamps the embedder identity per row; `load_matrices` could assert it
matches the configured `late_interaction.model_name` (this is a small
PR-5 sanity check — surface as a follow-up if it costs a test).

### 9.2 Keeping the default install ~90 MB (`pylate` as optional extra)

**Three load-bearing guards** keep this promise:

1. **`pyproject.toml`** — `pylate` lives ONLY under
   `[project.optional-dependencies] late-interaction` (PR-3).
2. **Lazy import in the factory** — `build_multi_vector_embedder` defers
   the `from pylate import models` until its `if cfg.provider == "pylate":`
   branch fires; an actionable `ImportError` surfaces the install
   instruction (PR-3, AC-3 / AC-14).
3. **Gated composition root** — `build_retrieval_context` and the
   storage factory choice both depend on whether the active YAML
   pipeline references the multi-vector stage / step. A default install
   never enters the late-interaction branch (PR-6, AC-13).

**Verification belt-and-braces:** PR-6 ships
`tests/test_default_install_no_torch_import.py` which runs the
`sys.modules` assertion in a subprocess to guarantee no fixture-level
pre-import sneaks past. PR-7's byte-stability guard
(`test_default_chunk_search_byte_unchanged.py`) catches accidental
edits to the shipped defaults that would route a user into the
multi-vector path without their knowledge.

### 9.3 Composing with the shipped `rrf_fusion` / `weighted_score_interpolation` (no new fuser)

Decision H pins this: `LateInteractionScorerStep` publishes its ranking
to `state.scratch[publish_to]` via the same convention `TopKFilterStep`
already uses. The two shipped fusers (`rrf_fusion` rank-based,
`weighted_score_interpolation` min-max normalized) consume from the
same scratch keys with zero new code. The PR-7 preset YAML wires
`branch_keys: [bm25.ranked, dense.ranked, late.ranked]` to `rrf_fusion`;
power users wanting to weight the late branch higher swap `rrf_fusion`
for `weighted_score_interpolation` with `weights: [0.3, 0.2, 0.5]` —
zero code change required.

**Test:** PR-5's `test_does_not_mutate_input_state_scratch_in_place` is
the load-bearing assertion that protects the `ParallelStep` branch
safety — the same rule that closed the latent `TopKFilterStep` bug
(CLAUDE.md §"`RetrieverState.scratch` mutation discipline"). PR-7's
preset round-trip test exercises the full BM25 + dense + late RRF fuse
end-to-end.

### 9.4 Authorship + commit hygiene (AC-17)

Every commit on every PR branch is authored solely by
`Max Raphael Sobroza Marques <max.raphael@gmail.com>`; zero
`Co-authored-by:` trailers. Per CLAUDE.md `Bash` guidance: no
`--no-verify`, no `--no-gpg-sign`, no amending another author's commit.
Verify per-PR via:

```bash
git log main..HEAD --pretty=full | grep -i 'co-authored-by' && \
    echo "TRAILER FOUND" || echo "(clean)"
```

This mirrors AC-17 in every recent plan (`2026-05-26-llm-tree-reasoning…`,
`2026-05-24-chunk-cache…`, `2026-05-23-cleanups-and-pr-a`); a PR is
not mergeable until the audit returns "clean".

### 9.5 Single-source-of-truth defaults (CLAUDE.md §"Default values")

The following constants live in exactly one place across all seven PRs:

- `LateInteractionConfig.dim` default (`128`) — pydantic
  `Field(default=128, ge=1)`; PR-3's `PyLateEmbedder.dim` field default
  reads it via the config object passed in (never hardcoded).
- `LateInteractionConfig.document_length` (`180`),
  `query_length` (`32`), `pool_factor` (`1`), `candidate_limit` (`100`)
  — same rule.
- The preset YAMLs (`chunk_search_late_interaction.yaml`, etc.) repeat
  some numbers (e.g. `k: 100` for `top_k_filter`) for user-visible YAML
  clarity — the CLAUDE.md exemption for YAML files applies.
- A module-level `_DEFAULT_BATCH_SIZE` shared between `EmbedChunksStage`
  and `EmbedChunksMultiVectorStage` (PR-4) — if `EmbedChunksStage`'s
  constant is currently private, lift it to a sibling `_constants.py`
  in the same PR.

---

## 10. Open questions / spec gaps

Flag-only (per the user's instructions — these are decisions for the
implementer / user, NOT resolved by this plan):

1. **`lightonai/LateOn-Code` projection dim — TENTATIVE 128.** Spec Open
   Item #1 carries this forward; PR-1 must confirm by reading the model
   card's `1_Dense/config.json` `out_features` and updating the
   `Field(default=...)` if it differs. If the canonical value is NOT 128,
   every test using `dim=128` (PR-1 onward) needs updating — surface the
   spec drift in the PR-1 description.

2. **Defense-in-depth model-name guard at load time.** Spec Decision J
   risk row names `chunk_vectors.model_name` as the guard; AC-5 does NOT
   require `load_matrices` to assert the loaded `model_name` matches the
   configured `late_interaction.model_name`. PR-5 could add the
   assertion as a `_maxsim`-side sanity check. Flagged as a small
   optional belt-and-braces — implementer's call.

3. **`_ingestion_uses_multi_vector` substring-match false positives.** A
   YAML comment containing `"embed_chunks_multi_vector"` would trigger a
   false positive on the cheap scan (PR-4). Accept as v1 trade-off
   (over-invalidation, not corruption), or swap to a parsed-YAML walk
   if a user reports it. Spec doesn't pin the implementation; flagged
   for design review during PR-4.

4. **`do_query_expansion=True` padding to `query_length` with `[MASK]`.**
   Spec §9 risk row notes the behavior but explicitly defers to a future
   config knob (YAGNI). If a benchmark sweep finds query padding hurts
   recall on short queries, the knob lands as an `AppConfig.late_interaction.do_query_expansion`
   addition — additive, single-source-of-truth, no API change. Flagged so
   it's not forgotten.

5. **`device:` config knob for PyLate.** Spec §6 lists out-of-scope.
   Sentence-transformers picks the device automatically; a benchmark
   showing torch-CPU runs too slow on the CI runner would motivate
   `LateInteractionConfig.device: Literal["auto", "cpu", "cuda"] = "auto"`.
   Flagged as the obvious follow-up.

6. **Connection-open overhead in the hot retrieval path.** Each
   `LateInteractionScorerStep.run()` opens a fresh SQLite connection via
   `uow_factory()`. For long benchmark sweeps this may dominate. Not in
   scope this PR series; flagged for a future plan if benchmarks show
   it. Could be mitigated by a per-context shared connection or a
   pre-fetched matrix bundle.

7. **Multi-vector support for `member_search`.** Spec §6 lists this as
   out-of-scope. If a user needs it, the same step machinery applies to
   the `member_search` pipeline; the lift is a sibling
   `_member_fetcher`-flavored variant. Flagged for visibility.

8. **Single-row `chunks` row vs sidecar `chunk_vectors` row coupling.**
   PR-2 ships `chunk_vectors.chunk_id INTEGER PRIMARY KEY` (one matrix
   per chunk). Spec doesn't pin a FK to `chunks.id` — adding it would
   ON-DELETE-CASCADE the matrix when the chunk is removed via the
   diff-merge. Currently `IndexingService.remove_package` deletes chunks
   then deletes from the vector store separately (per the `uow.vectors`
   surface). Adding a FK would simplify the delete path but introduce a
   schema-tighter coupling; the spec doesn't pin a preference. Flagged
   for implementer's call during PR-2.

---

## 11. Handoff

After PR-1 lands:

1. Re-plan PR-2 via `superpowers:writing-plans` into a per-task list
   following the same shape every recent single-PR plan ships
   (`2026-05-27-serve-watch-flag.md`,
   `2026-05-26-llm-tree-reasoning-and-weighted-fusion.md`,
   `2026-05-24-chunk-cache-vector-cleanup.md` are the closest templates).
2. Execute via `superpowers:subagent-driven-development` — fresh subagent
   per task; per-task `/code-review` + `/review`; final code-reviewer
   subagent over the full PR diff before merge.
3. Repeat for PR-3 through PR-7. PR-2 and PR-3 can run in parallel after
   PR-1 lands (different worktrees or sequential is the implementer's
   call); the rest is strictly serial.
4. After PR-7 merges: update `EXTENSIONS.md` (if such a tracker still
   exists at that point) to mark the late-interaction work shipped,
   mirroring the `[SHIPPED]` annotation the LLM-tree-reasoning PR
   landed.

The spec's "Suggested commit shape" §12 lines up exactly with the seven
PRs above — one commit family per PR. The plan trades the spec's
suggested shape for the multi-PR structure because (a) the work is too
big for one squashed PR (estimated ~3000 LOC across production +
tests + YAML), (b) the inside-out ordering wants explicit review gates
at the contract / adapter / step / wiring / preset boundaries, and (c)
PR-1 + PR-2 + PR-3 want independent reviewability so storage and
embedder concerns don't bleed across each other in a single review.
