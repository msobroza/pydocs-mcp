# Graph Explorer Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second Streamlit screen to the `ask-your-docs` app that explores a project's graph — overview of modules, click-to-expand connections, docstrings in a side panel, filters for content type / node type / edge kind, and an "add to question" bridge to the chat.

**Architecture:** A new read-only query module (`graph.py`, mirrors `catalog.py`'s `mode=ro` no-wipe pattern) exposes pure functions returning small `Graph` value objects. A native Streamlit `pages/` page renders them with `streamlit-agraph` (returns the clicked node → drives expansion). Graph→chat is a shared `session_state.attached` list woven into the question. Built in two phases: Phase 1 = code graph; Phase 2 = markdown-doc + decision content types.

**Tech Stack:** Python 3.11, stdlib `sqlite3` (read-only), Streamlit multipage, `streamlit-agraph`, existing `theme.py` / `catalog.py`.

**Spec:** `docs/superpowers/specs/2026-07-08-graph-explorer-page-design.md`

**Data facts (verified against the demo bundles):**
- Own code lives under package `__project__`.
- A code member's node id = `f"{module}.{name}"` (`module_members.name` is bare); this equals the dotted ids in `node_references.from_node_id` / `to_node_id`.
- `node_references(from_package, from_node_id, to_name, to_node_id, kind)`, kinds `calls`/`imports`/`inherits`; `to_node_id` is NULL for unresolved/external targets.
- `module_members(package, module, name, kind, signature, return_annotation, parameters, docstring)`, kind ∈ {`class`,`def`}.
- `chunks(id, package, module, title, text, origin, content_hash, qualified_name)`; `origin` ∈ {`python_def`, `markdown_section`, `decision_record`}. For `markdown_section`, `module` is the doc file path and `title` is the section heading.

**Test env:** run `pytest` from the repo root (the `test` dependency-group env, where `pydocs_mcp` is importable via `pythonpath=["python"]`). Graph tests import only `pydocs_mcp.ask_your_docs.graph`, which must stay free of the heavy agent stack (Task 1 guarantees this).

---

## File structure

- Create `python/pydocs_mcp/ask_your_docs/graph.py` — value objects (`Node`, `Edge`, `Graph`, `NodeMeta`), read-only queries (`overview`, `expand`, `node_meta`, `induce`), and Phase-2 sibling queries (`doc_nodes`, `decision_nodes`). Pure, stdlib-only.
- Modify `python/pydocs_mcp/ask_your_docs/__init__.py` — make re-exports lazy so importing `graph`/`catalog` never pulls in langgraph/streamlit.
- Modify `python/pydocs_mcp/ask_your_docs/agent.py` — add a pure `weave_attachments(attached, question)` helper.
- Create `python/pydocs_mcp/ask_your_docs/pages/2_Graph.py` — the page.
- Modify `python/pydocs_mcp/ask_your_docs/app.py` — attachment chips + weave on send.
- Modify `pyproject.toml` — add `streamlit-agraph` to the `[ask-your-docs]` extra.
- Create `tests/ask_your_docs/__init__.py`, `tests/ask_your_docs/_fixture.py` (tiny-bundle builder), `tests/ask_your_docs/test_graph.py`, `tests/ask_your_docs/test_attachment.py`.

---

# Phase 1 — code graph

### Task 1: Slim `__init__.py` so `graph.py` imports lean

**Files:**
- Modify: `python/pydocs_mcp/ask_your_docs/__init__.py`
- Test: `tests/ask_your_docs/test_graph.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ask_your_docs/__init__.py` (empty), then in `tests/ask_your_docs/test_graph.py`:

```python
import subprocess
import sys


def test_graph_import_does_not_pull_agent_stack():
    # graph.py must be importable without langgraph/streamlit installed.
    code = (
        "import sys; import pydocs_mcp.ask_your_docs.graph as g; "
        "assert 'langgraph' not in sys.modules and 'streamlit' not in sys.modules; "
        "print('lean')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert "lean" in out.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ask_your_docs/test_graph.py::test_graph_import_does_not_pull_agent_stack -v`
Expected: FAIL — `graph` doesn't exist yet / `__init__` imports agent (langgraph). (After Task 2 creates `graph.py`, this stays failing until this task's `__init__` change lands; that's fine — do Step 3 now.)

- [ ] **Step 3: Make the re-exports lazy**

Replace `python/pydocs_mcp/ask_your_docs/__init__.py` with:

```python
"""Ask-your-docs — a LangGraph ReAct agent over pydocs-mcp, with a Streamlit UI.

Re-exports are lazy (PEP 562 ``__getattr__``) so importing a light submodule
like ``graph`` or ``catalog`` never drags in the heavy agent stack (langgraph /
streamlit), which only ships with the ``[ask-your-docs]`` extra.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ask",
    "build_agent",
    "reformulate",
    "render_catalog",
    "scope_prefix",
    "workspace_catalog",
]

_LAZY = {
    "ask": "agent",
    "build_agent": "agent",
    "reformulate": "agent",
    "scope_prefix": "agent",
    "render_catalog": "catalog",
    "workspace_catalog": "catalog",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(f"{__name__}.{module}")
    return getattr(mod, name)
```

- [ ] **Step 4: Verify (after Task 2 lands, re-run)**

Run: `pytest tests/ask_your_docs/test_graph.py::test_graph_import_does_not_pull_agent_stack -v`
Expected: PASS once `graph.py` exists (Task 2). Also confirm the public API still resolves:
Run: `python -c "from pydocs_mcp.ask_your_docs import build_agent, workspace_catalog; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/ask_your_docs/__init__.py tests/ask_your_docs/__init__.py tests/ask_your_docs/test_graph.py
git commit -m "refactor(ask-your-docs): lazy package re-exports so leaf modules import lean"
```

---

### Task 2: Value objects + fixture builder + `overview()`

**Files:**
- Create: `python/pydocs_mcp/ask_your_docs/graph.py`
- Create: `tests/ask_your_docs/_fixture.py`
- Test: `tests/ask_your_docs/test_graph.py`

- [ ] **Step 1: Write the fixture builder**

Create `tests/ask_your_docs/_fixture.py`:

```python
"""Build a tiny read-only-safe pydocs bundle for graph tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE index_metadata (project_name TEXT, indexed_at REAL);
CREATE TABLE packages (name TEXT, embedding_model TEXT);
CREATE TABLE module_members (
    id INTEGER PRIMARY KEY, package TEXT, module TEXT, name TEXT, kind TEXT,
    signature TEXT, return_annotation TEXT, parameters TEXT, docstring TEXT
);
CREATE TABLE node_references (
    from_package TEXT, from_node_id TEXT, to_name TEXT, to_node_id TEXT, kind TEXT
);
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY, package TEXT, module TEXT DEFAULT '',
    title TEXT, text TEXT, origin TEXT, content_hash TEXT, qualified_name TEXT
);
"""


def make_bundle(
    path: Path,
    *,
    project: str = "demo",
    user_version: int = 99,
    members: list[tuple[str, str, str]] = (),      # (module, name, kind)
    refs: list[tuple[str, str, str]] = (),          # (from_node_id, to_node_id, kind)
    markdown: list[tuple[str, str, str]] = (),      # (file, title, text)
    decisions: list[tuple[str, str]] = (),          # (title, text)
    docstrings: dict[str, str] | None = None,       # node_id -> docstring
) -> Path:
    docstrings = docstrings or {}
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.execute(f"PRAGMA user_version={user_version}")
    conn.execute("INSERT INTO index_metadata VALUES (?, ?)", (project, 1.0))
    conn.execute("INSERT INTO packages VALUES ('__project__', '')")
    for module, name, kind in members:
        node_id = f"{module}.{name}"
        conn.execute(
            "INSERT INTO module_members (package, module, name, kind, signature, "
            "return_annotation, parameters, docstring) VALUES "
            "('__project__', ?, ?, ?, ?, '', '', ?)",
            (module, name, kind, f"def {name}(...)", docstrings.get(node_id, "")),
        )
    for from_id, to_id, kind in refs:
        to_name = (to_id or "").rsplit(".", 1)[-1]
        conn.execute(
            "INSERT INTO node_references VALUES ('__project__', ?, ?, ?, ?)",
            (from_id, to_name, to_id, kind),
        )
    for i, (file, title, text) in enumerate(markdown):
        conn.execute(
            "INSERT INTO chunks (id, package, module, title, text, origin, "
            "content_hash, qualified_name) VALUES (?, '__project__', ?, ?, ?, "
            "'markdown_section', '', ?)",
            (1000 + i, file, title, text, f"{file}#{i}"),
        )
    for i, (title, text) in enumerate(decisions):
        conn.execute(
            "INSERT INTO chunks (id, package, module, title, text, origin, "
            "content_hash, qualified_name) VALUES (?, '__project__', '', ?, ?, "
            "'decision_record', '', ?)",
            (2000 + i, title, text, f"decision:{i}"),
        )
    conn.commit()
    conn.close()
    return path
```

- [ ] **Step 2: Write the failing test for `overview`**

Append to `tests/ask_your_docs/test_graph.py`:

```python
import sqlite3
from pathlib import Path

from tests.ask_your_docs._fixture import make_bundle


def _overview_bundle(tmp_path: Path) -> Path:
    # Two modules; mod_a.Foo calls mod_b.bar (module edge a->b, kind=calls).
    return make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("mod_a", "Foo", "class"), ("mod_a", "run", "def"),
                 ("mod_b", "bar", "def")],
        refs=[("mod_a.Foo", "mod_b.bar", "calls"),
              ("mod_a", "mod_b", "imports")],
    )


def test_overview_nodes_are_project_modules(tmp_path):
    from pydocs_mcp.ask_your_docs import graph

    g = graph.overview(_overview_bundle(tmp_path), "demo")
    assert {n.id for n in g.nodes} == {"mod_a", "mod_b"}
    assert all(n.node_type == "module" for n in g.nodes)


def test_overview_aggregates_cross_module_edges(tmp_path):
    from pydocs_mcp.ask_your_docs import graph

    g = graph.overview(_overview_bundle(tmp_path), "demo")
    pairs = {(e.source, e.target, e.kind) for e in g.edges}
    assert ("mod_a", "mod_b", "calls") in pairs
    assert ("mod_a", "mod_b", "imports") in pairs


def test_overview_read_only_never_migrates_bundle(tmp_path):
    from pydocs_mcp.ask_your_docs import graph

    db = _overview_bundle(tmp_path)  # stamped user_version=99
    graph.overview(db, "demo")
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 99
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/ask_your_docs/test_graph.py -k overview -v`
Expected: FAIL — `graph` module / `overview` not defined.

- [ ] **Step 4: Implement value objects + `overview`**

Create `python/pydocs_mcp/ask_your_docs/graph.py`:

```python
"""Read-only queries over a pydocs bundle, shaped into small graph values.

Mirrors ``catalog.py``: every connection is ``mode=ro`` and never routes through
``pydocs_mcp.multirepo.open_index_database`` (which opens read-write and can
migrate/rebuild a bundle). Stdlib-only so it imports without the agent stack.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.ask_your_docs.catalog import _ro_uri

MAX_NEIGHBORS = 50
_OWN = "__project__"


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    label: str
    node_type: str  # module | class | function | doc | decision


@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    target: str
    kind: str  # calls | imports | inherits | contains | documents | concerns


@dataclass(frozen=True, slots=True)
class Graph:
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()
    truncated: int = 0  # neighbors dropped by MAX_NEIGHBORS


@dataclass(frozen=True, slots=True)
class NodeMeta:
    id: str
    node_type: str
    title: str
    body: str


def _short(node_id: str) -> str:
    return node_id.rsplit(".", 1)[-1] or node_id


def _own_modules(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT module FROM module_members WHERE package=? ORDER BY module",
            (_OWN,),
        )
    ]


def _module_of(node_id: str | None, modules: list[str]) -> str | None:
    """Longest module that is the node id or a dotted prefix of it."""
    if not node_id:
        return None
    best: str | None = None
    for m in modules:
        if node_id == m or node_id.startswith(m + "."):
            if best is None or len(m) > len(best):
                best = m
    return best


def overview(db_path: Path, project: str) -> Graph:
    """The project's own modules as nodes + aggregated module→module edges.

    ``project`` is accepted for symmetry with the UI; the bundle already scopes
    the own code under ``__project__`` so it is not needed in the query.
    """
    with closing(sqlite3.connect(_ro_uri(db_path), uri=True)) as conn:
        modules = _own_modules(conn)
        rows = conn.execute(
            "SELECT from_node_id, to_node_id, kind FROM node_references WHERE from_package=?",
            (_OWN,),
        ).fetchall()
    nodes = tuple(Node(m, _short(m), "module") for m in modules)
    seen: set[tuple[str, str, str]] = set()
    edges: list[Edge] = []
    for from_id, to_id, kind in rows:
        a = _module_of(from_id, modules)
        b = _module_of(to_id, modules)
        if a and b and a != b and (a, b, kind) not in seen:
            seen.add((a, b, kind))
            edges.append(Edge(a, b, kind))
    return Graph(nodes, tuple(edges))
```

- [ ] **Step 5: Run to verify overview tests pass**

Run: `pytest tests/ask_your_docs/test_graph.py -k "overview or lean" -v`
Expected: PASS (including the Task 1 lean-import test, now that `graph` exists).

- [ ] **Step 6: Lint + commit**

```bash
ruff check python/pydocs_mcp/ask_your_docs/graph.py && ruff format python/pydocs_mcp/ask_your_docs/graph.py
git add python/pydocs_mcp/ask_your_docs/graph.py tests/ask_your_docs/_fixture.py tests/ask_your_docs/test_graph.py
git commit -m "feat(ask-your-docs): graph.overview — read-only module overview"
```

---

### Task 3: `expand()` — type-dependent expansion

**Files:**
- Modify: `python/pydocs_mcp/ask_your_docs/graph.py`
- Test: `tests/ask_your_docs/test_graph.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/ask_your_docs/test_graph.py`:

```python
def _expand_bundle(tmp_path):
    from tests.ask_your_docs._fixture import make_bundle
    return make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("mod_a", "Foo", "class"), ("mod_a", "helper", "def"),
                 ("mod_b", "bar", "def")],
        refs=[("mod_a.Foo", "mod_b.bar", "calls"),
              ("mod_a.Foo", "mod_a.helper", "calls"),
              ("mod_b.Base", "mod_a.Foo", "inherits")],
    )


def test_expand_module_reveals_members_with_contains(tmp_path):
    from pydocs_mcp.ask_your_docs import graph

    g = graph.expand(_expand_bundle(tmp_path), "mod_a", "module", kinds=frozenset())
    ids = {n.id: n.node_type for n in g.nodes}
    assert ids.get("mod_a.Foo") == "class"
    assert ids.get("mod_a.helper") == "function"
    assert all(e.kind == "contains" and e.source == "mod_a" for e in g.edges)


def test_expand_symbol_reveals_reference_neighbors_filtered(tmp_path):
    from pydocs_mcp.ask_your_docs import graph

    db = _expand_bundle(tmp_path)
    only_calls = graph.expand(db, "mod_a.Foo", "class", kinds=frozenset({"calls"}))
    kinds = {e.kind for e in only_calls.edges}
    assert kinds == {"calls"}
    targets = {e.target for e in only_calls.edges} | {e.source for e in only_calls.edges}
    assert "mod_b.bar" in targets  # callee via calls
    # inherits edge is filtered out
    assert all(e.kind != "inherits" for e in only_calls.edges)


def test_expand_caps_neighbors(tmp_path):
    from pydocs_mcp.ask_your_docs import graph

    from tests.ask_your_docs._fixture import make_bundle
    refs = [("hub.f", f"leaf.g{i}", "calls") for i in range(graph.MAX_NEIGHBORS + 5)]
    members = [("hub", "f", "def")] + [("leaf", f"g{i}", "def") for i in range(graph.MAX_NEIGHBORS + 5)]
    db = make_bundle(tmp_path / "demo_0123456789.db", members=members, refs=refs)
    g = graph.expand(db, "hub.f", "function", kinds=frozenset({"calls"}))
    assert g.truncated == 5
    assert len([e for e in g.edges]) == graph.MAX_NEIGHBORS
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/ask_your_docs/test_graph.py -k expand -v`
Expected: FAIL — `expand` not defined.

- [ ] **Step 3: Implement `expand` + a member-typing helper**

Append to `python/pydocs_mcp/ask_your_docs/graph.py`:

```python
def _member_types(conn: sqlite3.Connection) -> dict[str, str]:
    """node_id -> 'class' | 'function' for every own-code member."""
    out: dict[str, str] = {}
    for module, name, kind in conn.execute(
        "SELECT module, name, kind FROM module_members WHERE package=?", (_OWN,)
    ):
        out[f"{module}.{name}"] = "class" if kind == "class" else "function"
    return out


def expand(db_path: Path, node_id: str, node_type: str, kinds: frozenset[str]) -> Graph:
    """Neighbors of one node. Module → its members (contains); class/function →
    its reference neighbors, filtered to ``kinds``. Capped at ``MAX_NEIGHBORS``."""
    with closing(sqlite3.connect(_ro_uri(db_path), uri=True)) as conn:
        if node_type == "module":
            rows = conn.execute(
                "SELECT name, kind FROM module_members WHERE package=? AND module=? ORDER BY name",
                (_OWN, node_id),
            ).fetchall()
            nodes = tuple(
                Node(f"{node_id}.{name}", name, "class" if kind == "class" else "function")
                for name, kind in rows
            )
            edges = tuple(Edge(node_id, n.id, "contains") for n in nodes)
            return Graph(nodes, edges)

        member_types = _member_types(conn)
        rows = conn.execute(
            "SELECT from_node_id, to_node_id, kind FROM node_references "
            "WHERE from_package=? AND (from_node_id=? OR to_node_id=?)",
            (_OWN, node_id, node_id),
        ).fetchall()

    edges: list[Edge] = []
    nodes: dict[str, Node] = {}
    for from_id, to_id, kind in rows:
        if kind not in kinds or not to_id:
            continue
        other = to_id if from_id == node_id else from_id
        if other == node_id:
            continue
        edges.append(Edge(from_id, to_id, kind))
        nodes[other] = Node(other, _short(other), member_types.get(other, "function"))
        if len(edges) >= MAX_NEIGHBORS:
            break
    total = sum(1 for f, t, k in rows if k in kinds and t and (t if f == node_id else f) != node_id)
    return Graph(tuple(nodes.values()), tuple(edges), truncated=max(0, total - len(edges)))
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/ask_your_docs/test_graph.py -k expand -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/pydocs_mcp/ask_your_docs/graph.py && ruff format python/pydocs_mcp/ask_your_docs/graph.py
git add python/pydocs_mcp/ask_your_docs/graph.py tests/ask_your_docs/test_graph.py
git commit -m "feat(ask-your-docs): graph.expand — type-dependent neighbor expansion"
```

---

### Task 4: `node_meta()` + `induce()` (filter helper)

**Files:**
- Modify: `python/pydocs_mcp/ask_your_docs/graph.py`
- Test: `tests/ask_your_docs/test_graph.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/ask_your_docs/test_graph.py`:

```python
def test_node_meta_member_has_signature_and_docstring(tmp_path):
    from pydocs_mcp.ask_your_docs import graph
    from tests.ask_your_docs._fixture import make_bundle

    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("mod_a", "Foo", "class")],
        docstrings={"mod_a.Foo": "The Foo class."},
    )
    meta = graph.node_meta(db, "mod_a.Foo", "class")
    assert meta is not None and "Foo" in meta.title and "The Foo class." in meta.body


def test_induce_filters_by_node_type_and_edge_kind():
    from pydocs_mcp.ask_your_docs import graph

    g = graph.Graph(
        nodes=(graph.Node("m", "m", "module"), graph.Node("m.C", "C", "class"),
               graph.Node("m.f", "f", "function")),
        edges=(graph.Edge("m", "m.C", "contains"),
               graph.Edge("m.C", "m.f", "calls")),
    )
    out = graph.induce(g, node_types=frozenset({"module", "class"}),
                       edge_kinds=frozenset({"calls"}))
    assert {n.id for n in out.nodes} == {"m", "m.C"}          # function hidden
    # 'contains' kept (structural) between visible nodes; the calls edge touches
    # a hidden node (m.f) so it drops.
    assert {(e.source, e.target, e.kind) for e in out.edges} == {("m", "m.C", "contains")}
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/ask_your_docs/test_graph.py -k "node_meta or induce" -v`
Expected: FAIL — `node_meta` / `induce` not defined.

- [ ] **Step 3: Implement `node_meta` + `induce`**

Append to `python/pydocs_mcp/ask_your_docs/graph.py`:

```python
_STRUCTURAL = frozenset({"contains", "documents", "concerns"})


def node_meta(db_path: Path, node_id: str, node_type: str) -> NodeMeta | None:
    with closing(sqlite3.connect(_ro_uri(db_path), uri=True)) as conn:
        if node_type in {"class", "function"}:
            module, _, name = node_id.rpartition(".")
            row = conn.execute(
                "SELECT name, signature, docstring FROM module_members "
                "WHERE package=? AND module=? AND name=?",
                (_OWN, module, name),
            ).fetchone()
            if row is None:
                return None
            body = "\n\n".join(part for part in (row[1], row[2]) if part)
            return NodeMeta(node_id, node_type, row[0], body)
        if node_type == "module":
            members = conn.execute(
                "SELECT COUNT(*) FROM module_members WHERE package=? AND module=?",
                (_OWN, node_id),
            ).fetchone()[0]
            return NodeMeta(node_id, "module", node_id, f"{members} members")
    return None


def induce(g: Graph, node_types: frozenset[str], edge_kinds: frozenset[str]) -> Graph:
    """Keep nodes whose type is enabled; keep an edge when both endpoints survive
    AND (its kind is enabled OR it is structural)."""
    nodes = tuple(n for n in g.nodes if n.node_type in node_types)
    ids = {n.id for n in nodes}
    edges = tuple(
        e for e in g.edges
        if e.source in ids and e.target in ids
        and (e.kind in edge_kinds or e.kind in _STRUCTURAL)
    )
    return Graph(nodes, edges, g.truncated)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/ask_your_docs/test_graph.py -v`
Expected: PASS (all graph tests so far).

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/pydocs_mcp/ask_your_docs/graph.py && ruff format python/pydocs_mcp/ask_your_docs/graph.py
git add python/pydocs_mcp/ask_your_docs/graph.py tests/ask_your_docs/test_graph.py
git commit -m "feat(ask-your-docs): graph.node_meta + induce (filter) helpers"
```

---

### Task 5: Attachment weave helper

**Files:**
- Modify: `python/pydocs_mcp/ask_your_docs/agent.py`
- Test: `tests/ask_your_docs/test_attachment.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ask_your_docs/test_attachment.py`:

```python
def test_weave_prepends_deduped_context():
    from pydocs_mcp.ask_your_docs.agent import weave_attachments

    woven = weave_attachments(["a.b.C", "a.b.C", "d.e.f"], "how does it work?")
    assert woven == "Regarding `a.b.C`, `d.e.f`: how does it work?"


def test_weave_empty_is_identity():
    from pydocs_mcp.ask_your_docs.agent import weave_attachments

    assert weave_attachments([], "hi") == "hi"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/ask_your_docs/test_attachment.py -v`
Expected: FAIL — `weave_attachments` not defined. (This test imports `agent`, which needs the extra stack; run it in the example venv, or in the `test` env with the `[ask-your-docs]` extra installed.)

- [ ] **Step 3: Implement `weave_attachments`**

Add to `python/pydocs_mcp/ask_your_docs/agent.py` (near `scope_prefix`):

```python
def weave_attachments(attached: list[str], question: str) -> str:
    """Prepend de-duped attached symbols to a question as plain context text."""
    seen: dict[str, None] = {}
    for a in attached:
        if a:
            seen.setdefault(a, None)
    if not seen:
        return question
    names = ", ".join(f"`{a}`" for a in seen)
    return f"Regarding {names}: {question}"
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/ask_your_docs/test_attachment.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
ruff check python/pydocs_mcp/ask_your_docs/agent.py
git add python/pydocs_mcp/ask_your_docs/agent.py tests/ask_your_docs/test_attachment.py
git commit -m "feat(ask-your-docs): weave_attachments helper for graph→chat"
```

---

### Task 6: Packaging — add `streamlit-agraph` to the extra

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, under `[project.optional-dependencies]` `ask-your-docs = [ ... ]`, add the line:

```toml
    "streamlit-agraph>=0.0.45",   # interactive node-link graph + click-return
```

- [ ] **Step 2: Install into the example venv and verify import**

Run:
```bash
uv pip install --python examples/ask_your_docs_agent/.venv/bin/python "streamlit-agraph>=0.0.45"
examples/ask_your_docs_agent/.venv/bin/python -c "from streamlit_agraph import agraph, Node, Edge, Config; print('agraph ok')"
```
Expected: `agraph ok`. If the import fails, STOP — the "Open risk" fallback in the spec (`st.graphviz_chart` + selectbox) applies; escalate before proceeding.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build(ask-your-docs): add streamlit-agraph to the extra"
```

---

### Task 7: The graph page

**Files:**
- Create: `python/pydocs_mcp/ask_your_docs/pages/2_Graph.py`

- [ ] **Step 1: Write the page**

Create `python/pydocs_mcp/ask_your_docs/pages/2_Graph.py`:

```python
"""Graph explorer — second page of the ask-your-docs app.

Overview → click a node to expand its connections → read docstrings in the
panel. Filters: content type (Phase 1 fixed to Codebase), node type, edge kind.
"Add to question" pushes a node onto session_state.attached for the chat page.
"""

from __future__ import annotations

import os

import streamlit as st
from streamlit_agraph import Config, Edge as AEdge, Node as ANode, agraph

from pydocs_mcp.ask_your_docs import graph
from pydocs_mcp.ask_your_docs.catalog import workspace_catalog
from pydocs_mcp.ask_your_docs.theme import THEMES, theme_css

st.set_page_config(page_title="ask your docs — graph", page_icon="✦", layout="wide")
st.markdown(theme_css(THEMES["light" if st.session_state.get("light_mode") else "dark"]),
            unsafe_allow_html=True)

_TYPE_COLOR = {"module": "#34D3B7", "class": "#818CF8", "function": "#60A5FA",
               "doc": "#F0997B", "decision": "#EF9F27"}


@st.cache_data(ttl=60)
def _catalog(workspace: str) -> dict[str, list[str]]:
    return workspace_catalog(workspace)


def _db_for(workspace: str, project: str):
    from pathlib import Path
    for db in Path(workspace).expanduser().glob("*.db"):
        # first bundle whose catalog name matches; catalog already de-dups
        from pydocs_mcp.ask_your_docs.catalog import _project_name
        import sqlite3
        from contextlib import closing
        from pydocs_mcp.ask_your_docs.catalog import _ro_uri
        with closing(sqlite3.connect(_ro_uri(db), uri=True)) as conn:
            if _project_name(conn, db) == project:
                return db
    return None


workspace = os.environ.get("PYDOCS_WORKSPACE", "")
with st.sidebar:
    st.markdown('<div class="side-label">Workspace</div>', unsafe_allow_html=True)
    workspace = st.text_input("Workspace", workspace, key="graph_ws")
    projects = {}
    if workspace:
        try:
            projects = _catalog(workspace)
        except Exception as exc:  # unreadable dir / no bundles
            st.warning(f"Couldn't scan workspace: {exc}")
    project = st.selectbox("Project", list(projects) or ["—"], key="graph_project")

    st.markdown('<div class="side-label">Show</div>', unsafe_allow_html=True)
    node_types = frozenset(
        t for t in ("module", "class", "function")
        if st.checkbox(t, value=True, key=f"nt_{t}")
    )
    edge_kinds = frozenset(
        k for k in ("calls", "imports", "inherits")
        if st.checkbox(k, value=True, key=f"ek_{k}")
    )
    if st.button("Reset view", key="graph_reset"):
        st.session_state.pop("visible", None)

st.markdown('<div class="brand">graph <span class="accent">explorer</span></div>',
            unsafe_allow_html=True)

if not workspace or not projects:
    st.info("Set a workspace with pydocs-mcp bundles (same as the chat page).")
    st.stop()

db = _db_for(workspace, project)
if db is None:
    st.warning(f"No bundle found for project {project!r}.")
    st.stop()

# Seed / accumulate the visible set (session-scoped, per project).
key = f"visible::{project}"
if key not in st.session_state:
    st.session_state[key] = {n.id for n in graph.overview(db, project).nodes}
    if not st.session_state[key]:
        st.info("This bundle has no reference graph — enable "
                "`reference_graph.capture` and re-index.")
        st.stop()
visible: set[str] = st.session_state[key]

# Build the full graph from overview + everything expanded so far, then induce.
full = graph.overview(db, project)
known = {n.id: n for n in full.nodes}
edges = list(full.edges)
for nid in list(visible):
    ntype = known.get(nid).node_type if nid in known else "module"
    sub = graph.expand(db, nid, ntype, edge_kinds)
    for n in sub.nodes:
        known.setdefault(n.id, n)
    edges.extend(sub.edges)
combined = graph.Graph(tuple(known[i] for i in visible if i in known), tuple(edges))
shown = graph.induce(combined, node_types, edge_kinds)

anodes = [ANode(id=n.id, label=n.label, size=15,
                color=_TYPE_COLOR.get(n.node_type, "#8A97A6")) for n in shown.nodes]
aedges = [AEdge(source=e.source, target=e.target, label=e.kind) for e in shown.edges]
clicked = agraph(nodes=anodes, edges=aedges,
                 config=Config(width="100%", height=600, directed=True,
                               physics=True, collapsible=False))

if shown.truncated:
    st.caption(f"Showing {graph.MAX_NEIGHBORS} of {graph.MAX_NEIGHBORS + shown.truncated} neighbors.")

if clicked and clicked in known:
    ntype = known[clicked].node_type
    st.session_state[key] |= {n.id for n in graph.expand(db, clicked, ntype, edge_kinds).nodes}
    st.session_state[key].add(clicked)
    meta = graph.node_meta(db, clicked, ntype)
    with st.sidebar:
        st.markdown('<div class="side-label">Selected</div>', unsafe_allow_html=True)
        if meta:
            st.markdown(f"**{meta.title}**  \n`{meta.id}`")
            st.code(meta.body or "(no docstring)")
        if st.button("➕ Add to question", key="graph_attach"):
            att = st.session_state.setdefault("attached", [])
            if clicked not in att:
                att.append(clicked)
            st.toast(f"Attached {clicked}")
```

- [ ] **Step 2: Verify the page boots (headless smoke)**

Run:
```bash
lsof -ti tcp:8502 | xargs kill -9 2>/dev/null; sleep 1
PYDOCS_WORKSPACE="$HOME/pydocs-index" \
  examples/ask_your_docs_agent/.venv/bin/python -m streamlit run \
  python/pydocs_mcp/ask_your_docs/pages/2_Graph.py \
  --server.headless true --server.port 8502 &
for i in $(seq 1 20); do c=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8502); [ "$c" = 200 ] && break; sleep 1; done
echo "http=$c"; curl -s http://localhost:8502/_stcore/health
```
Expected: `http=200` and `ok`. Then kill it: `lsof -ti tcp:8502 | xargs kill -9`.
(Note: this runs the page standalone for a boot check. In the app it is reached via the sidebar page switcher — verified in Task 8.)

- [ ] **Step 3: Lint + commit**

```bash
ruff check python/pydocs_mcp/ask_your_docs/pages/2_Graph.py && ruff format python/pydocs_mcp/ask_your_docs/pages/2_Graph.py
git add python/pydocs_mcp/ask_your_docs/pages/2_Graph.py
git commit -m "feat(ask-your-docs): graph explorer page (overview + expand + filters)"
```

---

### Task 8: Wire attachments into the chat page

**Files:**
- Modify: `python/pydocs_mcp/ask_your_docs/app.py`

- [ ] **Step 1: Render chips above the input + weave on send**

In `app.py`, change the import line to include the weave helper:

```python
from pydocs_mcp.ask_your_docs.agent import ask, build_agent, reformulate, scope_prefix, weave_attachments
```

Immediately BEFORE the `if question := st.chat_input(...)` block, insert the chip row:

```python
attached = st.session_state.setdefault("attached", [])
if attached:
    st.caption("Attached from the graph:")
    cols = st.columns(len(attached) + 1)
    for i, sym in enumerate(list(attached)):
        if cols[i].button(f"✕ {sym.rsplit('.', 1)[-1]}", key=f"chip_{sym}"):
            attached.remove(sym)
            st.rerun()
    if cols[-1].button("clear all", key="chip_clear"):
        attached.clear()
        st.rerun()
```

Then change the send block so the woven question is used and attachments clear after send. Replace:

```python
        standalone = run(reformulate(llm, st.session_state.history, question))
        answer = run(ask(agent, st.session_state.history, standalone, scope=scope))
```

with:

```python
        woven = weave_attachments(attached, question)
        st.session_state.attached = []
        standalone = run(reformulate(llm, st.session_state.history, woven))
        answer = run(ask(agent, st.session_state.history, standalone, scope=scope))
```

- [ ] **Step 2: AppTest — chips render + weave + clear**

Add `tests/ask_your_docs/test_app_attachment.py`:

```python
import os


def test_attached_symbols_render_and_weave(monkeypatch):
    from streamlit.testing.v1 import AppTest
    import pydocs_mcp.ask_your_docs.app as appmod

    os.environ["PYDOCS_WORKSPACE"] = os.path.expanduser("~/pydocs-index")
    at = AppTest.from_file(appmod.__file__, default_timeout=120)
    at.session_state["attached"] = ["mod_a.Foo"]
    at.run()
    assert not at.exception
    # a chip button exists for the attached symbol
    assert any("Foo" in b.label for b in at.button)
```

Run: `pytest tests/ask_your_docs/test_app_attachment.py -v`
Expected: PASS (needs the extra stack installed; run in the example venv).

- [ ] **Step 3: Full-app smoke — page switcher shows both pages**

Run:
```bash
lsof -ti tcp:8501 | xargs kill -9 2>/dev/null; sleep 1
set -a && source /Users/msobroza/Projects/pyctx7-mcp/.env && set +a
PYDOCS_WORKSPACE="$HOME/pydocs-index" HF_HUB_OFFLINE=1 \
  examples/ask_your_docs_agent/.venv/bin/python -m pydocs_mcp.ask_your_docs \
  --workspace "$HOME/pydocs-index" --port 8501 -- --server.headless true &
for i in $(seq 1 20); do c=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8501); [ "$c" = 200 ] && break; sleep 1; done
echo "http=$c"
```
Expected: `http=200`. Streamlit auto-adds the "Graph" page to the sidebar because `pages/2_Graph.py` sits beside `app.py`.

- [ ] **Step 4: Lint + commit**

```bash
ruff check python/pydocs_mcp/ask_your_docs/app.py
git add python/pydocs_mcp/ask_your_docs/app.py tests/ask_your_docs/test_app_attachment.py
git commit -m "feat(ask-your-docs): attach graph nodes into the chat question"
```

---

### Task 9: Phase-1 docs + final verify

**Files:**
- Modify: `examples/ask_your_docs_agent/README.md`, `CLAUDE.md`

- [ ] **Step 1: Document the graph page**

In `examples/ask_your_docs_agent/README.md`, under the "## 2. Chat" section, add:

```markdown
### Graph explorer

The app has a second page (sidebar → **Graph**) that visualizes a project's
structure: a module overview, click a node to open its connections, docstrings
in a side panel, and filters for node type and relationship (calls / imports /
inherits). Click **➕ Add to question** on a node to attach it to your next chat
question.
```

In `CLAUDE.md`, in the `ask_your_docs/` architecture line, append: `+ a graph-explorer page (pages/2_Graph.py over graph.py, read-only)`.

- [ ] **Step 2: Full check — ruff, ruff format, graph tests**

Run:
```bash
ruff check python/pydocs_mcp/ask_your_docs/
ruff format --check python/pydocs_mcp/ask_your_docs/
pytest tests/ask_your_docs/test_graph.py -v
```
Expected: all clean / PASS.

- [ ] **Step 3: Commit**

```bash
git add examples/ask_your_docs_agent/README.md CLAUDE.md
git commit -m "docs(ask-your-docs): document the graph explorer page"
```

---

# Phase 2 — documentation + decision content types

### Task 10: `doc_nodes()` — markdown files + `documents` edges

**Files:**
- Modify: `python/pydocs_mcp/ask_your_docs/graph.py`
- Test: `tests/ask_your_docs/test_graph.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/ask_your_docs/test_graph.py`:

```python
def test_doc_nodes_one_per_file_attached_to_project(tmp_path):
    from pydocs_mcp.ask_your_docs import graph
    from tests.ask_your_docs._fixture import make_bundle

    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        markdown=[("README.md", "Intro", "hello"),
                  ("README.md", "Usage", "run it"),
                  ("CLAUDE.md", "Rules", "be nice")],
    )
    g = graph.doc_nodes(db, "demo")
    files = {n.id: n.node_type for n in g.nodes}
    assert files == {"doc:README.md": "doc", "doc:CLAUDE.md": "doc",
                     "project:demo": "module"}
    assert all(e.kind == "documents" and e.target == "project:demo" for e in g.edges)


def test_expand_doc_file_reveals_sections(tmp_path):
    from pydocs_mcp.ask_your_docs import graph
    from tests.ask_your_docs._fixture import make_bundle

    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        markdown=[("README.md", "Intro", "hello"), ("README.md", "Usage", "run it")],
    )
    g = graph.expand(db, "doc:README.md", "doc", kinds=frozenset())
    labels = sorted(n.label for n in g.nodes)
    assert labels == ["Intro", "Usage"]
    assert all(e.kind == "contains" for e in g.edges)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/ask_your_docs/test_graph.py -k "doc_nodes or doc_file" -v`
Expected: FAIL — `doc_nodes` not defined / `expand` doesn't handle `doc`.

- [ ] **Step 3: Implement `doc_nodes` + extend `expand` for `doc`**

Append to `graph.py`:

```python
def doc_nodes(db_path: Path, project: str) -> Graph:
    """One node per markdown file, each linked to the project by a ``documents``
    edge (markdown files document the project, not a single code module)."""
    with closing(sqlite3.connect(_ro_uri(db_path), uri=True)) as conn:
        files = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT module FROM chunks WHERE package=? AND origin='markdown_section' "
                "ORDER BY module",
                (_OWN,),
            )
        ]
    project_node = Node(f"project:{project}", project, "module")
    nodes = [project_node] + [Node(f"doc:{f}", f, "doc") for f in files]
    edges = [Edge(f"doc:{f}", project_node.id, "documents") for f in files]
    return Graph(tuple(nodes), tuple(edges))
```

In `expand`, add a `doc` branch at the top of the function body (before the `module` branch):

```python
        if node_type == "doc":
            file = node_id.removeprefix("doc:")
            rows = conn.execute(
                "SELECT id, title FROM chunks WHERE package=? AND origin='markdown_section' "
                "AND module=? ORDER BY id",
                (_OWN, file),
            ).fetchall()
            nodes = tuple(Node(f"section:{cid}", title, "doc") for cid, title in rows)
            edges = tuple(Edge(node_id, n.id, "contains") for n in nodes)
            return Graph(nodes, edges)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/ask_your_docs/test_graph.py -k "doc" -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/pydocs_mcp/ask_your_docs/graph.py && ruff format python/pydocs_mcp/ask_your_docs/graph.py
git add python/pydocs_mcp/ask_your_docs/graph.py tests/ask_your_docs/test_graph.py
git commit -m "feat(ask-your-docs): graph.doc_nodes — markdown files as doc nodes"
```

---

### Task 11: `decision_nodes()` — architectural decisions + `concerns` edges

**Files:**
- Modify: `python/pydocs_mcp/ask_your_docs/graph.py`
- Test: `tests/ask_your_docs/test_graph.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/ask_your_docs/test_graph.py`:

```python
def test_decision_nodes_grouped_under_project(tmp_path):
    from pydocs_mcp.ask_your_docs import graph
    from tests.ask_your_docs._fixture import make_bundle

    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        decisions=[("Use RRF fusion", "We chose RRF because ..."),
                   ("SQLite over DuckDB", "Simplicity ...")],
    )
    g = graph.decision_nodes(db, "demo")
    types = {n.node_type for n in g.nodes if n.id.startswith("decision:")}
    assert types == {"decision"}
    assert len([n for n in g.nodes if n.node_type == "decision"]) == 2
    assert all(e.kind == "concerns" for e in g.edges)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/ask_your_docs/test_graph.py -k decision -v`
Expected: FAIL — `decision_nodes` not defined.

- [ ] **Step 3: Implement `decision_nodes`**

Append to `graph.py`:

```python
def decision_nodes(db_path: Path, project: str) -> Graph:
    """One node per decision record, grouped under the project via ``concerns``.

    (Symbol-level backlinks are not present in current bundles, so decisions
    attach to the project node; refine to per-symbol edges when available.)"""
    with closing(sqlite3.connect(_ro_uri(db_path), uri=True)) as conn:
        rows = conn.execute(
            "SELECT id, title FROM chunks WHERE package=? AND origin='decision_record' ORDER BY id",
            (_OWN,),
        ).fetchall()
    project_node = Node(f"project:{project}", project, "module")
    nodes = [project_node] + [Node(f"decision:{cid}", title, "decision") for cid, title in rows]
    edges = [Edge(f"decision:{cid}", project_node.id, "concerns") for cid, _ in rows]
    return Graph(tuple(nodes), tuple(edges))
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/ask_your_docs/test_graph.py -k decision -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/pydocs_mcp/ask_your_docs/graph.py && ruff format python/pydocs_mcp/ask_your_docs/graph.py
git add python/pydocs_mcp/ask_your_docs/graph.py tests/ask_your_docs/test_graph.py
git commit -m "feat(ask-your-docs): graph.decision_nodes — decisions as graph nodes"
```

---

### Task 12: Content-type selector on the page + doc/decision panel

**Files:**
- Modify: `python/pydocs_mcp/ask_your_docs/graph.py` (extend `node_meta` for doc/decision + section)
- Modify: `python/pydocs_mcp/ask_your_docs/pages/2_Graph.py`

- [ ] **Step 1: Extend `node_meta` for doc / decision / section**

In `graph.py` `node_meta`, before the final `return None`, add:

```python
        if node_type in {"doc", "decision"}:
            if node_id.startswith("section:") or node_id.startswith("decision:"):
                cid = int(node_id.split(":", 1)[1])
                row = conn.execute(
                    "SELECT title, text FROM chunks WHERE id=?", (cid,)
                ).fetchone()
                if row:
                    return NodeMeta(node_id, node_type, row[0], row[1] or "")
            if node_id.startswith("doc:"):
                return NodeMeta(node_id, "doc", node_id.removeprefix("doc:"), "Markdown file")
```

- [ ] **Step 2: Add the content-type selector + seeding in the page**

In `pages/2_Graph.py`, add a selector in the sidebar (just below the Project selectbox):

```python
    content = st.radio("Content", ["Codebase", "Documentation", "Documentation + codebase"],
                       key="graph_content")
```

Replace the seed block so the seed depends on content type:

```python
key = f"visible::{project}::{content}"
if key not in st.session_state:
    seed = set()
    if content != "Documentation":
        seed |= {n.id for n in graph.overview(db, project).nodes}
    if content != "Codebase":
        seed |= {n.id for n in graph.doc_nodes(db, project).nodes}
        seed |= {n.id for n in graph.decision_nodes(db, project).nodes}
    st.session_state[key] = seed
    if not seed:
        st.info("Nothing to show for this content type in this bundle.")
        st.stop()
visible = st.session_state[key]
```

And extend `node_types` and the `full`/expand accumulation to include `doc`/`decision` categories when the content type enables them:

```python
    if content != "Codebase":
        node_types = node_types | frozenset({"doc", "decision"})
```

Build `full` from all enabled category seeds (overview + doc_nodes + decision_nodes as applicable) instead of overview alone:

```python
full_nodes: dict[str, graph.Node] = {}
full_edges: list[graph.Edge] = []
for src in ([graph.overview(db, project)] if content != "Documentation" else []) + \
           ([graph.doc_nodes(db, project), graph.decision_nodes(db, project)]
            if content != "Codebase" else []):
    for n in src.nodes:
        full_nodes.setdefault(n.id, n)
    full_edges.extend(src.edges)
known = dict(full_nodes)
edges = list(full_edges)
```

(Then the existing per-visible `expand` accumulation and `induce`/render follow unchanged, using `known`/`edges`.)

- [ ] **Step 3: Verify against the demo bundle (markdown path)**

Run the page smoke from Task 7 Step 2 again, then confirm markdown appears:
```bash
examples/ask_your_docs_agent/.venv/bin/python - <<'PY'
from pydocs_mcp.ask_your_docs import graph
from pathlib import Path
db = next(Path("~/.pydocs-mcp").expanduser().glob("example_needle_*.db"))
g = graph.doc_nodes(db, "example_needle")
print("doc files:", sum(1 for n in g.nodes if n.node_type == "doc"))
PY
```
Expected: a non-zero count (example_needle has 356 markdown sections across several files).

- [ ] **Step 4: Lint + commit**

```bash
ruff check python/pydocs_mcp/ask_your_docs/graph.py python/pydocs_mcp/ask_your_docs/pages/2_Graph.py
ruff format python/pydocs_mcp/ask_your_docs/graph.py python/pydocs_mcp/ask_your_docs/pages/2_Graph.py
git add python/pydocs_mcp/ask_your_docs/graph.py python/pydocs_mcp/ask_your_docs/pages/2_Graph.py
git commit -m "feat(ask-your-docs): content-type selector (codebase / docs / both) on the graph page"
```

---

### Task 13: Verify decisions against a real index + final docs

**Files:**
- Modify: `examples/ask_your_docs_agent/README.md`

- [ ] **Step 1: Index this repo (it captures decisions) and confirm decision nodes**

Run:
```bash
examples/ask_your_docs_agent/.venv/bin/python -m pydocs_mcp index . --cache-dir /tmp/graph-decisions --skip-deps
examples/ask_your_docs_agent/.venv/bin/python - <<'PY'
from pydocs_mcp.ask_your_docs import graph
from pathlib import Path
db = next(Path("/tmp/graph-decisions").glob("*.db"))
g = graph.decision_nodes(db, db.stem.rsplit("_", 1)[0])
print("decision nodes:", sum(1 for n in g.nodes if n.node_type == "decision"))
PY
```
Expected: a non-zero decision count (this repo records design decisions). If zero, note that decision capture may be disabled in the default config; that is a data condition, not a code bug — the query is unit-tested in Task 11.

- [ ] **Step 2: Document the content types**

In `examples/ask_your_docs_agent/README.md`, extend the "### Graph explorer" section:

```markdown
Use the **Content** selector to switch between **Codebase** (modules, classes,
functions), **Documentation** (markdown files and architectural decisions), or
both — with `documents` / `concerns` links from docs and decisions to the code.
```

- [ ] **Step 3: Final full verify + commit**

```bash
ruff check python/pydocs_mcp/ask_your_docs/ && ruff format --check python/pydocs_mcp/ask_your_docs/
pytest tests/ask_your_docs/ -v
git add examples/ask_your_docs_agent/README.md
git commit -m "docs(ask-your-docs): document graph content types; verify decisions"
```

---

## Notes for the implementer

- **Read-only is sacred.** Every `graph.py` connection uses `_ro_uri` (`file:...?mode=ro`) and never imports `pydocs_mcp.multirepo` / `open_index_database`. The `user_version`-unchanged test in Task 2 guards this — keep it green.
- **`streamlit-agraph` click model:** `agraph(...)` returns the clicked node id (or `None`) on the next rerun. Expansion is driven by that return value; the `visible` set in `session_state` accumulates across reruns. If the component returns something falsy unexpectedly, check its version against Task 6's floor before debugging deeper.
- **Commits:** this plan commits per task. The repo's authorship policy is msobroza-only, no `Co-Authored-By` trailers — do not add any.
- **Git:** confirm you are on a feature branch, not `main`, before the first commit.
