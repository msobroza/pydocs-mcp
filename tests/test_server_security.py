"""Security tests for MCP tool inputs."""
import sqlite3
from pathlib import Path


def make_conn_with_package(tmp_path: Path, pkg_name: str) -> sqlite3.Connection:
    """Create a minimal DB with one indexed package for testing."""
    from pydocs_mcp.db import open_db
    conn = open_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO packages(name, version, summary, homepage, dependencies, content_hash, origin) VALUES (?,?,?,?,?,?,?)",
        (pkg_name, "1.0", "test pkg", "", "[]", "h", "dependency"),
    )
    conn.commit()
    return conn


def test_inspect_module_rejects_path_traversal_submodule(tmp_path, monkeypatch):
    """submodule must not allow path traversal or arbitrary dotted paths beyond simple identifiers."""
    conn = make_conn_with_package(tmp_path, "fastapi")
    # Patch open_db so run() uses our test conn
    import pydocs_mcp.server as srv
    monkeypatch.setattr(srv, "open_db", lambda _: conn)

    # We test the validation logic directly by calling the inner function.
    # Since tools are registered inside run(), extract the validation logic to a helper.
    from pydocs_mcp.server import _validate_submodule
    assert _validate_submodule("") is True          # empty = ok (no submodule)
    assert _validate_submodule("routing") is True   # simple identifier = ok
    assert _validate_submodule("a.b.c") is True     # dotted = ok
    assert _validate_submodule("../evil") is False  # path traversal = rejected
    assert _validate_submodule("a b") is False      # spaces = rejected
    assert _validate_submodule("a;drop") is False   # semicolon = rejected


def test_validate_submodule_blocks_invalid_in_context(tmp_path):
    """The _validate_submodule helper correctly blocks known-bad inputs."""
    from pydocs_mcp.server import _validate_submodule
    # Adversarial inputs that could cause issues via importlib
    bad_inputs = ["../evil", "a;drop", "a b", "a\x00b", "-evil", "evil-", ".routing", "routing.", "a..b"]
    for bad in bad_inputs:
        assert not _validate_submodule(bad), f"Should reject: {repr(bad)}"

    # Valid module path patterns
    good_inputs = ["", "routing", "a.b.c", "test_module", "my_pkg.sub"]
    for good in good_inputs:
        assert _validate_submodule(good), f"Should accept: {repr(good)}"
