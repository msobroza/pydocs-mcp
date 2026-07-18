# ADR 0005 — Description source: one delimited text document, productizing the shipped section grammar

**Status:** Accepted · **Date:** 2026-07-18 · **Phase:** 1

- **Decision area:** D1 of the Phase 1 owner spec ("externalized optimizable surface &
  deterministic harness behavior") — source format and section schema of the optimizable
  description document.
- **Siblings:** [0006-description-injection-and-precedence.md](0006-description-injection-and-precedence.md) (D2),
  [0007-deterministic-routing-suggestions.md](0007-deterministic-routing-suggestions.md) (D3),
  [0008-turn0-context-injection.md](0008-turn0-context-injection.md) (D4).

## Context

The Phase 0 freeze (`docs/tool-contracts.md` §1) fixed nine tool names and their parameter
schemas but deliberately left the tool **descriptions** mutable: "Names frozen, descriptions
optimizable — that split is the point of this freeze" (docs/tool-contracts.md:32-40). Phase 1
turns that mutable text into a first-class artifact: a single source document that a text-space
optimizer can read, mutate, and write back, and that the product itself loads.

Today the optimizable text lives as Python literals: `TOOL_DOCS: dict[str, str]` keyed by the
nine tool names (application/tool_docs.py:37-137) and `SERVER_INSTRUCTIONS`
(tool_docs.py:139-150). Registration passes `description=TOOL_DOCS[name]` explicitly
(server.py:642-650); handler docstrings are deliberately unused. The spec asks what file
format holds this text once externalized — markdown headings, markdown + front-matter,
structured YAML/TOML, a per-section directory, or a reuse of the owner's coding-agent-playbook
format — under three constraints: round-trip parse/render losslessness (optimizers write the
file back), section IDs stable and derived from the frozen tool names so a rename can never
silently orphan a section, and plain-text-editor friendliness.

## Evidence

**Inventory of the text being externalized**
(`docs/superpowers/research/2026-07-18-phase1-evidence-d1a-descriptions-inventory.md`;
measured by script against this worktree at HEAD f4a8f2e):

- `TOOL_DOCS` totals 9,307 chars ≈ 2,326 tokens against the 3,600-token lint budget; per-tool
  sizes range 891 (get_why) to 1,360 (grep). `SERVER_INSTRUCTIONS` is 937 chars, consumed
  both as FastMCP `instructions=` (server.py:551) and as the CLI top-level argparse
  description (__main__.py:66).
- Two shared fragments are interpolated into every entry: `_WORKFLOW` (123 chars,
  tool_docs.py:25-28) and `_CONTRACT` (293 chars, tool_docs.py:29-35).
- **Zero parameter descriptions exist on the MCP surface.** An empirical FastMCP registration
  + `list_tools()` dump found no `description` key on any of the 77 advertised parameters
  across the nine tools; `Field(...)` in application/mcp_inputs.py carries only constraints
  (`min_length`, `ge`, `validation_alias`), never `description=`. Parameter guidance lives
  only inside `TOOL_DOCS` prose and CLI-only argparse `help=` strings. (The dump ran under a
  non-pinned pydantic 2.12.5; the zero-descriptions result follows from source and is
  version-independent, but the pinned-version schema bytes were not reproduced.)

**Prior art already in the repo**
(`docs/superpowers/research/2026-07-18-phase1-evidence-d1a-descriptions-inventory.md`,
`docs/superpowers/research/2026-07-18-phase1-evidence-d1b-optimizer-interfaces.md`):
the benchmarks side ships a working delimited
grammar at `benchmarks/src/pydocs_eval/optimize/artifacts/_delimited.py` —
`render_delimited(dict) -> str` / `parse_delimited(str) -> dict` with a **closed** header
regex (`^=== (SERVER_INSTRUCTIONS|SYSTEM_PROMPT|REWRITE_PROMPT|TOOL: [a-z_]+) ===$`,
_delimited.py:31-33) and `find_header_collisions` (:77-95): a content line that looks like a
section header is promoted to a section and **rejected as a collision** rather than escaped.
Round-trip is idempotent after one normalization pass, so fingerprints hash the normalized
surface (_delimited.py:11-18). `ToolDocsArtifact.validate()` already re-runs the product's
tool-docs lint constants — five `REQUIRED_MARKERS`, `CHARS_PER_TOKEN=4`,
`PER_TOOL_TOKEN_BUDGET=500`, `TOTAL_TOKEN_BUDGET=3600` — imported directly from
application/tool_docs.py:14-23.

**Optimizer candidate shapes**
(`docs/superpowers/research/2026-07-18-phase1-evidence-d1b-optimizer-interfaces.md`):
SkillOpt 0.2.0 (installed, interface read from
site-packages) mutates **one flat document string** — edit ops
`{append, insert_after, replace, delete}` with substring targets against `skill_content`
(skillopt `optimizer/skill.py:148-201`), winner emitted as one `best_skill.md`; its
protected-region markers are hardcoded and cannot freeze arbitrary sections in 0.2.0
(`skill.py:14-30`). GEPA natively represents a candidate as `Candidate = dict[str, str]`
(component name → text) with per-component selective updates — **web-derived** from the
gepa-ai/gepa main-branch source (`src/gepa/core/adapter.py`, fetched 2026-07-18), not
verified against a pinned release, and labeled as such here.

**coding-agent-playbook probe**
(`docs/superpowers/research/2026-07-18-phase1-evidence-d1c-playbook-probe.md`):
the owner's playbook engine is accessible (local clone
at ~/Projects/coding-agent-playbook, private GitHub repo), Apache-2.0, version 0.1.0a0, 93
Python files. Its source format is markdown + YAML front-matter (`id/name/version/...`)
rendered deterministically with an AUTOGENERATED sentinel and a `playbook sync --check` drift
gate. None of its six built-in formats target MCP tool descriptions, CLI help, or server
instructions; all emit harness config files.

