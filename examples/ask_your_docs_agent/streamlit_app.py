"""Streamlit chat UI for the ask-your-docs agent.

    pip install -r requirements.py
    streamlit run streamlit_app.py

Configure via the sidebar or env: PYDOCS_WORKSPACE, PYDOCS_MODEL,
OPENAI_BASE_URL, PYDOCS_CONFIG.
"""

from __future__ import annotations

import asyncio
import os
import threading

import streamlit as st
from agent import ask, build_agent, reformulate

# Northern-lights palette.
BG, TEAL, BLUE, PURPLE, TEXT = "#0E2337", "#19B8A6", "#3B70A2", "#59497F", "#E6EDF3"

st.set_page_config(page_title="ask your docs", page_icon="✦", layout="centered")
st.markdown(
    f"""<style>
    .stApp {{ background: {BG}; color: {TEXT}; }}
    h1 {{ color: {TEAL}; font-family: monospace; }}
    section[data-testid="stSidebar"] {{ background: {BG}; border-right: 1px solid {BLUE}; }}
    [data-testid="stChatMessage"] {{ background: {BG}; border: 1px solid {BLUE}; border-radius: 8px; }}
    [data-testid="stChatMessage"]:has([aria-label="Chat message from user"]) {{ border-color: {PURPLE}; }}
    code {{ color: {TEAL}; }}
    pre {{ background: #0A1A2A !important; border-left: 3px solid {PURPLE}; }}
    .stChatInput textarea {{ background: #0A1A2A; color: {TEXT}; }}
    </style>""",
    unsafe_allow_html=True,
)
st.title('"ask your docs"')


@st.cache_resource
def event_loop() -> asyncio.AbstractEventLoop:
    # The MCP stdio session must live on ONE loop across Streamlit reruns.
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def run(coro):
    return asyncio.run_coroutine_threadsafe(coro, event_loop()).result()


@st.cache_resource
def get_agent(workspace: str, model: str, base_url: str | None, config: str | None):
    return run(build_agent(workspace, model, base_url or None, config or None))


with st.sidebar:
    workspace = st.text_input("Workspace", os.environ.get("PYDOCS_WORKSPACE", ""))
    model = st.text_input("Model", os.environ.get("PYDOCS_MODEL", "gpt-4o-mini"))
    base_url = st.text_input("Base URL (optional)", os.environ.get("OPENAI_BASE_URL", ""))
    config = st.text_input("pydocs config (optional)", os.environ.get("PYDOCS_CONFIG", ""))

if not workspace:
    st.info("Set the workspace (directory of pydocs-mcp index bundles) in the sidebar.")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages, st.session_state.history = [], []

for role, text in st.session_state.messages:
    with st.chat_message(role):
        st.markdown(text)

if question := st.chat_input("Ask about your indexed projects…"):
    st.session_state.messages.append(("user", question))
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"), st.spinner("searching your docs…"):
        agent, llm = get_agent(workspace, model, base_url, config)
        standalone = run(reformulate(llm, st.session_state.history, question))
        answer = run(ask(agent, st.session_state.history, standalone))
        st.markdown(answer)
    st.session_state.messages.append(("assistant", answer))
