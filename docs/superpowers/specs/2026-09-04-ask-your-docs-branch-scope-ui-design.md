# Ask-your-docs branch scope UI: soft defaults, hard pins, and labeled fan-out — Design

**Date:** 2026-09-04
**Status:** Draft for owner ratification. Encodes the fourteen owner decisions
D1–D14 as requirements R1–R13 (§3): D1–D13 of 2026-09-04, and D14 of
2026-09-15, which replaces the interface parts of D2, D3 and D4 with designs
A + C of the owner's proposals page (§13) — D1 and D5–D13 stand as ratified.
Two requirements restate a decision with a proposed change pending
ratification: R3 spells the shipped branch default `base` instead of D3's
`main` (§12 O3) and R7 gates the catalog branch listing on the `branch`
capability (§12 O5). The stage-U0 implementation of the 2026-09-04 text
landed as draft PR #267; its engine is kept and its interface is superseded
by this amendment (§6.12 U0r). Every claim about existing code cites a
`file:line` anchor at worktree HEAD `4fbe32d` (multi-branch P0). Toolkit
claims are verified against the installed sources (`streamlit` 1.59.1,
`langchain_mcp_adapters` 0.3.0, `langchain_core` 1.4.9, `mcp` 1.28.1) and
the toolkit release notes; §11 lists what each verification established.
**Amended 2026-09-15:** the stage-U0 interface is replaced by design A + C
(owner decision D14, see Amendments) — one "Searching in …" strip above the
composer with a keyed "Where to search" picker, typed one-shot `in:` / `on:`
tokens, a connection-only sidebar, the new on-screen vocabulary, and fan-out
block labels that name what was sent (R2, R3 and R4 restated; §6.4 rule 3,
§6.4a, §6.7, §6.8, §6.9, §6.10, the new §6.10a, §6.11–§6.13, §7–§12
revised). The engine — `QuestionScope`, the interceptor, fan-out,
observations, the footer and follow-up derivation — is unchanged.
**Revised the same day after review** (second pass, listed in the
Amendments section): one U0 cell shape for strip, token and graph cells;
the strip's targets as the only source of the project override; the named
`StripState` and its session key; the popover as its own trigger on both
pages; the token grammar's punctuation, empty-name and duplicate rules; the
"Only these" lifecycle; the union answer carries no teaching hint; the
`streamlit>=1.59` floor already on the branch.
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
several indexed branches (a) always see one sentence saying where the next
question will search ("Searching in …") and one control that changes it,
(b) keep soft session defaults the agent may override when a question calls
for it (the strip with no target, or one target with "Only these" off),
(c) restrict a question to one or several project-and-branch targets with
one tick, (c′) or name them inside the question with `in:` / `on:` for one
question only, (d) read on every answer which project, branch and slice it
came from, and (e) compare two branches on the graph page — all without
adding a tool, a parameter or an envelope field to the frozen MCP surface,
and without changing one byte of the tool arguments or the system prompt on
today's single-branch path.

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
sidebar pin). The two kinds are the engine's `ScopeKind`; on screen they are
**one place**, not two (D14): an always-visible "Searching in …" strip above
the composer whose state compiles to the scope — no target → DEFAULT over the
union; one target with "Only these" off → DEFAULT for that project; one
target with "Only these" on, or two or more targets → PIN, fan-out over the
cells. The sidebar keeps only the connection. A second, one-shot channel is
the question text itself: `in:<project>` / `on:<branch>` tokens, validated
against the indexed names and refused — nothing sent — on an unknown name.
Both kinds are carried by one frozen `QuestionScope` value object whose
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
follow-up buttons ("Ask this on main too", "Compare with main", "Keep
searching feature/retry", "Show what changed"). The graph page opens the
same picker, and gets a branch selector and a "compare with" overlay
computed over the reader with no server work. Controls whose
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
  plan P1.9 `:41`), schema v18 with a `branch` column on the tree-tier tables
  (plan P1.1 `:33`), the tracking policy that **populates `branches.base_name`**
  (plan P1.6 `:38` — the P0 `BranchRecord` is built without it,
  `application/branch_membership.py:92-101`, so the `base` default of §6.2 and
  the "Compare with <base>" button of §6.9 resolve to nothing before P1.6),
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
a sidebar has no natural per-question lifetime. That diagnosis stands. The
2026-09-04 decisions answered it by separating the two lifetimes (session
defaults vs per-question pin) into two hidden controls; the 2026-09-15 review
of the shipped stage U0 found that shape traded the old problems for two new
ones (D14): *two controls both called "scope"*, and *the picker holds one
project — branch x of A and branch y of B cannot be built here at all; the
server already accepts a two-project pin today, and the interface is the
only thing in the way*. The amended answer is one named control, always
visible, sticky, stating its own state in a sentence ("Searching in …"); the
per-question lifetime is served by typed tokens (§6.10a) and by the buttons
under an answer (§6.9), not by a second control. A multi-project,
multi-branch selection is a **matrix**, so the picker is a row per project
(§6.4a), never one project at a time.

---

## 2. Terms

- **Scope defaults**: session-level soft values (project, branch, slice, code,
  package) that fill in arguments the model leaves empty. The model may name
  another indexed project / branch / slice when the question calls for it.
  Shipped values come from YAML (§7); the strip (§6.7) overrides them for the
  session only — there is no sidebar panel. On screen the idea has no name of
  its own: the strip with no target, or one target with "Only these" off,
  *is* the default. `ScopeDefaultsConfig` / `ScopeDefaultsOverride` stay the
  code names.
- **Pin**: the engine's word (`ScopeKind.PIN`, never on screen — §6.7 words
  rule) for a hard scope forced onto every tool call of one question, exactly
  like today's sidebar pin. The strip's targets are **sticky** — they stay
  until changed (§6.7). A **one-shot** pin arises only from a typed-token
  question (§6.10a) and from a follow-up button (`ASK_ON`, `COMPARE_WITH`,
  `SHOW_DIFF`, §6.9); it is sent with that one question and leaves the strip
  untouched. There is no "keep for next".
- **Target**: one ticked project in the strip, with its chosen branches (on
  U0 the single stamped branch, informational). A target expands into one or
  more cells; the strip's chips are rendered **per cell** (§6.7), and
  removing a project's last cell removes the target.
- **Strip state**: the frozen `StripState(targets, only_these)` value object
  under the session key `scope_strip` (§6.7) — the one object that is
  sticky, seeded from YAML, edited by the picker, the chips, "Clear",
  "Keep searching" and the graph page, and compiled to a `QuestionScope`
  by `compile_strip_scope` (§6.1).
- **Token**: `in:<project>` or `on:<branch>` inside the question text, parsed
  by `scope_tokens.py` (§6.10a), validated against the listing, one-shot.
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
  NULL until P1.6 stamps it (§1.2). Under D14 the symbolic label "main (base
  branch)" of D3 has no on-screen control: `branch_default` / `branch_name`
  are YAML-only (§7), and the picker shows resolved names — the pills'
  preselected row is the resolved default (R3, §6.7).
- **Landing unit**: one first-parent step `c` on the base branch with the diff
  `c^1..c` — a merged branch's retained diff (multi-branch spec §2 Terms,
  §6.5b, amended 2026-09-04). In storage it is a `branches` row whose `name`
  is the full 40-hex landing sha, with `landing_kind` set (v18) and no `TREE`
  slice. It is addressed through the `branch` selector by that sha (full, or a
  unique prefix of at least 7 hex characters) and answers `scope=diff` only —
  any other scope on it returns empty with a suggestion. A retired branch's
  tombstone row points at it through `merged_into` = the landing sha, and a
  request naming the retired branch by **name** is refused with an error that
  names the landing sha (§6.8a). The UI therefore never sends a retired name:
  the merged tombstones listed after the live names in the picker's
  per-project branch pills (§6.10, U2) pin the landing sha.
- **Capability**: a fact the harness reads from the loaded tools' input
  schemas at startup — whether `branch` is a parameter, whether `changed` /
  `diff` are `scope` values (§6.12).
- **Stage U0 / U1 / U2**: what is implementable on P0 / after P1 / after P2
  (§6.12).
- **Observation**: the interceptor's record of one server call — the cell it
  sent, how the branch was chosen, and the response's `meta` (§6.5).

---

## 3. Requirements (owner decisions D1–D14, restated precisely)

D14 (2026-09-15) — *the U0 interface is A + C* — supersedes the interface
parts of D2, D3 and D4; R2, R3 and R4 below are restated under it. D1 and
D5–D13 stand. The proposals page that defines A and C is cited in §13.

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
- **R2 — The default view is the connection plus one sentence (D2 as
  restated by D14).** The sidebar shows connection settings only — no scope
  button, no scope panel. The default view carries one always-visible strip
  inside `st.bottom`, full width, directly above the chat input; with no
  target it reads "Searching in all projects, each on its indexed branch"
  followed by the "Change…" popover trigger (§6.7). The transcript keeps its
  per-question scope caption, rendered for pins only. Every answer gets a
  small footer line built from the tool responses' `meta` — project, branch,
  short head sha, slice, `index_stale` — worded `Searched <project> ·
  <branch> @<sha7> (<origin>) · <freshness>`, where the origin says whether
  the branch came from your default, "only these", or the agent's choice
  (§6.8).
- **R3 — Defaults are YAML-seeded and edited in the picker (D3 as restated by
  D14).** There is no sidebar defaults panel. `ask_your_docs.scope` still
  seeds the **initial strip state** — `project` (any | one),
  `branch_default` / `branch_name`, `code`, `package` — and is still the only
  place a deployment tunes defaults (§7). **Seed rule** (the only mapping
  from `ask_your_docs.scope.project` to the strip): `project: any` seeds
  **zero** targets; `project: <name>` seeds **one** target `(name, <its
  default row>)` with "Only these" off when the listing knows the name, and
  zero targets (with one structured `scope_default_replaced` log) when it
  does not; `code` / `package` seed the picker's "More" values. The strip's
  targets are thereafter the **only** source of the project override
  (§6.1). The former panel controls move into the "Where to search" picker
  (§6.7, §6.10): Project → one `st.checkbox` per indexed project; Branch →
  per-project `st.pills` (U1) or a read-only caption "indexed on <branch>
  @<sha7>" (U0); Code, Package and, on U2, "Which files" → a "More"
  `st.expander`. `branch_default` / `branch_name` have **no in-session
  control** under D14: the picker offers real branch names only, and the
  pills' preselected row is the resolved default (the symbolic "base" /
  "checked out" options of the 2026-09-04 panel are deleted). These values
  stay soft: with no target, or one target and "Only these" off, they fill
  in arguments the model leaves empty and the model may name another
  indexed project / branch / slice when the question calls for it.
  *(Deviates: O3 — R3 encodes D3 with one spelling change pending
  ratification: the YAML value is `base`, resolved to `branches.base_name`
  — D3's label "main (base branch)" survives only in the YAML comment,
  because under D14 no picker control carries it — because a literal `main`
  has no row on a bundle indexed from another branch.)*
