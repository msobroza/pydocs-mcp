# Ask-your-docs branch scope UI: soft defaults, hard pins, and labeled fan-out — Design

**Date:** 2026-09-04
**Status:** Draft for owner ratification. Encodes the thirteen owner decisions
D1–D13 of 2026-09-04 as requirements R1–R13 (§3). Two requirements restate a
decision with a proposed change pending ratification: R3 spells the shipped
branch default `base` instead of D3's `main` (§12 O3) and R7 gates the
catalog branch listing on the `branch` capability (§12 O5); the plan is not
started until O3 and O5 are answered. No code written. Every claim about
existing code cites a `file:line` anchor at worktree HEAD `4fbe32d`
(multi-branch P0). Toolkit claims are verified against the installed sources
(`streamlit` 1.59.1, `langchain_mcp_adapters` 0.3.0, `langchain_core` 1.4.9,
`mcp` 1.28.1) and the toolkit release notes; §11 lists what each verification
established.
**Owner:** msobroza.
**Companions:** `2026-09-03-multi-branch-indexing-design.md` (the server-side
branch dimension this UI consumes; P0 landed, P1/P2 pending),
`docs/superpowers/plans/2026-09-03-multi-branch-indexing-program.md` (P1/P2
task index), `2026-07-26-retriever-centric-harness-platform-design.md` (the
harness as a consumer of the frozen nine-tool surface), ADR 0008 (session-start
injection — the precedent for "rendered only when on"),
`docs/tool-contracts.md` (frozen surface; this design adds nothing to it).
The owner's amendment of the same date to the multi-branch spec (landing
units, diff retention, squash detection — its §6.5b, §6.5c, §6.8a, and the
matching rows of the program plan) is committed as `1c371bc` on top of
`4fbe32d`. This design cites that revision by **section number only**, so a
later renumbering changes nothing here; stage U1 starts from the committed
text (§11 V5).

**Goal:** Let a person asking the ask-your-docs chat agent about a project with
several indexed branches (a) see nothing about branches until it matters,
(b) set soft session defaults the agent may override when a question calls
for it, (c) pin one question hard to one or several branches with one click,
(d) read on every answer which project, branch and slice it came from, and
(e) compare two branches on the graph page — all without adding a tool, a
parameter or an envelope field to the frozen MCP surface, and without changing
one byte of the tool arguments or the system prompt on today's single-branch
path.

---

## Abstract

Today the chat page pins every question through a sidebar of three pickers
(project / own-vs-dependency / package) that is always visible and always
applied (`app.py:113-143`, `agent.py:121-137`). The branch dimension that P0
just stamped into every bundle (`db.py:171-217`) has no client-side shape at
all: no listing, no selector, no attribution on answers. This design replaces
the always-on sidebar pin with **two kinds of scope**: session **defaults**
(soft — they fill in what the model leaves empty and may be overridden by the
model when the question names another indexed project or branch) and a
per-question **pin** (hard — forced onto every tool call, exactly like today's
sidebar pin). Both are carried by one frozen `QuestionScope` value object whose
`cells` are `(project, branch)` pairs. Under a multi-branch pin, a tool call
that omits `branch` **fans out** client-side — one server call per cell, each
result prefixed with its cell label, structured items merged with a `branch`
field — while a call that names one pinned branch is honored as is; the
system prompt gains one rule that tells the model when each shape is worth it.
The agent architecture (one ReAct loop) is unchanged. Branch names come only
from the bundles' `branches` table through a new read-only
`BundleReader.branches()`, so no unknown name can be typed anywhere. Every
answer carries a footer line built from the tool responses' `meta` and from
the interceptor's own record of which cell it sent, plus deterministic
follow-up chips ("compare with main", "show the diff", "pin feature/retry").
The graph page gets the same defaults, a branch selector and a "compare with"
overlay computed over the reader with no server work. Controls whose
parameter or value the server does not advertise stay hidden, which stages
the work as U0 (now, on P0), U1 (after P1's `branch` parameter) and U2 (after
P2's `changed` / `diff` scope values). The single-cell default path produces
the same tool arguments and the same prompt as today; the eval binding is
untouched; every tunable is YAML.

---

## 1. Context and problem statement

### 1.1 What the chat page does today

- **An always-on sidebar pin.** The sidebar renders Project / Code / Package
  pickers under the connection settings (`app.py:113-143`, choices mapped by
  `_CODE_CHOICES` at `app.py:95`) and a caption "Searches run only inside this
  scope." (`app.py:143`). Each question snapshots them into a plain dict
  (`scope = {"project": …, "package": …, "code": …}`, `app.py:258`) and passes
  it to `ask()` (`app.py:264-273`).
- **A hard interceptor with no soft mode.** `ask()` stores the dict on the
  `_active_scope` contextvar (`agent.py:60-62`, `agent.py:443`);
  `_intercept` forces `project` on every tool, `package` on the two package
  tools (`_PACKAGE_TOOLS`, `agent.py:85`) and `scope` on `search_codebase`
  only when code is not `all` (`agent.py:121-137`). There is no notion of
  "fill in only what the model left empty". Note the comment at
  `agent.py:81-84` still says "all six tools"; nine tools take `project`
  (`server.py:698-852`).
- **The model is told about the pin transiently.** `scope_prefix`
  (`agent.py:140-149`) prepends `[pinned scope: …]` to the question; history
  keeps the bare question (`agent.py:430-435`, `agent.py:465-467`); rule 6 of
  the shipped system prompt explains the note
  (`harness/core/prompts/system_v1.j2:44-49`).
- **Answers carry no attribution.** `ask()` returns a bare string
  (`agent.py:468`); the response envelope's `meta` (`tool`, `project`,
  `indexed_git_head`, `live_git_head`, `index_stale`, `truncated`, `branch` —
  `application/tool_response.py:40-54`) is dropped on the floor by the harness.
- **Attachments already have a chip row.** Symbols attached from the graph
  page (`pages/2_Graph.py:262-266`) render as removable `✕ name` buttons with a
  "clear all" (`app.py:173-183`) and are woven into the question one-shot
  (`app.py:259-260`, `attachments.py:128-137`).

### 1.2 What P0 stamped and what P1/P2 will add

- P0 (landed): every project index pass stamps exactly one row into
  `branches` — the checked-out working-tree branch, or `no git`
  (`models.py:41`) — plus `branch_files`, `branch_chunks` and
  `file_extractions` (`db.py:171-217`, schema v16); `meta.branch` rides on
  every response from the FIRST loaded bundle's default row
  (`server.py:487-496`, `storage/factories.py:904-915`,
  `application/freshness.py:85-90`). No tool takes a `branch` argument
  (`server.py:698-852`; `mcp_inputs.py:44-48` has three `scope` values).
- P1 (pending): `branch: str = ""` on all nine tools, accepting an indexed
  name or a 7–40-hex landing sha (multi-branch spec §3.2 Q5, §7 item 2, §10;
  plan P1.9 `:41`), schema v17 with a `branch` column on the tree-tier tables
  (plan P1.1 `:33`), the tracking policy that **populates `branches.base_name`**
  (plan P1.6 `:38` — the P0 `BranchRecord` is built without it,
  `application/branch_membership.py:92-101`, so the `base` default of §6.2 and
  the `compare with <base>` chip of §6.9 resolve to nothing before P1.6),
  unknown / retired branch errors (spec §6.11), retirement tombstones with
  `merged_into` = the landing sha (spec §6.8a; plan P1.7 `:39`).
- P2 (pending): `scope="changed"` and `scope="diff"` on `search_codebase` and
  `grep` only (spec §6.5, §6.5a, §10; plan P2.1–P2.3 `:64-66`), and the
  landing-unit index that keeps a merged branch's diff addressable as
  `branch=<landing sha>` with `scope=diff` (spec §6.5b; plan P2.8 `:71`).

The server-side non-goal stands: `branch=""` never fans out across branches
server-side (multi-branch spec §4 non-goals). Fan-out is therefore a
**client** concern, and the adapter's interceptor contract sanctions it ("the
handler can be called multiple times",
`langchain_mcp_adapters/interceptors.py:119-120`).

### 1.3 Why the sidebar pin does not scale to branches

