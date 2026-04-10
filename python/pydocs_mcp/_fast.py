"""
Try to import Rust-accelerated functions, fall back to pure Python.

Usage anywhere in the project:
    from pydocs_mcp._fast import walk_py_files, chunk_text, ...
"""
import logging

log = logging.getLogger("pydocs-mcp")

try:
    from pydocs_mcp._native import (  # type: ignore[import]
        walk_py_files,
        hash_files,
        chunk_text,
        parse_py_file,
        extract_module_doc,
        read_file,
        read_files_parallel,
        Symbol,
    )
    RUST_AVAILABLE = True
    log.debug("Using Rust-accelerated functions")

except ImportError:
    from pydocs_mcp._fallback import (
        walk_py_files,
        hash_files,
        chunk_text,
        parse_py_file,
        extract_module_doc,
        read_file,
        read_files_parallel,
        Symbol,
    )
    RUST_AVAILABLE = False
    log.debug("Rust extension not found, using Python fallback")
