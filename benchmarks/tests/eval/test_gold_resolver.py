"""Pin the unified ground-truth resolution layer (spec §5, locked decision).

Hermetic by design: NO ``pydocs_mcp`` import (it isn't installed in the
benchmarks venv). The eager ``PydocsFuzzyGoldResolver`` takes an INJECTED
``uow_factory``, so a fake async context-manager + canned ``chunks.list``
exercises the store-scan path without a real SQLite. The
``normalize_package_name`` import inside ``resolve`` is the only
``pydocs_mcp`` touch and is patched out via a stub module so this test
collects and runs with pydocs_mcp absent.

Identity scheme under test (``_item_key``): ``chunk:{id}`` when the
store/retrieved item carries a chunk id, else ``rank:{rank}`` — namespaced
so an int chunk-id can never collide with an int rank.
"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest
from benchmarks.eval.datasets.base_dataset import EvalTask, GoldAnswer
from benchmarks.eval.gold_resolver import (
    _DEFAULT_FUZZ_THRESHOLD,
    GoldResolver,
    LazyFuzzyGoldResolver,
    PydocsFuzzyGoldResolver,
    _item_key,
)
from benchmarks.eval.systems.base_system import RetrievedItem

# ── fakes (no pydocs_mcp) ──────────────────────────────────────────────────


@dataclass
class _FakeStoreChunk:
    """Stand-in for pydocs's store ``Chunk`` — only ``.id`` + ``.text`` are
    read by the resolver."""

    id: int | None
    text: str


class _FakeChunks:
    """Fake ``uow.chunks`` exposing the async ``list`` the resolver calls."""

    def __init__(self, chunks: list[_FakeStoreChunk]) -> None:
        self._chunks = chunks
        self.calls: list[object] = []

    async def list(self, filter=None, limit=None):  # noqa: A002 -- mirrors repo API
        self.calls.append(filter)
        return list(self._chunks)


class _FakeUow:
    """Async context-manager fake mirroring ``SqliteUnitOfWork``'s shape
    (exposes ``.chunks``). Records enter/exit so a test can assert the
    early-return path never opened it."""

    def __init__(self, chunks_obj: _FakeChunks) -> None:
        self.chunks = chunks_obj
        self.entered = False

    async def __aenter__(self) -> "_FakeUow":
        self.entered = True
        return self

    async def __aexit__(self, *exc) -> None:
        return None


def _fake_uow_factory(chunks: list[_FakeStoreChunk]):
    """Build a ``Callable[[], _FakeUow]`` + return the shared ``_FakeChunks``
    so the test can introspect call count after ``resolve``."""
    chunks_obj = _FakeChunks(chunks)
    uow = _FakeUow(chunks_obj)

    def factory() -> _FakeUow:
        return uow

    return factory, chunks_obj, uow


@pytest.fixture(autouse=True)
def _stub_normalize_package_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a fake ``pydocs_mcp.deps`` so the resolver's DEFERRED
    ``from pydocs_mcp.deps import normalize_package_name`` resolves without
    pydocs_mcp installed. Mirrors the real impl (``.lower().replace("-","_")``)."""
    pkg = types.ModuleType("pydocs_mcp")
    deps = types.ModuleType("pydocs_mcp.deps")

    def normalize_package_name(raw: str) -> str:
        return raw.lower().replace("-", "_")

    deps.normalize_package_name = normalize_package_name  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pydocs_mcp", pkg)
    monkeypatch.setitem(sys.modules, "pydocs_mcp.deps", deps)


def _task(doc_contents: tuple[str, ...], *, library: str = "pandas") -> EvalTask:
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(extra={"doc_contents": doc_contents}),
        corpus_source=lambda: Path("."),
        metadata={"library": library},
    )


# ── _item_key ──────────────────────────────────────────────────────────────


def test_item_key_uses_chunk_id_when_present() -> None:
    item = RetrievedItem(rank=3, text="x", source_path="p", chunk_id=42)
    assert _item_key(item) == "chunk:42"


