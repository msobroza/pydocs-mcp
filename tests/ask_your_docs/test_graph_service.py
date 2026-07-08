"""Tests for the graph layers.

Domain logic (GraphService) is tested two ways: against a real bundle via the
SqliteBundleReader (integration), and against an in-memory FakeBundleReader
(pure, no SQLite) — the latter proves the data-access seam is real.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

from pydocs_mcp.ask_your_docs import graph_service as gs
from pydocs_mcp.ask_your_docs.bundle import SqliteBundleReader
from pydocs_mcp.ask_your_docs.graph_service import GraphService
from pydocs_mcp.ask_your_docs.model import Edge, Graph, Node
from tests.ask_your_docs._fixture import make_bundle


def _svc(db: Path) -> GraphService:
    return GraphService(SqliteBundleReader(db))


def test_service_layer_imports_without_agent_stack():
    # graph_service / bundle / catalog must import without langgraph/streamlit.
    code = (
        "import sys; import pydocs_mcp.ask_your_docs.graph_service; "
        "import pydocs_mcp.ask_your_docs.bundle, pydocs_mcp.ask_your_docs.catalog; "
        "assert 'langgraph' not in sys.modules and 'streamlit' not in sys.modules; "
        "print('lean')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "lean" in out.stdout


# --- overview ---------------------------------------------------------------


def _overview_bundle(tmp_path: Path) -> Path:
    return make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("mod_a", "Foo", "class"), ("mod_a", "run", "def"), ("mod_b", "bar", "def")],
        refs=[("mod_a.Foo", "mod_b.bar", "calls"), ("mod_a", "mod_b", "imports")],
    )


def test_overview_nodes_are_project_modules(tmp_path):
    g = _svc(_overview_bundle(tmp_path)).overview("demo")
    assert {n.id for n in g.nodes} == {"mod_a", "mod_b"}
    assert all(n.node_type == "module" for n in g.nodes)


def test_overview_aggregates_cross_module_edges(tmp_path):
    g = _svc(_overview_bundle(tmp_path)).overview("demo")
    pairs = {(e.source, e.target, e.kind) for e in g.edges}
    assert ("mod_a", "mod_b", "calls") in pairs
    assert ("mod_a", "mod_b", "imports") in pairs


def test_overview_read_only_never_migrates_bundle(tmp_path):
    db = _overview_bundle(tmp_path)  # stamped user_version=99
    _svc(db).overview("demo")
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 99


# --- expand -----------------------------------------------------------------


def _expand_bundle(tmp_path: Path) -> Path:
    return make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("mod_a", "Foo", "class"), ("mod_a", "helper", "def"), ("mod_b", "bar", "def")],
        refs=[
            ("mod_a.Foo", "mod_b.bar", "calls"),
            ("mod_a.Foo", "mod_a.helper", "calls"),
            ("mod_b.Base", "mod_a.Foo", "inherits"),
        ],
    )


def test_expand_module_reveals_members_with_contains(tmp_path):
    g = _svc(_expand_bundle(tmp_path)).expand("mod_a", "module", kinds=frozenset())
    ids = {n.id: n.node_type for n in g.nodes}
    assert ids.get("mod_a.Foo") == "class"
    assert ids.get("mod_a.helper") == "function"
    assert all(e.kind == "contains" and e.source == "mod_a" for e in g.edges)


def test_expand_module_lists_defined_classes_even_if_unreferenced(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("mod_a", "Widget", "class"), ("mod_a", "run", "def")],
        refs=[("mod_a.run", "x.y", "calls")],
    )
    ids = {
        n.id: n.node_type for n in _svc(db).expand("mod_a", "module", frozenset({"calls"})).nodes
    }
    assert ids.get("mod_a.Widget") == "class"  # defined class shows despite no refs
    assert ids.get("mod_a.run") == "function"


def test_expand_symbol_reveals_reference_neighbors_filtered(tmp_path):
    g = _svc(_expand_bundle(tmp_path)).expand("mod_a.Foo", "class", kinds=frozenset({"calls"}))
    assert {e.kind for e in g.edges} == {"calls"}
    endpoints = {e.source for e in g.edges} | {e.target for e in g.edges}
    assert "mod_b.bar" in endpoints
    assert all(e.kind != "inherits" for e in g.edges)


def test_expand_caps_neighbors(tmp_path):
    refs = [("hub.f", f"leaf.g{i}", "calls") for i in range(gs.MAX_NEIGHBORS + 5)]
    members = [("hub", "f", "def")] + [
        ("leaf", f"g{i}", "def") for i in range(gs.MAX_NEIGHBORS + 5)
    ]
    db = make_bundle(tmp_path / "demo_0123456789.db", members=members, refs=refs)
    g = _svc(db).expand("hub.f", "function", kinds=frozenset({"calls"}))
    assert g.truncated == 5
    assert len(g.edges) == gs.MAX_NEIGHBORS


# --- node_meta / induce -----------------------------------------------------


def test_node_meta_member_has_signature_and_docstring(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("mod_a", "Foo", "class")],
        docstrings={"mod_a.Foo": "The Foo class."},
    )
    meta = _svc(db).node_meta("mod_a.Foo", "class")
    assert meta is not None and "Foo" in meta.title and "The Foo class." in meta.body


def test_node_meta_resolves_docstring_across_src_prefix(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("src.pkg.a", "Foo", "class")],
        refs=[("pkg.a.Foo", "pkg.a.helper", "calls")],
        docstrings={"src.pkg.a.Foo": "Docstring for Foo."},
    )
    meta = _svc(db).node_meta("pkg.a.Foo", "class")
    assert meta is not None and "Docstring for Foo." in meta.body


def test_induce_filters_by_node_type_and_edge_kind():
    g = Graph(
        nodes=(Node("m", "m", "module"), Node("m.C", "C", "class"), Node("m.f", "f", "function")),
        edges=(Edge("m", "m.C", "contains"), Edge("m.C", "m.f", "calls")),
    )
    out = gs.induce(g, node_types=frozenset({"module", "class"}), edge_kinds=frozenset({"calls"}))
    assert {n.id for n in out.nodes} == {"m", "m.C"}
    assert {(e.source, e.target, e.kind) for e in out.edges} == {("m", "m.C", "contains")}


def test_overview_reconciles_src_layout_module_mismatch(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("src.pkg.a", "Foo", "class"), ("src.pkg.b", "bar", "def")],
        refs=[("pkg.a.Foo", "pkg.b.bar", "calls")],
    )
    g = _svc(db).overview("demo")
    assert {n.id for n in g.nodes} == {"pkg.a", "pkg.b"}
    assert ("pkg.a", "pkg.b", "calls") in {(e.source, e.target, e.kind) for e in g.edges}


def test_edges_for_collapses_to_visible_ancestor(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("app.a.mod", "Foo", "class"), ("app.b.mod", "bar", "def")],
        refs=[("app.a.mod.Foo", "app.b.mod.bar", "calls")],
    )
    e = _svc(db).edges_for({"app.a", "app.b"}, frozenset({"calls"}))
    assert ("app.a", "app.b", "calls") in {(x.source, x.target, x.kind) for x in e}


# --- children / docs / decisions --------------------------------------------


def test_children_navigates_the_namespace(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[
            ("app.adapters.base", "Adapter", "class"),
            ("app.adapters.base", "helper", "def"),
            ("app.cli", "main", "def"),
        ],
        refs=[
            ("app.adapters.base.Adapter", "app.cli.main", "calls"),
            ("app.adapters.base.Adapter.run", "app.cli.main", "calls"),
        ],
    )
    svc = _svc(db)
    assert {(n.id, n.node_type) for n in svc.children("")} == {("app", "package")}

    app = {(n.id, n.node_type) for n in svc.children("app")}
    assert ("app.adapters", "package") in app
    assert ("app.cli", "module") in app

    base = {(n.id, n.node_type) for n in svc.children("app.adapters.base")}
    assert ("app.adapters.base.Adapter", "class") in base
    assert ("app.adapters.base.helper", "function") in base

    methods = {n.id for n in svc.children("app.adapters.base.Adapter")}
    assert "app.adapters.base.Adapter.run" in methods


def test_children_root_includes_docs_and_decisions(tmp_path):
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("app.cli", "main", "def")],
        refs=[("app.cli.main", "x.y", "calls")],
        markdown=[("README.md", "Intro", "hi")],
        decisions=[("Use RRF", "because ...")],
    )
    svc = _svc(db)
    both = {(n.id, n.node_type) for n in svc.children("", "Documentation + codebase")}
    assert ("app", "package") in both
    assert ("doc:README.md", "doc") in both
    assert any(nt == "decision" for _id, nt in both)
    # doc file zooms into its sections
    secs = {n.label for n in svc.children("doc:README.md")}
    assert "Intro" in secs


def test_is_test_flags_test_modules():
    assert gs.is_test("pkg.tests.unit.test_cli")
    assert gs.is_test("pkg.foo_test")
    assert gs.is_test("pkg.conftest")
    assert not gs.is_test("pkg.adapters.base")


def test_type_of_from_id_prefixes():
    mods = {"pkg.a"}
    assert gs.type_of("doc:README.md", mods) == "doc"
    assert gs.type_of("decision:2", mods) == "decision"
    assert gs.type_of("pkg.a", mods) == "module"
    assert gs.type_of("pkg.a.Foo", mods) == "class"
    assert gs.type_of("pkg.a.run", mods) == "function"


# --- the seam: GraphService runs on a fake reader (no SQLite) ---------------


class FakeBundleReader:
    """In-memory BundleReader for testing the domain layer without SQLite."""

    def __init__(self, *, refs=(), members=(), markdown=(), decisions=()):
        self._refs = [(a, b, k) for a, b, k in refs]
        self._members = list(members)  # (module, name, kind)
        self._markdown = list(markdown)  # (file, title)
        self._decisions = list(decisions)  # (id, title)

    def reference_rows(self):
        return list(self._refs)

    def references_of(self, node_id):
        return [r for r in self._refs if r[0] == node_id or r[1] == node_id]

    def member_rows(self):
        return list(self._members)

    def find_member(self, name, module_part):
        for module, mname, _kind in self._members:
            if mname == name and (module == module_part or module.endswith("." + module_part)):
                return (mname, f"def {mname}(...)", "doc")
        return None

    def markdown_files(self):
        return sorted({f for f, _t in self._markdown})

    def markdown_sections(self, file):
        return [(i, t) for i, (f, t) in enumerate(self._markdown) if f == file]

    def decisions(self):
        return list(self._decisions)

    def chunk(self, chunk_id):
        return None

    def project_name(self):
        return "fake"

    def packages(self):
        return []

    def indexed_at(self):
        return 0.0


def test_graph_service_runs_on_a_fake_reader():
    reader = FakeBundleReader(
        members=[("mod_a", "Foo", "class"), ("mod_b", "bar", "def")],
        refs=[("mod_a.Foo", "mod_b.bar", "calls")],
    )
    g = GraphService(reader).overview("fake")
    assert {n.id for n in g.nodes} == {"mod_a", "mod_b"}
    assert ("mod_a", "mod_b", "calls") in {(e.source, e.target, e.kind) for e in g.edges}
    # and the namespace / node_meta paths work with zero SQLite
    assert {n.id for n in GraphService(reader).children("")} == {"mod_a", "mod_b"}
