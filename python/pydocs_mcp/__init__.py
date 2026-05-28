"""pydocs-mcp — Local Python docs MCP server, accelerated with Rust."""

# Library convention: attach a NullHandler at the package root so callers
# who haven't configured logging don't see "No handlers could be found"
# warnings on the first log call. Users who configure logging via
# `logging.basicConfig()` or their own handlers see no behaviour change.
# Underscore alias keeps `logging` out of `from pydocs_mcp import *`.
import logging as _logging
from importlib.metadata import PackageNotFoundError, version

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

_logging.getLogger(__name__).addHandler(_logging.NullHandler())

try:
    __version__ = version("pydocs-mcp")
except PackageNotFoundError:  # not installed (e.g. running from a checkout
    # without an editable install). Fallback keeps imports working.
    __version__ = "0.0.0+unknown"

__all__ = [  # noqa: RUF022 — intentionally ordered as (version → exception base → leaves), not alphabetical
    "__version__",
    # Public exception hierarchy
    "PydocsMCPError",
    "MCPToolError",
    "InvalidArgumentError",
    "NotFoundError",
    "ServiceUnavailableError",
]
