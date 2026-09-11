"""``get_references`` on a MODULE target answers the import graph (spec §1).

Covers AC1.2 (importer union), AC1.4 + E1 (rejection messages), AC1.5
(single-segment dependency package), AC1.7 (extension channel), AC1.8
(tree shows byte-identical) and AC1.9 (seed cap + truncation entry + log).

The tree/reference doubles are LOCAL copies of the ones in
``test_lookup_service.py`` (spec §1 "copied, not imported") so neither file
constrains the other's fake shape.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pydocs_mcp.application.lookup_service import TARGET_EXTENSION_EXTRA, LookupService
from pydocs_mcp.application.mcp_errors import InvalidArgumentError
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.application.reference_service import ImpactNode
from pydocs_mcp.application.truncation import ledger_scope
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Package, PackageDoc, PackageOrigin
from pydocs_mcp.storage.node_reference import NodeReference

# ── doubles ────────────────────────────────────────────────────────────────


def _node(
    qname: str,
    kind: NodeKind,
    *,
    path: str = "pkg/mod.py",
    children: tuple[DocumentNode, ...] = (),
) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=kind,
        source_path=path,
        start_line=1,
        end_line=9,
        text="",
        content_hash="h",
        children=children,
    )


def _module_tree(path: str = "pkg/mod.py") -> DocumentNode:
    return _node(
        "pkg.mod",
        NodeKind.MODULE,
        path=path,
        children=(
            _node("pkg.mod.__imports__", NodeKind.IMPORT_BLOCK, path=path),
            _node("pkg.mod.Alpha", NodeKind.CLASS, path=path),
            _node("pkg.mod.beta", NodeKind.FUNCTION, path=path),
        ),
    )


def _tree_svc_for_module(module_path: str, tree: Any) -> MagicMock:
    """A tree_svc double that resolves exactly one module id."""
    svc = MagicMock()

    async def _exists(package: str, module: str) -> bool:
        return module == module_path

    async def _get_tree(package: str, module: str) -> Any:
        return tree if module == module_path else None

    svc.exists = _exists
    svc.get_tree = _get_tree
    return svc


def _ref(frm: str, to: str, kind: ReferenceKind) -> NodeReference:
    return NodeReference(from_package="pkg", from_node_id=frm, to_name=to, to_node_id=to, kind=kind)


@dataclass
class _FakeRefSvc:
    """``ReferenceNavigator`` double recording the qnames it was asked about."""

    callers_by_qname: dict[str, tuple[NodeReference, ...]] = field(default_factory=dict)
    callees_rows: tuple[NodeReference, ...] = ()
    governed_rows: tuple[NodeReference, ...] = ()
    impact_rows: tuple[ImpactNode, ...] = ()
    asked: list[str] = field(default_factory=list)

    async def callers(self, package: str, node_qname: str, /) -> tuple[NodeReference, ...]:
        self.asked.append(node_qname)
        return self.callers_by_qname.get(node_qname, ())

    async def callees(self, package: str, node_qname: str, /) -> tuple[NodeReference, ...]:
        self.asked.append(node_qname)
        return self.callees_rows

    async def governed_by(self, package: str, node_qname: str, /) -> tuple[NodeReference, ...]:
        self.asked.append(node_qname)
        return self.governed_rows

    async def inherits(self, package: str, node_qname: str, /) -> tuple[NodeReference, ...]:
        return ()

    async def impact(
        self, package: str, qname: str, /, *, max_depth: int, limit: int
    ) -> tuple[ImpactNode, ...]:
        self.asked.append(qname)
        return self.impact_rows[:limit]

    async def context(self, package: str, qname: str, /, *, max_depth: int, limit: int):
        return ()


@pytest.fixture
def package_lookup_mock() -> MagicMock:
    m = MagicMock()
    m.list_packages = AsyncMock(return_value=())
    m.get_package_doc = AsyncMock(return_value=None)
    m.find_module = AsyncMock(return_value=False)
    return m


def _service(
    package_lookup: MagicMock,
    ref_svc: Any,
    tree: DocumentNode,
    *,
    module_path: str = "pkg.mod",
    seed_cap: int = 32,
) -> LookupService:
    return LookupService(
        package_lookup=package_lookup,
        tree_svc=_tree_svc_for_module(module_path, tree),
        ref_svc=ref_svc,
        module_seed_cap=seed_cap,
    )


# ── AC1.2: callers = importers of the module + IMPORTS into its children ───


async def test_module_callers_union_module_edges_and_child_imports(
    package_lookup_mock: MagicMock,
) -> None:
    ref_svc = _FakeRefSvc(
        callers_by_qname={
            "pkg.mod": (
                _ref("pkg.user", "pkg.mod", ReferenceKind.IMPORTS),
                _ref("decision:d1", "pkg.mod", ReferenceKind.GOVERNS),
            ),
            "pkg.mod.Alpha": (
                _ref("pkg.other", "pkg.mod.Alpha", ReferenceKind.IMPORTS),
                _ref("pkg.other.run", "pkg.mod.Alpha", ReferenceKind.CALLS),
            ),
        }
    )
    svc = _service(package_lookup_mock, ref_svc, _module_tree())

    text, items, extras = await svc.lookup_with_items(LookupInput(target="pkg.mod", show="callers"))

    assert not text.lstrip().startswith("{")  # not the PageIndex JSON any more
    assert "# Callers of `pkg.mod`" in text
    assert [(i["from_qualified_name"], i["kind"]) for i in items] == [
        ("pkg.user", "imports"),
        ("decision:d1", "governs"),
        ("pkg.other", "imports"),
    ]
    assert ref_svc.asked == ["pkg.mod", "pkg.mod.Alpha", "pkg.mod.beta"]
    assert extras[TARGET_EXTENSION_EXTRA] == ".py"


async def test_module_callees_and_governed_by_read_the_module_root(
    package_lookup_mock: MagicMock,
) -> None:
    ref_svc = _FakeRefSvc(
        callees_rows=(_ref("pkg.mod", "json", ReferenceKind.IMPORTS),),
        governed_rows=(_ref("decision:d1", "pkg.mod", ReferenceKind.GOVERNS),),
    )
    svc = _service(package_lookup_mock, ref_svc, _module_tree())

    callees, callee_items, _e = await svc.lookup_with_items(
        LookupInput(target="pkg.mod", show="callees")
    )
    governed, governed_items, _g = await svc.lookup_with_items(
        LookupInput(target="pkg.mod", show="governed_by")
    )

    assert "pkg.mod" in callees and len(callee_items) == 1
    assert "pkg.mod" in governed and len(governed_items) == 1
    assert ref_svc.asked == ["pkg.mod", "pkg.mod"]


async def test_module_impact_excludes_the_modules_own_internals(
    package_lookup_mock: MagicMock,
) -> None:
    ref_svc = _FakeRefSvc(
        impact_rows=(
            ImpactNode("pkg.mod.Alpha", 1, 0.9, 2, True),
            ImpactNode("pkg.user.run", 1, 0.4, 1, True),
        )
    )
    svc = _service(package_lookup_mock, ref_svc, _module_tree())

    text, items, extras = await svc.lookup_with_items(LookupInput(target="pkg.mod", show="impact"))

    assert "pkg.user.run" in text
    assert "pkg.mod.Alpha" not in text
    assert items == ()
    assert extras[TARGET_EXTENSION_EXTRA] == ".py"


# ── AC1.4 + E1: inherits / context on a module ─────────────────────────────


@pytest.mark.parametrize("show", ["inherits", "context"])
async def test_module_inherits_and_context_raise_invalid_argument(
    package_lookup_mock: MagicMock, show: str
) -> None:
    svc = _service(package_lookup_mock, _FakeRefSvc(), _module_tree())
    with pytest.raises(InvalidArgumentError) as exc:
        await svc.lookup_with_items(LookupInput(target="pkg.mod", show=show))
    text = str(exc.value)
    assert "pkg.mod" in text
    assert "module" in text


async def test_inherits_on_a_function_uses_surface_neutral_wording(
    package_lookup_mock: MagicMock,
) -> None:
    """E1: no ``show=`` and no ``CLASS nodes`` — the MCP client says direction."""
    svc = _service(package_lookup_mock, _FakeRefSvc(), _module_tree())
    with pytest.raises(InvalidArgumentError) as exc:
        await svc.lookup_with_items(LookupInput(target="pkg.mod.beta", show="inherits"))
    text = str(exc.value)
    assert "pkg.mod.beta" in text
    assert "function" in text
    assert "inherits" in text
    assert "show=" not in text
    assert "CLASS nodes" not in text


# ── AC1.5: a single-segment dependency package ─────────────────────────────


def _package_doc() -> PackageDoc:
    package = Package(
        name="pkg",
        version="1.0.0",
        summary="A package",
        homepage="",
        dependencies=(),
        content_hash="abc",
        origin=PackageOrigin.DEPENDENCY,
    )
    return PackageDoc(package=package, chunks=(), members=())


async def test_package_target_with_a_graph_direction_routes_to_its_top_module(
    package_lookup_mock: MagicMock,
) -> None:
    package_lookup_mock.get_package_doc = AsyncMock(return_value=_package_doc())
    tree = _node("pkg", NodeKind.MODULE, path="pkg/__init__.py")
    ref_svc = _FakeRefSvc(callers_by_qname={"pkg": (_ref("app", "pkg", ReferenceKind.IMPORTS),)})
    svc = _service(package_lookup_mock, ref_svc, tree, module_path="pkg")

    text, items, _extras = await svc.lookup_with_items(LookupInput(target="pkg", show="callers"))

    assert "# Callers of `pkg`" in text
    assert [i["from_qualified_name"] for i in items] == ["app"]


async def test_package_target_without_a_top_module_raises_invalid_argument(
    package_lookup_mock: MagicMock,
) -> None:
    package_lookup_mock.get_package_doc = AsyncMock(return_value=_package_doc())
    svc = _service(package_lookup_mock, _FakeRefSvc(), _module_tree())

    with pytest.raises(InvalidArgumentError) as exc:
        await svc.lookup_with_items(LookupInput(target="pkg", show="callers"))
    text = str(exc.value)
    assert "'callers'" in text
    assert "pkg.<module>" in text


async def test_package_target_tree_show_still_renders_the_package_doc(
    package_lookup_mock: MagicMock,
) -> None:
    package_lookup_mock.get_package_doc = AsyncMock(return_value=_package_doc())
    svc = _service(package_lookup_mock, _FakeRefSvc(), _module_tree())
    text, items, extras = await svc.lookup_with_items(LookupInput(target="pkg"))
    assert "pkg" in text and items == () and extras == {}


# ── AC1.7 / AC1.8: extension channel + byte-identical tree shows ───────────


@pytest.mark.parametrize(
    ("path", "expected"), [("pkg/mod.py", ".py"), ("pkg/mod.toml", ".toml"), ("docs/a.md", ".md")]
)
async def test_module_callers_thread_the_roots_file_extension(
    package_lookup_mock: MagicMock, path: str, expected: str
) -> None:
    svc = _service(package_lookup_mock, _FakeRefSvc(), _module_tree(path=path))
    _text, items, extras = await svc.lookup_with_items(
        LookupInput(target="pkg.mod", show="callers")
    )
    assert items == ()
    assert extras[TARGET_EXTENSION_EXTRA] == expected


@pytest.mark.parametrize("show", ["default", "tree"])
async def test_module_tree_shows_are_byte_identical_to_the_page_index(
    package_lookup_mock: MagicMock, show: str
) -> None:
    tree = _module_tree()
    svc = _service(package_lookup_mock, _FakeRefSvc(), tree)
    text, items, extras = await svc.lookup_with_items(LookupInput(target="pkg.mod", show=show))
    assert text == json.dumps(tree.to_pageindex_json(), indent=2)
    assert len(items) == 4
    assert extras[TARGET_EXTENSION_EXTRA] == ".py"


# ── AC1.9: the seed cap ────────────────────────────────────────────────────


def _wide_module(members: int) -> DocumentNode:
    return _node(
        "pkg.mod",
        NodeKind.MODULE,
        children=tuple(_node(f"pkg.mod.f{i}", NodeKind.FUNCTION) for i in range(members)),
    )


async def test_seed_cap_bounds_the_fan_out_and_records_one_truncation_entry(
    package_lookup_mock: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    ref_svc = _FakeRefSvc()
    svc = _service(package_lookup_mock, ref_svc, _wide_module(10), seed_cap=4)

    with caplog.at_level(logging.WARNING), ledger_scope() as ledger:
        await svc.lookup_with_items(LookupInput(target="pkg.mod", show="callers"))

    assert ref_svc.asked == ["pkg.mod", "pkg.mod.f0", "pkg.mod.f1", "pkg.mod.f2"]
    entries = [e for e in ledger.entries if "module members not searched" in e.description]
    assert len(entries) == 1
    assert "7 of 10" in entries[0].description
    assert "reference_graph.impact.max_module_seeds" in entries[0].description
    events = [json.loads(r.message)["event"] for r in caplog.records if r.message.startswith("{")]
    assert events.count("module_target_seed_cap") == 1


async def test_no_truncation_entry_when_every_member_is_searched(
    package_lookup_mock: MagicMock,
) -> None:
    svc = _service(package_lookup_mock, _FakeRefSvc(), _wide_module(3), seed_cap=32)
    with ledger_scope() as ledger:
        await svc.lookup_with_items(LookupInput(target="pkg.mod", show="callers"))
    assert [e for e in ledger.entries if "module members not searched" in e.description] == []
