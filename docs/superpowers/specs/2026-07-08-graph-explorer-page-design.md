# Graph Explorer — second Streamlit page for ask-your-docs

**Date:** 2026-07-08
**Status:** Approved (design), pending implementation plan
**Component:** `pydocs_mcp/ask_your_docs/` (the `[ask-your-docs]` extra)

## Goal

Add a second screen to the ask-your-docs Streamlit app for interactively
exploring a project. Start from a whole-project overview (modules and their
edges), click any node to open its connections, read docstrings / doc text in a
side panel, and filter what is shown along three axes: **content type**
(codebase / documentation / both), node type, and edge kind. The graph also
acts as a **selector for the chat**: attach nodes you find to your next chat
question. Only a tiny, additive touch to the chat page; no core library changes.

## Non-goals

- No new indexing or schema changes — this is a read/visualization layer over
  existing tables (`node_references`, `module_members`, `chunks`).
- No MCP calls and no LLM on this page. It reads the bundle directly.
- No graph editing, no persistence of layouts, no cross-project graphs.

## Data model (already in the bundle)

- `node_references(from_package, from_node_id, to_name, to_node_id, kind)` —
  code edges. `kind` ∈ {`calls`, `imports`, `inherits`} (also `mentions` when
  captured). Node ids are dotted paths: a module (`examples.custom_retriever`)
  or a member (`examples.custom_retriever.ColorPatchRetriever`). `to_node_id`
  is null for references that don't resolve inside the indexed corpus.
- `module_members(package, module, name, kind, signature, return_annotation,
  parameters, docstring)` — member metadata. `kind` ∈ {`class`, `def`}.
- `chunks(id, package, module, title, text, origin, qualified_name, …)` —
  content chunks. `origin` is the content discriminator:
  - `python_def` — code (the same symbols as the reference graph).
  - `markdown_section` — a section of a `.md` doc (README, CLAUDE.md, …), with
    a title, text, and a module/package association.
  - `decision_record` — an architectural decision (also carries a `decision_id`;
    surfaced by `search_codebase(kind="decision")` / `get_why`).
- A project's own code lives under package `__project__` in its bundle.
- Scale reference: example_needle = 77 modules, 2250 code edges, 356 markdown
  sections, 0 decision records; coding-agent-playbook = 686 markdown sections.
  Both demo bundles have **no** decision records (this repo does — testable by
  indexing it). The module overview is a ~77-node graph; drill-downs are small.

## Content types and node categories (UI vocabulary → data)

The **content-type selector** chooses which node categories appear:

- **Codebase** — code nodes only.
- **Documentation** — doc + decision nodes only.
- **Documentation + codebase** — all three, with doc/decision→code links.

Node categories:

| Category | Source | Node identity | Attaches to code via |
|---|---|---|---|
| **code** | `module_members` + `node_references` | dotted path (module / class / function) | `calls` / `imports` / `inherits` (+ `contains` module→member) |
| **doc** | `chunks WHERE origin='markdown_section'` | one node per `.md` file; expands into its sections | a `documents` edge to the module/package the file lives under |
| **decision** | `chunks WHERE origin='decision_record'` | one node per decision (`decision_id`); title = summary | a `concerns` edge to referenced symbols, else grouped under the project |

Within the **code** category, the node-type filter (module / class / function
= `module_members.kind` module/class/def) and the edge-kind filter
(calls / imports / inherits) still apply. When the content type is
**Documentation** only, those code-specific filters are inert (greyed out).

`documents` and `concerns` are structural edges (like `contains`): always drawn
between two visible nodes when the content type includes both worlds; they are
NOT part of the calls/imports/inherits toggle.

## Architecture

Additions, all inside `pydocs_mcp/ask_your_docs/`: a `graph.py` query module, a
`pages/2_Graph.py` page, and a small additive touch to `app.py` (render
attachment chips + prepend a context line on send).

### 1. `graph.py` — pure, read-only query functions

Mirrors `catalog.py`: opens each bundle via a `mode=ro` URI, never mutates it,
never goes through `pydocs_mcp.multirepo.open_index_database` (which opens
read-write and can migrate/rebuild). Functions:

- `overview(db_path, project) -> Graph` — the project's own modules as nodes,
  plus module→module edges. An edge (A, B, kind) exists when a node in module A
  has a `node_references` row of that kind whose `to_node_id` resolves to a
  node in module B (external / unresolved targets are dropped at the overview
  level). Edges are deduped per (A, B, kind).
