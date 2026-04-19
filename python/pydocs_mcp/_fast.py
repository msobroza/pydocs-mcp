"""
Try to import Rust-accelerated functions, fall back to pure Python.

Usage anywhere in the project:
    from pydocs_mcp._fast import walk_py_files, split_into_chunks, ...
"""
import logging

log = logging.getLogger("pydocs-mcp")

try:
    from pydocs_mcp._native import (  # type: ignore[import]
        walk_py_files,
        hash_files,
        split_into_chunks,
        parse_py_file,
        extract_module_doc,
        read_file,
        read_files_parallel,
        ParsedMember,
    )
    RUST_AVAILABLE = True
    log.debug("Using Rust-accelerated functions")

except ImportError:
    from pydocs_mcp._fallback import (
        walk_py_files,
        hash_files,
        split_into_chunks,
        parse_py_file,
        extract_module_doc,
        read_file,
        read_files_parallel,
        ParsedMember,
    )
    RUST_AVAILABLE = False
    log.debug("Rust extension not found, using Python fallback")


def disable_rust() -> None:
    """Replace Rust-accelerated functions with pure-Python fallback.

    Call this before any indexing to force the Python implementation
    even when the Rust extension is compiled and available.
    """
    import pydocs_mcp._fast as mod
    from pydocs_mcp import _fallback
    for name in (
        "walk_py_files", "hash_files", "split_into_chunks", "parse_py_file",
        "extract_module_doc", "read_file", "read_files_parallel", "ParsedMember",
    ):
        setattr(mod, name, getattr(_fallback, name))
    mod.RUST_AVAILABLE = False
    log.info("Rust acceleration disabled, using Python fallback")
