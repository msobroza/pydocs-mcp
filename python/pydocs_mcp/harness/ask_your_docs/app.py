"""Streamlit chat UI for the ask-your-docs agent.

Launched by the ``harness-ask-your-docs`` CLI. Workspace and config prefill from
PYDOCS_WORKSPACE / PYDOCS_CONFIG; the chat model's endpoint, model and bearer come
from the LLM connection (design §4.9): ``ask_your_docs.llm`` < OPENAI_BASE_URL /
LLM_MODEL < --base-url / --model (forwarded by the CLI under private
``HARNESS_ASK_YOUR_DOCS_*`` names that the serve child never reads) < the
Connection dialog (session only). AppTest seams (session state, tests only):
``connection_bearer`` (a BearerSource used instead of the registry),
``connection_list_models`` (the listing seam), ``connection_transport`` (the
httpx transport handed to the Test-connection helper), ``connection_group_info``
(the LiteLLM probe seam), ``serve_tools_opener`` (a ServeToolsOpener standing in
for the page's pydocs-mcp serve child) and ``scope_capabilities`` (the server's scope
capability record, otherwise learned from the held session after the first turn).

Scope (UI spec 2026-09-04): soft defaults live behind the sidebar's "Scope defaults"
button; a per-question pin lives in the popover left of the chat input, as chips in
the attachment row, and as follow-up chips under an answer. ``send_question`` is the
ONE send path — the chat input and the follow-up chips both call it.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.activity_redaction import secret_env_names, turn_redactor
from pydocs_mcp.harness.ask_your_docs.activity_view import LiveActivityPanel, PanelSettings
from pydocs_mcp.harness.ask_your_docs.agent import ask, build_agent, weave_attachments
from pydocs_mcp.harness.ask_your_docs.answer_footer import apply_follow_up_chip
from pydocs_mcp.harness.ask_your_docs.attachments import (
    ImageAttachment,
    text_only_policy,
    update_image_store,
)
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BEARER_ERRORS,
    BearerSource,
    display_host,
    redacted_failure_caption,
)
from pydocs_mcp.harness.ask_your_docs.catalog import workspace_catalog
from pydocs_mcp.harness.ask_your_docs.chat_wire import WireParams
from pydocs_mcp.harness.ask_your_docs.cli import LAUNCH_BASE_URL_ENV_VAR, LAUNCH_MODEL_ENV_VAR
from pydocs_mcp.harness.ask_your_docs.connection_dialog import (
    KEY_OPEN,
    STATE_DIALOG_OPEN,
    STATE_OVERRIDE,
    open_connection_dialog,
    render_connection_status_line,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    LlmConnection,
    bearer_for_connection,
    connection_identity,
    resolve_llm_connection,
    resolve_vision_capabilities,
)
from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities
from pydocs_mcp.harness.ask_your_docs.page_agent import (
    PageAgentHandle,
    release_page_agent,
    restart_notice,
)
from pydocs_mcp.harness.ask_your_docs.page_connection_actions import (
    PageConnectionHooks,
    dialog_actions,
)
from pydocs_mcp.harness.ask_your_docs.page_scope import (
    answer_footer_and_chips,
    page_scope_capabilities,
    remember_scope_capabilities,
    scan_workspace,
)
from pydocs_mcp.harness.ask_your_docs.page_turn import (
    AskTurn,
    TurnRunners,
    answer_question,
    collect_images,
    fail_turn,
    finish_turn,
    open_turn_panel,
    refuse,
    technical_details_toggle,
    turn_progress,
)
from pydocs_mcp.harness.ask_your_docs.param_feedback import (
    StarvationWatch,
    learn_param_rejection,
    log_page_wire,
    page_wire,
)
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    resolve_question_scope_defaults,
    scope_caption_text,
    snapshot_pin_for_send,
)
from pydocs_mcp.harness.ask_your_docs.reasoning_caption import render_reasoning_caption
from pydocs_mcp.harness.ask_your_docs.reformulation import reformulate
from pydocs_mcp.harness.ask_your_docs.scope_panel import (
    drop_pin_if_listing_changed,
    render_composer_row,
    render_follow_up_chips,
    render_scope_chip_row,
    render_scope_defaults_button,
    render_scope_defaults_panel,
)
from pydocs_mcp.harness.ask_your_docs.serve_session import page_serve_opener
from pydocs_mcp.harness.ask_your_docs.theme import theme_css
from pydocs_mcp.harness.ask_your_docs.transcript import (
    assistant_transcript_entry,
    render_transcript,
    user_transcript_entry,
)
from pydocs_mcp.retrieval.config.app_config import AppConfig
from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode, VisionRule

if TYPE_CHECKING:
    from pydocs_mcp.harness.ask_your_docs.serve_session import ServeToolsOpener

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

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


_T = TypeVar("_T")


def run(coro: Coroutine[Any, Any, _T]) -> _T:
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


def resolve_connection(config: str | None, dialog: ConnectionOverride) -> LlmConnection:
    """The page's connection: YAML < environment < the launcher's flags < dialog (spec R3)."""
    launch = ConnectionOverride(
        os.environ.get(LAUNCH_BASE_URL_ENV_VAR), os.environ.get(LAUNCH_MODEL_ENV_VAR)
    )
    return resolve_llm_connection(
        load_ayd_config(config).llm, os.environ, launch, dialog, config_path=config
    )


def page_connection(config: str | None) -> LlmConnection:
    override = st.session_state.get(STATE_OVERRIDE) or ConnectionOverride()
    return resolve_connection(config, override)


def page_bearer(connection: LlmConnection) -> BearerSource:
    """The registry's bearer for the identity — one per process (design §4.4) — or the test seam."""
    seeded = st.session_state.get("connection_bearer")
    return seeded if seeded is not None else bearer_for_connection(connection)


