"""Streamlit chat UI for the ask-your-docs agent.

Launched by the ``harness-ask-your-docs`` CLI. Workspace and config prefill from
PYDOCS_WORKSPACE / PYDOCS_CONFIG; the chat model's endpoint, model and bearer come
from the LLM connection (design §4.9): ``ask_your_docs.llm`` < OPENAI_BASE_URL /
LLM_MODEL < --base-url / --model (copied into the environment by the CLI) < the
Connection dialog (session only). AppTest seams (session state, tests only):
``connection_bearer`` (a BearerSource used instead of the registry),
``connection_list_models`` (the listing seam) and ``connection_transport`` (the
httpx transport handed to the Test-connection helper).
"""

from __future__ import annotations

import asyncio
import base64
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import openai
import streamlit as st

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.ask_your_docs.agent import ask, build_agent, weave_attachments
from pydocs_mcp.harness.ask_your_docs.architectures import AgentArchitectureError
from pydocs_mcp.harness.ask_your_docs.attachments import (
    ImageAttachment,
    text_only_policy,
    update_image_store,
    validate_attachment,
)
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BEARER_ERRORS,
    BearerSource,
    TokenServiceError,
    redact_bearer,
    redacted_failure_caption,
)
from pydocs_mcp.harness.ask_your_docs.catalog import workspace_catalog
from pydocs_mcp.harness.ask_your_docs.connection_dialog import (
    KEY_OPEN,
    NOTHING_RENEWED,
    STATE_DIALOG_OPEN,
    STATE_OVERRIDE,
    ConnectionActions,
    open_connection_dialog,
    render_connection_status_line,
    vision_cell,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    LlmConnection,
    bearer_for_connection,
    connection_identity,
    resolve_llm_connection,
    resolve_vision_capabilities,
    run_connection_test,
)
from pydocs_mcp.harness.ask_your_docs.model_listing import (
    ModelListing,
    cached_model_listing,
    clear_model_listing_cache,
)
from pydocs_mcp.harness.ask_your_docs.multimodal import ListModels, ModelCapabilities
from pydocs_mcp.harness.ask_your_docs.reformulation import reformulate
from pydocs_mcp.harness.ask_your_docs.theme import (
    current_palette,
    render_appearance_toggle,
    theme_css,
)
from pydocs_mcp.retrieval.config.app_config import AppConfig
from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode, VisionRule

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


def resolve_connection(config: str | None, dialog: ConnectionOverride) -> LlmConnection:
    """The page's connection: YAML < environment (the CLI copied its flags there) < dialog."""
    return resolve_llm_connection(
        load_ayd_config(config).llm, os.environ, ConnectionOverride(), dialog, config_path=config
    )


def page_connection(config: str | None) -> LlmConnection:
    override = st.session_state.get(STATE_OVERRIDE) or ConnectionOverride()
    return resolve_connection(config, override)


def page_bearer(connection: LlmConnection) -> BearerSource:
    """The registry's bearer for the identity — one per process (design §4.4) — or the test seam."""
    seeded = st.session_state.get("connection_bearer")
    return seeded if seeded is not None else bearer_for_connection(connection)


def preflight_bearer(connection: LlmConnection, bearer: BearerSource) -> str | None:
    """Fetch the token once at render so the status line is honest (H3); the E1 text on failure.

    Runs on the Streamlit thread BEFORE the capability ladder (the first fetch is blocking
    I/O; the ladder's probes run on the loop), redacted: it rides ``help=`` and the refusal.
    """
    if connection.auth_mode is not AuthMode.TOKEN_SERVICE:
        return None
    try:
        bearer.current()
    except TokenServiceError as exc:
        return redact_bearer(str(exc), bearer)
    return None


