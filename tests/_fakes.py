"""Shared in-memory Protocol fakes for tests.

Promotes Protocol fakes from inline-test-definitions to a single
canonical place so multiple test files don't drift on what
``DocumentTreeStore``'s shape actually is. Each new method on a
Protocol must be reflected here once, instead of in every test file's
copy of the fake.

Currently exports:
- :class:`InMemoryDocumentTreeStore` — records call history and keeps
  per-package payloads. Structurally satisfies
  :class:`~pydocs_mcp.storage.protocols.DocumentTreeStore`.

Tests that need to assert call ordering can either import the fake's
own ``calls`` list (each entry is a ``(method, payload)`` tuple) or
inject a shared audit list at construction time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Call:
    method: str
    payload: Any


@dataclass
class InMemoryDocumentTreeStore:
    """Structurally satisfies DocumentTreeStore — async methods only.

    Use directly in tests that exercise ``IndexingService`` /
    ``LookupService`` write+read interactions without touching SQLite.
    """

    calls: list[_Call] = field(default_factory=list)
    by_package: dict[str, list] = field(default_factory=dict)

    async def save_many(
        self, trees, *, package, uow=None,
    ) -> None:
        materialised = tuple(trees)
        self.calls.append(_Call("save_many", (package, materialised)))
        self.by_package.setdefault(package, []).extend(materialised)

    async def load(self, package, module):
        return None  # not exercised in write-side tests

    async def load_all_in_package(self, package):
        return {}

    async def exists(self, package, module):
        return False  # not exercised in write-side tests

    async def delete_for_package(self, package, *, uow=None) -> None:
        self.calls.append(_Call("delete_for_package", package))
        self.by_package.pop(package, None)

    async def delete_all(self, *, uow=None) -> None:
        self.calls.append(_Call("delete_all", None))
        self.by_package.clear()


__all__ = ("InMemoryDocumentTreeStore", "_Call")