- **R4 — The per-question scope is four cooperating pieces (D4 as restated
  by D14).** (1) The **strip**, row 1: the label "Searching in", one chip per
  **cell** rendered `<project> · <branch> ✕`, and the "Change…" trigger of
  the picker. (2) The strip, row 2, only with at least one target: the
  checkbox "Only these — the agent never searches elsewhere" (forced on and
  disabled at two or more cells with the help text "several targets always
  run as separate searches"), the count "N searches per question · limit M"
  when N ≥ 2, and a "Clear" button. (3) The **picker**, a keyed `st.popover`
  whose label **is** "Change…" (a popover is its own trigger button) and
  whose body opens with the heading "Where to search"; closed by "Use these"
  or "Reset" (§6.7, §6.10). (4)
  **Follow-up buttons** under the answer footer, derived deterministically
  from the response `meta` and the catalog — "Ask this on <branch> too",
  "Compare with <base>", "Keep searching <branch>", "Show what changed"
  (§6.9): the first, second and fourth send under a one-shot pin; "Keep
  searching" adds the cell to the strip and sends nothing. The composer is
  a plain `st.chat_input` — the icon-only popover button and its narrow
  column of the 2026-09-04 text are deleted (a summary such as "backend · 2
  branches" wrapped one letter per line in a 1/12 column). The attachment
  chip row keeps **attached symbols only**; target chips live in the strip.
  The graph page's "Add to question" still adds a cell (§6.11). A fifth,
  one-shot channel is the question text: `in:` / `on:` tokens (§6.10a).
  Pins are hard (§6.7, §6.9).
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
  strip, the picker, the prompt catalog and the graph page; unknown names
  cannot be picked. The typed-token channel does not weaken this:
  `scope_tokens.py` validates every `in:` / `on:` name against
  `WorkspaceBranchListing`, and an unknown name **refuses the send** rather
  than passing free text anywhere (§6.10a). Retired or merged branches
  (tombstones with `merged_into` set) appear after the live names in the
  picker's branch pills, selectable for diff questions about their landing
  unit (§6.10).
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
- **R8 — Graph page (D8).** One sidebar popover labeled "Where to search"
  (key `graph_where_to_search` — the popover is its own trigger) rendering
  the same picker **body** as the chat page, with the same `scope_picker_*`
  widget keys, reading and writing the same `StripState` under the same
  session key (§6.7, §6.11); the graph page has no strip; a Branch
  selector; a "Compare with" second branch; the comparison is computed
  in a GraphService companion over the reader with no server work: a symbol on
  both branches with the same chunk id is unchanged, a different chunk id is
  changed, edge-set differences give added / removed references; a "changed
  only" toggle hides unchanged nodes and edges; attaching a symbol to a
  question carries its branch into the pin (§6.11).
- **R9 — Capability gating and staging (D9).** At startup the harness
  inspects the loaded tools' input schemas; controls whose parameter or value
  the server does not advertise stay hidden. U0r (now, on P0; the U0 of
  D9 restated by D14): `QuestionScope`, the strip, the picker with one row
  per project plus "More" (code / package), multi-project fan-out over
  `project` alone (the server accepts it today —
  `test_ac6b_two_project_pin_fans_out_over_project_only`), "Only these",
  `in:` tokens with `on:` refused, the footer wording and its teaching hint,
  the renamed follow-up buttons with `ASK_ON` inactive (`branch_selector`
  false), the sidebar scope block removed, the branch listing shown per
  row as a read-only caption,
  the graph branch label. U1 (after P1): branch argument wiring, fan-out
  with `branch` sent, branch pills, `on:` tokens, chips carrying the branch,
  "Ask this on … too" / "Keep searching …", the graph compare overlay. U2
  (after P2): "Which files" under More, "Show what changed", the merged
  group (§6.12).
- **R10 — Errors (D10).** An unknown branch from the model under DEFAULT is
  replaced and logged; under PIN it cannot happen (closed list); fan-out over
  `max_cells` is refused before any call with a message naming the cap; a
  stale index is shown in the footer, never hidden; a server without `branch`
  hides the controls rather than erroring. Under D14, four refusals join
  the list — an unknown `in:` name and an unknown `on:` name (each refusing
  the send and naming the indexed names), an `on:` token before
  `branch_selector` is advertised (refused with a sentence that names no
  branch, E14), and a token selection over `max_cells` (E4) — and one state
  rule: "Only these" is forced on at two or more cells (§9 E13–E16).
- **R11 — Byte-identity (D11).** The single-cell DEFAULT path produces the
  same tool arguments and the same prompt as today; the eval binding
  (`binding.py`) and its control arm are unaffected; no MCP tool, parameter or
  envelope field is added; every tunable is YAML (§8). Under D14 this is
  load-bearing for the strip: its no-target state **is** today's no-pin
  path, byte-identical, and a question without tokens is handed on
  unchanged.
- **R12 — Tests (D12).** Interceptor rules against fake tools; fan-out merging
  of `CallToolResult` content and `structuredContent`; prompt byte-identity
  when `branch` is not advertised; catalog rendering with branches; the graph
  comparison over a fake reader; follow-up chip derivation; Streamlit AppTest
  smoke tests for the strip states (no target / one target / several) and
  the picker; pure parser tests for `scope_tokens.py` with no Streamlit
  (§11).
- **R13 — Code rules (D13).** Plain-English identifiers, `StrEnum` for closed
  vocabularies in new code, frozen dataclasses, functions of 4–20 lines,
  files under 500 lines, no vendor or competitor product names, WHY comments,
  Null-object over Optional for optional service dependencies (§6.13).
  `FollowUpKind` gains `ASK_ON` and stays a `StrEnum`; `scope_tokens.py`
  exposes `PROJECT_TOKEN_PREFIX = "in:"` and `BRANCH_TOKEN_PREFIX = "on:"`
  as the single source of the two literals.

---

## 4. Goals / Non-goals

### Goals

- One sentence, always visible above the question, that says where the next
  question will search — and one control that changes it.
- Soft defaults and hard pins carried by one value object with one `kind`.
- A multi-project, multi-branch selection is built in one place, as a
  matrix.
- Any scope the user can pick, they can also say in the question — checked
  against the indexed names, refused when unknown. One stated exception:
  the U2 merged entries (landing shas, §6.10) are picker-only; `on:` accepts
  pickable branch names, never a sha (§6.10a).
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
- A free-text scope language — `in:` / `on:` are a closed-list shortcut, not
  a query syntax; anything unknown refuses the send (§6.10a).

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
ask "how does routing work?". The panel does not survive at all (D14); its
values become the strip's initial state, seeded from YAML (R3), and are
edited in the picker's rows and its "More" block.

**A composer bar above the chat input (adopted as the strip, D14).** The
2026-09-04 text rejected this for being always visible and for competing
with the attachment chip row. Under D14 "always visible" is the point, not
the cost: the strip is one sentence plus chips, not a row of pickers, and
it answers "where will my next question search?" before the question is
asked. It does not compete with the attachment row because target chips
live in the strip and the attachment row keeps symbols only; `st.bottom`
holds both the strip and the input, so the input stays pinned (§6.10).

**Typed mentions in the question (adopted as `in:` / `on:` tokens, one-shot,
D14).** The 2026-09-04 text rejected `@backend#feature/retry` on three
grounds; each is answered rather than dismissed. R6's closed list is kept by
validation-then-refusal: every name is checked against the listing before
the send, and an unknown name refuses the send — free text never reaches a
tool argument. Reformulation (`agent.py:391-416`) never sees the syntax:
tokens are stripped from the text handed to reformulation and to `ask()`;
the transcript shows the original question and its scope caption reads
"searched in: <cells> (from your question)". The hardness objection is
answered by compiling a token question into a **one-shot PIN** — as hard as
a strip pin, visible in the transcript caption and in the footer. `@` and
`#` were rejected as prefixes because they collide with code words
(`@dataclass`, `#123`); hence `in:` / `on:` (§6.10a).

**A pills row above the input (rejected).** The proposals page's design C
also drew a sticky pills row of projects and branches above the input. Not
built: the strip is the closed-list sticky form, and two sticky selectors
would be two ways to do one thing; `st.chat_input` cannot be pre-filled, so
pills could not even insert tokens (§11 V1).

---

## 6. Architecture

### 6.1 The two kinds of scope

| | Defaults (`ScopeKind.DEFAULT`) | Pin (`ScopeKind.PIN`) |
|---|---|---|
| Lifetime | session (the strip) / deployment (YAML) | session (the strip, sticky) or one question (a typed-token question, a follow-up button) |
| Strength | soft: fills what the model left empty | hard: overwrites what the model passed |
| Where set | the strip with no target, or one target and "Only these" off (§6.7) | the strip with "Only these" on or two or more targets; `in:` / `on:` tokens (§6.10a); follow-up buttons (§6.9); graph "Add to question" (§6.11) |
| Visible in transcript | never (the strip's own line is not repeated per question) | a scope caption above the question — `searched in: <cells>`, with ` (from your question)` for a token question |
| Multi-branch | never (one branch per project) | yes → fan-out (§6.4) |
| Model note | none | transient `[pinned scope: …]` prefix (as today; not on screen) |

Exactly one `QuestionScope` is active per question. When a pin is active it
carries the pinned values and the session defaults fill its unset fields
(package, code); when no pin is active the defaults are the scope.

**The strip compiles to the scope** (D14). One pure function is the
normative mapping from what the screen shows to what the engine receives:

```python
compile_strip_scope(strip: StripState, config, listing) -> QuestionScope
```

The strip's targets are the **only** source of the project override
(R3's seed rule maps YAML into the strip once, at first load; after that
YAML's `project` is never consulted again). For the `DEFAULT` cases the
function builds a `ScopeDefaultsOverride` from the targets plus the
picker's "More" values and calls `resolve_question_scope_defaults` — one
resolution path, not two (§6.2):

- **no target** → `DEFAULT` over the union: `override.project` is forced to
  `ANY_PROJECT` (`"any"`, resolving to the cell `("", "")`) **whatever YAML
  holds** — a deployment with `project: backend` seeded one target at first
  load (R3), and after "Clear" the strip promises "all projects" and must
  send exactly that; `code` / `package` / slice come from "More". With the
  shipped YAML (`project: any`) this is exactly
  `resolve_question_scope_defaults(config, ScopeDefaultsOverride(), listing)`
  — today's no-pin path, byte-identical (R11, §8);
- **one cell, "Only these" off** → `DEFAULT` with that project as the cell
  (`override.project = <target>`) and `branch_name` = **the target's single
  chosen branch** (the existing named-default rule of §6.2: a named default
  wins over `branch_default`), resolved per call as today by
  `resolve_default_branch`. On U1 this is what makes the strip and the
  interceptor agree — a target on `feature/retry` with "Only these" off is
  answered from `feature/retry`, not silently from the YAML base (the very
  implicit-branch problem §1.3 diagnoses). On U0 the stamped name is
  informational: `branch_name` stays YAML's (`""`), and nothing
  branch-shaped is sent (§6.4 step 2);
- **one cell, "Only these" on** → `PIN` with that one cell;
- **two or more cells** → `PIN`, fan-out over the cells in listing order;
  `max_cells` is refused before any call (§6.4 step 1). Two or more cells
  imply `PIN` in the engine whatever the checkbox holds; the strip mirrors
  that by forcing "Only these" on and disabling it (§6.7, §12 O10).

`code` / `package` (and the slice on U2) ride through from the picker's
"More" values in every case; `code_compatible_with_slice` still applies.
On U0 every target, token and attach cell carries the listing's
default-row name (§6.4a), so the `PIN` cases see one cell shape; the two
`DEFAULT` cases still emit the invariant's `(project, "")` cell (§6.2) —
the target's branch travels as `branch_name` on U1 and is informational on
U0.

**Stickiness and invalidation.** The strip state lives in
`st.session_state` until changed — it survives sends and reruns. A
workspace change reloads the listing and drops only the targets the new
listing lacks, each with the toast "<project> · <branch> is no longer
indexed — removed from where to search"; the remaining targets stay (§9
E12). One-shot scopes come only from typed tokens and from the follow-up
buttons.

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
class ScopeDefaultsOverride:         # the strip's session values (the picker's "More"); None = "use YAML"
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
turns the YAML block (§7), the strip's session override and the bundle's
branch listing (§6.10) into the DEFAULT scope: one project cell — `("", "")`
for project `any` (a union request, `mcp_inputs.py:237-240`), `(name, "")`
for a listed project — plus `branch_default` / `branch_name` copied through.
Its result is also what `compile_strip_scope` (§6.1) returns for the
no-target and the one-target-soft cases, so there is one resolution path,
not two.

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
scope={"project": "p"})`, is updated to a `QuestionScope`. The note is
model-facing only: it is never rendered on screen, and D14's vocabulary rule
(§6.7) does not touch it.

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
capability record of §6.12. Under D14 the `DEFAULT` columns are reached
from the strip's no-target and one-target-soft states, and the `PIN` column
from "Only these", two or more targets, a typed-token question or a
follow-up button; no rule, log record or argument below changes.

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
reads "the agent's choice → your default" so the widening is visible (§6.8,
§9 E1).
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
   labels; the picker's cell order is project then branch as listed). The
   per-cell arguments are `{**args, "project": cell.project}`, plus
   `"branch": cell.branch` **only when `advertised.branch_selector` and
   `cell.branch` is non-empty** — the interceptor never sends an argument the
   capability does not cover, so a two-project pin on U0 fans out over
   `project` alone (AC-6b). The adapter validates each cell's
   `structuredContent` against the advertised `outputSchema` inside the
   handler (`mcp/client/session.py:412-413`), before the interceptor sees it.
3. **Merge** (`merge_cell_results(cells, sent_arguments, results) ->
   CallToolResult`, where `sent_arguments` is the tuple of per-cell
   argument dicts step 2 sent, one per cell in cell order; the label helper
   is `cell_label(cell, branch_sent: bool)`):
   - `content`: for each cell, one `TextContent` label **naming what was
     sent**, not what the cell holds (D14, amending the 2026-09-04 rule
     that labeled from the cell): `f"## {cell.project}\n"` when the cell's
     per-cell arguments carry no `branch` key — which is every U0 call,
     where step 2 sends `project` alone, so the label is `## backend` even
     for a cell that carries `main` — and `f"## {cell.project} ·
     {cell.branch}\n"` when they do (U1, where `branch` is sent).
     `branch_sent` is `"branch" in sent_arguments[i]`, derived from the
     per-cell arguments of step 2, never from the cell itself, so the label
     can never claim a branch the server was not asked for. This is what
     the plan's original tests expected (`test_ac6b_…`, `test_ac9_…`). The
     label is followed by the cell's own content blocks (the server emits
     exactly one `TextContent`, `server.py:632-642`). Blocks are kept as
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
whether the app should hold one session too — under D14 a two-project
selection is the headline gesture rather than an occasional pin, so the
per-cell spawn is now on the common path (O4 is escalated, not settled).

### 6.4a The cell matrix

A pin's cells are the product of its projects and, per project, its selected
branches. The picker builds the **full matrix directly** (D14): every ticked
project is a row, and the cells are `{(P, b) for P in ticked for b in
branches_of(P)}` — on U1 the branches selected in the project's pills, on
U0 the project's single stamped branch.

**One U0 cell shape, every source.** On U0 every cell — a strip target, an
`in:` token (§6.10a), a graph "Add to question" attach (§6.11) — carries
the listing's default-row name, `listing.default_row(project).name`; the
branch is `""` in the cell **only** when the project has no branch row at
all (a pre-v16 bundle, E8). The interceptor never sends it: step 2 drops
`branch` while `branch_selector` is false, and rule 3 labels from what was
sent. The one shape is what keeps the chips, the transcript caption, the
footer's `head_sha(project, branch)` and `QuestionScope.with_cells`'s
set semantics in agreement — `(backend, "")` and `(backend, "main")` are
distinct cells to the value object, so a token cell and a strip cell for
the same project would otherwise fan out twice over `project=backend`.
Every source therefore builds its cell through one helper,
`listing_cell(listing, project, branch="")` (in `question_scope.py`),
which fills the default-row name when `branch` is empty. The E8 shape: a
project with no branch row yields the cell `(project, "")`, the chip
`backend ✕`, the caption `backend`, and the picker caption "no branch
information".

The graph page's "Add to question", which appends
`(symbol.project, symbol.branch)` (§6.11), remains a second way for a
project to enter a scope; it is no longer the only way. On U0 (no `branch`
capability) a multi-project pin fans out over `project` only; on U1 the
model may narrow with `branch=<name>` or `project=<name>` as §6.4 defines.
The picker shows a live preview caption "Next question runs N searches:
<cells> · N of M" and disables "Use these" past `M =
ask_your_docs.scope.max_cells` with the caption "N searches is over the
limit of M (ask_your_docs.scope.max_cells) — untick a project or a
branch", so an over-cap selection cannot be applied from the picker; E4
(§9) is reachable only through graph attaches (the model-facing tool-result
text of §6.4 step 1) and through typed tokens (§6.10a, a user-facing
sentence under the composer). The strip's
chips and its "N searches per question · limit M" line replace the
2026-09-04 popover summary label (`<project> · N branches`, `N projects`),
which is deleted.

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
and `meta.branch` is the server's answer (the footer renders it as "the
server's default", §6.8). Provenance is thus *observed at the interceptor*,
not inferred from `meta` (which has no such field, `tool_response.py:40-54`).
A token-derived one-shot pin (§6.10a) produces `PINNED` observations exactly
like a strip pin; the "(from your question)" wording lives in the transcript
caption and the footer, not in `BranchOrigin` — there is no fifth member for
tokens.

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
What persists across reruns is `st.session_state` (the strip state, the
transcript entries — keys named in §6.7), never the contextvar.

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
branch names still feed the strip, the picker and the footer — only the
model-facing prompt waits for U1. (§12 O3 records the alternative.)

**Pinned-scope note stays transient**: `scope_prefix` output is prepended
after reformulation and never stored (`agent.py:446-452`, `:465-467`); rule 6
is unchanged. A typed-token question reaches reformulation and the prompt
**with its tokens stripped** (§6.10a), so the model never sees `in:` / `on:`
syntax and the pinned-scope note is the only scope signal it reads — which
keeps R11's byte-identity argument intact for untokenized questions.

**Hook, out of scope:** a task head (code review, release notes) may later
override the default branch choice through the skill-artifact block
(`agent.py:218-239`, `prompt_override.py:50-51`).

### 6.7 The strip, the picker, and what the screen shows

Replaced wholesale under D14. The 2026-09-04 text described three screen
states (a bare default view, a sidebar defaults panel, and a composer
popover with pin chips); none of those surfaces survives. What the screen
shows is now one strip whose state is always visible, one picker, the
transcript, the footer and the follow-up buttons.

**Sidebar — connection only.** Appearance, Connection (workspace, model,
base URL, config — `app.py:101-105`), the capability caption, and the
"Show technical details" toggle. No scope button, no scope panel, nothing
else. This is true on every stage.

**The strip.** Rendered inside `st.bottom`, full width, directly above a
plain `st.chat_input` (the composer row has no popover button and no column
split — §6.10). Two rows:

- *Row 1:* the label **"Searching in"**, then one chip per **cell** rendered
  `<project> · <branch> ✕` (key `scope_chip_<project>_<branch>`; on U1 a
  target with two selected branches renders two chips) — on U0 the branch
  shown is the listing's **stamped** default-row name, informational
  (nothing branch-shaped is sent, §6.4a); a project with no branch row (E8)
  renders `<project> ✕`; clicking a chip's `✕` removes that one cell, and
  removing a project's last cell removes the target — then the picker's
  **"Change…"** trigger. With no target the row reads **"Searching in all
  projects, each on its indexed branch"** followed by "Change…" — **on
  every listing size**, one project included (the sentence is stable so a
  second indexed project changes nothing on screen; the picker's single
  row names the project). On a single-project workspace with one target
  row 1 reads `Searching in backend · main ✕` followed by "Change…", and
  row 2 renders as below (the one-cell "Only these" checkbox is the only
  way to reach `PIN` there, §6.1); only the searches-per-question count
  waits for N ≥ 2.
- *Row 2*, only when at least one target is set: the checkbox **"Only these
  — the agent never searches elsewhere"**; with two or more cells it is
  forced on and disabled, with **"several targets always run as separate
  searches"** rendered as the checkbox's `help` tooltip; the count **"N
  searches per question · limit M"** (M = `ask_your_docs.scope.max_cells`)
  when N ≥ 2; and a **"Clear"** button that empties the strip.

**The strip state.** One frozen value object,

```python
@dataclass(frozen=True, slots=True)
class StripTarget:
    project: str
    branches: tuple[str, ...]        # U0: (the default-row name,) — or ("",) on E8

@dataclass(frozen=True, slots=True)
class StripState:
    targets: tuple[StripTarget, ...]  # listing order; () = no target
    only_these: bool                  # the user's own choice, see the lifecycle below
```

lives under the session key **`scope_strip`** and is the object every
writer edits — "Use these", "Reset", a chip's `✕`, "Clear", "Keep
searching …" (§6.9) and the graph page (§6.11) — and every reader compiles
(`compile_strip_scope(strip, config, listing)`, §6.1). Its cells are the
targets' `(project, branch)` pairs in order. It is seeded from YAML at
first load by R3's seed rule.

**"Only these" lifecycle.** A keyed `st.checkbox` holding a session value
ignores `value=`, so forcing needs a write before the widget renders, and
that write must not leak into the user's own choice. The user's explicit
tick lives in `StripState.only_these` (written by the checkbox's
`on_change`); the widget key `scope_strip_only_these` is derived from it on
every run: while N ≥ 2 the widget key is written `True` before rendering
and the checkbox is disabled; when N drops back to 1 (a chip removed) the
widget key is restored from `only_these`, so a user who never ticked the
box is not left in a one-cell `PIN` they never asked for (AC-42). The
engine ignores the checkbox at N ≥ 2 (§6.1) and the widget mirrors it
(§12 O10, closed).

The strip's state compiles to the question scope by `compile_strip_scope`
(§6.1). The state is **sticky**: it lives in `st.session_state` until
changed, survives sends and reruns, and is never stored in `ask()`'s
history. A workspace change drops only the targets the new listing lacks,
each with the toast "<project> · <branch> is no longer indexed — removed
from where to search" (§9 E12: while `branch_selector` is false a target
is matched by **project only** and its informational branch is refreshed
from the new listing; when true it is matched as `(project, branch)`).
"Keep for next" does not exist; one-shot scopes come only from typed
tokens (§6.10a) and from the follow-up buttons (§6.9). The strip does not
empty during the spinner: the transcript caption records what a question
was sent under, and the strip keeps showing what the next one will be sent
under.

**The picker.** On the chat page the strip's trigger **is** the popover:
`st.popover("Change…", key="scope_picker", on_change="rerun")` — a
`st.popover` is its own label button, so there is no separate "Change…"
`st.button` and no callback that writes `True` into the popover key. Its
body opens with the heading **"Where to search"** and is one shared
fragment, `render_where_to_search_body(...)`, that the graph page renders
inside its own popover (§6.11); "the same widget keys" on both pages means
the body's `scope_picker_*` keys — the popover key differs per page. The
body holds:

- one `st.checkbox` per indexed project, in listing order;
- under a ticked project: on U0 a read-only caption **"indexed on <branch>
  @<sha7>"** (nothing branch-shaped can be sent; "no branch information"
  on E8); after multi-branch P1, when `branch_selector` is advertised, one
  multi-select `st.pills` over the project's pickable branch names,
  defaulting to the default row; on U2 the merged tombstones follow the
  live names (§6.10: they pin the landing sha with the diff slice);
- a **"More"** `st.expander` holding the Code radio **"project code and
  dependencies / project code only / dependencies only"**, the Package
  selectbox (hidden when code is project-only, as today `app.py:140-142` —
  packages are dependencies), and on U2 the **"Which files"** radio
  **"everything on the branch / only files this branch changed / only the
  changes themselves"**;
- a preview caption **"Next question runs N searches: <cells> · N of M"**;
  past M **"Use these"** is disabled and the caption reads **"N searches is
  over the limit of M (ask_your_docs.scope.max_cells) — untick a project or
  a branch"** (§6.4a);
- the buttons **"Use these"** (an `on_click` callback writes the
  `StripState` from the ticked rows — every ticked row is kept, never
  replaced — and closes the popover by writing `False` to its own key) and
  **"Reset"** (an `on_click` callback that replaces the `StripState` with
  the YAML-seeded one — the same mapping as first load, R3 — pops the
  `scope_picker_*` widget values so they re-seed, and closes the popover).

On every render the picker's checkboxes, pills and "More" controls are
seeded from the **current `StripState`**, so a popover dismissed by an
outside click without "Use these" shows the strip's state the next time
it opens, never a half-edited selection. No free text anywhere: R6's
closed list holds. The picker body always executes (§11 V3), so its
widgets are addressable in AppTest without opening it.

**Typed tokens.** A question may carry `in:<project>` / `on:<branch>`
tokens anywhere in its text; parsing, refusal, stripping and the one-shot
compile are §6.10a. A refused token means nothing is sent and a warning
renders under the composer.

**Transcript.** The per-question scope caption stays, rendered as a
`st.caption` above the question for pins only: `searched in: <project> ·
<branch>[, <branch>…] | <project> · <branch>` (e.g. `searched in: backend ·
main, feature/retry | tooling · main`); for a token question it reads
`searched in: <cells> (from your question)`. The attachment chip row keeps
attached symbols only, with its "clear all" (§6.10).

**Words.** Every on-screen string follows the vocabulary of the proposals
page (§13): no "pin", no "keep for next", no "Reset to shipped", no "whole
branch", no "own code", never the retired origin segments (`· default`,
`· pinned`, `· agent-chosen`, `· server default`, `index stale`, `answered
from`), and never "scope" as a button label. The activity panel's scope
line ("Show technical details", `activity_labels.scope_note`, today
`Scope: project "x" (pinned by you)`) is on screen and follows the same
rule: it reads **`Searching only in: project "x", branch "y"`** for a
`PIN` question and stays absent for `DEFAULT`, as today. The model-facing
`[pinned scope: …]` note (§6.2, §6.6) is not on screen and stays. A
whole-segment (not substring) sweep pins this (AC-47).

**Widget keys.** Strip: `scope_strip_only_these`, `scope_strip_clear`, chips
`scope_chip_<project>_<branch>` (one per cell). Session key (not a widget):
`scope_strip` — the `StripState`. Picker: popover key `scope_picker` (the
chat page's "Change…" popover), `scope_picker_project_<name>`,
`scope_picker_branches_<name>` (U1), `scope_picker_code`,
`scope_picker_package`, `scope_picker_files` (U2), `scope_picker_use`,
`scope_picker_reset`. Follow-up buttons `follow_up_<index>_<kind>`. Graph
page: `graph_where_to_search` — the graph page's "Where to search" popover
key, wrapping the same `scope_picker_*` body. **Gone**,
and asserted absent by name (AC-49): the 2026-09-04 keys
`scope_defaults_{project,branch,slice,code,package}`,
`scope_defaults_open`, `scope_pin_popover`,
`scope_pin_{project,branches,slice,keep}`, `scope_chip_slice`,
`scope_chip_project`, plus today's `scope_project` / `scope_code` /
`scope_package` (`app.py:123-141`), which disappear with the sidebar block.

Every control that would produce a tool argument is rendered only when its
capability is advertised (§6.12); listing-only surfaces — the U0 branch
caption in the picker and the strip's chip text, the footer's
`meta.branch` — render regardless.

### 6.8 Answer footer — new file `harness/ask_your_docs/answer_footer.py`

`render_answer_footer(observations: ScopeObservations, listing, config)
-> str` builds one caption line under the answer. Aggregation rule:
observations are grouped by `(project, branch)` cell in sorted order; one
segment per cell; the line opens with `Searched ` once and the segments are
joined by ` | `. Line and segment format (D14 words):

```
Searched backend · feature/retry @3e1a9c2 (only these) · index up to date | tooling · main @8c90bd5 (only these) · index up to date
```

- project: the cell's project sent by the interceptor; when nothing was sent
  and the listing has more than one project, `all projects` (a union request
  spans every bundle, while `meta.project` names only the first loaded one —
  `tool_router.py:108-111`); else `meta.project`.
- branch: the cell's branch sent, else `meta.branch`, else "no branch" (the
  four null cases of contract §2.4, `docs/tool-contracts.md:142-164`).
- `@<sha7>`: the listing's `head_sha[:7]` for the cell's `(project, branch)`
  when the interceptor sent that cell (exact per bundle and branch, §6.10);
  otherwise `meta.indexed_git_head[:7]` when present; omitted when neither
  gives one.
- origin, in parentheses: **"your default"** (`DEFAULT`), **"only these"**
  (`PINNED`), **"the agent's choice"** (`AGENT_CHOSEN`), **"the server's
  default"** (`SERVER`) — one phrase per `BranchOrigin` member (§6.5; the
  enum members keep their names); a replaced argument (§6.3 rule a) renders
  as **"the agent's choice → your default"**.
- slice (U2): ` · <slice words>` after the origin when the slice sent on
  that cell's calls is not the default — "only files this branch changed" /
  "only the changes themselves"; "everything on the branch" is the default
  and is omitted; distinct slices are listed.
- freshness, always last and never hidden (R10): **"index up to date"**, or
  **"index behind your checkout — reindex to search it"** when any
  observation of the cell has `meta.index_stale` true.

**Teaching hint.** When `ask_your_docs.scope.tokens_enabled` and
`ask_your_docs.scope.footer_hint` are both true (§7, both default true) one
hint is appended after the last segment, as the plain string
`add in:<name> to search there too` (no backtick characters are part of
the rendered text; the backticks in this document are markdown), naming
the first project of the listing (listing order) that no cell of the
answer searched. **"Searched" is defined so that the union counts:** a
cell whose project is `""` (a union request — the no-target strip, the
state every session starts in) has searched **every** listed project, so
a union answer never carries the `in:` hint; the hint is derived only when
at least one cell named a project and some listed project was named by
none. When every indexed project was searched, no hint. On U1, when
exactly one cell was answered and its branch's base is indexed for that
project, the hint reads `add on:<base> to compare with <base>` instead.
The hint teaches the typed form at the moment it is useful (§5); it is
omitted entirely when either key is false.

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

`derive_follow_up_chips(observations, listing, capabilities, strip_scope:
QuestionScope, question: str) -> tuple[FollowUpChip, ...]` in
`answer_footer.py`, pure and deterministic. Inputs: the question's
observations (cells, origins, slices), the branch listing (base names,
indexed set, landing shas), the capability record, the strip's **compiled**
scope after the answer (`compile_strip_scope(strip, config, listing)` —
never `None`: a no-target strip compiles to a `DEFAULT` scope; this is the
parameter the 2026-09-04 text called `kept_pin`, and "the strip does not
contain this cell" means `cell not in strip_scope.cells` when
`strip_scope.kind is PIN`, always true under `DEFAULT`), and `question` —
the **stripped** standalone text the answer was produced from (the app has
it as `standalone` in `page_turn.py`), never the transcript's original,
so an `ASK_ON` re-send of a token question can never hand `in:` / `on:`
syntax to reformulation or the model (§6.6, §8).
`FollowUpChip(kind: FollowUpKind, label, project, branches, slice,
question)` is frozen; `FollowUpKind` is a `StrEnum` `{ASK_ON, COMPARE_WITH,
PIN_BRANCH, SHOW_DIFF}` — four members under D14. **Each kind yields at most
one chip per answer**, so there are at most four chips; the cap is the count
of `FollowUpKind` members, not a tunable. (This replaces the 2026-09-04 rule
of one `PIN_BRANCH` chip per answered cell.)

Rules, evaluated in this order; labels are the user's words:

| Button | Derived when | Effect |
|---|---|---|
| **"Ask this on <branch> too"** (`ASK_ON`) | U1; exactly one distinct answered cell; the listing has at least one other pickable branch for that project **that is not the base a "Compare with" button of the same answer names** — the first such branch in listing order is named; when the only other branch is that base, no `ASK_ON` chip (so a two-branch project never shows "Ask this on main too" beside "Compare with main") | re-sends the **same question** — the stripped text the answer was produced from — under a one-shot PIN over `{(project, <branch>)}`; the strip is untouched. Inactive on U0r (`branch_selector` false) |
| **"Compare with <base>"** (`COMPARE_WITH`) | U1; exactly one distinct answered cell; its branch's `base_name` is in the listing for that project and differs from itself (needs P1.6, §1.2) | one-shot PIN with cells `{(project, branch), (project, base)}`; canned question `"Compare the previous answer between <branch> and <base>: what differs?"` |
| **"Keep searching <branch>"** (`PIN_BRANCH`) | U1; the first distinct answered cell in `(project, branch)` order whose origin is `DEFAULT`, `SERVER` or `AGENT_CHOSEN` and that the strip does not already contain as a cell | **adds the cell to the strip** (sticky, §6.7) — the strip, not a "kept pin" object, is the home of stickiness; sends nothing |
| **"Show what changed"** (`SHOW_DIFF`) | U2 (`diff_slice` advertised); exactly one distinct answered cell; no observation of the question had `slice == DIFF_HUNKS`; the cell's branch is live — for a merged tombstone the cell is `(project, merged_into)`, its landing sha | one-shot PIN, that cell, `slice = DIFF_HUNKS`; canned question `"Show the diff hunks behind the previous answer."` |

Two answered cells therefore yield no "Ask this on", no "Compare with" and
at most one "Keep searching" button. Effects: an `ASK_ON` / `COMPARE_WITH`
/ `SHOW_DIFF` click builds a one-shot `QuestionScope(kind=PIN, …)` that is
sent with that one question only — the strip is untouched before, during
and after the answer (the button never edits it); a `PIN_BRANCH` click
changes only the strip and sends nothing. The pure function under test
(AC-31, AC-46) is

```python
apply_follow_up_chip(chip, strip: StripState) -> tuple[str | None, QuestionScope | None, StripState]
```

— the question to send (if any), the one-shot pin to send it under (if
any), and the strip after the click. For `ASK_ON` the question is the
chip's stored `question` (the stripped text, above) and the strip comes
back unchanged; for `COMPARE_WITH` / `SHOW_DIFF` the canned question, the
pin, the strip unchanged; for `PIN_BRANCH` no question, no pin, and the
strip **grown by the chip's cell** — appended as a new `StripTarget`, or
as one more branch of an existing target for that project — with
`only_these` unchanged (the ≥ 2 rule of §6.7 forces the checkbox when the
grown strip has two or more cells, so the strip then shows the
searches-per-question line and the disabled checkbox, E16). The strip
holds `(targets, only_these)`, not a `QuestionScope`, and
`compile_strip_scope` is one-way — which is why the function returns a
`StripState` rather than a grown scope the app could not turn back into
targets. The transcript entry stores the chips beside both texts of the
question — the original shown and the stripped one sent (§6.10a) — so
`ASK_ON` can read the latter on a rerun. Chips are computed once per
answer and stored in the transcript entry; reruns re-render the stored
tuple.

Chips are rendered as small buttons under the footer (keys
`follow_up_<index>_<kind>`, §6.7); clicking one calls
`send_question(question, images=(), scope=one_shot_pin)` — the send path
extracted from today's inline `if submission := st.chat_input(...)` block
(`app.py:218-276`: policy check, image store, history append,
`weave_attachments`, `reformulate`, `ask`, transcript append) — so the
canned question is woven, reformulated and prefixed exactly like a typed
one (§6.13). The follow-up send path never parses tokens (§6.10a).

### 6.10 The strip, the picker, and the branch listing

**Strip and picker** (`render_where_to_search_strip` and
`render_where_to_search_picker` in `harness/ask_your_docs/scope_panel.py`,
a Streamlit-only module of page fragments: the strip, the picker, the
attachment chip row, the graph page's selector row; the 2026-09-04
`render_scope_defaults_button` / `render_scope_defaults_panel` /
`render_scope_pin_popover` / `render_scope_chip_row` are deleted). The
picker is the keyed `st.popover` of §6.7 — on the chat page
`st.popover("Change…", key="scope_picker", on_change="rerun")`, on the
graph page `st.popover("Where to search", key="graph_where_to_search",
on_change="rerun")`, both wrapping the one body fragment
`render_where_to_search_body(...)` — holding one
`st.checkbox` per project (`scope_picker_project_<name>`); per ticked
project a multi-select `st.pills` of the listing's pickable branch names
(`scope_picker_branches_<name>`, **rendered only when `branch_selector` is
advertised** — on U0 the row shows the read-only caption "indexed on
<branch> @<sha7>" instead); the "More" expander with `scope_picker_code`,
`scope_picker_package` and, on U2, `scope_picker_files`; the preview
caption; `scope_picker_use` and `scope_picker_reset`. `st.pills` replaces
the 2026-09-04 `st.multiselect`: pills carry a closed option list by
construction, and no widget in the picker accepts free text (R6). The
popover always receives its key, because without one the label is part of
the widget identity and a label change would create a new widget.

Placement and closing, verified on the installed 1.59.1 source and the
toolkit release notes: the pinned chat input is only pinned when it sits at
the main root with no ancestor block
(`streamlit/elements/widgets/chat.py:1012-1023`); `st.bottom` supplies the
pinning for the strip and the input together — `with st.bottom:` renders
the strip's two rows and then a plain `st.chat_input` beneath them; the
2026-09-04 column split `st.columns([1, 12])` is deleted with the composer
popover (`st.bottom` is public at `streamlit/__init__.py:114`; the private
`_bottom` is deprecated with sunset 2026-07-01, `:121-122`; `st.bottom`
refuses to nest inside the sidebar or a dialog, `elements/bottom.py`, so the
strip is built in the main script flow). Closing the picker: the popover
takes `key` and `on_change` (`elements/layouts.py:1329-1330`), its
session-state key holds the open bool only with `on_change` set, and a keyed
popover is closed programmatically by writing
`st.session_state["scope_picker"] = False` (or `"graph_where_to_search"`)
from "Use these"'s `on_click` callback before the rerun (a widget's own
key may be written only inside a callback). Two of these features
post-date the 2026-09-04 floor `streamlit>=1.43`: per the release notes,
`st.bottom` became public in 1.57.0 and popover `key` / `on_change`
arrived in 1.55.0. The branch already pins **`streamlit>=1.59`**
(`pyproject.toml:170`, the stage-U0 bump of draft PR #267), which also
covers `at.pills` — the AppTest accessor the picker's branch pills need,
present in 1.59.1 at `testing/v1/app_test.py:527` /
`testing/v1/element_tree.py:808` (§11 V7, closed). The floor is therefore
stated as `streamlit>=1.59` everywhere in this design, with the WHY
comment `# WHY: st.bottom (the strip + chat input) + stateful st.popover
(the picker) + at.pills (its AppTests)`; dropping back to 1.57 would need a
verification of `at.pills` there and is not proposed. The private
`_bottom` shim is not used as a fallback: its sunset date is already past.
`st.chat_input` cannot be pre-filled (no `value` parameter in 1.59.1),
which is why nothing on screen inserts a token for the user (§5, §6.10a).

**Chip rows.** The 2026-09-04 `render_scope_chip_row(attached, pin)` splits
in two. The **strip** renders one chip per **cell** (`scope_chip_<project>_
<branch>`, label `<project> · <branch> ✕`; on U1 a target with two pills
is two chips, so one click never drops two branches); removing a chip
removes that one cell, removing a project's last cell removes the target,
and removing the last chip empties the strip (the no-target state). The
**attachment row** (`render_attachment_chip_row`,
generalizing the block at `app.py:173-183`) keeps attached symbols only —
absent when none — with its "clear all", which clears attachments and
nothing else; the strip's own "Clear" empties the strip. Attached elements
become a frozen `AttachedSymbol(symbol, project, branch)` (new, in
`attachments.py`, 147 lines) instead of a bare string so the graph page can
carry the branch (R8); `weave_attachments` (`attachments.py:128-137`) takes
the symbols' names; `test_app_attachment.py:11-21` seeds `AttachedSymbol`
values. Whether attached symbols should instead become chips in the strip's
row 1 is §12 O7.

**Branch listing** — `BundleReader.branches()` (added to the Protocol at
`bundle.py:30-77` and to `SqliteBundleReader`, ~40 lines; `bundle.py` is 174
lines). Returns `tuple[IndexedBranch, ...]` where the frozen `IndexedBranch`
carries `name, head_sha, base_name, is_default, status: BranchStatus,
merged_into, landing_kind: str | None, indexed_at` read from `branches`
ordered `is_default DESC, name` (the server's own order,
`storage/sqlite/branch_repository.py:120-124`; `landing_kind` is read as
`None` on v16, where the column does not exist), plus the derived property
`is_landing_unit` — true when `landing_kind` is set (v18) or `name` is 40 hex
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
landing unit — the only rows the picker's branch pills, the token validator
and the catalog line list; a busy base can hold hundreds of landing rows
after P2.8), `merged(project)` (tombstones: `status in {MERGED, DELETED}`
with `merged_into` set), `default_row(project)`, `head_sha(project, branch)`
(the footer's sha, §6.8) and `knows_project(name)` (a project name or a
bundle stem, §6.3). It feeds the strip, the picker, the token parser
(§6.10a), the prompt catalog (gated, §6.6), the footer and the graph page.

**Merged group** (U2). `merged(project)` forms the "merged" group after the
live names in the picker's per-project branch pills (§6.7),
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

### 6.10a Typed tokens: `in:` / `on:` in the question — new file `harness/ask_your_docs/scope_tokens.py`

Design C of D14: the question may name where to search. A new **pure**
module — no Streamlit import, no langchain import (the `scope_pin.py`
subprocess purity pin is copied for it, §11) — owns the grammar, the
validation and the stripping. Two module constants are the single source of
the literals (R13): `PROJECT_TOKEN_PREFIX = "in:"` and
`BRANCH_TOKEN_PREFIX = "on:"`.

**Grammar.** Tokens are whitespace-delimited words anywhere in the question,
each of the form `<prefix><name>` with no space after the prefix.

- **Trailing punctuation** is stripped from `<name>` before matching — the
  characters `? . , ; : ! )` — so the most common shape, a token last
  before the question mark (`… fail in:backend?`), names `backend`, not
  `backend?`; the whole word, punctuation included, vanishes from the
  stripped text (the question mark travels with the token; nothing is
  re-attached).
- A prefix with an **empty name** (`log in:`, `turn it on:`, `in:?`) is
  plain question text, not a token: it parses nothing and stays in the
  text byte-identical.
- Names are matched **case-sensitively** (they are file-system and git
  names).
- `in:<name>` — `<name>` must match an indexed project name **exactly**
  (`listing.knows_project`; a bundle stem is accepted for the same reason as
  §6.3 and is **normalized to its project name at parse time** —
  `WorkspaceBranchListing` knows both — so cells, chips, captions and the
  footer's `head_sha(project, branch)` always carry the project name). Any
  number of `in:` tokens may appear.
- `on:<name>` — attaches to the **nearest preceding** `in:` token and
  `<name>` must be a pickable branch of that project (`listing.pickable`;
  the U2 merged entries — landing shas — are picker-only and cannot be
  typed, §4). Several `on:` tokens after one `in:` select several branches
  of that project. An `on:` token with **no** preceding `in:` is valid only
  when exactly one project is in play — the strip's single target if it has
  one, else the workspace's single project — and is refused otherwise.
- **Duplicates collapse**: cells are deduplicated in first-occurrence order
  (`in:backend in:backend` is one cell), so a repeated token never reaches
  `QuestionScope`'s "cells has duplicates" invariant.
- Everything else is question text.

**Result.** `parse_scope_tokens(text, listing, capabilities, strip_targets,
tokens_enabled) -> ParsedScopeTokens`, a frozen value object carrying
`cells: tuple[ScopeCell, ...]` (empty when the text has no token),
`stripped_text: str` and `refusal: str` (`""` when nothing was refused).
Tokens are removed from `stripped_text` with the surrounding whitespace
collapsed; a text with no token comes back byte-identical (§8).

**Refusals** — the send is refused, the question is **not** sent, and a
warning renders under the composer (through `page_turn.refuse`, so the text
and the "Your question (not sent): …" echo both cross the bearer redaction).
The messages, verbatim:

- an unknown project — `No project named 'backnd'. Indexed: backend,
  tooling, example_needle. Nothing was sent.` (the indexed names in listing
  order);
- an unknown branch of a known project — `No branch named 'featur/retry' on
  backend. Indexed: feature/retry, main. Nothing was sent.` (the project's
  pickable branches in listing order: the default row first, then by name);
- a bare `on:` that precedes an `in:` token in the same question — `on:main
  must come after its in:<project> (found in:backend later in the question).
  Nothing was sent.`, whatever the strip holds: the later `in:` says which
  project was meant, so guessing from the strip would silently pick another;
- any `on:` token while `branch_selector` is not advertised — `Branches
  can't be chosen yet: this server indexes one branch per project.`
  (§9 E14; on U0r every `on:` token is refused this way, before the name is
  even checked);
