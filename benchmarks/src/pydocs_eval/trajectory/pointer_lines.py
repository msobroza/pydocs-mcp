"""Tolerant parser for the resolved follow-up call lines a response renders.

A response ends with ready-made follow-up calls — pointers — rendered as
arrow-prefixed call lines in the tool surface's own syntax::

    → get_symbol(target="pkg.mod.Cls")
    together: → get_references(target="pkg.mod.Cls", direction="callers")
    then: → get_symbol(target="pkg.mod.Cls", depth="source")

This module turns that text back into comparable :class:`PointerCall` values so
the metric layer can ask whether a recorded tool call is one an earlier response
already offered.

It is deliberately TOLERANT, because it reads text that changes while the metric
must keep running: anything before the arrow is ignored (the group labels above
are one such prefix), several pointers may share a line, and a call line it
cannot decode is skipped rather than raised on. It reads only the MCP call form
— the command-line rendering of the same pointer names a shell command, which no
recorded tool call can equal.

The eval package keeps a zero-``pydocs_mcp``-import floor (ADR 0009 placement),
so the grammar is mirrored here rather than imported; a renderer change that
alters the line shape has to be mirrored in this module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

# ``→ tool(arguments)`` anywhere in the text: any prefix before the arrow is
# ignored, and excluding parentheses from the argument run keeps one match
# inside one call — the grammar never nests them.
_POINTER_CALL_RE = re.compile(r"→\s*([A-Za-z_]\w*)\(([^()]*)\)")

# ``name="value"`` or ``name=["a", "b"]`` — the only two argument shapes the
# pointer grammar renders.
_ARGUMENT_RE = re.compile(r'([A-Za-z_]\w*)\s*=\s*("(?:[^"\\]|\\.)*"|\[[^\]]*\])')

# The prefix a client stamps on tools an MCP server provides (``mcp__srv__tool``);
# pointers name the bare tool.
_MCP_TOOL_PREFIX = "mcp__"

ArgumentValue = str | tuple[str, ...]


def normalize_tool_name(tool: str) -> str:
    """The bare tool name: ``mcp__pydocs-mcp__get_symbol`` → ``get_symbol``.

    A name without the MCP prefix is returned unchanged, so a bare client tool
    (``Read``, ``Grep``) keeps its own name.
    """
    if not tool.startswith(_MCP_TOOL_PREFIX):
        return tool
    return tool.rsplit("__", 1)[-1]


@dataclass(frozen=True, slots=True)
class PointerCall:
    """One follow-up call a response offered: a tool plus the arguments it named.

    ``arguments`` is a name-sorted tuple of ``(name, value)`` pairs, so two
    renderings of the same call compare and hash equal whatever order the
    renderer wrote them in.
    """

    tool: str
    arguments: tuple[tuple[str, ArgumentValue], ...]

    def matches(self, tool: str, args: Mapping[str, object]) -> bool:
        """True when a recorded call IS this pointer.

        The tool names must be equal (an ``mcp__server__`` prefix is stripped
        from the recorded one first) and every argument the pointer named must
        be present on the call with the same value. Arguments the call adds and
        the pointer never named — a project selector, a limit — do not break the
        match: a pointer is what the model was offered, not a whole call schema.

        Example:
            >>> pointer = PointerCall("get_symbol", (("target", "a.B"),))
            >>> pointer.matches("get_symbol", {"target": "a.B", "project": "p"})
            True
        """
        if normalize_tool_name(tool) != self.tool:
            return False
        return all(_recorded_value(args.get(name)) == value for name, value in self.arguments)


def parse_pointer_calls(text: str) -> tuple[PointerCall, ...]:
    """Every pointer rendered in ``text``, in the order it appears.

    Duplicates are kept — deduplicating is the caller's decision.

    Example:
        >>> parse_pointer_calls('then: → get_symbol(target="a.B")')
        (PointerCall(tool='get_symbol', arguments=(('target', 'a.B'),)),)
    """
    parsed = (_pointer_call(m.group(1), m.group(2)) for m in _POINTER_CALL_RE.finditer(text))
    return tuple(call for call in parsed if call is not None)


def _pointer_call(tool: str, argument_text: str) -> PointerCall | None:
    """Build one pointer, or ``None`` when its argument run does not fully decode.

    Every character of the run has to be accounted for. An argument the grammar
    never renders would otherwise be dropped SILENTLY, leaving an argument-less
    pointer that matches every call of that tool and inflates the followed rate.
    """
    arguments: list[tuple[str, ArgumentValue]] = []
    leftover = argument_text
    for match in _ARGUMENT_RE.finditer(argument_text):
        value = _decode_literal(match.group(2))
        if value is None:
            return None  # tolerant: an undecodable argument drops the whole pointer
        arguments.append((match.group(1), value))
        leftover = leftover.replace(match.group(0), "", 1)
    if leftover.strip(" \t,"):
        return None
    ordered = tuple(sorted(arguments, key=lambda pair: pair[0]))
    return PointerCall(tool=normalize_tool_name(tool), arguments=ordered)


def _decode_literal(literal: str) -> ArgumentValue | None:
    """Decode a rendered ``"string"`` / ``["a", "b"]`` literal (JSON-compatible)."""
    try:
        decoded = json.loads(literal)
    except ValueError:
        return None
    if isinstance(decoded, str):
        return decoded
    if isinstance(decoded, list) and all(isinstance(value, str) for value in decoded):
        return tuple(decoded)
    return None


def _recorded_value(raw: object) -> ArgumentValue | None:
    """Normalize one recorded argument value into the pointer vocabulary."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (list, tuple)):
        return tuple(str(item) for item in raw)
    return None
