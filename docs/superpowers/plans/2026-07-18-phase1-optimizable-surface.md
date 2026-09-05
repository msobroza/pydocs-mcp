# Phase 1 implementation plan — externalized optimizable surface & deterministic harness behavior

**Date:** 2026-07-18 · **Branch:** `claude/phase-1-optimizable-surface` (base f4a8f2e)
**Decisions:** ADRs 0005–0008 (`docs/adr/`). This plan turns them into code. TDD per task:
failing test first, smallest change to green, then refactor. Full CI gate set at the end.

## Ground rules

- The nine tool names, parameter schemas, and envelope stay frozen (`docs/tool-contracts.md`).
  The ONLY contract-adjacent change is the additive optional `meta.suggestion` field
  (ADR 0007), which follows the `meta.resolution` precedent and is flagged for owner
  review in the PR description.
- Seed content v0 = current live `TOOL_DOCS` / `SERVER_INSTRUCTIONS` text **verbatim**
  (byte-identical default behavior proves R6), plus the new `TURN0_PREAMBLE` section.
- No optimizer-library imports anywhere in the product (R8). No trace/metric/ablation
  work (Phase 2/3 scope).

## Task 1 — Product grammar module

`python/pydocs_mcp/application/description_source.py` (new):

- `parse_sections(text, *, allowed) -> dict[str, str]`, `render_sections(sections) -> str`,
  `find_header_collisions(sections, *, allowed)`, `normalize(text)` — the
  `=== SECTION ===` delimited grammar, same semantics as
  `benchmarks/src/pydocs_eval/optimize/artifacts/_delimited.py` (closed header set,
  collision firewall instead of escaping, idempotent after one normalization pass),
  parameterized by allowed-header set.
- Canonical header set for the product document:
  `SERVER_INSTRUCTIONS`, `TOOL: <name>` for the frozen nine, `TURN0_PREAMBLE`.
- Move the lint constants here (`REQUIRED_MARKERS`, `CHARS_PER_TOKEN`,
  `PER_TOOL_TOKEN_BUDGET`, `TOTAL_TOKEN_BUDGET`) with back-compat re-exports from
  `application/tool_docs.py` (benchmarks imports them from there).
- `validate_sections(sections)` — R5 drift check: exactly the required headers, no
  unknown tool names, required markers per tool section, token budgets.
- The module docstring carries the one-normalization-pass rule: render→parse is
  byte-stable only after one normalization pass, and every fingerprint consumer
  hashes the normalized surface.
- Typed exceptions carrying offending value + expectation.

Tests (`tests/application/test_description_source.py`): round-trip losslessness
(parse→render→parse identity), normalization idempotence, collision rejection,
unknown-header / missing-section / renamed-tool failures, determinism (two passes
byte-identical).

## Task 2 — Packaged source document + loader + hash

- `python/pydocs_mcp/defaults/descriptions.md` (new packaged data): seed v0 as above.
- `application/tool_docs.py` rewired: literals removed; `TOOL_DOCS`,
  `SERVER_INSTRUCTIONS`, `TURN0_PREAMBLE` populated at import by parsing the packaged
  document via `importlib.resources`. Public API (in `description_source.py`):
  - `load_packaged() -> dict[str, str]`
  - `apply_source(path: Path) -> str` — read, parse, **validate (hard error)**, rebind
    the `tool_docs` module attributes; returns the new artifact hash.
  - `current_artifact_hash() -> str` — sha256 over `normalize(render_sections(live
    attributes))` + `RENDERER_VERSION = 1`. Truthful under legacy attribute rebinding.
- Migration parity test (one-time, in the PR): rendered `TOOL_DOCS` == previous
  literals (golden captured from git HEAD before the rewire). Long-term tests:
  golden-file snapshot of the rendered MCP schema (names + descriptions + inputSchema
  + outputSchema from a `list_tools()` dump) for the seed; hash changes iff source or
  renderer version changes; packaged-file validation failure raises at import.

## Task 3 — Override plumbing (ServeConfig + CLI + entry points)

- `ServeConfig.descriptions_path: str | None = None`
  (`retrieval/config/models.py`; YAML `serve.descriptions_path`; env
  `PYDOCS_SERVE__DESCRIPTIONS_PATH`).
- `pydocs-mcp serve --descriptions <path>` flag → threaded to `server.run`, applied via
  `apply_source` BEFORE `FastMCP(...)` construction / tool registration. Precedence:
  CLI flag > env > user YAML > packaged default. Any explicitly-supplied source that is
  missing or invalid = hard startup error (universal strictness — no fallback).