- a lone `on:` with more than one project in play — `on:<name> needs a
  project: add in:<project> before it. In play: backend, tooling. Nothing
  was sent.`;
- more cells than `ask_your_docs.scope.max_cells` — refused before any
  call (E4) with its own **user-facing** sentence, `That would be N
  searches; the limit is M (ask_your_docs.scope.max_cells). Nothing was
  sent.` — never the model-facing tool-result text of §6.4 step 1, which
  says "pin" and tells the reader to pass `branch=<name>`, both wrong on
  screen (§6.7 words). The §6.4 text stays for the tool result a graph
  attach can still trigger.

**Compilation.** A question with at least one accepted token is sent under a
**one-shot PIN** over the token cells: each `in:` project yields
`(project, <default row>)` when no `on:` token is attached to it — on U0r
always, since every `on:` is refused (`listing_cell(listing, project)`,
the one U0 cell shape of §6.4a; `(project, "")` only on E8) — and on U1
one cell per attached `on:` branch. The strip's targets are **not** added
to a token question: the tokens name the whole scope for that question
(the lone-`on:` rule borrows only the strip's single project), so a user
with "Only these" on `backend` who types `in:tooling` searches `tooling`
only for that question. `code` and `package` (and the slice on U2) come
from the strip's "More" values; the slice is the whole branch. The strip
state is untouched before, during and after the answer; `max_cells`
applies.

**Stripping and the transcript.** The stripped text is what reaches
`weave_attachments`, reformulation and `ask()` (§6.6); the transcript shows
the **original** question and its scope caption reads `searched in:
<cells> (from your question)`. The failure texts of a refused turn quote
the original. The follow-up-button send path (§6.9) never parses tokens.

**YAML.** `ask_your_docs.scope.tokens_enabled` (default `true`); `false`
leaves the text untouched and parses nothing — `parse_scope_tokens` returns
the input text, no cells, no refusal. `ask_your_docs.scope.footer_hint`
(default `true`) governs the footer's teaching hint (§6.8). Both in §7.

**Not built.** The proposals page's C pills row (a sticky pills row above
the input): the strip is the closed-list sticky form (§5).

