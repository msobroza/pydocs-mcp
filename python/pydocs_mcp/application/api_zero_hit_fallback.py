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

A ``kind="api"`` request ends in one of four outcomes: a hit (the API pass
returned rows), served (no API row, the fallback served rows), empty (both
passes returned nothing) or failed (the fallback pass raised).

The trigger is row-based: a response that carries API rows is never
replaced, even when the token budget rendered none of them (the empty literal
over real rows keeps today's Z/R lines). Each pass renders under its own
truncation ledger and only the served pass's ledger is replayed into the
response's, so the footer, ``meta.truncated`` and the rendered row count
describe the body actually served — one clamp line, not two. Rules are logged
only once the fallback outcome is known, so a fallback that raises logs
nothing (fired rule ⇔ ``meta.suggestion`` holds on every response) and fails
loudly as ``ServiceUnavailableError`` instead of degrading to the old hint
(CLAUDE.md: no silent fallbacks). With F off, or for any other ``kind``, the
composition is exactly the pre-ADR-0027 one.
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
    SEARCH_ZERO_HIT_RULE,
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
# The fixed prefix of a failed fallback's error (ADR 0027) — trace readers
# count an error carrying it as a raw API zero hit.
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
        the response's ledger — called for the served pass only, so the
        footer never describes a body the response does not carry."""
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


def _fallback_labelled(text: str, result: _SearchBodyTriple, label: str) -> _SearchBodyTriple:
    """``result``'s rows and extras under ``text``, with ``label`` as
    meta.suggestion — logged here, once, after the fallback pass returned."""
    _body, items, extras = result
    log_suggestion_fired(_TOOL, API_ZERO_HIT_FALLBACK_ANY_RULE)
    return text, items, {**extras, "suggestion": label}


def _served_fallback_response(fallback: _SearchBodyTriple) -> _SearchBodyTriple:
    """Served: the label line, then the ``kind="any"`` body, rows and extras."""
    label = API_ZERO_HIT_FALLBACK_SERVED_SUGGESTION
    return _fallback_labelled(f"{label}\n{fallback[0]}", fallback, label)


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
            # A hit: rows mean no zero hit, whatever the text shows — so the
            # budget corner keeps today's body-text Z/R lines (ADR 0027).
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
        """Empty: today's zero-hit body with the empty label where the retry
        line would be; Z (when on) is logged before F, R never."""
        label = API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION
        text = f"{self._with_overview_pointer(api_body[0])}\n{label}"
        return _fallback_labelled(text, api_body, label)

    def _with_overview_pointer(self, body: str) -> str:
        """``body`` plus the zero-hit overview pointer when Z is on (logged)."""
        if not self.suggestions.search_zero_hit:
            return body
        log_suggestion_fired(_TOOL, SEARCH_ZERO_HIT_RULE)
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
