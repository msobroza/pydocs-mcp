# ADR 0011 — Evidence attribution: surfaced → inspected → used tiers with first-touch credit

**Status:** Accepted · **Date:** 2026-07-18 (validated 2026-07-21) · **Phase:** 2

- **Decision area:** D3 of the Phase 2 owner spec ("evidence attribution and
  gold-diff parsing")
- **Siblings:** ADR 0009 (capture), ADR 0010 (trace schema — the
  `events.jsonl` this attributor consumes), ADR 0012 (score/taxonomy/feedback
  — the components these tiers feed). Phase 0/1 background: the frozen
  nine-tool contract (`docs/tool-contracts.md`), the `meta.suggestion` +
  fired-rule markers (ADR 0007), the session-start pack (ADR 0008).

## Context

Phase 2's metrics must answer, per rollout: which files did the model *find*,
which did it *read*, and which did it actually *edit* — and which tool deserves
credit for the find. The raw material is the merged trace of ADR 0010 (tool
events with per-item path/span/qname identifiers plus loop-side Read events)
and the final patch the runner captures as `git diff`; the gold standard is the
SWE-bench-Live instance's `patch` field. Three things make attribution
non-trivial: the nine tools return content at wildly different fidelities
(verbatim file windows down to pure path lists), their path conventions differ
per tool family, and the gold patches carry every unified-diff edge case a
four-year dataset accumulates. R6 demands deterministic attribution; R7
demands harness machinery never count as model-retrieved evidence. The spec
put three shapes on the table: a single binary "used-in-answer" bit, a tier
ladder with first-touch credit, or weighted multi-tool credit.

## Evidence

Per-tool claims are verified in
`docs/superpowers/research/2026-07-18-phase2-evidence-result-shapes.md`
(static reads + 14 live envelope captures against a fixture index); dataset
claims in `…-swebench-formats.md` (all 1888 SWE-bench-Live `full` rows
downloaded and measured); marker claims in `…-phase01-outputs.md`
(runtime-probed). All files in the same research directory.

**Content classification is a per-tool/mode fact, not a guess.** Verbatim (or
near-verbatim) file bytes reach the model from: `read_file` (`cat -n` window,
`application/file_tools.py:334-353`), `grep` content mode (matched lines +
context, `file_tools.py:271-289`), `get_symbol depth=source` (≤400-line fenced
source, `application/symbol_source.py:107-114`), `get_context` (skeleton render
with full bodies for central closure nodes, `defaults/default_config.yaml:101-104`),
and `search_codebase` chunk rows (full chunk text in the body,
`application/formatting.py:283-293`). Pure hit lists: `glob` (path+mtime only,
`file_tools.py:308-317`) and `get_references` (qname edge lists, no file
content). Outline/derived: `get_overview` (one doc line per module),
`get_symbol depth=summary|tree` (PageIndex JSON with **no** `text` field,
`extraction/model/document_node.py:88-95`), `get_why`/decision rows (mined
rationale, not file bytes). One leak crosses the boundary: grep's
`files_with_matches`/`count` modes render a paths-only text body while each
`items[]` row keeps the first-match span *and matched-line text* "so clients
can jump straight in" (`file_tools.py:292-301`; observed live: item text
`"class MiniUnitOfWork:"` against a path-only body) — items alone over-count
content exposure, text alone under-counts what a structured-reading client
saw. A second gap runs the opposite direction: `search_codebase` items[] are
read from `SearchResponse.candidates` — the ranked rows *before* the
2000-token composite budget collapses the text body
(`multi_project_search.py:61-63`) — so items can enumerate MORE rows than the
text ever rendered (result-shapes §2.2, §7). ADR 0010 encodes this as a
schema semantic: `result_ids` presence MUST NOT be read as "shown to the
model"; model-visible surfacing is a text-side judgment.

**Line fidelity varies by row kind.** Chunk rows carry real 1-indexed spans of
the originating DocumentNode (schema-v15 keys, `python/pydocs_mcp/models.py:152-157`;
verified live: `start_line: 22, end_line: 25` matches the fixture file). Member
rows are best-effort tree-node lookups degrading to null path/span on any miss
(`application/multi_project_search.py:246-292`). Decision rows are null-span
**by contract**; locators live in `get_why` as strings needing parsing
(`application/decision_service.py:357-373`). `get_references` spans are the
attributed endpoint's *defining node*, never the call site — "per-call-site
line numbers are not stored in the graph" (`tool-contracts.md:252-254`,
`application/lookup_service.py:523-529`). `grep`/`read_file` spans are exact
*live-disk* lines while indexed spans are as-of-last-index (result-shapes §4).
A further live-only caveat: `get_symbol depth=source` on a class returns only
the class-header chunk text while its item span covers the whole class —
rendered coverage ≠ span coverage (result-shapes §6.3).

