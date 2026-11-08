"""KAF-04 final-quality follow-up — independence regression for the two
zero-hit suggestion flags at the public ``ToolRouter.search_codebase`` boundary.

Whole-branch review (post-``340913f0``) observed that the existing
``kind="api"`` zero-hit tests pin three of the four possible
``(search_zero_hit, api_zero_hit_retry_any)`` combinations: ``(True, True)``,
``(True, False)``, ``(False, False)``. The fourth combination —
``(False, True)`` — is the asymmetric case: the generic ``search_zero_hit``
overview pointer is OFF while the kind-aware retry is ON. The product
already handles this correctly in ``application/tool_router.py:272–294``
(the two ``fire_generic`` / ``fire_api`` branches are independently gated),
but the corner carried no regression, so a future refactor that collapsed
the two branches would not fail loudly.

This file adds ONE parametric test that pins ALL FOUR combinations against
the public ``ToolRouter.search_codebase`` boundary — both body content and
``meta.suggestion`` precedence — so that ADR 0007's "every rule is
independently flaggable" invariant is pinned at the user-visible surface,
not only at the renderer unit.

Shape (per SPEC.md §"Further Notes" clarification: the overview pointer
renders when ITS OWN rule, ``search_zero_hit``, is enabled, independently
of the kind-aware retry):

| gen | api | overview pointer in body? | retry line in body? | meta.suggestion         |
|-----|-----|---------------------------|---------------------|-------------------------|
| T   | T   | yes                       | yes                 | kind-aware retry string |
| T   | F   | yes                       | no                  | SEARCH_ZERO_HIT text    |
| F   | T   | no                        | yes                 | kind-aware retry string |
| F   | F   | no                        | no                  | None (absent)           |

No new product code. The test invokes the shipped empty-search router from
``test_error_empty_contract.py`` so wiring stays identical to the other
zero-hit tests; `(False, True)` is the only new boundary assertion, and
the three already-pinned combinations are re-asserted here so a single
reviewer reading this file sees the full independence contract at one
glance.
"""

from __future__ import annotations

import asyncio

import pytest

from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
)
from pydocs_mcp.application.suggestions import SEARCH_ZERO_HIT_SUGGESTION
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.models import ChunkList, ModuleMemberList, SearchResponse
from pydocs_mcp.retrieval.config import SuggestionsConfig

from ._router_fakes import (
    FakeOverview,
    FakeSymbolSource,
    make_envelope,
    make_services,
)


_WITNESS_QUERY = "nested serialization"


class _EmptyDocs:
    async def search(self, query):
        return SearchResponse(result=ChunkList(items=()), query=query, duration_ms=0.0)

    async def ranked(self, query):
        return ChunkList(items=())


class _EmptyApi:
    async def search(self, query):
        return SearchResponse(result=ModuleMemberList(items=()), query=query, duration_ms=0.0)

    async def ranked(self, query):
        return ModuleMemberList(items=())


def _router(gen: bool, api: bool) -> ToolRouter:
    base = make_services()[0]
    services = (
        ProjectServices(
            project=base.project,
            docs=_EmptyDocs(),
            api=_EmptyApi(),
            lookup=base.lookup,
            symbol_source=FakeSymbolSource(),
            overview=FakeOverview(),
            decisions=base.decisions,
        ),
    )
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
        suggestions=SuggestionsConfig(
            search_zero_hit=gen,
            api_zero_hit_retry_any=api,
        ),
    )


@pytest.mark.parametrize(
    ("gen", "api", "expect_pointer", "expect_retry", "expect_meta_kind"),
    [
        # (search_zero_hit=True, api_zero_hit_retry_any=True): both lines render,
        # kind-aware retry wins the single-string meta field (precedence).
        (True, True, True, True, "retry"),
        # (True, False): overview pointer only — the pre-change generic path.
        (True, False, True, False, "generic"),
        # (False, True): the asymmetric case — overview pointer suppressed by
        # its own flag, kind-aware retry still fires independently. Previously
        # unpinned at the ToolRouter boundary; this row is the new regression.
        (False, True, False, True, "retry"),
        # (False, False): ADR 0007 "every flag off ⇒ meta is always null".
        (False, False, False, False, "absent"),
    ],
)
def test_api_zero_hit_flag_independence_covers_four_combinations(
    gen: bool,
    api: bool,
    expect_pointer: bool,
    expect_retry: bool,
    expect_meta_kind: str,
) -> None:
    """Body and ``meta.suggestion`` both reflect the two flags independently."""
    response = asyncio.run(
        _router(gen, api).search_codebase(SearchInput(query=_WITNESS_QUERY, kind="api"))
    )

    assert "No symbols found." in response.text
    assert len(response.items) == 0

    pointer_present = "→ get_overview()" in response.text
    retry_lines = [line for line in response.text.splitlines() if line.startswith("[suggestion:")]
    assert pointer_present is expect_pointer
    assert (len(retry_lines) == 1) is expect_retry

    if expect_retry:
        retry_line = retry_lines[0]
        assert 'kind="any"' in retry_line
        assert f'query="{_WITNESS_QUERY}"' in retry_line

    # meta.suggestion precedence contract.
    if expect_meta_kind == "retry":
        suggestion = response.meta["suggestion"]
        assert suggestion.startswith("[suggestion:")
        assert 'kind="any"' in suggestion
        assert f'query="{_WITNESS_QUERY}"' in suggestion
        # The kind-aware rule beats the generic string when both fire.
        assert suggestion != SEARCH_ZERO_HIT_SUGGESTION
    elif expect_meta_kind == "generic":
        assert response.meta["suggestion"] == SEARCH_ZERO_HIT_SUGGESTION
    else:  # absent
        # ADR 0007: every flag off ⇒ meta carries no suggestion key (not even null).
        assert "suggestion" not in response.meta
