"""ISP split: read-only *Searchable views extracted; *Store extends them."""
from pydocs_mcp.storage.protocols import (
    GraphSearchable,
    MultiVectorSearchable,
    MultiVectorStore,
    ReferenceStore,
)


def test_multi_vector_store_is_a_searchable():
    # A MultiVectorStore is structurally a MultiVectorSearchable (read view).
    assert issubclass(MultiVectorStore, MultiVectorSearchable)


def test_reference_store_is_a_graph_searchable():
    assert issubclass(ReferenceStore, GraphSearchable)


def test_searchable_views_expose_only_read_methods():
    assert hasattr(MultiVectorSearchable, "score")
    assert not hasattr(MultiVectorSearchable, "add_vectors")
    assert hasattr(GraphSearchable, "find_callers")
    assert not hasattr(GraphSearchable, "save_many")
