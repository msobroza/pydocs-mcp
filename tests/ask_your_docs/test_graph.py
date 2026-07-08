import subprocess
import sys


def test_graph_import_does_not_pull_agent_stack():
    # graph.py must be importable without langgraph/streamlit installed.
    code = (
        "import sys; import pydocs_mcp.ask_your_docs.graph as g; "
        "assert 'langgraph' not in sys.modules and 'streamlit' not in sys.modules; "
        "print('lean')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "lean" in out.stdout


import sqlite3
from pathlib import Path

from tests.ask_your_docs._fixture import make_bundle


def _overview_bundle(tmp_path: Path) -> Path:
    return make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[
            ("mod_a", "Foo", "class"),
            ("mod_a", "run", "def"),
            ("mod_b", "bar", "def"),
        ],
        refs=[
            ("mod_a.Foo", "mod_b.bar", "calls"),
            ("mod_a", "mod_b", "imports"),
        ],
    )


def test_overview_nodes_are_project_modules(tmp_path):
    from pydocs_mcp.ask_your_docs import graph

    g = graph.overview(_overview_bundle(tmp_path), "demo")
    assert {n.id for n in g.nodes} == {"mod_a", "mod_b"}
    assert all(n.node_type == "module" for n in g.nodes)


def test_overview_aggregates_cross_module_edges(tmp_path):
    from pydocs_mcp.ask_your_docs import graph

    g = graph.overview(_overview_bundle(tmp_path), "demo")
    pairs = {(e.source, e.target, e.kind) for e in g.edges}
    assert ("mod_a", "mod_b", "calls") in pairs
    assert ("mod_a", "mod_b", "imports") in pairs


def test_overview_read_only_never_migrates_bundle(tmp_path):
    from pydocs_mcp.ask_your_docs import graph

    db = _overview_bundle(tmp_path)
    graph.overview(db, "demo")
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 99


def _expand_bundle(tmp_path):
    from tests.ask_your_docs._fixture import make_bundle

    return make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[
            ("mod_a", "Foo", "class"),
            ("mod_a", "helper", "def"),
            ("mod_b", "bar", "def"),
        ],
        refs=[
            ("mod_a.Foo", "mod_b.bar", "calls"),
            ("mod_a.Foo", "mod_a.helper", "calls"),
            ("mod_b.Base", "mod_a.Foo", "inherits"),
        ],
    )


def test_expand_module_reveals_members_with_contains(tmp_path):
    from pydocs_mcp.ask_your_docs import graph

    g = graph.expand(_expand_bundle(tmp_path), "mod_a", "module", kinds=frozenset())
    ids = {n.id: n.node_type for n in g.nodes}
    assert ids.get("mod_a.Foo") == "class"
    assert ids.get("mod_a.helper") == "function"
    assert all(e.kind == "contains" and e.source == "mod_a" for e in g.edges)


def test_expand_symbol_reveals_reference_neighbors_filtered(tmp_path):
    from pydocs_mcp.ask_your_docs import graph

    db = _expand_bundle(tmp_path)
    only_calls = graph.expand(db, "mod_a.Foo", "class", kinds=frozenset({"calls"}))
    kinds = {e.kind for e in only_calls.edges}
    assert kinds == {"calls"}
    targets = {e.target for e in only_calls.edges} | {e.source for e in only_calls.edges}
    assert "mod_b.bar" in targets
    assert all(e.kind != "inherits" for e in only_calls.edges)


def test_expand_caps_neighbors(tmp_path):
    from pydocs_mcp.ask_your_docs import graph
    from tests.ask_your_docs._fixture import make_bundle

    refs = [("hub.f", f"leaf.g{i}", "calls") for i in range(graph.MAX_NEIGHBORS + 5)]
    members = [("hub", "f", "def")] + [
        ("leaf", f"g{i}", "def") for i in range(graph.MAX_NEIGHBORS + 5)
    ]
    db = make_bundle(tmp_path / "demo_0123456789.db", members=members, refs=refs)
    g = graph.expand(db, "hub.f", "function", kinds=frozenset({"calls"}))
    assert g.truncated == 5
    assert len(g.edges) == graph.MAX_NEIGHBORS


def test_node_meta_member_has_signature_and_docstring(tmp_path):
    from pydocs_mcp.ask_your_docs import graph
    from tests.ask_your_docs._fixture import make_bundle

    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("mod_a", "Foo", "class")],
        docstrings={"mod_a.Foo": "The Foo class."},
    )
    meta = graph.node_meta(db, "mod_a.Foo", "class")
    assert meta is not None and "Foo" in meta.title and "The Foo class." in meta.body


def test_induce_filters_by_node_type_and_edge_kind():
    from pydocs_mcp.ask_your_docs import graph

    g = graph.Graph(
        nodes=(
            graph.Node("m", "m", "module"),
            graph.Node("m.C", "C", "class"),
            graph.Node("m.f", "f", "function"),
        ),
        edges=(
            graph.Edge("m", "m.C", "contains"),
            graph.Edge("m.C", "m.f", "calls"),
        ),
    )
    out = graph.induce(
        g, node_types=frozenset({"module", "class"}), edge_kinds=frozenset({"calls"})
    )
    assert {n.id for n in out.nodes} == {"m", "m.C"}
    assert {(e.source, e.target, e.kind) for e in out.edges} == {("m", "m.C", "contains")}


def test_overview_reconciles_src_layout_module_mismatch(tmp_path):
    from pydocs_mcp.ask_your_docs import graph
    from tests.ask_your_docs._fixture import make_bundle

    # module_members stores fs-derived "src." module paths; node_references uses
    # the import path (no "src."). The two must still join into a connected graph.
    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("src.pkg.a", "Foo", "class"), ("src.pkg.b", "bar", "def")],
        refs=[("pkg.a.Foo", "pkg.b.bar", "calls")],
    )
    g = graph.overview(db, "demo")
    assert {n.id for n in g.nodes} == {"pkg.a", "pkg.b"}
    assert ("pkg.a", "pkg.b", "calls") in {(e.source, e.target, e.kind) for e in g.edges}


def test_node_meta_resolves_docstring_across_src_prefix(tmp_path):
    from pydocs_mcp.ask_your_docs import graph
    from tests.ask_your_docs._fixture import make_bundle

    db = make_bundle(
        tmp_path / "demo_0123456789.db",
        members=[("src.pkg.a", "Foo", "class")],
        refs=[("pkg.a.Foo", "pkg.a.helper", "calls")],
        docstrings={"src.pkg.a.Foo": "Docstring for Foo."},
    )
    meta = graph.node_meta(db, "pkg.a.Foo", "class")
    assert meta is not None and "Docstring for Foo." in meta.body
