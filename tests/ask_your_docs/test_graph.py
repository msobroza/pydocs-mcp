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