# What a cached verdict or agent belongs to: the endpoint, the model, the pydocs config and where
# the bearer comes from — never the token itself, so Renew leaves both entries alone (§4.7, R7).
ConnectionKey = tuple[str, str | None, str | None, tuple[AuthMode, str, bool]]


def connection_key(connection: LlmConnection) -> ConnectionKey:
    """The hashed half of the page's caches; everything beside it rides unhashed (underscored)."""
    identity = connection_identity(connection)
    return (connection.model or "", connection.base_url, connection.config_path, identity)


@st.cache_resource
def get_capabilities(key: ConnectionKey, _connection: LlmConnection, _bearer: BearerSource):
    # One verdict pair per key — the status line, text_only_policy and the agent read the same
    # one (design §4.7). Underscore-prefixed objects are not hashed; the key carries no secret.
    cfg = load_ayd_config(_connection.config_path)
    return run(resolve_vision_capabilities(_connection, _bearer, cfg.multimodal.detection))


def page_vision_capabilities(
    connection: LlmConnection, bearer: BearerSource
) -> tuple[ModelCapabilities | None, str | None]:
    """The VISION half plus the bearer text beside it — ONE boundary for both bearer fetches.

    The badge and the send policy read this verdict, never the main one, blind by design under
    a separate vision model. No verdict — a caption instead (H3) — whenever the bearer cannot
    be had (preflight, or an opt-in probe's own fetch), or under ``vision: null`` with no model."""
    try:
        if connection.auth_mode is AuthMode.TOKEN_SERVICE:
            bearer.current()  # preflight: the blocking first fetch, on the Streamlit thread
        if connection.model is None and connection.vision_rule is VisionRule.DETECT:
            return None, None
        _main, vision = get_capabilities(connection_key(connection), connection, bearer)
    except BEARER_ERRORS as exc:  # the status line and the refusal, never the exception box
        return None, redacted_failure_caption(exc, bearer)
    return vision, None


# Per BROWSER session: the tools are bound to this page's own serve child, not one per tool
# call. Keyed on the auth identity, never on a token: Renew leaves the entry alone (R7); a new
# endpoint, model or sent settings (``wire``, model-params v2 §5 rule 8) evicts it
# (max_entries=1) and on_release closes its child, as a disconnect or "Clear caches" does.
@st.cache_resource(scope="session", max_entries=1, on_release=release_page_agent)
def page_agent(
    workspace: str,
    key: ConnectionKey,
    wire: WireParams,
    _connection: LlmConnection,
    _bearer: BearerSource,
    _opener: ServeToolsOpener | None,
) -> PageAgentHandle:
    verdicts = get_capabilities(key, _connection, _bearer)
    log_page_wire(_connection)
    opener = (
        _opener if _opener is not None else page_serve_opener(workspace, _connection.config_path)
    )
    build = functools.partial(_build_page_agent, workspace, verdicts, wire, _connection, _bearer)
    return PageAgentHandle(event_loop(), opener, build)  # lazy: spawns nothing yet


# WHY both halves: capabilities= alone makes build_agent treat the main verdict as the vision
# one too — wrong under a separate vision model, where the main model is blind by design.
def _build_page_agent(
    workspace: str,
    verdicts: tuple[ModelCapabilities, ModelCapabilities],
    wire: WireParams,
    connection: LlmConnection,
    bearer: BearerSource,
    mcp_tools: list,
):
    main_caps, vision_caps = verdicts
    return build_agent(
        workspace,
        connection.model,
        connection.base_url,
        connection.config_path,
        catalog=load_catalog(workspace),
        config=load_ayd_config(connection.config_path),
        capabilities=main_caps,
        vision_capabilities=vision_caps,
        connection=connection,
        bearer=bearer,
        mcp_tools=mcp_tools,
        wire=wire,
    )