**Worked example** (U1, listing `backend: main (default), feature/retry`;
`tooling: main (default), develop`; `example_needle: main`; strip empty,
"More" at the YAML values):

- typed: `Why does the retry path drop the last attempt? in:backend
  on:feature/retry in:tooling`
- parsed: cells `((backend, feature/retry), (tooling, main))`, stripped text
  `Why does the retry path drop the last attempt?`, no refusal;
- sent: a one-shot `QuestionScope(kind=PIN, cells=…, code=ALL)`; the
  interceptor fans out over the two cells with `branch` sent (§6.4);
- transcript: the original question under the caption `searched in:
  backend · feature/retry | tooling · main (from your question)`;
- footer: `Searched backend · feature/retry @3e1a9c2 (only these) · index
  up to date | tooling · main @8c90bd5 (only these) · index up to date ·
  add `in:example_needle` to search there too`;
- the strip still reads "Searching in all projects, each on its indexed
  branch" afterwards.

On U0r the same text is refused with `Branches can't be chosen yet: this
server indexes one branch per project.`; `… in:backend in:tooling` is
accepted and fans out over `project` alone with the labels `## backend` and
`## tooling` (§6.4 rule 3).

### 6.11 Graph page: branch selector and compare overlay

`pages/2_Graph.py` (266 lines) gains, in its sidebar, one popover
**"Where to search"** — `st.popover("Where to search",
key="graph_where_to_search", on_change="rerun")`, the popover being its
own trigger button — whose body is the **same picker body with the same
widget keys** as the chat page (`render_where_to_search_body(...)` from
`scope_panel.py`, keys `scope_picker_*`; only the popover key differs per
page; widget keys are app-wide unique per script run, and the two pages
never execute in one run, so the reuse is safe and makes the picker
literally one component). Both pages read and write **one `StripState`
under the one session key `scope_strip`** (§6.7): the graph page has no
strip of its own, and its "Use these" writes the state the chat page
compiles on its next run — that shared state, not the shared keys, is what
makes it one component rather than a second selector. Next to it, a Branch
selectbox (indexed names of the selected project `graph_project`;
**preselection**: when a strip target's project equals `graph_project`,
that target's first selected branch is preselected; with no such target
the resolved default row — YAML `branch_name`, else base, else the
checked-out row — as today), and — on U1 — a "Compare with" selectbox and
a "changed only" toggle.
`GraphService(SqliteBundleReader(db), hide_tests)` (`2_Graph.py:118-122`) is
built as today; the branch label is shown in the selection panel on U0.

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
schema v18 adds to `node_references` (program plan P1.1 `:31`); on v16 the
reference edges are branch-agnostic, which is why the compare overlay is a
U1 item — on P0 the bundle holds one branch and the comparison is not
computable. `FakeBundleReader` (`test_graph_service.py:390-433`) and the
fixture schema (`_fixture.py:8-22`, which has no branch tables) grow the two
methods and the `branches` / `branch_chunks` tables.

"Add to question" (`2_Graph.py:262-266`) appends `AttachedSymbol(selected,
project, branch)`; on the chat page the attached symbol's `(project, branch)`
joins the active pin as a cell (added once — cells are a set) — the active
pin being the strip's compiled scope when it is a PIN — and when no pin is
active a **one-shot** pin with that single cell is created (AC-30), so the
woven question and the tool calls agree on the branch. AC-30's semantics
are unchanged by D14; only the session-state target it reads moves from the
2026-09-04 `scope_pin` key to the strip state. This is one of the ways a
second project enters a scope; the picker's matrix is the other (§6.4a).

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
`st.cache_resource` key, so the strip, the picker, the token parser and the
graph page read one record per agent build; `binding.py`, `cli.py` and
`test_prompt_seam.py`
keep calling `build_agent` unchanged (AC-27). The app hides every control
whose capability is false; the interceptor never sends an argument the
capability does not cover (§6.4 step 2). A server without `branch` therefore
hides the controls and never errors (R10).

