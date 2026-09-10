"""Git adapters (spec §6.2): plumbing readers and the subprocess repository.

This package holds the git port's only subprocess adapter; everything else
reaches git through the
:class:`~pydocs_mcp.application.protocols.GitRepository` Protocol. The one
other subprocess caller is the decision layer's bounded ``git log`` reader
(``extraction/decisions/_git.py``), which predates the port and shares this
package's :func:`~pydocs_mcp.git.env.git_child_env` guard.
"""

from pydocs_mcp.git.errors import GitCommandError

__all__ = ("GitCommandError",)
