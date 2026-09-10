# Branch and diff task layer: change review, release notes, and the diff-scoped variants over the frozen nine tools — Design

**Date:** 2026-09-04
**Status:** Draft, for owner ratification. Companion of the multi-branch
indexing design (`docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md`,
amended 2026-09-04, whose Amendments section names this file as the owner of
"the task shapes, their gold construction, and their benchmarks" —
`:2292-2305`) and of the ask-your-docs branch scope UI design
(`docs/superpowers/specs/2026-09-04-ask-your-docs-branch-scope-ui-design.md`).
The program plan (`docs/superpowers/plans/2026-09-03-multi-branch-indexing-program.md:82-85`)
states that this spec "consumes P2.2 and P2.8 and adds no rows here until it
is ratified". No code written. Owner directions T1–T6 are restated in §3;
the clarifying questions are answered by stated proposals in §13, each
flagged for ratification.
**Owner:** the repository owner.
**Amends (proposed, additive, routed to their owning documents):** the
multi-branch spec R12 (three card blocks, §6.9 of this document); the
run-contract design §5 (a dated amendment recording the fourth and fifth
framings, §6.7); the UI spec §6.9 (a fourth follow-up chip kind, §6.8);
`skill_artifact_loader.TASK_NAMES` (the widening event, §6.7). Nothing here
amends `docs/tool-contracts.md`. This document depends on one external
decision that is still open in the multi-branch spec: O10 there (decision
mining per branch, proposed branch-only), which §6.2 step 7 and §10 mark as
conditional.
**Companions:** the harness run contract
(`docs/superpowers/specs/2026-07-27-harness-run-contract-design.md` §5, §6),
the retriever-centric platform design
(`docs/superpowers/specs/2026-07-26-retriever-centric-harness-platform-design.md`
§5.2–§5.4), the third-framing precedent
(`benchmarks/src/pydocs_eval/datasets/bug_localization.py`,
`benchmarks/src/pydocs_eval/datasets/_bug_loc_gold.py`), ADR 0013 (dev/val
splits), ADR 0014 (the canonical-checkout index cache that P3.3 retires).

**Goal:** Specify the task layer that the branch and diff features serve —
what a user or an agent asks, which of the frozen nine tools answer it in
which order, what the answer must contain, and how each task is evaluated —
with no new tool, parameter, or envelope field, every tunable in YAML, task
guidance in the skill layer, and a benchmark gate per task.

---

## Abstract

The multi-branch design gives the nine tools a branch selector, two corpus
slices (`scope=changed`, `scope=diff`), landing units that keep a merged
branch's diff inside a retention window, and two cards (branch card, landing
card). Those are retrieval primitives; nothing yet says what an agent is
supposed to *do* with them. This document defines the tasks. Two are new
task names — **`change_review`** (review of a live branch or a landed unit
against its base, with blast radius through the reference graph) and
**`release_notes`** (a changelog section from the landing units between two
tags). Five candidates raised with the owner are placed rather than named:
test gap, conflict pre-check, documentation drift, and PR description are
**dimensions of `change_review`** (same inputs, same tool order, one more
required section or one different rubric); the API-surface diff is a
**section of `release_notes`**; regression localization is the existing
**`bug_loc`** framing over a ref range of landing units. Each task gets a
harness-invariant `TASK_HEAD` and two `HARNESS_TASK_HEAD` seed sections
(all six drafts measured under the 300-token caps, §6.2–§6.3), an ordered
tool trajectory that uses only the nine tools with the selectors the
multi-branch spec plans, an output contract expressed as `StrEnum` section
vocabularies, and an evaluation: dataset sources, gold, task-id minting,
splits, deterministic checks, metrics, and the benchmark gate. Where a task
needs something the surface does not provide — enumerating the landing units
between two tags, the base-side change set for a conflict pre-check, a
structured signature diff — the gap is named and routed to a card block or a
YAML key, never to a parameter (§6.9). Staging follows the program plan: on
P0 only a diff-in-the-prompt review is possible; P1 enables whole-branch
review; P2.1/P2.2 enable slice-anchored review; P2.8 enables release notes
and regression localization; P3.3 enables the graph on a landed state.

---

## 1. Context and problem statement

### 1.1 What the diff slice gives an agent, and what it does not

After P1 and P2 the surface answers, per request, from one branch
(`§6.4 :685-727`), narrows to the files a branch changed (`§6.5 :729-782`)
or to the hunks themselves (`§6.5a :784-848`), and addresses a landed
branch's diff by its landing SHA (`§6.5b :930-953`). The tool split for a
landing unit is strict: `search_codebase(scope="diff")`, `grep(scope="diff")`
and `get_overview` answer; the six other tools raise `InvalidArgumentError`
because a unit has no tree (`§6.5b :935-950`, `§6.11 :1548-1549`, O17
`:2092-2097`). `get_references` resolves within one branch only — unions are
a non-goal (`§6.4 :720-724`).

Three consequences shape every task below:

1. **Blast radius is symbol-anchored and tree-anchored.** `impact` is a
   reverse transitive-caller walk from one symbol
   (`application/reference_service.py:288-339`, depth from
   `reference_graph.impact.max_depth`, `defaults/default_config.yaml:105`).
   The blast radius of a *change* is therefore N calls, one per changed
   symbol, on a branch that has a tree — for a landed unit, on the base
   branch after landing (or a P3.3 tree-indexed SHA).
2. **No tool lists the landing units of a release.** The landing card
   carries "window position (the release tags before and after it)"
   (R12 `:325-330`); the plain `get_overview()` listing is "one line per
   branch" and does not say whether units appear; the CLI `branches` verb
   lists them "under landed" (`§6.9`, P2.8 row) but is not an MCP path.
3. **Signatures are not on the wire.** `items[]` rows carry no signature
   (`docs/tool-contracts.md` §3.2), and the PageIndex JSON of
   `get_symbol(depth="summary")` has no signature field
   (`extraction/model/document_node.py:83-101`) — only `depth="source"`
   returns text a signature can be read from. An API-surface diff is
   therefore a card block, a per-branch source-text comparison, or (between
   two tags) a reading of the `-def` / `+def` lines the units' hunks carry.

### 1.2 The task layer today

`TASK_NAMES = ("repo_qa", "vuln", "bug_loc")` is a plain tuple
(`python/pydocs_mcp/harness/core/skill_artifact_loader.py:72`);
`HARNESS_NAMES = ("ask_your_docs", "external")` (`:71`). Everything derives:
`TASK_HEAD_SECTION_HEADERS` (`:122-124`), the harness-major
`HARNESS_TASK_HEAD_SECTION_HEADERS` (`:125-129`), and
`SKILL_ARTIFACT_HEADERS` whose count is `1 + T + 2T` (`:136-140`). Every
enumerated section is required unconditionally (`:232-237`) and capped:
`PER_TASK_HEAD_TOKEN_BUDGET = PER_HARNESS_TASK_HEAD_TOKEN_BUDGET = 300`
(`:80-81`) at `CHARS_PER_TOKEN = 4`
(`application/description_source.py:65`), so 1,200 characters per section.
The packaged seed (`harness/core/skills/search_guidance_seed.md`) carries a
`BACKBONE` of routing policy (`:1-41`) and one head per task; the `bug_loc`
head (`:69-82`) is the style model — one sentence naming input and answer
shape, the routing hint, the one graph move that matters, two named failure
modes, the exact output shape, no tool catalogue.

At run time the sample row's `task_name` selects the head
(`harness/core/run_contract.py:117` `REQUIRED_SAMPLE_KEYS`): the ask
harness folds `backbone + task_head + harness_task_head`
(`harness/ask_your_docs/agent.py:218-239`), the external harness folds the
same three tiers onto its engine's system-prompt channel
(`harness/external/harness.py:105-109`, `harness/core/guidance_fold.py:66-84`).
The shipped chat UI and CLI never pass a `task_name`
(`harness/ask_your_docs/app.py:83`, `cli.py`), so a task head reaches a
model only through an arm today.

### 1.3 Why a task name is a widening event

A name is not a label: it is one `TASK_HEAD` section plus two
`HARNESS_TASK_HEAD` sections, all required, all capped, all folded into a
fingerprint. The eval side reads the enumeration and refuses to mint outside
it (`benchmarks/src/pydocs_eval/optimize/load_firewall.py:87-101`: "widening
them is a product event"; `optimize/ask_binding.py:283-294`
`known_task_names()`), and the task-id parse is vocabulary-anchored on it
(`benchmarks/src/pydocs_eval/datasets/task_ids.py:81-101`). Widening moves
the seed fingerprint and the ask delivery-map digest, hence every ask arm
hash (run-contract §5, third-framing amendment item 9). The platform rule is
therefore: **a candidate becomes a task name only when it has its own answer
shape and its own tool order; when it shares both with an existing name, it
is a dimension of that name** — selected per dataset, scored per dataset
(`optimize/rubric/checks.py:184-189` keys applicability on the dataset
prefix), and it costs no seed section.

---

## 2. Terms

The multi-branch vocabulary is reused verbatim (multi-branch spec §2
`:131-178`); the UI spec §2 (`:150-196`) agrees on it. Task-layer terms are
added at the end.

- **Branch**: a local ref under `refs/heads/`; the `branch` selector also
  accepts the commit SHA of a landing unit.
- **Working-tree branch**: the branch checked out at the project root the
  server was started in.
- **Base branch**: the branch a diff is computed against (R14).
- **Base tip**: the current head of the base branch — the remote-tracking
  ref of `git.remote.name` when present, else the local branch; read, never
  indexed.
- **Merge-base pair**: `(merge_base_sha, head_sha)`, the two commits a diff
  slice was generated from.
- **Landing unit**: one first-parent step `c` on the base branch with the
  diff `c^1..c`; a `branches` row named by the full 40-hex landing sha with
  `landing_kind ∈ {MERGE_COMMIT, SINGLE_COMMIT, LINEAR_SNAPSHOT}`; carries a
  `DIFF` slice only, never a tree.
- **Slice**: which part of a branch a request covers — the whole branch
  (`BranchSlice.TREE`, the default), the files the branch changed
  (`scope=changed`), or the diff hunks (`scope=diff`, `BranchSlice.DIFF`).
  The UI spells the three as *whole branch / changed files / diff hunks*
  (`ScopeSlice.WHOLE_BRANCH | CHANGED_FILES | DIFF_HUNKS`, UI spec
  `:394-397`); the server spells the request values `changed` and `diff`
  and the membership rows `TREE` and `DIFF` (`models.py:159`
  `BranchSlice`). This document uses the request spelling in trajectories
  and the UI spelling in §6.8 only.
- **Retention window**: the set of landing units whose `DIFF` slice is kept
  (`git.diff_chunks.retain`: exactly one of `since_tags` / `days` /
  `landings`, capped by `max_landings`).
- **Ref range**: two refs `(A, B]` on the base's first-parent line — two
  tags, a tag and the base tip, or two landing shas — and the landing units
  strictly after `A` up to and including `B`: `B`'s own landing is in the
  range, `A`'s is not; for a tag, the landing is the tag's peeled commit.
- **Branch card / landing card**: the `get_overview` renderings of R12
  (`:320-330`).
- **Task name**: one member of `TASK_NAMES`; owns one `TASK_HEAD` and two
  `HARNESS_TASK_HEAD` seed sections and is the middle segment of a
  three-part task id.
- **Dimension**: a variant of a task name that keeps its answer shape and
  tool order and differs by dataset, required section, or rubric. Selected
  per dataset, never per name.
- **Change under review**: the branch or landing unit a `change_review`
  record targets; its **change set** is `scope=changed` and its **hunks**
  are `scope=diff`.
- **Finding**: one review claim cited to a path and a symbol.
- **Bullet**: one release-notes entry cited to one or more landing units.
- **Churn unit**: a landing unit whose diff touches only paths under
  `.github/`, lockfiles, or the formatter snapshot (the §6.3 path rule);
  left out of the notes and out of the coverage key set.
- **Coverage key set**: the units a `release_notes` answer must cite —
  the sidecar-aligned units of the window, or every non-churn unit when
  the window has no sidecar rows (§7.2).

---

## 3. Requirements

### 3.1 Owner directions (T1–T6, restated precisely)

- **R1 — Purpose (T1).** The task layer specifies, per task: what is asked,
  which of the nine tools answer it in what order, what the answer must
  contain, and how it is evaluated. The two primary tasks are code review
  with blast radius and release description. Each further candidate is
  either a task name or a dimension, with the platform rule of §1.3 as the
  justification.
- **R2 — Surface constraint (T2).** No new MCP tool, parameter, or envelope
  field. Every trajectory uses only the nine tools with the selectors the
  multi-branch spec plans: `branch` (names and landing shas, P1),
  `scope=changed` (P2.1), `scope=diff` (P2.2), `get_references(direction=
  impact|callers|callees)`, `get_why`, the two cards, and `grep` with
  `scope` (the only filesystem tool that takes the slice; `glob` and
  `read_file` take `branch` only — multi-branch spec §6.5 and O2 there).
  A need the surface does not meet is routed to YAML or to a card.
- **R3 — Guidance in the skill layer (T3).** Each task name gets one
  `TASK_HEAD` (harness-invariant, ≤ 300 tokens) and two `HARNESS_TASK_HEAD`
  sections (≤ 300 tokens each) following the seed grammar. The ReAct
  architecture is unchanged: the head states the expected shape and the tool
  order that works; the agent decides the trajectory.
- **R4 — Evaluation (T4).** Per task name: framing, dataset sources, gold
  shape and derivation, deterministic checks, metrics, task-id minting
  (`<dataset>/<task_name>/<record_id>`), splits, and the benchmark gate —
  following run-contract §5 and the `bug_loc` precedent.
- **R5 — Dependencies and staging (T5).** Per task: the program items it
  needs (P1 selector, P2.1, P2.2, P2.8, v17 columns, P3.3) and what ships
  on P0; a staged rollout aligned with the program plan and the UI stages
  U0/U1/U2 so the three documents share one vocabulary.
- **R6 — Constraints (T6).** Plain-English names; `StrEnum` vocabularies
  (task names stay an enumerated tuple; §6.7 says how the widening lands);
  no vendor or competitor product names; no internal PR numbers in
  normative text; every tunable in YAML; numbered testable acceptance
  criteria; only genuine open decisions.

### 3.2 Proposed additional requirements

- **R7 — Hunk rows name their enclosing symbol on the wire.** A
  `search_codebase(scope="diff")` item's `qualified_name` is the dotted
  qualified name of the enclosing symbol (the one the title's
  `"<path> · <enclosing symbol>"` names, `§6.5a :802-809`), so a review can
  chain a hunk hit into `get_references(target=…)` without parsing the
  title. This is a value-semantics statement on an existing field, not a
  field. Routed to the multi-branch spec §6.5a as an amendment (§6.9 G4).
