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