@st.cache_resource
def get_capabilities(
    model: str,
    base_url: str | None,
    config: str | None,
    identity: tuple,
    _connection: LlmConnection,
    _bearer: BearerSource,
):
    # One verdict pair per (model, base_url, config, auth identity) — the status line,
    # text_only_policy and the agent read the same one (design §4.7). Underscore-prefixed
    # objects are not hashed; the key carries no secret.
    cfg = load_ayd_config(config)
    return run(resolve_vision_capabilities(_connection, _bearer, cfg.multimodal.detection))


def page_vision_capabilities(
    connection: LlmConnection,
    bearer: BearerSource,
    config: str | None,
    identity: tuple,
    bearer_error: str | None,
) -> ModelCapabilities | None:
    """The VISION half — the badge and the send policy read this one, never the main verdict,
    which is blind by design under a separate vision model. None while no verdict exists: an
    unavailable bearer (H3), or ``vision: null`` with no model to run the ladder on yet."""
    if bearer_error is not None:
        return None
    if connection.model is None and connection.vision_rule is VisionRule.DETECT:
        return None
    _main, vision = get_capabilities(
        connection.model or "", connection.base_url, config, identity, connection, bearer
    )
    return vision


@st.cache_resource
def get_agent(
    workspace: str,
    model: str,
    base_url: str | None,
    config: str | None,
    identity: tuple,
    _connection: LlmConnection,
    _bearer: BearerSource,
):
    # Keyed on the auth identity, never on a token: Renew mutates the bearer's cache and
    # leaves this entry alone (R7); a new endpoint or model builds anew.
    verdicts = get_capabilities(model, base_url, config, identity, _connection, _bearer)
    return run(_build_page_agent(workspace, config, verdicts, _connection, _bearer))


# WHY both halves: capabilities= alone makes build_agent treat the main verdict as the vision
# one too — wrong under a separate vision model, where the main model is blind by design.
def _build_page_agent(
    workspace: str,
    config: str | None,
    verdicts: tuple[ModelCapabilities, ModelCapabilities],
    connection: LlmConnection,
    bearer: BearerSource,
):
    main_caps, vision_caps = verdicts
    return build_agent(
        workspace,
        connection.model,
        connection.base_url,
        config,
        catalog=load_catalog(workspace),
        config=load_ayd_config(config),
        capabilities=main_caps,
        vision_capabilities=vision_caps,
        connection=connection,
        bearer=bearer,
    )


@dataclass(frozen=True, slots=True)
class PageConnectionActions:
    """The page side of ``ConnectionActions``: the event loop, the caches and the seams stay here."""

    config: str | None
    connection: LlmConnection
    bearer: BearerSource
    list_seam: ListModels | None  # the connection_list_models AppTest seam
    transport: Any  # the connection_transport AppTest seam

    def resolve(self, override: ConnectionOverride) -> LlmConnection:
        return resolve_connection(self.config, override)

    def list_models(self, candidate: LlmConnection) -> ModelListing:
        bearer = page_bearer(candidate)
        try:
            return run(cached_model_listing(candidate, bearer, list_models=self.list_seam))
        except BEARER_ERRORS as exc:  # E1 / E4 / E5: shown in the caption, never raised
            return ModelListing((), redacted_failure_caption(exc, bearer), 0.0)

    def refresh_models(self, candidate: LlmConnection) -> ModelListing:
        clear_model_listing_cache(candidate)
        return self.list_models(candidate)

    def test(self, candidate: LlmConnection) -> str:
        return run(run_connection_test(candidate, page_bearer(candidate), transport=self.transport))

    def renew(self) -> str | None:
        """None once a new token is cached (the auth row shows its time); else the caption."""
        before = self.bearer.describe().renewed_at
        try:
            self.bearer.renew(self.bearer.peek() or None, reason="manual")
        except TokenServiceError as exc:
            return f"renew failed: {redact_bearer(str(exc), self.bearer)}"
        if self.bearer.describe().renewed_at == before:  # H3: answered from the bearer's cache
            return NOTHING_RENEWED
        clear_model_listing_cache(self.connection)
        return None


