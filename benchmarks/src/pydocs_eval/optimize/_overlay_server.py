"""Arm-B ``tool_docs`` overlay server wrapper (spec §D6, zero product hook).

**Why this shape (the §D6 recorded alternative, not an AppConfig field):** the
spec weighed two ways to inject an optimized ``tool_docs`` surface into the
served MCP server. The rejected one added an ``AppConfig.tool_docs_overlay_path``
field the product would read at startup. This wrapper is the smaller-footprint
alternative the spec recorded as preferred and this plan verified feasible
(2026-07-08): ``pydocs_mcp.server`` reads ``TOOL_DOCS`` (server.py:249 → 261)
and ``SERVER_INSTRUCTIONS`` (server.py:183) via **function-local imports at call
time**, so re-binding the ``pydocs_mcp.application.tool_docs`` module attributes
BEFORE calling ``server.run`` injects the overlay with ZERO product change — the
product-code footprint of the whole slice stays at the §D2b lint-constant
refactor only, and no MCP tool param or config field is added.

**Fail-closed (spec §D6):** an overlay that violates the §D2a/§D13 firewall
(``ToolDocsArtifact.validate``) raises ``OverlayValidationError`` and the server
NEVER boots — the harness must never serve a candidate surface that would fail
the product lint a human is asked to land.

**Serve-path parity:** the wrapper resolves the project path to its SQLite cache
db through :func:`pydocs_mcp.db.cache_path_for_project` — the SAME helper the CLI
``serve`` command uses (see ``__main__._project_and_db``) — so the per-project
``<dirname>_<hash>.db`` path-hash resolution never forks between a normal serve
and an overlay serve.

Launch as ``python -m pydocs_eval.optimize._overlay_server <project> --overlay
<file>``; the Task-6 ``ArtifactInjection.overlay_path`` plugs in here — the
paired-agent fitness swaps the arm-B ``.mcp.json`` server command for this
wrapper when an overlay is injected.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra

# Module-level ``pydocs_mcp`` boundary: this overlay wrapper re-binds the
# product's ``tool_docs`` module attributes and delegates to the product's
# serve path, so it is inherently library-coupled. A base install without the
# [retrieval] extra gets the actionable install hint instead of a bare
# ModuleNotFoundError. (``pydocs_mcp.server`` stays deferred inside
# ``serve_with_overlay`` so a monkeypatch of ``server.run`` in tests takes
# effect — see the call-site note below.)
try:
    import pydocs_mcp.application.tool_docs as td
    from pydocs_mcp.db import cache_path_for_project
except ImportError as exc:
    raise_missing_retrieval_extra(exc)

from pydocs_eval.optimize.artifacts._delimited import parse_delimited
from pydocs_eval.optimize.artifacts.tool_docs import ToolDocsArtifact

# WHY: the delimited grammar prefixes each per-tool section header with this;
# stripping it maps a section key (``TOOL: get_why``) back to a ``TOOL_DOCS``
# key (``get_why``). Single source of truth so parse + re-bind stay aligned with
# the artifact's ``_TOOL_KEY``.
_TOOL_PREFIX = "TOOL: "
_SERVER_KEY = "SERVER_INSTRUCTIONS"


class OverlayValidationError(RuntimeError):
    """An overlay failed the §D2a/§D13 firewall — the server refuses to boot."""


def serve_with_overlay(*, project: Path, overlay: Path | None) -> None:
    """Inject ``overlay`` into ``tool_docs``, then serve the MCP server.

    With no overlay this is a byte-identical no-op wrapper around the normal
    serve path — the ``tool_docs`` module attributes are untouched. With an
    overlay it reads the file, runs it through the artifact firewall
    (``ToolDocsArtifact.validate``), and — only if clean — re-binds
    ``td.SERVER_INSTRUCTIONS`` and each ``td.TOOL_DOCS[name]`` from the parsed
    sections BEFORE delegating to ``pydocs_mcp.server.run`` (spec §D6). A dirty
    overlay raises :class:`OverlayValidationError` and never serves.

    Args:
        project: the repository root to serve (resolved to its cache db).
        overlay: a delimited ``tool_docs`` overlay file, or ``None`` for a
            plain serve with the live product surface.

    Raises:
        OverlayValidationError: the overlay violated the §D2a/§D13 firewall;
            the offending sections are named in the message (fail-closed).
    """
    if overlay is not None:
        _apply_overlay(overlay)
    # Import at call time so a test that monkeypatches ``pydocs_mcp.server.run``
    # patches the attribute this call resolves (mirrors the CLI's own late
    # ``from pydocs_mcp.server import run`` in ``__main__._serve_run``).
    from pydocs_mcp.server import run

    # Keyword ``db_path`` (not positional) so the delegated call is unambiguous
    # against ``server.run``'s ``(db_path=None, config_path=None, *, ...)`` shape.
    run(db_path=cache_path_for_project(project.resolve()))


def _apply_overlay(overlay: Path) -> None:
    """Validate ``overlay`` and re-bind the ``tool_docs`` module attributes.

    Fail-closed: any firewall violation raises before a single attribute is
    mutated, so a rejected overlay leaves the live surface untouched.
    """
    text = overlay.read_text(encoding="utf-8")
    violations = ToolDocsArtifact().with_content(text).validate()
    if violations:
        raise OverlayValidationError(
            f"overlay {overlay} failed the tool_docs firewall: " + "; ".join(violations)
        )
    sections = parse_delimited(text)
    td.SERVER_INSTRUCTIONS = sections[_SERVER_KEY]
    for key, content in sections.items():
        if key.startswith(_TOOL_PREFIX):
            td.TOOL_DOCS[key[len(_TOOL_PREFIX) :]] = content


def main(argv: list[str] | None = None) -> None:
    """CLI entry: ``python -m pydocs_eval.optimize._overlay_server <project> [--overlay F]``.

    Lets the paired-agent fitness's arm-B ``.mcp.json`` boot this wrapper in
    place of ``pydocs_mcp serve`` when an overlay is injected.
    """
    parser = argparse.ArgumentParser(
        prog="pydocs_eval.optimize._overlay_server",
        description="Serve pydocs-mcp with an injected tool_docs overlay (spec §D6).",
    )
    parser.add_argument("project", type=Path, help="Repository root to serve.")
    parser.add_argument(
        "--overlay",
        type=Path,
        default=None,
        help="Delimited tool_docs overlay file (omit for a plain serve).",
    )
    args = parser.parse_args(argv)
    serve_with_overlay(project=args.project, overlay=args.overlay)


if __name__ == "__main__":  # pragma: no cover - module CLI entry
    main()
