"""``search_codebase``'s zero-hit composition, and the ``kind="api"`` zero-hit fallback (ADR 0027).

Three ADR 0007 rules shape a ``search_codebase`` response that found nothing,
and this module is their one home:

- ``search_zero_hit`` (Z) — the overview pointer under the empty literal;
- ``api_zero_hit_retry_any`` (R) — a ready-to-issue ``kind="any"`` retry line;
- ``api_zero_hit_fallback_any`` (F, ADR 0027) — instead of hinting at that
  retry, run it: a ``kind="api"`` request whose API pass (the member lookup)
  returned no ``items[]`` row is answered by the same request with only
  ``kind`` changed to ``"any"`` (the fallback pass) — same query string, same
  selectors, same ``limit``, same branch pins, same multi-project body.

The trigger is row-based (D2): a response that carries API rows is never
replaced, even when the token budget rendered none of them. Each pass renders
under its own truncation ledger and only the served pass's ledger is replayed
into the response's (D9), so the footer, ``meta.truncated`` and the rendered
row count describe the body actually served — one clamp line, not two. Rules
are logged only once the fallback outcome is known, so a fallback that raises
logs nothing and fails loudly as ``ServiceUnavailableError`` (D8, D12). With F
off, or for any other ``kind``, the composition is exactly the pre-ADR-0027 one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.multi_project_search import EMPTY_SEARCH_MESSAGES
from pydocs_mcp.application.pointer_bundles import render_pointer_bundle
from pydocs_mcp.application.suggestions import (
    API_ZERO_HIT_FALLBACK_ANY_RULE,
    API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION,
    API_ZERO_HIT_FALLBACK_SERVED_SUGGESTION,
    API_ZERO_HIT_RETRY_ANY_RULE,
    SEARCH_ZERO_HIT_SUGGESTION,
    api_zero_hit_retry_any_suggestion,
    log_suggestion_fired,
)
from pydocs_mcp.application.truncation import TruncationLedger, get_active_ledger, ledger_scope
from pydocs_mcp.pointer_table import PointerTableConfig, ResponseKind
from pydocs_mcp.retrieval.config import SuggestionsConfig

# One search body triple: (text, items[] rows, meta extras).
_SearchBodyTriple = tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]
# The multi-project search body, already bound to the request's branch pins.
SearchBodyRunner = Callable[[SearchInput], Awaitable[_SearchBodyTriple]]

_TOOL = "search_codebase"
_FALLBACK_KIND = "any"
# D12: the fixed prefix of a failed fallback's error — what trace readers
# count as a raw API zero hit that errored.
API_ZERO_HIT_FALLBACK_FAILURE_PREFIX = (
    'search_codebase(kind="api") found no symbols; its kind="any" fallback failed'
)


@dataclass(frozen=True, slots=True)
class _IsolatedSearchPass:
    """One pass's body plus the ledger it rendered under, not yet replayed."""

    triple: _SearchBodyTriple
    ledger: TruncationLedger

    @property
    def items(self) -> tuple[dict[str, Any], ...]:
        return self.triple[1]

    def replay_into_response_ledger(self) -> None:
        """Copy this pass's entries, in order, and its rendered-row count into
        the response's ledger — the served pass's only (D9)."""
        response_ledger = get_active_ledger()
        if response_ledger is None:
            return
        for entry in self.ledger.entries:
            response_ledger.record(entry)
        if self.ledger.rendered_rows is not None:
            response_ledger.record_rendered_rows(self.ledger.rendered_rows)


async def _run_search_pass(run_body: SearchBodyRunner, payload: SearchInput) -> _IsolatedSearchPass:
    with ledger_scope() as ledger:
        triple = await run_body(payload)
    return _IsolatedSearchPass(triple, ledger)


async def _run_fallback_pass(
    run_body: SearchBodyRunner, payload: SearchInput
) -> _IsolatedSearchPass:
    """The same request with only ``kind`` changed; any ``Exception`` becomes
    the tool's typed error, chained (``BaseException`` such as cancellation
    propagates untouched)."""
    fallback_payload = payload.model_copy(update={"kind": _FALLBACK_KIND})
    try:
        return await _run_search_pass(run_body, fallback_payload)
    except Exception as exc:
        message = f"{API_ZERO_HIT_FALLBACK_FAILURE_PREFIX}: {type(exc).__name__}: {exc}"
        raise ServiceUnavailableError(message) from exc