**Path conventions — three, verified.** Index-backed tools emit
index-root-relative paths (`extraction/strategies/chunkers/_shared.py:115-130`
`_relpath`); the filesystem tools emit project-root-relative POSIX for project
files and ABSOLUTE paths for dependency files (`file_tools.py:12-14` module
docstring); the loop's client-side Read tool uses absolute paths.

**Gold-file semantics.** The harness's own gold prediction is literally the
instance `patch` column (`swebench/harness/utils.py:41-52`, swebench 4.1.0;
same in SWE-bench-Live `evaluation/evaluation.py:313`), and the eval script
resets `test_patch`-modified files to base before re-applying `test_patch`
(`swebench/harness/test_spec/python.py:406-416`) — so `patch` and `test_patch`
file sets are disjoint by construction. Measured exhaustively: **0 of 1888**
full-split instances have any overlap between the two file sets.

**Gold-patch edge cases, measured over all 1888 instances** (gold `patch`
field): multi-hunk file sections 70.1%, multi-file patches 59.9%, new files
(`/dev/null` sources) 21.1%, `\ No newline at end of file` 2.0%, deletions
1.3%, renames 0.3% (including hunkless `similarity index 100%` renames),
unquoted paths containing spaces 0.3%, one binary (`Binary files … differ`,
no `GIT binary patch` blobs anywhere), one symlink (`new file mode 120000`).
Patch sizes reach 2,114,789 chars; P2P lists reach 23,953 entries.
`difficulty.files` equals the `diff --git` section count for all 1888 rows —
a free parser cross-check.

**Dataset traps.** 644 of 11152 F2P entries (5.8%, across 148 instances) are
parametrized pytest ids truncated at the first space by the harness's own log
parser (`line.split()` keeping token [1],
`swebench/harness/log_parsers/python.py:7-26`) — e.g.
`…::test_validate[Invalid` with an unclosed bracket. `full` also contains a
real duplicate: `conan-io__conan-18153` ×2 (1888 rows, 1887 distinct ids).

**R7 markers that exist.** The injected session-start pack always begins with
the exact line
`[pydocs-mcp session-start-context: harness-injected at session start; not model-retrieved]`
(`application/session_start_context.py:36-38,80-81`; byte-verified at
runtime), and each fired suggestion emits
`{"event": "suggestion_fired", "tool", "rule"}` on logger
`pydocs_mcp.application.suggestions` — built as "the Phase 2 attribution
input" (`application/suggestions.py:34-36`).

## Options considered

- **(a) Binary used-in-answer flag per file.** One bit: did the file end up in
  the final patch after appearing in any tool result. Buried: it conflates
  hit-lists with reads — a `glob` path listing and a 400-line verbatim
  `read_file` both count as "the tool found it", so the metric can express
  neither the wasted-read pattern nor the surface-vs-read distinction. The
  verified classification (Evidence, first block) shows the content/hit-list
  boundary is real and per-mode; a single bit throws that structure away.
- **(b) Tiered surfaced → inspected → used with first-touch credit. CHOSEN.**
  Three cumulative tiers grounded in the verified per-tool classification,
  plus "which tool *first* surfaced each gold file" as the headline credit
  rule. Deterministic, computable from trace + patch alone (R5/R6), and
  honest about fidelity because each tier maps to an observed content class.
- **(c) Weighted multi-tool credit.** Split credit across every tool that
  touched a gold file (inverse-rank, decay, …). Rejected *pending fixture
  evidence*: it adds free parameters with nothing to calibrate against in
  Phase 2 (weight calibration is explicitly Phase 3), and its added value
  exists only if first-touch demonstrably misassigns credit on real
  trajectories — exactly what the hand-labeling exercise below measures. If
  labelers find material misassignment, (c) is reconsidered with data rather
  than adopted on spec.

## Decision

**Option (b): three tiers, first-touch credit, one path normalizer, honest
per-kind fidelity — validated against 10–20 hand-labeled fixtures at a ≥0.90
agreement bar.**

