"""``ProviderKeyRedactor``: provider key values masked in a byte stream, however its reads split.

The pure seam of the 2026-11-08 amendment (AC-R1 to AC-R6). Every output is
checked against the naive whole-buffer oracle (``_redaction_oracle``), whose
end-of-stream rule withholds a partial value as one marker, never raw. Every
value here is synthetic: a FAKE constant, or a small-alphabet value built to
force partial matches and overlaps.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterable, Mapping

import pytest

from pydocs_eval.bench_vm.provider_key_redactor import ProviderKeyRedactor

from ._redaction_oracle import WITHHELD_PARTIAL_KEY, oracle_redact

_FAKE_ANTHROPIC = "sk-ant-FAKE-3f9a1c7e-redact-me-in-the-stream"
_FAKE_OPENROUTER = "sk-or-FAKE-b2d84e61-redact-me-in-the-stream"
_REALISTIC = {"ANTHROPIC_API_KEY": _FAKE_ANTHROPIC, "OPENROUTER_API_KEY": _FAKE_OPENROUTER}
# Small alphabet, values that overlap themselves and each other.
_OVERLAPPING = {"K_ABA": "aba", "K_ABBA": "abba", "K_BAB": "bab", "K_AABAA": "aabaa"}


def _redacted(keys: Mapping[str, str], chunks: Iterable[bytes]) -> bytes:
    redactor = ProviderKeyRedactor(keys)
    return b"".join(redactor.feed(chunk) for chunk in chunks) + redactor.finish()


def _split_at(data: bytes, cuts: Iterable[int]) -> list[bytes]:
    bounds = [0, *cuts, len(data)]
    return [data[a:b] for a, b in itertools.pairwise(bounds)]


def _random_chunks(rng: random.Random, data: bytes) -> list[bytes]:
    cuts = sorted(rng.sample(range(1, len(data)), rng.randint(0, len(data) - 1))) if data else []
    return _split_at(data, cuts)


# --- AC-R1: every chunking gives the whole-stream result ------------------------------


_REALISTIC_INPUTS = [
    f"config: key={_FAKE_ANTHROPIC}\nother={_FAKE_OPENROUTER}".encode(),
    f"{_FAKE_ANTHROPIC}{_FAKE_ANTHROPIC} sk-ant-FAKE-3f9a sk-".encode(),
    f"Authorization: Bearer {_FAKE_OPENROUTER}\r\n\xc3\xa9 no newline".encode(),
]


@pytest.mark.parametrize("data", _REALISTIC_INPUTS)
def test_every_two_chunk_split_of_realistic_output_matches_the_oracle(data: bytes) -> None:
    expected = oracle_redact(data, _REALISTIC)

    for cut in range(len(data) + 1):
        assert _redacted(_REALISTIC, _split_at(data, [cut])) == expected, cut


@pytest.mark.parametrize("data", [b"abababbab", b"aabaabaab", b"babbaabab", b"xabbaabax"])
def test_every_three_chunk_split_of_short_output_matches_the_oracle(data: bytes) -> None:
    expected = oracle_redact(data, _OVERLAPPING)

    for first, second in itertools.combinations_with_replacement(range(len(data) + 1), 2):
        assert _redacted(_OVERLAPPING, _split_at(data, [first, second])) == expected


@pytest.mark.parametrize("data", [*_REALISTIC_INPUTS, b"aabaababbabaabaa"])
def test_one_byte_chunks_match_the_oracle(data: bytes) -> None:
    keys = {**_REALISTIC, **_OVERLAPPING}

    assert _redacted(keys, [bytes([b]) for b in data]) == oracle_redact(data, keys)


@pytest.mark.parametrize("seed", range(8))
def test_seeded_random_chunkings_of_random_output_match_the_oracle(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(150):
        data = bytes(rng.choice(b"abx") for _ in range(rng.randint(0, 40)))

        assert _redacted(_OVERLAPPING, _random_chunks(rng, data)) == oracle_redact(
            data, _OVERLAPPING
        ), (seed, data)


@pytest.mark.parametrize("seed", range(4))
def test_seeded_random_chunkings_of_output_built_from_realistic_keys_match_the_oracle(
    seed: int,
) -> None:
    rng = random.Random(1000 + seed)
    pieces = [_FAKE_ANTHROPIC.encode(), _FAKE_OPENROUTER.encode(), b"sk-", b"sk-ant-", b" \n"]
    for _ in range(40):
        data = b"".join(rng.choice(pieces) for _ in range(rng.randint(0, 12)))

        assert _redacted(_REALISTIC, _random_chunks(rng, data)) == oracle_redact(data, _REALISTIC)


def test_a_value_split_at_every_one_of_its_positions_is_replaced() -> None:
    value = _FAKE_ANTHROPIC.encode()
    for cut in range(1, len(value)):
        out = _redacted(_REALISTIC, [b"before ", value[:cut], value[cut:], b" after"])

        assert out == b"before <ANTHROPIC_API_KEY> after", cut


# --- AC-R2: overlaps ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("keys", "data", "expected"),
    [
        # A value overlapping itself: one group, however long the chain.
        ({"K": "abab"}, b"x ababab y", b"x <K> y"),
        ({"K": "abab"}, b"abababababab", b"<K>"),
        # Two values overlapping.
        ({"A": "abcd", "B": "cdef"}, b"x abcdef y", b"x <A> y"),
        # A value inside another: the outer one starts first.
        ({"INNER": "bc", "OUTER": "abcd"}, b"abcd", b"<OUTER>"),
        ({"INNER": "bc", "OUTER": "abcd"}, b"xbcd", b"x<INNER>d"),
        # A value that starts another: the longer one names the group.
        ({"SHORT": "abc", "LONG": "abcdef"}, b"abcdef", b"<LONG>"),
        ({"SHORT": "abc", "LONG": "abcdef"}, b"abcdeX", b"<SHORT>deX"),
        # The group is named after its first occurrence, whatever key sorts first.
        ({"A": "bcd", "B": "abc"}, b"abcd", b"<B>"),
        # Two keys holding the same value: the name that sorts first.
        ({"Z_KEY": "same", "A_KEY": "same"}, b"-same-", b"-<A_KEY>-"),
    ],
)
def test_each_group_of_overlapping_occurrences_becomes_one_placeholder(
    keys: dict[str, str], data: bytes, expected: bytes
) -> None:
    assert oracle_redact(data, keys) == expected  # the oracle agrees with the rule
    for cut in range(len(data) + 1):
        assert _redacted(keys, _split_at(data, [cut])) == expected


def test_adjacent_occurrences_get_one_placeholder_each() -> None:
    data = (_FAKE_ANTHROPIC * 3).encode() + _FAKE_OPENROUTER.encode()

    assert _redacted(_REALISTIC, [data]) == (
        b"<ANTHROPIC_API_KEY><ANTHROPIC_API_KEY><ANTHROPIC_API_KEY><OPENROUTER_API_KEY>"
    )


# --- AC-R3: short output -------------------------------------------------------------------


def test_an_empty_stream_writes_nothing() -> None:
    assert _redacted(_REALISTIC, []) == b""
    assert _redacted(_REALISTIC, [b""]) == b""


def test_output_shorter_than_every_value_is_written_in_full() -> None:
    assert _redacted(_REALISTIC, [b"ok\n"]) == b"ok\n"


def test_a_stream_that_is_exactly_one_value_is_one_placeholder() -> None:
    assert _redacted(_REALISTIC, [_FAKE_OPENROUTER.encode()]) == b"<OPENROUTER_API_KEY>"


def test_a_stream_ending_in_a_partial_value_withholds_it_at_finish() -> None:
    redactor = ProviderKeyRedactor(_REALISTIC)

    assert redactor.feed(b"last words sk-ant-FAKE") == b"last words "
    assert redactor.finish() == WITHHELD_PARTIAL_KEY


# --- the end of the stream: a held partial value is withheld, fail-closed ------------------


@pytest.mark.parametrize("size", [1, 2, 12, len(_FAKE_ANTHROPIC) - 1])
def test_a_stream_cut_off_inside_a_value_writes_no_byte_of_it_however_it_was_read(
    size: int,
) -> None:
    data = b"text before the cut " + _FAKE_ANTHROPIC.encode()[:size]

    for cut in range(len(data) + 1):
        out = _redacted(_REALISTIC, _split_at(data, [cut]))

        assert out == b"text before the cut " + WITHHELD_PARTIAL_KEY, (size, cut)


def test_a_benign_end_that_could_begin_a_value_is_withheld_too() -> None:
    # "sk" is how every realistic value starts: the fidelity loss the rule accepts.
    assert _redacted(_REALISTIC, [b"assess the risk"]) == b"assess the ri" + WITHHELD_PARTIAL_KEY
    assert _redacted(_REALISTIC, [b"done sk-"]) == b"done " + WITHHELD_PARTIAL_KEY


@pytest.mark.parametrize(
    ("keys", "data", "expected"),
    [
        # A value overlapping itself: the group's placeholder, then its unfinished repeat.
        ({"K": "abab"}, b"x ababa", b"x <K>" + WITHHELD_PARTIAL_KEY),
        # A held end wholly covered by the placeholder already written adds nothing.
        ({"K": "abab"}, b"x ababab", b"x <K>"),
        # A value lying inside the held end of a longer one: the whole end is withheld.
        ({"SHORT": "abc", "LONG": "abcdef"}, b"x abcde", b"x " + WITHHELD_PARTIAL_KEY),
        # Two values overlapping, the second cut off by the end.
        ({"A": "abcd", "B": "cdef"}, b"x abcde", b"x <A>" + WITHHELD_PARTIAL_KEY),
    ],
)
def test_an_overlapping_partial_value_at_the_end_is_withheld_however_it_was_read(
    keys: dict[str, str], data: bytes, expected: bytes
) -> None:
    assert oracle_redact(data, keys) == expected  # the oracle agrees with the rule
    for first, second in itertools.combinations_with_replacement(range(len(data) + 1), 2):
        assert _redacted(keys, _split_at(data, [first, second])) == expected, (first, second)


def test_every_byte_before_a_withheld_end_is_kept_as_written() -> None:
    data = _ODD_BYTES + b" " + _FAKE_OPENROUTER.encode() + b" then sk-or-FA"

    for cut in range(len(data) + 1):
        out = _redacted(_REALISTIC, _split_at(data, [cut]))

        assert out == _ODD_BYTES + b" <OPENROUTER_API_KEY> then " + WITHHELD_PARTIAL_KEY, cut


# --- AC-R4: the hold-back ------------------------------------------------------------------


def _continuations(keys: Mapping[str, str], so_far: bytes) -> list[bytes]:
    """Ways the stream may go on, including completing each value ``so_far`` may be starting."""
    endings = [b"", b"z", b"\n", b"zzzzzzzz"]
    for value in (v.encode() for v in keys.values()):
        for size in range(1, len(value)):
            if so_far.endswith(value[:size]):
                endings += [value[size:], value[size:] + value]
    return endings


@pytest.mark.parametrize("keys", [_OVERLAPPING, _REALISTIC])
def test_a_feed_never_writes_a_byte_that_later_input_could_change(keys: dict[str, str]) -> None:
    rng = random.Random(7)
    alphabet = b"ab" if keys is _OVERLAPPING else b"sk-ant-FAKE3f9"
    for _ in range(200):
        so_far = bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        redactor = ProviderKeyRedactor(keys)
        written = b"".join(redactor.feed(chunk) for chunk in _random_chunks(rng, so_far))

        for ending in _continuations(keys, so_far):
            assert oracle_redact(so_far + ending, keys).startswith(written), (so_far, ending)


def test_a_feed_writes_everything_when_the_input_ends_in_no_partial_value() -> None:
    data = f"x {_FAKE_ANTHROPIC} y abab done\n".encode()
    redactor = ProviderKeyRedactor({**_REALISTIC, "K": "abab"})

    assert redactor.feed(data) == oracle_redact(data, {**_REALISTIC, "K": "abab"})


@pytest.mark.parametrize("size", [1, 5, len(_FAKE_ANTHROPIC) - 1])
def test_exactly_the_trailing_partial_value_is_held_until_ruled_in_or_out(size: int) -> None:
    redactor = ProviderKeyRedactor(_REALISTIC)
    partial = _FAKE_ANTHROPIC.encode()[:size]

    assert redactor.feed(b"plain text " + partial) == b"plain text "
    assert redactor.feed(b"") == b""  # nothing is released between feeds
    assert redactor.feed(b"") == b""
    # Ruled out by the next byte: written as it was.
    assert redactor.feed(b"\x00") == partial + b"\x00"


def test_held_bytes_that_complete_a_value_are_replaced() -> None:
    redactor = ProviderKeyRedactor(_REALISTIC)
    value = _FAKE_ANTHROPIC.encode()

    assert redactor.feed(b"text " + value[:10]) == b"text "
    assert redactor.feed(value[10:] + b"\n") == b"<ANTHROPIC_API_KEY>\n"


# --- AC-R5: verbatim -----------------------------------------------------------------------


_ODD_BYTES = b"\xff\xfe invalid utf-8 \x00 NUL \r CR \r\n CRLF \xc3\xa9 \xc3 no final newline"


def test_input_with_no_occurrence_comes_out_identical() -> None:
    for cut in range(len(_ODD_BYTES) + 1):
        assert _redacted(_REALISTIC, _split_at(_ODD_BYTES, [cut])) == _ODD_BYTES


def test_with_no_known_values_every_feed_returns_its_input_and_nothing_is_held() -> None:
    redactor = ProviderKeyRedactor({})

    assert redactor.feed(_ODD_BYTES) == _ODD_BYTES
    assert redactor.feed(b"sk-") == b"sk-"
    assert redactor.finish() == b""


# --- AC-R6: no value in its words ----------------------------------------------------------


def test_an_empty_value_is_refused_naming_the_key() -> None:
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY") as refused:
        ProviderKeyRedactor({"ANTHROPIC_API_KEY": _FAKE_ANTHROPIC, "OPENROUTER_API_KEY": ""})

    assert _FAKE_ANTHROPIC not in str(refused.value)


def test_feeding_after_finishing_is_refused_without_a_value() -> None:
    redactor = ProviderKeyRedactor(_REALISTIC)
    redactor.feed(b"held " + _FAKE_ANTHROPIC.encode()[:12])
    redactor.finish()

    with pytest.raises(RuntimeError, match="finish") as refused:
        redactor.feed(b"more")
    with pytest.raises(RuntimeError, match="finish"):
        redactor.finish()
    for value in _REALISTIC.values():
        assert value not in str(refused.value)
        assert value[:12] not in str(refused.value)


def test_the_repr_names_the_keys_and_holds_no_value() -> None:
    redactor = ProviderKeyRedactor(_REALISTIC)
    redactor.feed(b"held " + _FAKE_OPENROUTER.encode()[:15])

    shown = repr(redactor)

    assert "ANTHROPIC_API_KEY" in shown
    assert "OPENROUTER_API_KEY" in shown
    for value in _REALISTIC.values():
        assert value[:12] not in shown
