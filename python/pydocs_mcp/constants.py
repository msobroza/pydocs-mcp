"""Named constants for text truncation and display limits.

These values control how much text is stored in the database and how much is
shown in search results. The three constants in the "Indexing limits" group
are mirrored in src/lib.rs and MUST be kept in sync when changed.
"""

# ---------------------------------------------------------------------------
# Indexing limits — shared with Rust (src/lib.rs)
# ---------------------------------------------------------------------------
# SYNC: Any change here MUST be mirrored in src/lib.rs constants.

DOCSTRING_LOOKAHEAD: int = 500
"""Max chars inspected after a def/class line to find a docstring."""

FUNC_DOCSTRING_MAX: int = 3000
"""Max chars stored for a single function or method docstring."""

MODULE_DOCSTRING_MAX: int = 5000
"""Max chars stored for a module-level docstring."""

# ---------------------------------------------------------------------------
# Display limits — MCP tool output formatting
# ---------------------------------------------------------------------------

PACKAGE_DOC_MAX: int = 30_000
"""Max chars for the concatenated package doc response."""

PACKAGE_DOC_LINE_MAX: int = 120
"""Max chars of symbol first-line doc shown in package listings."""

LIVE_SIGNATURE_MAX: int = 200
"""Max chars of live-inspection signature in inspect_module."""

LIVE_DOC_MAX: int = 150
"""Max chars of live-inspection first-line doc in inspect_module."""

# ---------------------------------------------------------------------------
# Collection limits
# ---------------------------------------------------------------------------

REQUIREMENTS_DISPLAY: int = 20
"""Max dependency names shown in get_package_doc output."""

LIST_PACKAGES_MAX: int = 200
"""Max packages returned by the ``list_packages`` MCP tool.

Defensive cap so a badly-populated cache (or a future all-deps mode)
cannot blow up the client's context window or stream size."""
