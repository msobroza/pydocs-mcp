"""Graph explorer — second page of the ask-your-docs app.

A zoom / drill-down view: the canvas shows the direct children of the current
focus (a breadcrumb up top), and clicking a container node (package / module /
class / doc file) zooms into it. Click a breadcrumb segment to zoom back out.
Leaves (functions, methods, decisions, doc sections) just open the side panel.
Node types are shown by shape + colour (legend); edges are coloured by kind.
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
_pal = THEMES["light" if st.session_state.get("light_mode") else "dark"]
st.markdown(theme_css(_pal), unsafe_allow_html=True)
st.markdown(
    "<style>.block-container{max-width:100% !important;padding-left:2rem;padding-right:2rem;}</style>",
    unsafe_allow_html=True,
)

# Node types: distinct SHAPE + COLOUR (shape so it never relies on colour alone).
_TYPE_STYLE = {
    "package": ("hexagon", "#2A9D8F", "⬡"),
    "module": ("diamond", "#34D3B7", "◆"),
    "class": ("square", "#818CF8", "■"),
    "function": ("dot", "#60A5FA", "●"),
    "doc": ("triangle", "#F0997B", "▲"),
    "decision": ("star", "#EF9F27", "★"),
}
_TYPE_SIZE = {"package": 22, "module": 18, "class": 15, "function": 11, "doc": 15, "decision": 15}
_EDGE_COLOR = {"calls": "#60A5FA", "imports": "#8A97A6", "inherits": "#D4537E"}
_CONTAINER_TYPES = {"package", "module", "class", "doc"}


def _is_container(nid: str, ntype: str) -> bool:
    if nid.startswith(("section:", "decision:")):
        return False
    return ntype in _CONTAINER_TYPES


def _crumbs(focus: str, project: str) -> list[tuple[str, str]]:
    """(label, target-focus) for the breadcrumb, root first."""
    if focus == "":
        return [(project, "")]
    if focus.startswith("doc:"):
        return [(project, ""), (focus.removeprefix("doc:"), focus)]
    parts = focus.split(".")
    return [(project, ""), *[(parts[i], ".".join(parts[: i + 1])) for i in range(len(parts))]]


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
    picked_types = frozenset(
        t for t in ("module", "class", "function") if st.checkbox(t, value=True, key=f"nt_{t}")
    )
    edge_kinds = frozenset(
        k for k in ("calls", "imports", "inherits") if st.checkbox(k, value=True, key=f"ek_{k}")
    )
    hide_tests = st.checkbox("Hide test files", value=True, key="graph_hide_tests")

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

focus_key = f"graph_focus::{project}::{content}"
focus: str = st.session_state.get(focus_key, "")

# Breadcrumb: each segment zooms back out to that level.
crumbs = _crumbs(focus, project)
cols = st.columns([1] * len(crumbs) + [6])
for i, (label, target) in enumerate(crumbs):
    if cols[i].button(
        ("📁 " if i == 0 else "") + label, key=f"crumb_{i}", disabled=(target == focus)
    ):
        st.session_state[focus_key] = target
        st.session_state.pop("graph_selected", None)
        st.rerun()

# Children of the current focus + reference edges among them.
allowed = picked_types | {"package", "doc", "decision"}
kids = tuple(n for n in graph.children(db, focus, content, hide_tests) if n.node_type in allowed)
if not kids:
    st.info("Nothing to show here — zoom out, or widen the filters / content type.")
    st.stop()
ids = {n.id for n in kids}
type_map = {n.id: n.node_type for n in kids}
edges = tuple(
    e for e in graph.edges_for(db, ids, edge_kinds) if e.source in ids and e.target in ids
)

# Legend.
_node_legend = " ".join(
    f'<span style="color:{_TYPE_STYLE[t][1]}">{_TYPE_STYLE[t][2]}</span>'
    f'<span style="color:{_pal["muted"]}"> {t}</span>'
    for t in _TYPE_STYLE
    if any(nt == t for nt in type_map.values())
)
_edge_legend = " ".join(
    f'<span style="color:{_EDGE_COLOR[k]}">──</span><span style="color:{_pal["muted"]}"> {k}</span>'
    for k in ("calls", "imports", "inherits")
    if k in edge_kinds
)
st.markdown(
    f'<div style="display:flex;gap:1.4rem;flex-wrap:wrap;font-size:.82rem;margin:.1rem 0 .5rem;">'
    f'<span style="color:{_pal["text"]};font-weight:600">nodes</span> {_node_legend}'
    f'<span style="color:{_pal["text"]};font-weight:600;margin-left:.6rem">edges</span> {_edge_legend}'
    f"</div>",
    unsafe_allow_html=True,
)
st.caption(f"{len(kids)} items · {len(edges)} edges — click a ◆/⬡/■ to zoom in")

anodes = [
    ANode(
        id=n.id,
        label=n.label,
        size=_TYPE_SIZE.get(n.node_type, 12),
        color=_TYPE_STYLE.get(n.node_type, ("dot", "#8A97A6", ""))[1],
        shape=_TYPE_STYLE.get(n.node_type, ("dot", "#8A97A6", ""))[0],
    )
    for n in kids
]
aedges = [
    AEdge(source=e.source, target=e.target, color=_EDGE_COLOR.get(e.kind, "#8A97A6")) for e in edges
]
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
    ),
)

# A click zooms into a container, or just selects a leaf. Guard on the last id so
# the component re-returning the same value on a rerun doesn't loop.
if clicked and clicked != st.session_state.get("graph_last_click"):
    st.session_state.graph_last_click = clicked
    st.session_state.graph_selected = clicked
    if clicked in type_map and _is_container(clicked, type_map[clicked]):
        st.session_state[focus_key] = clicked  # zoom in
    st.rerun()

selected = st.session_state.get("graph_selected")
if selected:
    with st.sidebar:
        st.markdown('<div class="side-label">Selected</div>', unsafe_allow_html=True)
        ntype = type_map.get(selected) or graph.type_of(selected, graph.modules(db))
        meta = graph.node_meta(db, selected, ntype)
        if meta:
            st.markdown(f"**{meta.title}**  \n`{meta.id}`")
            if meta.body:
                st.code(meta.body)
        if (
            selected in type_map
            and _is_container(selected, type_map[selected])
            and st.button("🔍 Zoom in", key="graph_zoom")
        ):
            st.session_state[focus_key] = selected
            st.rerun()
        if st.button("➕ Add to question", key="graph_attach"):
            att = st.session_state.setdefault("attached", [])
            if selected not in att:
                att.append(selected)
            st.toast(f"Attached {selected}")