A pin that is always visible and always applied is right for "which project"
(one choice, rarely changed) and wrong for "which branch": the question decides
it ("does the retry change break the client?" needs two branches; "how does
routing work?" needs none), a picker that is always on makes every answer
implicitly branch-scoped whether or not the user noticed, and a multiselect in
a sidebar has no natural per-question lifetime. The owner's decisions separate
the two lifetimes (session defaults vs per-question pin) and hide both until
they are needed.

---

## 2. Terms

- **Scope defaults**: session-level soft values (project, branch, slice, code,
  package) that fill in arguments the model leaves empty. The model may name
  another indexed project / branch / slice when the question calls for it.
  Shipped values come from YAML (§7); the sidebar panel overrides them for the
  session only.
- **Pin**: a per-question hard scope forced onto every tool call of that
  question, exactly like today's sidebar pin. A pin is either **one-shot**
  (cleared when the question is sent, before the answer arrives — §6.7 "Pin
  lifecycle") or **kept** ("keep for next").
- **Question scope**: the frozen `QuestionScope` value object (§6.2) that
  carries either the defaults (`kind = DEFAULT`) or a pin (`kind = PIN`) for
  one question.
- **Cell**: one `(project, branch)` pair. A question scope has one or more
  cells; a fan-out issues one server call per cell.
- **Slice**: which part of a branch a search covers — the whole branch, the
  files the branch changed (`scope=changed`, P2) or the diff hunks themselves
  (`scope=diff`, P2). Distinct from **code** (all / own / deps), which is
  today's own-vs-dependency filter; the server carries both in one `scope`
  vocabulary (§6.3).
- **Base branch**: the branch a diff is computed against — the bundle's
  `branches.base_name` for the selected branch (`db.py:174`), the server's
  own notion (multi-branch spec §3.3 R14). The column exists in v16 but is
  NULL until P1.6 stamps it (§1.2). The UI labels it "main (base branch)".
- **Landing unit**: one first-parent step `c` on the base branch with the diff
  `c^1..c` — a merged branch's retained diff (multi-branch spec §2 Terms,
  §6.5b, amended 2026-09-04). In storage it is a `branches` row whose `name`
  is the full 40-hex landing sha, with `landing_kind` set (v17) and no `TREE`
  slice. It is addressed through the `branch` selector by that sha (full, or a
  unique prefix of at least 7 hex characters) and answers `scope=diff` only —
  any other scope on it returns empty with a suggestion. A retired branch's
  tombstone row points at it through `merged_into` = the landing sha, and a
  request naming the retired branch by **name** is refused with an error that
  names the landing sha (§6.8a). The UI therefore never sends a retired name:
  the "merged" picker group of §6.10 pins the landing sha.
- **Capability**: a fact the harness reads from the loaded tools' input
  schemas at startup — whether `branch` is a parameter, whether `changed` /
  `diff` are `scope` values (§6.12).
- **Stage U0 / U1 / U2**: what is implementable on P0 / after P1 / after P2
  (§6.12).
- **Observation**: the interceptor's record of one server call — the cell it
  sent, how the branch was chosen, and the response's `meta` (§6.5).

---

## 3. Requirements (owner decisions D1–D13, restated precisely)

- **R1 — Answer shape is the agent's decision (D1).** Under a multi-branch
  pin, when the model omits `branch` the interceptor fans out: one server call
  per `(project, branch)` cell, each result prefixed with its cell label,
  structured items merged with a `branch` field. When the model passes
  `branch=<one of the pinned branches>` the interceptor honors it (§6.4; the
  "indexed but not pinned" case is §12 O1). One short system-prompt rule states
  when each shape is worth it: merged labeled results for comparisons; one
  branch at a time when only one is relevant or the output is long; always say
  which branch each claim comes from (§6.6). The ReAct architecture is
  unchanged: no supervisor, no extra agents.
- **R2 — Screen state 1 shows no scope controls (D2).** The sidebar shows
  connection settings plus one button labeled "Scope defaults". The transcript
  shows nothing about scope unless a pin is active. Every answer gets a small
  footer line built from the tool responses' `meta` — project, branch, short
  head sha, slice, `index_stale` — and whether the branch came from the
  default, a pin, or the agent's own choice (§6.8).
- **R3 — Screen state 2 is a soft-defaults panel (D3).** Clicking the button
  reveals a "Scope defaults" panel in the sidebar with Project (any | one),
  Branch ("main (base branch)" — the shipped default | checked-out branch |
  any indexed name), Slice (whole branch | changed files | diff hunks), Code
  (all | own | deps), Package. These are soft: they fill in arguments the
  model leaves empty; the model may name another indexed project / branch /
  slice when the question calls for it. Shipped values come from a new
  `ask_your_docs.scope` YAML block (project any, branch = base, slice whole
  branch, code all, `max_cells`); the panel overrides YAML for the session
  only (§7). *(Deviates: O3 — R3 encodes D3 with one spelling change pending
  ratification: the YAML value is `base`, resolved to `branches.base_name` and
  labeled "main (base branch)" exactly as D3 words it, because a literal
  `main` has no row on a bundle indexed from another branch.)*
- **R4 — The per-question pin is three cooperating pieces (D4).** (1) One
  popover button left of the chat input: idle = icon only; clicked = popover
  with Project, Branches (multiselect from indexed names), Slice, a "keep for
  next" toggle and Clear; it closes after sending; with an active pin its label
  carries a short summary such as "backend · 2 branches". (2) Active pins
  render as removable chips in the existing attachment chip row (the row that
  shows symbols attached from the graph page), with "clear all"; the row is
  absent when nothing is pinned or attached. (3) Follow-up chips under the
  answer footer, derived deterministically from the response `meta` and the
  catalog ("compare with main", "show the diff", "pin feature/retry"):
  "compare with X" and "show the diff" send a canned follow-up under a
  one-shot pin; "pin X" only sets a kept pin. Pins are hard (§6.7, §6.9).
- **R5 — One frozen `QuestionScope` replaces the dict (D5).** Fields: `cells`
  (tuple of `(project, branch)` pairs), `slice`, `code`, `package`, `kind`
  (`ScopeKind.DEFAULT | PIN`), plus the DEFAULT-only `branch_default` /
  `branch_name` that §6.2 adds so the branch can be resolved per effective
  project at call time. `ask()` sets it on the contextvar. Interceptor
  rules: (a) model passed an argument under DEFAULT — keep it if it names an
  indexed project / branch, else replace with the default and log; (b) model
  omitted — inject the default; (c) PIN — overwrite; (d) PIN with several
  branches and `branch` omitted — fan out over the cells with a `max_cells`
  cap refused before any call. The interceptor also records each result's
  `meta` on a per-question contextvar for the footer and follow-up chips (§6.3,
  §6.4, §6.5).
- **R6 — Branch names come from the bundles (D6).** A new read-only
  `BundleReader.branches()` over the `branches` table (schema v16) feeds the
  defaults panel, the popover, the prompt catalog and the graph page; unknown
  names cannot be typed. Retired or merged branches (tombstones with
  `merged_into` set) appear under a "merged" group, selectable for diff
  questions about their landing unit (§6.10).
- **R7 — Prompt (D7).** The catalog block gains the indexed branches per
  project, mirroring the package listing; one new rule, numbered 7, is
  rendered only when the server advertises a `branch` parameter, so the
  assembled prompt stays byte-identical otherwise (the branch listing in the
  catalog is gated on the same capability — §6.6 explains why this is the
  only reading compatible with R11). The pinned-scope note stays transient.
  Task heads (code review, release notes) may later override the default
  choice through the existing skill-artifact layer — a hook mention, out of
  scope. *(Deviates: O5 — D7 lists the branches unconditionally; R7 gates the
  listing on the capability, pending ratification.)*
- **R8 — Graph page (D8).** The same "Scope defaults" button and defaults; a
  Branch selector; a "Compare with" second branch; the comparison is computed
  in a GraphService companion over the reader with no server work: a symbol on
  both branches with the same chunk id is unchanged, a different chunk id is
  changed, edge-set differences give added / removed references; a "changed
  only" toggle hides unchanged nodes and edges; attaching a symbol to a
  question carries its branch into the pin (§6.11).
- **R9 — Capability gating and staging (D9).** At startup the harness
  inspects the loaded tools' input schemas; controls whose parameter or value
  the server does not advertise stay hidden. U0 (now, on P0): `QuestionScope`,
  the settings panel with project / code / package plus the branch listing
  (one stamped branch per bundle), the footer from `meta.branch`, the popover
  and chip row, the graph branch label. U1 (after P1): branch argument wiring,
  fan-out, the graph compare overlay. U2 (after P2): slice controls and diff
  follow-up chips (§6.12).
- **R10 — Errors (D10).** An unknown branch from the model under DEFAULT is
  replaced and logged; under PIN it cannot happen (closed list); fan-out over
  `max_cells` is refused before any call with a message naming the cap; a
  stale index is shown in the footer, never hidden; a server without `branch`
  hides the controls rather than erroring (§9).
- **R11 — Byte-identity (D11).** The single-cell DEFAULT path produces the
  same tool arguments and the same prompt as today; the eval binding
  (`binding.py`) and its control arm are unaffected; no MCP tool, parameter or
  envelope field is added; every tunable is YAML (§8).
- **R12 — Tests (D12).** Interceptor rules against fake tools; fan-out merging
  of `CallToolResult` content and `structuredContent`; prompt byte-identity
  when `branch` is not advertised; catalog rendering with branches; the graph
  comparison over a fake reader; follow-up chip derivation; Streamlit AppTest
  smoke tests for the three screen states (§11).
- **R13 — Code rules (D13).** Plain-English identifiers, `StrEnum` for closed
  vocabularies in new code, frozen dataclasses, functions of 4–20 lines,
  files under 500 lines, no vendor or competitor product names, WHY comments,
  Null-object over Optional for optional service dependencies (§6.13).

---

## 4. Goals / Non-goals

### Goals

- Zero scope UI in the default view; scope surfaces only through one sidebar
  button, one popover button, the chip row, the footer and follow-up chips.
- Soft defaults and hard pins carried by one value object with one `kind`.
- Client-side labeled fan-out under a multi-branch pin, capped by YAML.
- Branch names from the bundle only; no free text anywhere.
- Attribution on every answer, stale index never hidden.
- Graph page branch selector and a reader-only compare overlay.
- Same bytes as today on the single-cell default path; eval binding untouched.

### Non-goals

- Any change to the nine tools, their parameters, or the envelope
  (`docs/tool-contracts.md`); any server-side fan-out (multi-branch spec §4
  non-goals).
- A second agent, a supervisor, or a per-branch sub-agent; the ReAct loop of
  `text_react.py:32-34` is the only architecture touched, and only through its
  prompt and tool interceptor.
- Task-head overrides of the default branch (code review, release notes) —
  they belong to the skill-artifact layer (`agent.py:218-239`) and are only
  mentioned as a hook.
- Retention policy for merged branches and landing units — server territory
  (multi-branch spec §6.5b, §6.8a; program plan P2.8).
- Per-project freshness probes on multi-bundle servers (§9 E6; a hook for the
  multi-branch program).

---

## 5. Approaches considered

**Side-by-side answer columns (rejected).** Rendering one answer column per
pinned branch would force the answer shape from the UI and require either N
agent runs or an answer splitter. The owner made the answer shape the agent's
decision (R1): one run, one answer, with the model choosing between merged
labeled results and one branch at a time. Columns also break for three or more
cells and for questions where only one branch turns out to matter.

**A persistent sidebar scope panel (rejected).** Today's shape, extended with a
branch multiselect. Rejected because it makes every answer implicitly
branch-scoped whether or not the user looked at the sidebar, gives the pin no
per-question lifetime, and puts a seven-control panel in front of people who
ask "how does routing work?". The panel survives only as the collapsed
"Scope defaults" button of state 2 (R2, R3), and its values become soft.

**A composer bar above the chat input (rejected).** A row of always-visible
pickers directly above the input. Rejected for the same "always visible"
reason and because it competes with the attachment chip row for the same
space; the popover keeps the idle state to one icon (R4).

**Typed mentions in the question (`@backend#feature/retry`) (rejected).**
Rejected because free text is exactly what R6 forbids (unknown names cannot
be typed), because reformulation (`agent.py:391-416`) would rewrite or strip
the syntax, and because the pin must be hard and visible as a chip, not a
token inside the question the model may or may not honor.

---

## 6. Architecture

### 6.1 The two kinds of scope

| | Defaults (`ScopeKind.DEFAULT`) | Pin (`ScopeKind.PIN`) |
|---|---|---|
| Lifetime | session (panel) / deployment (YAML) | one question, or kept until cleared |
| Strength | soft: fills what the model left empty | hard: overwrites what the model passed |
| Where set | sidebar "Scope defaults" panel (§6.7) | popover, follow-up chips, graph "Add to question" |
| Visible in transcript | never | a small scope chip on the question |
| Multi-branch | never (one branch per project) | yes → fan-out (§6.4) |
| Model note | none | transient `[pinned scope: …]` prefix (as today) |

Exactly one `QuestionScope` is active per question. When a pin is active it
carries the pinned values and the session defaults fill its unset fields
(package, code); when no pin is active the defaults are the scope.

### 6.2 The `QuestionScope` value object — new file `harness/ask_your_docs/question_scope.py`

A new file because `agent.py` has 32 lines of headroom under the 500-line
ceiling (468 today) and the value object plus its enums, the session override
and the two resolution helpers are ~190 lines. `scope_prefix` moves here too
(it renders the value object); the lazy export at `__init__.py:21-28` is
re-pointed (`"scope_prefix": "question_scope"`) so the public name is
unchanged.

```python
class ScopeKind(StrEnum):
    DEFAULT = "default"
    PIN = "pin"

class ScopeSlice(StrEnum):           # server `scope` values in the comments
    WHOLE_BRANCH = "whole_branch"    # (no value sent)
    CHANGED_FILES = "changed_files"  # "changed"  (P2)
    DIFF_HUNKS = "diff_hunks"        # "diff"     (P2)

class ScopeCode(StrEnum):            # today's own-vs-dependency filter
    ALL = "all"                      # (no value sent — today's rule, agent.py:133)
    OWN = "own"                      # "project"
    DEPS = "deps"                    # "deps"

class ScopeBranchDefault(StrEnum):   # symbolic values of the branch default
    BASE = "base"                    # the bundle's branches.base_name — "main (base branch)"
    CHECKED_OUT = "checked_out"      # the bundle's is_default row — the server's own default

@dataclass(frozen=True, slots=True)
class ScopeCell:
    project: str                     # "" = union across loaded projects (mcp_inputs.py:237-240)
    branch: str                      # "" = let the server resolve (spec §6.4)

@dataclass(frozen=True, slots=True)
class QuestionScope:
    kind: ScopeKind
    cells: tuple[ScopeCell, ...]     # never empty; DEFAULT has exactly one cell with branch ""
    slice: ScopeSlice = ScopeSlice.WHOLE_BRANCH
    code: ScopeCode = ScopeCode.ALL
    package: str = ""
    # DEFAULT only — the branch is resolved lazily, per call, against the
    # effective project (§6.3), because "base branch of whichever project the
    # model picks" cannot be pre-resolved into one cell. Ignored under PIN.
    branch_default: ScopeBranchDefault = ScopeBranchDefault.BASE
    branch_name: str = ""            # a named default wins over branch_default when non-empty

    @property
    def is_multi_branch(self) -> bool: ...
    def branches_for(self, project: str) -> tuple[str, ...]: ...

@dataclass(frozen=True, slots=True)
class ScopeDefaultsOverride:         # the panel's session values; None = "use YAML"
    project: str | None = None
    branch_default: ScopeBranchDefault | None = None
    branch_name: str | None = None
    slice: ScopeSlice | None = None
    code: ScopeCode | None = None
    package: str | None = None
```

Invariants (enforced in `__post_init__`, errors carry the offending value):
`cells` is non-empty and free of duplicates; `kind == DEFAULT` ⇒ exactly one
cell and that cell's `branch == ""` (the branch is resolved per call, never
stored); `kind == PIN` ⇒ `branch_default` / `branch_name` are ignored;
`slice != WHOLE_BRANCH` ⇒ `code != DEPS` (the server's `deps` and `changed` /
`diff` slices are disjoint, multi-branch spec §6.5).

`resolve_question_scope_defaults(config: ScopeDefaultsConfig, session:
ScopeDefaultsOverride, listing: WorkspaceBranchListing) -> QuestionScope`
turns the YAML block (§7), the panel's session override and the bundle's
branch listing (§6.10) into the DEFAULT scope: one project cell — `("", "")`
for project `any` (a union request, `mcp_inputs.py:237-240`), `(name, "")`
for a listed project — plus `branch_default` / `branch_name` copied through.

`resolve_default_branch(scope: QuestionScope, project: str, listing:
WorkspaceBranchListing) -> str` is called by the interceptor once per tool
call against the **effective project** — the project the model passed and the
listing knows, else the default cell's project. It returns the branch to
inject, `""` meaning "inject nothing":

- `project == ""` (union) → `""`: no per-bundle name can be sent on a union
  request, so the server resolves it.
- `branch_name` non-empty → itself when the listing has that name for the
  project; otherwise `""` plus one `scope_default_replaced` log (the name came
  from the closed list, so this happens only after the workspace changed
  under a saved session value).
