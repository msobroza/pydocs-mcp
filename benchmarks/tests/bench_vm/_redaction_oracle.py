"""The naive whole-buffer redaction the streaming ``ProviderKeyRedactor`` must match (spec AC-R1).

It finds every occurrence of every value at every position, groups the
occurrences that share a byte, and replaces each group by ``<NAME>``: the name
of the group's first occurrence (the one that starts first; among those, the
longest; then the name that sorts first). Every other byte is kept, in order.
Quadratic on purpose: it is the reference, not an implementation.

A plain module with no package-relative import, so a fixture process run as a
script can import it too.
"""

from __future__ import annotations

from collections.abc import Mapping


def _occurrences(data: bytes, keys: Mapping[str, str]) -> list[tuple[int, int, str, int]]:
    """``(start, -length, name, end)`` per occurrence, sorted so a group's first comes first."""
    found = []
    for name, value in keys.items():
        needle = value.encode("utf-8")
        for start in range(len(data) - len(needle) + 1):
            if data[start : start + len(needle)] == needle:
                found.append((start, -len(needle), name, start + len(needle)))
    return sorted(found)


def oracle_redact(data: bytes, keys: Mapping[str, str]) -> bytes:
    """``data`` with every group of occurrences replaced by its placeholder."""
    pieces: list[bytes] = []
    written = 0
    group_end: int | None = None
    for start, _negative_length, name, end in _occurrences(data, keys):
        if group_end is not None and start < group_end:
            group_end = max(group_end, end)  # shares a byte: same group
            continue
        if group_end is not None:
            written = group_end
        pieces += [data[written:start], f"<{name}>".encode()]
        group_end = end
    if group_end is not None:
        written = group_end
    pieces.append(data[written:])
    return b"".join(pieces)
