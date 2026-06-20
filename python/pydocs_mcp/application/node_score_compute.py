"""Index-time node-score computation over the reference graph.

Pure helper: given the resolved directed edges of the reference graph and the
``{qualified_name: package}`` map of indexed symbols, returns a ``NodeScore``
per indexed symbol — in-degree (resolved edges pointing at it), PageRank
(global importance), and Louvain community id.

``networkx`` is an optional ``[graph]`` extra imported lazily (the same
slot/guard pattern as the fast-plaid extra), so default installs never pay for
it and a missing extra raises an actionable :class:`ImportError`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from pydocs_mcp.storage.node_score import NodeScore

log = logging.getLogger(__name__)

# Lazy-import slots (monkeypatchable in tests) — keep networkx off the
# module-load path so the default install (no [graph] extra) never imports it.
_nx: Any = None
_NX_IMPORT_ERROR: Exception | None = None

_INSTALL_HINT = (
    "Node-score computation (PageRank / community detection) requires the "
    "'graph' extra. Install with: pip install 'pydocs-mcp[graph]' (pulls "
    "networkx, pure-Python)."
)

# Louvain is randomized — pin a seed so community ids are reproducible across
# reindexes (else the community-diversity reranker's behaviour drifts).
_LOUVAIN_SEED = 42
# PageRank damping — networkx default.
_PAGERANK_ALPHA = 0.85


def _ensure_networkx() -> Any:
    """Resolve ``networkx`` on first call; raise an actionable ImportError if
    the optional ``[graph]`` extra isn't installed. Idempotent."""
    global _nx, _NX_IMPORT_ERROR
    if _nx is not None:
        return _nx
    if _NX_IMPORT_ERROR is not None:
        raise ImportError(_INSTALL_HINT) from _NX_IMPORT_ERROR
    try:
        import networkx as nx  # type: ignore[import-untyped]
    except ImportError as e:  # pragma: no cover - exercised when extra is missing
        _NX_IMPORT_ERROR = e
        raise ImportError(_INSTALL_HINT) from e
    _nx = nx
    return nx


def compute_scores(
    edges: Iterable[tuple[str, str]],
    qname_packages: Mapping[str, str],
) -> list[NodeScore]:
    """Return one :class:`NodeScore` per indexed symbol in ``qname_packages``.

    ``edges`` are resolved directed ``(from_qname, to_qname)`` pairs (the whole
    cross-package graph). PageRank + Louvain run over that graph; in-degree
    counts edges pointing at each node. Scores are emitted ONLY for symbols that
    have a chunk (``qname_packages`` keys) — the rerank steps join candidate
    chunks' ``qualified_name`` against this table, so scoring graph-only nodes
    (with no chunk) would never be read. Symbols absent from the graph get a
    neutral score (in_degree 0, pagerank 0.0, community -1).
    """
    edge_list = [(a, b) for a, b in edges if a and b]
    in_degree: dict[str, int] = {}
    for _src, dst in edge_list:
        in_degree[dst] = in_degree.get(dst, 0) + 1

    pagerank: dict[str, float] = {}
    community_of: dict[str, int] = {}
    if edge_list:
        nx = _ensure_networkx()
        digraph = nx.DiGraph()
        digraph.add_edges_from(edge_list)
        pagerank = nx.pagerank(digraph, alpha=_PAGERANK_ALPHA)
        communities = nx.community.louvain_communities(digraph.to_undirected(), seed=_LOUVAIN_SEED)
        community_of = {qn: idx for idx, comm in enumerate(communities) for qn in comm}

    return [
        NodeScore(
            package=package,
            qualified_name=qname,
            in_degree=in_degree.get(qname, 0),
            pagerank=pagerank.get(qname, 0.0),
            community=community_of.get(qname, -1),
        )
        for qname, package in qname_packages.items()
    ]
