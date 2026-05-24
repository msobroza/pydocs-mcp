"""Package.embedding_model dataclass field (Task 7 + AC-11)."""
from pydocs_mcp.models import Package, PackageOrigin


def test_package_defaults_to_none() -> None:
    p = Package(
        name="x",
        version="1.0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="",
        origin=PackageOrigin.DEPENDENCY,
    )
    assert p.embedding_model is None


def test_package_accepts_embedding_model() -> None:
    p = Package(
        name="x",
        version="1.0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="",
        origin=PackageOrigin.DEPENDENCY,
        embedding_model="BAAI/bge-small-en-v1.5",
    )
    assert p.embedding_model == "BAAI/bge-small-en-v1.5"
