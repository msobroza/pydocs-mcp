"""Pin the slice of :class:`DocumentNode` that :class:`LookupService` depends on.

LookupService and DocumentNode live in different layers (``application/``
and ``extraction/model/`` respectively). Commit ``32ecb64`` fixed a
production AttributeError caused by exactly this kind of cross-layer
drift: LookupService called ``tree.find_node_by_qualified_name(...)`` /
``tree.to_pageindex_json()`` on a DocumentNode that didn't expose them.

The MagicMock-based tests in ``tests/application/test_lookup_service.py``
patch these methods, so they keep passing even if the real DocumentNode
loses or renames the method. This file is the structural seam those
tests don't cover — it asserts the real DocumentNode satisfies the
exact API surface LookupService consumes.

Three guard rails:

1. Structural Protocol (``@runtime_checkable`` ``isinstance``) — catches
   attribute / method removal (e.g. removing ``node_id`` or renaming
   ``find_node_by_qualified_name`` to ``find_node``).
2. Signature pinning via ``inspect.signature`` — catches drift in the
   public parameter names ``to_pageindex_json()`` and
   ``find_node_by_qualified_name(target)`` declare.
3. Smoke call — calls each method on a real instance to confirm runtime
   shape (Protocol doesn't enforce return-type structure).

If LookupService ever needs a new attribute/method on DocumentNode, add
it here first; that forces the contract to evolve deliberately rather
than via silent code drift.
"""
from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydocs_mcp.extraction.model import DocumentNode, NodeKind


@runtime_checkable
class DocumentNodeUsedByLookupService(Protocol):
    """Subset of :class:`DocumentNode` consumed by LookupService.

    Anything outside this Protocol is free to evolve. Anything inside
    must stay stable — or this test breaks, signalling drift.
    """

    node_id: str
    extra_metadata: Mapping[str, Any]
    # ``kind`` is read as ``node.kind != "class"`` in lookup_service.py.
    # NodeKind is a StrEnum so it satisfies that comparison; declaring
    # the attribute as ``Any`` keeps the Protocol agnostic to that
    # representation choice (str vs StrEnum vs plain str literal).

    def to_pageindex_json(self) -> dict[str, Any]: ...
    def find_node_by_qualified_name(
        self, target: str,
    ) -> DocumentNodeUsedByLookupService | None: ...


def _real_node() -> DocumentNode:
    """Minimal valid DocumentNode for contract tests."""
    return DocumentNode(
        node_id="pkg.mod",
        qualified_name="pkg.mod",
        title="mod",
        kind=NodeKind.MODULE,
        source_path="pkg/mod.py",
        start_line=1,
        end_line=10,
        text="",
        content_hash="h",
    )


# Guard 1 — structural Protocol check


def test_real_document_node_satisfies_lookup_service_protocol() -> None:
    """isinstance check confirms node_id / extra_metadata / both methods
    are present. Catches outright removal or rename."""
    assert isinstance(_real_node(), DocumentNodeUsedByLookupService)


# Guard 2 — signature pinning


def test_to_pageindex_json_signature_matches_contract() -> None:
    """to_pageindex_json must accept ``self`` only (LookupService calls
    it as ``node.to_pageindex_json()`` with no args). Catches the case
    where someone adds a required parameter that would break the call."""
    sig = inspect.signature(DocumentNode.to_pageindex_json)
    param_names = [p.name for p in sig.parameters.values()]
    assert param_names == ["self"], (
        f"to_pageindex_json signature drifted to {param_names}; "
        f"LookupService calls it with no arguments at lookup_service.py:93,110"
    )


def test_find_node_by_qualified_name_signature_matches_contract() -> None:
    """find_node_by_qualified_name must accept ``(self, target: str)``.
    LookupService calls it as ``tree.find_node_by_qualified_name(target)``
    at lookup_service.py:105."""
    sig = inspect.signature(DocumentNode.find_node_by_qualified_name)
    param_names = [p.name for p in sig.parameters.values()]
    assert param_names == ["self", "target"], (
        f"find_node_by_qualified_name signature drifted to {param_names}; "
        f"LookupService calls it as `tree.find_node_by_qualified_name(target)`"
    )


# Guard 3 — runtime smoke (catches return-shape drift)


def test_to_pageindex_json_returns_dict() -> None:
    """LookupService passes the result to ``json.dumps`` — it must be a
    JSON-serializable mapping. Catches return-type change to non-dict."""
    payload = _real_node().to_pageindex_json()
    assert isinstance(payload, dict)


def test_find_node_by_qualified_name_returns_node_or_none() -> None:
    """LookupService treats ``None`` as a 404 and otherwise calls
    methods/attributes on the returned object. Catches a return-type
    change that drops the None branch or returns the wrong type."""
    tree = _real_node()
    # Self-match returns self (the tree IS the node with that qualified_name).
    found = tree.find_node_by_qualified_name("pkg.mod")
    assert found is tree
    # Miss returns None.
    miss = tree.find_node_by_qualified_name("does.not.exist")
    assert miss is None


def test_lookup_service_consumed_attributes_present_on_instance() -> None:
    """node.node_id / node.kind / node.extra_metadata.get(...) are all
    used by LookupService at lookup_service.py:118-125. Confirm each
    is reachable on a real instance (slots don't hide them)."""
    n = _real_node()
    assert isinstance(n.node_id, str)
    # NodeKind is a StrEnum — comparable to "class" string literally.
    assert n.kind == NodeKind.MODULE
    assert isinstance(n.extra_metadata, Mapping)
    # The `inherits_from` lookup is `.get(..., [])`, a Mapping API call.
    assert n.extra_metadata.get("inherits_from", []) == []