| Stage | Server precondition | Visible / active |
|---|---|---|
| **U0r** (now — the U0 of the 2026-09-04 text, re-shaped by D14; against today's servers) | P0, schema v16; nothing server-side — a multi-project pin is accepted today (`test_ac6b_two_project_pin_fans_out_over_project_only`) | `QuestionScope` and the interceptor (project / package / code rules only); YAML block with `tokens_enabled` / `footer_hint`; the strip; the picker with one row per project plus "More" (code / package) and the **branch listing shown as a read-only caption per row** (`indexed on <branch> @<sha7>` — no branch control, nothing sent — §6.7); multi-project fan-out over `project` alone with the labels `## <project>`; "Only these"; `in:` tokens (`on:` refused, §6.10a); footer from `meta.branch` + the cell's project in the D14 wording, plus the teaching hint; follow-up buttons renamed with `ASK_ON` inactive; the sidebar scope block removed; graph page "Where to search" button and branch label; `BundleReader.branches()` and `WorkspaceBranchListing`; the `streamlit>=1.59` floor already on the branch; README screenshot caption redrawn; CHANGELOG bullet rewritten in place; catalog and rule 7 **not** rendered |
| **U1** (after P1) | `branch` on all nine tools, v18, retirement, `base_name` stamped (P1.6) | `branch` rules in the interceptor; fan-out over cells with `branch` sent and the labels `## <project> · <branch>` (§6.4); rule 7 and the catalog branch listing; branch pills under each ticked project in the picker; strip chips carrying the chosen branch; `on:` tokens accepted; "Ask this on <branch> too", "Compare with <base>" and "Keep searching <branch>" buttons; graph "Compare with" overlay and "changed only" |
| **U2** (after P2) | `changed` / `diff` scope values; the landing-unit index (P2.8) | the "Which files" radio under "More"; slice injection on `search_codebase` / `grep`; the merged tombstones after the live names in the picker's pills and the catalog tombstone marker (both pin a landing sha with `scope=diff`, §6.10); the "Show what changed" button; the slice words in the footer |

**Inactive code, three plans.** Code and tests for U1 and U2 are written
with U0r, driven by fake tools that advertise `branch` / `changed` / `diff`
and by a fake reader with two branches; they ship inactive behind
`ScopeCapabilities` and become active when the server advertises the
capability. The work is
delivered as three plans: **U0r** (§6.2, §6.3's project / package / code
rules, §6.5, §6.7, §6.8, §6.9's words and `ASK_ON` inactive, §6.10 without
the pills and the merged group, §6.10a with `on:` refused, §6.11's button,
§6.12, §7 — replacing the stage-U0 interface of draft PR #267 before it
leaves draft), **U1** (§6.3's branch rules, §6.4 with `branch` sent, §6.6,
§6.9's `ASK_ON` / `COMPARE_WITH` / `PIN_BRANCH` buttons, §6.10's pills,
§6.10a's `on:` tokens, §6.11's compare overlay — mergeable now, activated
by P1.9 and P1.6), **U2** ("Which files", the merged group, "Show what
changed" — activated by P2.3 and P2.8). A stage's PR is gated on its own
acceptance criteria (§10 tags each AC with its stage); inactive-stage ACs
run against fakes in the same PR. The commit of the multi-branch amendment
(Status) is a precondition of the U1 plan.

### 6.13 Module map and file budget

| Module | Status | Size after | Owns |
|---|---|---|---|
| `harness/ask_your_docs/question_scope.py` | edit (348 lines on the branch; **no line-budget entry yet** — add one, and split if `compile_strip_scope` + `StripState` push it past 500) | 348 + `compile_strip_scope`, `StripState`, `StripTarget`, `listing_cell` | `QuestionScope`, `ScopeCell`, `ScopeKind`, `ScopeSlice`, `ScopeCode`, `ScopeBranchDefault`, `ScopeDefaultsOverride`, `resolve_question_scope_defaults`, `resolve_default_branch`, `scope_prefix` (moved from `agent.py:140-149`; lazy export re-pointed), `StripState` / `StripTarget` (§6.7), `compile_strip_scope` (§6.1), `listing_cell` (the one U0 cell shape, §6.4a), `scope_caption_text` with the `(from your question)` form; the 2026-09-04 popover summary label helper is deleted; `snapshot_pin_for_send` loses its `keep` argument |
| `harness/ask_your_docs/scope_tokens.py` | **new (pure — no Streamlit, no langchain)** | ~120 | `PROJECT_TOKEN_PREFIX`, `BRANCH_TOKEN_PREFIX`, `ParsedScopeTokens`, `parse_scope_tokens` (grammar — punctuation, empty names, case, stems, duplicates — validation, the refusal texts of §6.10a), `strip_scope_tokens` |
| `harness/ask_your_docs/scope_interceptor.py` | new | ~240 | contextvars, `intercept_question_scope`, per-argument rules, `fan_out_over_cells` (target-cell selection, §6.4), `merge_cell_results(cells, sent_arguments, results)` — the per-cell argument dicts of §6.4 step 2 are a parameter so the label can be derived from them — `ScopeObservations`, `CellObservation`, `BranchOrigin`; `cell_label(cell, branch_sent)` labels from what was sent (§6.4 rule 3) |
| `harness/ask_your_docs/scope_capabilities.py` | new | ~70 | `ScopeCapabilities`, `inspect_scope_capabilities`, `BuiltAgent` |
| `harness/ask_your_docs/answer_footer.py` | new | ~190 (re-check: the origin / freshness vocabulary, the teaching hint and `ASK_ON` grow it) | `render_answer_footer`, `derive_follow_up_chips(observations, listing, capabilities, strip_scope, question)`, `apply_follow_up_chip(chip, strip) -> (question, pin, strip)` (§6.9), `FollowUpChip`, `FollowUpKind` (`ASK_ON`, `COMPARE_WITH`, `PIN_BRANCH`, `SHOW_DIFF`) |
| `harness/ask_your_docs/scope_panel.py` | edit (467 of its 500-line budget used on the branch) | **re-check the 500-line ceiling**: the strip + picker + "More" is more surface than the button + panel + popover it replaces; split into `scope_strip.py` + `scope_picker.py` (each with its own line-budget entry) if it does not fit | `render_where_to_search_strip`, `render_where_to_search_picker` (the chat page's "Change…" popover), `render_where_to_search_body` (the shared picker body, §6.7), the graph page's "Where to search" popover (§6.11), `render_attachment_chip_row`, `render_follow_up_chips`, `render_graph_branch_row`, the per-target drop on a listing change (§6.7) |
| `harness/ask_your_docs/activity_labels.py` | edit | — | `scope_note` re-worded to `Searching only in: …` (§6.7 words; the activity panel is on screen) |
| `harness/ask_your_docs/graph_compare.py` | new | ~120 | `compare_branch_graphs`, `BranchGraphComparison`, `ChangeState` |
| `harness/ask_your_docs/agent.py` | edit | < 500 (468 today, target ≤ 468) | `_intercept` becomes a delegate; `_active_scope`, `ToolScope` and `scope_prefix` leave; `ask()` takes `QuestionScope` + `observations`; `_assemble_prompt` threads `branch_selector_advertised`; new `build_agent_with_scope_capabilities -> BuiltAgent`; `build_agent` is a three-line wrapper with its `(graph, llm)` shape unchanged |
| `harness/ask_your_docs/__init__.py` | edit | — | `_LAZY["scope_prefix"] = "question_scope"` (`:21-28`); public names unchanged |
| `harness/ask_your_docs/app.py` | edit | ~320 | sidebar scope block `:113-143` **deleted outright** (not replaced); `st.bottom` holds the strip and a plain `st.chat_input`; the chip row `:173-183` keeps symbols only (`render_attachment_chip_row`); the inline send block `:218-276` extracted into `send_question(question, images, scope, display_question)` — two texts, the original for the transcript and failure messages and the stripped one for `weave_attachments` — called by the chat input and by the follow-up buttons; the token parse / refusal gate sits with the other pre-send refusals, before `weave_attachments` and `reformulate`; `get_agent` caches a `BuiltAgent`; transcript entries store `(answer, footer, chips)` |
| `harness/ask_your_docs/bundle.py` | edit | ~230 | `branches()`, `branch_symbol_chunks()`, `reference_rows(branch=)`, `IndexedBranch` |
| `harness/ask_your_docs/catalog.py` | edit | ~130 | `WorkspaceBranchListing` (value object), `workspace_branch_listing`, `render_catalog(branches=)` |
| `harness/ask_your_docs/attachments.py` | edit | ~170 | `AttachedSymbol` |
| `harness/ask_your_docs/pages/2_Graph.py` | edit | ~320 | the "Where to search" button opening the shared picker (§6.11), branch row, compare overlay wiring |
| `harness/core/prompts/system_v1.j2` | edit | +12 | rule 7 inside the `is defined` guard (§6.6) |
| `retrieval/config/ask_your_docs_scope_models.py` | edit (88 lines on the branch; re-exported from `ask_your_docs_models.py:32-37`) | ~100 | `ScopeDefaultsConfig` (`:51-79`) with `_DEFAULT_*` constants, plus `tokens_enabled` / `footer_hint` (§7) |
| `defaults/default_config.yaml` | edit | +12 | `ask_your_docs.scope` block, nine keys |
| `pyproject.toml` | already done on the branch (`:170`) | 0 lines | `streamlit>=1.59` floor (§6.10); only its WHY comment is re-worded to name the strip, the picker and `at.pills` |
| `examples/harness/ask_your_docs_agent/README.md` | edit | — | replaces the sidebar-pickers paragraph (`:123-129`; conformance-checked, `tests/test_doc_conformance.py:34-42`) and redraws the screenshot caption, which still describes the sidebar pickers (D14; the screenshot image itself is an owner action) |
| `CHANGELOG.md` | edit | — | the `[Unreleased]` ask-your-docs scope bullet rewritten in place — one bullet, never a second (the changelog serialization rule of `CLAUDE.md`) |

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
(`retrieval/config/ask_your_docs_scope_models.py:51-79` on the branch,
re-exported from `ask_your_docs_models.py:32-37` and mounted at its `:165`;
`extra="forbid"`, so the sub-model is mandatory for the key to be
accepted). Field defaults live in `_DEFAULT_*`
module constants; the YAML restates them for readers by the documented
exemption. Env overrides follow the existing prefix:
`PYDOCS_ASK_YOUR_DOCS__SCOPE__MAX_CELLS=2`.

```yaml
ask_your_docs:
  # …architecture / multimodal / images unchanged…
  scope:                        # the initial "Where to search" state for the chat
                                # and graph pages; the strip overrides these for
                                # one session only
    project: any                # any | <indexed project name>; "any" sends no
                                # project (the server's union across bundles)
    branch_default: base        # base | checked_out — "base" = the bundle's base
                                # branch (branches.base_name; stamped by multi-branch
                                # P1.6, so it resolves to nothing on P0 bundles);
                                # YAML-only: the picker offers real branch names
                                # and preselects the resolved row, there is no
                                # in-session "base" control;
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
                                # one question may query; shown on screen as the
                                # strip's "limit M" and the picker's "N of M";
                                # refused before any call when exceeded
    tokens_enabled: true        # allow "in:<project>" / "on:<branch>" tokens inside
                                # the question (one-shot); false leaves the text
                                # untouched and parses nothing
    footer_hint: true           # append the teaching hint to the answer footer
                                # ("add in:<name> to search there too") when
                                # tokens_enabled is true
```

`max_cells` is now user-visible twice — the strip's "N searches per question
· limit M" and the picker's "N of M" preview plus the disabled "Use these"
caption — and M is read from this key everywhere, never re-encoded (the
single-source rule). `tokens_enabled` and `footer_hint` are the two keys D14
adds (§6.8, §6.10a).

Validation: `project`, `branch_name` and `package` are strings;
`branch_default: ScopeBranchDefault = Field(default=ScopeBranchDefault.BASE)`
— the two symbolic values are a closed vocabulary and stay a `StrEnum` (D13),
split from the free name so that a branch literally named `base` or
`checked_out` remains addressable through `branch_name`; the name is checked
against the listing at resolution time, not at config load, because the
config is workspace-agnostic; `slice` and `code` are the enums of §6.2;
`max_cells: int = Field(ge=1, le=16)`; `tokens_enabled: bool = Field(default=True)`
and `footer_hint: bool = Field(default=True)`.
`tests/test_config_ask_your_docs.py:55-60` requires
`AppConfig.load().ask_your_docs == AskYourDocsConfig()`, so YAML and
`Field` defaults must agree; `test_default_yaml_ships_the_block_keys` (`:113`)
grows the nine `scope` keys. `extra="forbid"` stays; no key is removed, so
an overlay written for the 2026-09-04 block still loads.

Every tunable of this design is in this block — the token channel included;
nothing is a CLI flag and nothing is an MCP parameter. The follow-up chip
count (§6.9) is the number of `FollowUpKind` members, not a tunable.

---

## 8. Contract guarantees

- **No MCP surface change.** No tool, parameter or envelope field is added;
  the harness only reads what the server advertises
  (`docs/tool-contracts.md` §3, §5.2). Client-side merged results never reach
  the server and never change the per-cell validated envelope.
- **Byte-identity of the single-cell DEFAULT path, stated precisely.**
  With the shipped YAML (`project: any`, `branch_default: base`,
  `branch_name: ""`, `slice: whole_branch`, `code: all`, `package: ""`) and no
  pin — which under D14 means **the strip with no target**, the state every
  session starts in; the strip is always visible, but with no target it
  sends nothing that today's no-pin path does not send
  (`compile_strip_scope`'s no-target case is `resolve_question_scope_defaults`
  itself, §6.1):
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
- **Token neutrality.** With `tokens_enabled: true` and no token in the
  question, the text handed to reformulation and to `ask()` is
  byte-identical to the typed text (the stripper is a no-op when nothing
  parses, §6.10a); with `tokens_enabled: false` the text is byte-identical
  and no parse happens. With a token present the scope is a one-shot PIN
  and the text is the stripped text, by design.
- **The eval binding is untouched.** `binding.py` never calls `ask()`
  (`binding.py:366-369`), so the scope contextvar is `None` and the
  interceptor is a strict passthrough (§6.3); `build_agent` is called without
  scope arguments (`binding.py:352-364`), keeps its `(graph, llm)` return
  shape (§6.12) and its prompt assembly receives
  `branch_selector_advertised=False` by default; the control-arm doctrine of
  `agent.py:304-315` and the delivery-map digest golden
  (`test_binding.py:187-217`) are unaffected. The `_intercept` and
  `serve_connection` names imported at `binding.py:320` are kept.
- **Every tunable is YAML** (§7); the strip is a session override of YAML,
  not a second source of defaults.
- **Freeze manifests.** The core prompt pool is outside the prompt-freeze
  golden (`test_prompt_freeze.py:47-55`); rule 7 hits the seed-parity test only
  if `SYSTEM_PROMPT` bytes change, which §6.6 prevents and §11 V4 verifies.

---

## 9. Error handling

| Case | Behavior |
|---|---|
| E1 Model names a project the listing does not know (DEFAULT) | replaced by the default cell's project; structured log `scope_default_replaced {tool, argument, passed, replacement}`. When the default project is `any` the replacement is `""` — a union across every loaded bundle, i.e. a widening — the record carries `replacement: ""` and the footer origin reads "the agent's choice → your default" (§6.3, §6.8) |
| E2 Model names a branch the listing lacks for the effective project (DEFAULT, U1) | replaced by `resolve_default_branch(...)` — possibly nothing — and logged (same record); the server's own unknown-branch error (multi-branch spec §6.11) is thus never reached from DEFAULT |
| E3 Unknown branch under PIN | cannot happen: pins are built from the closed listing — the picker's checkboxes and pills carry a closed option list (§6.10), and typed tokens are validated against the listing before the send (§6.10a) |
| E4 Fan-out exceeds `max_cells` | refused before any handler call; error tool result naming the cap and the YAML key (§6.4 step 1 — model-facing text, kept for the graph-attach path); the model reports it, the footer shows no cells. The picker disables "Use these" past the cap with the caption "N searches is over the limit of M (ask_your_docs.scope.max_cells) — untick a project or a branch" (§6.4a), so E4 is reachable only through graph attaches and through typed tokens — the latter refused before the send with the user-facing sentence "That would be N searches; the limit is M (ask_your_docs.scope.max_cells). Nothing was sent." (§6.10a; never the tool-result text) |
| E5 A cell errors during fan-out | partial: kept as labeled error text, `isError=False`; all cells: `isError=True`; transport exception: propagates as today |
| E6 Stale index | ` · index behind your checkout — reindex to search it` in the footer for that cell, never suppressed (the freshness segment is present in both states, §6.8); on multi-bundle servers the staleness flag describes the first bundle's probe (`server.py:487-496`) until a per-project probe exists — the footer tooltip says so; a hook for the multi-branch program, not this design. The sha is exact (it comes from the listing, §6.8) |
| E7 Server does not advertise `branch` / `changed` / `diff` | argument-bearing controls are hidden: the picker's per-project branch pills (replaced by the read-only caption "indexed on <branch> @<sha7>"), the graph "Compare with" and "changed only", rule 7, the catalog branch segment, and the "Ask this on … too" / "Compare with …" / "Keep searching …" / "Show what changed" buttons are absent; "Which files" is absent; `on:` tokens are refused with "Branches can't be chosen yet: this server indexes one branch per project." (E14). Informational surfaces stay: the branch caption, the strip's chip text and the footer's `meta.branch`. Nothing is sent; no error |
| E8 Pre-v16 bundle (no `branches` table) | `branches()` returns `()`; the picker's row caption reads "no branch information"; a target for the project yields the cell `(project, "")`, the chip `demo ✕` and the caption `demo` (§6.4a); the footer reads `Searched demo · no branch (the server's default) · index up to date` (AC-34) |
| E9 Landing unit outside the retention window (U2, merged group) | the server's `InvalidArgumentError` naming `git.diff_chunks.retain` and the `branches pin` command (multi-branch spec §6.11) is shown verbatim in the transcript; the chip stays so the user can clear it |
| E10 Streamlit older than 1.59 installed | the `[harness-ask-your-docs]` extra pins `streamlit>=1.59` (`pyproject.toml:170`, §6.10); an environment that bypasses the pin fails at import of `st.bottom` or `st.pills` with the toolkit's own `AttributeError` — no shim, no fallback shape |
| E11 `slice != WHOLE_BRANCH` with `code == DEPS` | rejected at `QuestionScope` construction with both values in the message; the picker disables the combination |
| E12 A strip target references a cell the reloaded listing no longer has (workspace changed) | **only the missing targets are dropped**, not the whole selection (a behavior change from the 2026-09-04 "dropped whole" rule), each with the toast "<project> · <branch> is no longer indexed — removed from where to search"; the remaining targets stay (§6.7). **Matching rule:** while `branch_selector` is false a target is matched by **project only** — a project re-indexed on another branch keeps its target and its informational branch is refreshed from the new listing, so the toast never claims a still-indexed project is gone; when `branch_selector` is true a target is matched as `(project, branch)` per cell |
| E13 Unknown `in:` name typed in the question | the send is refused; nothing is sent (no handler call, no history append, no transcript entry); a warning under the composer names the indexed projects: "No project named 'backnd'. Indexed: backend, tooling, example_needle. Nothing was sent." (§6.10a); an unknown `on:` name on a known project is refused the same way with that project's branches. A token whose trailing punctuation was stripped (`in:backend?`) is **not** E13 — it names `backend` (§6.10a grammar); a prefix with no name is plain text and never refuses |
| E14 `on:` token while `branch_selector` is not advertised | refused with "Branches can't be chosen yet: this server indexes one branch per project."; nothing is sent (§6.10a) |
| E15 `on:` token with no preceding `in:` and more than one project in play | refused, naming the ambiguity: "on:<name> needs a project: add in:<project> before it. In play: backend, tooling. Nothing was sent." (§6.10a) |
| E16 Two or more cells with "Only these" unticked | not an error, a state rule: the widget key is written on and the checkbox rendered disabled with the help text "several targets always run as separate searches" (§6.7 lifecycle); the user's own choice is kept in `StripState.only_these`, and when the cells drop back to one the checkbox is restored from it — never left silently on; the engine compiles two or more cells to `PIN` regardless (§6.1) |

---

## 10. Acceptance criteria

Each criterion is tagged with the stage whose PR it gates (§6.12); U1 / U2
criteria run against fake tools and a fake reader in the U0r code base and
stay green while inactive. Under D14 four criteria are superseded (kept
below, marked "superseded by AC-Nx (D14)", never deleted), twelve are
reworded for the new labels, keys and footer format (marked "(D14 words)":
AC-6, AC-6b, AC-17, AC-18, AC-22, AC-25, AC-26, AC-30–AC-34), and
AC-35–AC-52 are added. AC-6b is the load-bearing U0r criterion: it is the
proof that today's server accepts the two-project gesture of design A.

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
- **AC-6** [U1] (D14 words) Under `PIN` with three cells and `branch`
  omitted on a server that advertises `branch`, the handler is called three
  times in cell order, and the merged `CallToolResult` has labeled text
  blocks `## <project> · <branch>` before each cell's blocks — the branch
  appears in the label **because it was sent** (§6.4 rule 3, AC-51) —
  `structuredContent.items` carrying `branch` and `project` on every item,
  and `structuredContent.meta` equal to the first cell's `meta` with no added
  key.
- **AC-6b** [U0r] (D14 words) Under `PIN` with the cells `{(a, main), (b, main)}` and a
  server that does not advertise `branch`, the handler is called twice, with
  `project=a` and `project=b`, and no `branch` key in either call; the
  labels read `## a` and `## b` — no ` · main`, because no branch was sent
  (D14; the cells carry `main` on purpose, so the old label rule and the
  new one disagree on this fixture). This is the load-bearing U0r
  criterion: it proves the server accepts the multi-project strip today.
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
- **AC-17** [U1; "Show what changed" U2] (D14 words) `derive_follow_up_chips`
  yields "Compare with <base>" only for exactly one answered cell whose base
  is indexed and differs, "Show what changed" only when `diff_slice` is
  advertised and no observation was a diff-hunks slice, "Keep searching
  <branch>" only for a cell the strip scope does not contain and only on U1;
  two answered cells yield no "Compare with", no "Ask this on" and **one**
  "Keep searching" chip (the first cell in `(project, branch)` order); at
  most one chip per kind and at most `len(FollowUpKind)` chips,
  deterministically for a shuffled observation list.
- **AC-18** [U0r] (D14 words) `render_answer_footer` prints one segment per
  distinct cell in sorted order, in the §6.8 format — the line opens with
  `Searched `, segments joined by ` | `, each `<project> · <branch> @<sha7>
  (<origin>) · <freshness>` (`all projects` for a union answer on a
  multi-project listing; the listing's sha for a sent cell; the origin
  phrases "your default" / "only these" / "the agent's choice" / "the
  server's default" and "the agent's choice → your default" for a replaced
  argument, asserted as full segment strings); the freshness segment reads
  "index up to date" or "index behind your checkout — reindex to search it"
  whenever any observation of that cell is stale, and is never absent.
- **AC-19** [U0] *superseded by AC-49 (D14)* — asserted a sidebar button
  "Scope defaults", the popover key `scope_pin_popover` and the absence of
  `scope_project` / `scope_code` / `scope_package`. The sidebar now has no
  scope widget at all; the absence assertion moves to AC-49 and grows to
  the 2026-09-04 keys.
- **AC-20** [U0] *superseded by AC-48 (D14)* — asserted the sidebar defaults
  panel after the button click. The panel does not exist; the picker rows
  and its "More" block are asserted by AC-48.
- **AC-21** [U0] *superseded by AC-50 (D14)* — asserted the popover summary
  label `2 branches` and the chips `scope_chip_slice` / `scope_chip_project`.
  Neither label nor chip exists; the strip's two-target state is AC-50.
- **AC-21b** [U0] *superseded by AC-41 (D14)* — asserted the one-shot vs kept
  pin lifecycle. "Keep for next" no longer exists; the strip is sticky and
  one-shot scopes come only from tokens and follow-up buttons (AC-40,
  AC-41).
- **AC-22** [U0r] (D14 words) `AppConfig.load().ask_your_docs.scope ==
  ScopeDefaultsConfig()`, `branch_default` rejects a value outside
  `ScopeBranchDefault`, an unknown key under `scope:` is rejected,
  `PYDOCS_ASK_YOUR_DOCS__SCOPE__MAX_CELLS=2` overrides the cap,
  `PYDOCS_ASK_YOUR_DOCS__SCOPE__TOKENS_ENABLED=false` turns tokens off, and
  the shipped YAML block carries exactly the **nine** `scope` keys of §7.
- **AC-23** [U0] `QuestionScope(kind=DEFAULT, cells=(a, b))`,
  `QuestionScope(kind=DEFAULT, cells=(ScopeCell("p", "main"),))`,
  `QuestionScope(cells=())` and `slice=DIFF_HUNKS` with `code=DEPS` each raise
  with the offending values in the message.
- **AC-24** [U0] The eval binding's delivery-map digest golden and
  `test_binding.py` pass unchanged; `binding.py` imports `_intercept` and
  `serve_connection` as before.
- **AC-25** [U0r] (D14 words) The README paragraph at
  `examples/harness/ask_your_docs_agent/README.md:123-129` describes the
  strip, the picker, "Only these", the searches-per-question count, `in:`
  tokens and their refusal, the footer line and the follow-up buttons — no
  "pin", no "Scope defaults" — and the screenshot caption no longer
  describes sidebar pickers; `tests/test_doc_conformance.py` passes; no
  `PR #`, sub-PR or task jargon is introduced (the audit grep of CLAUDE.md).
- **AC-26** [U0r] (D14 words) Every new module and `agent.py` are under 500 lines (the
  ≤ 468 figure for `agent.py` is a target, not a gate; `scope_panel.py` is
  re-checked against the ceiling and split if the strip + picker do not
  fit, §6.13); `ruff format --check`, `mypy`, `complexipy
  --max-complexity-allowed 15` and `vulture` pass.
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
- **AC-30** [U0r] (D14 words) Attaching `AttachedSymbol("mod.Foo",
  "backend", "feature/retry")` with no active pin (the strip compiles to
  `DEFAULT`) yields a one-shot pin with the cell `(backend, feature/retry)`
  and the woven question `` Regarding `mod.Foo`: … ``; with an active pin
  (the strip compiles to `PIN`) the cell is added once. The session-state
  target read is the strip state, not a `scope_pin` key.
- **AC-31** [U1; `SHOW_DIFF` U2] (D14 words) `apply_follow_up_chip(chip,
  strip) -> (question, pin, strip)` for a `COMPARE_WITH` chip returns the
  canned question, a one-shot pin of two cells and the input `StripState`
  unchanged; for `PIN_BRANCH` it returns no question, no pin, and a
  `StripState` grown by the chip's cell — a new `StripTarget` for a project
  the strip lacked, one more branch on an existing target otherwise — with
  `only_these` equal to the input's (the ≥ 2 rule then forces the checkbox
  on screen, E16); for `ASK_ON` see AC-46; the chat page calls
  `send_question` only when a question is returned and writes the returned
  `StripState` to `scope_strip` in every case.
- **AC-32** [U2] (D14 words) The picker's branch pills for a project list a
  `MERGED` tombstone labeled `feature/old (merged into main @3e1a9c2)` after
  the live names and never among them; selecting it produces the cell
  `(backend, <merged_into>)` with `slice=DIFF_HUNKS`.
- **AC-33** [U0r] (D14 words) "Reset" (`scope_picker_reset`, an `on_click`
  callback) replaces the `StripState` under `scope_strip` with the
  YAML-seeded one (R3's seed rule: a fixture with `project: <name>` yields
  one target with "Only these" off; the shipped YAML yields no target), pops
  the `scope_picker_*` widget values so the checkboxes, pills and "More"
  controls re-seed from `ScopeDefaultsConfig()`, and closes the popover —
  starting from a strip and a picker seeded away from those values, and
  asserting the strip as well as the widgets. A picker dismissed without
  "Use these" re-opens showing the current `StripState`, not the abandoned
  ticks.
- **AC-34** [U0r] (D14 words) On a pre-v16 fixture the footer reads
  `Searched demo · no branch (the server's default) · index up to date`.
- **AC-35** [U0r] `compile_strip_scope`: no target → `DEFAULT` with the
  union cell `("", "")`, asserted explicitly **on a config whose `project`
  is not `any`** (`project: backend`, so a mutant that lets YAML through
  would yield `(backend, "")`), and — under the shipped `project: any` only
  — equal to `resolve_question_scope_defaults(config,
  ScopeDefaultsOverride(), listing)` (the byte-identical path, R11); one
  cell with "Only these" off → `DEFAULT` with that project as the cell,
  asserting `kind` explicitly on a target that differs from
  `ScopeDefaultsConfig().project`, with `branch_name` equal to YAML's on a
  U0 listing; the same case on a U1 fake listing with the target on
  `feature/retry` (not the base) → `DEFAULT`, `branch_name ==
  "feature/retry"`, and `resolve_default_branch` returns it; one cell with
  "Only these" on → `PIN` with that one cell; two cells → `PIN` with both
  cells in listing order, whatever the checkbox holds; a five-cell
  selection with `max_cells: 4` is refused before any call; `code` /
  `package` from the "More" values ride through.
- **AC-36** [U0r] `parse_scope_tokens`: `in:backend` resolves to that project
  (on a fixture where `backend` is not the YAML default project);
  `… fail in:backend?` resolves to `backend` and the stripped text ends
  `fail` (trailing `? . , ; : ! )` stripped from the name, the whole word
  removed); `in:backend in:backend` yields **one** cell; `in:Backend` is
  refused (case-sensitive); a bundle stem `in:backend_abc123` yields the
  cell with the **project name**; `in:backend on:feature/retry` attaches
  the branch to the nearest preceding `in:` (U1 fake capabilities);
  `in:backend on:main in:tooling` yields `((backend, main), (tooling,
  <default>))`; on U0 `in:backend` yields `(backend, <default-row name>)`
  (the one cell shape, §6.4a) and `(backend, "")` only on a listing with no
  row for it; a lone `on:` with exactly one project in play (the strip's
  single target, else the workspace's single project) resolves; a lone
  `on:` with two projects in play is refused with the E15 text;
  `PROJECT_TOKEN_PREFIX` / `BRANCH_TOKEN_PREFIX` are the only occurrences
  of the literals `"in:"` / `"on:"` in the module; the module imports
  neither `streamlit` nor `langchain` (a subprocess import pin).
- **AC-37** [U0r] Token refusal: `in:backnd` returns a refusal whose message
  is exactly `No project named 'backnd'. Indexed: backend, tooling,
  example_needle. Nothing was sent.`; five `in:` projects with `max_cells:
  4` return exactly `That would be 5 searches; the limit is 4
  (ask_your_docs.scope.max_cells). Nothing was sent.`; and in the AppTest
  nothing is sent for either — no handler call, no history append,
  `messages` unchanged — while the warning renders under the composer.
- **AC-38** [U0r] `on:main` on a server that does not advertise `branch` is
  refused with exactly `Branches can't be chosen yet: this server indexes
  one branch per project.` and nothing is sent, even when `main` is the
  project's stamped branch.
- **AC-39** [U0r] Token stripping: the text handed to `reformulate` and to
  `ask()` carries no `in:` / `on:` token and no doubled whitespace; the
  transcript shows the original question; its scope caption reads
  `searched in: <cells> (from your question)`; with no token present the
  downstream text is byte-identical to the typed text, and so is a text
  carrying only an empty-name prefix (`log in:` — plain text, no token);
  with `tokens_enabled: false` the text is byte-identical, no cells are
  produced and `in:backnd` is not refused.
- **AC-40** [U0r] A token question sends under a one-shot `PIN` over the
  token cells, takes `code` / `package` from the strip's "More" values, and
  leaves the strip state unchanged after the answer (asserted on
  `st.session_state` before and after the send, with a non-empty strip so
  the assertion is not vacuous).
- **AC-41** [U0r] Strip stickiness and invalidation: a seeded two-target
  strip survives a send and a rerun unchanged; changing the workspace to a
  listing that lacks one of the two projects removes only that target, with
  the toast `<project> · <branch> is no longer indexed — removed from where
  to search`, and the other target stays; on U0 (`branch_selector` false) a
  project re-stamped on **another branch** in the new listing keeps its
  target, no toast, and its chip shows the new branch name (matched by
  project only, E12); on a U1 fake a target is matched as `(project,
  branch)`; "Clear" empties the strip.
- **AC-42** [U0r] Forced "Only these": with two cells and the checkbox
  seeded **off**, after the run `scope_strip_only_these` is on and disabled
  and its help text is "several targets always run as separate searches";
  removing one chip so one cell remains restores the checkbox to **off**
  (from `StripState.only_these`, which the forcing never wrote) and
  editable, and the compiled scope is `DEFAULT`; with the box ticked before
  the second cell arrived, the 2 → 1 transition restores it to **on**.
- **AC-43** [U0r] Footer teaching hint: with `footer_hint: true` and
  `tokens_enabled: true` the footer ends with the plain string `add
  in:<name> to search there too` (no backtick characters) naming the
  **first unsearched** project in listing order; a **union** answer (the
  no-target strip, one cell with project `""`) on a three-project listing
  yields **no** hint; when every indexed project was searched there is no
  hint; with `footer_hint: false` or `tokens_enabled: false` there is no
  hint; on U1 with one answered cell whose base is indexed it reads `add
  on:<base> to compare with <base>`.
- **AC-44** [U0r] Picker cap surface: the preview caption reads `Next
  question runs N searches: <cells> · N of M`; with N > M "Use these" is
  disabled and its caption reads exactly `N searches is over the limit of M
  (ask_your_docs.scope.max_cells) — untick a project or a branch`.
- **AC-45** [U0r] Graph page: the sidebar renders exactly one scope
  control — the popover keyed `graph_where_to_search`, labeled "Where to
  search" — and no `st.button` for scope; its body carries the same widget
  keys as the chat page's picker (`scope_picker_project_<name>`,
  `scope_picker_code`), asserted on the children, not on a button;
  "Use these" there writes `scope_strip`, and a chat-page run seeded with
  that state compiles it; with a strip target whose project equals
  `graph_project` and whose first selected branch is off the base (U1
  fake), the Branch selectbox preselects that branch; with no target for
  `graph_project` it preselects the resolved default row (the surviving
  intent of the 2026-09-04 graph-page override test).
- **AC-46** [U1] `ASK_ON`: "Ask this on <branch> too" is derived only when
  `branch_selector` is advertised and the project has another pickable
  branch that is not the base a `COMPARE_WITH` chip of the same answer
  names (a two-branch project yields "Compare with main" alone);
  `apply_follow_up_chip` returns the chip's stored question — the stripped
  text the answer was produced from: for an answer to `… in:backend
  on:feature/retry` the re-sent text carries no token — and a one-shot pin
  over `(project, <branch>)`, leaving the `StripState` unchanged; at most
  one `ASK_ON` chip per answer; the chip is absent on U0r.
- **AC-47** [U0r] Vocabulary sweep, **whole-segment, not substring**: the
  rendered AppTest text of the chat page and the graph page (including the
  activity panel's scope line for a `PIN` question), plus the three label
  tables (`CODE_LABELS` / `SLICE_LABELS` in `question_scope.py`,
  `ORIGIN_LABELS` in `answer_footer.py`) and the string literals of
  `scope_panel.py`, `activity_labels.py` and `scope_pin.py`, contain none of
  these exact segments: `Scope defaults`, `keep for next`, `Reset to
  shipped`, `whole branch`, `own code only`, `all code`, `answered from`,
  `index stale`, `(default)` outside the model-facing catalog line,
  `· default`, `(pinned)`, `· pinned`, `pinned by you`, `agent-chosen`,
  `server default`, and the button label `Pin`. The approved phrases "your
  default", "the server's default" and "main (base branch)" contain the
  word "default" and must pass, which is why the check is by segment;
  `scope_prefix`'s `[pinned scope: …]` bytes are exempt and unchanged
  (AC-28).
- **AC-48** [U0r] AppTest, the picker (replaces AC-20): opening `scope_picker`
  shows one `scope_picker_project_<name>` checkbox per indexed project in
  listing order; a ticked project shows the caption `indexed on <branch>
  @<sha7>` when `branch_selector` is false and `scope_picker_branches_<name>`
  pills when it is true (the U1 inactive-code pin); "More" holds
  `scope_picker_code` and `scope_picker_package`, the latter absent when
  code is project-only, and no `scope_picker_files` when `changed_slice` is
  false; ticking two projects and clicking "Use these" yields **two**
  targets in the strip — both rows kept, on a fixture whose two projects
  differ in default and base branch and whose first-ticked project is not
  the listing's first row — and closes the popover.
- **AC-49** [U0r] AppTest, no target (replaces AC-19): the sidebar has no
  button labeled "Scope defaults" or "Where to search" on the chat page and
  no scope widget; the strip reads "Searching in all projects, each on its
  indexed branch" with the "Change…" popover (a `Block` in AppTest, not an
  `at.button`) and no row 2; the composer is a bare `chat_input` with no
  popover column; the popover key `scope_picker` exists and
  `graph_where_to_search` does not on the chat page; no widget keyed
  `scope_project`, `scope_code`, `scope_package`,
  `scope_defaults_project`, `scope_defaults_branch`, `scope_defaults_slice`,
  `scope_defaults_code`, `scope_defaults_package`, `scope_defaults_open`,
  `scope_pin_popover`, `scope_pin_project`, `scope_pin_branches`,
  `scope_pin_slice`, `scope_pin_keep`, `scope_pin_apply`, `scope_chip_slice`
  or `scope_chip_project` exists (the **old** keys — an absence test over
  the new keys would pass on an empty page).
- **AC-50** [U0r] AppTest, two targets (replaces AC-21): with two targets
  seeded in `scope_strip`, row 1 shows two `scope_chip_<project>_<branch>`
  buttons (one per cell, the branch being each project's default-row
  name), row 2 shows `scope_strip_only_these` on and disabled, the line
  `2 searches per question · limit 4` and `scope_strip_clear`; clicking one
  chip's `✕` keeps the other target (a mutant that clears the whole
  selection must fail); after a send the last user message shows the
  caption `searched in: backend · main | tooling · main`; on a U1 fake a
  target with two selected branches renders two chips and clicking one
  removes only that branch.