- `BASE` → the project's default row's `base_name` when the listing has a row
  of that name for the project **and** it differs from the default row's own
  name; otherwise `""`. On P0 bundles `base_name` is NULL (P1.6 stamps it,
  §1.2), so `BASE` is `""` on every U0 deployment — byte-identical by
  construction (§8).
- `CHECKED_OUT` → `""` (the server's own default; injecting nothing is the
  byte-identical choice).

`ask()` (`agent.py:419-468`) takes `scope: QuestionScope | None` and sets it
on the contextvar; the `ToolScope` dict alias (`agent.py:54`) is deleted, and
`scope_prefix` (public lazy export, `__init__.py:12-28`, now served from
`question_scope.py`) keeps its name and renders a `QuestionScope`:
`[pinned scope: project=backend, branches=main, feature/retry, diff hunks,
own code only] ` — for `kind == DEFAULT` it returns `""` (the defaults are
never announced to the model; rule 6 is about pins); a one-cell pin whose
branch is `""` renders `project=backend` only, today's bytes
(`test_image_attachment.py:94`). That test, which calls `ask(...,
scope={"project": "p"})`, is updated to a `QuestionScope`.

### 6.3 Interceptor rules — new file `harness/ask_your_docs/scope_interceptor.py`

`agent.py` keeps the `_intercept` name (imported by name at
`binding.py:320`) as a three-line delegate to
`intercept_question_scope(request, handler)` in the new file, which also owns
the two contextvars (`ACTIVE_QUESTION_SCOPE`, `ACTIVE_SCOPE_OBSERVATIONS`);
`agent.py`'s `_active_scope` (`agent.py:60-62`) is removed. The stale
"all six tools" comment (`agent.py:81-84`) dies with it.

**Strict passthrough when no question is active.** When
`ACTIVE_QUESTION_SCOPE` is `None` — every call from the eval binding, which
invokes the graph directly and never calls `ask()` (`binding.py:366-369`) —
the interceptor returns `await handler(request)` unchanged, exactly as today
(`agent.py:127` coalesces `None` to `{}` and then changes nothing). YAML
defaults are never consulted on this path (R11, §8).

Per-argument rules, evaluated once per tool call; `advertised` is the
capability record of §6.12.

| Argument | Tools | `DEFAULT`, model passed | `DEFAULT`, model omitted | `PIN` |
|---|---|---|---|---|
| `project` | all nine | keep if `listing.knows_project(name)` — a project name or a bundle stem, the two forms `select_project` accepts (`multirepo.py:198-205`; the listing carries both, §6.10); else replace with the default cell's project and log `scope_default_replaced` | inject the default cell's project when it is non-empty; inject nothing for `any` | overwrite with the cell's project (one cell) or fan out over the cells (§6.4, §6.4a) |
| `branch` (U1) | all nine, only when `advertised.branch_selector` | keep if the listing has it for the effective project; else replace with `resolve_default_branch(scope, effective_project, listing)` (possibly nothing) and log | inject `resolve_default_branch(scope, effective_project, listing)` when non-empty (§6.2: a named default, or `BASE` differing from the checked-out row); inject nothing for `""` | the model named a branch → the pinned cells whose branch matches (§6.4 `matching_cells`: one → single call, several → fan out over those, none → §12 O1, default reading: fan out over every cell and log — the pin is hard); omitted → fan out over the cells (§6.4) |
| `package` | `search_codebase`, `get_overview` (`agent.py:85`) | keep (passed through unchanged, as today) | inject the default package when non-empty | overwrite when the pin carries one |
| `scope` as code | `search_codebase` only (today's rule, `agent.py:133-134`) | keep | inject `project` / `deps` when the default code is `OWN` / `DEPS`; nothing for `ALL` | overwrite |
| `scope` as slice (U2) | `search_codebase`, `grep` only, when `advertised.changed_slice` / `diff_slice` | keep a `changed` / `diff` the model passed | inject `changed` / `diff` when the default slice is not `WHOLE_BRANCH` | overwrite |

Rules that keep today's bytes: `grep`'s own `scope` (default `"project"`,
`server.py:814`) is never touched by the code filter — as today
(`agent.py:133` names `search_codebase` only); the slice, when it applies, is
sent on `grep` as well because the server defines `changed` / `diff` on both
tools (multi-branch spec §3.2 Q5, §7). The slice is never injected on the
seven tools that do not take it. Under `DEFAULT` the `scope` key is injected
only when the model omitted it; then the slice value is chosen over the code
value (the §6.2 invariant forbids `DEPS` with a slice, and `changed` implies
own code — spec §6.5 `all ⊃ project ⊃ changed`). Under `PIN` the pin's slice
overwrites, else the pin's code, else nothing. A model-passed `scope` is never
overwritten under `DEFAULT`.

The one behavioral deviation from today under `DEFAULT` is rule (a): a model
that names an unknown project gets it replaced client-side instead of a server
`InvalidArgumentError` (`multi_project_search.py:295-310`). It fires only when
the model passed a name the listing does not know, and it logs a structured
`scope_default_replaced` record `{tool, argument, passed, replacement}`. When
the default project is `any` the replacement is `""` — a **union across every
loaded bundle** (`mcp_inputs.py:237-240`), i.e. the call is widened, not
narrowed; the log record carries `replacement: ""` and the footer origin
reads `agent-chosen → default` so the widening is visible (§6.8, §9 E1).
Passing the unknown name through so the server's typed error reaches the model
was not chosen: D5 rule (a) says replace and log.

### 6.4 Fan-out and result merging

Fan-out is defined over **cells**, not over the `branch` argument.
`fan_out_over_cells(request, handler, scope, target_cells, observations)`
runs whenever `kind == PIN`, `len(cells) > 1`, and the model did not name a
single cell. `target_cells` is chosen before the cap:

- the model omitted `branch` (or `branch` is not advertised) and omitted
  `project` → every cell;
- the model passed `project=<pinned project>` → that project's cells;
- the model passed `branch=<name>` (U1) → `matching_cells = tuple(c for c in
  cells if c.branch == name)`: exactly one → a single call, no fan-out;
  several (two pinned projects that share the name, §6.4a) → fan out over
  those; none → §12 O1's default reading: every cell, plus one
  `scope_pin_branch_ignored` log — the pin is hard.

1. **Cap first.** `len(target_cells) > config.max_cells` → return
   `CallToolResult(isError=True, content=[TextContent(text=f"scope pin spans
   {n} (project, branch) cells; the limit is max_cells={cap}
   (ask_your_docs.scope.max_cells). Narrow the pin or pass branch=<name>.")])`
   **without calling the handler**. Returning an error result rather than
   raising is deliberate: the adapter raises `_MCPToolExecutionError` on an
   `isError` result (`langchain_mcp_adapters/tools.py:274`), and the generated
   tool's error handler (`_handle_mcp_tool_error`, `tools.py:122-160`, wired
   at `tools.py:527`; on by default — `handle_tool_errors: bool = True` at
   `tools.py:547` and `client.py:58`) renders it as an error `ToolMessage` the
   model can read, whereas a bare exception escapes the graph.
2. **One handler call per cell**, sequential in cell order (deterministic
   labels; the popover's cell order is project then branch as listed). The
   per-cell arguments are `{**args, "project": cell.project}`, plus
   `"branch": cell.branch` **only when `advertised.branch_selector` and
   `cell.branch` is non-empty** — the interceptor never sends an argument the
   capability does not cover, so a two-project pin on U0 fans out over
   `project` alone (AC-6b). The adapter validates each cell's
   `structuredContent` against the advertised `outputSchema` inside the
   handler (`mcp/client/session.py:412-413`), before the interceptor sees it.
3. **Merge** (`merge_cell_results(cells, results) -> CallToolResult`):
   - `content`: for each cell, `TextContent(text=f"## {cell.project} ·
     {cell.branch}\n")` followed by the cell's own content blocks (the server
     emits exactly one `TextContent`, `server.py:632-642`). Blocks are kept as
     separate blocks; the adapter converts them 1:1 into the tool message's
     content list (`tools.py:268-271`).
   - `structuredContent`: the `{text, items, meta}` envelope shape is kept
     (`tool_response.py:27-37`): `text` = the labeled texts joined; `items` =
     every cell's items, each with two added fields `branch` and `project`
     (§3.2 rows carry `package`, not project, `multi_project_search.py:211-225`)
     — D1's sanctioned additions; `meta` = **exactly the first cell's `meta`**,
     no added key. The contract freezes the `meta` field names
     (`docs/tool-contracts.md` §2), and a harness-side `cells` key would look
     like a surface extension; per-cell attribution lives in the labeled text,
     the per-item fields, and `ScopeObservations` (§6.5), which is the only
     per-cell `meta` record. The merged dict is never re-validated by the
     adapter (validation happened per cell, `tools.py:278-281` wraps it
     unvalidated), and it never reaches the server — the envelope contract is
     untouched.
   - `isError`: `True` only when **every** cell errored (all texts kept). A
     partial failure keeps `isError=False` and keeps the failing cell's error
     text verbatim under its label — rule 7 tells the model to report it per
     branch. A transport exception from one cell propagates and aborts the
     call (as a single call would today).
4. **Observe.** One `CellObservation` per cell is appended (§6.5).

Cost note: the app's `build_agent` path binds tools with `session=None`, so
every handler call opens a new serve session — one subprocess spawn per cell
per tool call (`langchain_mcp_adapters/tools.py:460-469`; the eval binding
holds one session, `binding.py:309-327`). The cap bounds it; §12 O4 asks
whether the app should hold one session too.

### 6.4a The cell matrix

A pin's cells are the product of its projects and, per project, its selected
branches. The popover builds cells for **one** project (`{(P, b) for b in
branches}`, §6.10); a second project enters a pin only through the graph
page's "Add to question", which appends `(symbol.project, symbol.branch)`
(§6.11). On U0 (no `branch` capability) every cell's `branch` is `""` and a
multi-project pin fans out over `project` only; on U1 the model may narrow
with `branch=<name>` or `project=<name>` as §6.4 defines. The popover
disables its Send action and shows a caption naming `max_cells` when the
cell count exceeds `ask_your_docs.scope.max_cells`, so E4 is reachable only
from a pin assembled by graph attaches. The popover's summary label is
`<project> · N branches`, `N projects` when cells span projects, and
`<project> · <branch> · <slice>` for one cell with a non-default slice.

### 6.5 Observations: recording `meta` per question

`ScopeObservations` is a small mutable container (a list of frozen
`CellObservation(tool, project, branch, branch_origin, slice, meta)`), created
by `ask()` per question and stored on `ACTIVE_SCOPE_OBSERVATIONS`. It is
mutable on purpose: the interceptor runs in child tasks with **copied**
contexts (`langchain_core/tools/base.py:1186-1196` copies the context via
`set_config_context`, `runnables/config.py:236-239`, and the tool task is
created with that copy — `asyncio.create_task(coro, context=context)`,
`langchain_core/runnables/utils.py:142-156`; the tool node gathers calls
concurrently, `langgraph/prebuilt/tool_node.py:858`), so a value *set* inside
the interceptor is invisible to `ask()`; only in-place mutation of a container
created in `ask()` travels back — the `_reinspect_state` precedent
(`agent.py:77-79`, set at `agent.py:445`, mutated by the tool at
`reinspect.py:62`). Append order is nondeterministic across parallel tool
calls of one turn; consumers sort by `(project, branch)`.