**Tiers** (cumulative; per file, per trajectory, from the ADR 0010 trace):

- **surfaced** — the file appears in any result set of any tool, any mode
  (items[] row, or a path in the text body). This scope is deliberately
  items-inclusive: an *enumeration* scope, not a model-visibility scope.
  ADR 0010's schema semantic (items presence ≠ "shown to the model") holds
  unchanged at the schema layer; this tier knowingly counts budget-elided
  `search_codebase` rows the token-budgeted text never rendered. That is
  the second stated directional bias — symmetric with the grep items-leak
  below, documented in Consequences, and measured by the fixture-labeling
  exercise.
- **inspected** — file *content* was returned by a content-classified
  tool/mode: `read_file`, `grep` content mode, `get_symbol depth=source`,
  `get_context`, `search_codebase` chunk rows — plus the loop's client-side
  Read tool events from the stream-json side. The grep `files_with_matches` /
  `count` items-leak (one matched line per file, `file_tools.py:292-301`) is
  classified **surfaced, not inspected**: the text body a text-reading client
  consumes is paths-only, and one leaked line is not a read. The leak is
  documented here and in the attributor's classification table — a
  deliberate bias, not an oversight.
- **used** — the file overlaps the final patch (the runner-captured
  `git diff`). Overlap is hunk-level where line fidelity exists, file-level
  where it does not, per the Evidence fidelity rules: chunk rows, `grep`,
  `read_file`, `get_symbol`/`get_context` node spans qualify for hunk-level;
  member rows (best-effort), decision rows (null by contract), and
  `get_references` rows (defining-node spans, not call sites) are file-level
  only. **Hunk metrics are emitted exclusively from span-bearing evidence**,
  each metric row stamped with its source's fidelity class — no fabricated
  line precision.
- **wasted-read** = inspected ∧ ¬used — the diagnostic the tier split exists
  to enable.

**First-touch credit.** "Which tool first surfaced each gold file" goes to
the single earliest event (by the server-authoritative `seq` of ADR
0009/0010) whose result set contains the file; per-tool inspected/used rates
are the diagnostic tail. No credit splitting in Phase 2.

**One path normalizer.** A single function (with its own test module) is the
only code allowed to compare paths across sources. It reconciles the three
verified conventions — index-root-relative (index-backed tools),
project-root-relative POSIX (filesystem tools, project files), absolute
(filesystem tools' dependency files; the loop's Read tool) — into one normal
form: **workspace-root-relative POSIX**. Dependency paths resolving outside
the workspace stay absolute and are **excluded from gold matching**: gold
diffs are workspace-relative by construction (`a/…`/`b/…`, `-p1` compatible),
so a dependency path can never legitimately match a gold file, and forcing it
into the workspace would fabricate matches.

**Gold side.** Gold files = files modified by the instance `patch`, extracted
by our own rule, stated independently: unidiff `PatchSet` over the patch;
every file section contributes its **target** file; sections with a
`/dev/null` source are new files and are **included**; renames contribute
source **and** target. The upstream `get_modified_files`
(`swebench/harness/utils.py:334-343`) is cited only as the
unidiff-`PatchSet` precedent and MUST NOT be copied for gold extraction: it
takes only source-side files and deliberately skips `/dev/null` sources —
it serves the test-file *reset* path, where new files have nothing to reset
— so copying it literally would drop every gold file the patch creates
(new files appear in 21.1% of instances). `patch`/`test_patch` file-set
disjointness is **asserted in the parser** — measured 0/1888 overlap, but the
assert stays per spec so a dataset refresh that breaks the invariant fails
loudly instead of silently polluting gold. The parser must survive every
measured edge case in the Evidence frequency table — including hunkless
renames, the binary one-liner, the symlink, and unquoted space-containing
paths (naive `line.split(' ')` header parsing is forbidden) — and 2.1 MB
patches without O(n²) behavior on 24k-entry P2P lists. It dedupes by
`instance_id` (the known `conan-io__conan-18153` duplicate) and **never
treats F2P/P2P entries as valid pytest node ids** — matching uses the same
`line.split()[1]` normalization the harness applies, because 5.8% of F2P
entries are space-truncated parametrized ids.

**R7 exclusions.** Content whose first line exactly matches
`INJECTED_CONTEXT_MARKER` is excluded from all three tiers — harness-injected,
never model-retrieved (the marker's own contract,
`session_start_context.py:32-35`). Suggestion-fired events attach to their
tool event as machinery annotations and never add evidence to any tier; they
exist so analysis can subtract nudged routing from model-earned routing, per
ADR 0007.

