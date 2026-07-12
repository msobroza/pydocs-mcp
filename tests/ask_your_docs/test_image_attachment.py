"""ImageAttachment value object + validation + message flow (spec §3.2/§3.6).

The value-object tests need no heavy deps; the ask()-flow tests (AC18-AC20,
added with commit 4) importorskip langchain_core like the sibling modules.
"""

from __future__ import annotations

import base64

import pytest

from pydocs_mcp.ask_your_docs.attachments import (
    _ALLOWED_IMAGE_TYPES,
    ImageAttachment,
    validate_attachment,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import ImagesConfig

_PNG_B64 = base64.b64encode(b"fake-png-bytes").decode()


def test_as_content_block_shape() -> None:
    """AC17: a well-formed OpenAI image_url data-URI content block."""
    att = ImageAttachment(name="shot.png", media_type="image/png", data_b64=_PNG_B64)
    block = att.as_content_block()
    assert block == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{_PNG_B64}"},
    }


def test_validate_rejects_oversized_payload() -> None:
    """AC17: over-max_bytes payloads are rejected naming value + limit."""
    cfg = ImagesConfig(max_bytes=10)
    att = ImageAttachment(name="big.png", media_type="image/png", data_b64=_PNG_B64)
    with pytest.raises(ValueError, match=r"big\.png.*10"):
        validate_attachment(att, cfg)


def test_validate_rejects_disallowed_media_type() -> None:
    """AC17: a non-allowlisted media type is rejected naming the offender."""
    att = ImageAttachment(name="doc.pdf", media_type="application/pdf", data_b64=_PNG_B64)
    with pytest.raises(ValueError, match="application/pdf"):
        validate_attachment(att, ImagesConfig())


def test_validate_accepts_all_allowlisted_types() -> None:
    for media_type in _ALLOWED_IMAGE_TYPES:
        att = ImageAttachment(name="x", media_type=media_type, data_b64=_PNG_B64)
        validate_attachment(att, ImagesConfig())  # must not raise


def test_weave_attachments_reexported_from_agent() -> None:
    """AC16 guard: the import path used by app.py and the existing tests
    survives the move into attachments.py."""
    pytest.importorskip("langchain_core")
    from pydocs_mcp.ask_your_docs.agent import weave_attachments as via_agent
    from pydocs_mcp.ask_your_docs.attachments import weave_attachments as via_attachments

    assert via_agent is via_attachments


# ── ask() message flow with images (spec §3.6 — AC18-AC20) ──


class _RecordingAgent:
    """Records every ainvoke payload; replies with a fixed answer."""

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def ainvoke(self, payload: dict) -> dict:
        pytest.importorskip("langchain_core")
        from langchain_core.messages import AIMessage

        self.payloads.append(payload)
        return {"messages": [*payload["messages"], AIMessage("ANSWER")]}


def _att(name: str = "shot.png") -> ImageAttachment:
    return ImageAttachment(name=name, media_type="image/png", data_b64=_PNG_B64)


def test_ask_without_images_sends_plain_str() -> None:
    """AC18: images=() default — byte-for-byte today's scope_prefix + question."""
    pytest.importorskip("langgraph")
    import asyncio

    from pydocs_mcp.ask_your_docs.agent import ask

    agent = _RecordingAgent()
    history: list = []
    asyncio.run(ask(agent, history, "q1", scope={"project": "p"}))
    sent = agent.payloads[0]["messages"][-1]
    assert sent.content == "[pinned scope: project=p] q1"
    assert isinstance(sent.content, str)


def test_ask_with_image_sends_text_then_image_blocks() -> None:
    """AC18: with one image — [text block, image block] in that order."""
    pytest.importorskip("langgraph")
    import asyncio

    from pydocs_mcp.ask_your_docs.agent import ask

    agent = _RecordingAgent()
    asyncio.run(ask(agent, [], "what is this?", images=(_att(),)))
    content = agent.payloads[0]["messages"][-1].content
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "what is this?"}
    assert content[1]["type"] == "image_url"


def test_ask_history_stays_text_with_placeholder_and_trims() -> None:
    """AC19: history contains only str contents; the user entry carries the
    [attached images: ...] placeholder; max_history trim unchanged."""
    pytest.importorskip("langgraph")
    import asyncio

    from pydocs_mcp.ask_your_docs.agent import ask

    agent = _RecordingAgent()
    history: list = []
    asyncio.run(ask(agent, history, "look", images=(_att("a.png"), _att("b.png"))))
    assert all(isinstance(m.content, str) for m in history)
    assert history[0].content == "look [attached images: a.png, b.png]"
    for i in range(9):
        asyncio.run(ask(agent, history, f"q{i}"))
    assert len(history) == 8  # max_history default


