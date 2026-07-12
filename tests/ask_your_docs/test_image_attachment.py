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
