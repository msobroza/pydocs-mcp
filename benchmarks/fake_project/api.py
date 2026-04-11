"""HTTP API helpers for the fake analytics project."""
from typing import Any
import requests


def fetch_dataset(url: str, timeout: int = 30) -> list[dict[str, Any]]:
    """Download a JSON dataset from a remote URL.

    Args:
        url: Full HTTP(S) URL pointing to a JSON array.
        timeout: Request timeout in seconds.

    Returns:
        Parsed list of record dicts.

    Raises:
        requests.HTTPError: If the server returns a non-2xx status.
    """
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def post_results(endpoint: str, payload: dict[str, Any]) -> bool:
    """Upload benchmark results to a collection endpoint.

    Args:
        endpoint: URL accepting POST with JSON body.
        payload: Dict to serialize as JSON.

    Returns:
        True if server acknowledged with 2xx.
    """
    resp = requests.post(endpoint, json=payload, timeout=10)
    return resp.ok
