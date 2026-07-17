"""flatten_to_chunks emits start_line/end_line spans (schema v15).

``DocumentNode`` already computes the spans; the flattener carried only
``source_path`` into ``Chunk.metadata``, so the persisted chunk lost the
line range the tool-contracts items[] fields need.
"""

from __future__ import annotations

from pydocs_mcp.extraction.model import DocumentNode, NodeKind, flatten_to_chunks
from pydocs_mcp.models import ChunkFilterField


def _node(start_line: int, end_line: int) -> DocumentNode:
    return DocumentNode(
        node_id="pkg.mod",
        qualified_name="pkg.mod",
        title="mod",
        kind=NodeKind.MODULE,
        source_path="pkg/mod.py",
        start_line=start_line,
        end_line=end_line,
        text="doc",
        content_hash="deadbeef",
    )


def test_flatten_emits_line_spans_in_metadata() -> None:
    chunks = flatten_to_chunks(_node(3, 42), "pkg")
    assert len(chunks) == 1
    md = chunks[0].metadata
    assert md[ChunkFilterField.START_LINE.value] == 3
    assert md[ChunkFilterField.END_LINE.value] == 42
    assert md[ChunkFilterField.SOURCE_PATH.value] == "pkg/mod.py"


def test_span_filter_fields_are_canonical_keys() -> None:
    assert ChunkFilterField.START_LINE.value == "start_line"
    assert ChunkFilterField.END_LINE.value == "end_line"