## Options considered

**(a) One markdown file with conventional `##` headings, parsed by heading path.** The spec's
prior; simple and diff-friendly. Its concrete failure mode: heading parsing cannot fail
loudly when a *body* line happens to look like a heading — an optimizer edit emitting
`## TOOL: grep` inside another section silently splits that section instead of raising.
Rejected in its literal-markdown form on exactly that silent-split failure mode, which the
in-repo delimited grammar catches loudly.

**(b) Markdown + YAML front-matter per section.** Buried: section IDs are fully determined by
the nine frozen tool names, so front-matter `id:` fields would be redundant state that can
drift from the header they annotate — a second parsing layer that buys no validation the
closed header set does not already provide.

**(c) Structured config (YAML/TOML) with multiline text blocks.** Multiline block scalars
make round-trip losslessness fragile — indentation and chomping indicators change content
bytes, and a SkillOpt substring edit inside a block scalar can silently alter semantics with
no loud failure. Buried: the spec's own bar ("structured formats only on a concrete
validation failure mode heading-parsing can't catch") is not met — the delimited grammar
catches that failure mode without structure.

**(d) Directory of per-section files.** Buried: SkillOpt 0.2.0 verifiably mutates ONE string
(`skill_content` in, `best_skill.md` out — see
`docs/superpowers/research/2026-07-18-phase1-evidence-d1b-optimizer-interfaces.md`),
so a per-section directory would need a
join/split layer anyway and multiplies the packaged-data surface by eleven.

**(e) Reuse/extend coding-agent-playbook.** Its conventions (stable section IDs, deterministic
renderer, drift gate) are sound and are borrowed below. Vendoring the engine is buried: a
93-file 0.1.0a0 private-repo dependency with zero MCP-description adapters, for a job needing
roughly two hundred product lines.

## Decision