def _served_fallback_response(fallback: _SearchBodyTriple) -> _SearchBodyTriple:
    """Outcome S: the label, then the ``kind="any"`` body, rows and extras."""
    body, items, extras = fallback
    log_suggestion_fired(_TOOL, API_ZERO_HIT_FALLBACK_ANY_RULE)
    suggestion = API_ZERO_HIT_FALLBACK_SERVED_SUGGESTION
    return f"{suggestion}\n{body}", items, {**extras, "suggestion": suggestion}


@dataclass(frozen=True, slots=True)
class SearchZeroHitResponder:
    """Composes one ``search_codebase`` response's zero-hit rules (Z, R, F)."""

    suggestions: SuggestionsConfig
    pointers: PointerTableConfig

    async def respond(
        self, payload: SearchInput, run_body: SearchBodyRunner, *, selector: str
    ) -> _SearchBodyTriple:
        """``selector`` is the request's branch selector, which the retry hint
        renders (the CLI ``--branch`` override included)."""
        if not self._fallback_applies(payload):
            return self._with_zero_hit_hints(payload, await run_body(payload), selector)
        api_pass = await _run_search_pass(run_body, payload)
        if api_pass.items:
            # Outcome H: rows mean no zero hit, whatever the text shows (SC1).
            api_pass.replay_into_response_ledger()
            return self._with_zero_hit_hints(payload, api_pass.triple, selector)
        return await self._answer_api_zero_hit(payload, run_body, api_pass)

    def _fallback_applies(self, payload: SearchInput) -> bool:
        return payload.kind == "api" and self.suggestions.api_zero_hit_fallback_any

    async def _answer_api_zero_hit(
        self, payload: SearchInput, run_body: SearchBodyRunner, api_pass: _IsolatedSearchPass
    ) -> _SearchBodyTriple:
        fallback = await _run_fallback_pass(run_body, payload)
        if fallback.items:
            fallback.replay_into_response_ledger()
            return _served_fallback_response(fallback.triple)
        api_pass.replay_into_response_ledger()
        return self._both_passes_empty_response(api_pass.triple)

    def _both_passes_empty_response(self, api_body: _SearchBodyTriple) -> _SearchBodyTriple:
        """Outcome E: today's zero-hit body with F_EMPTY where the retry line
        would be; Z (when on) is logged before F, R never."""
        body, items, extras = api_body
        text = f"{self._with_overview_pointer(body)}\n{API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION}"
        log_suggestion_fired(_TOOL, API_ZERO_HIT_FALLBACK_ANY_RULE)
        suggestion = API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION
        return text, items, {**extras, "suggestion": suggestion}

    def _with_overview_pointer(self, body: str) -> str:
        """``body`` plus the zero-hit overview pointer when Z is on (logged)."""
        if not self.suggestions.search_zero_hit:
            return body
        log_suggestion_fired(_TOOL, "search_zero_hit")
        zero_hit = self.pointers.row_for(ResponseKind.ZERO_HIT)
        return f"{body}\n{render_pointer_bundle(zero_hit, '')}"

    def _with_zero_hit_hints(
        self, payload: SearchInput, result: _SearchBodyTriple, selector: str
    ) -> _SearchBodyTriple:
        """The pre-ADR-0027 composition, byte for byte: an empty-literal body
        gains the overview pointer (Z) and, on ``kind="api"``, the retry line
        (R); meta.suggestion carries R's text over Z's (ADR 0007 amendment
        2026-11-08). Detected on the body text, as it always was."""
        body, items, extras = result
        fire_retry = payload.kind == "api" and self.suggestions.api_zero_hit_retry_any
        if body not in EMPTY_SEARCH_MESSAGES:
            return result
        if not self.suggestions.search_zero_hit and not fire_retry:
            return result
        text = self._with_overview_pointer(body)
        suggestion = SEARCH_ZERO_HIT_SUGGESTION if self.suggestions.search_zero_hit else None
        if fire_retry:
            retry = self._retry_hint(payload, selector)
            text, suggestion = f"{text}\n{retry}", retry
        return text, items, {**extras, "suggestion": suggestion}

    def _retry_hint(self, payload: SearchInput, selector: str) -> str:
        log_suggestion_fired(_TOOL, API_ZERO_HIT_RETRY_ANY_RULE)
        return api_zero_hit_retry_any_suggestion(
            payload.query,
            scope=payload.scope,
            package=payload.package,
            branch=selector,
            project=payload.project,
        )