def test_reformulate_history_lines_never_show_list_reprs() -> None:
    """AC20: the _history_line hardening — content blocks flatten to text +
    [image] markers; REWRITE_PROMPT never sees a Python-list repr."""
    pytest.importorskip("langgraph")
    from langchain_core.messages import HumanMessage

    from pydocs_mcp.ask_your_docs.agent import _history_line

    plain = HumanMessage("plain")
    blocks = HumanMessage(
        content=[
            {"type": "text", "text": "the question"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ]
    )
    assert _history_line(plain) == "human: plain"
    line = _history_line(blocks)
    assert "the question" in line and "[image]" in line
    assert "{" not in line and "image_url" not in line


# ── text-only degradation policy (spec §3.8 — AC21-AC22) ──


def test_reject_policy_blocks_before_any_llm_call() -> None:
    """AC21: reject → actionable message naming model, source, override path."""
    from pydocs_mcp.ask_your_docs.attachments import text_only_policy
    from pydocs_mcp.ask_your_docs.multimodal import ModelCapabilities
    from pydocs_mcp.retrieval.config.ask_your_docs_models import MultimodalConfig

    verdict = text_only_policy(
        (_att("x.png"),),
        ModelCapabilities(multimodal=False, source="static"),
        MultimodalConfig(),
        model="gpt-3.5-turbo",
    )
    assert verdict is not None and verdict.kind == "reject"
    assert "gpt-3.5-turbo" in verdict.message
    assert "source=static" in verdict.message
    assert "ask_your_docs.multimodal.detection.override" in verdict.message


def test_describe_policy_prefixes_and_drops_blocks() -> None:
    """AC22: describe → cannot-see note with names; images NOT attached."""
    from pydocs_mcp.ask_your_docs.attachments import text_only_policy
    from pydocs_mcp.ask_your_docs.multimodal import ModelCapabilities
    from pydocs_mcp.retrieval.config.ask_your_docs_models import MultimodalConfig

    verdict = text_only_policy(
        (_att("a.png"),),
        ModelCapabilities(multimodal=False, source="default"),
        MultimodalConfig(text_only_fallback="describe"),
        model="m",
    )
    assert verdict is not None and verdict.kind == "describe"
    assert "a.png" in verdict.message and "cannot see" in verdict.message


def test_policy_none_when_capable_or_no_images() -> None:
    from pydocs_mcp.ask_your_docs.attachments import text_only_policy
    from pydocs_mcp.ask_your_docs.multimodal import ModelCapabilities
    from pydocs_mcp.retrieval.config.ask_your_docs_models import MultimodalConfig

    vision = ModelCapabilities(multimodal=True, source="static")
    text = ModelCapabilities(multimodal=False, source="default")
    assert text_only_policy((_att(),), vision, MultimodalConfig(), model="m") is None
    assert text_only_policy((), text, MultimodalConfig(), model="m") is None


# ── session image store (reinspect extension) ──


def test_update_image_store_appends_and_evicts_oldest() -> None:
    from pydocs_mcp.ask_your_docs.attachments import update_image_store

    store: dict[str, ImageAttachment] = {}
    for i in range(5):
        update_image_store(store, (_att(f"img{i}.png"),), retention=3)
    assert list(store) == ["img2.png", "img3.png", "img4.png"]  # oldest evicted


def test_update_image_store_reattach_refreshes_position() -> None:
    from pydocs_mcp.ask_your_docs.attachments import update_image_store

    store: dict[str, ImageAttachment] = {}
    update_image_store(store, (_att("a.png"), _att("b.png")), retention=3)
    update_image_store(store, (_att("a.png"),), retention=3)  # re-attach a
    update_image_store(store, (_att("c.png"), _att("d.png")), retention=3)
    assert list(store) == ["a.png", "c.png", "d.png"]  # b evicted, a survived


def test_update_image_store_zero_retention_disables() -> None:
    from pydocs_mcp.ask_your_docs.attachments import update_image_store

    store: dict[str, ImageAttachment] = {}
    update_image_store(store, (_att("a.png"),), retention=0)
    assert store == {}


def test_describe_note_rides_transient_note_not_history() -> None:
    """AC22 wiring (review fix): the cannot-see note attaches AFTER
    reformulation via ask(transient_note=...) — it reaches the sent message
    deterministically and never persists into history."""
    pytest.importorskip("langgraph")
    import asyncio

    from pydocs_mcp.ask_your_docs.agent import ask

    agent = _RecordingAgent()
    history: list = []
    asyncio.run(
        ask(agent, history, "what does the image show?", transient_note="[note: cannot see]")
    )
    sent = agent.payloads[0]["messages"][-1].content
    assert sent.startswith("[note: cannot see]\n")
    assert "what does the image show?" in sent
    assert history[0].content == "what does the image show?"  # bare — no note
