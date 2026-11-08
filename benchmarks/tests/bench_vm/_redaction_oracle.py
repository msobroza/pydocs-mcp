"""The naive whole-buffer redaction the streaming ``ProviderKeyRedactor`` must match (spec AC-R1).

It finds every occurrence of every value at every position, groups the
occurrences that share a byte, and replaces each group by ``<NAME>``: the name
of the group's first occurrence (the one that starts first; among those, the
longest; then the name that sorts first). Every other byte is kept, in order,
except at the end of the stream: the longest end of it that is a partial value
(the first bytes of some value, fewer than all of them) is never kept. Its
bytes that no placeholder already covers become one ``WITHHELD_PARTIAL_KEY``
marker, even when they were benign text, and a group starting inside it gets
no placeholder of its own (spec "The end of the stream", fail-closed).
Quadratic on purpose: it is the reference, not an implementation.

A plain module with no package-relative import, so a fixture process run as a
script can import it too.
"""

from __future__ import annotations

from collections.abc import Mapping

# Spelled out here, not imported: the oracle restates the rule independently.
WITHHELD_PARTIAL_KEY = b"<partial key withheld>"


def _occurrences(data: bytes, keys: Mapping[str, str]) -> list[tuple[int, int, str, int]]:
    """``(start, -length, name, end)`` per occurrence, sorted so a group's first comes first."""
    found = []
    for name, value in keys.items():
        needle = value.encode("utf-8")
        for start in range(len(data) - len(needle) + 1):
            if data[start : start + len(needle)] == needle:
                found.append((start, -len(needle), name, start + len(needle)))
    return sorted(found)


def _partial_tail_start(data: bytes, keys: Mapping[str, str]) -> int:
    """Where the longest end of ``data`` that is a partial value starts; ``len(data)`` if none."""
    needles = [value.encode("utf-8") for value in keys.values()]
    for start in range(len(data)):
        tail = data[start:]
        if any(len(needle) > len(tail) and needle.startswith(tail) for needle in needles):
            return start
    return len(data)


def oracle_redact(data: bytes, keys: Mapping[str, str]) -> bytes:
    """``data`` with every group replaced by its placeholder and a partial-value end withheld."""
    held_from = _partial_tail_start(data, keys)
    pieces: list[bytes] = []
    written = 0
    group_end: int | None = None
    for start, _negative_length, name, end in _occurrences(data, keys):
        if group_end is not None and start < group_end:
            group_end = max(group_end, end)  # shares a byte: same group
            continue
        if start >= held_from:  # starts inside the withheld end
            break
        if group_end is not None:
            written = group_end
        pieces += [data[written:start], f"<{name}>".encode()]
        group_end = end
    if group_end is not None:
        written = group_end
    pieces.append(data[written:held_from])
    if max(written, held_from) < len(data):
        pieces.append(WITHHELD_PARTIAL_KEY)
    return b"".join(pieces)
