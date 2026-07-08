import subprocess
import sys


def test_graph_import_does_not_pull_agent_stack():
    # graph.py must be importable without langgraph/streamlit installed.
    code = (
        "import sys; import pydocs_mcp.ask_your_docs.graph as g; "
        "assert 'langgraph' not in sys.modules and 'streamlit' not in sys.modules; "
        "print('lean')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert "lean" in out.stdout
