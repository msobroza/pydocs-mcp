"""Read-only queries over a pydocs bundle, shaped into small graph values.

Mirrors ``catalog.py``: every connection is ``mode=ro`` and never routes through
``pydocs_mcp.multirepo.open_index_database`` (which opens read-write and can
migrate/rebuild a bundle). Stdlib-only so it imports without the agent stack.

Topology comes from ``node_references`` (self-consistent import-path node ids).
``module_members`` stores filesystem-derived module paths (e.g. ``src.pkg.mod``,
plus worktree copies) that do NOT match the reference-graph ids, so module
identity is reconciled by normalizing each ``module_members.module`` to its
longest suffix that exists in the reference-graph's node-id space.
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
    truncated: int = 0


@dataclass(frozen=True, slots=True)
class NodeMeta:
    id: str
    node_type: str
    title: str
    body: str


_STRUCTURAL = frozenset({"contains", "documents", "concerns"})


def _short(node_id: str) -> str:
    return node_id.rsplit(".", 1)[-1] or node_id


def _node_ids(conn: sqlite3.Connection) -> set[str]:
    """Every own-code symbol id referenced (either end) in the graph."""
    ids: set[str] = set()
    for a, b in conn.execute(
        "SELECT from_node_id, to_node_id FROM node_references WHERE from_package=?",
        (_OWN,),
    ):
        if a:
            ids.add(a)
        if b:
            ids.add(b)
    return ids


def _prefixes(node_ids: set[str]) -> set[str]:
    """Every dotted prefix of every node id (the import-path module space)."""
    out: set[str] = set()
    for nid in node_ids:
        segs = nid.split(".")
        for i in range(1, len(segs) + 1):
            out.add(".".join(segs[:i]))
    return out


def _normalize(raw_module: str, prefixes: set[str]) -> str | None:
    """The longest suffix of a filesystem-derived module that is an import path."""
    segs = raw_module.split(".")
    for i in range(len(segs)):
        cand = ".".join(segs[i:])
        if cand in prefixes:
            return cand
    return None


def _modules(conn: sqlite3.Connection) -> list[str]:
    """Own modules, in import-path form (reconciled with the reference graph)."""
    prefixes = _prefixes(_node_ids(conn))
    mods = {
        norm
        for (raw,) in conn.execute(
            "SELECT DISTINCT module FROM module_members WHERE package=?", (_OWN,)
        )
        if (norm := _normalize(raw, prefixes))
    }
    return sorted(mods)


def _module_of(node_id: str | None, modules: list[str]) -> str | None:
    """Longest module that is the node id or a dotted prefix of it."""
    if not node_id:
        return None
    best: str | None = None
    for m in modules:
        is_prefix = node_id == m or node_id.startswith(m + ".")
        if is_prefix and (best is None or len(m) > len(best)):
            best = m
    return best


def _type_of(node_id: str, modules: set[str]) -> str:
    """module if a known module, else class/function by the Capitalized-name rule."""
    if node_id in modules:
        return "module"
    return "class" if _short(node_id)[:1].isupper() else "function"


def _defined_members(
    conn: sqlite3.Connection, module: str, node_ids: set[str]
) -> list[tuple[str, str]]:
    """(name, kind) for every class/def DEFINED in ``module``.

    Reads ``module_members`` (the authoritative definition list) and reconciles
    each fs-derived module path to the import-path ``module``, so ALL defined
    classes/functions show — not just the ones that happen to be referenced in
    the call graph. Deduped by name across worktree-copy module rows.
    """
    prefixes = _prefixes(node_ids)
    seen: dict[str, str] = {}
    for raw, name, kind in conn.execute(
        "SELECT module, name, kind FROM module_members WHERE package=?", (_OWN,)
    ):
        if _normalize(raw, prefixes) == module:
            seen.setdefault(name, kind)
    return sorted(seen.items())


def overview(db_path: Path, project: str) -> Graph:
    """The project's own modules as nodes + aggregated module->module edges."""
    with closing(sqlite3.connect(_ro_uri(db_path), uri=True)) as conn:
        modules = _modules(conn)
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


