"""pydocs-mcp — Local Python docs MCP server, accelerated with Rust."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pydocs-mcp")
except PackageNotFoundError:  # not installed (e.g. running from a checkout
    # without an editable install). Fallback keeps imports working.
    __version__ = "0.0.0+unknown"

# Public exception hierarchy. Embedders can:
#   try:
#       result = pydocs_mcp_call()
#   except pydocs_mcp.PydocsMCPError as exc:
#       ...  # catch any pydocs-mcp originated failure
# See `python/pydocs_mcp/exceptions.py` for the full design.
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    MCPToolError,
    NotFoundError,
    ServiceUnavailableError,
)
from pydocs_mcp.exceptions import PydocsMCPError

__all__ = [
    "__version__",
    # Public exception hierarchy
    "PydocsMCPError",
    "MCPToolError",
    "InvalidArgumentError",
    "NotFoundError",
    "ServiceUnavailableError",
]
