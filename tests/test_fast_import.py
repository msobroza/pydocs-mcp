"""Tests for _fast.py import logic — covers both Rust and fallback paths."""
import importlib
import sys
from unittest.mock import patch


def test_fallback_path_when_native_unavailable():
    """When _native is not importable, _fast.py should use _fallback functions."""
    # Remove cached modules
    mods_to_remove = [k for k in sys.modules if k.startswith("pydocs_mcp._fast")]
    for k in mods_to_remove:
        del sys.modules[k]

    # Block _native import
    with patch.dict(sys.modules, {"pydocs_mcp._native": None}):
        import pydocs_mcp._fast as fast_mod
        importlib.reload(fast_mod)
        assert fast_mod.RUST_AVAILABLE is False
        # Functions should still work
        assert callable(fast_mod.walk_py_files)
        assert callable(fast_mod.chunk_text)
        assert callable(fast_mod.parse_py_file)

    # Restore
    mods_to_remove = [k for k in sys.modules if k.startswith("pydocs_mcp._fast")]
    for k in mods_to_remove:
        del sys.modules[k]
    import pydocs_mcp._fast  # Re-import normally


def test_native_path_when_available():
    """When _native is importable, RUST_AVAILABLE should be True (if compiled)."""
    import pydocs_mcp._fast as fast_mod
    # This test verifies whichever path is active in this environment
    assert isinstance(fast_mod.RUST_AVAILABLE, bool)
    assert callable(fast_mod.walk_py_files)


def test_disable_rust_swaps_to_fallback():
    """disable_rust() must replace all functions with Python fallback."""
    import pydocs_mcp._fast as fast_mod
    from pydocs_mcp import _fallback

    original_rust = fast_mod.RUST_AVAILABLE
    try:
        fast_mod.disable_rust()
        assert fast_mod.RUST_AVAILABLE is False
        # Every exported function must point to the fallback implementation
        for name in (
            "walk_py_files", "hash_files", "chunk_text", "parse_py_file",
            "extract_module_doc", "read_file", "read_files_parallel", "Symbol",
        ):
            assert getattr(fast_mod, name) is getattr(_fallback, name), (
                f"{name} was not replaced by fallback"
            )
    finally:
        # Restore original state by reloading _fast
        mods_to_remove = [k for k in sys.modules if k.startswith("pydocs_mcp._fast")]
        for k in mods_to_remove:
            del sys.modules[k]
        import pydocs_mcp._fast  # noqa: F811


def test_disable_rust_is_idempotent():
    """Calling disable_rust() when already using fallback must not error."""
    import pydocs_mcp._fast as fast_mod

    try:
        fast_mod.disable_rust()
        fast_mod.disable_rust()  # second call should be safe
        assert fast_mod.RUST_AVAILABLE is False
    finally:
        mods_to_remove = [k for k in sys.modules if k.startswith("pydocs_mcp._fast")]
        for k in mods_to_remove:
            del sys.modules[k]
        import pydocs_mcp._fast  # noqa: F811
