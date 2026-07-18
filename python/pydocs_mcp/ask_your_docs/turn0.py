"""Turn-0 context gate for the ask-your-docs channel (ADR 0008 §Decision 5.i).

Core-only imports (no langgraph / streamlit): the flag gate and the pack
build are testable without the ``[ask-your-docs]`` extra, keeping the lazy-
import contract of this subpackage. ``agent.build_agent`` calls this once at
its single prompt-assembly site.
"""

from __future__ import annotations

from pathlib import Path


async def turn0_context_for_workspace(workspace: str, config_path: str | None) -> str | None:
    """The turn-0 pack for ``workspace``'s default bundle, or ``None`` when off.

    ``None`` (the shipped default — ``serve.turn0_context.enabled: false``)
    means the caller's prompt assembly stays byte-identical. The default
    bundle is the FIRST workspace bundle — the same ``services[0]`` rule the
    MCP server uses — so both channels describe the same project. A broken /
    empty workspace with the flag ON propagates the discovery error: the
    serve subprocess would fail on the same workspace, and a silent skip
    would contaminate the ablation arms with an unmarked control.
    """
    from pydocs_mcp.retrieval.config import AppConfig

    # build_agent carries the config path as a str (subprocess argv shape);
    # AppConfig.load wants a Path and hard-fails on a missing explicit path.
    config = AppConfig.load(explicit_path=Path(config_path) if config_path else None)
    settings = config.serve.turn0_context
    if not settings.enabled:
        return None

    from pydocs_mcp.application.description_override import apply_descriptions_override

    # The pack embeds the LIVE ``TURN0_PREAMBLE``; without applying the
    # configured description source (ADR 0006) the injected context would
    # carry the packaged preamble while the serve subprocess serves the
    # override — the two channels must describe the same surface.
    apply_descriptions_override(cli_path=None, configured_path=config.serve.descriptions_path)

    from pydocs_mcp.application.turn0_context import build_turn0_context
    from pydocs_mcp.multirepo import discover_workspace
    from pydocs_mcp.storage.factories import (
        build_sqlite_overview_service,
        build_sqlite_uow_factory,
    )

    first = discover_workspace(Path(workspace).expanduser())[0]
    project_root = Path(first.metadata.project_root or ".")
    overview = build_sqlite_overview_service(
        first.db_path, project_root=project_root, config=config
    )
    return await build_turn0_context(
        uow_factory=build_sqlite_uow_factory(first.db_path),
        overview=overview,
        budget_tokens=settings.budget_tokens,
    )
