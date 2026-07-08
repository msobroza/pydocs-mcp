"""Shared HTTP-download-truncation guard for stdlib-only dataset loaders.

WHY a dedicated helper: SWE-QA and SWE-QA-Pro both validate a downloaded
JSONL payload line-by-line before committing it to the on-disk cache
(``_download_split_atomic`` / ``_download_release_atomic``). Per-line
``json.loads`` alone cannot detect a body truncated exactly on a ``\\n``
boundary (proxy cut-off, connection drop flushed mid-stream) — every
surviving line is still well-formed JSON, so the partial payload sails
through and gets cached forever (keyed by pinned revision, guarded by
``if not target.exists()``). Comparing the actual byte count against the
response's own ``Content-Length`` header catches that case; duplicated
across two loaders would drift, so it lives here once.
"""

from __future__ import annotations

from http.client import HTTPResponse


class TruncatedDownloadError(ValueError):
    """Raised when a downloaded body is shorter than its own advertised
    Content-Length — the strongest stdlib-available signal of a truncated
    HTTP transfer that per-line JSON validation alone cannot catch."""


def check_content_length(resp: HTTPResponse, payload: bytes, *, url: str) -> None:
    """Raise :class:`TruncatedDownloadError` if ``payload`` is shorter than
    the response's advertised ``Content-Length``.

    Usage::

        with urllib.request.urlopen(url, timeout=60) as resp:
            payload = resp.read()
            check_content_length(resp, payload, url=url)
    """
    advertised = resp.headers.get("Content-Length")
    if advertised is None:
        return
    try:
        expected = int(advertised)
    except ValueError:
        return
    if len(payload) < expected:
        raise TruncatedDownloadError(
            f"truncated download from {url}: got {len(payload)} bytes, "
            f"Content-Length advertised {expected}"
        )
