"""A seeded in-memory corpus for the ADR 0008 session-start pack.

Shared by the two channels that build the pack — the builder tests
(``tests/application/test_session_start_context.py``) and the ask-your-docs
agent-prompt injection tests
(``tests/harness/ask_your_docs/test_session_start_injection.py``) — so both
assert against the same card, module names and version inventory. Follows the
``tests/_src_layout_fixture.py`` precedent for a shared fixture module built
out of the named fakes in ``tests/_fakes.py``.
"""

from __future__ import annotations

from collections.abc import Callable

from pydocs_mcp.application.overview_service import OverviewService
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import Package, PackageOrigin
from pydocs_mcp.storage.protocols import UnitOfWork
from tests._fakes import (
    InMemoryDocumentTreeStore,
    InMemoryPackageStore,
    make_fake_uow_factory,
)

PROJECT_PACKAGE = "__project__"

#: Qualified name of the first seeded module — the one whose module-map bullet
#: carries the card's first pointer, so a test can pin the resolved call.
FIRST_MODULE_QNAME = "demo.subsystem_00.component_module"

_DEFAULT_DEPENDENCY_VERSIONS = {"numpy": "1.26.4", "fastapi": "0.111.0"}


def _package(name: str, version: str, origin: PackageOrigin) -> Package:
    return Package(
        name=name,
        version=version,
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=origin,
    )


def _module_node(qname: str) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=NodeKind.MODULE,
        source_path=qname.replace(".", "/") + ".py",
        start_line=1,
        end_line=10,
        text=f"Documentation prose for the {qname} module of the demo project.",
        content_hash="h",
    )


def build_session_start_fixture(
    *,
    dependency_versions: dict[str, str] | None = None,
    module_count: int = 3,
) -> tuple[Callable[[], UnitOfWork], OverviewService]:
    """A fake ``uow_factory`` + ``OverviewService`` over a small seeded corpus."""
    deps = dependency_versions if dependency_versions is not None else _DEFAULT_DEPENDENCY_VERSIONS
    packages = InMemoryPackageStore()
    packages.items[PROJECT_PACKAGE] = _package(PROJECT_PACKAGE, "0.1.0", PackageOrigin.PROJECT)
    for name, version in deps.items():
        packages.items[name] = _package(name, version, PackageOrigin.DEPENDENCY)
    trees = InMemoryDocumentTreeStore()
    trees.by_package[PROJECT_PACKAGE] = [
        _module_node(f"demo.subsystem_{i:02d}.component_module") for i in range(module_count)
    ]
    factory = make_fake_uow_factory(packages=packages, trees=trees)
    return factory, OverviewService(uow_factory=factory, scripts={})
