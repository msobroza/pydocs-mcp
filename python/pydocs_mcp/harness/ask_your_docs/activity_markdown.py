"""Safe one-line markdown for the activity panel (PROPOSAL §3): escape first, icon second.

A step's line carries model text (tool arguments, reasoning, vision facts), so
:func:`plain_markdown` escapes it first. Only then does :func:`iconed_markdown` put a
trusted Material icon from ``activity_labels`` in front. Putting the icon first also means
a line never STARTS with model text, so a leading "1." or "-" in that text cannot open a
list.

Example:
    >>> iconed_markdown(":material/map:", "Got an overview of fastapi")
    ':material/map: Got an overview of fastapi'
"""

from __future__ import annotations

import re

from pydocs_mcp.harness.ask_your_docs.activity_labels import THINKING_ICON, tool_icon
from pydocs_mcp.harness.ask_your_docs.activity_trace import StepStatus, ThinkingStep, ToolStep

_TEASER_CHARS = 60
_GLYPH = {StepStatus.RUNNING: "●", StepStatus.OK: "✓", StepStatus.FAILED: "✗"}
# An em space. Markdown collapses a run of ASCII spaces, so the old three-space gap between
# a label and its outcome would shrink to one once the line became markdown. (st.text
# keeps the spaces but cannot show an icon.)
_TAIL_GAP = "\u2003"
# Markdown that could restyle or link a label; everything else in our labels is inert.
# "&" is escaped because micromark decodes a named entity ("&colon;") into plain text AFTER
# Streamlit's raw-string icon checks, which let "&colon;streamlit&colon;" draw the logo.
_MARKDOWN_SPECIALS = re.compile(r"([\\`*_\[\]<>#|~$&])")
# Streamlit 1.59's markdown acts on text that a backslash escape cannot reach. It rewrites
# ":material/" on the RAW string. It finds icon, logo and emoji ":name:" shortcodes, and
# ":red[" style directives, in parsed text nodes. remark-gfm links a bare "https://",
# "www." or "a@b" there too. A zero-width space splits each trigger. Streamlit's own
# validate_material_icon uses the same character for the same reason.
# This runs BEFORE escaping, because the "\_" that escaping adds hid ":material/thumb_up:"
# from the lookahead. Trade-off: Streamlit's emoji name set lives only in its JS bundle,
# so ANY ":name:" is split. Colon text like "std::string" copied out of the panel then
# carries an invisible U+200B. Letting model text draw an image would cost more.
_INERT_TRIGGER = re.compile(
    r"""
    :(?=[\w+/-]+(?::|\[))   # ":material/x:", ":streamlit:", ":smile:", directive ":red["
    | :(?=//)               # "https://" autolink
    | (?<=www)(?=\.)        # "www." autolink
    | (?=@)                 # "name@host" email autolink
    """,
    re.IGNORECASE | re.VERBOSE,
)
_ZERO_WIDTH_SPACE = "\u200b"
_LINE_BREAKS = re.compile(r"[\r\n]+")


def plain_markdown(text: str) -> str:
    """``text`` with its markdown, shortcodes, directives and autolinks defused (renders as typed)."""
    defused = _INERT_TRIGGER.sub(lambda match: match.group(0) + _ZERO_WIDTH_SPACE, text)
    return _MARKDOWN_SPECIALS.sub(r"\\\1", defused)


def iconed_markdown(icon: str, text: str) -> str:
    """One line: the trusted ``icon``, then ``text`` escaped (never the other way round)."""
    # A newline would let the next line start a list, a heading or a code block.
    return f"{icon} {plain_markdown(_LINE_BREAKS.sub(' ', text))}"


def tool_step_markdown(step: ToolStep) -> str:
    """ "<icon> <status glyph> <label> <duration · outcome>", for live and final panels."""
    label = step.running_label if step.status is StepStatus.RUNNING else step.label
    duration = f"{step.duration_s:.1f} s" if step.duration_s is not None else ""
    tail = " · ".join(part for part in (duration, step.outcome) if part)
    line = f"{_GLYPH[step.status]} {label}" + (f"{_TAIL_GAP}{tail}" if tail else "")
    return iconed_markdown(tool_icon(step.name), line)


def thinking_teaser_markdown(step: ThinkingStep) -> str:
    """':material/psychology: Thinking · 1.2 s · "first words…"'."""
    first = " ".join(step.text.split())
    teaser = first if len(first) <= _TEASER_CHARS else f"{first[:_TEASER_CHARS]}…"
    took = _seconds_between(step.started_at, step.ended_at)
    words = " · ".join(part for part in ("Thinking", took, f'"{teaser}"') if part)
    return iconed_markdown(THINKING_ICON, words)


def _seconds_between(start: float | None, end: float | None) -> str:
    return f"{end - start:.1f} s" if start is not None and end is not None else ""


__all__ = (
    "iconed_markdown",
    "plain_markdown",
    "thinking_teaser_markdown",
    "tool_step_markdown",
)