def test_item_key_falls_back_to_rank_when_chunk_id_none() -> None:
    item = RetrievedItem(rank=7, text="x", source_path="p", chunk_id=None)
    assert _item_key(item) == "rank:7"


# ── PydocsFuzzyGoldResolver (eager) ─────────────────────────────────────────


def test_resolver_is_runtime_checkable_protocol() -> None:
    # WHY: the runner gates on ``isinstance(system, HasGoldResolver)`` and the
    # resolvers must satisfy the GoldResolver Protocol structurally.
    assert isinstance(LazyFuzzyGoldResolver(_DEFAULT_FUZZ_THRESHOLD), GoldResolver)
    factory, _chunks, _uow = _fake_uow_factory([])
    assert isinstance(PydocsFuzzyGoldResolver(factory), GoldResolver)


async def test_eager_empty_doc_contents_returns_frozenset_without_db() -> None:
    # WHY: pydocs is HasGoldResolver even for RepoQA (no doc_contents). The
    # early return MUST be zero-cost — the fake UoW is never entered and
    # ``chunks.list`` is never called.
    factory, chunks_obj, uow = _fake_uow_factory(
        [_FakeStoreChunk(id=1, text="anything")]
    )
    resolver = PydocsFuzzyGoldResolver(factory)
    task = _task(doc_contents=())

    result = await resolver.resolve(task, ())

    assert result == frozenset()
    assert chunks_obj.calls == []  # list() never called
    assert uow.entered is False  # UoW never opened


async def test_eager_includes_above_threshold_excludes_below() -> None:
    gold = "the quick brown fox jumps over the lazy dog"
    chunks = [
        # exact substring -> partial_ratio == 100 -> included
        _FakeStoreChunk(id=10, text="prefix " + gold + " suffix"),
        # unrelated -> well below 85 -> excluded
        _FakeStoreChunk(id=11, text="completely different unrelated content zzz"),
    ]
    factory, chunks_obj, _uow = _fake_uow_factory(chunks)
    resolver = PydocsFuzzyGoldResolver(factory, _DEFAULT_FUZZ_THRESHOLD)

    result = await resolver.resolve(_task((gold,)), ())

    assert result == frozenset({"chunk:10"})


async def test_eager_excludes_chunk_with_none_id() -> None:
    # WHY: composite/budgeted chunks carry id=None and cannot be id-matched
    # to the store — the eager resolver must skip them even on a content hit.
    gold = "alpha beta gamma delta epsilon"
    chunks = [
        _FakeStoreChunk(id=None, text=gold),  # perfect match but id=None
        _FakeStoreChunk(id=20, text=gold),  # perfect match, real id
    ]
    factory, _chunks, _uow = _fake_uow_factory(chunks)
    resolver = PydocsFuzzyGoldResolver(factory, _DEFAULT_FUZZ_THRESHOLD)

    result = await resolver.resolve(_task((gold,)), ())

    assert result == frozenset({"chunk:20"})


async def test_eager_normalizes_package_name_in_filter() -> None:
    # WHY (coherence): pydocs stores package names lower+underscored
    # (scikit-learn -> scikit_learn). The resolver MUST normalize before
    # filtering or it reads zero rows. We assert the filter passed to
    # ``chunks.list`` carries the normalized package.
    gold = "fit predict transform estimator pipeline"
    chunks = [_FakeStoreChunk(id=30, text=gold)]
    factory, chunks_obj, _uow = _fake_uow_factory(chunks)
    resolver = PydocsFuzzyGoldResolver(factory, _DEFAULT_FUZZ_THRESHOLD)
    task = _task((gold,), library="scikit-learn")

    result = await resolver.resolve(task, ())

    assert result == frozenset({"chunk:30"})
    assert chunks_obj.calls == [{"package": "scikit_learn"}]


async def test_eager_filter_is_none_when_no_library() -> None:
    gold = "some matching documentation body text here"
    chunks = [_FakeStoreChunk(id=40, text=gold)]
    factory, chunks_obj, _uow = _fake_uow_factory(chunks)
    resolver = PydocsFuzzyGoldResolver(factory, _DEFAULT_FUZZ_THRESHOLD)
    task = _task((gold,), library="")  # no library -> filter None

    result = await resolver.resolve(task, ())

    assert result == frozenset({"chunk:40"})
    assert chunks_obj.calls == [None]


