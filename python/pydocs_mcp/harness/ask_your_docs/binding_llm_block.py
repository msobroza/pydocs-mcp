"""The eval binding's ``ask_your_docs.llm`` block source (design §4.11, R8/D8).

Moved out of ``binding`` to keep that module inside its line budget; ``binding``
re-imports :func:`connection_block_for_binding` and :func:`clear_config_block_cache`.
"""

from __future__ import annotations

import functools
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.retrieval.config.app_config import AppConfig
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

if TYPE_CHECKING:  # the settings type only; binding imports this module at runtime
    from pydocs_mcp.harness.ask_your_docs.binding import AskYourDocsRunnerSettings

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

# AppConfig's environment tier, spelled for THIS block (env_prefix 'PYDOCS_' +
# env_nested_delimiter '__'). THREE shapes reach ask_your_docs.llm and all three
# are matched case-insensitively, because pydantic-settings matches that way:
# one field (…__LLM__BASE_URL), the whole block as one JSON value (…__LLM — a
# complex field is decoded at ITS level, so no trailing '__'), and the whole
# section as JSON (…ASK_YOUR_DOCS, which MAY carry llm; only its value would
# say, and values are never read here). Probed 2026-09-10: all three win.
_ASK_LLM_ENV_PREFIX = "PYDOCS_ASK_YOUR_DOCS__LLM"
_ASK_SECTION_ENV_VAR = "PYDOCS_ASK_YOUR_DOCS"
_ENV_OVERLAY_EVENT = "binding_env_overlay_present"


def _warn_if_env_overlays_the_block(config_path: str) -> None:
    """Make ``AppConfig``'s environment tier VISIBLE (owner ruling 2026-09-10).

    Spec §4.11 mandates the ``AppConfig`` loader, which layers every
    ``PYDOCS_ASK_YOUR_DOCS…`` spelling above over the arm's YAML — so an
    exported variable changes what a campaign measures, and the memo below
    bakes it in for every record. The loader stays; this line is how a campaign
    log shows it. NAMES only, never values (H4): endpoints and token URLs.
    """
    overlaid = sorted(
        name
        for name in os.environ
        if (upper := name.upper()).startswith(_ASK_LLM_ENV_PREFIX) or upper == _ASK_SECTION_ENV_VAR
    )
    if not overlaid:
        return
    payload = {"event": _ENV_OVERLAY_EVENT, "variables": overlaid, "config_path": config_path}
    log.warning(json.dumps(payload))


# WHY memoized: one arm runs ONE settings mapping across every record, and
# AppConfig.load re-reads and re-validates the whole layered YAML — a
# 1300-record campaign would otherwise pay 1300 parses of a file that is fixed
# for the run (the sibling of §4.4's one-bearer-per-identity registry).
@functools.cache
def _llm_block_from_config_file(config_path: str) -> LlmConnectionConfig | None:
    """The ``ask_your_docs.llm`` block of one pydocs YAML, read once per process."""
    # Inside the memo on purpose: one warning per run, not one per record.
    _warn_if_env_overlays_the_block(config_path)
    return AppConfig.load(explicit_path=Path(config_path)).ask_your_docs.llm


def clear_config_block_cache() -> None:
    """Test seam: forget every loaded block (``clear_bearer_registry``'s sibling)."""
    _llm_block_from_config_file.cache_clear()


def connection_block_for_binding(
    settings: AskYourDocsRunnerSettings,
) -> LlmConnectionConfig | None:
    """The ``ask_your_docs.llm`` block this run uses (design §4.11, R8/D8).

    An arm may pin a block under ``harness: {llm: ...}``; otherwise it comes
    from the file named by ``pydocs_config`` — the same file the serve child is
    pointed at, but NOT the same layering: that child's environment tier is
    sealed (``serve_child_env``), while this load layers the parent's
    ``PYDOCS_ASK_YOUR_DOCS__LLM__*`` over the YAML (spec §4.11's loader;
    :func:`_warn_if_env_overlays_the_block` logs it). Read once per process
    (:func:`clear_config_block_cache` is the test seam). No file, no block ⇒
    ``None`` ⇒ the control arm's byte-identical build.
    """
    if settings.harness.llm is not None:
        return settings.harness.llm
    if settings.pydocs_config is None:
        return None
    return _llm_block_from_config_file(settings.pydocs_config)


__all__ = (
    "clear_config_block_cache",
    "connection_block_for_binding",
)
