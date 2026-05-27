"""pydocs-mcp — Local Python docs MCP server, accelerated with Rust."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pydocs-mcp")
except PackageNotFoundError:  # not installed (e.g. running from a checkout
    # without an editable install). Fallback keeps imports working.
    __version__ = "0.0.0+unknown"