**Validation gate.** Before the attribution metrics ship, 10–20 trajectories
are hand-labeled and compared against the algorithm on (i) the used-file set
and (ii) first-surface credit assignment, per-trajectory macro-averaged.
**Committed threshold: ≥ 0.90 exact agreement on both.** Rationale: at 10–20
fixtures, 0.90 is the strictest bar distinguishable from noise — one
disagreement in ten labels — while still catching the systematic path- and
tier-classification errors the fixtures exist to catch. Below 0.90 the
algorithm is revised (or option (c) reconsidered, if the failures are
first-touch misassignments) before any metric ships.

## Validation results

Run 2026-07-21 over the 12 captured real rollouts
(`benchmarks/tests/trajectory/fixtures/trajectories/real/`, ADR 0009 capture,
`claude-haiku-4-5-20251001`, `--max-turns 15`) against the independent
model-visible hand labels, via the one documented command
(`compare_labels.validate_directory`).

- **Threshold:** ≥ 0.90 exact agreement (used-file set AND first-surface
  credit, per-trajectory macro-average).
- **Trajectories labeled:** 12 (4 edit tasks × 3 samples; all `resolved`).
- **Used-file-set agreement (macro):** **1.000** (12/12 exact).
- **First-surface credit agreement (macro):** **1.000** (12/12 exact).
- **First-touch misassignments observed (option-(c) trigger):** 0. No gold
  file was first surfaced by a hit-list tool and only later re-surfaced by a
  content tool, so first-touch never misassigned credit. Option (c) stays
  deferred — no real-trajectory evidence justifies weighted multi-tool credit.
- **Budget-elided surfaced credit (search items-beyond-text over-count;
  text-side re-scope trigger):** 0. In all 11 MCP rollouts `search_codebase`
  (seq 1) rendered the buggy function body in the *visible text* (the gold
  line was on-screen), so first-touch credit to search matched the text-side
  label; no credit went to a row the token budget elided. The over-count bias
  is real by construction but did not bite this corpus — the surfaced tier is
  **not** re-scoped to the text side.
- **Disposition: SHIP.** Both agreements meet the bar; the qualifier is
  dropped and the attributor ships as specified.

