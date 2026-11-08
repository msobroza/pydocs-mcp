"""The product repository before/after measures, and how to tell a remote points at it.

ONE source for the URL: the VM clones it (``product_clone``), and the laptop
refuses a before/after baseline or candidate that no remote-tracking branch
of it holds (``run_commits``), so the commits the run froze are the ones the
clone will find.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# The public product repository: before/after measures two of its commits.
PRODUCT_REPO_URL = "https://github.com/msobroza/pydocs-mcp.git"
# git's scp-like syntax: [user@]host:path, where the host holds no slash.
_SCP_LIKE = re.compile(r"(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.+)")
_REPEATED_SLASHES = re.compile(r"/{2,}")


def normalised_repo_url(url: str) -> str:
    """``host/owner/repo``, lowercased: equal for every spelling of one hosted repository.

    https, http, git and ssh URLs, and scp-like ``git@host:owner/repo``, lose
    their scheme, user (and so any credential), port, repeated and trailing
    slashes and ``.git`` (in any case). Anything else (a local path) is only
    equal to itself. An ssh ``Host`` alias from ``~/.ssh/config`` is not
    resolved: it reads as another host.
    """
    text = url.strip()
    if "://" in text:
        parts = urlsplit(text)
        host, path = parts.hostname or "", parts.path
    elif match := _SCP_LIKE.fullmatch(text):
        host, path = match["host"], match["path"]
    else:
        return text
    if not host:  # file:// and the like name a path, not a hosted repository
        return text
    path = _REPEATED_SLASHES.sub("/", path.lower()).strip("/").removesuffix(".git").rstrip("/")
    return f"{host.lower()}/{path}"


def is_product_repo_url(url: str) -> bool:
    return normalised_repo_url(url) == normalised_repo_url(PRODUCT_REPO_URL)