- **AC-51** [U0r; U1 half against fakes] Label rule:
  `merge_cell_results(cells, sent_arguments, results)` labels each cell's
  blocks `## <project>` when that cell's entry in `sent_arguments` carries
  no `branch` key (U0r, even for a cell that carries `main`) and
  `## <project> · <branch>` when it does (U1 fake capabilities);
  `cell_label(cell, branch_sent=False)` never mentions the branch; a partial
  failure keeps the error text under the same label shape (the reworded
  AC-9 fixture).
- **AC-52** [U0r] AppTest, a single-project listing: with no target the
  strip reads "Searching in all projects, each on its indexed branch" with
  "Change…" and no row 2; with one target seeded, row 1 reads `Searching in
  backend · main ✕` followed by "Change…", row 2 shows
  `scope_strip_only_these` **editable** and `scope_strip_clear`, and no
  searches-per-question line is rendered; ticking "Only these" compiles to
  a one-cell `PIN`.

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
  `ScopeDefaultsOverride` layering and the "Reset" values (AC-33), and the
  `compile_strip_scope` table (AC-35: no target on a `project: backend`
  config → the union cell / one target soft, U0 and the U1 named-branch
  case / one target "Only these" / two targets / the cap), including the
  equality assertion against `resolve_question_scope_defaults` for the
  no-target case under the shipped YAML only (the R11 fence),
  `listing_cell`'s one U0 cell shape (§6.4a) and the token form of the
  scope caption (AC-39).
