"""Graph explorer — second page of the ask-your-docs app.

Overview -> click a node to expand it (its members / connections) -> read the
docstring in the panel and collapse it again with the panel toggle. Filters:
content type (codebase / documentation / both), node type, edge kind.
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
# The chat theme caps .block-container at 46rem (a readable column); the graph
# wants the full wide-layout width.
st.markdown(
    "<style>.block-container{max-width:100% !important;padding-left:2rem;padding-right:2rem;}</style>",
    unsafe_allow_html=True,
)

_TYPE_COLOR = {
    "module": "#34D3B7",
    "class": "#818CF8",
    "function": "#60A5FA",
    "doc": "#F0997B",
    "decision": "#EF9F27",
}
_TYPE_SIZE = {"module": 22, "class": 16, "function": 12, "doc": 16, "decision": 16}
_STRUCTURAL = {"contains", "documents", "concerns"}


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
    content = st.radio(
        "Content",
        ["Codebase", "Documentation", "Documentation + codebase"],
        key="graph_content",
    )

    st.markdown('<div class="side-label">Show</div>', unsafe_allow_html=True)
    node_types = frozenset(
        t for t in ("module", "class", "function") if st.checkbox(t, value=True, key=f"nt_{t}")
    )
    if content != "Codebase":
        node_types = node_types | frozenset({"doc", "decision"})
    edge_kinds = frozenset(
        k for k in ("calls", "imports", "inherits") if st.checkbox(k, value=True, key=f"ek_{k}")
    )
    if st.button("Reset view", key="graph_reset"):
        for k in [k for k in st.session_state if k.startswith("expanded::")]:
            del st.session_state[k]
        st.session_state.pop("graph_selected", None)
        st.session_state.pop("graph_last_click", None)

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

# Seed nodes for the enabled content type.
cats = []
if content != "Documentation":
    cats.append(graph.overview(db, project))
if content != "Codebase":
    cats.append(graph.doc_nodes(db, project))
    cats.append(graph.decision_nodes(db, project))

known: dict[str, graph.Node] = {n.id: n for cat in cats for n in cat.nodes}
if not known:
    st.info("Nothing to show for this content type in this bundle.")
    st.stop()

module_set = graph.modules(db)
exp_key = f"expanded::{project}::{content}"
expanded: set[str] = st.session_state.setdefault(exp_key, set())

# visible is DERIVED each run: the seed plus whatever the expanded nodes reveal.
# Expanding/collapsing a node just toggles its membership in `expanded`, and the
# graph recomputes — so nodes implode and explode deterministically.
visible: set[str] = set(known)
struct_edges = [e for cat in cats for e in cat.edges if e.kind in _STRUCTURAL]
for nid in list(expanded):
    sub = graph.expand(db, nid, graph.type_of(nid, module_set), edge_kinds)
    for n in sub.nodes:
        known.setdefault(n.id, n)
        visible.add(n.id)
    struct_edges += [e for e in sub.edges if e.kind in _STRUCTURAL]
    visible.add(nid)

# Reference edges among ALL visible nodes (so siblings show their relationships,
# collapsing to modules when members are hidden).
ref_edges = graph.edges_for(db, visible, edge_kinds)
combined = graph.Graph(
    tuple(known[i] for i in visible if i in known), tuple(struct_edges) + ref_edges
)
shown = graph.induce(combined, node_types, edge_kinds)

st.caption(f"{len(shown.nodes)} nodes · {len(shown.edges)} edges — click a node to expand it")
anodes = [
    ANode(
        id=n.id,
        label=n.label,
        size=_TYPE_SIZE.get(n.node_type, 14),
        color=_TYPE_COLOR.get(n.node_type, "#8A97A6"),
    )
    for n in shown.nodes
]
aedges = [AEdge(source=e.source, target=e.target, label=e.kind) for e in shown.edges]
clicked = agraph(
    nodes=anodes,
    edges=aedges,
    config=Config(
        width="100%",
        height=760,
        directed=True,
        physics=True,
        nodeHighlightBehavior=True,
        highlightColor="#34D3B7",
        collapsible=False,
    ),
)

# A click selects a node and expands it. Guard on the last handled id so the
# component re-returning the same value on a rerun doesn't loop.
if clicked and clicked != st.session_state.get("graph_last_click"):
    st.session_state.graph_last_click = clicked
    st.session_state.graph_selected = clicked
    if clicked in known:
        expanded.add(clicked)
    st.rerun()

selected = st.session_state.get("graph_selected")
if selected and selected in known:
    with st.sidebar:
        st.markdown('<div class="side-label">Selected</div>', unsafe_allow_html=True)
        meta = graph.node_meta(db, selected, graph.type_of(selected, module_set))
        if meta:
            st.markdown(f"**{meta.title}**  \n`{meta.id}`")
            if meta.body:
                st.code(meta.body)
        if selected in expanded:
            if st.button("⊟ Collapse", key="graph_collapse"):
                expanded.discard(selected)
                st.rerun()
        else:
            if st.button("⊞ Expand", key="graph_expand"):
                expanded.add(selected)
                st.rerun()
        if st.button("➕ Add to question", key="graph_attach"):
            att = st.session_state.setdefault("attached", [])
            if selected not in att:
                att.append(selected)
            st.toast(f"Attached {selected}")