# The dialog's callbacks run through the page's own loop and caches (page_connection_actions).
_PAGE_HOOKS = PageConnectionHooks(run, resolve_connection, page_bearer)


with st.sidebar:
    st.markdown('<div class="side-label">Connection</div>', unsafe_allow_html=True)
    workspace = st.text_input("Workspace", os.environ.get("PYDOCS_WORKSPACE", ""))
    config_path = (
        st.text_input("pydocs config (optional)", os.environ.get("PYDOCS_CONFIG", "")) or None
    )
    connection = page_connection(config_path)
    bearer = page_bearer(connection)
    wire = page_wire(connection)  # what the dialog's Test line reported (v2 §5 rule 4)
    vision_caps, bearer_error = page_vision_capabilities(connection, bearer)
    render_connection_status_line(
        connection, bearer.describe(), vision_caps, bearer_error=bearer_error
    )
    ui_config = load_ayd_config(config_path).ui
    reasoning_caption = render_reasoning_caption(
        ui_config, connection_key(connection), thinking_off=wire.thinking_off
    )
    # State-driven opener: AppTest always runs the full script, so a transient
    # `if st.button(...)` alone would never re-enter the dialog on the next run.
    if st.button("Connection", key=KEY_OPEN):
        st.session_state[STATE_DIALOG_OPEN] = True
    if st.session_state.get(STATE_DIALOG_OPEN):
        open_connection_dialog(
            connection,
            bearer.describe(),
            dialog_actions(config_path, connection, bearer, _PAGE_HOOKS),
            capabilities=vision_caps,
            bearer_error=bearer_error,
        )
    st.caption("Point Workspace at a folder of pydocs-mcp index bundles.")

    catalog, listing = scan_workspace(workspace, load_catalog)
    ayd_cfg = load_ayd_config(config_path)
    scope_caps = page_scope_capabilities()
    # Hidden by default: one button, the panel only once clicked (§6.7 state 2).
    render_scope_defaults_button()
    override = render_scope_defaults_panel(ayd_cfg.scope, catalog, listing, scope_caps)
    technical = technical_details_toggle(ui_config)

st.markdown(theme_css(), unsafe_allow_html=True)
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

defaults = resolve_question_scope_defaults(ayd_cfg.scope, override, listing)
drop_pin_if_listing_changed(listing, workspace)

if "messages" not in st.session_state:
    st.session_state.messages, st.session_state.history = [], []

panel_settings = PanelSettings(ui_config, technical, display_host(connection.base_url))
clicked_chip = render_transcript(panel_settings)

attached = st.session_state.setdefault("attached", [])
render_scope_chip_row(attached, st.session_state.get("scope_pin"))

# Image chips from the last image-bearing question — visually distinct from
# the symbol-name buttons above (🖼 markdown pills, not buttons). Pre-send
# removal is the chat_input file widget's own ✕ (accept_file arrives
# atomically with the question, spec §4.7).
image_chips = st.session_state.setdefault("image_chips", [])
if image_chips:
    st.caption("Images attached to the last question:")
    st.markdown(" ".join(f"`🖼 {name}`" for name in image_chips))


def refuse_unless_connected(question: str) -> None:
    """Both send paths refuse BEFORE any tool or LLM construction (design E19)."""
    if bearer_error is not None:
        refuse(question, bearer_error, bearer)
    if connection.model is None:
        refuse(question, "No model chosen — open Connection and pick one.", bearer)


