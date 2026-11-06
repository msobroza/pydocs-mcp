"""``LaunchRefused``: the launcher will not launch this, and says what and why."""

from __future__ import annotations


class LaunchRefused(ValueError):
    """A launch refused before anything was created; the message names the value and the shape."""