`BranchOrigin` is a `StrEnum` `{DEFAULT, PINNED, AGENT_CHOSEN, SERVER}`,
observed at the interceptor with these rules: under `PIN` every observation is
`PINNED`, whether the model omitted `branch` or named a pinned cell; under
`DEFAULT` a model-passed branch that the listing knows is `AGENT_CHOSEN` even
when it equals the value the default would have injected; `DEFAULT` when the
interceptor injected the resolved default; `SERVER` when no `branch` was sent
and `meta.branch` is the server's answer (the footer renders it as `server
default`, §6.8). Provenance is thus *observed at the interceptor*, not
inferred from `meta` (which has no such field, `tool_response.py:40-54`).

`ask()` gains a keyword `observations: ScopeObservations | None = None`
(the `image_store: dict | None` precedent at `agent.py:427`); the app passes a
fresh container per question (Streamlit's session state is unreachable from
the loop thread, so the container is the only channel back), the eval binding
passes nothing and `ask()` creates a discarded one. The return type of `ask()`
stays `str`.

Why this survives Streamlit reruns and the shared cached agent: the scope and
the observations are per-`ask()` task state — set inside the coroutine that
`run()` schedules on the one cached loop thread (`app.py:43-52`) — so a rerun
rebuilds them from session state, and the agent cached by `get_agent`
(`app.py:80-91`, shared by every session) holds only the interceptor closure.
What persists across reruns is `st.session_state` (the kept pin, the panel
override, the transcript entries — keys named in §6.7), never the contextvar.

### 6.6 Prompt changes

**Single assembly site preserved.** `_assemble_prompt` (`agent.py:188-215`)
gains one keyword `branch_selector_advertised: bool = False` and threads it as
a Jinja variable: `prompts_for(name).render("system_v1",
branch_selector_advertised=…)` (`prompts/__init__.py:47-50` →
`prompt_namespace.py:67-71`, which already forwards `**variables`). The
constant `SYSTEM_PROMPT = render_shared("system_v1")` (`prompts/__init__.py:70`)
renders with **no variables**. The shared loader uses `StrictUndefined`
(`retrieval/prompts/_loader.py:17`: a missing name raises
`jinja2.UndefinedError`), so a plain `{% if branch_selector_advertised %}`
would break `import pydocs_mcp.harness.ask_your_docs.prompts` — and with it
the app, the CLI, the eval binding and `test_prompts_package.py:21,36,60`.
The guard is therefore written

```
{% if branch_selector_advertised is defined and branch_selector_advertised %}
```

— the `is defined` test is the one construct `StrictUndefined` permits on a
missing name. Every variable-less render (`SYSTEM_PROMPT`,
`render_shared("system_v1")`, `prompts_for(name).render("system_v1")`,
`render_core_prompt("system_v1")`) takes the false branch and keeps today's
bytes, which keeps the eval seed parity pin (`test_prompt_seed_parity.py:38-49`)
and the assembly pin (`test_prompt_seam.py:36-40`) green without
regeneration. The environment has `trim_blocks` and `lstrip_blocks` on
(`_loader.py:18-19`), so the `{% if %}` / `{% endif %}` lines vanish; the
block is placed after the blank line that follows rule 6 (`:50`), so the false
branch renders to exactly today's `…widening it.\n` (the file ends `it.\n\n`;
the loader strips one trailing newline). That the block renders to the exact
pre-edit bytes is **pinned by a golden**, not assumed (§11 V4).

**Rule 7, exact text** (appended to `system_v1.j2` after rule 6 at `:44-49`,
inside the guard above; rule 6's bytes are unchanged so the U0 prompt stays
identical):

```
7. Every tool takes a "branch" argument; the indexed-projects list below
   names each project's branches and marks the default. Leave "branch" empty
   to answer from the default branch, or from the pinned branches when the
   question carries a pin (the app applies the pin for you). Under a pin with
   several branches, an empty "branch" returns one labeled result per branch
   — best when the user is comparing branches; pass branch=<name> to read one
   branch at a time when only one is relevant or the output is long — under
   a pin, <name> must be one of the pinned branches; any other name is
   answered from all pinned branches. A pinned-scope note may also list
   branches and a slice; the app applies those too. Always say which branch
   each claim comes from.
```

**Catalog line, exact shape.** `render_catalog(catalog, branches=None)`
(`catalog.py:57-64`) gains an optional `branches: WorkspaceBranchListing |
None` (§6.10); `None` renders today's bytes. With branches:

```
- backend — branches: main (default), feature/retry — dependency packages: fastapi, pydantic
- tooling — branches: main (default) — own code only (no dependency packages indexed)
```

The branch segment is inserted between the project name and the package
segment; only the listing's **pickable** rows appear (live branches; landing
units — rows named by a 40-hex sha — never appear); the default row is marked
`(default)`. On U2, when `diff_slice` is advertised, the listing's merged
tombstones follow the live names, marked `feature/old (merged into main
@3e1a9c2)` — `merged_into[:7]`, because `merged_into` holds the landing sha,
not a branch name — so the model can name that sha with `scope=diff`.

**Why the catalog branch listing is gated on the same capability as rule 7.**
R7 says the prompt stays byte-identical when `branch` is not advertised, and
R11 says the DEFAULT path produces the same prompt as today. Every P0 bundle
already stamps one branch, so an ungated listing would change the catalog
bytes of every post-P0 workspace on U0 (`test_prompt_seam.py:36-40` would
fail on a real workspace). The branch listing is therefore passed to
`render_catalog` only when `branch_selector_advertised` is true. On U0 the
branch names still feed the panel, the popover and the footer — only the
model-facing prompt waits for U1. (§12 O3 records the alternative.)

**Pinned-scope note stays transient**: `scope_prefix` output is prepended
after reformulation and never stored (`agent.py:446-452`, `:465-467`); rule 6
is unchanged.

**Hook, out of scope:** a task head (code review, release notes) may later
override the default branch choice through the skill-artifact block
(`agent.py:218-239`, `prompt_override.py:50-51`).

### 6.7 The three screen states

**State 1 — default view.** Sidebar: Appearance, Connection (workspace, model,
base URL, config — `app.py:101-105`), the capability caption, and one button
"Scope defaults". Nothing else. Main area: the transcript and the chat input,
with one icon-only popover button to its left. No caption, no picker, no
mention of scope. Each answer ends with one small footer line
(§6.8) and, when derivable, follow-up chips (§6.9). This is the whole screen
for a workspace with one branch per bundle.

**State 2 — "Scope defaults" clicked.** The sidebar reveals the panel below
the button: Project (selectbox: "any" + indexed names), Branch (selectbox,
**rendered only when `branch_selector` is advertised** — U1: "main (base
branch)" and "checked-out branch", which map to `branch_default`, then the
indexed names of the selected project, which map to `branch_name`; when
Project is "any" only the two symbolic entries are offered, because a named
entry needs a project; on U2 a "merged" group follows, §6.10), Slice (radio:
whole branch / changed files / diff hunks — hidden until U2), Code (radio:
all / own / deps), Package (selectbox; hidden when Code is "own", as today
`app.py:140-142`). On U0, where the server advertises no `branch`, the Branch
row is a **read-only caption per project** from the listing — `branch: main
@3e1a9c2 (checked out)` — the "branch listing" D9 names for U0: informational,
not a control, so nothing can be selected that would not be sent. A caption
under the controls: "Soft defaults — they fill in what the agent leaves
unspecified. The agent may pick another indexed project or branch when the
question asks for it." A "Reset to shipped" button restores the YAML values.
The panel stays open for the session once clicked (a session-state flag);
clicking the button again collapses it.

**State 3 — pin active.** The popover button's label shows a summary
(`backend · 2 branches`, `backend · feature/retry · diff hunks`, `2 projects`
— the §6.4a rule). The attachment chip row (`app.py:173-183`) shows one
removable chip per pin element — one chip per branch (`✕ feature/retry`), one
for the slice when not whole-branch (`✕ diff hunks`), one for the project when
pinned (`✕ backend`) — next to any attached symbols, plus "clear all", which
clears both pins and attachments. The question in the transcript shows a small
scope chip rendered as a `st.caption` above the question with the text
`<project> · <branch>[, <branch>…][ · <slice>]` (e.g. `backend · main,
feature/retry`).

**Pin lifecycle.** The active pin lives in `st.session_state["scope_pin"]` as
a `QuestionScope | None` next to `st.session_state["scope_pin_keep"]: bool`.
On send the app snapshots the pin into the question; a one-shot pin
(`keep=False`) is set to `None` immediately after the snapshot, **before
`ask()` runs**, so the chip row is empty during the spinner and the
transcript's scope chip is the only trace; a kept pin (`keep=True`) stays
until (i) its last cell chip is removed, (ii) "clear all", (iii) the
popover's Clear, or (iv) the workspace text input changes — the listing is
reloaded and a pin with any cell no longer in it is dropped whole, with a
toast naming the missing cell. Turning "keep for next" off while a kept pin
is active makes it one-shot for the next send. The popover's controls are
pre-filled from the active pin on every render. `st.session_state` survives
agent rebuilds and reruns; the pin is never stored in `ask()`'s history.

**Widget keys.** Defaults panel: `scope_defaults_{project,branch,slice,code,
package}` plus the flag `scope_defaults_open`; popover container key
`scope_pin_popover` with `scope_pin_{project,branches,slice,keep}`; chips
`scope_chip_<project>_<branch>`, `scope_chip_slice`, `scope_chip_project`;
follow-up chips `follow_up_<transcript index>_<kind>`. Today's
`scope_project` / `scope_code` / `scope_package` keys (`app.py:123-141`)
disappear with the sidebar block, and AC-19 asserts their absence by name.

Every control that would produce a tool argument is rendered only when its
capability is advertised (§6.12); listing-only surfaces — the U0 branch
caption in the panel and in the popover, the footer's `meta.branch` — render
regardless.

### 6.8 Answer footer — new file `harness/ask_your_docs/answer_footer.py`

`render_answer_footer(observations: ScopeObservations) -> str` builds one
caption line under the answer. Aggregation rule: observations are grouped by
`(project, branch)` cell in sorted order; one segment per cell; segments
joined by ` | `. Segment format:

```
answered from backend · feature/retry @3e1a9c2 · whole branch · pinned
```

- project: the cell's project sent by the interceptor; when nothing was sent
  and the listing has more than one project, `all projects` (a union request
  spans every bundle, while `meta.project` names only the first loaded one —
  `tool_router.py:108-111`); else `meta.project`.
- branch: the cell's branch sent, else `meta.branch`, else "no branch" (the
  four null cases of contract §2.4, `docs/tool-contracts.md:142-164`).
- `@<sha7>`: the listing's `head_sha[:7]` for the cell's `(project, branch)`
  when the interceptor sent that cell (exact per bundle and branch, §6.10);
  otherwise `meta.indexed_git_head[:7]` when present.
- slice: the slice sent on that cell's calls (`whole branch` / `changed files`
  / `diff hunks`; distinct slices are listed).
- origin: `default` / `pinned` / `agent-chosen` / `server default` (one per
  `BranchOrigin` member, §6.5); a replaced argument (§6.3 rule a) renders as
  `agent-chosen → default`.
- ` · index stale` appended when any observation of the cell has
  `meta.index_stale` true — never hidden (R10).

Caveat, stated in the caption's tooltip and in §9 E6: on P0 the freshness
probe is built once from the first loaded bundle (`server.py:487-496`), so
`meta.branch` and `index_stale` describe bundle #1 even when `project=`
selected another bundle. The footer's project, branch and sha come from the
interceptor's own cell and the client-side listing whenever a cell was sent,
so attribution is exact under a pin or a named default; only the staleness
flag is as precise as the server's probe.

`ask()` returns the answer string; the app reads the container it passed and
stores `(answer, footer, chips)` in the transcript entry so reruns re-render
the footer without re-asking.

### 6.9 Follow-up chips

`derive_follow_up_chips(observations, listing, capabilities, kept_pin:
QuestionScope | None) -> tuple[FollowUpChip, ...]` in `answer_footer.py`,
pure and deterministic. Inputs: the question's observations (cells, origins,
slices), the branch listing (base names, indexed set, landing shas), the
capability record, and the kept pin active after the answer (`None` when
none). `FollowUpChip(kind: FollowUpKind, label, project, branches, slice,
question)` is frozen; `FollowUpKind` is a `StrEnum` `{COMPARE_WITH,
SHOW_DIFF, PIN_BRANCH}`. There are at most three chips because there are
three kinds and each kind yields at most one chip per answer (`PIN_BRANCH` is
one chip per answered cell, capped so the total stays three); the cap is the
count of `FollowUpKind` members, not a tunable.

Rules, evaluated in this order:

| Chip | Derived when | Effect |
|---|---|---|
| `compare with <base>` | U1; exactly one distinct answered cell; its branch's `base_name` is in the listing for that project and differs from itself (needs P1.6, §1.2) | one-shot PIN with cells `{(project, branch), (project, base)}`; canned question `"Compare the previous answer between <branch> and <base>: what differs?"` |
| `show the diff` | U2 (`diff_slice` advertised); exactly one distinct answered cell; no observation of the question had `slice == DIFF_HUNKS`; the cell's branch is live — for a merged tombstone the cell is `(project, merged_into)`, its landing sha | one-shot PIN, that cell, `slice = DIFF_HUNKS`; canned question `"Show the diff hunks behind the previous answer."` |
| `pin <branch>` | U1; one chip per distinct answered cell in `(project, branch)` order whose origin is `DEFAULT`, `SERVER` or `AGENT_CHOSEN` and that `kept_pin` does not contain as a cell | sets `kept_pin` to `QuestionScope(kind=PIN, cells=(cell,), slice / code / package from the session defaults)`, or adds the cell to an existing kept pin; sends nothing |

Two answered cells therefore yield no `compare with` chip and up to two `pin`
chips. Effects: a `COMPARE_WITH` / `SHOW_DIFF` click builds a one-shot
`QuestionScope(kind=PIN, …)` that **replaces** the kept pin for that one
question only — the kept pin is restored unchanged after the answer (the chip
never edits it); a `PIN_BRANCH` click changes only the kept pin and sends
nothing. `apply_follow_up_chip(chip, kept_pin) -> tuple[str | None,
QuestionScope | None]` (the canned question to send, if any, and the pin to
send it under) is the pure function under test (AC-31). Chips are computed
once per answer and stored in the transcript entry; reruns re-render the
stored tuple.

Chips are rendered as small buttons under the footer (keys in §6.7); clicking
one calls `send_question(question, images=(), scope=one_shot_pin)` — the send
path extracted from today's inline `if submission := st.chat_input(...)`
block (`app.py:218-276`: policy check, image store, history append,
`weave_attachments`, `reformulate`, `ask`, transcript append) — so the canned
question is woven, reformulated and prefixed exactly like a typed one (§6.13).

### 6.10 The pin trio: popover, chip row, and the branch listing

**Popover** (`render_scope_pin_popover` in the new
`harness/ask_your_docs/scope_panel.py`, a Streamlit-only module of page
fragments: the defaults button and panel, the popover, the chip row, the graph
page's selector row). Contents: Project (selectbox, indexed names), Branches
(`st.multiselect` over the listing's pickable rows for the selected project —
the widget accepts no free text by default, R6; the keyword
`accept_new_options` is **not passed**: its default is already `False` and
the list is closed by construction; **rendered only when `branch_selector` is
advertised** — on U0 the popover shows a caption naming the stamped branch
instead), Slice (radio, hidden until U2), "keep for next" toggle, Clear. The
button label is `""` with an icon when idle and the summary text when a pin
is active; the popover always receives the key `scope_pin_popover`, because
without one the label is part of the widget identity and a label change
would create a new widget.

Placement and closing, verified on the installed 1.59.1 source and the
toolkit release notes: the pinned chat input is only pinned when it sits at
the main root with no ancestor block
(`streamlit/elements/widgets/chat.py:1012-1023`); the shape that puts a button
left of it is `with st.bottom: left, right = st.columns([1, 12])` with the
chat input rendered **inline** in the right column — inline position is by
design there, and the bottom container supplies the pinning (`st.bottom` is
public at `streamlit/__init__.py:114`; the private `_bottom` is deprecated
with sunset 2026-07-01, `:121-122`; `st.bottom` refuses to nest inside the
sidebar or a dialog, `elements/bottom.py`). Closing after send: the popover
takes `key` and `on_change` (`elements/layouts.py:1329-1330`), and a keyed
popover can be closed programmatically by writing
`st.session_state["scope_pin_popover"] = False` before `st.rerun()`. Three of
these features post-date the pinned floor `streamlit>=1.43`
(`pyproject.toml:143`): per the release notes, `st.bottom` became public in
1.57.0, popover `key` / `on_change` arrived in 1.55.0, and
`accept_new_options` in 1.45.0. The floor is therefore raised to
`streamlit>=1.57` with the WHY comment `# WHY: st.bottom (chat composer row)
+ stateful st.popover (scope pin)`; the U0 plan carries the bump. The private
`_bottom` shim is not used as a fallback: its sunset date is already past.

**Chip row.** `render_scope_chip_row(attached, pin)` generalizes the block at
`app.py:173-183`: one row, absent when both are empty; chips for pin elements
first, then attached symbols; "clear all" clears both. Attached elements
become a frozen `AttachedSymbol(symbol, project, branch)` (new, in
`attachments.py`, 147 lines) instead of a bare string so the graph page can
carry the branch (R8); `weave_attachments` (`attachments.py:128-137`) takes
the symbols' names; `test_app_attachment.py:11-21` seeds `AttachedSymbol`
values. Removing a branch chip removes that cell from the pin; removing the
last cell clears the pin.

**Branch listing** — `BundleReader.branches()` (added to the Protocol at
`bundle.py:30-77` and to `SqliteBundleReader`, ~40 lines; `bundle.py` is 174
lines). Returns `tuple[IndexedBranch, ...]` where the frozen `IndexedBranch`
carries `name, head_sha, base_name, is_default, status: BranchStatus,
merged_into, landing_kind: str | None, indexed_at` read from `branches`
ordered `is_default DESC, name` (the server's own order,
`storage/sqlite/branch_repository.py:120-124`; `landing_kind` is read as
`None` on v16, where the column does not exist), plus the derived property
`is_landing_unit` — true when `landing_kind` is set (v17) or `name` is 40 hex
characters (v16). `branches()` wraps its SELECT in the guard of
`storage/factories.py:904-915` — an `OperationalError` containing "no such
table" (a pre-v16 bundle) → `()`, anything else re-raised — because the
existing `_scalar` tolerance (`bundle.py:87-94`) serves single values only.
It reuses `BranchStatus` from `models.py:143-149` rather than defining a
second vocabulary.

`WorkspaceBranchListing` is a frozen value object in `catalog.py` (64 lines
today): `projects: Mapping[str, tuple[IndexedBranch, ...]]` and
`bundle_stems: frozenset[str]`, built by `workspace_branch_listing(workspace)`
(newest bundle wins, like `CatalogService.projects()` at `catalog.py:32-42`),
with the methods `pickable(project)` (live rows: `status == ACTIVE` and not a
landing unit — the only rows the Branch pickers, the popover multiselect and
the catalog line list; a busy base can hold hundreds of landing rows after
P2.8), `merged(project)` (tombstones: `status in {MERGED, DELETED}` with
`merged_into` set), `default_row(project)`, `head_sha(project, branch)` (the
footer's sha, §6.8) and `knows_project(name)` (a project name or a bundle
stem, §6.3). It feeds the panel, the popover, the prompt catalog (gated,
§6.6), the footer and the graph page.

**Merged group** (U2). `merged(project)` forms the "merged" group after the
live names in the panel's Branch selectbox and the popover's Branches list,
each entry labeled `feature/old (merged into main @3e1a9c2)` —
`merged_into[:7]`, because `merged_into` is the **landing sha**, never a
branch name (multi-branch spec §6.8a). Selecting one pins the cell
`(project, merged_into)` — the landing sha the server's selector accepts —
with `slice = DIFF_HUNKS` forced, because a landing unit answers `scope=diff`
only (spec §6.5b; E11 already forbids `DEPS` with a slice). The retired
**name** is never sent: the server refuses it with an error naming the
landing sha. The group therefore needs the landing-unit index (plan P2.8)
and the `diff` scope value, and is rendered only when `diff_slice` is
advertised; before that the tombstones are not listed at all. On P0 the
group is always empty: retirement is P1.7 (program plan `:39`).

### 6.11 Graph page: branch selector and compare overlay

`pages/2_Graph.py` (266 lines) gains, in its sidebar, the same "Scope
defaults" button and panel (shared fragments from `scope_panel.py`), a
Branch selectbox (indexed names of the selected project; the defaults'
branch preselected), and — on U1 — a "Compare with" selectbox and a "changed
only" toggle. `GraphService(SqliteBundleReader(db), hide_tests)`
(`2_Graph.py:118-122`) is built as today; the branch label is shown in the
selection panel on U0.

**Comparison** — new file `harness/ask_your_docs/graph_compare.py`
(`graph_service.py` is 338 lines; the comparison is ~120 lines).
`compare_branch_graphs(reader, branch_a, branch_b, *, hide_tests) ->
BranchGraphComparison` with `ChangeState` a `StrEnum`
`{UNCHANGED, CHANGED, ADDED, REMOVED}` and a frozen result carrying
`nodes: tuple[(Node, ChangeState), ...]` and `edges: tuple[(Edge,
ChangeState), ...]`. Rules (R8): a symbol present on both branches with the
same chunk id → `UNCHANGED`; present on both with different chunk ids →
`CHANGED` (chunks are content-addressed per blob, so an edited file yields
new chunk ids — multi-branch spec §6.1); present on one side only → `ADDED` /
`REMOVED` relative to `branch_a`; edges are compared as sets of
`(source, target, kind)` per branch. The "changed only" toggle filters
`UNCHANGED` out of both tuples. The overlay colors nodes and edges by state
in the existing renderer (`2_Graph.py:180-206`).

Reader support: two branch-scoped methods on `BundleReader` —
`branch_symbol_chunks(branch) -> dict[qualified_name, chunk_id]` over
`branch_chunks JOIN chunks` (`db.py:206-215`, `chunks.qualified_name` v15),
and `reference_rows(branch=...)`. The second needs the `branch` column that
schema v17 adds to `node_references` (program plan P1.1 `:31`); on v16 the
reference edges are branch-agnostic, which is why the compare overlay is a
U1 item — on P0 the bundle holds one branch and the comparison is not
computable. `FakeBundleReader` (`test_graph_service.py:390-433`) and the
fixture schema (`_fixture.py:8-22`, which has no branch tables) grow the two
methods and the `branches` / `branch_chunks` tables.

"Add to question" (`2_Graph.py:262-266`) appends `AttachedSymbol(selected,
project, branch)`; on the chat page the attached symbol's `(project, branch)`
joins the active pin as a cell (added once — cells are a set), and when no pin
is active a **one-shot** pin with that single cell is created (AC-30), so the
woven question and the tool calls agree on the branch. This is the only path
that puts a second project into a pin (§6.4a).

### 6.12 Capability gating and staging U0 / U1 / U2

New file `harness/ask_your_docs/scope_capabilities.py` (~60 lines):

```python
@dataclass(frozen=True, slots=True)
class ScopeCapabilities:
    branch_selector: bool   # "branch" in every tool's inputSchema properties
    changed_slice: bool     # "changed" in search_codebase's scope enum
    diff_slice: bool        # "diff" in search_codebase's and grep's scope enum

