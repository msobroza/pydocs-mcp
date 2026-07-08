"""Graph explorer — presentation layer only (Streamlit + streamlit-agraph).

A zoom / drill-down view: the canvas shows the direct children of the current
focus (a breadcrumb up top); clicking a container node (package / module / class
/ doc file) zooms in, a breadcrumb segment zooms out. All data comes from a
``GraphService`` (domain) over a ``SqliteBundleReader`` (data access) — this file
holds no SQL and no graph logic.
"""

from __future__ import annotations

import os

import streamlit as st
import streamlit.components.v1 as components
from pydocs_mcp.ask_your_docs.bundle import SqliteBundleReader
from pydocs_mcp.ask_your_docs.catalog import CatalogService
from pydocs_mcp.ask_your_docs.graph_service import GraphService, type_of
from pydocs_mcp.ask_your_docs.theme import current_palette, render_appearance_toggle, theme_css
from streamlit_agraph import Config, agraph
from streamlit_agraph import Edge as AEdge
from streamlit_agraph import Node as ANode

st.set_page_config(
    page_title="ask your docs — graph",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)
_pal = current_palette()
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
def _projects(workspace: str) -> dict[str, list[str]]:
    return CatalogService(workspace).projects()


workspace = os.environ.get("PYDOCS_WORKSPACE", "")
with st.sidebar:
    st.markdown('<div class="side-label">Appearance</div>', unsafe_allow_html=True)
    render_appearance_toggle()

    st.markdown('<div class="side-label">Workspace</div>', unsafe_allow_html=True)
    workspace = st.text_input("Workspace", workspace, key="graph_ws")
    projects: dict[str, list[str]] = {}
    if workspace:
        try:
            projects = _projects(workspace)
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

db = CatalogService(workspace).bundle_path(project)
if db is None:
    st.warning(f"No bundle found for project {project!r}.")
    st.stop()
svc = GraphService(SqliteBundleReader(db), hide_tests=hide_tests)

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
kids = tuple(n for n in svc.children(focus, content) if n.node_type in allowed)
if not kids:
    st.info("Nothing to show here — zoom out, or widen the filters / content type.")
    st.stop()
ids = {n.id for n in kids}
type_map = {n.id: n.node_type for n in kids}
edges = tuple(e for e in svc.edges_for(ids, edge_kinds) if e.source in ids and e.target in ids)

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

# vis-network defaults label text to #343434 (near-invisible on the dark canvas).
# Force the theme's text colour + a background-coloured halo so every name reads
# clearly over nodes, edges and the canvas alike, in both light and dark mode.
_LABEL_FONT = {
    "color": _pal["text"],
    "size": 15,
    "face": "Helvetica, Arial, sans-serif",
    "strokeWidth": 4,
    "strokeColor": _pal["bg"],
}
anodes = [
    ANode(
        id=n.id,
        label=n.label,
        size=_TYPE_SIZE.get(n.node_type, 12),
        color=_TYPE_STYLE.get(n.node_type, ("dot", "#8A97A6", ""))[1],
        shape=_TYPE_STYLE.get(n.node_type, ("dot", "#8A97A6", ""))[0],
        font=_LABEL_FONT,
    )
    for n in kids
]
aedges = [
    AEdge(source=e.source, target=e.target, color=_EDGE_COLOR.get(e.kind, "#8A97A6")) for e in edges
]
# Config(width=...) appends "px", so width="100%" would become the invalid
# "100%px" and collapse the canvas (nodes end up tiny in a corner). Build with a
# placeholder, then set the raw CSS value so vis-network gets a responsive "100%"
# and its stabilization `fit` frames every node centered.
_cfg = Config(
    height=760,
    directed=True,
    physics=True,
    nodeHighlightBehavior=True,
    highlightColor="#34D3B7",
)
_cfg.width = "100%"
clicked = agraph(nodes=anodes, edges=aedges, config=_cfg)

# The streamlit-agraph component renders in its own (same-origin) iframe. Two
# things need patching there, both unreachable via Python Config / parent CSS:
#   1. Its <body> defaults to white — jarring in the dark theme and it washes out
#      the light node labels; repaint it to the theme background.
#   2. Its built-in doubleClick handler runs `window.open(node.title)`, and the
#      node title defaults to the node id — so a double-click navigates the browser
#      to a bogus URL. Neutralise window.open inside that iframe (single-click zoom
#      is unaffected). Both are re-applied on every Streamlit rerun.
components.html(
    f"""
    <script>
      const BG = "{_pal["bg"]}";
      const patch = () => {{
        try {{
          window.parent.document.querySelectorAll('iframe').forEach((f) => {{
            if (!(f.title || "").includes("agraph")) return;
            if (f.contentDocument) f.contentDocument.body.style.background = BG;
            if (f.contentWindow) f.contentWindow.open = () => null;
          }});
        }} catch (e) {{ /* cross-origin / not ready yet — retried by the interval */ }}
      }};
      patch();
      setInterval(patch, 500);
    </script>
    """,
    height=0,
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
        ntype = type_map.get(selected) or type_of(selected, svc.modules())
        meta = svc.node_meta(selected, ntype)
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
