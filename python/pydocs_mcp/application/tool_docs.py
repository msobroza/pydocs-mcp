"""Single source of truth for tool documentation (spec §D13, ADRs 0005/0006).

``TOOL_DOCS[name]`` becomes the MCP tool description AND (first line only) the
CLI subcommand help text; ``SERVER_INSTRUCTIONS`` is the FastMCP server-level
orientation; ``SESSION_START_PREAMBLE`` frames the injected session-start context pack
when that feature is enabled (ADR 0008).

The text itself lives in the packaged ``defaults/descriptions.md`` (the
externalized optimizable surface, ADR 0005); this module's attributes are
populated at import by :func:`~pydocs_mcp.application.description_source.load_packaged`
and can be rebound by
:func:`~pydocs_mcp.application.description_source.apply_source` (override
documents) or the legacy benchmarks overlay wrapper. A validation failure of
the shipped document raises HERE, at import — a corrupted packaged file is a
packaging bug that must break every entry point loudly, not serve a partial
surface. The §D13 lint test enforces the section structure and size budgets,
so edits to the document fail fast instead of drifting.
"""

from __future__ import annotations

# --- §D13 contract constants — canonical home is now
# application/description_source.py (ADR 0005). Re-exported here (redundant
# aliases = explicit re-export) because the benchmarks optimizer's validate()
# and the §D13 lint import them from this module; drift here is drift in the
# lint.
from pydocs_mcp.application.description_source import (
    CHARS_PER_TOKEN as CHARS_PER_TOKEN,
)
from pydocs_mcp.application.description_source import (
    PER_TOOL_TOKEN_BUDGET as PER_TOOL_TOKEN_BUDGET,
)
from pydocs_mcp.application.description_source import (
    REQUIRED_MARKERS as REQUIRED_MARKERS,
)
from pydocs_mcp.application.description_source import (
    TOTAL_TOKEN_BUDGET as TOTAL_TOKEN_BUDGET,
)
from pydocs_mcp.application.description_source import attribute_views, load_packaged

SERVER_INSTRUCTIONS, TOOL_DOCS, SESSION_START_PREAMBLE = attribute_views(load_packaged())
