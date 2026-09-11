"""LookupService fallback hooks (spec 2026-09-10 §2.5) — isolated from the rules.

A ``FakeTargetResolver`` supplies canned rewrites, so these tests pin only the
hook seams: exact path first, one pinned ``__project__`` retry, canonical
display target for get_context, retry-miss candidates, and pass-through of
``ServiceUnavailableError``.
"""

from __future__ import annotations

import asyncio

import pytest

from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_errors import NotFoundError, ServiceUnavailableError
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.application.null_services import NullReferenceService
from pydocs_mcp.application.package_lookup import PackageLookup
from pydocs_mcp.application.target_resolution import (
    NullTargetResolver,
    TargetResolution,
    TargetRewrite,
)
from pydocs_mcp.application.tree_service import TreeService
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk
from tests._fakes import (
    FakeTargetResolver,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)

_MODULE = "needle.scoring.strategies"
_CANONICAL = f"{_MODULE}.MaxSimScorer"
_SRC_TARGET = f"src.{_CANONICAL}"
_STRIP = TargetRewrite("source_root_strip", _CANONICAL, _MODULE, ("MaxSimScorer",))


def _module_tree(package_marker: str) -> DocumentNode:
    """A module tree whose class summary names the package it was stored under
    (``summary`` is what the rendered page-index JSON carries; ``text`` is not)."""
    cls = DocumentNode(
        node_id=_CANONICAL,
        qualified_name=_CANONICAL,
        title="MaxSimScorer",
        kind=NodeKind.CLASS,
        source_path="src/needle/scoring/strategies.py",
        start_line=3,
        end_line=9,
        text="class MaxSimScorer: ...",
        content_hash="h-cls",
        summary=f"stored under {package_marker}",
    )
    return DocumentNode(
        node_id=_MODULE,
        qualified_name=_MODULE,
        title="strategies",
        kind=NodeKind.MODULE,
        source_path="src/needle/scoring/strategies.py",
        start_line=1,
        end_line=9,
        text="module body",
        content_hash="h-mod",
        children=(cls,),
    )


def _module_chunk(package: str) -> Chunk:
    # find_module probes chunks (the fake tree store's exists() is always False).
    return Chunk(text="x", metadata={"package": package, "module": _MODULE})


class _RecordingContextRefs:
    """ReferenceNavigator slice for ``context``: records the seed, empty closure."""

    def __init__(self) -> None:
        self.seeds: list[tuple[str, str]] = []

    async def context(self, package: str, qname: str, *, max_depth: int, limit: int) -> tuple:
        self.seeds.append((package, qname))
        return ()


def _service(
    resolver: object | None = None,
    *,
    with_dependency: bool = False,
    ref_svc: object | None = None,
) -> LookupService:
    """Project tree for ``needle.scoring.strategies``; optionally a same-named
    indexed dependency ``needle`` whose tree would win a re-parse (P2)."""
    packages = [PROJECT_PACKAGE_NAME] + (["needle"] if with_dependency else [])
    trees = InMemoryDocumentTreeStore(by_package={p: [_module_tree(p)] for p in packages})
    chunks = InMemoryChunkStore()
    asyncio.run(chunks.upsert(_module_chunk(p) for p in packages))
    uow_factory = make_fake_uow_factory(trees=trees, chunks=chunks)
    extra = {} if resolver is None else {"target_resolver": resolver}
    return LookupService(
        package_lookup=PackageLookup(uow_factory=uow_factory),
        tree_svc=TreeService(uow_factory=uow_factory),
        ref_svc=ref_svc if ref_svc is not None else _RecordingContextRefs(),
        **extra,
    )


def _strip_resolver(**more: TargetResolution) -> FakeTargetResolver:
    return FakeTargetResolver(
        resolution_by_target={_SRC_TARGET: TargetResolution(rewrite=_STRIP), **more}
    )


# ── lookup_with_items ─────────────────────────────────────────────────────


def test_lookup_retry_is_pinned_to_project_over_same_named_dependency() -> None:
    svc = _service(_strip_resolver(), with_dependency=True)
    body, _items, _extras = asyncio.run(svc.lookup_with_items(LookupInput(target=_SRC_TARGET)))
    assert f"stored under {PROJECT_PACKAGE_NAME}" in body
    assert "stored under needle" not in body


