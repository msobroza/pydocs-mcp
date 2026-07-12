"""Streamlit chat UI for the ask-your-docs agent.

Launched by the ``ask-your-docs`` CLI (``ask_your_docs.cli``). Connection
settings prefill from env: PYDOCS_WORKSPACE, LLM_MODEL, OPENAI_BASE_URL,
PYDOCS_CONFIG.
"""

from __future__ import annotations

import asyncio
import base64
import os
import threading
from pathlib import Path

import streamlit as st

from pydocs_mcp.ask_your_docs.agent import ask, build_agent, reformulate, weave_attachments
from pydocs_mcp.ask_your_docs.attachments import (
    ImageAttachment,
    text_only_policy,
    update_image_store,
    validate_attachment,
)
from pydocs_mcp.ask_your_docs.catalog import workspace_catalog
from pydocs_mcp.ask_your_docs.multimodal import detect_capabilities
from pydocs_mcp.ask_your_docs.theme import current_palette, render_appearance_toggle, theme_css
from pydocs_mcp.retrieval.config.app_config import AppConfig

st.set_page_config(
    page_title="ask your docs",
    page_icon="✦",
    layout="centered",
    # Keep the sidebar (and its page-navigation menu: chat / graph) open on load.
    initial_sidebar_state="expanded",
)


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
def load_ayd_config(config: str | None):
    # One YAML file configures both the pydocs-mcp subprocess and the agent
    # (spec §3.5): the same PYDOCS_CONFIG path, loaded through AppConfig
    # layering (defaults → overlay → PYDOCS_ASK_YOUR_DOCS__* env).
    return AppConfig.load(explicit_path=Path(config) if config else None).ask_your_docs


@st.cache_resource
def get_capabilities(model: str, base_url: str | None, config: str | None):
    # Detection runs once per (model, base_url, config) cache entry on the
    # shared background loop; the ladder default (static table only) is
    # network-free, so this is safe at sidebar-render time.
    cfg = load_ayd_config(config)
    return run(detect_capabilities(model, base_url or None, cfg.multimodal.detection))


@st.cache_resource
def get_agent(workspace: str, model: str, base_url: str | None, config: str | None):
    return run(
        build_agent(
            workspace,
            model,
            base_url or None,
            config or None,
            catalog=load_catalog(workspace),
            config=load_ayd_config(config),
            capabilities=get_capabilities(model, base_url, config),
        )
    )


_CODE_CHOICES = {"All code": "all", "Own code": "project", "Dependencies": "deps"}

with st.sidebar:
    st.markdown('<div class="side-label">Appearance</div>', unsafe_allow_html=True)
    render_appearance_toggle()

    st.markdown('<div class="side-label">Connection</div>', unsafe_allow_html=True)
    workspace = st.text_input("Workspace", os.environ.get("PYDOCS_WORKSPACE", ""))
    model = st.text_input("Model", os.environ.get("LLM_MODEL", "gpt-4o-mini"))
    base_url = st.text_input("Base URL (optional)", os.environ.get("OPENAI_BASE_URL", ""))
    config = st.text_input("pydocs config (optional)", os.environ.get("PYDOCS_CONFIG", ""))
    if model:
        # Capability badge: makes auto's routing visible (spec §3.7) —
        # e.g. "vision: yes (static)" / "vision: no (default)".
        caps = get_capabilities(model, base_url, config)
        st.caption(f"vision: {'yes' if caps.multimodal else 'no'} ({caps.source})")
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

st.markdown(theme_css(current_palette()), unsafe_allow_html=True)
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

# Image chips from the last image-bearing question — visually distinct from
# the symbol-name buttons above (🖼 markdown pills, not buttons). Pre-send
# removal is the chat_input file widget's own ✕ (accept_file arrives
# atomically with the question, spec §4.7).
image_chips = st.session_state.setdefault("image_chips", [])
if image_chips:
    st.caption("Images attached to the last question:")
    st.markdown(" ".join(f"`🖼 {name}`" for name in image_chips))


def _collect_images(files, images_cfg) -> tuple[ImageAttachment, ...]:
    """UploadedFiles → validated ImageAttachments; violations render an
    inline error chip and drop the offending file (spec §3.6)."""
    if len(files) > images_cfg.max_per_turn:
        st.warning(
            f"only the first {images_cfg.max_per_turn} images were kept (images.max_per_turn)"
        )
    collected: list[ImageAttachment] = []
    for f in files[: images_cfg.max_per_turn]:
        att = ImageAttachment(
            name=f.name,
            media_type=f.type or "application/octet-stream",
            data_b64=base64.b64encode(f.getvalue()).decode(),
        )
        try:
            validate_attachment(att, images_cfg)
        except ValueError as exc:
            st.error(str(exc))
            continue
        collected.append(att)
    return tuple(collected)


if submission := st.chat_input(
    "Ask about your indexed projects…",
    accept_file="multiple",
    file_type=["png", "jpg", "jpeg", "webp", "gif"],
):
    question = submission.text or ""
    ayd_cfg = load_ayd_config(config)
    images = _collect_images(list(submission.files or ()), ayd_cfg.images)
    caps = get_capabilities(model, base_url, config)
    verdict = text_only_policy(images, caps, ayd_cfg.multimodal, model=model)
    if verdict is not None and verdict.kind == "reject":
        # Fail loudly BEFORE any LLM call (spec §3.8): nothing is sent, and
        # the question text stays visible for copy-back.
        st.error(verdict.message)
        st.info(f"Your question (not sent): {question}")
        st.stop()
    transient_note = ""
    if verdict is not None and verdict.kind == "describe":
        st.warning("The model cannot see the attached image(s); answering from text only.")
        # The cannot-see note rides ask()'s transient_note (attached AFTER
        # reformulation, never persisted) — the scope-pin pattern.
        transient_note = verdict.message
        images = ()
    st.session_state.image_chips = [att.name for att in images]
    # Session image store: bytes from recent turns stay reinspectable by the
    # reinspect_images tool (history itself keeps only the placeholder).
    image_store = st.session_state.setdefault("image_store", {})
    # Snapshot BEFORE folding this turn's images: the current attachment was
    # just seen (inline) or extracted (vision node) — only LATER questions
    # need to reinspect it, and same-turn re-reads would be wasted vision
    # calls (necessity gating).
    prior_images = dict(image_store)
    update_image_store(image_store, images, retention=ayd_cfg.images.session_retention)
    shown = question + ("\n\n" + " ".join(f"`🖼 {att.name}`" for att in images) if images else "")
    st.session_state.messages.append(("user", shown))
    with st.chat_message("user"):
        st.markdown(shown)
    with st.chat_message("assistant"), st.spinner("searching your docs…"):
        agent, llm = get_agent(workspace, model, base_url, config)
        # A fresh immutable snapshot per question — not shared across sessions.
        scope = {"project": project_pin, "package": package_pin, "code": code_pin}
        woven = weave_attachments(attached, question)
        st.session_state.attached = []
        # reformulate is text-only by contract (§3.6): it runs on the woven
        # question BEFORE image blocks are attached.
        standalone = run(reformulate(llm, st.session_state.history, woven))
        answer = run(
            ask(
                agent,
                st.session_state.history,
                standalone,
                scope=scope,
                images=images,
                image_store=prior_images,  # PRIOR turns only — see snapshot note above
                transient_note=transient_note,
            )
        )
        st.markdown(answer)
    st.session_state.messages.append(("assistant", answer))