def expand(db_path: Path, node_id: str, node_type: str, kinds: frozenset[str]) -> Graph:
    """Neighbors of one node. Module -> its members (contains); class/function ->
    its reference neighbors, filtered to ``kinds``. Capped at ``MAX_NEIGHBORS``."""
    with closing(sqlite3.connect(_ro_uri(db_path), uri=True)) as conn:
        modules = set(_modules(conn))
        node_ids = _node_ids(conn)
        if node_type == "doc":
            file = node_id.removeprefix("doc:")
            section_rows = conn.execute(
                "SELECT id, title FROM chunks WHERE package=? AND origin='markdown_section' "
                "AND module=? ORDER BY id",
                (_OWN, file),
            ).fetchall()
            section_nodes = tuple(
                Node(f"section:{cid}", title, "doc") for cid, title in section_rows
            )
            contains = tuple(Edge(node_id, n.id, "contains") for n in section_nodes)
            return Graph(section_nodes, contains)
        if node_type == "module":
            members = _defined_members(conn, node_id, node_ids)
            member_nodes = tuple(
                Node(f"{node_id}.{name}", name, "class" if kind == "class" else "function")
                for name, kind in members
            )
            contains = tuple(Edge(node_id, n.id, "contains") for n in member_nodes)
            return Graph(member_nodes, contains)

        rows = conn.execute(
            "SELECT from_node_id, to_node_id, kind FROM node_references "
            "WHERE from_package=? AND (from_node_id=? OR to_node_id=?)",
            (_OWN, node_id, node_id),
        ).fetchall()

    edges: list[Edge] = []
    nodes: dict[str, Node] = {}
    total = 0
    for from_id, to_id, kind in rows:
        if kind not in kinds or not to_id:
            continue
        other = to_id if from_id == node_id else from_id
        if other == node_id:
            continue
        total += 1
        if len(edges) >= MAX_NEIGHBORS:
            continue
        edges.append(Edge(from_id, to_id, kind))
        nodes[other] = Node(other, _short(other), _type_of(other, modules))
    return Graph(tuple(nodes.values()), tuple(edges), truncated=max(0, total - len(edges)))


def node_meta(db_path: Path, node_id: str, node_type: str) -> NodeMeta | None:
    with closing(sqlite3.connect(_ro_uri(db_path), uri=True)) as conn:
        if node_type in {"class", "function"}:
            module_part, _, name = node_id.rpartition(".")
            row = conn.execute(
                "SELECT name, signature, docstring FROM module_members "
                "WHERE package=? AND name=? AND (module=? OR module LIKE ?)",
                (_OWN, name, module_part, f"%.{module_part}"),
            ).fetchone()
            if row is None:
                return NodeMeta(node_id, node_type, name or node_id, "")
            body = "\n\n".join(part for part in (row[1], row[2]) if part)
            return NodeMeta(node_id, node_type, row[0], body)
        if node_type == "module":
            count = len(_defined_members(conn, node_id, _node_ids(conn)))
            return NodeMeta(node_id, "module", node_id, f"{count} members")
        if node_type == "package":
            n = sum(1 for m in _modules(conn) if m == node_id or m.startswith(node_id + "."))
            return NodeMeta(node_id, "package", node_id, f"{n} modules")
        if node_type in {"doc", "decision"}:
            if node_id.startswith(("section:", "decision:")):
                cid = int(node_id.split(":", 1)[1])
                row = conn.execute("SELECT title, text FROM chunks WHERE id=?", (cid,)).fetchone()
                if row:
                    return NodeMeta(node_id, node_type, row[0], row[1] or "")
            if node_id.startswith("doc:"):
                return NodeMeta(node_id, "doc", node_id.removeprefix("doc:"), "Markdown file")
    return None


def induce(g: Graph, node_types: frozenset[str], edge_kinds: frozenset[str]) -> Graph:
    """Keep nodes whose type is enabled; keep an edge when both endpoints survive
    AND (its kind is enabled OR it is structural)."""
    nodes = tuple(n for n in g.nodes if n.node_type in node_types)
    ids = {n.id for n in nodes}
    edges = tuple(
        e
        for e in g.edges
        if e.source in ids and e.target in ids and (e.kind in edge_kinds or e.kind in _STRUCTURAL)
    )
    return Graph(nodes, edges, g.truncated)


def doc_nodes(db_path: Path, project: str) -> Graph:
    """One node per markdown file, each linked to the project by a ``documents``
    edge (markdown files document the project, not a single code module)."""
    with closing(sqlite3.connect(_ro_uri(db_path), uri=True)) as conn:
        files = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT module FROM chunks "
                "WHERE package=? AND origin='markdown_section' ORDER BY module",
                (_OWN,),
            )
        ]
    project_node = Node(f"project:{project}", project, "module")
    nodes = [project_node] + [Node(f"doc:{f}", f, "doc") for f in files]
    edges = [Edge(f"doc:{f}", project_node.id, "documents") for f in files]
    return Graph(tuple(nodes), tuple(edges))


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


def modules(db_path: Path) -> set[str]:
    """The project's own modules in import-path form (for the page's typing)."""
    with closing(sqlite3.connect(_ro_uri(db_path), uri=True)) as conn:
        return set(_modules(conn))


def type_of(node_id: str, module_set: set[str]) -> str:
    """Node category from the id alone (so the page can expand without a lookup)."""
    if node_id.startswith(("doc:", "section:")):
        return "doc"
    if node_id.startswith("decision:"):
        return "decision"
    if node_id.startswith("project:"):
        return "module"
    return _type_of(node_id, module_set)


