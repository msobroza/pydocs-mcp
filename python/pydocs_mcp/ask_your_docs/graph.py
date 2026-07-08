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
        is_prefix = node_id == m or node_id.startswith(m + ".")
        if is_prefix and (best is None or len(m) > len(best)):
            best = m
    return best


def overview(db_path: Path, project: str) -> Graph:
    """The project's own modules as nodes + aggregated module->module edges.

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


def _member_types(conn: sqlite3.Connection) -> dict[str, str]:
    """node_id -> 'class' | 'function' for every own-code member."""
    out: dict[str, str] = {}
    for module, name, kind in conn.execute(
        "SELECT module, name, kind FROM module_members WHERE package=?", (_OWN,)
    ):
        out[f"{module}.{name}"] = "class" if kind == "class" else "function"
    return out


def expand(db_path: Path, node_id: str, node_type: str, kinds: frozenset[str]) -> Graph:
    """Neighbors of one node. Module -> its members (contains); class/function ->
    its reference neighbors, filtered to ``kinds``. Capped at ``MAX_NEIGHBORS``."""
    with closing(sqlite3.connect(_ro_uri(db_path), uri=True)) as conn:
        if node_type == "module":
            rows = conn.execute(
                "SELECT name, kind FROM module_members WHERE package=? AND module=? ORDER BY name",
                (_OWN, node_id),
            ).fetchall()
            member_nodes = tuple(
                Node(f"{node_id}.{name}", name, "class" if kind == "class" else "function")
                for name, kind in rows
            )
            contains = tuple(Edge(node_id, n.id, "contains") for n in member_nodes)
            return Graph(member_nodes, contains)

        member_types = _member_types(conn)
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
        nodes[other] = Node(other, _short(other), member_types.get(other, "function"))
    return Graph(tuple(nodes.values()), tuple(edges), truncated=max(0, total - len(edges)))


_STRUCTURAL = frozenset({"contains", "documents", "concerns"})


def node_meta(db_path: Path, node_id: str, node_type: str) -> NodeMeta | None:
    """Detail card for one node: name + signature + docstring for a class/function,
    path + member count for a module, else ``None``."""
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
        e
        for e in g.edges
        if e.source in ids and e.target in ids and (e.kind in edge_kinds or e.kind in _STRUCTURAL)
    )
    return Graph(nodes, edges, g.truncated)