def inspect_scope_capabilities(tools: Sequence[BaseTool]) -> ScopeCapabilities: ...
```

It reads each loaded tool's `args_schema`, which the adapter sets to the raw
`inputSchema` dict (`langchain_mcp_adapters/tools.py:531`); today the golden
registration surface has `scope` enum `["project","deps","all"]` and no
`branch` under any `inputSchema` (`tests/fixtures/goldens/
mcp_registration_surface.json`). **Delivery to the app without touching `build_agent`'s callers.**
`build_agent` keeps its `(graph, llm)` return shape byte for byte — the eval
binding unpacks a pair (`binding.py:352` `graph, _ = await build_agent(`),
so does the app (`app.py:264` `agent, llm = get_agent(...)`), the docstring
(`agent.py:3`) and `test_binding.py:251`'s `_fake_build_agent`; a third tuple
element would raise `ValueError: too many values to unpack` on the first eval
run. Instead, `scope_capabilities.py` defines the frozen
`BuiltAgent(graph, llm, scope_capabilities: ScopeCapabilities)`, and
`agent.py` gains `build_agent_with_scope_capabilities(...same signature...)
-> BuiltAgent`, which owns today's `build_agent` body (`agent.py:269-370`)
plus the one-time `inspect_scope_capabilities(tools)` call and the
`branch_selector` hand-off to `_assemble_prompt`. `build_agent` becomes a
three-line wrapper: `built = await build_agent_with_scope_capabilities(...);
return built.graph, built.llm`. `app.py`'s `get_agent` (`app.py:82-91`) calls
the new function and caches the `BuiltAgent` under the same
`st.cache_resource` key, so the sidebar, the popover and the graph page read
one record per agent build; `binding.py`, `cli.py` and `test_prompt_seam.py`
keep calling `build_agent` unchanged (AC-27). The app hides every control
whose capability is false; the interceptor never sends an argument the
capability does not cover (§6.4 step 2). A server without `branch` therefore
hides the controls and never errors (R10).

| Stage | Server precondition | Visible / active |
|---|---|---|
| **U0** (now) | P0, schema v16 | `QuestionScope` and the interceptor (project / package / code rules only); YAML block; "Scope defaults" button and panel with Project / Code / Package plus the **branch listing shown as a read-only caption per project** (no branch control, nothing sent — §6.7); footer from `meta.branch` + the cell's project; popover with Project, a caption naming the stamped branch (no Branches control), "keep for next", Clear, and the chip row; graph page branch label; `BundleReader.branches()` and `WorkspaceBranchListing`; `streamlit>=1.57` floor; catalog and rule 7 **not** rendered |
| **U1** (after P1) | `branch` on all nine tools, v17, retirement, `base_name` stamped (P1.6) | `branch` rules in the interceptor; fan-out over cells with `branch` sent (§6.4); rule 7 and the catalog branch listing; the Branch selectbox in the panel and the Branches multiselect in the popover; `compare with <base>` and `pin <branch>` chips; graph "Compare with" overlay and "changed only" |
| **U2** (after P2) | `changed` / `diff` scope values; the landing-unit index (P2.8) | Slice controls in the panel and popover; slice injection on `search_codebase` / `grep`; the "merged" picker group and the catalog tombstone marker (both pin a landing sha with `scope=diff`, §6.10); `show the diff` chip; slice segment in the footer |

**Dormant code, three plans.** Code and tests for U1 and U2 are written with
U0, driven by fake tools that advertise `branch` / `changed` / `diff` and by a
fake reader with two branches; they ship dormant behind `ScopeCapabilities`
and become active when the server advertises the capability. The work is
delivered as three plans: **U0** (§6.2, §6.3's project / package / code
rules, §6.5, §6.7, §6.8, §6.10 without the multiselect and the merged group,
§6.12, §7 — mergeable now), **U1** (§6.3's branch rules, §6.4, §6.6, §6.9's
compare / pin chips, §6.11's compare overlay — mergeable now, activated by
P1.9 and P1.6), **U2** (slice controls, the merged group, `show the diff` —
activated by P2.3 and P2.8). A stage's PR is gated on its own acceptance
criteria (§10 tags each AC with its stage); dormant-stage ACs run against
fakes in the same PR. The commit of the multi-branch amendment (Status) is a
precondition of the U1 plan.

### 6.13 Module map and file budget

| Module | Status | Size after | Owns |
|---|---|---|---|
| `harness/ask_your_docs/question_scope.py` | new | ~190 | `QuestionScope`, `ScopeCell`, `ScopeKind`, `ScopeSlice`, `ScopeCode`, `ScopeBranchDefault`, `ScopeDefaultsOverride`, `resolve_question_scope_defaults`, `resolve_default_branch`, `scope_prefix` (moved from `agent.py:140-149`; lazy export re-pointed) |
| `harness/ask_your_docs/scope_interceptor.py` | new | ~240 | contextvars, `intercept_question_scope`, per-argument rules, `fan_out_over_cells` (target-cell selection, §6.4), `merge_cell_results`, `ScopeObservations`, `CellObservation`, `BranchOrigin` |
| `harness/ask_your_docs/scope_capabilities.py` | new | ~70 | `ScopeCapabilities`, `inspect_scope_capabilities`, `BuiltAgent` |
| `harness/ask_your_docs/answer_footer.py` | new | ~190 | `render_answer_footer`, `derive_follow_up_chips`, `apply_follow_up_chip`, `FollowUpChip`, `FollowUpKind` |
| `harness/ask_your_docs/scope_panel.py` | new (Streamlit-only) | ~280 | `render_scope_defaults_button`, `render_scope_defaults_panel`, `render_scope_pin_popover`, `render_scope_chip_row`, `render_graph_branch_row` |
| `harness/ask_your_docs/graph_compare.py` | new | ~120 | `compare_branch_graphs`, `BranchGraphComparison`, `ChangeState` |
| `harness/ask_your_docs/agent.py` | edit | < 500 (468 today, target ≤ 468) | `_intercept` becomes a delegate; `_active_scope`, `ToolScope` and `scope_prefix` leave; `ask()` takes `QuestionScope` + `observations`; `_assemble_prompt` threads `branch_selector_advertised`; new `build_agent_with_scope_capabilities -> BuiltAgent`; `build_agent` is a three-line wrapper with its `(graph, llm)` shape unchanged |
| `harness/ask_your_docs/__init__.py` | edit | — | `_LAZY["scope_prefix"] = "question_scope"` (`:21-28`); public names unchanged |
| `harness/ask_your_docs/app.py` | edit | ~320 | sidebar block `:113-143` replaced by the button/panel fragments; chip row `:173-183` replaced by `render_scope_chip_row`; the inline send block `:218-276` extracted into `send_question(question: str, images: tuple[ImageAttachment, ...], scope: QuestionScope) -> None`, called by the chat input and by the follow-up chips; `get_agent` caches a `BuiltAgent`; transcript entries store `(answer, footer, chips)` |
| `harness/ask_your_docs/bundle.py` | edit | ~230 | `branches()`, `branch_symbol_chunks()`, `reference_rows(branch=)`, `IndexedBranch` |
| `harness/ask_your_docs/catalog.py` | edit | ~130 | `WorkspaceBranchListing` (value object), `workspace_branch_listing`, `render_catalog(branches=)` |
| `harness/ask_your_docs/attachments.py` | edit | ~170 | `AttachedSymbol` |
| `harness/ask_your_docs/pages/2_Graph.py` | edit | ~320 | branch row, compare overlay wiring |
| `harness/core/prompts/system_v1.j2` | edit | +12 | rule 7 inside the `is defined` guard (§6.6) |
| `retrieval/config/ask_your_docs_models.py` | edit | ~130 | `ScopeDefaultsConfig` with `_DEFAULT_*` constants |
| `defaults/default_config.yaml` | edit | +10 | `ask_your_docs.scope` block |
| `pyproject.toml` | edit | 1 line | `streamlit>=1.57` floor with its WHY comment (§6.10) |
| `examples/harness/ask_your_docs_agent/README.md` | edit | — | replaces the sidebar-pickers paragraph (`:123-129`; conformance-checked, `tests/test_doc_conformance.py:34-42`) |

Rules applied: every new closed vocabulary is a `StrEnum`; every new value
object is `@dataclass(frozen=True, slots=True)`; the older plain-`str`
vocabularies the harness already has (`Node.node_type`, `PolicyVerdict.kind`,
`text_only_fallback: Literal`) are left as they are — the `StrEnum` rule
applies to new code. Optional service dependencies use Null objects; the two
`| None` parameters on `ask()` are per-call inputs following the existing
`image_store` shape, not service dependencies. `agent.py` is imported by the
eval binding and must keep `_intercept` and `serve_connection`
(`binding.py:320`); the lazy exports of `__init__.py:12-28` keep their names.

---

## 7. Configuration

Appended to the `ask_your_docs:` block of
`python/pydocs_mcp/defaults/default_config.yaml` (today `:345-362`), modeled by
a new `ScopeDefaultsConfig` sub-model mounted as `AskYourDocsConfig.scope`
(`ask_your_docs_models.py:70-79`, `extra="forbid"`, so the sub-model is
mandatory for the key to be accepted). Field defaults live in `_DEFAULT_*`
module constants; the YAML restates them for readers by the documented
exemption. Env overrides follow the existing prefix:
`PYDOCS_ASK_YOUR_DOCS__SCOPE__MAX_CELLS=2`.

```yaml
ask_your_docs:
  # …architecture / multimodal / images unchanged…
  scope:                        # soft defaults for the chat and graph pages;
                                # the sidebar "Scope defaults" panel overrides
                                # them for one session only
    project: any                # any | <indexed project name>; "any" sends no
                                # project (the server's union across bundles)
    branch_default: base        # base | checked_out — "base" = the bundle's base
                                # branch (branches.base_name; stamped by multi-branch
                                # P1.6, so it resolves to nothing on P0 bundles),
                                # shown as "main (base branch)" in the panel;
                                # "checked_out" sends nothing (the server's own default)
    branch_name: ""             # an indexed branch name; wins over branch_default
                                # when non-empty; checked against the listing at
                                # resolution time, not at config load
    slice: whole_branch         # whole_branch | changed_files | diff_hunks;
                                # the last two need the server's scope=changed /
                                # scope=diff values (multi-branch P2) and apply
                                # to search_codebase and grep only
    code: all                   # all | own | deps  (today's own-vs-dependency filter)
    package: ""                 # "" = no package default
    max_cells: 4                # fan-out cap: the most (project, branch) cells
                                # one tool call may query under a pin; refused
                                # before any call when exceeded
```

Validation: `project`, `branch_name` and `package` are strings;
`branch_default: ScopeBranchDefault = Field(default=ScopeBranchDefault.BASE)`
— the two symbolic values are a closed vocabulary and stay a `StrEnum` (D13),
split from the free name so that a branch literally named `base` or
`checked_out` remains addressable through `branch_name`; the name is checked
against the listing at resolution time, not at config load, because the
config is workspace-agnostic; `slice` and `code` are the enums of §6.2;
`max_cells: int = Field(ge=1, le=16)`. `tests/test_config_ask_your_docs.py:55-60`
requires `AppConfig.load().ask_your_docs == AskYourDocsConfig()`, so YAML and
`Field` defaults must agree; `test_default_yaml_ships_the_block_keys` (`:113`)
grows the seven `scope` keys.

Every tunable of this design is in this block; nothing is a CLI flag and
nothing is an MCP parameter. The follow-up chip count (§6.9) is the number of
`FollowUpKind` members, not a tunable.

---

## 8. Contract guarantees

- **No MCP surface change.** No tool, parameter or envelope field is added;
  the harness only reads what the server advertises
  (`docs/tool-contracts.md` §3, §5.2). Client-side merged results never reach
  the server and never change the per-cell validated envelope.
- **Byte-identity of the single-cell DEFAULT path, stated precisely.**
  With the shipped YAML (`project: any`, `branch_default: base`,
  `branch_name: ""`, `slice: whole_branch`, `code: all`, `package: ""`) and no
  pin:
  - on U0 the interceptor sends exactly today's arguments for every call:
    nothing is injected (project `any` → nothing; `code: all` → nothing; no
    `branch` capability → nothing), and a model-passed argument is kept
    unless it names an unknown project (§6.3's one deviation);
  - on U1 nothing changes for a union request (`project=""`), and for a
    request naming a project the interceptor injects `branch=<resolved base
    of the effective project>` only when that project's `base_name` is
    stamped, indexed, and differs from the checked-out row — the owner's
    chosen default (§6.2 `resolve_default_branch`); setting `branch_default:
    checked_out` restores today's bytes on every request;
  - the assembled prompt is byte-identical whenever `branch` is not
    advertised (rule 7 and the catalog listing are both gated, §6.6), which
    is every U0 deployment; `SYSTEM_PROMPT` and the eval seed file are
    unchanged in all stages.
- **The eval binding is untouched.** `binding.py` never calls `ask()`
  (`binding.py:366-369`), so the scope contextvar is `None` and the
  interceptor is a strict passthrough (§6.3); `build_agent` is called without
  scope arguments (`binding.py:352-364`), keeps its `(graph, llm)` return
  shape (§6.12) and its prompt assembly receives
  `branch_selector_advertised=False` by default; the control-arm doctrine of
  `agent.py:304-315` and the delivery-map digest golden
  (`test_binding.py:187-217`) are unaffected. The `_intercept` and
  `serve_connection` names imported at `binding.py:320` are kept.
- **Every tunable is YAML** (§7); the panel is a session override of YAML,
  not a second source of defaults.
- **Freeze manifests.** The core prompt pool is outside the prompt-freeze
  golden (`test_prompt_freeze.py:47-55`); rule 7 hits the seed-parity test only
  if `SYSTEM_PROMPT` bytes change, which §6.6 prevents and §11 V4 verifies.

---

## 9. Error handling

| Case | Behavior |
|---|---|
| E1 Model names a project the listing does not know (DEFAULT) | replaced by the default cell's project; structured log `scope_default_replaced {tool, argument, passed, replacement}`. When the default project is `any` the replacement is `""` — a union across every loaded bundle, i.e. a widening — the record carries `replacement: ""` and the footer origin reads `agent-chosen → default` (§6.3, §6.8) |
| E2 Model names a branch the listing lacks for the effective project (DEFAULT, U1) | replaced by `resolve_default_branch(...)` — possibly nothing — and logged (same record); the server's own unknown-branch error (multi-branch spec §6.11) is thus never reached from DEFAULT |
| E3 Unknown branch under PIN | cannot happen: pins are built from the closed listing (the multiselect accepts no free text, §6.10) |
| E4 Fan-out exceeds `max_cells` | refused before any handler call; error tool result naming the cap and the YAML key (§6.4 step 1); the model reports it, the footer shows no cells. The popover already refuses to build such a pin (§6.4a), so E4 is reachable only through graph attaches |
| E5 A cell errors during fan-out | partial: kept as labeled error text, `isError=False`; all cells: `isError=True`; transport exception: propagates as today |
| E6 Stale index | ` · index stale` in the footer for that cell, never suppressed; on multi-bundle servers the staleness flag describes the first bundle's probe (`server.py:487-496`) until a per-project probe exists — the footer tooltip says so; a hook for the multi-branch program, not this design. The sha is exact (it comes from the listing, §6.8) |
| E7 Server does not advertise `branch` / `changed` / `diff` | argument-bearing controls are hidden: the panel's Branch selectbox and the popover's Branches multiselect (each replaced by a read-only caption naming the stamped branch), the graph "Compare with" and "changed only", rule 7, the catalog branch segment, and the `compare with` / `pin` / `show the diff` chips are absent; Slice is absent. Informational surfaces stay: the branch caption and the footer's `meta.branch`. Nothing is sent; no error |
| E8 Pre-v16 bundle (no `branches` table) | `branches()` returns `()`; the branch caption / pickers show "no branch information"; the footer segment reads `answered from demo · no branch · server default` (AC-34) |
| E9 Landing unit outside the retention window (U2, merged group) | the server's `InvalidArgumentError` naming `git.diff_chunks.retain` and the `branches pin` command (multi-branch spec §6.11) is shown verbatim in the transcript; the chip stays so the user can clear it |
| E10 Streamlit older than 1.57 installed | the `[harness-ask-your-docs]` extra pins `streamlit>=1.57` (§6.10); an environment that bypasses the pin fails at import of `st.bottom` with the toolkit's own `AttributeError` — no shim, no fallback shape |
| E11 `slice != WHOLE_BRANCH` with `code == DEPS` | rejected at `QuestionScope` construction with both values in the message; the panel and popover disable the combination |
| E12 Kept pin references a cell the reloaded listing no longer has (workspace changed) | the whole pin is dropped with a toast naming the missing cell (§6.7 "Pin lifecycle") |

---

## 10. Acceptance criteria

Each criterion is tagged with the stage whose PR it gates (§6.12); U1 / U2
criteria run against fake tools and a fake reader in the U0 code base and
stay green while dormant.

- **AC-1** [U0] With `ACTIVE_QUESTION_SCOPE` unset, `intercept_question_scope`
  calls the handler with the request unchanged for every tool and every
  argument set, including unknown project names.
- **AC-2** [U0] Under `DEFAULT` with the shipped YAML on U0, the arguments
  sent equal the model's arguments for every call in a fixture of the nine
  tools whose `project` is empty or names a listed project.
- **AC-2b** [U1] Under `DEFAULT` with the shipped YAML and a listing where
  `backend` has the rows `feature/x` (default, `base_name = main`) and `main`,
  a call passing `project=backend` and no `branch` is sent `branch=main`; the
  same call with `branch_default: checked_out` is sent no `branch`; a union
  call (no project) is sent no `branch`; with `base_name` NULL (a P0 bundle)
  nothing is sent.
- **AC-3** [U0] Under `DEFAULT`, a model-passed project the listing does not
  know (neither a name nor a bundle stem) is replaced by the default cell's
  project — `""` when the default is `any` — and one `scope_default_replaced`
  log record is emitted with the passed and replacement values; a bundle
  stem passes through unchanged.
- **AC-4** [U0] Under `DEFAULT` with `code: own`, `scope="project"` is
  injected on `search_codebase` only; `grep`'s `scope` is untouched; a
  model-passed `scope="deps"` is never overwritten under `DEFAULT`.
- **AC-5** [U1] Under `PIN` with one cell, `project` (and `branch` when
  advertised) are overwritten on all nine tools regardless of what the model
  passed.
- **AC-6** [U1] Under `PIN` with three cells and `branch` omitted, the handler
  is called three times in cell order, and the merged `CallToolResult` has
  labeled text blocks `## <project> · <branch>` before each cell's blocks,
  `structuredContent.items` carrying `branch` and `project` on every item,
  and `structuredContent.meta` equal to the first cell's `meta` with no added
  key.
- **AC-6b** [U0] Under `PIN` with the cells `{(a, main), (b, main)}` and a
  server that does not advertise `branch`, the handler is called twice, with
  `project=a` and `project=b`, and no `branch` key in either call.
- **AC-7** [U1] Under `PIN` with two cells in one project and `branch=<pinned
  name>`, the handler is called exactly once with that branch; with the cells
  `{(a, main), (b, main)}` and `branch=main` it is called twice (one per
  matching cell); with `branch=<unpinned name>` it fans out over every cell
  and one `scope_pin_branch_ignored` record is logged.
- **AC-8** [U1] With `max_cells: 2` and a three-cell pin, the handler is never
  called and the returned result has `isError=True` and a text naming
  `max_cells=2` and `ask_your_docs.scope.max_cells`.
- **AC-9** [U1] One erroring cell out of two yields `isError=False` with the
  error text under its label; two erroring cells yield `isError=True`.
- **AC-10** [U0] `ScopeObservations` passed into `ask()` contains one
  `CellObservation` per handler call with `branch_origin` `PINNED` /
  `DEFAULT` / `AGENT_CHOSEN` / `SERVER` as §6.5 defines (every observation
  under `PIN` is `PINNED`; a model-passed branch equal to the default's value
  is `AGENT_CHOSEN`), and the same object is populated when the interceptor
  runs in a copied-context child task.
- **AC-11** [U0] `_assemble_prompt(name, catalog, None)` equals
  `f"{SYSTEM_PROMPT}\nIndexed projects and packages:\n{render_catalog(catalog)}"`
  byte for byte; `render_shared("system_v1")` succeeds with **no variables**
  under `StrictUndefined` and equals the pre-change bytes (a golden of today's
  rendered prompt taken before the template edit); `test_prompts_package.py`
  passes unchanged.
- **AC-12** [U1] With `branch_selector_advertised=True` the rendered prompt
  contains rule 7 exactly as §6.6 prints it and the catalog lines carry the
  branch segment in the §6.6 shape with the `(default)` marker and only
  pickable rows; [U2] with `diff_slice` advertised a `MERGED` tombstone
  follows as `feature/old (merged into main @3e1a9c2)`.
- **AC-13** [U0] `render_catalog(catalog)` and `render_catalog(catalog,
  branches=None)` return identical bytes.
- **AC-14** [U0] `SqliteBundleReader.branches()` returns the rows of
  `branches` ordered `is_default DESC, name`, `()` on a bundle without the
  table, re-raises any other `OperationalError`, and never opens the bundle
  read-write (the `user_version=99` guard of `test_graph_service.py:61-65`
  extended to the new methods).
- **AC-14b** [U0] On a fixture with one default row, one `MERGED` tombstone
  (`merged_into` = a 40-hex sha) and one 40-hex landing row, `branches()`
  returns three rows; `WorkspaceBranchListing.pickable` excludes the landing
  row and the tombstone, `merged` holds the tombstone, `knows_project`
  accepts the project name and the bundle stem, and `render_catalog(branches=)`
  prints only the pickable rows unless `diff_slice` is advertised.
- **AC-15** [U0] `inspect_scope_capabilities` returns all-false on the current
  registration golden's schemas and `branch_selector=True` when every tool's
  schema gains `branch`.
- **AC-16** [U1] `compare_branch_graphs` over a fake reader classifies
  same-chunk symbols `UNCHANGED`, different-chunk `CHANGED`, one-sided
  `ADDED` / `REMOVED`, and edge-set differences as `ADDED` / `REMOVED` edges;
  the "changed only" filter drops every `UNCHANGED` node and edge.
- **AC-17** [U1; `show the diff` U2] `derive_follow_up_chips` yields `compare
  with <base>` only for exactly one answered cell whose base is indexed and
  differs, `show the diff` only when `diff_slice` is advertised and no
  observation was a diff-hunks slice, `pin <branch>` only for cells the
  `kept_pin` does not contain and only on U1; two answered cells yield no
  `compare with` and two `pin` chips in `(project, branch)` order; at most
  three chips, deterministically for a shuffled observation list.
- **AC-18** [U0] `render_answer_footer` prints one segment per distinct cell
  in sorted order, in the §6.8 format (`all projects` for a union answer on a
  multi-project listing; the listing's sha for a sent cell; `server default`
  for `SERVER`), with ` · index stale` present whenever any observation of
  that cell is stale.
- **AC-19** [U0] AppTest, state 1: the sidebar has a button labeled "Scope
  defaults" and no widget keyed `scope_project`, `scope_code` or
  `scope_package`; the main area has no scope caption; the popover
  (key `scope_pin_popover`) exists and, with `branch_selector` false, holds
  no multiselect.
- **AC-20** [U0] AppTest, state 2: after the button is clicked (or
  `scope_defaults_open` is seeded) the sidebar shows the `scope_defaults_*`
  Project / Code / Package controls and the soft-defaults caption; the
  Branch row is a caption naming the stamped branch when `branch_selector` is
  false and a selectbox when it is true; Slice is absent when `changed_slice`
  is false.
- **AC-21** [U0] AppTest, state 3: with a two-branch pin seeded in
  `st.session_state["scope_pin"]`, the popover button label contains
  `2 branches`, the chip row shows one `scope_chip_*` button per branch plus
  "clear all", and the last user message shows the caption `backend · main,
  feature/retry`.
- **AC-21b** [U0] AppTest: a seeded one-shot pin is `None` in session state
  after a send; a seeded kept pin survives one send and is `None` after
  "clear all".
- **AC-22** [U0] `AppConfig.load().ask_your_docs.scope ==
  ScopeDefaultsConfig()`, `branch_default` rejects a value outside
  `ScopeBranchDefault`, an unknown key under `scope:` is rejected, and
  `PYDOCS_ASK_YOUR_DOCS__SCOPE__MAX_CELLS=2` overrides the cap.
- **AC-23** [U0] `QuestionScope(kind=DEFAULT, cells=(a, b))`,
  `QuestionScope(kind=DEFAULT, cells=(ScopeCell("p", "main"),))`,
  `QuestionScope(cells=())` and `slice=DIFF_HUNKS` with `code=DEPS` each raise
  with the offending values in the message.
- **AC-24** [U0] The eval binding's delivery-map digest golden and
  `test_binding.py` pass unchanged; `binding.py` imports `_intercept` and
  `serve_connection` as before.
- **AC-25** [U0] The README paragraph at
  `examples/harness/ask_your_docs_agent/README.md:123-129` describes the new
  shape and `tests/test_doc_conformance.py` passes; no `PR #`, sub-PR or task
  jargon is introduced (the audit grep of CLAUDE.md).
- **AC-26** [U0] Every new module and `agent.py` are under 500 lines (the
  ≤ 468 figure for `agent.py` is a target, not a gate); `ruff format
  --check`, `mypy`, `complexipy --max-complexity-allowed 15` and `vulture`
  pass.
- **AC-27** [U0] `build_agent(...)` returns a 2-tuple
  (`len(await build_agent(...)) == 2`);
  `build_agent_with_scope_capabilities(...)` returns a `BuiltAgent` whose
  `scope_capabilities` matches the fake tool list; `binding.py:352` and
  `app.py`'s unpacking are unchanged.
- **AC-28** [U0] `scope_prefix(QuestionScope(kind=PIN, cells=(("backend",
  "main"), ("backend", "feature/retry")), slice=DIFF_HUNKS, code=OWN))` ==
  `"[pinned scope: project=backend, branches=main, feature/retry, diff hunks,
  own code only] "`; for `kind=DEFAULT` it returns `""`; a one-cell pin with
  `branch=""` renders `project=backend` only (today's bytes,
  `test_image_attachment.py:94`).
- **AC-29** [U0] `resolve_default_branch`: `BASE` → the project's default
  row's `base_name` when that name is listed for the project and differs from
  the default row, else `""`; `CHECKED_OUT` → `""`; a listed `branch_name` →
  itself; an unlisted `branch_name` → `""` plus one `scope_default_replaced`
  log; a union project → `""`.
- **AC-30** [U0] Attaching `AttachedSymbol("mod.Foo", "backend",
  "feature/retry")` with no active pin yields a one-shot pin with the cell
  `(backend, feature/retry)` and the woven question `` Regarding `mod.Foo`: …
  ``; with an active pin the cell is added once.
- **AC-31** [U1; `SHOW_DIFF` U2] `apply_follow_up_chip(chip, kept_pin)` for a
  `COMPARE_WITH` chip returns the canned question and a one-shot pin of two
  cells while leaving `kept_pin` untouched; for `PIN_BRANCH` it returns no
  question and a kept pin containing the cell; the chat page calls
  `send_question` only when a question is returned.
- **AC-32** [U2] The merged group lists a `MERGED` tombstone labeled
  `feature/old (merged into main @3e1a9c2)` after the live names and never in
  the live group; selecting it produces the cell `(backend, <merged_into>)`
  with `slice=DIFF_HUNKS`.
- **AC-33** [U0] "Reset to shipped" restores the panel widgets to
  `ScopeDefaultsConfig()` values (`ScopeDefaultsOverride()` all-`None`).
- **AC-34** [U0] On a pre-v16 fixture the footer segment reads `answered from
  demo · no branch · server default`.

---

## 11. Testing plan

**Unit, headless, in the core suite (`pytest tests/harness/ask_your_docs
-q`):**

- `test_scope_interceptor.py` (new): a fake `MCPToolCallRequest` (the
  adapter's dataclass, `interceptors.py:51-73`) and a recording fake handler
  returning `mcp.types.CallToolResult` per the server's shape (one
  `TextContent` + `{text, items, meta}`); parametrized over the nine tool
  names; covers AC-1…AC-10, AC-2b, AC-6b. The copied-context case (AC-10)
  runs the interceptor inside `asyncio.create_task(...,
  context=contextvars.copy_context())`.
- `test_question_scope.py` (new): invariants (AC-23), `resolve_default_branch`
  against a fake listing (AC-29), `scope_prefix` rendering (AC-28),
  `ScopeDefaultsOverride` layering and "Reset to shipped" values (AC-33).
- `test_prompt_seam.py` (extended): AC-11, AC-12 with a golden file of
  today's rendered `system_v1` bytes captured before the template edit;
  AC-27 (`build_agent` arity and `BuiltAgent`) with the existing fake tool
  list; `test_prompts_package.py` stays as the no-variables render pin.
- `test_catalog.py` (new or extended): AC-13, AC-14b's listing methods and
  the branch line shapes.
- `test_bundle_branches.py` (new): AC-14, AC-14b over the `_fixture.py`
  schema grown with `branches` and `branch_chunks`.
- `test_scope_capabilities.py` (new): AC-15 driven from the registration
  golden JSON.
- `test_graph_compare.py` (new): AC-16 over `FakeBundleReader` grown with
  `branch_symbol_chunks` and branch-scoped `reference_rows`.
- `test_answer_footer.py` (new): AC-17, AC-18, AC-31, AC-34, including
  shuffled input.
- `test_attachments.py` (new or extended): AC-30 (`AttachedSymbol` into a
  pin, woven question).
- `test_config_ask_your_docs.py` (extended): AC-22.
- `test_binding.py`: unchanged, must stay green (AC-24).

**AppTest smoke tests (`pytest.importorskip("streamlit")`; run where the
`[harness-ask-your-docs]` extra is installed — the main checkout's venv, not
the worktree's):** `test_app_scope_states.py` (new) with the three states
(AC-19…AC-21, AC-21b, AC-32 on U2, AC-33), seeding `st.session_state` for
the panel flag and the pin. Verified on the installed source: a popover delta
parses as a generic `Block` (`streamlit/testing/v1/element_tree.py:2672-2673`
— only chat_message / column / expandable / tab are special-cased), so its
children are reachable through the flat accessors `at.multiselect`,
`at.toggle`, `at.button`, `at.selectbox` (`testing/v1/app_test.py:910, :1154,
:497, :966`); its open state is not exposed, which is why AC-21 seeds the pin
and asserts the label and the chip row. These tests need a fixture workspace:
a temporary directory with one `make_bundle` bundle (`_fixture.py:25-72`)
pointed to by `PYDOCS_WORKSPACE`, which replaces the reliance on
`~/pydocs-index` in `test_app_attachment.py:11-21`.

**Toolkit verifications (done during design; each names the evidence and the
test that keeps it true):**

- **V1** Verified from the 1.59.1 source and the release notes: `st.bottom`
  is public since 1.57.0 (`streamlit/__init__.py:114`; `_bottom` deprecated
  with sunset 2026-07-01, `:121-122`), an inline `st.chat_input` inside
  `with st.bottom: st.columns(...)` is inline by design and pinned by the
  container (`elements/widgets/chat.py:1012-1023`), and `st.bottom` refuses
  the sidebar and dialogs (`elements/bottom.py`). Implementation step: the
  floor bump to `streamlit>=1.57` (§6.10). The private `_bottom` shim is not
  used — its sunset is past.
- **V2** Verified: `st.popover` takes `key` and `on_change`
  (`elements/layouts.py:1329-1330`, since 1.55.0), and a keyed popover is
  closed by writing `False` to its session-state key before `st.rerun()`.
  One AppTest asserts the `scope_pin_popover` key exists (AC-19).
- **V3** Verified (above): the popover parses as a generic `Block`;
  `at.multiselect` / `at.toggle` reach its children; open state is not
  exposed, so AC-21 seeds the pin.
- **V4** The `is defined` guard renders to the exact pre-edit bytes when the
  variable is absent (Jinja `trim_blocks` / `lstrip_blocks` and the trailing
  newline, §6.6): pinned by AC-11's golden, and AC-11 also asserts
  `render_shared("system_v1")` succeeds with no variables under
  `StrictUndefined` (`test_prompts_package.py:21,36,60` stay green).
- **V5** The multi-branch amendment (landing units, §6.5b / §6.5c / §6.8a)
  is committed as `1c371bc` and its rules are already encoded in §2, §6.6,
  §6.9, §6.10 and §9 E9. This design cites it by section, so a later
  renumbering changes nothing here. Before U1 starts, re-read §6.5b and
  §6.8a of the committed text against §6.10.
- **V6** Verified by precedent: scope and observations are per-`ask()` task
  state — set inside the coroutine that `run()` schedules on the one cached
  loop thread (`app.py:43-52`) — so a rerun rebuilds them from session state
  and the agent cached by `get_agent` (`app.py:80-91`) holds only the
  interceptor closure; the container write-back is the `_reinspect_state`
  pattern (`agent.py:445`, `reinspect.py:62`); the tool task runs in a copied
  context (`langchain_core/runnables/utils.py:142-156`) and the tool node
  gathers calls (`langgraph/prebuilt/tool_node.py:858`). AC-10 pins it
  against the installed `langgraph` / `langchain-core` versions.

---

## 12. Open decisions for the owner

- **O1 — A branch under PIN that is indexed but not pinned.** D1 says the
  interceptor honors `branch=<one of the pinned/indexed branches>`; D4 says
  pins are hard. Default reading in §6.3: only pinned names are honored; an
  indexed-but-unpinned name is replaced by fan-out over the pin and logged.
  The alternative (honor any indexed name) makes the pin soft for `branch`
  only.
- **O2 — Merged group sequencing and label.** The landing-unit amendment
  settles what a merged entry sends (the landing sha with `scope=diff`,
  §6.10), which makes the "merged" picker group a U2 item that depends on the
  landing-unit index (program plan P2.8) and the `diff` scope value (P2.3).
  Confirm that P2.8 precedes U2 in the multi-branch program, and confirm the
  label format `feature/old (merged into main @3e1a9c2)`.
- **O3 — Shipped branch default spelled `base`, not `main`.** D3 lists the
  YAML value as `main` and words the panel entry "main = the base branch". A
  literal `main` has no row in a bundle indexed from another branch on P0
  (`storage/factories.py:904-915` stamps only the checked-out branch) and
  duplicates the server's base-branch detection. §7 spells the value
  `branch_default: base` (resolved to `branches.base_name`, labeled "main
  (base branch)" in the panel — D3's own label text, kept verbatim). Proposed
  resolution: ratify `base`; the alternative is the literal name plus an
  unknown-name fallback to the default row. Note that `base_name` is stamped
  only from P1.6 (§1.2), so either spelling resolves to nothing on P0.
- **O4 — One held session for the app.** Fan-out multiplies the per-call
  subprocess spawn of the app's tool binding (§6.4). Should the chat page
  move to the binding's held-session shape (`binding.py:309-327`) in the same
  change, or is the `max_cells` cap enough for now?
- **O5 — Gating the catalog branch listing on the `branch` capability**
  (§6.6). This is the only reading that keeps R7 and R11 both true on P0
  bundles; the alternative is to accept a catalog byte change on every
  post-P0 workspace and regenerate the prompt-seam expectations.
- **O6 — Per-project freshness in the footer.** The sha and staleness come
  from the first bundle's probe (§9 E6). Should the multi-branch program add
  a per-project probe (a server change), or is the tooltip caveat acceptable?

---

## 13. References

- `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md`
  (amended 2026-09-04, commit `1c371bc` — cited by section so a later
  renumbering changes nothing): §2 Terms (landing unit, base
  branch), §3.1 R4–R6, §3.2 Q5 selector shape, §3.3 R14, §4 non-goals
  (no cross-branch union), §6.1 storage, §6.4 read path, §6.5 `scope=changed`,
  §6.5a `scope=diff`, §6.5b landing units and retention, §6.5c membership
  validity, §6.8a retirement, §6.11 errors, §7 contract amendment (item 2:
  `branch: str = ""`, landing shas accepted), §10 roadmap.
- `docs/superpowers/plans/2026-09-03-multi-branch-indexing-program.md`
  (amended 2026-09-04, commit `1c371bc`) — P1.1 schema v17 (`:33`),
  P1.6 tracking policy / `base_name` (`:38`), P1.7 retirement (`:39`),
  P1.8 / P1.9 read path and parameter (`:40-41`), P2.1–P2.4 (`:64-67`),
  P2.8 landing-unit index (`:71`).
- `docs/tool-contracts.md` — §2.4 `meta.branch` (`:142-164`), §3 inventory
  (`:167`), §5.2 sanctioned parameter categories (`:450`).
- Harness: `harness/ask_your_docs/app.py` (`:43-52`, `:80-91`, `:95`,
  `:113-143`, `:173-183`, `:218-276`), `agent.py` (`:3`, `:52-62`, `:77-79`,
  `:81-88`, `:119-149`, `:186-215`, `:269-370`, `:419-468`, `:443-445`),
  `binding.py` (`:309-327`, `:350-369`), `reinspect.py` (`:62`),
  `catalog.py` (`:32-42`, `:57-64`), `bundle.py` (`:25-37`, `:87-94`),
  `graph_service.py` (`:132-136`), `pages/2_Graph.py` (`:118-122`,
  `:180-206`, `:262-266`), `attachments.py` (`:128-137`),
  `prompts/__init__.py` (`:47-50`, `:70`), `__init__.py` (`:12-28`),
  `harness/core/prompt_override.py` (`:30-52`),
  `harness/core/prompts/system_v1.j2` (`:28-50`),
  `retrieval/prompts/_loader.py` (`:13-20`, `StrictUndefined`).
- Server and storage: `application/branch_membership.py` (`:92-101`, the P0
  `BranchRecord` without `base_name`), `server.py` (`:487-496`, `:632-642`,
  `:698-852`),
  `application/tool_response.py` (`:27-37`, `:40-54`),
  `application/tool_router.py` (`:108-111`), `application/freshness.py`
  (`:85-90`), `application/mcp_inputs.py` (`:44-48`, `:237-240`),
  `application/multi_project_search.py` (`:211-225`, `:295-310`),
  `multirepo.py` (`:198-205`), `storage/factories.py` (`:904-915`),
  `storage/sqlite/branch_repository.py` (`:120-124`),
  `storage/branch_records.py` (`:16-33`), `db.py` (`:171-217`),
  `models.py` (`:41`, `:143-173`).
- Config: `retrieval/config/ask_your_docs_models.py` (`:70-79`),
  `defaults/default_config.yaml` (`:343-362`), `tests/test_config_ask_your_docs.py`
  (`:55-60`, `:113`).
- Tests: `tests/harness/ask_your_docs/test_prompt_seam.py` (`:36-40`,
  `:91-98`, `:150-155`), `test_prompts_package.py` (`:21`, `:36`, `:60` — the
  no-variables renders), `test_binding.py` (`:251` `_fake_build_agent`),
  `test_prompt_seed_parity.py` (`:38-49`), `test_prompt_freeze.py` (`:47-55`),
  `test_graph_service.py` (`:61-65`, `:390-433`), `_fixture.py` (`:8-22`,
  `:25-72`), `test_app_attachment.py` (`:11-21`), `test_image_attachment.py`
  (`:94`), `test_binding.py` (`:187-217`), `tests/test_doc_conformance.py`
  (`:34-42`), `tests/fixtures/goldens/mcp_registration_surface.json`.
- Toolkit (installed sources, versions as verified): `langchain_mcp_adapters`
  0.3.0 — `interceptors.py` (`:51-73`, `:112-121`), `tools.py` (`:122-160`
  error renderer, `:268-271` content conversion, `:274` `isError` raise,
  `:278-281` structured content, `:460-469` per-call session, `:527`, `:531`,
  `:547`), `client.py` (`:58`); `mcp` 1.28.1 — `client/session.py`
  (`:412-413`); `langchain_core` 1.4.9 — `tools/base.py` (`:1186-1196`),
  `runnables/config.py` (`:236-239`), `runnables/utils.py` (`:142-156`);
  `langgraph` — `prebuilt/tool_node.py` (`:858`); `streamlit` 1.59.1 —
  `elements/widgets/chat.py` (`:1012-1023`), `elements/layouts.py`
  (`:1319-1333`), `elements/bottom.py`, `elements/widgets/multiselect.py`
  (`accept_new_options`), `__init__.py` (`:110-122`),
  `testing/v1/element_tree.py` (`:2672-2673`), `testing/v1/app_test.py`
  (`:497`, `:910`, `:966`, `:1154`); release notes 1.45.0 / 1.55.0 / 1.57.0;
  pins `pyproject.toml:141-144`.
- Precedents: ADR 0008 and `session_start_injection.py:14-34` ("None keeps
  the prompt byte-identical"); `agent.py:77-79` (`_reinspect_state`, the
  mutable per-turn contextvar container); `_CODE_CHOICES` (`app.py:95`).