- `expand(db_path, node_id, node_type, kinds) -> Graph` — expansion is
  **type-dependent**, matching the overview→drill mental model:
  - Clicking a **module** reveals the classes/functions it **contains** (from
    `module_members` for that module). Each is linked to the module by a
    structural `contains` edge.
  - Clicking a **class or function** reveals its **reference neighbors** in both
    directions (rows where `from_node_id == node_id` OR `to_node_id == node_id`),
    restricted to the enabled edge `kinds` (calls / imports / inherits), linked
    by edges of those kinds.
  Capped at a fixed `MAX_NEIGHBORS` (e.g. 50); when there are more, return the
  cap plus a truncated-count flag so the UI can say "showing 50 of N" (no
  silent truncation).

  `contains` is a fourth, structural edge kind — distinct from the three
  reference kinds. It is NOT one of the calls/imports/inherits filter toggles;
  it is always drawn between two visible nodes (styled subtly, e.g. a thin grey
  line) so drill-downs read as nested. The reference-kind filter only governs
  calls/imports/inherits.
- `node_meta(db_path, node_id) -> NodeMeta | None` — for a member node, its
  kind + signature + docstring from `module_members`; for a module node, its
  path + member/edge counts; for a doc node, the file path + section text; for a
  decision node, the decision summary + body. Feeds the side panel.

The doc + decision categories add sibling queries (Phase 2), same shape:
- `doc_nodes(db_path, project) -> Graph` — one node per `.md` file (group
  `chunks WHERE origin='markdown_section'` by their source file), plus a
  `documents` edge to the module/package each file lives under. `expand` on a
  doc-file node reveals its sections; on a module node (when docs are enabled)
  also reveals the doc files that `documents` it.
- `decision_nodes(db_path, project) -> Graph` — one node per
  `chunks WHERE origin='decision_record'` (`decision_id`), plus a `concerns`
  edge to any symbol it references, else attached to the project node.

`Graph` is a small frozen value object: `nodes: tuple[Node, ...]`,
`edges: tuple[Edge, ...]`. `Node` carries `id`, `label` (short name),
`node_type` (module / class / function / **doc** / **decision**). `Edge`
carries `source`, `target`, `kind` (calls / imports / inherits / contains /
**documents** / **concerns**). Plain dataclasses — no coupling to any rendering
library.

### 2. `pages/2_Graph.py` — the page

Streamlit multipage: a `pages/` directory beside `app.py`. Streamlit
auto-discovers it and adds the sidebar page switcher; `app.py` stays page 1
("chat"), this is page 2 ("graph"). No navigation code.

State: a single `visible: set[str]` of node ids in `st.session_state`, plus the
currently `selected` node id. On first load, `visible` = the overview seed for
the enabled content type (code → the project's modules; documentation → its
`.md` files + decisions). The render pipeline each run:

1. Read the content-type selector + node-type + edge-kind filters.
2. Build the display graph = the subgraph of everything seeded/expanded so far,
   induced by `visible`, keeping only nodes whose category+type is enabled and
   edges whose kind is enabled (structural `contains`/`documents`/`concerns`
   edges always kept between two visible nodes).
3. Render with `streamlit-agraph`; it returns the clicked node id.
4. On a click: `selected = clicked`; add `expand(...)` results to `visible`
   (module → members and, if docs enabled, attached doc files; doc file →
   sections; class/function → reference neighbors); show `node_meta(clicked)`
   in a right-hand panel.

Controls (sidebar): the workspace + project picker reused from `catalog.py`
(no duplication), the **content-type selector** (codebase / documentation /
both), node-type checkboxes (module / class / function), edge-kind checkboxes
(calls / imports / inherits), and a **Reset** button that returns
`visible` to modules-only. Theme via the existing `theme.py` (`theme_css`), so
dark/light matches the chat page.

### 3. Graph → chat attachment (shared session_state)

The node panel gets an **"➕ Add to question"** button. It appends the selected
node's fully-qualified dotted id to `st.session_state.attached` (a de-duped,
order-preserving list). Because Streamlit shares `session_state` across pages
in one session, no other plumbing is needed.

The chat page (`app.py`) reads that list and:
- Renders the attachments as removable chips above the chat input (each with a
  ✕ that drops it from `attached`), with a "clear all" affordance.
- On send, weaves them into the question as plain context text (approach A),
  e.g. `Regarding `a.b.C`, `d.e.f`: <question>`, then clears `attached`. The
  agent resolves them with its existing `get_symbol` / `get_context` tools.