# ── LazyFuzzyGoldResolver ───────────────────────────────────────────────────


async def test_lazy_empty_doc_contents_returns_frozenset() -> None:
    resolver = LazyFuzzyGoldResolver(_DEFAULT_FUZZ_THRESHOLD)
    retrieved = (RetrievedItem(rank=1, text="anything", source_path="p"),)
    assert await resolver.resolve(_task(()), retrieved) == frozenset()


async def test_lazy_matches_retrieved_items_by_content() -> None:
    gold = "construct a DataFrame and merge on a shared key column"
    retrieved = (
        RetrievedItem(rank=1, text="noise about something else entirely qqq", source_path="p"),
        RetrievedItem(rank=2, text="intro " + gold + " trailer", source_path="p", chunk_id=99),
        RetrievedItem(rank=3, text="another irrelevant blob of words yyy", source_path="p"),
    )
    resolver = LazyFuzzyGoldResolver(_DEFAULT_FUZZ_THRESHOLD)

    result = await resolver.resolve(_task((gold,)), retrieved)

    # rank-2 carries chunk_id=99 -> keyed chunk:99
    assert result == frozenset({"chunk:99"})


async def test_lazy_uses_rank_key_when_chunk_id_absent() -> None:
    gold = "matplotlib figure axes subplot tight layout savefig"
    retrieved = (
        RetrievedItem(rank=1, text="prefix " + gold, source_path="p"),  # chunk_id None
    )
    resolver = LazyFuzzyGoldResolver(_DEFAULT_FUZZ_THRESHOLD)

    result = await resolver.resolve(_task((gold,)), retrieved)

    assert result == frozenset({"rank:1"})


# ── threshold boundary ──────────────────────────────────────────────────────


def _ratio(a: str, b: str) -> float:
    from rapidfuzz import fuzz

    return fuzz.partial_ratio(a, b)


async def test_threshold_boundary_at_and_below() -> None:
    # WHY: pin the >= comparison at the exact threshold. We pick a gold/text
    # pair whose partial_ratio straddles 85 and assert the boundary behavior.
    # Construct strings empirically so the test is self-validating.
    gold = "abcdefghijklmnopqrst"  # 20 chars
    # 17/20 chars as a clean substring -> partial_ratio 100 (>= 85 -> hit)
    at_or_above_text = "zz abcdefghijklmnopq zz"
    # a sparse scramble that scores below 85
    below_text = "a x b x c x d x e x f x"

    above_ratio = _ratio(gold, at_or_above_text)
    below_ratio = _ratio(gold, below_text)
    # Guard the fixture: these must straddle the threshold or the test is moot.
    assert above_ratio >= _DEFAULT_FUZZ_THRESHOLD
    assert below_ratio < _DEFAULT_FUZZ_THRESHOLD

    factory, _chunks, _uow = _fake_uow_factory(
        [
            _FakeStoreChunk(id=1, text=at_or_above_text),
            _FakeStoreChunk(id=2, text=below_text),
        ]
    )
    resolver = PydocsFuzzyGoldResolver(factory, _DEFAULT_FUZZ_THRESHOLD)
    result = await resolver.resolve(_task((gold,)), ())
    assert result == frozenset({"chunk:1"})


async def test_threshold_custom_value_respected() -> None:
    # A resolver with an impossibly-high threshold matches nothing.
    gold = "exact full text match candidate body"
    chunks = [_FakeStoreChunk(id=1, text=gold)]
    factory, _chunks, _uow = _fake_uow_factory(chunks)
    strict = PydocsFuzzyGoldResolver(factory, 101)  # nothing can reach 101
    assert await strict.resolve(_task((gold,)), ()) == frozenset()


def test_default_threshold_is_85() -> None:
    # WHY: single source of truth — pin the shipped default.
    assert _DEFAULT_FUZZ_THRESHOLD == 85
