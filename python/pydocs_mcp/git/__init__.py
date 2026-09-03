"""Git adapters (spec §6.2): plumbing readers and the subprocess repository.

Only this package may import ``subprocess``; everything else reaches git
through the :class:`~pydocs_mcp.application.protocols.GitRepository` Protocol.
"""

from pydocs_mcp.git.errors import GitCommandError

__all__ = ("GitCommandError",)