def _run_turn(
    question: str, woven: str, turn: AskTurn, panel: LiveActivityPanel | None
) -> tuple[str, PageAgentHandle | None]:
    """The page's ONE degrade boundary — EVERY failure, redacted (H4); (answer, handle)."""
    handle: PageAgentHandle | None = None
    watch = StarvationWatch(wire)  # v2 §5 rule 6: ask hands it the turn's last message
    try:
        opener = st.session_state.get("serve_tools_opener")  # the AppTest seam
        key = connection_key(connection)
        handle = page_agent(workspace, key, wire, connection, bearer, opener)
        rewrite = functools.partial(reformulate, wire=wire)  # P3: a sent temperature -> 0
        runners = TurnRunners(rewrite, functools.partial(ask, on_final=watch.observe))
        outcome = answer_question(woven, handle, bearer, turn, runners, panel)
    except Exception as exc:
        # WARNING, not INFO: `streamlit run` leaves the root logger unconfigured, so an INFO
        # record from this page is dropped. The class alone — never a message (H4 on logs).
        log.warning(json.dumps({"event": "send_failed", "error": exc.__class__.__name__}))
        # v2 §5 rule 5: a 400 naming a sent setting hides it for the session — never retried.
        rejected = learn_param_rejection(exc, wire, connection)
        caption = rejected or redacted_failure_caption(exc, bearer)
        if panel is None:
            refuse(question, caption, bearer)
        # A failure once the page released its agent is the page going away: "stopped".
        released = handle is not None and handle.closed
        fail_turn(panel, question, caption, exc.__class__.__name__, released=released)
    if outcome.restart is not None:
        st.info(restart_notice(outcome.restart))
    return watch.answer_or_notice(outcome.result), handle


def _record_question(
    question: str, images: tuple[ImageAttachment, ...], scope: QuestionScope
) -> dict[str, ImageAttachment]:
    """Show the question (with its scope chip) and keep it; returns the PRIOR image store."""
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
    caption = scope_caption_text(scope)
    st.session_state.messages.append(user_transcript_entry(shown, caption))
    with st.chat_message("user"):
        if caption:
            st.caption(caption)
        st.markdown(shown)
    return prior_images


def send_question(
    question: str,
    images: tuple[ImageAttachment, ...],
    scope: QuestionScope,
    transient_note: str = "",
) -> None:
    """The ONE send path: a follow-up chip's canned question is woven, reformulated,
    prefixed and observed exactly like a typed one (UI spec §6.13)."""
    prior_images = _record_question(question, images, scope)
    with st.chat_message("assistant"), turn_progress(ui_config):
        # A fresh immutable snapshot per question — not shared across sessions.
        turn = AskTurn(
            scope,
            images,
            prior_images,
            transient_note,
            listing=listing,
            max_cells=ayd_cfg.scope.max_cells,
        )
        woven = weave_attachments(attached, question)
        st.session_state.attached = []  # consumed by this send, answered or not
        redact = turn_redactor(bearer, secret_env_names(connection.api_key_env), os.environ)
        panel = open_turn_panel(panel_settings, redact, scope)  # None: the panel is off
        answer, handle = _run_turn(question, woven, turn, panel)
        st.markdown(answer)
        finish_turn(panel, answer, reasoning_caption)
        footer, chips = answer_footer_and_chips(turn, remember_scope_capabilities(handle), listing)
        st.caption(footer)
        render_follow_up_chips(len(st.session_state.messages), chips)
    st.session_state.messages.append(assistant_transcript_entry(answer, footer, chips))


if clicked_chip is not None:
    # Handled BEFORE the popover renders: a PIN_BRANCH chip writes the toggle's key.
    canned, pin = apply_follow_up_chip(clicked_chip, st.session_state.get("scope_pin"), defaults)
    if canned is None:
        st.session_state["scope_pin"] = pin
        st.session_state["scope_pin_keep"] = True
        st.rerun()
    refuse_unless_connected(canned)
    send_question(canned, (), pin if pin is not None else defaults)

submission = render_composer_row(listing, scope_caps, defaults, ayd_cfg.scope.max_cells)

if submission:
    question = submission.text or ""
    refuse_unless_connected(question)
    images = collect_images(list(submission.files or ()), ayd_cfg.images)
    # The VISION half decides, never the main verdict: under a separate vision
    # model the main model is blind by design while the images still have a reader.
    verdict = text_only_policy(images, vision_caps, ayd_cfg.multimodal, model=connection.model)
    if verdict is not None and verdict.kind == "reject":
        refuse(question, verdict.message, bearer)  # spec §3.8: a policy check, not an exception
    transient_note = ""
    if verdict is not None and verdict.kind == "describe":
        st.warning("The model cannot see the attached image(s); answering from text only.")
        # The cannot-see note rides ask()'s transient_note (attached AFTER
        # reformulation, never persisted) — the scope-pin pattern.
        transient_note = verdict.message
        images = ()
    # WHY: a bridge until the strip replaces the popover (the next task rewrites this
    # block): the snapshot no longer hands back a "kept pin", so the old one-shot
    # lifecycle is applied here — a one-shot pin is gone before ask() runs.
    pin = st.session_state.get("scope_pin")
    scope = snapshot_pin_for_send(pin if pin is not None else defaults, attached, listing)
    if not st.session_state.get("scope_pin_keep", False):
        st.session_state["scope_pin"] = None
    send_question(question, images, scope, transient_note)
