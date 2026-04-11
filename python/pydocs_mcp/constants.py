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
# Indexing limits — Python-only (no Rust equivalent)
# ---------------------------------------------------------------------------

SIGNATURE_MAX: int = 400
"""Max chars stored for a function signature string."""

RETURN_TYPE_MAX: int = 200
"""Max chars stored for a return-type annotation."""

PARAMS_JSON_MAX: int = 2000
"""Max chars stored for the JSON-serialised parameter list."""

PARAM_DEFAULT_MAX: int = 80
"""Max chars stored for a single parameter default repr."""

CLASS_DOCSTRING_MAX: int = 3000
"""Max chars stored for a class-level docstring (same as function)."""

CLASS_FULL_DOC_MAX: int = 5000
"""Max chars for a class doc after appending method summaries."""

METHOD_SUMMARY_MAX: int = 120
"""Max chars for a method's one-line docstring in class listings."""

# ---------------------------------------------------------------------------
# Display limits — MCP tool output formatting
# ---------------------------------------------------------------------------

SEARCH_BODY_DISPLAY: int = 1500
"""Max chars of chunk body shown in search_docs results."""

SEARCH_DOC_DISPLAY: int = 500
"""Max chars of symbol docstring shown in search_api results."""

SEARCH_BODY_CLI: int = 500
"""Max chars of chunk body printed in CLI 'query' output."""

SEARCH_DOC_CLI: int = 300
"""Max chars of symbol docstring printed in CLI 'api' output."""

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

SEARCH_RESULTS_MAX: int = 50
"""Max symbol results returned in search_api."""

REQUIREMENTS_PARSE_MAX: int = 40
"""Max requirements parsed from package metadata."""

CLASS_METHODS_MAX: int = 12
"""Max methods listed per class during inspection indexing."""