- **R8 — Deterministic slice evidence.** The rubric can tell whether a run
  consulted the change (a server call with `scope ∈ {changed, diff}`) and
  which branch or unit it consulted, from the trajectory's server events
  alone — three new registered check kinds over the existing
  `server_events.jsonl` `args`, no trace-schema change (§7.5).
- **R9 — Output contracts are enumerated.** Required answer headings are
  `StrEnum` members on the eval side and the seed head names them in prose;
  the deterministic heading gate is a registered gate kind that builds its
  pattern from the enum at call time (§7.5), so no config file spells a
  heading list.
- **R10 — Leak floor for self-corpus gold.** A record whose gold text is
  itself inside the indexed corpus (a release's CHANGELOG section, which
  sits in the tree at the newer tag, in the hunks of every unit in the window
  that touched the changelog, and in the release commit's message) is
  materialized with the gold removed from every surface the nine tools can
  reach — tree, hunks, and mined decisions — and a build-time leak check
  greps all three (§7.2), the `crosscommitvuln` leak-check precedent.
- **R11 — Head text is harness-neutral and ref-neutral.** A `TASK_HEAD`
  names tool *order*, the *slice* a step consults, and answer *shape*; it
  never names a branch, sha, tag, or base value, because under a kept pin
  the ask interceptor overwrites `branch` (UI spec §6.3 PIN column, R5(c)
  there) and the external harness does not — a head that forced a branch
  would make the two harnesses behave differently on the same text. Naming
  the slice is allowed because the slice is the task's substance: under a
  whole-branch pin the interceptor overwrites the slice too, by design (the
  pin is hard), and the `slice_consulted` check (§7.5) then scores the
  trajectory that ran, not the head.

---

## 4. Goals / Non-goals

### Goals

- Two ratifiable task names with complete shapes, seed drafts, trajectories,
  and evaluations.
- A placement for every candidate the owner raised, with the rule that
  placed it.
- A gap list that routes every unmet need to a card block or a YAML key.
- One staging table the multi-branch program, the UI spec, and this document
  can be checked against.

### Non-goals

- Changing `docs/tool-contracts.md` — the selector and the scope values are
  the multi-branch spec's amendment (its §7), proposed as the 0.7.0 event
  (O5 there); this document adds nothing to it.
- Widening the token budgets. All six drafts fit (§6.7 records the
  measurements; no budget change).
- Converting `TASK_NAMES` to a `StrEnum` — the owner's direction (R6) keeps
  the tuple; §6.7 says how the widening lands on it.
- Automating review comments back to a hosting service, or writing the
  changelog file. The tasks produce answers; side effects are out of scope.
- A per-request base (`changed@<base>`) — O3 of the multi-branch spec, YAML
  only.
- Unions across branches in `get_references` — a multi-branch non-goal that
  this document honors by running graph walks on one branch per call.

---

## 5. Approaches considered

| # | Approach | Why rejected (or kept) |
|---|---|---|
| A | **One task name per candidate** — seven new names (`change_review`, `release_notes`, `test_gap`, `conflict_precheck`, `doc_drift`, `pr_description`, `api_diff`) | Rejected. Seven names are 21 required seed sections and a 31-key artifact; five of the seven share the `change_review` inputs (one branch or unit, its change set, its hunks, the graph on the tree) and its tool order, so their heads would repeat the same 800 characters with one sentence changed. The optimizer would then tune seven near-identical heads on tiny corpora. The platform rule (§1.3) says a shared shape is a dimension, not a name. |
| B | **Two names plus dimensions** — `change_review` and `release_notes`; test gap, conflict pre-check, doc drift, PR description as `change_review` dimensions selected per dataset; API diff as a `release_notes` section; regression localization as `bug_loc` over a ref range | **Kept.** Two names are six seed sections (the `bug_loc` event's cost, ×2). Dimensions ride on dataset prefixes, which is exactly how `applies_to` / `weight_by_type` already select checks (`checks.py:119-129, :184-189`). Regression localization keeps `bug_loc`'s answer shape (paths, plus the unit) and its head already says "walk outward with get_references before answering". |
| C | **Fold everything into `repo_qa`** — review and release notes as question stems over the existing framing | Rejected. `repo_qa`'s head is shaped by the Where / What-How / Why probe and a concise code-grounded answer (seed `:42-56`); a review has fixed sections and a graph walk per changed symbol, and release notes have an enumeration step and a grouping rule — neither fits a probe-matched answer, and the head has no room (990 of 1,200 characters used). Folding would also mint review records as `repo_qa` rows and pollute its dev/test statistics. |
| D | **A diff-anchored impact primitive** (`get_references(direction="impact", branch=<sha>)` resolving on the unit) | Rejected by R2 and by the multi-branch non-goal: a unit has no tree, and adding tree semantics to a unit is P3.3's tree-indexed SHA, which makes the row a branch. The trajectory instead locates symbols in the unit's hunks and walks the graph on the base branch. |
| E | **A listing tool for landing units** | Rejected by R2 — a tenth tool. Routed to the base-branch card (§6.9 G1). |

---

## 6. Architecture

### 6.1 The naming decision (proposed, for ratification)

| Candidate | Placement | Rule applied |
|---|---|---|
| Code review of a branch or landing unit with blast radius | **task name `change_review`** | Own answer shape (fixed review sections), own tool order (slice → graph → read). |
| Release notes / changelog between two tags | **task name `release_notes`** | Own answer shape (grouped bullets with unit citations), own tool order (enumerate → per-unit card → per-unit diff). |
| Test-gap check per change | dimension of `change_review` (`ChangeReviewDimension.TEST_GAP`) | Same inputs and order; adds one required section and one gold key. |
| Conflict pre-check (both sides of the merge-base) | dimension of `change_review` (`CONFLICT_PRECHECK`), **v1 card-gated** | Same inputs; needs a card block the surface lacks (§6.9 G5). Ships when the block does. |
| Documentation drift (MENTIONS-linked doc chunks unchanged) | dimension of `change_review` (`DOC_DRIFT`) | Same inputs; adds one section; needs `mentions` capture (YAML). |
| PR description drafting | dimension of `change_review` (`PR_DESCRIPTION`) | Same inputs; a different rubric (judge-only), no localization gold. |
| API-surface diff between two refs (breaking changes) | section of `release_notes` (`ReleaseNotesHeading.CHANGED_API`), also readable as a `change_review` finding | It is one heading of the notes; between two branches it is a text comparison of `kind=api` rows; between two tags it is read from the units' hunks at S2b and made exact by P3.3 (§6.5). |
| Regression localization (which unit touched a symbol between two refs) | dataset under **`bug_loc`** with a ref-range corpus | Same answer shape as `bug_loc` (paths) plus the unit sha as an extra gold key; the head's "walk outward" move is the right one. |
| Diff-scoped vulnerability search | dataset under **`vuln`** with a fix-landing corpus | Same answer shape as `vuln`; the v2 `crosscommitvuln` invariant (no commit signal) forbids changing that dataset, so it is a sibling dataset (§7.4). |

The `StrEnum` vocabularies this document introduces, all on the eval side
(`benchmarks/src/pydocs_eval/datasets/change_tasks.py`, new, §6.7):

```python
class ChangeReviewDimension(StrEnum):
    BLAST_RADIUS = "blast_radius"        # the default dimension
    TEST_GAP = "test_gap"
    CONFLICT_PRECHECK = "conflict_precheck"
    DOC_DRIFT = "doc_drift"
    PR_DESCRIPTION = "pr_description"

class ReviewHeading(StrEnum):            # headings of a change_review answer
    SUMMARY = "summary"
    FINDINGS = "findings"
    BLAST_RADIUS = "blast radius"
    TEST_GAP = "test gap"
    DOC_DRIFT = "doc drift"

class ReleaseNotesHeading(StrEnum):      # headings of a release_notes answer
    ADDED = "added"
    CHANGED = "changed"
    FIXED = "fixed"
    REMOVED = "removed"
    CHANGED_API = "changed api"

# Which headings a dimension requires; the heading gate (§7.5) reads this.
REQUIRED_REVIEW_HEADINGS: Mapping[ChangeReviewDimension, tuple[ReviewHeading, ...]] = {
    ChangeReviewDimension.BLAST_RADIUS: (SUMMARY, FINDINGS, BLAST_RADIUS, TEST_GAP),
    ChangeReviewDimension.TEST_GAP: (SUMMARY, FINDINGS, BLAST_RADIUS, TEST_GAP),
    ChangeReviewDimension.CONFLICT_PRECHECK: (SUMMARY, FINDINGS),
    ChangeReviewDimension.DOC_DRIFT: (SUMMARY, FINDINGS, BLAST_RADIUS, TEST_GAP, DOC_DRIFT),
    ChangeReviewDimension.PR_DESCRIPTION: (SUMMARY,),
}
```

The two heading enums and the dimension enum share no member name with a
different meaning: `ChangeReviewDimension.TEST_GAP` is a dataset variant,
`ReviewHeading.TEST_GAP` is an answer heading, and the class name makes each
grep hit unambiguous.

**Heading grammar.** A heading is a line matching
`^\s*(#{1,6}\s*)?<heading>\s*:?\s*$` (case-insensitive, the heading text
through `re.escape`); the heading gate is the conjunction of one such
lookahead per required heading, built at call time from the enum and the
mapping above (§7.5). The heads say "answer in fixed sections" and the
harness heads add nothing about headings, so both harnesses emit the same
lines. `DOC_DRIFT` is required under the `DOC_DRIFT` dimension and optional
otherwise, which is what the `change_review` head's "when docs are indexed"
clause asks for.

`ChangeReviewDimension` is stamped into `EvalTask.metadata["dimension"]` by
each dataset. The report's category breakout reads only
`metadata["qa_type"]` today (`reporting/report.py:31-37`, `_CATEGORY_KEY`),
so the per-dimension breakout is not free: this work generalizes the
breakout to a `breakout_key` parameter defaulting to `"qa_type"`, and the
`change_review` sweep passes `"dimension"` — one function parameter, no
config key (AC-8a, §12). `EvalTask.metadata` is `Mapping[str, str]`
(`datasets/base_dataset.py:50`), so every count stamped there is spelled as
a string. The product side keeps `TASK_NAMES` a tuple (§6.7).

### 6.2 `change_review`

**User story.** "Review this branch" / "review what landed as `3e1a9c2`" /
"what does this change break?" — a developer, or an agent acting for one,
wants a review of one change against its base: what changed, what it
breaks (blast radius through callers), what it misses (tests, docs), each
claim cited to a path and a symbol.

**Inputs.** Exactly one *change under review*, named by the selector, and
one *slice combination*:

| Change under review | Selector | Change set | Hunks | Graph |
|---|---|---|---|---|
| Live branch (any indexed, incl. working tree) | `branch=<name>` (or `""` for the working tree) | `scope=changed` on `<name>` | `scope=diff` on `<name>` (working tree: lazy job, `§6.5c :1045-1066`) | `get_references` on `<name>` |
| Landing unit in the window | `branch=<sha>` | none — the landing card's files-changed list (R12) is the change set; `scope=changed` on a sha is empty with the no-tree suggestion (`§6.5b :941-944`) | `scope=diff` on `<sha>` | `get_references` on the **base branch** (a unit raises, `§6.5b :944-949`) |
| P0 working tree, diff supplied in the prompt | `branch` absent | the prompt's file list | the prompt's hunks | `get_references` on the working tree |

The record's `metadata["dimension"]` selects the required sections (§6.1).

**Tool trajectory** (the order that works; the agent may skip or reorder,
R3):

1. `get_overview(branch=<name or sha>)` — the branch card (head, base,
   merge-base, files changed by kind, symbols changed) or the landing card
   (kind, subject, files changed, hunk count, window position). Gives the
   file list `glob` cannot (multi-branch O2) and the base branch name for
   step 5.
2. `search_codebase(query=<the change's own words or the card's symbols>,
   scope="diff", branch=<name or sha>, limit≈10)` — the hunks; each item's
   `qualified_name` is the enclosing symbol (R7). Two or three sharp
   queries at most (backbone policy, seed `:25-29`).
3. `search_codebase(query=…, scope="changed", branch=<name>)` — whole
   symbols of the changed files, when the change is a live branch and the
   hunk context is too thin. `kind` is ignored on the `diff` slice (`§6.5a
   :832-833`); on `changed` it is assumed to filter the changed set as it
   filters the tree — an assumption for P2.1 to confirm. Live branches
   only: on a unit this call is empty (input table above).
4. `get_symbol(target=<enclosing symbol>, depth="source", branch=<name or
   base>)` / `read_file(file_path=<path>, offset, limit, branch=…)` — read
   the code the finding is about. On a unit both go to the base branch.
5. `get_references(target=<changed symbol>, direction="impact",
   branch=<name or base>)` — one call per load-bearing changed symbol (the
   card's "symbols changed" bounds N); `direction="callers"` for the tests
   that exercise it (`TEST_GAP`).
6. `grep(pattern=<symbol or literal>, scope="changed", branch=<name>,
   glob="**/test_*.py", output_mode="files_with_matches")` — confirm
   whether a test changed (`TEST_GAP`). The grep `glob` is matched against
   the root-relative path with `*` never crossing `/`
   (`application/file_tools.py:208-212`, the glob tool's dialect), so
   `test_*` alone matches only a root-level file; the three spellings that
   cover the eval's test predicate (`is_test_path`, `_bug_loc_gold.py:53-85`)
   are `**/test_*.py`, `**/*_test.py`, and `**/tests/**`, one call each or
   the first two when the repository keeps tests under `tests/`. On a
   landing unit the call is `grep(pattern=<symbol>, scope="diff",
   branch=<sha>, glob=…)` — the unit's test hunks — and the landing card's
   files-changed list is the test-file presence check.
   `grep(pattern=<symbol>, scope="diff", branch=…)` — is the literal in
   the hunks at all.
7. `get_why(query=<the change's subject>, branch=<name>)` — decisions mined
   from branch-only commits when a finding contradicts a recorded decision.
   Conditional on the multi-branch spec's O10 (`:2057-2059`, proposed
   branch-only, not yet ratified); until then `get_why` answers from the
   shared history only.
8. `search_codebase(query=<changed symbol name>, kind="docs", branch=<name>)`
   then `grep(pattern=".", glob=<doc path>, scope="changed", branch=<name>,
   output_mode="files_with_matches")` — a non-empty result means the doc is
   in the change set (`pattern` is a content regex, never a path filter;
   `glob` and `path` are the path filters, tool-contracts §3.7) — or,
   cheaper, read the doc path off the card's files-changed list from step 1.
   `DOC_DRIFT` (§6.4.3).
9. `get_context(targets=[<all findings' symbols>], branch=…)` — one shared
   budget for the final read before answering.

**Output contract.** A review in fixed sections, in this order:
`ReviewHeading.SUMMARY` (two to four sentences: what the change does, from
the hunks, not the subject line); `FINDINGS` (a structured list: `path ·
symbol — claim [· breaks <caller>]`, ordered by severity, each cited to a
path and a symbol read in step 4; an empty list is stated explicitly);
`BLAST_RADIUS` (per changed symbol: transitive-caller count and depth from
step 5, the top callers by name; "unavailable" when `meta.resolution ==
"unavailable"`); `TEST_GAP` (one entry `path · symbol → <test path>` per
changed non-test symbol with no changed test in the change set, the arrow
naming the test file that should exercise it — an existing test file found
through `get_references(direction="callers")` under a test path, else the
conventional `tests/test_<module>.py` location; empty list stated);
`DOC_DRIFT` (required under the `DOC_DRIFT` dimension, optional otherwise;
`REQUIRED_REVIEW_HEADINGS`, §6.1). Every path is repo-relative and spelled
as the repository spells it; every finding names a symbol (`qualified_name`).

**`TASK_HEAD: change_review` draft** (1,128 characters, 282 tokens; cap
1,200 / 300):

```
Review of one change — a live branch or a landed unit — against its base,
and the answer is a review: what changed, what it breaks, what it misses.
Start from the change itself: search_codebase with scope=diff (the hunks) or
scope=changed (the whole symbols) on that branch, never the default scope,
which sees the whole tree. Every hunk hit names its enclosing symbol; follow
the load-bearing ones into the graph with get_references — impact for the
blast radius, callers for the tests that exercise them. A landed unit has no
tree, so run the graph walk on the base branch. Judge each finding against
code you read, not against the diff header. Answer in fixed sections:
summary; findings, each cited to path and symbol and naming the caller it
breaks when it breaks one; blast radius; test gap (changed symbols with no
changed test, each with the test file that should cover it); doc drift
(changed symbols whose docs did not change) when docs are indexed. If the
server offers no diff slice, start from the card's file list instead. Two
failures: reviewing the tree instead of the change, and a finding that
names no symbol.
```

The closing "if the server offers no diff slice" sentence is what makes the
head safe on P1: a model-passed `scope=diff` on a server whose `ScopeLiteral`
lacks it is a pydantic validation error, not a preference the surface
ignores (the ask interceptor keeps a model-passed `scope` when the slice is
not advertised, UI spec §6.3 `scope` as code row; multi-branch §6.11 last
rows), so the head routes the agent to the card's file list before it sends
one.

**`HARNESS_TASK_HEAD: ask_your_docs.change_review` draft** (237 characters):

```
The catalog is already in your prompt; skip orientation calls. Any pinned
branch or slice is applied for you; otherwise name the slice yourself. Start
from the diff hits. The review sections are the answer; skip the
example-call snippet.
```

**`HARNESS_TASK_HEAD: external.change_review` draft** (247 characters):

```
No catalog is pre-injected: orient first — get_overview with the branch, or
the unit's sha, for its card: base, merge-base, files changed. Answers must
be self-contained: every path in full from the repository root, with the
line numbers you read.
```

The task head contains none of `ask_your_docs`, `catalog`, `pre-injected`
(`tests/harness/core/test_skill_artifact_loader.py:182-192` forbids
harness-local facts in a `TASK_HEAD`); it names no branch, sha, or tag
(R11) — the slice values it names are the task's substance.

**Failure modes.**

| Failure | Symptom | Guard |
|---|---|---|
| Reviewing the tree | default-scope search over the whole branch; findings about code the change did not touch | `slice_consulted` check (§7.5); the head's first failure sentence |
| Finding without a symbol | prose claims with no `qualified_name` | `findings_cited` measure (`answer_regex` on the findings grammar, §7.1); judge criterion |
| Blast radius on a unit | `get_references(branch=<sha>)` raises `InvalidArgumentError` | the head's "run the graph walk on the base branch"; the external head's orientation step reads the base from the card |
| Working-tree diff pending | `meta.suggestion = "diff of <branch> is being generated"` (`§6.5c`) | retry once after `lazy_wait_seconds`; fall back to `scope=changed` |
| Non-Python change | `meta.resolution == "unavailable"`, empty `impact` | the `BLAST_RADIUS` section says so; the check `gold_location_evidenced` still scores file evidence |
| Retired branch by name | `InvalidArgumentError` naming the landing sha (`§6.8a :1309-1313`) | re-anchor on the sha it names |

### 6.3 `release_notes`

**User story.** "Write the changelog for v0.5.1" / "what landed since the
last tag?" — a maintainer wants a release section: bullets grouped by effect
on a user, each supported by the landing units in the range, with changed
public signatures called out.

**Inputs.** One *ref range* `(A, B]` on the base's first-parent line — two
tags, a tag and the base tip ("since the last release"), or two landing
shas (§2) — and the base branch name. The range must be inside the
retention window. With the base checked out at the newer tag the default
`since_tags: 2` already covers any window between consecutive release tags
(the window is strictly after the third-newest tag, O15 `:2076-2085`); the
eval overlay pins `tag_pattern` and `max_landings` for reproducibility and
widens `since_tags` only when a record's `A` is older than the third-newest
tag (§8).

**Tool trajectory.**

1. `get_overview(branch=<base>)` — the base branch card with the **landed
   listing** block (§6.9 G1): every landing unit in the window with sha7,
   `landed_at`, subject, and its tag-window position, newest first (the
   `git log` order). The agent selects the units strictly after `A` up to
   and including `B`. Until that block lands, the fallback is `read_file` /
   `grep` on the changelog file (removed from the corpus on the eval path,
   R10) and `get_why` on commit-mined decisions.
2. `get_overview(branch=<sha>)` per selected unit — the landing card: kind,
   subject, files changed, hunk count and truncation, `merge_evidence`.
   Cost: one call per unit; the range v0.4.1..v0.5.0 on this repository is
   48 units, v0.5.1..`origin/main` is 47 (first-parent counts on
   `origin/main`, measured 2026-09-04; 238 first-parent steps in total, the
   multi-branch spec's own figures). `token_budget` bounds each card.
3. `search_codebase(query=<subject or effect>, scope="diff", branch=<sha>)`
   per unit whose subject does not say what it did — the hunks; and
   `grep(pattern=<symbol>, scope="diff", branch=<sha>)` to confirm a claim
   about a specific name.
4. `get_symbol(target=<public symbol>, depth="source", branch=<base>)` —
   confirm the post-landing state of a public name for the changed-API
   heading (a unit has no tree; `depth="summary"` carries no signature,
   §1.1 item 3). The removed and added signature lines themselves come from
   the unit's hunks (§6.5).
5. `get_why(query=<release theme>, branch=<base>)` — decisions mined from
   the base's commits, for the headline paragraph.

**Output contract.** One release section: a headline paragraph (optional),
then headings from `ReleaseNotesHeading` in the order `ADDED, CHANGED,
FIXED, REMOVED, CHANGED_API`, omitting empty headings (heading grammar as
§6.1). Each bullet: one user-visible effect, cited as `(<sha7>[, <sha7>…];
<path>[, <path>…])`. Several units may share a bullet; internal churn is
left out — a *churn unit* is one whose diff touches only paths under
`.github/`, lockfiles (`uv.lock`, `Cargo.lock`), or the formatter snapshot
(`complexipy-snapshot.json`); the same path rule defines the units the
eval does not require covered (§7.2). `CHANGED_API` lists added / removed /
signature-changed public names, each cited to a unit. Nothing in the
section is supported by a unit outside the range.

**`TASK_HEAD: release_notes` draft** (909 characters, 227 tokens):

```
Release notes for a range of landing units — the first-parent landings
between two tags, or since the last tag — and the answer is a changelog
section. Enumerate the units first: get_overview on the base branch lists
the landed units in the retention window with their tags; get_overview on a
unit's sha gives its subject, files changed and hunk count. Read the change,
not the tree: search_codebase or grep with scope=diff on the unit's sha; a
unit has no tree, so confirm anything further on the base branch. Group by
effect on a user — added, changed, fixed, removed — one bullet per
user-visible effect; several units may share a bullet and internal churn is
left out. Cite each bullet to its unit sha and the paths it touched, and
list changed public signatures under a separate changed-API heading. Two
failures: one bullet per commit subject copied verbatim, and a bullet no
unit in the range supports.
```

**`HARNESS_TASK_HEAD: ask_your_docs.release_notes` draft** (200 characters):

```
The catalog is already in your prompt; skip orientation calls. The catalog's
branch listing names the base branch to enumerate from. The changelog
section is the answer; skip the example-call snippet.
```

**`HARNESS_TASK_HEAD: external.release_notes` draft** (223 characters):

```
No catalog is pre-injected: orient first — get_overview on the base branch
for the landed listing, then one get_overview per unit in the range. Answers
must be self-contained: every bullet names its unit sha and full paths.
```

**Failure modes.**

| Failure | Symptom | Guard |
|---|---|---|
| Subject-copying | one bullet per unit, subject verbatim | judge criterion "grouped by effect"; `landing_coverage` rewards coverage, not count |
| Unsupported bullet | a bullet citing no unit, or a unit outside the range | `gold_recall(keys=[landing_*])` gives no credit; judge criterion "every bullet supported" |
| Range partly outside the window | the G1 listing stops at the oldest in-window unit and the card states the window's lower bound; the range's oldest units are simply absent (tombstones, `§6.5b :996-1000`) | the head's bullet rule ("no bullet a unit in the range supports"); the eval overlay widens `retain` when a record's `A` is older than the window (§8) |
| A named sha outside the window | `InvalidArgumentError` naming `git.diff_chunks.retain` and `branches pin` (`§6.11 :1547`) | re-anchor on the listing; the head does not prescribe the window |
| Enumeration by 47 card calls before G1 lands | turn budget exhausted | `max_turns` gate sized to the range; G1 is a P2.8 prerequisite for the release_notes gate (§10) |
| Changelog leak on the self-corpus | bullets copied from the indexed changelog, the release commit's message, or a mined decision | R10 leak floor over the three surfaces (§7.2) |
| Weak hunk labels on old units | `@@`-context titles instead of symbols (`§6.5b :968-977`) | the gold is path-level; a changed-API claim is confirmed on the base tree (step 4) |

### 6.4 Dimensions of `change_review`

Each dimension keeps the §6.2 inputs, trajectory, and heads; it changes the
dataset, one required section, and the rubric. No dimension owns a seed
section — the `change_review` head names test gap and doc drift in one
clause each, which is the widest the 1,200-character cap allows, and the
per-dataset rubric carries the rest.

#### 6.4.1 Test gap (`TEST_GAP`)

- **User story.** "Which changed symbols have no changed test?"
- **Inputs.** §6.2 inputs; the change set must be available (`scope=changed`
  on a live branch, or the landing card's file list on a unit).
- **Trajectory.** On a live branch: §6.2 steps 1, 3 (`scope=changed`), 5
  with `direction="callers"` filtered to test paths, 6 with the test globs
  (`**/test_*.py`, `**/*_test.py`, `**/tests/**`). On a landing unit: the
  changed test files are the test paths in the landing card's files-changed
  list (R12); a symbol's test coverage is confirmed with
  `grep(pattern=<symbol>, scope="diff", branch=<sha>, glob="**/test_*.py",
  output_mode="files_with_matches")` over the unit's hunks; callers under a
  test path come from `get_references(direction="callers")` on the base
  branch. Steps 3 and 6 with `scope=changed` apply to live branches only
  (a unit answers `diff` alone, `§6.5b :941-944`). The test-path predicate
  the eval side already ships (`_bug_loc_gold.py:53-85` `is_test_path`) is
  the gold's notion of "test", and the trajectory's globs must agree with
  it — a §12 test pins that every path `is_test_path` accepts matches one
  of the three globs.
- **Output.** The `TEST_GAP` section is required and non-empty or an
  explicit "none": each entry `path · symbol → <test path>` — a changed
  non-test symbol whose callers under a test path did not change and whose
  test file is absent from the change set, followed by the repo-relative
  path of the test file that should exercise it (§6.2 output contract).
  Gold recall for this dimension is over the expected test paths (§7.1).
- **Failure modes.** Naming a symbol whose test *did* change (the `grep`
  step guards); treating a renamed test as missing (the change set follows
  renames, `§6.5 :741`); naming the changed symbol without the test path
  (scores nothing, §7.1).

#### 6.4.2 Conflict pre-check (`CONFLICT_PRECHECK`) — card-gated

- **User story.** "Will this branch conflict with what landed on main since
  I branched?"
- **Inputs.** The branch's change set and the **base-side change set**
  `changed_files(mb, base_tip)` — which the surface does not expose: base-side
  changes since the merge-base are deliberately never indexed (`§6.5
  :731-739`), and `scope=changed` on the base branch itself returns the
  *unpushed* commits, not the base since the merge-base (`§6.5 :744-750`).
- **Routing (G5).** A branch-card block "files changed on the base since
  the merge-base, overlap N" computed at the `MergeBaseRecheckJob` from
  `changed_files(mb, base_tip)`, YAML-capped; it needs the base tip read,
  which the job already does (`§6.5 :751-770`). Until the block lands the
  dimension does not ship; nothing in the head refers to it.
- **Trajectory.** §6.2 step 1 (the card block gives the overlap list), then
  `search_codebase(scope="diff")` on the branch for the overlapping paths,
  `read_file(branch=<base>)` on the same paths for the base's side.
- **Output.** A `FINDINGS` entry per overlapping path, with the symbol both
  sides touched when it is the same one.

#### 6.4.3 Documentation drift (`DOC_DRIFT`)

- **User story.** "Which changed symbols have docs that did not change?"
- **Inputs.** §6.2 inputs; MENTIONS capture must be on (`reference_graph.
  capture.kinds` adds `mentions`, `defaults/default_config.yaml:80`, off by
  default), or the fallback route below.
- **Trajectory.** For each changed symbol: `get_references(target=<symbol>,
  direction="callers", branch=<name>)` and keep items whose `path` ends in
  `.md` (MENTIONS edges surface as callers; there is no `mentioned_by`
  direction and adding one is a parameter-schema change, `mcp_inputs.py:433`
  `DirectionLiteral`); or, without MENTIONS, `search_codebase(query=<symbol
  name>, kind="docs", branch=<name>)`. Then `grep(pattern=".", glob=<doc
  path>, scope="changed", branch=<name>, output_mode="files_with_matches")`
  — did that doc change (a non-empty result means yes; §6.2 step 8) — or
  read the doc path off the card's files-changed list.
- **Output.** The `DOC_DRIFT` section: `path · symbol → <doc path>` per
  drifted pair; explicit "none".
- **Failure modes.** MENTIONS off and no docs indexed → the section says
  "docs not indexed" (the head's "when docs are indexed" clause).

#### 6.4.4 PR description (`PR_DESCRIPTION`)

- **User story.** "Draft the description for this branch."
- **Inputs.** §6.2 inputs, live branch only.
- **Trajectory.** §6.2 steps 1, 2, 5 (impact for the "risk" paragraph), 7
  (`get_why` for the motivation).
- **Output.** `SUMMARY` expanded to: motivation (from decisions and the
  subject), what changed (from the hunks, by path), risk (the blast
  radius), how it was tested (the change set's test files). `FINDINGS`
  is optional. Scored judge-only (§7.1); no localization gold beyond the
  change set.

### 6.5 API-surface diff (a `release_notes` section)

- **User story.** "What public signatures changed between v0.5.0 and
  v0.5.1?" / "does this branch break any public API?"
- **What the surface gives.** Between two *branches* after v17 (P1.1 stamps
  `module_members.branch`, program plan P1.1 row): `search_codebase(kind=
  "api", scope="changed", branch=<B>)` lists member rows of the changed
  files, and `get_symbol(depth="source", branch=<B>)` vs the same on the
  base gives the text to compare — a client-side comparison, the same shape
  the UI's graph compare overlay uses (UI spec §6.11). Between two *tags*
  neither ref has a tree until P3.3 (`§10 P3 :2028`), but the units' hunks
  already carry the removed and added `def` / `class` lines (hunk text is
  the `+`, `-`, and context lines, `§6.5a :803-804`), so `CHANGED_API` is
  derivable at S2b per unit from `grep(pattern="^[-+]\s*(def|class) ",
  scope="diff", branch=<sha>)` (or `search_codebase(scope="diff",
  branch=<sha>, query="def ")`), confirmed post-landing with
  `get_symbol(depth="source", branch=<base>)`; P3.3 upgrades it to an exact
  pre/post comparison of per-branch `module_members` rows through the G6
  card block.
- **Routing (G6).** The branch card's "symbols changed" block (R12
  `:322-323`) is specified as three lists — added, removed, signature
  changed — derived from the per-branch `module_members` rows keyed by the
  branch (v17), capped by YAML (§8). With P3.3 a tag becomes a tree-indexed
  row and the same block answers a tag pair through the card of the newer
  one with `base` set to the older.
- **Output.** `ReleaseNotesHeading.CHANGED_API` entries: `<qualified name>:
  added | removed | signature changed (<old> → <new>)`, each cited to a unit
  (tag range) or to the branch and path (branch comparison).

### 6.6 Regression localization (`bug_loc` over a ref range)

- **User story.** "Which landing between v0.5.0 and main broke X?"
- **Placement.** The answer is the `bug_loc` shape — the paths that must
  change — plus the landing unit that introduced the regression. It stays
  under `bug_loc`: the head's "walk outward with get_references before
  answering" and "read every candidate" are the moves needed; the unit is
  one more gold key (`extra["landing_sha"]`) that `gold_recall(keys=
  ["landing_sha"])` scores (`checks.py:378-396`, keys over `file_set` and
  `extra` string values).
- **Inputs.** A bug report and a ref range on the base; the base branch
  indexed at `B` (the range's upper ref), units of the range in the window.
- **Trajectory.** The `bug_loc` trajectory on the base branch (lexical
  route on the report's literals, `get_references` outward), then per
  candidate symbol `grep(pattern=<symbol>, scope="diff", branch=<sha>)`
  over the range's units (from the landed listing, G1) to find the unit
  whose hunks touched it; `get_overview(branch=<sha>)` to confirm its
  subject and files.
- **Output.** The `bug_loc` path list, one per line, followed by one line
  `landing: <sha7>`. The `bug_loc` seed head never mentions landing units,
  and it cannot change without re-keying `bug_loc`, so the record's query
  text carries the output scaffold — task rendering owns the scaffold
  (platform spec §5.5 item 1): "… name the files that must change, one per
  line, and end with `landing: <sha7>` naming the landing unit that
  introduced it" (§7.3, AC-16).
- **Dataset.** `pydocs-self-landing-loc` (§7.3), minting
  `pydocs-self-landing-loc/bug_loc/<record_id>`.

### 6.7 The widening event

The event is enumeration-only, the fourth/fifth/sixth precedents' shape
(`description_source.py:131-172`): `change_review` and `release_notes`
match `TASK_HEAD: [a-z_]+` and `HARNESS_TASK_HEAD: [a-z_]+\.[a-z_]+`
(`_HEADER_RE`, `:173-176`), so the grammar regex and `RENDERER_VERSION`
(`:50`) do not move. Hygiene held on 2026-09-04: `=== TASK_HEAD:
change_review ===`, `=== TASK_HEAD: release_notes ===`, and the four
`=== HARNESS_TASK_HEAD: {ask_your_docs,external}.{change_review,release_notes}
===` lines have zero hits across tracked files.

**Exact tuple.** `TASK_NAMES = ("repo_qa", "vuln", "bug_loc",
"change_review", "release_notes")` — APPENDED, in that order, so the three
existing `TASK_HEAD:` sections keep their position in the seed and the
enumerated-set error messages read in declaration order
(`skill_artifact_loader.py:63-70`). Derived: `TASK_HEAD_SECTION_HEADERS`
becomes a 5-tuple, `HARNESS_TASK_HEAD_SECTION_HEADERS` a 10-tuple
(harness-major: all five `ask_your_docs.*` then all five `external.*`),
`SKILL_ARTIFACT_HEADERS` sixteen keys. `HARNESS_NAMES` is untouched.

**Seed sections.** The six drafts of §6.2 and §6.3 are appended to
`search_guidance_seed.md` in canonical order — the two `TASK_HEAD` sections
after `TASK_HEAD: bug_loc`, the two `ask_your_docs.*` heads after
`ask_your_docs.bug_loc`, the two `external.*` heads after
`external.bug_loc` — each ending in exactly one newline so the seed stays a
canonical byte surface (`tests/harness/core/test_skill_artifact_loader.py:208-214`).
A comment block ("A seventh event", dated the day the widening commit
lands) is appended to the regex comment stack
(`description_source.py:119-172`) recording the hygiene grep.

**Token budgets.** No budget change: the six drafts measure 1,128 / 909 /
237 / 200 / 247 / 223 characters against the 1,200-character caps
(`PER_TASK_HEAD_TOKEN_BUDGET`, `PER_HARNESS_TASK_HEAD_TOKEN_BUDGET`). The
`change_review` head is the closest to its cap (72 characters of headroom);
a revision that wants `CONFLICT_PRECHECK` named in it must drop a clause or
re-argue the platform spec §5.3 rationale for the cap.

**Tests that move in the same commit** (they pin the enumerated set by
design):

- `tests/harness/core/test_skill_artifact_loader.py:81-92` (the tuple and
  the 3-tuple of headers → 5), `:95-123` (the ten canonical keys → sixteen,
  renamed accordingly), `:73-78` and `:144-152` (error messages spell the
  five-name list), `:182-192` (the two new `TASK_HEAD` texts carry no
  harness-local facts), `:194-206` (caps).
- `tests/harness/ask_your_docs/test_binding.py:187-215` — the ask
  `delivery_map_digest()` literal MOVES (the map derives from `TASK_NAMES`,
  `harness/ask_your_docs/binding.py:89-103`); regenerate and record the
  cost paragraph below. `:218-220` and
  `tests/harness/ask_your_docs/test_tool_binding.py:83` iterate the names —
  extend to five.
- `tests/harness/external/test_binding.py:113-119, :141` — the external
  delivery map is pattern-keyed (`harness/external/binding.py:147-182`), so
  its digest does NOT move; the test stays as a negative pin.
- `benchmarks/tests/datasets/test_task_ids.py` — one new case in the
  "joining the vocabulary bites only a repo named X" pattern (`:151-160`):
  no shipped id has `change_review` or `release_notes` as its middle
  segment (verified by grep before minting).
- `benchmarks/tests/optimize/test_bug_loc_arms.py:45-48` and
  `test_repo_qa_arms.py:34-37` assert `known_task_names() == TASK_NAMES`
  live — unaffected; the two new sibling arm configs get sibling test files.
- `benchmarks/tests/optimize/candidates/test_firewall_parity.py:407-419`
  pins that the eval side re-encodes no header set and no budget —
  unaffected, and the reason no eval-side constant is edited.
- `tests/fixtures/goldens/mcp_registration_surface.json` — unaffected: a
  task-name widening changes no MCP surface.

**Goldens.** The `search_skill` seed fingerprint moves (the seed gains six
sections); the ask objective and arm hashes move through the delivery-map
digest; the external arm golden and the synthetic `arm_fingerprint` golden
stay unmoved (verify by execution, the third-framing precedent, run-contract
§5 item 9).

**Recorded cost.** The widening re-keys every ask objective hash and every
ask arm hash. Zero committed ledger rows carry the old ask arm hash: no
ledger is tracked under `benchmarks/` (`git ls-files benchmarks/results`
is empty), and the twelve real trajectories the instrumentation phase
recorded are ADR 0011 fixtures under
`benchmarks/tests/trajectory/fixtures/trajectories/real`, keyed by
`artifact_hash`, not by an ask arm hash — they are unmoved. The old ask
delivery digest appears in tracked files only in
`tests/harness/ask_your_docs/test_binding.py`, which regenerates (AC-5).
The widening must land **before** any `change_review` / `release_notes` task id
is minted, because `parse_framed_task_id` is vocabulary-anchored and
`record_id_of` falls back to the whole id for an unknown middle segment
(`task_ids.py:81-127`).

**Dated amendments this event adds elsewhere.** Run-contract §5 gains "the
fourth and fifth framings" (this section, condensed); the platform spec
§5.2 gains its dated blockquote; `CHANGELOG.md` gains an Unreleased entry.

### 6.8 UI touchpoints

The UI spec's three screen states (`§6.7 :746-812`) and follow-up chips
(`§6.9 :853-892`) are the product entry points to these tasks; they are
read here, not changed, except for one proposed chip.

- **State 1 (default view).** Nothing task-shaped is visible. A user types
  "review this branch"; the interceptor sends the session's scope defaults
  (`ask_your_docs.scope`, UI spec §7 `:1122-1177`); on U2 with
  `slice: diff_hunks` as the default the agent's `search_codebase` /
  `grep` calls carry `scope=diff`, which is the §6.2 step 2. The answer
  footer shows `meta.branch` and the slice segment.
- **State 2 (scope defaults panel).** The Slice radio *whole branch /
  changed files / diff hunks* (U2) is the user-facing spelling of the §6.2
  input table's slice column; the Branch selectbox's "merged" group (U2,
  §6.10) pins a landing sha with `scope=diff`, which is the landing-unit row
  of that table.
- **State 3 (pin active).** Under a kept pin the interceptor overwrites
  `branch` and slice on every call (UI spec §6.3 PIN column, R5(c) there);
  a `change_review` head that named a branch would be silently overridden,
  which is why R11 forbids it. Under a kept pin whose slice is *whole
  branch*, the head's slice instruction is overridden by design (the pin is
  hard, UI spec D5) and the answer footer shows the slice that ran; the
  `slice_consulted` check then scores the trajectory, not the head (R11).
  A `release_notes` question under
  a pin on one unit sees only that unit — the head's enumeration step needs
  the base branch, so the UI's natural entry is a pin on the **base** with
  the question naming the range.
- **Follow-up chips.** `show the diff` (U2) is the chip entry to
  `change_review`: it re-asks under a one-shot pin with `slice =
  DIFF_HUNKS`; `compare with <base>` (U1) is the two-cell entry to the
  API-surface comparison of §6.5 — the fan-out answers per cell and the
  agent compares. **Proposed (routed to the UI spec):** a fourth
  `FollowUpKind.RELEASE_NOTES` chip, derived when the answered cell is the
  base branch and the listing carries at least one landing unit in the
  window, sending the canned question "Write the release notes for the
  landings since the last tag" under a one-shot pin on the base. It is one
  enum member and one table row in UI §6.9; the chip cap ("the count of
  `FollowUpKind` members") rises to four with it.
- **Task-head selection in the shipped UI.** The chat page never folds a
  task head (§1.2). Whether the shipped UI should fold `change_review`
  behind the `show the diff` chip is a YAML tunable
  (`ask_your_docs.task_head`, §8) and an open decision (§13 O5) — never an
  MCP parameter. Until decided, task heads reach models through arms only,
  and the UI relies on the backbone plus the P1.13 / P2.3 tool descriptions.
- **Vocabulary alignment (G14).** The UI spec §2 reads the tombstone's
  `merged_into` as the landing sha (`:172-178`) while the multi-branch
  second pass keeps `merged_into` = base name and adds `landing_sha` (O18
  `:2098-2104`, §6.1). This document uses `landing_sha`; the UI text needs
  the one-word correction (flagged, not owned here). The same correction
  list carries the UI spec §6.12 "Dormant code" wording (`:1073-1083`): the
  plain-English rule reads *inactive*, and this document says
  "inactive-code pattern" (§11).

### 6.9 Surface gaps and where they route

Every gap below is routed to a card block or a YAML key. None is a
parameter. Card blocks are amendments to the multi-branch spec R12 / §6.5a
(that document owns cards); this document states what the task needs.

| Gap | What the task needs | Route |
|---|---|---|
| **G1** No enumeration of landing units between two tags | `release_notes` step 1, `bug_loc` ref range | **Base-branch card "landed" block**: in-window units ordered by `landed_at` descending (newest first, the `git log` order), each `sha7 · landed_at · subject · <tag before> → <tag after>`; truncated at `git.branches.card.landed_listing_max` (§8) with the truncation and the window's lower bound stated on the card. The plain `get_overview()` listing gains the same block under the base's line. |
| **G2** No impact of a diff / on a unit | `change_review` blast radius on a landed unit | N symbol-anchored `impact` calls on the base branch after landing; P3.3 for the exact landed state. Stated in the head. |
| **G3** `impact` / `context` return empty `items[]` (`lookup_service.py:372-373, :488`) | evidence checks cannot credit blast-radius calls | Judge criterion only; a `slice_consulted` sibling `graph_consulted` check counts `get_references` calls with `direction="impact"` from `server_events.jsonl` args (§7.5). Adding `items[]` rows to `impact` is a contract event and is **not** proposed. |
| **G4** Hunk `qualified_name` unspecified | chaining a hunk hit into `get_references` | R7: amend §6.5a to state `qualified_name` = enclosing symbol's dotted name. |
| **G5** Base-side change set for the conflict pre-check | `CONFLICT_PRECHECK` | Branch-card block "changed on the base since the merge-base: N files, overlap M" from `changed_files(mb, base_tip)` in the `MergeBaseRecheckJob`; `git.branches.card.base_side_changes: true|false` (§8). Dimension ships when the block does. |
| **G6** No structured signature diff | `CHANGED_API` | Branch-card "symbols changed" block specified as added / removed / signature-changed lists from per-branch `module_members` (v17), cap `git.branches.card.symbols_changed_max`. Tags need P3.3. |
| **G7** Tags have no tree | `CHANGED_API` between tags, graph on a landed state | `CHANGED_API` is hunk-derived at S2b (§6.5); P3.3 (R20) for the exact comparison and for the graph on the landed state. |
| **G8** No `changed` flag in `items[]` | knowing whether a hit is in the change set | The agent re-queries with `scope=changed`; no field. |
| **G9** Per-unit cards only | 47 cards for one release | G1's block carries subject and files-changed counts so most units need no card; `token_budget` bounds each card; `max_turns` sized per range in the arm config. |
| **G10** `meta` lacks the selected head sha / merge-base | reproducibility of a review | Card text; trace header `branch` / `head_sha` at P2.5 (R22). No envelope field. |
| **G11** MENTIONS capture off by default | `DOC_DRIFT` | YAML `reference_graph.capture.kinds: [calls, imports, inherits, mentions]` in the eval overlay; the `kind="docs"` fallback otherwise. |
| **G12** Eval corpora are history-less | every branch/diff task | A history-preserving corpus mode on the dataset side (§7.6). |
| **G13** `applies_to` keys on the dataset prefix, not the task name | per-dimension checks | One dataset per dimension (kept: dimensions are datasets); the `applies_to_task` field the run-contract taxonomy amendment reserves is not needed. |
| **G14** UI text reads `merged_into` as the landing sha; "Dormant code" | vocabulary | UI spec correction to `landing_sha` and to "inactive code" (§6.8). |
| **G15** No path-level exclusion of hunks or tree files (`exclude_dirs` takes directories only, tool-contracts §4.1; `git.diff_chunks` has no path key) | the R10 leak floor on the self-corpus | **Closed on the dataset side**, no YAML key: the self-corpus is materialized from a history rewrite that removes `CHANGELOG.md` from every commit (§7.2, §7.6). A `git.diff_chunks.exclude_paths` key was considered and is not proposed — it would clean the hunks but leave the file in the tree at the newer tag, where `read_file` reads it. |

---

## 7. Evaluation

Every task follows run-contract §5: datasets are registered loaders that
mint `EvalTask` rows with `mint_framed_task_id(dataset, task_name,
record_id)` (`task_ids.py:51-78`), gold is `GoldAnswer(ast_body, file_set,
extra)` (`datasets/base_dataset.py:24-31`), rows with underivable gold are
dropped loudly (`bug_localization.py:320-333, :379-400`), splits are
record-keyed, and the rubric is one declared section per objective
(`optimize/run_config.py:402`, `_configured_rubric_sections`).

### 7.1 `change_review`

**Framing.** Given one change under review (a branch or a unit in a
repository that carries its history) and a review request, produce the
§6.2 review. Retrieval track: given the request, rank the change's hunks.

**Dataset sources** (download-not-commit, the swe-qa-pro / crosscommitvuln
precedent; no hosting-service names in prose):

| Dataset (registered name) | Records | Dimension | Corpus |
|---|---|---|---|
| `pr-review-py` | per §13 O9: one published pull-request-review corpus pinned by academic citation whose records carry base sha, head sha, the merged diff, and reviewer comments anchored to file and line; Python-slice only (the reference graph is Python-only, `tool_router.py:76-87`). The loader pins revision and row count in code (`ParquetPin` with `expected_rows`, the `bug_localization.py:116-121` precedent); no hosting service is named in prose | `BLAST_RADIUS` | **S1 shape**: repository with `.git` at `base_sha`, the branch built by applying the record's merged diff as ONE synthetic commit on `base_sha` (`git apply` plus a commit carrying the record's title) and named `extra["branch"] = "review/<record_id>"` — head refs are never fetched; the branch indexed → its `DIFF` slice |
| `swe-bench-verified-test-gap` | SWE-bench Verified rows: fix `patch` as the change, `test_patch` as the tests that changed (`_bug_loc_gold.py:97-124` parses both; `trajectory/gold_diff.py:94-106` asserts their disjointness when the `GoldPatch` is built) | `TEST_GAP` | **S0 shape**: history-less corpus at the base commit, `patch` rendered into the prompt, `extra["branch"]` empty. **S1 shape** (same records, same ids): base commit with `.git`, a synthetic branch `change/<instance_id>` carrying `patch` only, overlay `git.branches.track: [change/<instance_id>]` |
| `pr-review-py-doc-drift` | the `pr-review-py` rows whose diff touches a symbol mentioned in an indexed `.md` (derived at build time from the corpus's docs, MENTIONS on) | `DOC_DRIFT` | as `pr-review-py`, overlay `mentions` on |
| `pr-review-py-description` | the `pr-review-py` rows with a merged description body | `PR_DESCRIPTION` | as `pr-review-py` |
| (v1 excluded) | conflict pre-check | `CONFLICT_PRECHECK` | needs G5 |

Because the synthetic branch of `swe-bench-verified-test-gap` carries
`patch` only, every changed symbol is a gap by construction; the dataset
measures whether the agent names the right test file for each gap, not
whether it detects one — stated in the README subsection. A withheld-subset
variant (carry `patch` plus all but one `test_patch` file, gold = the
withheld file's targets) would measure detection; it is not proposed in v1.

**Gold.** `file_set` = the paths carrying non-trivial review findings
(`BLAST_RADIUS`), the `test_patch` paths — the test files the `TEST_GAP`
arrow must name (`TEST_GAP`), the doc paths that should have changed
(`DOC_DRIFT`), or the change set (`PR_DESCRIPTION`); for `TEST_GAP`
additionally `extra["changed_<i>"]` = the non-test `patch` paths, the change
set the answer must enumerate. `extra["finding_<i>"]` = finding text (judge
reference); `extra["symbol_<i>"]` = the enclosing symbol of finding *i*,
from a new `gold_diff.enclosing_symbols(patch) -> dict[path,
tuple[(line_range, symbol), ...]]` that reads each hunk's `@@ … @@
<context>` function label (`target_line_map`, `gold_diff.py:138-161`,
returns line numbers only and supplies the lines a finding anchors to);
`extra["head_sha"]`, `extra["base_sha"]` (40-hex), `extra["branch"]` = the
synthetic branch name. Lists are encoded `key_0, key_1…` because the
candidate reader takes only string values of `extra`
(`optimize/rubric/gates.py:165-184`; the `cwe_id_<i>` precedent).
`ast_body = None` (file-set relevance branch, `metrics/_relevance.py:98-142`).
Label noise (nits vs defects) is reduced by keeping only comments the
corpus marks as resolved by a code change, when the source carries that
flag; otherwise stated as noise in the README subsection.

**Minting.** `pr-review-py/change_review/<owner__name__pr_number>`;
`swe-bench-verified-test-gap/change_review/<instance_id>` (the record id
is shared with `swe-bench-verified-loc`, so paired statistics can cluster
on it once the split is record-keyed, below). `metadata` = `{repo,
dimension, gold_file_count: "3", changed_file_count: "5", landing_kind}` —
string values throughout (§6.1).

**Splits.** Optimizer: `task_split` today hashes the *task id*
(`optimize/_split.py:24-36`), so
`swe-bench-verified-test-gap/change_review/<id>` and
`swe-bench-verified-loc/bug_loc/<id>` land on independent sides. The
platform rule that every row minted from a record travels with it (platform
spec §5.4 item 2) needs the partition taken over `record_id_of(task_id,
task_names=TASK_NAMES)` — a change to `partition_task_ids`' caller, listed
in §12 with a test that sibling rows over one record share a side. Until
it lands, the two swe-bench framings must not be pooled in one arm. Both
sides non-empty or `partition_task_ids` raises. Retrieval track:
`datasets/_split.py` stratified `dev / test / small_dev / small_test`
(small = 30). Campaign: repo-disjoint dev/val per ADR 0013, new repos only
appended to the committed split config.

**Checks** (rubric section `ask_rubric_change_review`, one new declared
field):

| Check | Kind | Params | Weight | Notes |
|---|---|---|---|---|
| `findings_located` | `gold_recall` | `keys: [file_set]` | 0.3 | fraction of gold files the answer names; under `TEST_GAP` this is recall over the expected test paths |
| `located_by_evidence` | `gold_location_evidenced` | — | 0.3 | trajectory evidence (`trajectory_evidence.py:205-228`); hunk items carry `path`, so `scope=diff` hits count |
| `change_consulted` | `slice_consulted` (**new**, §7.5) | `scopes: [changed, diff]` | 0.2 | at least one `search_codebase` / `grep` call with `scope` in the set |
| `graph_consulted` | `graph_consulted` (**new**, §7.5) | `directions: [impact, callers]` | 0.1 | at least one `get_references` call with the direction (a missing `direction` arg counts as `callers`) |
| `findings_cited` | `answer_regex` (measure, `required: false`) | one `^.+ · [A-Za-z_][\w.]* — ` line under the findings heading | 0 (measured, reported, unweighted) | the findings grammar; the judge scores its truth |
| `sections_present` | `review_headings_present` (**new** gate kind, §7.5), declared as a check with `required: true, fail: 1.0, weight: 0` | `dimension: <ChangeReviewDimension>` | gate | builds its pattern from `REQUIRED_REVIEW_HEADINGS` at call time; fails the sample when a required heading is missing; the one required applicable check each prefix needs for `validate_checks` |
| `used_indexed_tools` | gate | `n: 1` | gate | the external harness can bypass the retriever with a shell `git diff`; this and `change_consulted` are the guards |
| judge criteria | rubric | "findings are real and cited", "blast radius names true callers", "test gap is complete", "review, not tree" | `rubric_weight` | |

`required: false, fail: null` on the measures, `keep_deterministic_on_skip:
true` (the `bug_loc` apportionment argument, run-contract §5 item 7).
`validate_checks` (`checks.py:253-302`) is not called by
`validate_rubric_config` today; the widening wires
`validate_checks(known_task_types=<the arms' dataset names>)` into
`load_run_config` next to the existing `validate_rubric_config` call
(`run_config.py:402-410`), which is what makes a typo in a `weight_by_type`
key a load error (AC-10, §12).

Per-dimension weights use `weight_by_type` keyed on the **dataset prefix**
(`checks.py:184-189` keys on `task_id_prefix`; dimension names are not
task types the validator knows). The dimension → dataset map is the first
two columns of the table above:
`weight_by_type: {swe-bench-verified-test-gap: {findings_located: 0.5,
graph_consulted: 0}, pr-review-py-description: {findings_located: 0,
located_by_evidence: 0, graph_consulted: 0}}` — under `PR_DESCRIPTION` the
deterministic layer is `change_consulted` only and the judge dominates.
Arm configs carry a `surface_stage` note: until P2.3 lands, the S0 / S1 arm
configs set `change_consulted: 0` per dataset prefix (`graph_consulted`
stays — `get_references(direction="impact")` exists on P0; the
prompt-supplied diff is the P0 substitute for the slice, so the head's
"start from the change itself" then means the prompt). This is a dated
rubric change that moves `rubric_config_hash`; the deterministic layer is
apportioned over the checks the surface can satisfy, and the S0 number is
**not comparable** with the S2a number (§10).

**Metrics (retrieval track).** Query = the review request (or a finding's
text for the fine-grained variant); ranking over the branch's `DIFF` slice
(`search_codebase(scope="diff")`) with `hit@k`, `map@k`, `recall@k`,
`ndcg@k`, `mrr` from `metrics/__init__.py`, gold = `file_set`; `k` counts
chunks (run-contract §5 item 5). This track is P2.7's vehicle:
`diff_search.yaml` (BM25 ∥ dense RRF) vs dense-only.

**Gate.** (a) P2.7: `diff_search.yaml` is promoted only if it beats
dense-only on `pr-review-py` `dev` beyond noise and confirms on one `test`
run (`benchmarks/README.md:585-608` ladder). (b) Agent track: the
`change_review` arm on `small_dev` must clear the seed's own score by
`accept_margin 0.02` before `dev`; a candidate head is accepted only on
`dev` beyond noise. (c) Every phase keeps the §6.12 single-branch gate.

### 7.2 `release_notes`

**Framing.** Given a ref range on the base and the request, produce the
§6.3 section. Retrieval track: given a gold bullet, rank the units' hunks.

**Dataset sources.**

| Dataset | Records | Gold source | Corpus |
|---|---|---|---|
| `pydocs-self-releases` (**self-corpus**, proposed) | one record per release window of this repository: `(v0.3.0, v0.3.1]` 6 units, `(v0.3.1, v0.4.0]` 23, `(v0.4.0, v0.4.1]` 4, `(v0.4.1, v0.5.0]` 48, `(v0.5.0, v0.5.1]` 2, and the open window `(v0.5.1, <pinned sha>]` — 47 units at `origin/main` `4fbe32d`; first-parent counts on `origin/main`, 238 in total | the section for `tag_to` read from `git show <tag_to>:CHANGELOG.md` on the original clone — **never from the checkout's file**, whose sections are edited after the tag (the v0.5.0 section carries 16 bullets in the working tree and 15 at the tag). Headings come in three shapes — `## [x.y.z] — date`, `## vx.y.z`, and `## vx.y.z (unreleased)` (the v0.5.0 heading at its tag) — and the parser accepts all three. For the open window the gold is the `## [0.6.0] — Unreleased` section frozen in the sidecar at the pinned sha | this repository, rewritten so that `CHANGELOG.md` is absent from every commit (R10 below), with `.git`, base = `main` at `tag_to`, the default `since_tags: 2` (it covers the window when the base is at `tag_to`; the overlay pins `tag_pattern: "v*"` and `max_landings: 500` explicitly) |
| `changelog-tagged-py` (generic, proposed) | per §13 O9: a committed repository list `benchmarks/data/changelog_tagged/repos.yaml` (`{name, clone_url, license, tag_pattern}` rows, permissive licenses only) of Python repositories with a Keep-a-Changelog file and release tags; download-not-commit | the same derivation, per repository | as above, per record |

**Gold.** `extra["landing_<i>"]` = the sha7 of every unit in the window
the notes must cover — the **coverage key set**: the units the sidecar
aligns to at least one bullet; when the window has no sidecar rows, every
unit that is not a churn unit by the §6.3 path rule (diff touching only
`.github/`, lockfiles, or the formatter snapshot). `extra["bullet_<i>"]` =
the section's bullets (judge reference); `file_set` = the union of non-test
paths of the *aligned* units' diffs (`gold_diff.modified_files`; the
retrieval-track gold); `extra["tag_from"]`, `extra["tag_to"]`,
`extra["tag_from_sha"]`, `extra["tag_to_sha"]` (40-hex, the peeled tag
commits *after* the rewrite), `extra["base"]`. `metadata` carries
`unit_count`, `covered_unit_count`, `churn_unit_count`, `bullet_count`, and
`alignment_rate`, all as strings.

Bullet ↔ unit alignment is **primarily hand-labeled** in a committed sidecar
(`benchmarks/data/self_releases/alignment.yaml`, one row per bullet:
`{tag_to, bullet_index, landing: [sha7, …]}`, about 70 rows across the six
windows). Automatic alignment on a pull-request-number suffix covers almost
nothing here: 121 of the 130 first-parent subjects on `origin/main` carry
a `(#N)` suffix, but only 4 of the 70 release-section bullets cite one
anywhere in their text (11 `(#N)` occurrences file-wide, none on a bullet's
first line), so the automatic rule is a consistency check on the sidecar,
not the alignment path. The README states the measured
`alignment_rate` per window. Windows with no changelog section are dropped
loudly; an open window without a pinned `tag_to_sha` in the sidecar
(`unreleased: {tag_to_sha: <40-hex>}`) is dropped loudly too.

**Leak floor (R10).** The gold section is reachable through three surfaces
of the nine tools: the tree at `tag_to` (`read_file("CHANGELOG.md")`), the
hunks of every unit in the window that touched the changelog (bullets
accumulate under the unreleased heading, so it is not only the release-prep
unit: 3 of 48 units in `(v0.4.1, v0.5.0]` and 10 of 47 in the open window
touch `CHANGELOG.md`), and the mined decisions (`decision_capture.sources`
includes `commit_messages` and `changelog`, and `get_why(branch=<base>)`
replays them). No YAML exclusion closes the first two — `exclude_dirs`
takes directories only (tool-contracts §4.1) and a hunk-path exclusion
would leave the file in the tree (G15). Materialization therefore:

- (a) **rewrites history**: the corpus clone is filtered so that
  `CHANGELOG.md` is absent from every commit, tags re-pointed (git's own
  history filter with an index filter removing the one path; commit
  metadata is untouched, so the rewritten shas are deterministic across
  machines). The gold is read from the *original* clone before the rewrite;
  the sidecar pins the rewritten `tag_from_sha`, `tag_to_sha`, and
  `landing_<i>` shas, and the loader asserts that the rewrite reproduces
  them (the `expected_rows` precedent) — the rewritten shas **are** the
  gold shas;
- (b) sets `exclude_dirs: [docs, benchmarks]` in the overlay — the specs and
  plans narrate the same changes in prose, and `benchmarks/` holds the
  loader, the alignment sidecar, and the fixtures that spell the gold — and
  sets `decision_capture.sources: [adr_files, inline_markers, docs_prose]`
  (dropping `commit_messages` and `changelog`) so `get_why` cannot replay a
  release commit body;
- (c) declares the self-corpus **ask-harness-only** in v1: the external
  harness's engine always grants its own file tools (`Read`, `Grep`,
  `Glob`, `Bash`; `cli_agents/claude_code.py:82`, left in by
  `harness.py:182-200` regardless of `tool_names`), through which
  `git log -p` on the corpus's `.git` reads the release commits' messages —
  §13 O7.

The build-time leak check greps (i) every materialized file, (ii)
`git log --format=%B <tag_from_sha>..<tag_to_sha>` on the rewritten
corpus, and (iii) the indexed `decision_records` rows of the base branch,
for each gold bullet verbatim, and fails the record on any hit;
subject-level paraphrase (a release commit's subject naming the release
theme) is accepted noise, stated in the README. The `crosscommitvuln`
leak-check precedent.

**Minting.** `pydocs-self-releases/release_notes/<tag_to>` (the dataset
segment is slash-free; the record id is the newer tag; the open window's
record id is `unreleased@<sha7 of the pinned tag_to_sha>`, and the record is
re-minted under a new id when the pin moves).

**Splits.** Six records (five closed windows plus the pinned open one)
cannot feed a parity split (one side may be empty and `partition_task_ids`
raises). The self-corpus is declared a **gate corpus**: `split: all` only,
never an optimizer pool. The generic corpus feeds the optimizer with the
standard splits.

**Checks** (rubric section `ask_rubric_release_notes`):

| Check | Kind | Params | Weight |
|---|---|---|---|
| `landing_coverage` | `gold_recall` with the new optional `key_prefix` param (§7.5) | `key_prefix: landing_` — every `extra` key with that prefix; a static `keys` list cannot follow a per-record count, and `keys: null` would count `bullet_<i>` / `tag_*` too (`gates.py:165-184`) | 0.5 |
| `change_consulted` | `slice_consulted` | `scopes: [diff]` | 0.25 |
| `units_enumerated` | `card_consulted` (**new**, §7.5) | `tools: [get_overview]`, `min_calls: 2` | 0.25 |
| `sections_present` | `release_headings_present` (**new** gate kind, §7.5), declared as a check with `required: true, fail: 1.0, weight: 0` | — (at least one `ReleaseNotesHeading`) | gate |
| `used_indexed_tools` | gate | `n: 1` | gate |
| judge criteria | rubric | "grouped by effect, not by commit", "every bullet supported by a unit in range", "changed API complete" | |

`gold_location_evidenced` is deliberately not in this section: over a
48-unit window the union of every unit's paths measures breadth of
enumeration, not release-notes quality, and is structurally near zero.

**Metrics (retrieval track).** Query = one gold bullet; ranking over the
window's units' `DIFF` slices — one `search_codebase(scope="diff",
branch=<sha>)` per unit, the per-unit result lists fused by reciprocal rank
(`rrf_fusion` semantics, `k=60`, the pipeline's own fusion rule), ties
broken by `landed_at` descending; gold = the aligned unit's `file_set`;
`hit@k`, `map@k`.

**Gate.** `release_notes` ships as a benchmark only when G1 has landed
(the enumeration step is otherwise a 47-call loop, §6.3 failure table).
Self-corpus smoke gate: the seed head must reach `landing_coverage ≥ 0.6`
over the coverage key set on the two windows whose sidecar is small and
unambiguous — `(v0.4.0, v0.4.1]` (4 bullets / 4 units) and `(v0.5.0,
v0.5.1]` (1 bullet / 2 units) — before the generic corpus is built; the
other windows are unit-level coverage only until their sidecar rows are
committed. A smoke gate, not a quality claim.

### 7.3 Regression localization (`bug_loc`)

**Dataset** `pydocs-self-landing-loc`: per-bullet rows of the self-corpus
— record = one aligned (bullet, unit) pair phrased as a report ("since
v0.5.0, X no longer …" for `Fixed` / `Changed` bullets); gold `file_set`
= the unit's non-test paths, `extra["landing_sha"]` = sha7 (rewritten, as
§7.2). The dataset **exists only once the alignment sidecar is committed**:
under the automatic `(#N)` rule alone it would have about 4 records (§7.2);
with the sidecar its expected size is the count of aligned `Fixed` /
`Changed` bullets, on the order of 30–40 across the six windows, stated
exactly in the README at build time. The record's query text carries the
output scaffold (§6.6): "… name the files that must change, one per line,
and end with `landing: <sha7>` naming the landing unit that introduced
it" — the `bug_loc` head is untouched. Minting
`pydocs-self-landing-loc/bug_loc/<tag_to>__<bullet_index>`; the arm reuses
`optimize_search_skill_bug_loc.yaml`'s rubric with `gold_recall(keys:
[file_set, landing_sha])`, weights 0.5 / 0.5 as the precedent. Corpus =
the §7.2 corpus (base at `tag_to`, rewritten, with the window's units). No
new task name, no new seed section.

### 7.4 Diff-scoped `vuln` variant

`crosscommitvuln-fix-landing` (proposed): the same vendored records
(`task_id, repo_url, prefix_sha, fix_commit, query, gold, metadata`), corpus
= `fix_commit` itself checked out as the base branch with `retain:
{landings: 1}`, so the one landing unit is `fix_commit^1..fix_commit`.
Units are first-parent steps only (multi-branch §6.5b Definition), so a
record is dropped — loudly, counted, the count in the README subsection —
when `fix_commit` has two parents (the unit would be the merge, wider than
the fix) or when `fix_commit^1 != prefix_sha`. Query = the security question
**scoped to the landing** ("does this landing close a vulnerability, and
which?"). Gold = the record's `ast_body`, `file_set`, `cve_id`,
`cwe_id_<i>` (`_crosscommitvuln_build.py:210-220`); `cve_id_exact` reused.
It is a separate dataset because the v2 `crosscommitvuln` invariant bans
commit and diff vocabulary in queries and materializes history-less
snapshots (`datasets/crosscommitvuln.py:1-12`; `_FRAMING_BANS` in
`_crosscommitvuln_build.py:25-49`). "Landing" is not on that ban list, so
the fix-landing queries pass the existing v2 list **unchanged** — no ban is
relaxed — and the v2 dataset's rows are byte-identical before and after
(AC-17). Minting `crosscommitvuln-fix-landing/vuln/<cve>`. Needs P2.8 and
P3.3.

### 7.5 New check kinds (registrations, not config)

Three check predicates in `optimize/rubric/trajectory_evidence.py`
(`CheckPredicate` signature `(task, trajectory, params) -> float`,
`checks.py:93-98`), reading the `server_events.jsonl` `tool_call` records
exactly as `gold_location_evidenced` does (`trajectory_evidence.py:89-94,
:155-179`):

- `slice_consulted(scopes)` — 1.0 if any `search_codebase` / `grep` call's
  `args["scope"]` is in `scopes`, else 0.0. A missing `scope` arg never
  counts as a slice.
- `graph_consulted(directions)` — 1.0 if any `get_references` call's
  `args["direction"]` is in `directions`. A missing `direction` arg counts
  as `callers` (the contract default, tool-contracts §3.5).
- `card_consulted(tools, min_calls)` — 1.0 if at least `min_calls` calls
  went to the named tools.

Two gate kinds in `optimize/rubric/gates.py`, so that no config file spells
a heading list (R9):

- `review_headings_present(dimension)` — builds the conjunction of one
  lookahead per heading in `REQUIRED_REVIEW_HEADINGS[dimension]` (the §6.1
  grammar) at call time and matches it against the answer.
- `release_headings_present()` — the same over `ReleaseNotesHeading`, any
  one heading sufficing.

One parameter added to an existing check: `gold_recall` gains an optional
`key_prefix` (mutually exclusive with `keys`) selecting every `extra` key
with that prefix, for per-record key counts (`landing_<i>`, §7.2).

They read `args` and `extra`, which the trace and the gold already carry;
no `TrajectoryHeader` field, no `SCHEMA_VERSION` bump
(`trajectory/schema.py:27-29`). The `branch` / `head_sha` header fields
arrive with P2.5 independently.

### 7.6 Corpus materialization: a history-preserving mode

Every loader materializes history-less (`_repo_cache.py:351-372`
`read_checkout_files` → `materialize_corpus`, `bug_localization.py:336-346`).
The branch and diff tasks need `.git` present so the product builds
`branches` rows, merge-base pairs, and landing units. Proposed: one new
function `materialize_corpus_with_history(checkout, *, base_ref,
branch_refs, retain_overlay, remove_paths=())` in `datasets/corpus.py`,
used only by the datasets of this section, producing a directory with
`.git`, the base checked out, and an `AppConfig` overlay path carrying the
`retain` window, `exclude_dirs`, `decision_capture.sources`, and
`git.branches.track` of §7.2. `base_ref` is materialized as the **local
branch** `git.branches.base` (default `main`) created at that commit
(`git checkout -B <base> <base_ref>`) with no remote configured, so the
base tip is the local branch and HEAD is not detached (a detached HEAD is
named `detached-<sha7>` by the product and would not be the base);
`branch_refs` are created the same way and listed in the overlay's
`git.branches.track`. `remove_paths` runs the §7.2 history rewrite before
the checkout (the self-corpus passes `("CHANGELOG.md",)`; the generic
corpora pass their changelog path). Two costs stated: `RepoCache`
worktrees are kept (disk), and the external harness's optimize path has no
per-sample corpus (`settings.workspace` only, run-contract engine amendment
gap 1) — change_review / release_notes arms on the external harness need
one pre-indexed multi-branch workspace per dataset until that contract gap
is closed (P3.3 + ADR 0014 retirement; §13 O7).

---

## 8. Configuration

Every tunable is YAML under `AppConfig`; nothing is a CLI flag or an MCP
parameter. Keys this document *needs* and proposes to their owning blocks:

```yaml
git:
  branches:
    card:                          # proposed R12 blocks (multi-branch spec owns cards)
      landed_listing_max: 100      # G1: landing units listed on the base card, newest first
      symbols_changed_max: 200     # G6: added / removed / signature-changed entries
      base_side_changes: false     # G5: "changed on the base since the merge-base" block
                                   # (reads the base tip, which the re-check job already does)
    track: [checked_out]           # existing (P1.6); eval overlays list the synthetic branches
  diff_chunks:
    retain:                        # existing (§6.5b); eval overlays pin it per record and
      since_tags: 2                # widen since_tags only when a record's `A` is older than
      tag_pattern: "v*"            # the third-newest tag (§6.3)
      max_landings: 500
reference_graph:
  capture:
    kinds: [calls, imports, inherits]   # DOC_DRIFT overlays add `mentions`
  impact:
    max_depth: 3                        # existing; the blast-radius depth
decision_capture:
  sources: [adr_files, inline_markers, commit_messages, changelog, docs_prose]
                                   # existing; the self-corpus overlay drops commit_messages
                                   # and changelog (R10, §7.2)
ask_your_docs:
  task_head: ""                    # proposed, O5: "" = fold no task head (byte identity);
                                   # change_review | release_notes fold that head under the
                                   # backbone for every question of the session
```

Eval-side files (sibling per task name, the `optimize_search_skill_bug_loc.yaml`
precedent `:27-31`): `optimize_search_skill_change_review.yaml` (arms per
dimension dataset, one objective `ask_rubric_change_review`),
`optimize_search_skill_release_notes.yaml` (self-corpus arm as gate-only,
generic corpus arm as optimizer pool). Each declares `task_name` from the
enumeration; `load_firewall` refuses it until §6.7 lands.

---

## 9. Contract guarantees

- **No MCP surface change.** The nine tools, their parameters, and the
  envelope are as `docs/tool-contracts.md` and the multi-branch spec §7
  leave them. This document adds no tool, no parameter, no `meta` field, no
  `items[]` field, and no `direction` value. R7 is a value-semantics
  statement on an existing field.
- **The registration golden does not move** for the widening event; it
  moves only with P1.13 / P2.3.
- **Fold parity is untouched.** `TIER_SEPARATOR` and the fold order
  (`guidance_fold.py:63-84`, `agent.py:239`) are byte-pinned; new sections
  ride the same fold.
- **Task heads never name a branch, sha, or tag** (R11), so a head folds
  identically under the ask interceptor's pins and under the external
  harness; the slice a head names is overridden by a whole-branch pin by
  design and scored on the trajectory (§6.8 State 3).
- **Task ids are three-part** and the middle segment is one of the five
  enumerated names; no dataset mints before the enumeration widens.
- **Rubric keying** stays on the dataset prefix; dimensions are datasets.

---

## 10. Dependencies and staging

| Task | P0 (now, v16) | P1 (selector, v17, retirement) | P2.1 changed | P2.2 diff | P2.4 cards | P2.8 units | P3.3 tree-indexed refs |
|---|---|---|---|---|---|---|---|
| `change_review` (live branch) | working tree only; the diff must be in the prompt; review via `search_codebase` / `get_symbol` / `get_context` / `get_references(impact)` / `grep` / `read_file` over live files (no change set) | any indexed branch as a whole; `get_references` within it | change set (`scope=changed`) → `TEST_GAP` derivable | hunks (`scope=diff`) → step 2, `slice_consulted` | branch card: files changed, symbols changed (G6), base-side block (G5) | — | — |
| `change_review` (landed unit) | — | `branch` selector + sha validator (P1.9; resolves to the unknown-SHA error until P2.8) | — | — | landing card | the unit's hunks; graph on the base after landing | graph on the exact landed state |
| `release_notes` | — (subjects would have to be prompt-supplied) | `branch` selector + sha validator (P1.9) | — | per-unit hunks | landing card; **G1 landed block** on the base card | the units; `CHANGED_API` hunk-derived (§6.5) | exact `CHANGED_API` via G6 |
| `bug_loc` ref range | — | `branch` selector + sha validator (P1.9) | — | `grep(scope=diff)` per unit | G1 | the units | — |
| `vuln` fix-landing | — | `branch` selector + sha validator (P1.9) | — | — | — | the fix unit | pre-fix tree |
| Widening event (§6.7) | **ships on P0** — no surface dependency; gates every dataset above | | | | | | |
| Seed drafts | ship with the widening; the heads name the P2 slices, and on P0 / P1 the `change_review` head's closing sentence routes the agent to the card's file list on the default scope, so no invalid `scope` value is sent (§6.2). The `release_notes` head describes the G1 landed block, which exists only from S2b: on S0 / S1 / S2a the head degrades — enumeration falls back to `get_why` and the changelog file (§6.3 step 1) — and the arm configs for those stages set `max_turns` accordingly; the head ships with the widening so that the widening is one event, and this degrade is recorded here rather than by shipping an empty section. Step 7 of §6.2 is conditional on the multi-branch spec's O10 (branch-only decision mining) | | | | | | |

**Staged rollout, aligned with the program plan and the UI stages:**

| Stage | Program items | UI stage | What this document ships |
|---|---|---|---|
| S0 | P0 (shipped) | U0 | §6.7 widening + six seed sections; `ChangeReviewDimension`, `ReviewHeading`, `ReleaseNotesHeading`, `REQUIRED_REVIEW_HEADINGS`; the three check kinds, the two heading gate kinds, `gold_recall.key_prefix`; the report `breakout_key` parameter; the record-keyed partition; `validate_checks` wired at load; `swe-bench-verified-test-gap` loader in its **S0 shape** (prompt-supplied diff, history-less corpus, `change_consulted` at weight 0 — the S0 score is not comparable with S2a, §7.1) |
| S1 | P1.1, P1.5, P1.6, P1.8, P1.9, P1.10, P1.13 | U1 (`compare with`, `pin`) | `change_review` on any indexed branch (P1.5 `BranchIndexer` and P1.6 tracking policy are what index a branch other than the working tree); API comparison between two branches (§6.5); `pr-review-py` loader with the history-preserving corpus (§7.6) indexing the synthetic branch; `swe-bench-verified-test-gap` in its S1 shape |
| S2a | P2.1, P2.2, P2.3, P2.4 (+ G4, G6 amendments), P2.7 | U2 (slice controls, `show the diff`) | slice-anchored `change_review`; `slice_consulted` scoring at full weight; the P2.7 gate on `pr-review-py`; `DOC_DRIFT` dataset |
| S2b | P2.8 (+ G1 block), P2.5 | U2 (merged group) | `release_notes` self-corpus gate (hunk-derived `CHANGED_API`); `pydocs-self-landing-loc` (sidecar-gated); `RELEASE_NOTES` chip (UI amendment) |
| S3 | P3.3, ADR 0014 retirement | — | exact `CHANGED_API` between tags (G6); `crosscommitvuln-fix-landing`; graph on landed states; external-harness per-record corpora |

Vocabulary shared by the three documents at every stage: *branch, landing
unit, slice (whole branch | changed files | diff hunks), retention window,
base branch, merge-base pair, cell*. This document adds *ref range,
dimension, finding, bullet*.

### 10.1 Plans

Files: `docs/superpowers/plans/2026-09-04-branch-diff-task-layer-t0-seeds-and-vocabulary.md`, `…-t1-live-branch-datasets.md`, `…-t2-landing-unit-datasets.md`.

Three implementation plans, one per deliverable kind, so the owner's
question "one plan or several" has a definite answer:

- **Plan T0 — seeds and vocabulary (S0).** The §6.7 widening, the six seed
  sections, the three eval-side `StrEnum`s and the heading mapping, the
  three check kinds and two gate kinds, `gold_recall.key_prefix`, the
  report `breakout_key`, the record-keyed partition, `validate_checks`
  wiring, and `swe-bench-verified-test-gap` in its S0 shape. Product plus
  benchmarks; no surface dependency.
- **Plan T1 — eval datasets on live branches (S1–S2a).**
  `materialize_corpus_with_history`, `pr-review-py` and its dimension
  datasets, the two arm configs, the P2.7 gate. Benchmarks only; gated on
  P1.9 / P2.3 landing.
- **Plan T2 — landing-unit datasets (S2b–S3).** `pydocs-self-releases` with
  the history rewrite and the alignment sidecar, `pydocs-self-landing-loc`,
  `crosscommitvuln-fix-landing`, the smoke gate. Benchmarks only; gated on
  P2.8 and G1.

The product widenings this document needs are **not** plans of their own:
G1, G5, G6 become sub-rows of program plan P2.4 / P2.8 and G4 an amendment
of multi-branch §6.5a (O3); the `RELEASE_NOTES` chip and the `merged_into`
/ "inactive code" corrections go to the UI spec's U2 plan (O6, G14).

---

## 11. Acceptance criteria

Widening (S0):

- **AC-1.** `TASK_NAMES == ("repo_qa", "vuln", "bug_loc", "change_review",
  "release_notes")`; `SKILL_ARTIFACT_HEADERS` has sixteen keys in canonical
  order; the packaged seed loads with every section present.
- **AC-2.** Each of the six new seed sections is ≤ 300 tokens by the
  loader's own cap check; the two `TASK_HEAD` sections contain none of
  `ask_your_docs`, `catalog`, `pre-injected`.
- **AC-3.** `normalize(seed) == seed` (canonical byte surface) after the
  append.
- **AC-4.** `_HEADER_RE` and `RENDERER_VERSION` are byte-identical before
  and after; a test greps tracked files for the six header lines at the
  parent commit and finds zero.
- **AC-5.** The ask `delivery_map_digest()` literal is regenerated; the
  external digest is unmoved; the synthetic `arm_fingerprint` golden is
  unmoved; the amendment paragraph states that zero committed ledger rows
  carry the old ask arm hash and that the twelve ADR 0011 real-trajectory
  fixtures (keyed by `artifact_hash`) are unmoved (§6.7).
- **AC-6.** `load_firewall` accepts an arm with `task_name: change_review`
  after the widening and rejected it before (a test on the parent commit's
  tuple via monkeypatched enumeration).
- **AC-7.** `parse_framed_task_id("pr-review-py/change_review/x", …)`
  yields `record_id == "x"`; no shipped task id has either new name as its
  middle segment.

Output contracts and checks:

- **AC-8.** `ReviewHeading`, `ReleaseNotesHeading`, `ChangeReviewDimension`
  are `StrEnum`s and `REQUIRED_REVIEW_HEADINGS` covers every dimension; the
  `review_headings_present` gate builds its pattern from the enum and the
  mapping at call time, and a test asserts that no config under
  `optimize/configs/` spells a heading word from either enum in a regex
  param.
- **AC-8a.** A sweep over two `change_review` datasets with different
  `metadata["dimension"]` values renders one breakout row per dimension
  when `breakout_key="dimension"`, and the existing `qa_type` breakout is
  byte-identical when the parameter is omitted.
- **AC-9.** `slice_consulted`, `graph_consulted`, `card_consulted` are
  registered check kinds and `review_headings_present`,
  `release_headings_present` registered gate kinds; each returns 1.0 / 0.0
  on a synthetic `server_events.jsonl` (or answer) with and without the
  matching call (or heading); a trajectory with no events scores 0.0; a
  `get_references` call with no `direction` arg counts as `callers`; a
  `search_codebase` call with no `scope` arg never counts as a slice;
  `gold_recall(key_prefix="landing_")` scores over exactly the
  `landing_<i>` keys and rejects `keys` given together with `key_prefix`.
- **AC-10.** `validate_checks(known_task_types=<the arms' dataset names>)`
  is called by `load_run_config` and accepts `ask_rubric_change_review` and
  `ask_rubric_release_notes` — each prefix has one required applicable
  check (`sections_present`, `required: true, weight: 0`) and ≥ 1 positive
  weight — and rejects a `weight_by_type` keyed on a dimension name
  (`TEST_GAP`) rather than a dataset prefix.

Datasets and gold:

- **AC-11.** `swe-bench-verified-test-gap` yields rows whose `file_set` is
  exactly the `test_patch` paths, whose `extra["changed_<i>"]` are exactly
  the non-test `patch` paths, whose `extra["symbol_<i>"]` is non-empty for
  every finding whose hunk carries a function context, and whose
  `record_id` equals the `swe-bench-verified-loc` record id for the same
  instance; rows with an empty `test_patch` are dropped with an INFO log
  and counted; a fixture answer whose `TEST_GAP` entries name the
  `test_patch` paths after the arrow scores `findings_located = 1.0`, and
  one naming only the changed symbols scores 0.0.
- **AC-11a.** `pr-review-py` yields ≥ N records (N stated in the loader's
  README subsection at build time, pinned by `expected_rows`) with
  `extra["base_sha"]`, `extra["head_sha"]` 40-hex, `extra["branch"] ==
  "review/<record_id>"`, and ≥ 1 `finding_<i>`; the materialized branch's
  single commit applies cleanly on `base_sha`.
- **AC-12.** `pydocs-self-releases` yields one row per closed tag window
  with a changelog section and one for the open window only when the
  sidecar pins `tag_to_sha`; on every row `git rev-list --first-parent
  <tag_from_sha>..<tag_to_sha>` on the rewritten corpus equals the unit
  list the loader enumerated, the coverage key set `extra["landing_<i>"]`
  is that list minus the churn units by the §6.3 path rule (or the
  sidecar-aligned subset when sidecar rows exist), `metadata` carries the
  counts as strings, and a window without a section is dropped loudly.
- **AC-13.** The leak check fails a self-corpus record when any gold bullet
  appears verbatim in (i) a materialized file, (ii) `git log --format=%B`
  over the window on the rewritten corpus, or (iii) an indexed
  `decision_records` row of the base; after the R10 floor — history rewrite,
  `exclude_dirs: [docs, benchmarks]`, `decision_capture.sources` without
  `commit_messages` and `changelog` — every record of the six windows
  passes, and the gold read from `git show <tag_to>:CHANGELOG.md` on the
  original clone equals the sidecar's frozen section.
- **AC-14.** The changelog parser accepts the three heading shapes
  (`## [x.y.z] — date`, `## vx.y.z`, `## vx.y.z (unreleased)`) and the
  `Added / Changed / Fixed / Removed / Security / CI` sub-headings.
- **AC-15.** `materialize_corpus_with_history` produces a directory with
  `.git`, `git symbolic-ref HEAD` naming `refs/heads/<base>` at `base_ref`,
  no remote configured, and an overlay carrying `retain`, `exclude_dirs`,
  `decision_capture.sources`, and `git.branches.track`; with
  `remove_paths=("CHANGELOG.md",)` no commit of the result carries that
  path and the rewritten tag shas equal the sidecar pins; indexing it with
  the product yields a `branches` row per `branch_refs` entry (P1+) and
  landing-unit rows for the window (P2.8+).
- **AC-16.** `pydocs-self-landing-loc` rows mint under `bug_loc`, carry
  `extra["landing_sha"]`, end their query text with the `landing: <sha7>`
  scaffold sentence (§6.6), and `gold_recall(keys=[landing_sha])` scores
  1.0 for an answer naming the sha7 and 0.0 otherwise; the loader raises
  loudly when the alignment sidecar has no rows.
- **AC-17.** `crosscommitvuln-fix-landing` queries pass the existing v2
  `_FRAMING_BANS` list unchanged (no ban is relaxed); records whose
  `fix_commit` has two parents or whose `fix_commit^1 != prefix_sha` are
  dropped and counted; the v2 `crosscommitvuln` dataset's rows are
  byte-identical before and after.

Trajectories (against a fake server advertising the P2 surface, the UI
spec's inactive-code pattern — "dormant" there, G14):

- **AC-18.** A `change_review` run over a fixture branch with one changed
  symbol and one caller produces, on the seed head, a review whose
  `BLAST_RADIUS` names the caller and whose trajectory contains a
  `search_codebase(scope="diff")` call and a `get_references(direction=
  "impact")` call on the branch (recorded with the fake).
- **AC-19.** The same run over a landing-unit fixture issues
  `get_references` on the base branch, never on the sha (a fake that raises
  on the sha proves it).
- **AC-20.** A `release_notes` run over a fixture base with three units
  and G1's block issues one `get_overview(branch=<base>)`, at most three
  per-unit cards, and produces a section whose bullets cite all three sha7s.
- **AC-21.** With the diff pending (fake returns the `is being generated`
  suggestion), a `change_review` run retries at most once and falls back
  to `scope=changed`.

Gates:

- **AC-22.** The P2.7 report compares `diff_search.yaml` against dense-only
  on `pr-review-py` `dev` with `hit@5`, `map@5`, `recall@10`; promotion
  follows the README ladder and is recorded in the benchmarks README.
- **AC-23.** The single-branch §6.12 gate (RepoQA structural recall) is
  unchanged after every stage of §10.
- **AC-24.** The self-corpus smoke gate of §7.2 (`landing_coverage ≥ 0.6`
  over the coverage key set on the `(v0.4.0, v0.4.1]` and `(v0.5.0,
  v0.5.1]` windows) is a test marked as a local gate, not run by CI,
  documented in the benchmarks README together with the churn path rule
  and the per-window `alignment_rate`.

Documents:

- **AC-25.** Run-contract §5 carries the dated fourth/fifth-framing
  amendment; the platform spec §5.2 its blockquote; `CHANGELOG.md` its
  entry; the multi-branch spec R12 / §6.5a carry the G1, G4, G5, G6
  amendments or the owner's rejection; the UI spec §6.9 carries the
  `RELEASE_NOTES` chip row or its rejection; the UI §2 `merged_into` text
  is corrected to `landing_sha`.
- **AC-26.** No README under the repository references internal
  pull-request history labels after this work (the CLAUDE.md README audit
  grep returns no match).

---

## 12. Testing plan

- **Loader and seed** (`tests/harness/core/test_skill_artifact_loader.py`):
  the AC-1 to AC-4 pins, edited in lockstep; a new parametrized test that
  every `TASK_HEAD` names at least one of the nine tools and contains no
  `branch=` literal followed by a value, no 7–40-hex sha literal, and no
  `v*` tag literal (R11; slice values are allowed).
- **Bindings** (`tests/harness/ask_your_docs/`, `tests/harness/external/`):
  digest regeneration and the negative pin; a fold test that
  `change_review` folds three tiers in both harnesses with the same
  separator.
- **Eval datasets** (`benchmarks/tests/datasets/`): one test module per
  dataset with a fixture parquet / fixture repository; the leak check over
  its three surfaces; the changelog parser on the three heading shapes; the
  alignment sidecar schema; the history rewrite reproducing the pinned
  shas; a test that every path `is_test_path` accepts matches one of the
  three trajectory globs (§6.4.1); `gold_diff.enclosing_symbols` on a
  fixture patch.
- **Checks and splits** (`benchmarks/tests/optimize/rubric/`,
  `benchmarks/tests/optimize/`): the three new check kinds on synthetic
  event logs and the two heading gates on synthetic answers;
  `gold_recall.key_prefix`; `weight_by_type` per dataset prefix and the
  `validate_checks` load-time rejection of a dimension-named key; the
  record-keyed partition — sibling rows over one record share a side.
- **Report** (`benchmarks/tests/reporting/`): the `breakout_key` parameter
  (AC-8a).
- **Trajectory fixtures** (`benchmarks/tests/agent_track/`): a fake MCP
  tool set advertising `branch` / `changed` / `diff` (the UI spec's
  `ScopeCapabilities` fake shape) with scripted answers; AC-18 to AC-21.
- **Arm configs** (`benchmarks/tests/optimize/test_change_review_arms.py`,
  `test_release_notes_arms.py`): every arm declares its own task name; the
  self-corpus arm is `split: all` and flagged gate-only.
- **Gate scripts**: the P2.7 sweep config under
  `benchmarks/configs/` with `DEFAULT_METRIC_SPECS` order; the self-corpus
  smoke gate as a marked local test.
- **CI**: the full documented gate set (`ruff`, `mypy`, `complexipy`,
  `vulture`, coverage ≥ 90 %, lockfile, audit) on every PR of §10.

---

## 13. Open decisions for the owner

- **O1 — The two task names.** Ratify `change_review` and `release_notes`
  as the fourth and fifth framings, with the placements of §6.1. The
  alternative (approach A, seven names) is rejected in §5; the alternative
  spelling `code_review` was not chosen because the task covers landed units
  that are not code under review by anyone, and `changelog` because the
  answer is a section, not the file.
- **O2 — Self-corpus gold.** Ratify CHANGELOG sections (read at the tag,
  frozen in the sidecar) as `release_notes` gold with the R10 floor — a
  history rewrite that removes the changelog from every commit, so the
  gold shas are the rewritten shas — a committed hand-alignment sidecar as
  the primary alignment path (4 of 70 bullets align automatically, §7.2),
  and churn-excluded unit-level coverage where a window has no sidecar
  rows; or restrict the self-corpus to the aligned bullets only (fewer
  units, cleaner gold). Also: is the self-corpus a gate corpus only
  (proposed), or may it join an optimizer pool once the generic corpus
  exists?
- **O3 — Card blocks (G1, G5, G6) as R12 amendments.** Proposed: all three,
  YAML-capped, in P2.4 / P2.8; G1 is a precondition of the `release_notes`
  gate. Alternative: G1 only, and drop the conflict pre-check dimension.
- **O4 — Conflict pre-check in v1.** Proposed: specified, card-gated, not
  shipped until G5. Alternative: drop it from this document.
- **O5 — Task heads in the shipped UI.** Add `ask_your_docs.task_head`
  (YAML, default `""` = byte identity) so the chat page can fold
  `change_review` behind the `show the diff` chip; or keep task heads
  arm-only. Never an MCP parameter.
- **O6 — `RELEASE_NOTES` chip.** Add the fourth `FollowUpKind` member (UI
  spec amendment) or leave release notes to typed questions.
- **O7 — External-harness corpora.** Accept one pre-indexed multi-branch
  workspace per dataset for external arms until the per-sample corpus gap
  closes, or defer external arms for these tasks to S3; and, for the
  self-corpus specifically, accept ask-harness-only in v1 (proposed, §7.2:
  the engine's own file tools reach `.git`) or add a YAML knob that
  withholds the engine's file tools on an arm.
- **O8 — Where the history-preserving corpus mode lives.** `datasets/
  corpus.py` (proposed, next to `materialize_corpus`) or `_repo_cache.py`.
- **O9 — Generic corpora.** `pr-review-py` and `changelog-tagged-py` need a
  pinned source before their loaders exist. Proposed: the owner names one
  published pull-request-review corpus by academic citation (arXiv id)
  whose records carry base sha, head sha, merged diff, and file-and-line-
  anchored comments, pinned in code by revision and `expected_rows`; and a
  committed repository list `benchmarks/data/changelog_tagged/repos.yaml`
  (permissive licenses only). Until ratified, S1's dataset work ships
  `swe-bench-verified-test-gap` only.

Closed since the first draft, recorded rather than asked: the token
budgets (no change; §6.7 measurements) and `TaskName(StrEnum)` (the owner's
R6 keeps the tuple).

---

## 14. References

Documents:

- `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md` — §2
  `:131-178`; R12 `:320-330`; R22 `:335-339`; §6.4 `:685-727`; §6.5
  `:729-782`; §6.5a `:784-848` (hunk text `:803-804`, `kind` ignored
  `:832-833`); §6.5b `:850-1008` (selector `:935-950`, labels `:968-977`,
  retention `:978-1002`, tombstones `:996-1000`); §6.5c `:1010-1075`; §6.6
  `:1077-1105`; §6.7 `:1106-1130`; §6.8a `:1309-1313`; §6.9 `:1449-1523`
  (`diff_chunks` block `:1472-1483`); §6.11 `:1535-1558` (outside-window
  row `:1547`); §6.12 `:1617-1622`; §7 `:1730-1767`; §10 `:2017-2031`; §11
  O2 `:2038-2039`, O5, O10 `:2057-2059`, O11 `:2060-2063`, O15
  `:2076-2085`, O17 `:2092-2097`, O18 `:2098-2104`; Amendments
  `:2170-2305` (companion tasks `:2292-2305`).
- `docs/superpowers/plans/2026-09-03-multi-branch-indexing-program.md` —
  gate `:25-26`; P1 rows `:31-56`; P2 rows `:64-84`; P3.3 `:95`.
- `docs/superpowers/specs/2026-09-04-ask-your-docs-branch-scope-ui-design.md`
  — §2 `:150-196`; R5 `:239-250` (interceptor rules (a)–(d)); §6.3
  `:500-522` (the per-argument table, PIN column); §6.7 `:746-812`; §6.9
  `:853-892`; §6.10; §6.11; §6.12 `:1027-1086` ("Dormant code, three
  plans" `:1073-1083`); §7 `:1122-1177`; §12 `:1498-1536`.
- `docs/superpowers/specs/2026-07-27-harness-run-contract-design.md` — §5
  `:214-235`; third-framing amendment `:339-470` (items 1, 5, 7, 8, 9);
  engine amendment (the per-sample corpus gap).
- `docs/superpowers/specs/2026-07-26-retriever-centric-harness-platform-design.md`
  — §5.2 (tiers), §5.3 item 2 (caps), §5.4 (record-level clustering).
- `docs/tool-contracts.md` — §2.3 (suggestion field), §2.4 (`meta.branch`
  null cases), §3.2 (`items[]` fields), §3.5 (`get_references` rows), §5.2
  (selector litmus test).
- `benchmarks/README.md:585-608` — promotion ladder; `:457-467` — `k`
  counts chunks, metric row order.
- `CHANGELOG.md:8, :244, :254, :365, :397, :471, :480` — release headings
  in the working tree (the gold is read at the tag, §7.2; `git show
  v0.5.0:CHANGELOG.md` line 8 reads `## v0.5.0 (unreleased)`).

Code:

- `python/pydocs_mcp/harness/core/skill_artifact_loader.py:63-70, :71-72,
  :79-81, :99-117, :122-140, :232-250`.
- `python/pydocs_mcp/application/description_source.py:50, :65, :119-176`.
- `python/pydocs_mcp/harness/core/skills/search_guidance_seed.md:1-105`.
- `python/pydocs_mcp/harness/core/guidance_fold.py:24-32, :63-84, :87-141`;
  `harness/core/run_contract.py:48-76, :117`.
- `python/pydocs_mcp/harness/ask_your_docs/agent.py:218-239`;
  `binding.py:89-103, :270-274`; `app.py:83`.
- `python/pydocs_mcp/harness/external/harness.py:105-109`;
  `binding.py:65-69, :147-182`; `cli_agents/claude_code.py:76-82, :120-121`.
- `python/pydocs_mcp/application/reference_service.py:288-339`;
  `lookup_service.py:342-346, :372-373, :488`; `tool_router.py:76-87,
  :184-206`; `mcp_inputs.py:433`; `tool_response.py:40-74`;
  `formatting.py:691-745`.
- `python/pydocs_mcp/extraction/model/document_node.py:83-101`;
  `python/pydocs_mcp/models.py:97, :143, :159`;
  `python/pydocs_mcp/db.py:93-97`.
- `python/pydocs_mcp/defaults/default_config.yaml:80, :105`.
- `benchmarks/src/pydocs_eval/datasets/task_ids.py:51-127`;
  `datasets/base_dataset.py:24-51`; `datasets/bug_localization.py:83-145,
  :175-241, :320-346, :379-400`; `datasets/_bug_loc_gold.py:53-124`;
  `datasets/crosscommitvuln.py:1-12`;
  `datasets/_crosscommitvuln_build.py:25-49` (`_FRAMING_BANS`), `:210-220`;
  `datasets/data/crosscommitvuln/records.jsonl` (record keys);
  `datasets/corpus.py:22`; `datasets/_repo_cache.py:351-372`;
  `datasets/_split.py:20-80`.
- `benchmarks/src/pydocs_eval/optimize/rubric/checks.py:93-98
  (`CheckPredicate`), :104-129, :184-189, :253-302, :378-409`;
  `rubric/gates.py:94-219` (`n` params `:102, :200, :215`; candidate
  reader `:165-184`); `rubric/trajectory_evidence.py:54-62, :89-179,
  :205-228`; `optimize/_split.py:24-60` (task-id keyed today);
  `optimize/load_firewall.py:87-101`; `optimize/ask_binding.py:283-294`;
  `optimize/run_config.py:402-410` (no `validate_checks` call today);
  `optimize/configs/optimize_search_skill_bug_loc.yaml:27-31, :57-142`.
- `benchmarks/src/pydocs_eval/trajectory/gold_diff.py:65-90, :94-106
  (disjointness assertion), :138-161`; `trajectory/schema.py:27-29,
  :100-142`; `metrics/__init__.py`; `metrics/_relevance.py:98-142`;
  `reporting/report.py:31-37` (`_CATEGORY_KEY = "qa_type"`);
  `campaign/index_cache.py:42-118, :148-301`.
- `python/pydocs_mcp/application/file_tools.py:205-213` (grep `glob`
  dialect); `python/pydocs_mcp/application/mcp_inputs.py:45` (`ScopeLiteral`
  today).
- Tests pinning the enumeration:
  `tests/harness/core/test_skill_artifact_loader.py:65-123, :144-214,
  :308-335`; `tests/harness/ask_your_docs/test_binding.py:187-220`;
  `tests/harness/ask_your_docs/test_tool_binding.py:83`;
  `tests/harness/external/test_binding.py:113-141`;
  `benchmarks/tests/datasets/test_task_ids.py:13, :35, :151-160`;
  `benchmarks/tests/optimize/test_bug_loc_arms.py:45-48`;
  `benchmarks/tests/optimize/candidates/test_firewall_parity.py:407-419`.

Measurements (2026-09-04, `origin/main` at `4fbe32d`): first-parent
landings per window 6 / 23 / 4 / 48 / 2 / 47, 238 in total (this feature
branch adds two spec commits — 49 and 240 — which are not counted); units
touching `CHANGELOG.md` per window 1 / 1 / 1 / 3 / 1 / 10; first-parent
subjects with a `(#N)` suffix 121 of 130; release-section bullets 70, of
which 4 cite a pull-request number; tags `v0.3.0 … v0.5.1` interleaved with
`eval-v0.1.0`, `eval-v0.1.1`; the v0.5.0 section has 15 bullets at its tag
and 16 in the working tree; seed usage before widening: backbone 2,173
characters, task heads 990 / 726 / 922, harness heads 115–233; the six new
drafts 1,128 / 909 / 237 / 200 / 247 / 223 characters.
