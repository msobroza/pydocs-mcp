"""Chunk.content_hash auto-computation + compute_chunk_content_hash helper.

Per spec Decisions 1 + 3: SHA-256 of (package + \0 + module + \0 + title +
\0 + text + \0 + pipeline_hash). Auto-computed in __post_init__ when
content_hash is empty so Chunk(text="foo") just works in tests.
"""

from pydocs_mcp.models import Chunk, compute_chunk_content_hash


def test_compute_chunk_content_hash_is_deterministic() -> None:
    """Same inputs always produce the same hash."""
    h1 = compute_chunk_content_hash(
        package="demo",
        module="m",
        title="t",
        text="hello",
    )
    h2 = compute_chunk_content_hash(
        package="demo",
        module="m",
        title="t",
        text="hello",
    )
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digest


def test_compute_chunk_content_hash_includes_pipeline_hash() -> None:
    """Different pipeline_hash → different chunk_hash, even with same text."""
    base = compute_chunk_content_hash(
        package="demo",
        module="m",
        title="t",
        text="hello",
    )
    with_ph = compute_chunk_content_hash(
        package="demo",
        module="m",
        title="t",
        text="hello",
        pipeline_hash="some-pipeline-id",
    )
    assert base != with_ph


def test_compute_chunk_content_hash_null_byte_separators() -> None:
    """Null-byte separator prevents field-boundary collisions.

    package="a", module="bc" must NOT collide with package="ab", module="c".
    """
    h_a = compute_chunk_content_hash(
        package="a",
        module="bc",
        title="",
        text="",
    )
    h_b = compute_chunk_content_hash(
        package="ab",
        module="c",
        title="",
        text="",
    )
    assert h_a != h_b


def test_chunk_auto_computes_content_hash_when_unset() -> None:
    """Constructing Chunk(text="foo") without content_hash auto-computes it."""
    c = Chunk(
        text="hello",
        metadata={
            "package": "demo",
            "module": "m",
            "title": "t",
        },
    )
    assert c.content_hash != ""
    assert c.content_hash == compute_chunk_content_hash(
        package="demo",
        module="m",
        title="t",
        text="hello",
    )


def test_chunk_respects_explicit_content_hash() -> None:
    """If caller passes content_hash, __post_init__ does NOT overwrite it."""
    explicit = "deadbeef" * 8  # 64 hex chars
    c = Chunk(text="hello", content_hash=explicit, metadata={"package": "demo"})
    assert c.content_hash == explicit


def test_chunk_auto_compute_with_sparse_metadata_uses_empty_strings() -> None:
    """If metadata is missing keys, missing fields default to '' for hashing.

    Tests can construct Chunk(text="foo") with no metadata and still get a
    deterministic hash (lower entropy, but stable).
    """
    c = Chunk(text="hello")  # no metadata at all
    expected = compute_chunk_content_hash(
        package="",
        module="",
        title="",
        text="hello",
    )
    assert c.content_hash == expected
