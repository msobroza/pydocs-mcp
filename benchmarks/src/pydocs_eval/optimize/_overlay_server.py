"""Arm-B ``tool_docs`` overlay server wrapper (spec §D6).

**Why this shape:** the spec weighed two ways to inject an optimized
``tool_docs`` surface into the served MCP server and recorded this wrapper —
not a product config field — as the smaller-footprint alternative. It works
because ``pydocs_mcp.server.run`` reads ``SERVER_INSTRUCTIONS`` and
``TOOL_DOCS`` from ``pydocs_mcp.application.tool_docs`` via function-local
imports *at call time* (deliberately, so an earlier description-source
rebinding is what reaches the wire): rebinding those module attributes BEFORE
calling ``server.run`` injects the overlay without any MCP tool param or
harness-only config field.

Since ADR 0006 the product ships an official rebinding path —
:func:`pydocs_mcp.application.description_source.apply_source`, the same
read → parse → validate → rebind loader behind ``serve --descriptions`` — and
this wrapper now routes the overlay through it, so an overlay run and a
production override can never diverge in how the live surface is bound, and
the ``descriptions artifact <hash>`` startup line (computed from the LIVE
attributes) stays truthful for overlay runs.

**Fail-closed (spec §D6):** an overlay that violates the §D2a/§D13 firewall
(``ToolDocsArtifact.validate``) raises ``OverlayValidationError`` and the
server NEVER boots — the harness must never serve a candidate surface that
would fail the product lint a human is asked to land. The product loader's
own validation backstops the firewall under the same error type.

**Serve-path parity:** the wrapper resolves the project path to its SQLite
cache db through :func:`pydocs_mcp.db.cache_path_for_project` — the SAME
helper the CLI ``serve`` command uses (see ``__main__._project_and_db``) — so
the per-project ``<dirname>_<hash>.db`` path-hash resolution never forks
between a normal serve and an overlay serve.

Launch as ``python -m pydocs_eval.optimize._overlay_server <project> --overlay
<file>``; the Task-6 ``ArtifactInjection.overlay_path`` plugs in here — the
paired-agent fitness swaps the arm-B ``.mcp.json`` server command for this
wrapper when an overlay is injected.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra

# Module-level ``pydocs_mcp`` boundary: this overlay wrapper re-binds the
# product's ``tool_docs`` module attributes (through the product's own
# ``apply_source`` loader) and delegates to the product's serve path, so it is
# inherently library-coupled. A base install without the [retrieval] extra
# gets the actionable install hint instead of a bare ModuleNotFoundError.
# (``pydocs_mcp.server`` stays deferred inside ``serve_with_overlay`` so a
# monkeypatch of ``server.run`` in tests takes effect — see the call-site
# note below.)
try:
    import pydocs_mcp.application.tool_docs as td
    from pydocs_mcp.application.description_source import (
        SESSION_START_PREAMBLE_HEADER,
        DescriptionSourceError,
        apply_source,
    )
    from pydocs_mcp.db import cache_path_for_project
except ImportError as exc:
    raise_missing_retrieval_extra(exc)

from pydocs_eval.optimize.artifacts._delimited import parse_delimited, render_delimited
from pydocs_eval.optimize.artifacts.tool_docs import ToolDocsArtifact

# WHY: the delimited grammar prefixes each per-tool section header with this;
# it identifies the tool sections whose trailing terminator the product loader
# re-attaches (see ``_as_product_document``). Single source of truth so the
# bridge stays aligned with the artifact's ``_TOOL_KEY``.
_TOOL_PREFIX = "TOOL: "


class OverlayValidationError(RuntimeError):
    """An overlay failed the §D2a/§D13 firewall — the server refuses to boot."""


def serve_with_overlay(*, project: Path, overlay: Path | None) -> None:
    """Inject ``overlay`` into ``tool_docs``, then serve the MCP server.

    With no overlay this is a byte-identical no-op wrapper around the normal
    serve path — the ``tool_docs`` module attributes are untouched. With an
    overlay it reads the file, runs it through the artifact firewall
    (``ToolDocsArtifact.validate``), and — only if clean — rebinds the live
    ``tool_docs`` attributes through the product's ``apply_source`` loader
    BEFORE delegating to ``pydocs_mcp.server.run`` (spec §D6). A dirty
    overlay raises :class:`OverlayValidationError` and never serves.

    Args:
        project: the repository root to serve (resolved to its cache db).
        overlay: a delimited ``tool_docs`` overlay file, or ``None`` for a
            plain serve with the live product surface.

    Raises:
        OverlayValidationError: the overlay violated the §D2a/§D13 firewall
            (or the product loader's backstop validation); the offending
            sections are named in the message (fail-closed).
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
    """Validate ``overlay``, then rebind through the product loader.

    Fail-closed: the §D2a/§D13 artifact firewall runs first and any violation
    raises before a single attribute is mutated. The rebinding itself goes
    through the product's :func:`apply_source` (ADR 0006) — the SAME
    read → parse → validate → rebind path a ``--descriptions`` override
    takes — so overlay runs and production overrides bind the live surface
    identically and ``current_artifact_hash`` stays truthful.
    """
    text = overlay.read_text(encoding="utf-8")
    violations = ToolDocsArtifact().with_content(text).validate()
    if violations:
        raise OverlayValidationError(
            f"overlay {overlay} failed the tool_docs firewall: " + "; ".join(violations)
        )
    document = _as_product_document(text)
    # ``apply_source`` takes a Path (it is the ``--descriptions`` file loader),
    # so the bridged document is staged as a real file for the duration of the
    # call.
    with tempfile.TemporaryDirectory(prefix="pydocs-eval-overlay-") as tmp_dir:
        document_path = Path(tmp_dir) / "descriptions.md"
        document_path.write_text(document, encoding="utf-8")
        try:
            apply_source(document_path)
        except DescriptionSourceError as exc:
            # The overlay passed the artifact firewall but the product loader
            # still refused it — unreachable unless the firewall and the
            # product validation drift. Fail closed under the documented
            # error type rather than half-serving.
            raise OverlayValidationError(
                f"overlay {overlay} failed the product description validation: {exc}"
            ) from exc


def _as_product_document(overlay_text: str) -> str:
    """Bridge a ``tool_docs`` overlay to the full product-document shape.

    The overlay optimizes only the ``SERVER_INSTRUCTIONS`` + per-tool
    sections; the product loader validates the FULL canonical document, so
    the live ``SESSION_START_PREAMBLE`` is carried through unchanged. Tool sections
    drop their single trailing newline because ``apply_source`` re-attaches
    the terminator when projecting sections onto ``TOOL_DOCS`` — leaving it
    in would double-terminate every description (and a candidate that omitted
    it is normalized to the product's exactly-one-terminator invariant).
    """
    sections = parse_delimited(overlay_text)
    document = {
        key: content.removesuffix("\n") if key.startswith(_TOOL_PREFIX) else content
        for key, content in sections.items()
    }
    document[SESSION_START_PREAMBLE_HEADER] = td.SESSION_START_PREAMBLE
    return render_delimited(document)


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