- `test_scope_tokens.py` (new, pure — no Streamlit, no langchain): the
  grammar (trailing punctuation, the empty-name prefix as plain text, case
  sensitivity, stem normalization, duplicate collapse), the
  nearest-preceding-`in:` attachment, the lone-`on:` single-project rule,
  every refusal text of §6.10a verbatim (AC-36, AC-37, AC-38, E15), the
  stripping and whitespace collapse, the no-token byte-identity,
  `tokens_enabled: false` (AC-39), the cap with its user-facing sentence
  (E4), and the
  subprocess import pin copied from `test_scope_pin.py`. Fixture rule: the
  tokened project must differ from the YAML default project, or "tokens
  win" and "the default was already that" are indistinguishable.
- `test_scope_interceptor.py` (extended): AC-51 — a cell that carries a
  branch the sent arguments do **not**, so the old and new label rules
  disagree on the fixture.
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
- `test_answer_footer.py` (new): AC-17, AC-18, AC-31, AC-34, AC-43, AC-46,
  including shuffled input; footer origin phrases are asserted as full
  segment strings, never by containment (the four phrases share words), and
  booleans with `is`; the chip cap is re-derived from the four-member
  `FollowUpKind`, and "at most one chip per kind" is asserted directly, not
  through the count.
- `test_scope_vocabulary.py` (new): AC-47, a whole-segment sweep over the
  enumerated retired segments across the AppTest trees of both pages (a
  `PIN` question opened in the activity panel included), the three label
  tables and the string literals of `scope_panel.py`, `activity_labels.py`
  and `scope_pin.py`.
- `test_attachments.py` (new or extended): AC-30 (`AttachedSymbol` into a
  pin, woven question).
- `test_config_ask_your_docs.py` (extended): AC-22 with the nine keys and
  the `tokens_enabled` env override.
- `test_binding.py`: unchanged, must stay green (AC-24).

**AppTest smoke tests (`pytest.importorskip("streamlit")`; run where the
`[harness-ask-your-docs]` extra is installed — the main checkout's venv, not
the worktree's, with `PYTHONPATH` pointing at the worktree's `python/` so the
worktree sources are the ones imported):** `test_app_scope_states.py`
(rewritten whole) with the **strip states** — no target (AC-49), one target
with "Only these" off and on, two targets with the forced checkbox, the
count line and the limit (AC-42 including the 2 → 1 transition, AC-50),
the single-project listing (AC-52), the picker open with its rows and
"More" (AC-44, AC-48), the token send and refusal paths (AC-37, AC-38,
AC-40), stickiness and the per-target drop (AC-41), "Reset" (AC-33), and
AC-32 on U2 — seeding the `StripState` under `scope_strip` in
`st.session_state` before the first run (a widget that already holds a
session value ignores a changed default). `test_graph_page_scope.py`
carries AC-45. Every AppTest goes
through the shared page fixtures (`_page_fixtures.page(**seeds)` /
`graph_page(**seeds)`), never a raw `AppTest.from_file`. Verified on the
installed source: a popover delta parses as a generic `Block`
(`streamlit/testing/v1/element_tree.py:2672-2673` — only chat_message /
column / expandable / tab are special-cased) and its body executes on every
run, so its children are reachable through the flat accessors
`at.checkbox`, `at.pills`, `at.button`, `at.selectbox`, `at.caption`,
`at.expander` without opening it; the `st.bottom` container is an anonymous
block outside `at.main` and `at.sidebar`, so strip widgets are asserted
through the flat accessors only. Fixture rule for two-project strips: the
two projects must differ in default and base branch, and the seeded target
must not be the listing's first row — a fixture that agrees under the
intended rule and the plausible mutant proves nothing (the lesson of the
branch-listing fixture repair on PR #267, where a default flag on the
alphabetically first name left "default first" with zero coverage). These
tests need a fixture workspace: a temporary directory with one
`make_bundle` bundle (`_fixture.py:25-72`) pointed to by
`PYDOCS_WORKSPACE`, which replaces the reliance on `~/pydocs-index` in
`test_app_attachment.py:11-21`.

**Toolkit verifications (done during design; each names the evidence and the
test that keeps it true):**