def dialog_actions(
    config: str | None, connection: LlmConnection, bearer: BearerSource
) -> ConnectionActions:
    """The callbacks the dialog needs, bound to this page's connection and bearer."""
    return PageConnectionActions(
        config,
        connection,
        bearer,
        st.session_state.get("connection_list_models"),
        st.session_state.get("connection_transport"),
    )


_CODE_CHOICES = {"All code": "all", "Own code": "project", "Dependencies": "deps"}

with st.sidebar:
    st.markdown('<div class="side-label">Appearance</div>', unsafe_allow_html=True)
    render_appearance_toggle()

    st.markdown('<div class="side-label">Connection</div>', unsafe_allow_html=True)
    workspace = st.text_input("Workspace", os.environ.get("PYDOCS_WORKSPACE", ""))
    config_path = (
        st.text_input("pydocs config (optional)", os.environ.get("PYDOCS_CONFIG", "")) or None
    )
    connection = page_connection(config_path)
    bearer = page_bearer(connection)
    identity = connection_identity(connection)
    bearer_error = preflight_bearer(connection, bearer)
    vision_caps = page_vision_capabilities(connection, bearer, config_path, identity, bearer_error)
    render_connection_status_line(
        connection, bearer.describe(), vision_caps, bearer_error=bearer_error
    )
    # State-driven opener: AppTest always runs the full script, so a transient
    # `if st.button(...)` alone would never re-enter the dialog on the next run.
    if st.button("Connection", key=KEY_OPEN):
        st.session_state[STATE_DIALOG_OPEN] = True
    if st.session_state.get(STATE_DIALOG_OPEN):
        open_connection_dialog(
            connection,
            bearer.describe(),
            dialog_actions(config_path, connection, bearer),
            vision_text=vision_cell(vision_caps),
            bearer_error=bearer_error,
        )
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


def _refuse(question: str, message: str, bearer: BearerSource) -> None:
    """Fail loudly BEFORE any LLM call: nothing is sent, the question stays visible. The one
    boundary between a failure and the browser (H4): every text crosses ``redact_bearer``."""
    st.error(redact_bearer(message, bearer))
    st.info(f"Your question (not sent): {question}")
    st.stop()


if submission := st.chat_input(
    "Ask about your indexed projects…",
    accept_file="multiple",
    file_type=["png", "jpg", "jpeg", "webp", "gif"],
):
    question = submission.text or ""
    if bearer_error is not None:
        _refuse(question, bearer_error, bearer)
    if connection.model is None:  # design E19: before any tool or LLM construction
        _refuse(question, "No model chosen — open Connection and pick one.", bearer)
    ayd_cfg = load_ayd_config(config_path)
    images = _collect_images(list(submission.files or ()), ayd_cfg.images)
    # The VISION half decides, never the main verdict: under a separate vision
    # model the main model is blind by design while the images still have a reader.
    verdict = text_only_policy(images, vision_caps, ayd_cfg.multimodal, model=connection.model)
    if verdict is not None and verdict.kind == "reject":
        _refuse(question, verdict.message, bearer)  # spec §3.8: a policy check, not an exception
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
        # A fresh immutable snapshot per question — not shared across sessions.
        scope = {"project": project_pin, "package": package_pin, "code": code_pin}
        woven = weave_attachments(attached, question)
        st.session_state.attached = []
        try:
            agent, llm = get_agent(
                workspace,
                connection.model,
                connection.base_url,
                config_path,
                identity,
                connection,
                bearer,
            )
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
        except (PydocsMCPError, AgentArchitectureError, openai.APIError) as exc:
            # Auth failures arrive as BearerRejectedError already; the build-time
            # refusals (E13/E19) and the SDK's own errors cross the same boundary.
            _refuse(question, redacted_failure_caption(exc, bearer), bearer)
        st.markdown(answer)
    st.session_state.messages.append(("assistant", answer))
