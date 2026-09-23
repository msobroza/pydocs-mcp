"""The four branch-keyed tree-tier stores speak one API on every conformer (#307).

``runtime_checkable`` isinstance only checks that attributes exist; this pins
each Protocol method's parameters (name, kind, default) on the SQLite
repository AND the in-memory fake the service tests run against, so a branch
keyword added to one of the three and forgotten on another fails here. It also
pins the branch rule's defaults: reads ``None`` (the served default branch),
writes ``''`` (the dependency tier), deletes ``None`` (every branch).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path

import pytest

import pydocs_mcp
from pydocs_mcp.storage.protocols import (
    DecisionStore,
    DocumentTreeStore,
    NodeScoreStore,
    ReferenceStore,
)
from pydocs_mcp.storage.sqlite import (
    SqliteDecisionRepository,
    SqliteDocumentTreeStore,
    SqliteNodeScoreRepository,
    SqliteReferenceStore,
)
from tests._fakes import (
    InMemoryDecisionStore,
    InMemoryDocumentTreeStore,
    InMemoryNodeScoreStore,
    InMemoryReferenceStore,
)

_CONFORMERS = {
    DocumentTreeStore: (SqliteDocumentTreeStore, InMemoryDocumentTreeStore),
    ReferenceStore: (SqliteReferenceStore, InMemoryReferenceStore),
    NodeScoreStore: (SqliteNodeScoreRepository, InMemoryNodeScoreStore),
    DecisionStore: (SqliteDecisionRepository, InMemoryDecisionStore),
}
# Methods whose branch keyword WRITES (stamps) default to ''. Reads default to
# None (the served default branch), and so do deletes and resolve_unresolved
# (an UPDATE scoped like a delete), where None means every branch.
_WRITES = {"save_many", "upsert"}
# A tier delete names its branch exactly: required, never "every branch".
_EXACT_BRANCH = {"delete_for_branch"}
_BRANCHLESS = {"delete_all", "delete_by_ids"}
# A decision record carries its own branch (``DecisionRecord.branch``).
_BRANCH_IN_THE_RECORD = {(DecisionStore, "upsert")}


def _parameter_shape(function: Callable[..., object]) -> list[tuple[str, object, object]]:
    parameters = inspect.signature(function).parameters.values()
    return [(p.name, p.kind, p.default) for p in parameters]


def _protocol_methods(protocol: type) -> dict[str, Callable[..., object]]:
    methods: dict[str, Callable[..., object]] = {}
    for klass in reversed(protocol.__mro__):
        for name, member in vars(klass).items():
            if not name.startswith("_") and inspect.isfunction(member):
                methods[name] = member
    return methods


@pytest.mark.parametrize("protocol", list(_CONFORMERS), ids=lambda p: p.__name__)
def test_every_conformer_takes_the_protocol_parameters(protocol: type) -> None:
    for conformer in _CONFORMERS[protocol]:
        for name, method in _protocol_methods(protocol).items():
            implemented = getattr(conformer, name)
            assert _parameter_shape(implemented) == _parameter_shape(method), (
                f"{conformer.__name__}.{name}"
            )


@pytest.mark.parametrize("protocol", list(_CONFORMERS), ids=lambda p: p.__name__)
def test_the_branch_keyword_defaults_follow_the_read_write_delete_rule(protocol: type) -> None:
    for name, method in _protocol_methods(protocol).items():
        parameters = inspect.signature(method).parameters
        if name in _BRANCHLESS or (protocol, name) in _BRANCH_IN_THE_RECORD:
            assert "branch" not in parameters, name
            continue
        branch = parameters["branch"]
        if name in _EXACT_BRANCH:
            assert branch.default is inspect.Parameter.empty, name
            continue
        assert branch.kind is inspect.Parameter.KEYWORD_ONLY, name
        expected = "" if name in _WRITES else None
        assert branch.default == expected, name


def test_the_served_default_branch_rule_has_one_definition() -> None:
    """The v18 stamp names project rows after the served default branch and
    every ``branch=None`` read resolves it again: two spellings of the rule
    that drifted apart would hide every project row from every tool (#307)."""
    root = Path(pydocs_mcp.__file__).parent
    spelled = [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*.py"))
        if "WHERE is_default = 1" in path.read_text(encoding="utf-8")
    ]
    assert spelled == ["db_branch_key_migration.py"]
