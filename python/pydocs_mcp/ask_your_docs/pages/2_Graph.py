"""Graph explorer — second page of the ask-your-docs app.

Overview -> click a node to expand its connections -> read docstrings in the
panel. Filters: content type (Phase 1 fixed to Codebase), node type, edge kind.
"Add to question" pushes a node onto session_state.attached for the chat page.
"""

from __future__ import annotations

import os

import streamlit as st
from pydocs_mcp.ask_your_docs import graph
from pydocs_mcp.ask_your_docs.catalog import workspace_catalog
from pydocs_mcp.ask_your_docs.theme import THEMES, theme_css
from streamlit_agraph import Config, agraph
from streamlit_agraph import Edge as AEdge
from streamlit_agraph import Node as ANode

st.set_page_config(page_title="ask your docs — graph", page_icon="✦", layout="wide")
st.markdown(
    theme_css(THEMES["light" if st.session_state.get("light_mode") else "dark"]),
    unsafe_allow_html=True,
)

_TYPE_COLOR = {
    "module": "#34D3B7",
    "class": "#818CF8",
    "function": "#60A5FA",
    "doc": "#F0997B",
    "decision": "#EF9F27",
}


@st.cache_data(ttl=60)
def _catalog(workspace: str) -> dict[str, list[str]]:
    return workspace_catalog(workspace)


def _db_for(workspace: str, project: str):
    import sqlite3
    from contextlib import closing
    from pathlib import Path

    from pydocs_mcp.ask_your_docs.catalog import _project_name, _ro_uri

    for db in sorted(Path(workspace).expanduser().glob("*.db")):
        with closing(sqlite3.connect(_ro_uri(db), uri=True)) as conn:
            if _project_name(conn, db) == project:
                return db
    return None


workspace = os.environ.get("PYDOCS_WORKSPACE", "")
with st.sidebar:
    st.markdown('<div class="side-label">Workspace</div>', unsafe_allow_html=True)
    workspace = st.text_input("Workspace", workspace, key="graph_ws")
    projects: dict[str, list[str]] = {}
    if workspace:
        try:
            projects = _catalog(workspace)
        except Exception as exc:  # unreadable dir / no bundles
            st.warning(f"Couldn't scan workspace: {exc}")
    project = st.selectbox("Project", list(projects) or ["—"], key="graph_project")

    st.markdown('<div class="side-label">Show</div>', unsafe_allow_html=True)
    node_types = frozenset(
        t for t in ("module", "class", "function") if st.checkbox(t, value=True, key=f"nt_{t}")
    )
    edge_kinds = frozenset(
        k for k in ("calls", "imports", "inherits") if st.checkbox(k, value=True, key=f"ek_{k}")
    )
    if st.button("Reset view", key="graph_reset"):
        for k in list(st.session_state):
            if k.startswith("visible::"):
                del st.session_state[k]

st.markdown(
    '<div class="brand">graph <span class="accent">explorer</span></div>',
    unsafe_allow_html=True,
)

if not workspace or not projects:
    st.info("Set a workspace with pydocs-mcp bundles (same as the chat page).")
    st.stop()

db = _db_for(workspace, project)
if db is None:
    st.warning(f"No bundle found for project {project!r}.")
    st.stop()

key = f"visible::{project}"
if key not in st.session_state:
    st.session_state[key] = {n.id for n in graph.overview(db, project).nodes}
    if not st.session_state[key]:
        st.info(
            "This bundle has no reference graph — enable `reference_graph.capture` and re-index."
        )
        st.stop()
visible: set[str] = st.session_state[key]

full = graph.overview(db, project)
known = {n.id: n for n in full.nodes}
edges = list(full.edges)
for nid in list(visible):
    ntype = known[nid].node_type if nid in known else "module"
    sub = graph.expand(db, nid, ntype, edge_kinds)
    for n in sub.nodes:
        known.setdefault(n.id, n)
    edges.extend(sub.edges)
combined = graph.Graph(tuple(known[i] for i in visible if i in known), tuple(edges))
shown = graph.induce(combined, node_types, edge_kinds)

anodes = [
    ANode(id=n.id, label=n.label, size=15, color=_TYPE_COLOR.get(n.node_type, "#8A97A6"))
    for n in shown.nodes
]
aedges = [AEdge(source=e.source, target=e.target, label=e.kind) for e in shown.edges]
clicked = agraph(
    nodes=anodes,
    edges=aedges,
    config=Config(width="100%", height=600, directed=True, physics=True),
)

if shown.truncated:
    st.caption(
        f"Showing {graph.MAX_NEIGHBORS} of {graph.MAX_NEIGHBORS + shown.truncated} neighbors."
    )

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
