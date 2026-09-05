"""Overlay-name → serve-config resolver for campaign cells (ADR 0021 eval hook).

A campaign cell names a serve-YAML overlay as a stable STRING
(``CellConfig.suggestion_overlay``, and a future ``multilang`` on/off dimension).
The rollout driver needs the actual overlay FILE to thread through the product's
top-level ``pydocs-mcp --config`` flag. This module is the ONE registry mapping a
name to its shipped overlay path, so both the suggestions factor and a future
multilang cell resolve the same way (the evidence found ``suggestion_overlay``
lockfile-hashed but with no consumer — this is that consumer).

The overlays live beside this module (``campaign/overlays/*.yaml``,
package-relative) so the resolved path is stable regardless of the caller's cwd.
"""

from __future__ import annotations

from pathlib import Path

# Overlay YAMLs ship inside the package, next to this module.
_OVERLAY_DIR = Path(__file__).resolve().parent / "overlays"

# The registry of known overlay names → their shipped YAML filenames. A cell's
# overlay name MUST be a key here; an unknown name is a typed error rather than a
# silent stock-config serve (which would void the cell's measured factor).
_OVERLAY_FILES = {
    "suggestions_off": "suggestions_off.yaml",
}


class UnknownOverlayError(ValueError):
    """A cell named an overlay with no registered serve-YAML file."""


def resolve_overlay(name: str) -> Path:
    """Map an overlay name to its shipped serve-YAML path.

    Example:
        >>> resolve_overlay("suggestions_off").name
        'suggestions_off.yaml'

    Raises:
        UnknownOverlayError: ``name`` is not registered — the message carries the
            offending name and the known set.
        FileNotFoundError: the name is registered but its file is absent from the
            package (a packaging defect, surfaced loudly).
    """
    filename = _OVERLAY_FILES.get(name)
    if filename is None:
        raise UnknownOverlayError(
            f"unknown overlay {name!r}; expected one of {sorted(_OVERLAY_FILES)!r}"
        )
    path = _OVERLAY_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"overlay {name!r} registered but file missing: {path}")
    return path


def known_overlays() -> tuple[str, ...]:
    """The registered overlay names, sorted — the resolvable set for callers/docs."""
    return tuple(sorted(_OVERLAY_FILES))
