"""``ProviderKeyRedactor``: the run's provider key values masked in a byte stream, as it arrives.

The one redaction rule of the bench VM (ADR 0026, amendment 2026-11-08). It
covers every stream the supervisor runs for the benchmark: the child's and the
kind pre-flight's stdout and stderr, and the pre-flight's tail the supervisor
logs, which is cut from the redacted bytes.

- An *occurrence* is any run of bytes equal to a known value (a key's UTF-8
  bytes), at any position. Occurrences that share a byte form one *group*,
  replaced by one ``<NAME>`` placeholder, NAME being the key of the group's
  first occurrence: the one that starts first; among those, the longest;
  then the name that sorts first. Occurrences that only touch are separate.
- Every other byte is kept as written: no decoding, so invalid UTF-8, NUL, CR,
  CRLF and a missing last newline pass through untouched.
- However the reads cut the stream, the bytes released equal the redaction of
  the whole stream. A feed holds back only the longest end of the input so far
  that could still begin a value (a *partial value*, at most the longest
  value's length minus one), and releases it only when later bytes rule it in
  or out, or at ``finish``, which writes it as it was. Never on a timer: a
  timed release could write a value's first half, then its second.

Proof sketch of why nothing released can change: a future occurrence must
start inside the held end (anything earlier would already be a partial value
or complete), so it can neither name a group that starts earlier nor reach a
byte released before it; it can only join a group whose placeholder was
already written, which it then extends without a second placeholder.

The type does no I/O, keeps no copy of what it released (only the held end,
plus the bytes of an already written group that a later occurrence could still
overlap), and costs linear time in the output: it searches with the built-in
``bytes.find``. Its errors and its ``repr`` name keys, never values.
"""

from __future__ import annotations

from collections.abc import Mapping


class ProviderKeyRedactor:
    """Feed a stream's chunks, write what each feed returns, then write what ``finish`` returns."""

    __slots__ = ("_covered_until", "_finished", "_longest", "_values", "_window", "_window_start")

    def __init__(self, provider_keys: Mapping[str, str]) -> None:
        for name, value in provider_keys.items():
            if not value:
                raise ValueError(
                    f"provider key {name}: got an empty value, expected a non-empty secret"
                )
        # Sorted by name: equal values tie-break on the name that sorts first.
        self._values = tuple(sorted((n, v.encode("utf-8")) for n, v in provider_keys.items()))
        self._longest = max((len(value) for _, value in self._values), default=0)
        # The bytes from ``_window_start`` (a stream offset) on: the held end,
        # behind the context a later occurrence could still overlap.
        self._window = b""
        self._window_start = 0
        # The stream offset where the last placeholder's group ends.
        self._covered_until = 0
        self._finished = False

    def __repr__(self) -> str:
        return f"ProviderKeyRedactor(keys={[name for name, _ in self._values]!r})"

    def feed(self, chunk: bytes) -> bytes:
        """The bytes that may be written now, ``chunk`` included as far as it is determined."""
        self._refuse_when_finished()
        if not self._values:
            return bytes(chunk)
        window = self._window + chunk
        return self._release(window, self._partial_value_start(window))

    def finish(self) -> bytes:
        """The rest at the end of the stream: a held partial value is not a value, so as it was."""
        self._refuse_when_finished()
        self._finished = True
        rest = self._release(self._window, len(self._window))
        self._window = b""
        return rest

    def _refuse_when_finished(self) -> None:
        if self._finished:
            raise RuntimeError(
                f"{self!r} already ran finish(): expected a new redactor for a new stream"
            )

    def _partial_value_start(self, window: bytes) -> int:
        """Where the longest end of ``window`` that begins some value starts; its length if none."""
        for start in range(max(0, len(window) - self._longest + 1), len(window)):
            end = window[start:]
            if any(len(value) > len(end) and value.startswith(end) for _, value in self._values):
                return start
        return len(window)

    def _occurrences(self, window: bytes) -> list[tuple[int, int, str]]:
        """``(start, -length, name)`` per occurrence in ``window``: a group's first sorts first."""
        found = []
        for name, value in self._values:
            at = window.find(value)
            while at != -1:
                found.append((at, -len(value), name))
                at = window.find(value, at + 1)
        return sorted(found)

    def _release(self, window: bytes, hold_from: int) -> bytes:
        """Write ``window`` up to ``hold_from`` (and through any group started before it)."""
        pieces: list[bytes] = []
        written = covered = max(0, self._covered_until - self._window_start)
        for start, negative_length, name in self._occurrences(window):
            if start < covered:  # joins the group whose placeholder is written
                covered = max(covered, start - negative_length)
                continue
            if start >= hold_from:  # a later occurrence could still change it
                break
            pieces += [window[max(written, covered) : start], f"<{name}>".encode()]
            written = covered = start - negative_length
        pieces.append(window[max(written, covered) : hold_from])
        self._covered_until = self._window_start + covered
        self._window, self._window_start = window[hold_from:], self._window_start + hold_from
        return b"".join(pieces)
