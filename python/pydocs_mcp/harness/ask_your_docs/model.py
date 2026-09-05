"""Value objects for the graph explorer — pure data, no I/O, no framework."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    label: str
    node_type: str  # package | module | class | function | doc | decision


@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    target: str
    kind: str  # calls | imports | inherits | contains | documents | concerns


@dataclass(frozen=True, slots=True)
class Graph:
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()
    truncated: int = 0  # neighbours dropped by a cap


@dataclass(frozen=True, slots=True)
class NodeMeta:
    id: str
    node_type: str
    title: str
    body: str