**One algorithm revision made to reach these numbers (documented per the
gate's revision rule).** The first gate run scored 0.917/0.917 (11/12) — the
lone 0-MCP rollout (`3c63ee67…`) disagreed at 0.000/0.000. Root cause was a
**systematic path-normalization error**, exactly the failure class the gate
exists to catch, not a wrong label: that rollout's one content-surfacing event
was a loop-side `Read` whose `file_path` the CLI canonicalized to
`/private/var/folders/…/tmp.X/widgetlib/calculator.py`, while the rollout
driver recorded `workspace_root` under the macOS firmlink alias
`/var/folders/…/tmp.X`. `/var` is a symlink into `/private/var`, so the two
denote the same location, but the normalizer's lexical prefix check judged the
in-workspace file a dependency and excluded it from gold matching — leaving the
rollout with an empty surfacing set. The label was verified factually correct
against the trace (content first rendered at that `Read`; the patch edits that
file) before any code change. Fix: `path_normalizer` now folds the macOS
firmlink prefixes (`/private/var`→`/var`, `/private/tmp`→`/tmp`,
`/private/etc`→`/etc`) purely lexically on both sides of the comparison —
byte-identical on every platform (R6), a no-op off macOS — regression-tested by
`test_macos_private_var_firmlink_relativizes_against_var_workspace` and its
symmetric partner. A **counter-finding surfaced during the diagnosis and was
deliberately NOT "fixed":** every non-`file_path`-keyed `Read` in the corpus
(11 `path`-keyed, 1 `file`-keyed) was rejected by the CLI with
`InputValidationError` (`is_error: true`) and rendered no content — Haiku
emitting the wrong parameter name. Teaching the attributor those key aliases
would have fabricated surfacings for Reads that never put content in front of
the model; only `file_path` is the valid Read parameter, so the attributor
correctly ignores them. Re-measured after the one fix: 1.000/1.000.

## Consequences

Benefits:

- The wasted-read metric (inspected ∧ ¬used) becomes computable, and the tier
  boundaries rest on verified per-tool behavior, not assumed semantics.
- First-touch credit is deterministic and parameter-free (R6): same trace,
  same credit, no weights to version this phase.
- The single normalizer + parser-side disjointness assert concentrate the two
  highest-risk correctness surfaces (path identity, gold extraction) into two
  small, independently tested units.
- Honest fidelity stamping lets downstream consumers (ADR 0012's score
  components, reflector feedback) trust that a hunk-level number is really
  hunk-level.

Costs and risks:

- **The grep items-leak bias is real.** A structured-content-reading client
  genuinely sees one line per file in the per-file grep modes; classifying
  those as surfaced under-counts its content exposure. Accepted: the
  alternative (counting one leaked line as a read) inflates the inspected
  tier for the default text-reading client, which is worse.
- **The search items-beyond-text over-count is real, in the opposite
  direction.** `search_codebase` items[] enumerate ranked rows the
  2000-token composite budget may have elided from the text body, so a
  budget-elided gold row still counts as surfaced — first-touch credit
  (and ADR 0012's localization-recall component) can credit search for a
  file the model never saw. Accepted as the second deliberate bias, per
  the enumeration-scoped tier definition: hand labels are made from the
  model-visible transcript, so the fixture exercise measures this
  over-count directly (elided-row credit shows up as first-surface
  disagreement). If the measured drift is material, the surfaced tier is
  re-scoped to the text side (blob-dereferenced), per ADR 0010's
  text/items split.
- **First-touch may misassign** when a hit-list tool surfaces a file the
  model ignores until a later content tool re-surfaces it. Exactly what the
  fixture labeling measures; the Validation results section is the
  commitment to report it rather than assume it away.
- **File-level fallback flattens some evidence.** Member/decision/references
  contributions can never earn hunk-level credit even when used decisively;
  the fidelity limitation is the tools' (verified), not the attributor's,
  but per-tool used-rates for those tools will read conservatively. (The
  file-level rule also mitigates defining-node over-coverage: a references
  row's whole-node span could otherwise overlap unrelated patch hunks.)
- **Live-vs-indexed span skew.** Indexed spans are as-of-last-index; after
  mid-rollout edits, hunk overlap against the final diff can drift. Indexing
  immediately before rollout bounds but does not eliminate the skew —
  flagged for the Phase 3 dataset design.
- **The threshold is a small-sample bar.** 0.90 over 10–20 trajectories can
  only catch systematic errors, not certify the attributor in general.
  Accepted: a large labeled set is Phase 3 work.

## Action items

All Phase 2 (this phase) unless noted:

1. Implement the tier attributor in
   `benchmarks/src/pydocs_eval/trajectory/` (the reconciliation record's
   placement), consuming ADR 0010 `events.jsonl`; encode the per-tool/mode
   classification table from `…-evidence-result-shapes.md` §3 as data, one
   source comment per row, including the grep items-leak note and the
   search items-beyond-text elision note.
2. Implement the single path normalizer as its own module there, with a
   dedicated test file covering all three conventions plus the
   dependency-absolute exclusion.
3. Implement the gold-diff parser (unidiff-backed) with the disjointness
   assert, `instance_id` dedupe, and regression tests pinning every measured
   edge case in `2026-07-18-phase2-evidence-swebench-formats.md` §2 (new
   file, deletion, hunkless rename, binary one-liner, symlink, no-newline,
   space-in-path header).
4. Implement F2P/P2P name matching via the harness's `split()[1]`
   normalization; regression-test with a truncated id from the dataset
   (the `test_validate[Invalid` shape).
5. Wire R7 exclusions — exact-first-line `INJECTED_CONTEXT_MARKER` match and
   suggestion-fired machinery annotations (from the ADR 0009 log-handler
   capture) — with tests proving neither adds evidence to any tier.
6. Emit per-metric fidelity stamps (hunk vs file level, per source row kind)
   in the attributor output consumed by ADR 0012's components.
7. Run the 10–20-trajectory hand-labeling exercise; compute both agreement
   numbers plus the budget-elided surfaced-credit tally (how often
   first-touch credit went to a search row the text body never rendered);
   fill this ADR's Validation results section and set the status to plain
   Accepted (or revise per the gate).
8. **Deferred to Phase 3/4:** weighted multi-tool credit (only if item 7
   surfaces first-touch misassignment), large-scale labeled validation,
   weight calibration, dataset changes addressing live-vs-indexed span skew.
