"""Streamlit chat UI for the ask-your-docs agent.

Launched by the ``ask-your-docs`` CLI (``ask_your_docs.cli``). Connection
settings prefill from env: PYDOCS_WORKSPACE, PYDOCS_MODEL, OPENAI_BASE_URL,
PYDOCS_CONFIG.
"""

from __future__ import annotations

import asyncio
import os
import threading

import streamlit as st

from pydocs_mcp.ask_your_docs.agent import ask, build_agent, reformulate, weave_attachments
from pydocs_mcp.ask_your_docs.catalog import workspace_catalog
from pydocs_mcp.ask_your_docs.theme import THEMES, theme_css

st.set_page_config(page_title="ask your docs", page_icon="✦", layout="centered")


@st.cache_resource
def event_loop() -> asyncio.AbstractEventLoop:
    # The agent's async work must live on ONE loop across Streamlit reruns.
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def run(coro):
    return asyncio.run_coroutine_threadsafe(coro, event_loop()).result()


@st.cache_resource
def load_catalog(workspace: str) -> dict[str, list[str]]:
    # Cached per workspace (no ttl) and shared with the agent prompt, so the
    # pickers and the model always see the same projects. A newly indexed repo
    # appears on restart. Read-only — never mutates the bundles.
    return workspace_catalog(workspace)


@st.cache_resource
def get_agent(workspace: str, model: str, base_url: str | None, config: str | None):
    return run(
        build_agent(
            workspace, model, base_url or None, config or None, catalog=load_catalog(workspace)
        )
    )


_CODE_CHOICES = {"All code": "all", "Own code": "project", "Dependencies": "deps"}

with st.sidebar:
    st.markdown('<div class="side-label">Appearance</div>', unsafe_allow_html=True)
    light_mode = st.toggle("Light mode", value=False, key="light_mode")

    st.markdown('<div class="side-label">Connection</div>', unsafe_allow_html=True)
    workspace = st.text_input("Workspace", os.environ.get("PYDOCS_WORKSPACE", ""))
    model = st.text_input("Model", os.environ.get("PYDOCS_MODEL", "gpt-4o-mini"))
    base_url = st.text_input("Base URL (optional)", os.environ.get("OPENAI_BASE_URL", ""))
    config = st.text_input("pydocs config (optional)", os.environ.get("PYDOCS_CONFIG", ""))
    st.caption("Point Workspace at a folder of pydocs-mcp index bundles.")

    # Scope pickers. The project pin is forced onto every tool call; the package
    # and own-vs-dependency pins constrain the search tools (see agent._intercept).
    project_pin = package_pin = ""
    code_pin = "all"
    if workspace:
        try:
            projects = load_catalog(workspace)
        except Exception as exc:  # unreadable dir, no bundles, corrupt db
            projects = {}
            st.warning(f"Couldn't scan workspace: {exc}")
        if projects:
            st.markdown('<div class="side-label">Scope</div>', unsafe_allow_html=True)
            picked = st.selectbox("Project", ["All projects", *projects], key="scope_project")
            project_pin = "" if picked == "All projects" else picked
            code_pin = _CODE_CHOICES[
                st.radio("Code", list(_CODE_CHOICES), horizontal=True, key="scope_code")
            ]
            pool = sorted(
                {
                    p
                    for name, pkgs in projects.items()
                    if not project_pin or name == project_pin
                    for p in pkgs
                }
            )
            # No picker when own code is pinned (packages are dependencies) or
            # the pinned slice has no dependency packages indexed.
            if code_pin != "project" and pool:
                picked = st.selectbox("Package", ["All packages", *pool], key="scope_package")
                package_pin = "" if picked == "All packages" else picked
            st.caption("Searches run only inside this scope.")

st.markdown(theme_css(THEMES["light" if light_mode else "dark"]), unsafe_allow_html=True)
st.markdown(
    '<div class="brand">ask your <span class="accent">docs</span></div>'
    '<div class="brand-sub">grounded answers from your indexed code and docs</div>',
    unsafe_allow_html=True,
)

if not workspace:
    st.markdown(
        """<div class="empty">
        <div class="empty-title">Point me at your indexed repos</div>
        <div>Set a <b>Workspace</b> in the sidebar — a folder of pydocs-mcp
        <code>.db</code> / <code>.tq</code> bundles — then ask things like:</div>
        <div class="eg">how does routing work?</div>
        <div class="eg">what does IndexStorePort.load return?</div>
        <div class="eg">who calls BaseIndexStore.append?</div>
        </div>""",
        unsafe_allow_html=True,
    )
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages, st.session_state.history = [], []

for role, text in st.session_state.messages:
    with st.chat_message(role):
        st.markdown(text)

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

if question := st.chat_input("Ask about your indexed projects…"):
    st.session_state.messages.append(("user", question))
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"), st.spinner("searching your docs…"):
        agent, llm = get_agent(workspace, model, base_url, config)
        # A fresh immutable snapshot per question — not shared across sessions.
        scope = {"project": project_pin, "package": package_pin, "code": code_pin}
        woven = weave_attachments(attached, question)
        st.session_state.attached = []
        standalone = run(reformulate(llm, st.session_state.history, woven))
        answer = run(ask(agent, st.session_state.history, standalone, scope=scope))
        st.markdown(answer)
    st.session_state.messages.append(("assistant", answer))
