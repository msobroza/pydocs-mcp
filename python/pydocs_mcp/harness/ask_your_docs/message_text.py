"""The plain text of a chat message's ``content`` — one reading for every caller.

A leaf module (no harness import) so both ``activity_events`` (the panel) and
``first_turn`` (is a finalize reply an answer?) read text the SAME way: a string,
or the text of its ``{"type": "text"}`` blocks — never two disagreeing copies.

Example:
    >>> content_text([{"type": "text", "text": "a"}, {"type": "image_url"}, "b"])
    'ab'
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def content_text(content: Any) -> str:
    """Plain text of a message ``content``: a string, or the text of its text blocks."""
    if isinstance(content, str):
        return content
    blocks = content if isinstance(content, list) else []
    return "".join(_block_text(block) for block in blocks)


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    is_text = isinstance(block, Mapping) and block.get("type") == "text"
    return str(block.get("text") or "") if is_text else ""


__all__ = ("content_text",)