def test_lookup_resolved_body_is_byte_identical_to_the_canonical_target() -> None:
    svc = _service(_strip_resolver())
    resolved = asyncio.run(svc.lookup_with_items(LookupInput(target=_SRC_TARGET)))
    direct = asyncio.run(svc.lookup_with_items(LookupInput(target=_CANONICAL)))
    assert resolved == direct


def test_lookup_exact_never_consults_the_resolver() -> None:
    resolver = _strip_resolver()
    svc = _service(resolver)
    with pytest.raises(NotFoundError, match="no module matching"):
        asyncio.run(svc.lookup_exact(LookupInput(target=_SRC_TARGET)))
    assert resolver.calls == []


def test_lookup_exact_hit_makes_no_resolver_call() -> None:
    resolver = _strip_resolver()
    asyncio.run(_service(resolver).lookup_with_items(LookupInput(target=_CANONICAL)))
    assert resolver.calls == []


def test_lookup_retry_miss_carries_canonical_minus_self_candidates() -> None:
    ghost = f"{_MODULE}.Ghost"
    rewrite = TargetRewrite("source_root_strip", ghost, _MODULE, ("Ghost",))
    resolver = FakeTargetResolver(
        resolution_by_target={
            f"src.{ghost}": TargetResolution(rewrite=rewrite),
            ghost: TargetResolution(candidates=(ghost, _CANONICAL), candidate_total=2),
        }
    )
    with pytest.raises(NotFoundError) as info:
        asyncio.run(_service(resolver).lookup_with_items(LookupInput(target=f"src.{ghost}")))
    assert str(info.value) == (
        f"no module matching 'src.{ghost}' found under 'src'. Closest indexed names: {_CANONICAL}."
    )


def test_lookup_default_resolver_is_null_and_miss_is_unchanged() -> None:
    svc = _service()
    assert isinstance(svc.target_resolver, NullTargetResolver)
    with pytest.raises(NotFoundError) as info:
        asyncio.run(svc.lookup_with_items(LookupInput(target=_SRC_TARGET)))
    assert str(info.value) == f"no module matching '{_SRC_TARGET}' found under 'src'"


def test_service_unavailable_is_never_swallowed_by_the_fallback() -> None:
    resolver = _strip_resolver()
    svc = _service(resolver, ref_svc=NullReferenceService())
    with pytest.raises(ServiceUnavailableError):
        asyncio.run(svc.lookup_with_items(LookupInput(target=_CANONICAL, show="callers")))
    assert resolver.calls == []
    with pytest.raises(ServiceUnavailableError):
        asyncio.run(svc.lookup_with_items(LookupInput(target=_SRC_TARGET, show="callers")))


# ── context_nodes ─────────────────────────────────────────────────────────


def test_context_nodes_rewrite_displays_canonical_and_pins_project() -> None:
    refs = _RecordingContextRefs()
    svc = _service(_strip_resolver(), with_dependency=True, ref_svc=refs)
    display, nodes, focus = asyncio.run(svc.context_nodes(_SRC_TARGET))
    assert display == _CANONICAL
    assert focus["qualified_name"] == _CANONICAL
    assert nodes == ()
    assert refs.seeds == [(PROJECT_PACKAGE_NAME, _CANONICAL)]


def test_context_nodes_exact_never_consults_the_resolver() -> None:
    resolver = _strip_resolver()
    with pytest.raises(NotFoundError, match="for context closure"):
        asyncio.run(_service(resolver).context_nodes_exact(_SRC_TARGET))
    assert resolver.calls == []


def test_context_nodes_exact_target_is_echoed_verbatim() -> None:
    display, _nodes, _focus = asyncio.run(_service(_strip_resolver()).context_nodes(_CANONICAL))
    assert display == _CANONICAL


def test_context_nodes_service_unavailable_passes_through() -> None:
    svc = _service(_strip_resolver(), ref_svc=NullReferenceService())
    with pytest.raises(ServiceUnavailableError):
        asyncio.run(svc.context_nodes(_SRC_TARGET))