The fully-qualified path is unambiguous, so an attachment resolves regardless
of the chat's scope pins. This is the ONLY change to `app.py`: read a list,
render chips, prepend a line on send. The `agent.py` API is untouched (the
woven text goes through the existing `ask(...)` path).

## Data flow

```
pick project ──▶ catalog (read-only)          [reuse]
     │
     ▼
graph.overview(db, project) ──▶ visible = {modules}
     │
     ▼  (each rerun)
filters ──▶ induced subgraph ──▶ streamlit-agraph ──▶ clicked node id
                                                          │
                          ┌───────────────────────────────┤
                          ▼                                ▼
       graph.expand(node, type, kinds)          graph.node_meta(node)
       module→members (contains) /              → docstring / signature panel
       member→refs (calls/imports/inherits)         + "➕ Add to question"
       add to visible                                     │
                                                          ▼
                                     session_state.attached  ──(shared)──▶ chat page
                                     chips above chat input; woven into the
                                     question text on send, then cleared
```

## Phasing (how the plan stages the build)

To keep each step small and verifiable ("don't overcomplicate"):

- **Phase 1 — code graph.** `graph.py` (`overview` / `expand` / `node_meta`),
  `pages/2_Graph.py` with the node-type + edge-kind filters, docstring panel,
  and the graph→chat attachment. Content-type selector present but fixed to
  **Codebase**. Fully verifiable against both demo bundles today.
- **Phase 2 — documentation + decisions.** Add `doc_nodes` / `decision_nodes`
  and the `documents` / `concerns` edges, wire the content-type selector's other
  two positions, extend the panel to show doc/decision text. Markdown is
  verifiable against the demo bundles now; decisions are verified by indexing
  this repo (which captures them) since the demo bundles have none.

Same spec, same files — Phase 2 only adds sibling query functions and turns on
the selector. Each phase ships and is reviewed on its own.

## Error handling

- Unreadable workspace / missing bundle / corrupt db: same pattern as the chat
  page's catalog scan — catch, `st.warning`, render nothing.
- Project with an empty reference graph (capture disabled at index time): show
  an info message ("no reference graph in this bundle — enable
  `reference_graph.capture` and re-index") instead of a blank canvas.
- `streamlit-agraph` unavailable: guarded import with an actionable message
  pointing at the extra (consistent with the CLI's `_require_extra`).

## Packaging

- Add `streamlit-agraph` to the `[ask-your-docs]` extra in the root
  `pyproject.toml`. It bundles its own frontend build, so it works offline once
  installed.
- The page and `graph.py` ship automatically (maturin packages the whole
  `python/` tree). `graph.py` is excluded from the mypy gate along with the
  rest of `ask_your_docs/` (already configured).

## Testing

- `graph.py` is pure functions over a `mode=ro` connection → unit-tested
  against a tiny fixture bundle (same approach as the catalog read-only test):
  assert overview nodes/edges, edge-kind filtering in `expand`, the neighbor
  cap + truncation flag, `node_meta` docstring extraction, and — critically —
  that a fixture bundle stamped at a newer `user_version` is byte-for-byte
  unchanged after a full scan (no migration/wipe).
- Doc / decision queries (Phase 2) are unit-tested the same way: a fixture
  bundle with `markdown_section` chunks asserts `.md`-file grouping, section
  expansion, and the `documents` edge; a fixture with a `decision_record` +
  `decision_id` asserts the decision node + `concerns` edge (the demo bundles
  have no decision records, so this relies on a fixture, not a demo db).
- Page-level logic (the `visible`-set expansion and filter induction) is
  factored into pure helpers so it is testable without rendering; the
  `streamlit-agraph` component itself is not asserted in tests.
- The attachment weave is a pure helper (`attached` list + question → woven
  string, de-duped, cleared) → unit-tested directly; the chip UI is not
  asserted. An AppTest can also drive the chat page with a seeded
  `session_state.attached` to confirm the context line is prepended and the
  list is cleared after send.

## Open risk

`streamlit-agraph` is a third-party component of moderate maintenance. If it
misbehaves with the installed Streamlit, the fallback is a static
`st.graphviz_chart` render plus a selectbox to choose the node to expand —
this keeps the whole design (queries, filters, side panel, `visible` state)
and only swaps the render+click surface. Decision: use `streamlit-agraph`
first; fall back only if it breaks.
