"""Deterministic routing suggestions — fixed rendering + fired-rule log (ADR 0007).

The three rule texts are deterministic-behavior output, NOT optimizable
description text: they sit on the description-source exception list next to
the envelope rendering strings (freshness header, truncation footer, pointer
templates). Letting the optimizer mutate them would blur the machinery/model
boundary requirement R7 keeps sharp — a transcript line starting with the
fixed ``[suggestion:`` prefix is always a harness-initiated nudge, never
model-earned routing. Per-rule on/off flags live in YAML
(``output.suggestions.*``, ``SuggestionsConfig``); each fired rule emits one
structured log line here so Phase 2 analysis can attribute outcomes.
"""

from __future__ import annotations

import json
import logging

from pydocs_mcp.application.mcp_inputs import SearchInput

log = logging.getLogger(__name__)

# The renderer's scope omit-when-default guard reads this one constant so the
# retry rendering never silently emits-or-omits the wrong thing if
# ``SearchInput.scope``'s default changes. Direct pydantic-v2
# ``model_fields[...].default`` lookup (same precedent used throughout the
# codebase — see ``tests/test_cli_mcp_parity.py`` and
# ``python/pydocs_mcp/application/tool_response.py``). Not a Protocol, not an
# injection point.
_SEARCH_SCOPE_DEFAULT: str = SearchInput.model_fields["scope"].default

# Deterministic marker prefix — every server-initiated suggestion line starts
# with this so transcript analysis can separate machinery from model (R7).
SUGGESTION_PREFIX = "[suggestion:"

GREP_ZERO_HIT_SUGGESTION = (
    '[suggestion: no exact matches — for conceptual queries, try search_codebase(query="...")]'
)
GREP_TRUNCATED_SUGGESTION = (
    "[suggestion: output cut by head_limit — narrow with path= or glob=, or raise head_limit=]"
)
SEARCH_ZERO_HIT_SUGGESTION = "[suggestion: zero hits — orient with get_overview()]"

# On a ``kind="api"`` zero hit, the generic overview pointer stays (recovery,
# ADR 0007) and a kind-aware retry at ``kind="any"`` is offered with the
# preserved query VALUE + non-default selectors. The two rules are
# independently flaggable (ADR 0007) so each hint's contribution is measurable
# without touching the other. The rendered value is JSON-escaped (RFC 8259) so
# embedded quotes / backslashes / newlines / tabs / NUL / non-ASCII round-trip
# when an agent re-parses the call.
API_ZERO_HIT_RETRY_ANY_RULE = "api_zero_hit_retry_any"


def _json_escape_arg(value: str) -> str:
    """Render ``value`` as a safe JSON double-quoted string argument that round-trips.

    The retry call carries user input (query + selectors) inside a rendered
    ``search_codebase(name="value", ...)`` call — a raw interpolation breaks
    on an embedded ``"`` / ``\\`` / newline / ``]`` and may silently execute a
    different query. Delegating to :func:`json.dumps` with ``ensure_ascii=False``
    emits one valid RFC 8259 string literal: structural escapes (``\\"``,
    ``\\\\``, ``\\n``, ``\\t``, ``\\u0000`` ...) are applied, non-ASCII
    characters (combining marks, non-Latin scripts, RTL, zero-width joiners)
    pass through as themselves. An agent parsing the hint with ``json.loads``
    or Python's double-quoted-string parser recovers the original value
    byte-for-byte (the acceptance tests pin that round-trip property).
    """
    return json.dumps(value, ensure_ascii=False)


def api_zero_hit_retry_any_suggestion(
    query: str,
    *,
    scope: str = _SEARCH_SCOPE_DEFAULT,
    package: str = "",
    branch: str = "",
    project: str = "",
) -> str:
    """The fixed text of the ``api_zero_hit_retry_any`` rule.

    Carries a ready-to-issue ``search_codebase(kind="any", ...)`` call with
    the request's query VALUE and its non-default selectors preserved. The
    defaults (``scope=_SEARCH_SCOPE_DEFAULT``, ``package=""``, ``branch=""``,
    ``project=""``) are omitted so the rendered line stays concise; omitting a
    NON-default would silently widen the retry's corpus and is out of contract.
    """
    parts: list[str] = [f"query={_json_escape_arg(query)}", 'kind="any"']
    if scope != _SEARCH_SCOPE_DEFAULT:
        parts.append(f"scope={_json_escape_arg(scope)}")
    if package:
        parts.append(f"package={_json_escape_arg(package)}")
    if branch:
        parts.append(f"branch={_json_escape_arg(branch)}")
    if project:
        parts.append(f"project={_json_escape_arg(project)}")
    call = f"search_codebase({', '.join(parts)})"
    return (
        f'[suggestion: kind="api" matched nothing — retry with {call} '
        f"for a descriptive query, or give a name fragment]"
    )


# #311 (spec §6.11): the default selector fell back to the default branch
# because the checked-out branch has no index yet. Rule name and text live
# here with the other three; the envelope mirrors it into meta.suggestion.
CHECKOUT_NOT_INDEXED_RULE = "checkout_not_indexed"


def checkout_not_indexed_suggestion(branch: str) -> str:
    """The fixed text of the ``checkout_not_indexed`` rule for ``branch``."""
    return (
        f"[suggestion: checked-out branch '{branch}' is not indexed; "
        f"run: pydocs-mcp index . --branch {branch}]"
    )


# #318 (spec §6.8b layer 1): the resolved branch is behind its upstream as of
# the last fetch. Fires only when no other suggestion did; gated by
# ``git.remote.behind_hint``, the layer's own switch.
BEHIND_UPSTREAM_RULE = "behind_upstream"
# How to sync, by where the branch is checked out: ``git pull`` syncs the
# served working tree's branch only (#318 review). Another worktree's branch is
# pulled there; a branch no worktree holds is fast-forwarded to the
# remote-tracking ref its count was read against, which git refuses for a
# branch checked out in the current worktree (and, since git 2.35, in any).
BEHIND_UPSTREAM_PULL = "git pull"
BEHIND_UPSTREAM_PULL_IN_ITS_WORKTREE = "git pull in the worktree that has it checked out"


def behind_upstream_fast_forward_command(branch: str, upstream: str) -> str:
    """Fast-forward ``branch`` to ``upstream`` without a checkout; git refuses a
    non-fast-forward (no ``+``) and touches no working tree."""
    return f"git fetch . {upstream}:{branch}"


def behind_upstream_suggestion_text(
    branch: str, upstream: str, behind: int, fetch_age: str | None, sync_command: str
) -> str:
    """The fixed text of the ``behind_upstream`` rule; ``fetch_age`` like ``2h``."""
    age = f" (last fetch {fetch_age} ago)" if fetch_age else ""
    return (
        f"[suggestion: branch '{branch}' is behind {upstream} by {behind}{age}; "
        f"run: {sync_command}]"
    )


def log_suggestion_fired(tool: str, rule: str) -> None:
    """One structured line per fired rule — the Phase 2 attribution input."""
    log.info(json.dumps({"event": "suggestion_fired", "tool": tool, "rule": rule}))