- **V1** Verified from the 1.59.1 source and the release notes: `st.bottom`
  is public since 1.57.0 (`streamlit/__init__.py:114`; `_bottom` deprecated
  with sunset 2026-07-01, `:121-122`), a `st.chat_input` inside `with
  st.bottom:` is pinned by the container (`elements/widgets/chat.py:
  1012-1023`; the strip's rows precede it in the same container), and
  `st.bottom` refuses the sidebar and dialogs (`elements/bottom.py`). The
  2026-09-04 column-split claim is dropped with the composer popover.
  `st.chat_input` has no `value` parameter (`elements/widgets/chat.py`), so
  tokens are typed only. The floor is `streamlit>=1.59`, already on the
  branch (`pyproject.toml:170`, §6.10); no further bump. The private
  `_bottom` shim is not used — its sunset is past.
- **V2** Verified: `st.popover` takes `key` and `on_change`
  (`elements/layouts.py:1329-1330`, since 1.55.0), its session-state key
  holds the open bool only with `on_change` set, and a keyed popover is
  closed by writing `False` to that key from a callback. One AppTest asserts
  the `scope_picker` key exists (AC-49).
- **V3** Verified (above): the popover parses as a generic `Block` and its
  body runs on every script run; `at.checkbox` / `at.button` / `at.caption`
  / `at.expander` reach its children without opening it (AC-48); the
  picker body is therefore never gated on the popover's `open` property.
- **V7** Verified (closed): `at.pills` is an AppTest accessor on the pinned
  floor — the branch pins `streamlit>=1.59` (`pyproject.toml:170`) and the
  installed 1.59.1 has it at `testing/v1/app_test.py:527`
  (`WidgetList[ButtonGroup]`) with `.value`, `.options`, `.set_value`,
  `.select`, `.unselect` at `testing/v1/element_tree.py:808-920`. The
  2026-09-15 first draft wrote the floor as 1.57 and left this open; the
  code never targeted 1.57 (§12 O8, closed). A re-verification is owed
  only if the floor is ever lowered.
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
  **Settled 2026-09-15 (owner):** only the selected names are honored; an indexed-but-unselected `branch` from the model is replaced by the fan-out over the selection and logged — "Only these" stays hard.
- **O2 — Merged group sequencing and label.** The landing-unit amendment
  settles what a merged entry sends (the landing sha with `scope=diff`,
  §6.10), which makes the "merged" picker group a U2 item that depends on the
  landing-unit index (program plan P2.8) and the `diff` scope value (P2.3).
  Confirm that P2.8 precedes U2 in the multi-branch program, and confirm the
  label format `feature/old (merged into main @3e1a9c2)`.
  **Settled 2026-09-15 (owner):** P2.8 precedes U2 in the program, and the label is `feature/old (merged into main @3e1a9c2)`.
- **O3 — Shipped branch default spelled `base`, not `main`.** D3 lists the
  YAML value as `main` and words the panel entry "main = the base branch". A
  literal `main` has no row in a bundle indexed from another branch on P0
  (`storage/factories.py:904-915` stamps only the checked-out branch) and
  duplicates the server's base-branch detection. §7 spells the value
  `branch_default: base` (resolved to `branches.base_name`; D3's label text
  "main (base branch)" survives in the YAML comment only, because under D14
  the picker offers real branch names and no symbolic control — R3, §7).
  Proposed resolution: ratify `base`; the alternative is the literal name
  plus an unknown-name fallback to the default row. Note that `base_name` is
  stamped only from P1.6 (§1.2), so either spelling resolves to nothing on
  P0. D14 does not touch the spelling: the strip inherits whichever is
  ratified; what D14 settles is that the value is YAML-only in-session.
  **Settled 2026-09-15 (owner):** `base`.
- **O4 — One held session for the app** (escalated by D14). Fan-out
  multiplies the per-call subprocess spawn of the app's tool binding (§6.4).
  Under the 2026-09-04 design fan-out was an occasional pin; under design A
  a two-project selection is the headline gesture, so the per-cell spawn is
  on the common path. Before U0r ships a multi-target strip, does the chat
  page move to the binding's held-session shape (`binding.py:309-327`), or
  is the `max_cells` cap enough for now?
  **Closed by fact 2026-09-15:** the chat page already holds one serve session per browser tab (`page_agent.py`) and the fan-out calls each cell through that same handler, so a multi-target selection costs calls, not processes; the `max_cells` cap bounds the calls.
- **O5 — Gating the catalog branch listing on the `branch` capability**
  (§6.6). This is the only reading that keeps R7 and R11 both true on P0
  bundles; the alternative is to accept a catalog byte change on every
  post-P0 workspace and regenerate the prompt-seam expectations.
  **Settled 2026-09-15 (owner):** the catalog's branch listing stays gated on the `branch` capability.
- **O6 — Per-project freshness in the footer.** The sha and staleness come
  from the first bundle's probe (§9 E6). Should the multi-branch program add
  a per-project probe (a server change), or is the tooltip caveat
  acceptable? The D14 freshness sentence ("index behind your checkout —
  reindex to search it") makes the caveat more visible, not less.
  **Settled 2026-09-15 (owner):** multi-branch P1 adds a per-project freshness probe (`meta.index_stale` and the indexed head per served bundle) so the footer's "index behind your checkout — reindex to search it" is true per project; recorded as O19 of the multi-branch design.
- **O7 — The attachment chip row under design A.** D14 puts target chips in
  the strip and is silent on attached symbols. §6.10 keeps them in a
  separate row with their own "clear all"; the alternative is to render
  attached symbols as chips in the strip's row 1. AC-30 and the
  `render_attachment_chip_row` fragment depend on the answer.
  **Settled 2026-09-15 (owner):** attached symbols keep their own row with their own "clear all" — a strip chip is a search target, an attached symbol is question context.
- **O8 — `st.pills` in AppTest on the pinned floor** — *closed* (§11 V7):
  the branch already pins `streamlit>=1.59` (`pyproject.toml:170`) and
  `at.pills` exists there (`testing/v1/app_test.py:527`,
  `element_tree.py:808`); the first draft's 1.57 floor described a version
  the code never targeted. The picker's branch control stays `st.pills`.
- **O9 — `max_cells` default now that it is on screen.** The strip shows
  "limit M" at two or more targets and the picker shows "N of M"; the
  proposals mockup shows "limit 4". Confirm that 4 stays the shipped
  default now that the cap is user-visible.
  **Settled 2026-09-15 (owner):** 4 stays the shipped default.
- **O10 — Where "Only these" is forced** — *closed* by the §6.7 lifecycle:
  the engine ignores the checkbox at two or more cells
  (`compile_strip_scope` compiles them to `PIN` whatever it holds, §6.1)
  and the UI mirrors it — the widget key is written on and disabled while
  N ≥ 2, the user's own choice is kept apart in `StripState.only_these`
  and restored when N drops to 1 (E16, AC-42). Both readings of the first
  draft collapse into one: the engine is the source, the checkbox never
  disagrees with it, and the user's tick is never overwritten.

Items D14 settles, recorded so they are not re-opened: the interface of
D2 (a sidebar button), D3 (a defaults panel) and D4 (a composer popover with
"keep for next" and pin chips) — replaced by the strip, the picker and the
typed tokens; the 2026-09-04 popover summary label (deleted, §6.4a); the
whole-pin drop on a workspace change (now per target, E12); the
one-`PIN_BRANCH`-chip-per-cell rule (now one chip per kind, §6.9); the
fan-out label rule (now "what was sent", §6.4 rule 3); and, from the
same-day review pass, the symbolic "base" / "checked out" branch controls
(YAML-only under D14, R3) and the one U0 cell shape for every source
(§6.4a).

---

## Amendments

**2026-09-15** — owner decision D14 after reviewing the stage-U0
implementation (draft PR #267) against the proposals page (§13): the U0
interface is designs A + C; the engine stays. Directions: (A1) the sidebar
is connection only — the "Scope defaults" button and panel are removed from
the chat page, YAML still seeds the initial state, and the graph page's
button becomes "Where to search" over the same picker; (A2) the strip —
"Searching in" plus one chip per target and "Change…" inside `st.bottom`
above a plain chat input, with "Only these", the searches-per-question
count and "Clear" on a second row, sticky in session state, compiled to
the scope by one pure function, invalidated per target; (A3) the picker —
a keyed popover "Where to search" with one checkbox per project, branch
pills after P1, a "More" expander for code / package / "Which files", a
preview with the cap, "Use these" and "Reset", no free text; (A4) typed
tokens — `in:<project>` / `on:<branch>` anywhere in the question, parsed by
a pure `scope_tokens.py`, validated against the listing, refusing the send
on an unknown name, `on:` refused until `branch_selector` is advertised,
stripped before reformulation and `ask()`, sent under a one-shot pin,
governed by `tokens_enabled`; (A5) the footer line "Searched … (<origin>) ·
<freshness>" with the origin phrases, the never-hidden freshness sentence
and a teaching hint governed by `footer_hint`; four follow-up buttons
including the new `ASK_ON`, at most one per kind; fan-out block labels name
what was sent; (A6) the vocabulary table applied to every on-screen string,
the model-facing note untouched; (A7) the new widget keys and the absence
assertion over the old ones; (A8) staging U0r / U1 / U2. D14 supersedes the
interface parts of D2, D3 and D4; D1 and D5–D13 stand.

Sections touched, one line each:

- Header status block — fourteen decisions; the dated amendment note (A1–A8).
- Goal and Abstract — the always-visible sentence, the strip compiling to
  the two kinds, the token channel (A2, A4).
- §1.2 — the P1.6 bullet names the "Compare with <base>" button of §6.9,
  not the compare chip the 2026-09-04 text had there (A5).
- §1.3 — the diagnosis kept, the conclusion replaced by the two problems the
  review found and the matrix picker (A1, A3).
- §2 Terms — "Scope defaults" and "Pin" re-worded, "keep for next" gone,
  "Landing unit" re-homed in the pills; Target and Token added (A2, A4, A6).
- §3 — D14 preamble; R2, R3, R4 restated; R6, R8, R9, R10, R11, R12, R13
  extended (A1–A8).
- §4 — the first goal replaced; two goals and one non-goal added (A2, A4).
- §5 — the composer bar and typed mentions converted to adopted approaches
  with their objections answered; the pills row rejected (A2, A4).
- §6.1 — the table re-drawn; `compile_strip_scope` as the normative mapping;
  stickiness and invalidation (A2).
- §6.2 — the override is the strip's; one resolution path; the note is not
  on screen (A2, A6).
- §6.3 — editorial note on which strip states reach which column.
- §6.4 — rule 3: labels name what was sent; the picker's cell order; O4
  escalated (A5).
- §6.4a — the picker builds the full matrix; "Use these" cap; the summary
  label deleted; E4 via tokens (A3, A4).
- §6.5 — token pins observe as `PINNED`, no fifth origin; session-state
  wording (A4).
- §6.6 — tokens stripped before the prompt; strip and picker fed on U0 (A4).
- §6.7 — replaced wholesale: sidebar, strip, picker, tokens, transcript,
  words, widget keys (A1, A2, A3, A6, A7).
- §6.8 — the footer format, origin words, freshness sentence, teaching hint
  (A5).
- §6.9 — four kinds, one chip per kind, the new labels, `ASK_ON`,
  `kept_pin` → strip scope (A5, A6).
- §6.10 — retitled; picker replaces popover; pills replace multiselect; the
  column split deleted; the chip row split; listing and merged group kept
  (A2, A3).
- §6.10a — new: typed tokens (A4).
- §6.11 — the "Where to search" button and shared keys; AC-30's target (A1).
- §6.12 — the U0r row and the three-plan list (A8).
- §6.13 — `scope_tokens.py`; `scope_panel.py` contents and ceiling; `app.py`
  send path; README caption; CHANGELOG (A4, A7, A8).
- §7 — `tokens_enabled`, `footer_hint`; nine keys; two comments (A4, A5).
- §8 — byte-identity through the no-target strip; token neutrality (A2, A4).
- §9 — E3, E4, E6, E7, E8, E11, E12 revised; E13–E16 added (A4, A5).
- §10 — AC-6, AC-6b, AC-17, AC-18, AC-22, AC-25, AC-26, AC-30–AC-34
  reworded; AC-19, AC-20, AC-21, AC-21b superseded; AC-35–AC-51 added.
- §11 — strip-state AppTests, `test_scope_tokens.py`, the compile table,
  fixture rules; V1–V3 restated; V7 added.
- §12 — O3, O4, O6 prose; O7–O10 added; the items D14 settles recorded.
- §13 — the proposals page, the load-bearing test, PR #267, the pills
  citation.

**2026-09-15, second pass** — review of the first amended draft against the
branch (`feat/ayd-branch-scope-u0`, `pyproject.toml:170` already at
`streamlit>=1.59`, `question_scope.py` at 348 lines, the config model in
`ask_your_docs_scope_models.py`) and against Streamlit's popover and
checkbox semantics. Directions: (B1) one U0 cell shape for strip, token
and graph cells — the listing's default-row name, `""` only on E8 —
through one helper `listing_cell`; (B2) the strip's targets are the only
source of the project override, with R3's seed rule mapping YAML into the
strip once, so "Clear" really sends the union; a target's chosen branch
travels as `branch_name` under a soft one-cell `DEFAULT`; (B3) the strip
state is a named frozen `StripState` under `scope_strip`, `apply_follow_up_chip`
returns it, `derive_follow_up_chips` takes the compiled scope and the
stripped question; (B4) a `st.popover` is its own trigger: "Change…" is
the chat page's popover label (key `scope_picker`), "Where to search" the
graph page's (key `graph_where_to_search`), one shared body; (B5) token
grammar: trailing punctuation stripped, empty-name prefixes are text,
duplicates collapse, case-sensitive, stems normalized, merged entries
picker-only, the strip's targets never added to a token question, a
user-facing cap sentence; (B6) the "Only these" lifecycle keeps the user's
choice apart from the forced widget value; (B7) a union answer carries no
teaching hint; (B8) `merge_cell_results` takes the sent arguments;
(B9) chips per cell, E12 matching by project on U0, the graph Branch
selectbox preselection rule, Reset replacing the strip, the activity
panel's scope line, AC-47 as enumerated whole segments, "inactive" for
"dormant", the reworded-AC count made consistent.

Sections touched, one line each:

- Header — the second-pass note (B1–B9).
- §2 Terms — Target per cell; Strip state added; Base branch YAML-only (B3, B9).
- §3 — R3 seed rule and YAML-only branch default; R4 chips per cell and the
  popover as trigger; R8 one popover, shared state; R9 wording; R10 four
  refusals (B2, B4, B9).
- §4 — the merged-entry exception to the goal (B5).
- §6.1 — `compile_strip_scope(strip, config, listing)`; the union override;
  `branch_name` from the target; one cell shape (B1, B2, B3).
- §6.4 — `merge_cell_results(cells, sent_arguments, results)`,
  `cell_label(cell, branch_sent)` (B8).
- §6.4a — one U0 cell shape, `listing_cell`, the E8 shape; the picker cap
  caption (B1, B5).
- §6.7 — chips per cell; the single-project rule; `StripState`; the "Only
  these" lifecycle; E12 matching; the popover as trigger; Reset and
  re-seeding; the activity line; the widget-key notes (B3, B4, B6, B9).
- §6.8 — the union counts as searched; the hint as a plain string (B7).
- §6.9 — `question` and the compiled scope in `derive_follow_up_chips`;
  `apply_follow_up_chip` returns `StripState`; `ASK_ON` skips the compare
  base and re-sends the stripped text (B3).
- §6.10 — the `streamlit>=1.59` floor; chips per cell; both popovers (B4, B9).
- §6.10a — the grammar rules; the compile line; the strip not added; the
  cap sentence (B1, B5).
- §6.11 — the graph popover, shared `StripState`, Branch preselection (B4, B9).
- §6.12 — "inactive"; the floor (B9).
- §6.13 — `question_scope.py` size and budget; `scope_tokens.py`,
  `scope_interceptor.py`, `answer_footer.py`, `scope_panel.py`,
  `activity_labels.py`, `ask_your_docs_scope_models.py`, `pyproject.toml`
  rows (B3, B8, B9).
- §7 — the model's file; the `branch_default` comment (B9).
- §9 — E4, E8, E10, E12, E13, E16 (B1, B5, B6, B9).
- §10 — twelve reworded (AC-6b, AC-26 tagged); AC-31, AC-33, AC-35, AC-36,
  AC-37, AC-39, AC-41–AC-48, AC-50, AC-51 revised; AC-52 added.
- §11 — the compile table, the grammar tests, the vocabulary sweep, the
  AppTest list; V1 restated; V7 closed.
- §12 — O3 prose; O8 and O10 closed; the settled items extended.
- §13 — the pin line.

---

Implementation plan: `docs/superpowers/plans/2026-09-04-ask-your-docs-branch-scope-ui.md` (one document, three stage sections U0 / U1 / U2, one PR per stage; a U0r stage between U0 and U1 carries this amendment — the plan is amended separately).

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
  (amended 2026-09-04, commit `1c371bc`) — P1.1 schema v18 (`:33`),
  P1.6 tracking policy / `base_name` (`:38`), P1.7 retirement (`:39`),
  P1.8 / P1.9 read path and parameter (`:40-41`), P2.1–P2.4 (`:64-67`),
  P2.8 landing-unit index (`:71`).
- `docs/tool-contracts.md` — §2.4 `meta.branch` (`:142-164`), §3 inventory
  (`:167`), §5.2 sanctioned parameter categories (`:450`).
- The owner's design source for D14: the proposals page
  `pydocs-handoffs/2026-09-15/where-to-search-proposals.html` (kept outside
  the repository) — sections `#a` (one strip, adopted), `#c` (typed tokens,
  adopted as a layer on A), `#compare`, `#words` (the vocabulary table of
  §6.7), `#staging`, `#decisions` (the five owner choices). The stage-U0
  implementation it reviewed is draft PR #267 (`feat/ayd-branch-scope-u0`);
  the test the decision cites as proof that today's server accepts a
  two-project pin is
  `tests/harness/ask_your_docs/test_scope_interceptor.py::test_ac6b_two_project_pin_fans_out_over_project_only`.
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
- Config: `retrieval/config/ask_your_docs_scope_models.py` (`:51-79`,
  re-exported from `ask_your_docs_models.py:32-37`),
  `defaults/default_config.yaml` (`:406-428`), `tests/test_config_ask_your_docs.py`
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
  `elements/widgets/chat.py` (`:1012-1023`; no `value` parameter on
  `st.chat_input`), `elements/layouts.py` (`:1319-1333`, `:1461-1464` the
  keyed popover's session value), `elements/bottom.py`,
  `elements/lib/mutable_popover_container.py` (the body always runs),
  `elements/widgets/button_group.py` (`st.pills`, `selection_mode="multi"`),
  `__init__.py` (`:110-122`), `testing/v1/element_tree.py` (`:2672-2673`;
  `:2538-2543` the roots the tree seeds — the bottom container is an
  anonymous block), `testing/v1/app_test.py` (`:497`, `:527` `at.pills`,
  `:784` `at.expander`, `:966`, `:1140` `at.toast`); release notes 1.55.0 /
  1.57.0; the pin `pyproject.toml:170` (`streamlit>=1.59`). (The `multiselect.py`
  `accept_new_options` citation of the 2026-09-04 text is dropped with the
  multiselect.)
- Precedents: ADR 0008 and `session_start_injection.py:14-34` ("None keeps
  the prompt byte-identical"); `agent.py:77-79` (`_reinspect_state`, the
  mutable per-turn contextvar container); `_CODE_CHOICES` (`app.py:95`).