**A single delimited text document — option (a) in spirit, but using the repo's proven
`=== SECTION ===` grammar instead of markdown headings, with coding-agent-playbook as a
conventions reference only.**

1. **File:** `python/pydocs_mcp/defaults/descriptions.md`, shipped as packaged data and
   loaded via `importlib.resources`.
2. **Sections (eleven, all required):** `=== SERVER_INSTRUCTIONS ===`; `=== TOOL: <name> ===`
   × 9, where `<name>` must be one of the nine frozen names from docs/tool-contracts.md §1;
   and `=== SESSION_START_PREAMBLE ===` — the optimizable framing text for the session-start
   context feature (sibling D4 ADR). SESSION_START_PREAMBLE is always required in the
   document but rendered only when the session-start-context flag is on. Unknown section, missing section, or unknown tool name is a **hard
   `ValidationError`** — section IDs derive from the frozen names, so a tool rename can never
   silently orphan a section.
3. **Grammar moves into the product:** the render/parse/collision implementation relocates
   from `benchmarks/src/pydocs_eval/optimize/artifacts/_delimited.py` into
   `python/pydocs_mcp/application/description_source.py`, parameterized by an
   allowed-header set. The benchmarks module becomes a delegating import — it already imports
   the product lint constants, so no dependency inversion is introduced.
4. **Why delimiters beat headings:** header-collision detection *rejects* body lines that
   look like section headers (`find_header_collisions`; escaping-free by construction)
   instead of silently splitting a section — the loud-failure property markdown heading
   parsing cannot provide. Diff legibility is identical (one plain-text file); round-trip
   losslessness is already proven and tested (idempotent after one normalization pass,
   fingerprints hashing the normalized surface).
5. **Dual views for the two optimizer families:** `parse` → `dict[section_id → text]` maps
   bijectively onto GEPA's native `Candidate = dict[str, str]` (section IDs = component
   names; web-derived interface, flagged above); `render` → one document is SkillOpt's
   single-genome state, with section headers doubling as stable substring anchors for its
   edits and parse-back + `validate()` firewalling boundary-mangling edits — the pattern the
   shipped skillopt adapter already uses (`optimizers/skillopt.py`:
   `with_content(best_path.read_text())` then reject-on-violation).
