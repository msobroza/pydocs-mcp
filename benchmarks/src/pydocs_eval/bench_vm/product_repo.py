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


def normalised_repo_url(url: str) -> str:
    """``host/owner/repo``, lowercased: equal for every spelling of one hosted repository.

    https, http, git and ssh URLs, and scp-like ``git@host:owner/repo``, lose
    their scheme, user (and so any credential), port, trailing slash and
    ``.git``. Anything else (a local path) is only equal to itself.
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
    return f"{host.lower()}/{path.strip('/').removesuffix('.git').lower()}"


def is_product_repo_url(url: str) -> bool:
    return normalised_repo_url(url) == normalised_repo_url(PRODUCT_REPO_URL)
