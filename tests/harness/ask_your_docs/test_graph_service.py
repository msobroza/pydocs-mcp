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
    assert gs.is_test("pkg.foo_tests")  # *_tests suffix
    assert gs.is_test("pkg.test.helpers")  # singular `test` package segment
    assert gs.is_test("pkg.conftest")
    assert not gs.is_test("pkg.adapters.base")
    # false-positive guards: name-based, module granularity — these stay visible
    assert not gs.is_test("needle.testing")  # a real "testing utilities" module
    assert not gs.is_test("app.contest.latest")  # 'contest'/'latest' are not tests
    assert not gs.is_test("pkg.pytest_plugin")  # a plugin, not a test file


def test_type_of_from_id_prefixes():
    mods = {"pkg.a"}
    assert gs.type_of("doc:README.md", mods) == "doc"
    assert gs.type_of("decision:2", mods) == "decision"
    assert gs.type_of("pkg.a", mods) == "module"
    assert gs.type_of("pkg.a.Foo", mods) == "class"
    assert gs.type_of("pkg.a.run", mods) == "function"


# --- test-file exclusion is a graph-wide invariant --------------------------


def _mixed_bundle(tmp_path: Path) -> Path:
    """One real package (``app``) plus a ``tests`` package that calls into it."""
    return make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[
            ("app.cli", "main", "def"),
            ("tests.test_cli", "test_main", "def"),
            ("tests.conftest", "fixture", "def"),
        ],
        refs=[("tests.test_cli.test_main", "app.cli.main", "calls")],
    )


def test_children_hides_test_package_by_default(tmp_path):
    db = _mixed_bundle(tmp_path)
    on = {n.id for n in _svc(db).children("")}  # default hide_tests=True
    assert "app" in on and "tests" not in on


def test_overview_excludes_tests_by_default(tmp_path):
    nodes = {n.id for n in _svc(_mixed_bundle(tmp_path)).overview("demo").nodes}
    assert "app.cli" in nodes
    assert not any(gs.is_test(n) for n in nodes)  # no tests.* module leaks


def test_overview_edges_never_touch_test_modules(tmp_path):
    g = _svc(_mixed_bundle(tmp_path)).overview("demo")
    endpoints = {e.source for e in g.edges} | {e.target for e in g.edges}
    assert not any(gs.is_test(x) for x in endpoints)


def test_expand_symbol_drops_test_neighbors_by_default(tmp_path):
    # app.cli.main is *called by* a test; that test neighbour must not appear.
    g = _svc(_mixed_bundle(tmp_path)).expand("app.cli.main", "function", frozenset({"calls"}))
    assert not any(gs.is_test(n.id) for n in g.nodes)


def test_hide_tests_off_restores_every_view(tmp_path):
    db = _mixed_bundle(tmp_path)
    svc = GraphService(SqliteBundleReader(db), hide_tests=False)
    assert "tests" in {n.id for n in svc.children("")}
    assert "tests.test_cli" in {n.id for n in svc.overview("demo").nodes}
    neighbors = {n.id for n in svc.expand("app.cli.main", "function", frozenset({"calls"})).nodes}
    assert "tests.test_cli.test_main" in neighbors


def test_edges_for_drops_edges_collapsing_to_a_test_ancestor(tmp_path):
    db = _mixed_bundle(tmp_path)
    # Even if a test node is (wrongly) in the visible set, its edges are dropped.
    edges = _svc(db).edges_for({"app.cli", "tests.test_cli"}, frozenset({"calls"}))
    assert all(not gs.is_test(e.source) and not gs.is_test(e.target) for e in edges)


def test_edges_for_drops_edge_into_a_test_target(tmp_path):
    # A real module referencing a test symbol: the edge's TARGET anchor is a test
    # module and must be dropped (pins the b-side guard, not just the a-side).
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("app.cli", "main", "def"), ("tests.test_cli", "helper", "def")],
        refs=[("app.cli.main", "tests.test_cli.helper", "calls")],
    )
    assert _svc(db).edges_for({"app.cli", "tests.test_cli"}, frozenset({"calls"})) == ()


def _stripped_test_bundle(tmp_path: Path) -> Path:
    """The fs module keeps its ``tests.`` prefix, but the reference graph records
    it stripped (src-layout / path-root mismatch) — the case is_test-on-normalized
    used to leak."""
    return make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("app.core", "go", "def"), ("tests.support.factory", "build", "def")],
        refs=[("support.factory.build", "app.core.go", "calls")],
    )


def test_prefix_stripped_test_module_stays_hidden(tmp_path):
    db = _stripped_test_bundle(tmp_path)
    svc = _svc(db)
    assert "support.factory" not in svc.modules()
    assert "support.factory" not in {n.id for n in svc.overview("demo").nodes}
    assert "support" not in {n.id for n in svc.children("")}
    # toggle off brings the (correctly test) module back
    off = GraphService(SqliteBundleReader(db), hide_tests=False)
    assert "support.factory" in {n.id for n in off.overview("demo").nodes}


def test_expand_hides_prefix_stripped_test_neighbor(tmp_path):
    db = _stripped_test_bundle(tmp_path)
    kept = {n.id for n in _svc(db).expand("app.core.go", "function", frozenset({"calls"})).nodes}
    assert "support.factory.build" not in kept
    off = {
        n.id
        for n in GraphService(SqliteBundleReader(db), hide_tests=False)
        .expand("app.core.go", "function", frozenset({"calls"}))
        .nodes
    }
    assert "support.factory.build" in off


def test_production_test_named_members_are_not_hidden(tmp_path):
    # test filtering is module-granular: a production def NAMED `test_*` inside a
    # non-test module must NOT be hidden (no member-name over-exclusion).
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[
            ("app.db", "connect", "def"),
            ("app.db", "test_connection", "def"),
            ("app.db", "smoke_test", "def"),
        ],
        refs=[("app.db.connect", "x.y", "calls")],
    )
    svc = _svc(db)  # default hide_tests=True
    via_children = {n.id for n in svc.children("app.db")}
    via_expand = {n.id for n in svc.expand("app.db", "module", frozenset()).nodes}
    for ids in (via_children, via_expand):
        assert {"app.db.connect", "app.db.test_connection", "app.db.smoke_test"} <= ids


def test_expand_truncated_counts_only_kept_neighbors(tmp_path):
    # A hub with >MAX_NEIGHBORS non-test callees PLUS test callees. Hidden test
    # neighbours must not inflate `truncated` (guard runs before the counter).
    n_real = gs.MAX_NEIGHBORS + 5
    members = [("app.hub", "run", "def")]
    members += [("app.leaf", f"g{i}", "def") for i in range(n_real)]
    members += [("tests.t", f"t{i}", "def") for i in range(10)]
    refs = [("app.hub.run", f"app.leaf.g{i}", "calls") for i in range(n_real)]
    refs += [("app.hub.run", f"tests.t.t{i}", "calls") for i in range(10)]
    g = _svc(make_bundle(tmp_path / "demo_0123456789.db", members=members, refs=refs)).expand(
        "app.hub.run", "function", frozenset({"calls"})
    )
    assert len(g.edges) == gs.MAX_NEIGHBORS
    assert g.truncated == 5  # only the capped NON-test neighbours; the 10 tests don't count
    assert not any(gs.is_test(n.id) for n in g.nodes)


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