6. **Section bodies:** free prose, but each TOOL section must contain the five
   `REQUIRED_MARKERS` ("When to use", "When NOT to use", "Workflow", "Response
   contract", "Examples" — colon-free substring markers) and respect the
   500-token-per-tool / 3,600-token-total budgets —
   all constants importable from application/tool_docs.py:14-23. This satisfies the
   seed-content requirement without inventing a subsection grammar.
7. **Fragments:** the source stores fully expanded text per section. The `_WORKFLOW` /
   `_CONTRACT` f-string interpolation happens once at seed generation; no template engine in
   the document (the spec's over-build warning).
8. **Justified exceptions — strings that stay OUT of the document** (the externalize-or-justify
   audit list): CLI-only argparse `help=` strings (~40 strings, operator-facing CLI UX, never
   optimizer-mutated; the ~4,500-char aggregate is an unmeasured eyeball estimate); envelope
   rendering strings (freshness header, stale warning, truncation footer, pointer templates —
   deterministic output rendering under the sibling D3 ADR, not description text); error
   messages (`ServiceUnavailableError` texts in null_services.py, file-tools errors,
   input-validator messages); the ask-your-docs and retrieval Jinja prompts (already
   externalized in their own versioned template system with the shipped
   `AskPrompts`/`build_agent(..., prompts=)` seam — double-sourcing would violate
   single-source); and the `[[next:...]]` pointer token grammar (fixed machinery, not text).
9. **Parameter descriptions: deliberately NOT created.** Zero parameter descriptions exist
   anywhere on the MCP surface (empirical, above), so the externalization requirement is
   satisfied vacuously — there is no hidden string to move. Creating ~40 new optimizable
   dimensions would expand the later optimization phase's search space with no evidence of
   value and would need a new stamping mechanism (`Field(description=...)` on the input
   models). Parameter guidance stays inside tool prose. Revisitable when evidence demands.
10. **coding-agent-playbook posture:** conventions borrowed (stable section IDs, deterministic
    renderer, drift-gate CI check); vendoring rejected as recorded above.

## Consequences

**Benefits.** Silent-section-split edits become loud validation failures. The dict/document
dual view serves both optimizer families with zero conversion beyond the shipped render/parse
pair. Seed v0 is the live `TOOL_DOCS`/`SERVER_INSTRUCTIONS` verbatim, so byte-identical
default behavior is trivially provable; the frozen-name-derived section set makes contract
drift structurally impossible.

**Costs and risks.**

- **Closed grammar = deliberate friction.** New section kinds require widening one regex plus
  each artifact's allowed set — single-source, but the regex is shared, so a widening touches
  the benchmarks artifacts too.
- **SkillOpt cannot freeze sections pre-hoc.** Its protected regions are hardcoded marker
  pairs (0.2.0), so any freeze policy is enforced only post-hoc by validate-and-reject —
  rollout budget is spent generating candidates that get discarded.
- **The GEPA mapping is unverified against a release.** The `dict[str, str]` candidate shape
  is web-derived from main-branch source; a pinned release could differ.
- **A `.md` file that is not markdown.** `=== ... ===` headers render as plain paragraphs in
  markdown viewers; the extension is kept for editor ergonomics, not fidelity.
- **Normalization is a load-bearing invariant.** Render→parse is not byte-stable on first
  pass; every fingerprint consumer must hash the normalized surface — easy to break silently
  and therefore pinned by test.
- **SESSION_START_PREAMBLE is mandatory for an off-by-default feature.** Every override document
  carries a section whose rendering is disabled by default — an authoring tax accepted so the
  section set stays fixed and validation unconditional.
- **Transition-window dual source.** Until the sibling injection ADR (D2) populates the
  `tool_docs` module attributes from the document, the document and the Python literals
  coexist; byte-parity of the packaged seed against the live constants must be CI-pinned.

## Action items

All in this phase unless noted.

1. Create `python/pydocs_mcp/application/description_source.py`: move
   `render_delimited`/`parse_delimited`/`find_header_collisions` from
   `benchmarks/src/pydocs_eval/optimize/artifacts/_delimited.py`, parameterized by an
   allowed-header set; document the header-widening protocol in its module docstring; leave
   the benchmarks module as a delegating import.
2. Create `python/pydocs_mcp/defaults/descriptions.md`: seed v0 = current live
   `TOOL_DOCS` + `SERVER_INSTRUCTIONS` verbatim (fragments expanded once) + the new
   `SESSION_START_PREAMBLE`; register as packaged data alongside
   `python/pydocs_mcp/defaults/default_config.yaml`.
3. Implement the validator in `description_source.py`: section-set equality against the nine
   frozen names of docs/tool-contracts.md §1 plus `SERVER_INSTRUCTIONS` and
   `SESSION_START_PREAMBLE`; per-TOOL-section `REQUIRED_MARKERS` and token budgets via the constants
   in application/tool_docs.py:14-23; hard `ValidationError` on any violation.
4. CI golden tests: (i) packaged `descriptions.md` parses and validates; (ii)
   parse(render(x)) idempotence after one normalization pass, and fingerprints hash the
   normalized surface; (iii) byte-parity of the seed sections against the live
   `TOOL_DOCS`/`SERVER_INSTRUCTIONS` for the transition window.
5. Loading the document into the `tool_docs` module attributes at import time, plus the
   override flag — owned by ADR 0006 (D2), not this one.
6. Parameter-description creation: deferred with no owning phase; revisit only if the later
   optimization phase produces evidence that per-parameter text is a worthwhile search
   dimension.