def edges_for(db_path: Path, visible: set[str], kinds: frozenset[str]) -> tuple[Edge, ...]:
    """Reference edges among the visible nodes, each raw edge collapsed to the
    nearest visible *ancestor* (the longest visible dotted prefix of an endpoint).

    So a member's ``calls``/``inherits``/``imports`` edge is drawn between whatever
    two visible nodes contain its endpoints — package↔package at a coarse zoom,
    member↔member when zoomed into a module. Endpoints with no visible ancestor
    (external targets, or nodes outside the current view) are dropped.
    """
    with closing(sqlite3.connect(_ro_uri(db_path), uri=True)) as conn:
        rows = conn.execute(
            "SELECT from_node_id, to_node_id, kind FROM node_references WHERE from_package=?",
            (_OWN,),
        ).fetchall()

    vis = sorted(visible, key=len, reverse=True)  # longest prefix wins

    def anchor(nid: str | None) -> str | None:
        if not nid:
            return None
        for v in vis:
            if nid == v or nid.startswith(v + "."):
                return v
        return None

    seen: set[tuple[str, str, str]] = set()
    out: list[Edge] = []
    for from_id, to_id, kind in rows:
        if kind not in kinds:
            continue
        a = anchor(from_id)
        b = anchor(to_id)
        if a and b and a != b and (a, b, kind) not in seen:
            seen.add((a, b, kind))
            out.append(Edge(a, b, kind))
    return tuple(out)


_TEST_SEGMENTS = frozenset({"tests", "test", "conftest"})


def is_test(node_id: str) -> bool:
    """True for test modules/files (a ``test``/``tests`` package, ``test_*`` or
    ``*_test`` module, or ``conftest``) so the UI can filter them out."""
    for seg in node_id.split("."):
        if seg in _TEST_SEGMENTS or seg.startswith("test_") or seg.endswith("_test"):
            return True
    return False


def _namespace_children(mods: set[str], focus: str, keep) -> list[Node]:
    """The next dotted segment under ``focus``: a module if that exact prefix is
    a module, else a package (it has modules deeper still)."""
    prefix = focus + "." if focus else ""
    segs: dict[str, str] = {}
    for m in mods:
        if not m.startswith(prefix) or not keep(m):
            continue
        rest = m[len(prefix) :]
        if not rest:
            continue
        child = prefix + rest.split(".", 1)[0]
        segs[child] = "module" if child in mods else "package"
    return [Node(cid, _short(cid), kind) for cid, kind in sorted(segs.items())]


def _doc_decision_children(conn: sqlite3.Connection) -> list[Node]:
    docs = [
        Node(f"doc:{f}", f, "doc")
        for (f,) in conn.execute(
            "SELECT DISTINCT module FROM chunks "
            "WHERE package=? AND origin='markdown_section' ORDER BY module",
            (_OWN,),
        )
    ]
    decisions = [
        Node(f"decision:{cid}", title, "decision")
        for cid, title in conn.execute(
            "SELECT id, title FROM chunks WHERE package=? AND origin='decision_record' ORDER BY id",
            (_OWN,),
        )
    ]
    return docs + decisions


def children(
    db_path: Path, focus: str, content: str = "Codebase", hide_tests: bool = True
) -> tuple[Node, ...]:
    """Direct namespace children of ``focus`` for the zoom view.

    Levels: root ("") -> top packages/modules; package -> sub-packages/modules;
    module -> its classes/functions; class -> its methods. ``content`` adds the
    project's markdown files + decision records at the root. Functions, methods,
    decisions and doc sections are leaves (return no children).
    """

    def keep(nid: str) -> bool:
        return not (hide_tests and is_test(nid))

    if focus.startswith("doc:"):
        return expand(db_path, focus, "doc", frozenset()).nodes

    with closing(sqlite3.connect(_ro_uri(db_path), uri=True)) as conn:
        mods = set(_modules(conn))
        node_ids = _node_ids(conn)

        if focus == "":
            nodes: list[Node] = []
            if content != "Documentation":
                nodes += _namespace_children(mods, "", keep)
            if content != "Codebase":
                nodes += _doc_decision_children(conn)
            return tuple(nodes)

        if focus in mods:  # module -> defined members
            members = _defined_members(conn, focus, node_ids)
            return tuple(
                Node(f"{focus}.{name}", name, "class" if kind == "class" else "function")
                for name, kind in members
                if keep(f"{focus}.{name}")
            )

        if any(m.startswith(focus + ".") for m in mods):  # package prefix
            return tuple(_namespace_children(mods, focus, keep))

        # class (or leaf) -> its methods (ids one segment beyond ``focus``)
        methods = sorted(
            nid
            for nid in node_ids
            if nid.startswith(focus + ".") and "." not in nid[len(focus) + 1 :] and keep(nid)
        )
        return tuple(Node(mid, _short(mid), "function") for mid in methods)
