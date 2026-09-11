"""Environment for OUR ``pydocs_mcp serve`` child spawned over an in-process MCP stdio connection.

WHY this exists (0.6.0 bug): the MCP SDK starts a stdio child from
``get_default_environment()`` (HOME LOGNAME PATH SHELL TERM USER on POSIX) plus
the connection's ``env`` map, never ``os.environ`` (``mcp/client/stdio``
``stdio_client``, mcp 1.27–1.30). With no map, the serve child lost the embedder
key: ``OpenAIEmbedder`` raised at startup and the client saw "McpError:
Connection closed". It also lost ``TMPDIR`` (FastEmbed's default cache),
``PYDOCS_CONFIG_PATH`` / ``PYDOCS_CACHE_DIR``, proxies and CA bundles.

WHY inherit rather than allowlist: the set a serve child needs is open-ended
(every provider's key variable, HF_* / FASTEMBED_*, SSL_* / *_PROXY, PYDOCS_*
overlays), and an allowlist drifts one bug report at a time. The child is our
own code, run by the same interpreter as the same user; the SDK's allowlist
protects clients that launch THIRD-PARTY servers.

Scope: pydocs-mcp's own serve child only. Never use this for a third-party
server, and never write the result to a file: the composed CLI harness's
``.mcp.json`` persists in the trace dir, so it keeps its trace-only map.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
from collections.abc import Mapping
from types import MappingProxyType

from pydocs_mcp.db import CACHE_DIR_ENV_VAR
from pydocs_mcp.observability.trace_env import TRACE_ENABLED_ENV_VAR

# Null object for "no overlay" — never None (CLAUDE.md §Null Object pattern).
NO_ENV_OVERLAY: Mapping[str, str] = MappingProxyType({})

# WHY the whole section, case-insensitively: pydantic-settings matches env names
# case-insensitively and decodes a complex field from one JSON value, so
# `pydocs_trace__enabled` and `PYDOCS_TRACE='{...}'` reach TraceConfig too
# (probed 2026-09-10). An inherited identity fails a per-tool-call spawn with
# TraceStartupError (no dir) or TrajectoryIdReuseError (second spawn). Only the
# explicit overlay (`observability.trace_env`) may set it.
_TRACE_SECTION_ENV_VAR = TRACE_ENABLED_ENV_VAR.rsplit("__", 1)[0]  # "PYDOCS_TRACE", derived
_TRACE_FIELD_PREFIX = f"{_TRACE_SECTION_ENV_VAR}__"

# WHY the eval binding seals the config tier: the binding is settings-in,
# trajectory-out. A shell's PYDOCS_* overlay or OPENAI_BASE_URL would change what
# an arm measures without moving its hash. Sealed, the child's configuration is
# its --config YAML alone, exactly as before 0.6.1. PYDOCS_CACHE_DIR stays: it
# is a location, not configuration (db.py sandbox channel). OPENAI_BASE_URL /
# LLM_MODEL are the pair `binding._llm_connection_for_run` already blocks for the
# chat endpoint. The child's OpenAI SDK falls back to OPENAI_BASE_URL when YAML
# leaves base_url null.
_CONFIG_TIER_PREFIX = "PYDOCS_"  # == AppConfig env_prefix (parity test)
_SEALED_TIER_KEEPS = frozenset({CACHE_DIR_ENV_VAR})
_ENDPOINT_TIER_ENV_VARS = frozenset({"OPENAI_BASE_URL", "LLM_MODEL"})

# Mirrors langchain_mcp_adapters.sessions._BRACED_VAR_RE (0.3.x; pinned by
# test_adapter_brace_pattern_matches_ours). The adapter rewrites ${VAR} in every
# env value, corrupting a value that merely contains the sequence, and logs an
# unresolved one IN FULL at WARNING. A secret must never ride that path.
_ADAPTER_EXPANDED_REF = re.compile(r"\$\{[^}]+\}")

# Mirrors the SDK's own get_default_environment, which skips "()"-prefixed
# values; bash exports functions as BASH_FUNC_<name>%%, which a Python child
# never needs.
_SHELL_FUNCTION_VALUE_PREFIX = "()"
_SHELL_FUNCTION_NAME_PREFIX = "BASH_FUNC_"

_WITHHELD_EVENT = "serve_child_env_withheld"
_REASON_SHELL_FUNCTION = "shell_function"
_REASON_OVERLAID = "overlaid"
_REASON_TRACE_IDENTITY = "trace_identity"
_REASON_ADAPTER_REF = "adapter_expanded_ref"
_REASON_ENDPOINT_SEALED = "endpoint_tier_sealed"
_REASON_CONFIG_SEALED = "config_tier_sealed"
_QUIET_REASONS = frozenset({_REASON_SHELL_FUNCTION, _REASON_OVERLAID})  # logged at DEBUG

WithheldNames = tuple[tuple[str, tuple[str, ...]], ...]

log = logging.getLogger("pydocs-mcp.harness.serve-child-env")


def serve_child_env(
    overlay: Mapping[str, str] = NO_ENV_OVERLAY,
    *,
    environ: Mapping[str, str] = os.environ,
    seal_config_tier: bool = False,
) -> dict[str, str]:
    """Parent env for OUR serve child, ``overlay`` on top; always a fresh dict.

    Withholds every ``PYDOCS_TRACE*`` spelling, exported shell functions, values
    the MCP adapter would expand (``${...}``) and any inherited spelling of an
    overlay name. ``seal_config_tier`` (the eval binding) also withholds
    ``PYDOCS_*`` except ``PYDOCS_CACHE_DIR``, and ``OPENAI_BASE_URL`` /
    ``LLM_MODEL``. One names-only JSON log line lists what was withheld.

    Example:
        >>> serve_child_env({"PYDOCS_TRACE__DIR": "/t"},
        ...                 environ={"OPENROUTER_API_KEY": "k", "pydocs_trace__dir": "/old"})
        {'OPENROUTER_API_KEY': 'k', 'PYDOCS_TRACE__DIR': '/t'}
    """
    overlaid = frozenset(name.upper() for name in overlay)
    kept: dict[str, str] = {}
    withheld: dict[str, list[str]] = {}
    for name, value in environ.items():
        reason = _withhold_reason(name, value, overlaid=overlaid, seal_config_tier=seal_config_tier)
        if reason is None:
            kept[name] = value
        else:
            withheld.setdefault(reason, []).append(name)
    _log_withheld_once(seal_config_tier, _frozen_withheld(withheld))
    return {**kept, **overlay}


def _withhold_reason(
    name: str, value: str, *, overlaid: frozenset[str], seal_config_tier: bool
) -> str | None:
    """Why ``name`` must not reach the child, or None to pass it through."""
    upper = name.upper()
    if _is_shell_function(upper, value):
        return _REASON_SHELL_FUNCTION
    if upper in overlaid:
        return _REASON_OVERLAID
    if upper == _TRACE_SECTION_ENV_VAR or upper.startswith(_TRACE_FIELD_PREFIX):
        return _REASON_TRACE_IDENTITY
    sealed = _sealed_tier_reason(upper) if seal_config_tier else None
    if sealed is not None:
        return sealed
    return _REASON_ADAPTER_REF if _ADAPTER_EXPANDED_REF.search(value) else None


def _is_shell_function(upper: str, value: str) -> bool:
    """An exported shell function, by bash's name mangling or the SDK's value test."""
    return upper.startswith(_SHELL_FUNCTION_NAME_PREFIX) or value.startswith(
        _SHELL_FUNCTION_VALUE_PREFIX
    )


def _sealed_tier_reason(upper: str) -> str | None:
    """The sealed-tier verdict for an upper-cased name: endpoint pair, then ``PYDOCS_*``."""
    if upper in _ENDPOINT_TIER_ENV_VARS:
        return _REASON_ENDPOINT_SEALED
    if upper.startswith(_CONFIG_TIER_PREFIX) and upper not in _SEALED_TIER_KEEPS:
        return _REASON_CONFIG_SEALED
    return None


def _frozen_withheld(withheld: Mapping[str, list[str]]) -> WithheldNames:
    """A sorted, hashable view of ``{reason: [names]}`` — the log memo's key."""
    return tuple(sorted((reason, tuple(sorted(names))) for reason, names in withheld.items()))


# WHY memoized: the eval binding spawns once per rollout, so a 1300-record
# campaign would print 1300 identical warnings (the sibling of
# binding_llm_block._llm_block_from_config_file's one-warning-per-run memo). The key holds
# NAMES only, never values (H4/G8).
@functools.cache
def _log_withheld_once(sealed: bool, withheld: WithheldNames) -> None:
    """One JSON line per distinct withheld set; WARNING unless every reason is quiet."""
    if not withheld:
        return
    payload = {
        "event": _WITHHELD_EVENT,
        "sealed_config_tier": sealed,
        "withheld": {reason: list(names) for reason, names in withheld},
    }
    quiet = all(reason in _QUIET_REASONS for reason, _names in withheld)
    log.log(logging.DEBUG if quiet else logging.WARNING, json.dumps(payload))


def clear_serve_child_env_log_memo() -> None:
    """Forget which withheld sets were logged (test seam)."""
    _log_withheld_once.cache_clear()