- `__main__.main()`: apply env-var override (if set) before `_build_parser()` so CLI
  help renders the same bundle (parity, R2).
- Startup log line: `descriptions artifact <hash12> source=packaged|<path>` (JSON
  fields per house logging style).

Tests: precedence chain, strict-mode hard errors (missing path, drift-failing doc),
MCP/CLI parity (same source → identical description strings on both surfaces),
startup log contains the hash.

## Task 4 — Deterministic suggestions (ADR 0007)

- Config: `output.suggestions.{grep_zero_hit,grep_truncated,search_zero_hit}`
  (bool, all default true) in `defaults/default_config.yaml` + config models.
- grep: 0-hit → suggestion line appended to body + `extras["suggestion"]`; truncated →
  narrowing hint likewise (`application/file_tools.py`).
- search_codebase: existing zero-hit `[[next:overview:]]` append
  (`tool_router.py:101-113`) moves behind its flag; the SAME `search_zero_hit` flag
  also gates the get_why zero-hit pointer append (`"No decisions found."` +
  `[[next:overview:]]`, `decision_service.py:219`); `meta.suggestion` populated with
  the resolved suggestion text.
- `tool_response.py`: `SuggestionMetaModel` (adds `suggestion: str | None = None`) for
  grep + search_codebase + get_why envelopes (all three suggestion-emitting tools),
  mirroring `ReferencesMetaModel` — the pydantic model change
  is mandatory (undeclared extras are dropped at validation).
- Fixed suggestion text constants with the deterministic `[suggestion: …]` prefix;
  fired rules emit one structured log line each (tool, rule) for Phase 2 attribution.
- Contract doc: §2.3 addendum documenting `meta.suggestion` as an additive optional
  extension + migration-note row (explicitly marked for owner ratification).
- Pin tests updated: `tests/test_structured_envelope.py` (`_META_FIELDS`, shape tests,
  registry test), plus new per-flag on/off behavior tests (off ⇒ byte-identical to
  today's output).

## Task 5 — Turn-0 context pack (ADR 0008)

- Config: `serve.turn0_context.{enabled: false, budget_tokens: 2000}`.
- `application/turn0_context.py` (new): `build_turn0_context(...)` = fixed
  `INJECTED_CONTEXT_MARKER` line + `TURN0_PREAMBLE` (from the source document) +
  overview card (reuse `OverviewService`/card renderer — same snapshot as the tools) +
  version inventory (`uow.packages.list` → `name version` lines). Budget enforced with
  `retrieval/llm_clients/model_budget.count_tokens`; card trimmed before inventory;
  truncation noted in the pack.
- ask-your-docs: inject at the single prompt-assembly site (`agent.py:160-173`) when
  enabled.
- CLI: `turn0-context` subcommand printing the pack (corpus selectors only; no tuning
  flags — budget lives in YAML).
- Tests: flag off ⇒ zero behavior change (byte-identical prompt assembly); on ⇒ marker
  first line + sections present; deterministic under fixed inputs; budget respected.

## Task 6 — Benchmarks reconciliation

- `benchmarks/.../artifacts/_delimited.py` delegates to the product grammar module
  (behavior-preserving; benchmarks tests stay green).
- `benchmarks/.../optimize/_overlay_server.py` migrates to `apply_source()` (keeps
  fail-closed semantics, fixes its stale server.py line-number docstring, makes the
  artifact hash truthful for overlay runs).
- `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q` green.

## Task 7 — Docs

- `docs/description-authoring.md`: grammar, section conventions, required markers,
  budgets, how to add a tool section when a future phase adds a tool, hash semantics,
  override flags.
- README + config reference: new keys (flag registry table from the ADRs).
- CHANGELOG (Unreleased): externalized description source, suggestion flags,
  turn0-context, `meta.suggestion`.

## Task 8 — Full gate + audit

- `ruff format --check` / `ruff check` / `mypy python/pydocs_mcp` / `complexipy ≤15` /
  `vulture ≥80` / `pytest tests/ --cov ≥90` / benchmarks suite / `uv lock --check`
  (no dep changes expected).
- R1 audit grep: no optimizable string hard-coded outside the source document except
  the ADR 0005 exception list.
- Acceptance checklist from the Phase 1 spec walked item by item (recorded in the PR
  description).

## Ordering

T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8. T4 and T5 are independent of each other but
both touch `default_config.yaml`/config models — run sequentially to avoid conflicts.
