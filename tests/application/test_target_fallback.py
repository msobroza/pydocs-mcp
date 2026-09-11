"""with_target_fallback + the multi-project decider/renderer (spec §2.2, §2.5, §3, §4).

Driven through the named ``FakeTargetResolver``; no hook is wired yet.
"""

from __future__ import annotations

import json
import logging

import pytest

from pydocs_mcp.application.mcp_errors import NotFoundError, ServiceUnavailableError
from pydocs_mcp.application.target_resolution import (
    TargetResolution,
    TargetRewrite,
    decide_workspace_rewrite,
    render_workspace_miss_message,
    with_target_fallback,
)
from tests._fakes import FakeTargetResolver

_LOGGER = "pydocs_mcp.application.target_resolution"


# ── with_target_fallback [AC11, AC14, P1] ─────────────────────────────────

_REWRITE = TargetRewrite("unique_bare_name", "pkg.mod.Cls", "pkg.mod", ("Cls",))


async def _raise(exc: Exception) -> str:
    raise exc


async def test_exact_hit_never_calls_the_resolver() -> None:
    fake = FakeTargetResolver()

    async def exact() -> str:
        return "body"

    result = await with_target_fallback(
        "Cls", entry="lookup", resolver=fake, run_exact=exact, run_rewrite=_unexpected_rewrite
    )
    assert result == "body"
    assert fake.calls == []


async def _unexpected_rewrite(rw: TargetRewrite) -> str:
    raise AssertionError(f"rewrite must not run: {rw!r}")


async def test_resolved_fallback_returns_the_rewrite_value_and_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeTargetResolver({"Cls": TargetResolution(rewrite=_REWRITE, exact_leaf_count=1)})

    async def rewrite(rw: TargetRewrite) -> str:
        return f"body of {rw.canonical}"

    caplog.set_level(logging.INFO)
    result = await with_target_fallback(
        "Cls",
        entry="lookup",
        resolver=fake,
        run_exact=lambda: _raise(NotFoundError("package 'Cls' not indexed")),
        run_rewrite=rewrite,
    )
    assert result == "body of pkg.mod.Cls"
    records = [r for r in caplog.records if r.name == _LOGGER]
    assert len(records) == 1
    payload = json.loads(records[0].getMessage())
    assert list(payload) == ["event", "entry", "rule", "target", "resolved"]
    assert payload == {
        "event": "target_fallback_resolved",
        "entry": "lookup",
        "rule": "unique_bare_name",
        "target": "Cls",
        "resolved": "pkg.mod.Cls",
    }
    assert not [r for r in caplog.records if r.name == "pydocs_mcp.application.suggestions"]


async def test_project_is_appended_to_the_log_payload(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeTargetResolver({"Cls": TargetResolution(rewrite=_REWRITE)})

    async def rewrite(rw: TargetRewrite) -> str:
        return "ok"

    caplog.set_level(logging.INFO)
    await with_target_fallback(
        "Cls",
        entry="source",
        resolver=fake,
        run_exact=lambda: _raise(NotFoundError("miss")),
        run_rewrite=rewrite,
        project="alpha",
    )
    payload = json.loads(next(r for r in caplog.records if r.name == _LOGGER).getMessage())
    assert list(payload)[-1] == "project"
    assert payload["project"] == "alpha"


async def test_retry_miss_raises_original_plus_candidates_minus_canonical(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeTargetResolver(
        {
            "Cls": TargetResolution(rewrite=_REWRITE),
            "pkg.mod.Cls": TargetResolution(
                candidates=("pkg.mod.Cls", "other.Cls"), candidate_total=2
            ),
        }
    )
    original = NotFoundError("package 'Cls' not indexed")
    caplog.set_level(logging.INFO)
    with pytest.raises(NotFoundError) as info:
        await with_target_fallback(
            "Cls",
            entry="lookup",
            resolver=fake,
            run_exact=lambda: _raise(original),
            run_rewrite=lambda rw: _raise(NotFoundError("'Cls' not found in pkg.mod")),
        )
    assert str(info.value) == "package 'Cls' not indexed. Closest indexed names: other.Cls."
    assert info.value.__cause__ is original
    assert fake.calls == [("Cls", "lookup"), ("pkg.mod.Cls", "lookup")]
    assert not [r for r in caplog.records if r.name == _LOGGER]


async def test_retry_miss_without_other_candidates_reraises_the_original() -> None:
    fake = FakeTargetResolver(
        {
            "Cls": TargetResolution(rewrite=_REWRITE),
            "pkg.mod.Cls": TargetResolution(candidates=("pkg.mod.Cls",), candidate_total=1),
        }
    )
    original = NotFoundError("package 'Cls' not indexed")
    with pytest.raises(NotFoundError) as info:
        await with_target_fallback(
            "Cls",
            entry="lookup",
            resolver=fake,
            run_exact=lambda: _raise(original),
            run_rewrite=lambda rw: _raise(NotFoundError("retry miss")),
        )
    assert info.value is original


async def test_miss_with_candidates_appends_them(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeTargetResolver({"Cls": TargetResolution(candidates=("a.Cls",), candidate_total=1)})
    original = NotFoundError("package 'Cls' not indexed")
    caplog.set_level(logging.INFO)
    with pytest.raises(NotFoundError) as info:
        await with_target_fallback(
            "Cls",
            entry="lookup",
            resolver=fake,
            run_exact=lambda: _raise(original),
            run_rewrite=_unexpected_rewrite,
        )
    assert str(info.value) == "package 'Cls' not indexed. Closest indexed names: a.Cls."
    assert info.value.__cause__ is original
    assert not [r for r in caplog.records if r.name == _LOGGER]


async def test_miss_without_candidates_reraises_the_identical_exception() -> None:
    original = NotFoundError("package 'md' not indexed")
    with pytest.raises(NotFoundError) as info:
        await with_target_fallback(
            "md",
            entry="lookup",
            resolver=FakeTargetResolver(),
            run_exact=lambda: _raise(original),
            run_rewrite=_unexpected_rewrite,
        )
    assert info.value is original


async def test_service_unavailable_propagates_without_a_resolver_call() -> None:
    fake = FakeTargetResolver()
    with pytest.raises(ServiceUnavailableError):
        await with_target_fallback(
            "Cls",
            entry="lookup",
            resolver=fake,
            run_exact=lambda: _raise(ServiceUnavailableError("no trees")),
            run_rewrite=_unexpected_rewrite,
        )
    assert fake.calls == []


# ── decide_workspace_rewrite + workspace renderer [AC13] ──────────────────

_RULE1 = TargetRewrite("source_root_strip", "pkg.mod.Cls", "pkg.mod", ("Cls",))


def test_workspace_single_rewrite_resolves() -> None:
    chosen = decide_workspace_rewrite(
        [("a", TargetResolution()), ("b", TargetResolution(rewrite=_RULE1))]
    )
    assert chosen == ("b", _RULE1)


def test_workspace_two_rewrites_are_ambiguous() -> None:
    both = [("a", TargetResolution(rewrite=_RULE1)), ("b", TargetResolution(rewrite=_RULE1))]
    assert decide_workspace_rewrite(both) is None
    rendered = render_workspace_miss_message("'Cls' not found in any loaded project.", both, 5)
    assert rendered == (
        "'Cls' not found in any loaded project. Ambiguous name 'Cls' matches 2 indexed "
        "symbols across projects: pkg.mod.Cls (project a), pkg.mod.Cls (project b)."
    )


def test_workspace_unique_in_a_plus_ambiguous_in_b_does_not_resolve() -> None:
    unique = TargetResolution(rewrite=_REWRITE, exact_leaf_count=1)
    ambiguous = TargetResolution(
        candidates=("x.Cls", "y.Cls"), candidate_total=2, exact_leaf_count=2, ambiguous=True
    )
    pairs = [("a", unique), ("b", ambiguous)]
    assert decide_workspace_rewrite(pairs) is None
    rendered = render_workspace_miss_message("miss.", pairs, 5)
    assert rendered == (
        "miss. Ambiguous name 'Cls' matches 3 indexed symbols across projects: "
        "pkg.mod.Cls (project a), x.Cls (project b), y.Cls (project b)."
    )


def test_workspace_truncated_scan_never_resolves() -> None:
    pairs = [
        ("a", TargetResolution(rewrite=_REWRITE, exact_leaf_count=1)),
        ("b", TargetResolution(scan_truncated=True)),
    ]
    assert decide_workspace_rewrite(pairs) is None


def test_workspace_all_miss_merges_tagged_candidates_capped_and_deduplicated() -> None:
    pairs = [
        ("new", TargetResolution(candidates=("a.Cls", "b.Cls"), candidate_total=4)),
        ("old", TargetResolution(candidates=("a.Cls",), candidate_total=1)),
    ]
    message = "'Clz' not found in any loaded project. [[next:search:Clz]]"
    rendered = render_workspace_miss_message(message, pairs, 2)
    assert rendered == (
        f"{message} Closest indexed names: a.Cls (project new), b.Cls (project new) (+3 more)."
    )


def test_workspace_without_candidates_returns_the_same_message() -> None:
    message = "'x' not found in any loaded project. [[next:search:x]]"
    rendered = render_workspace_miss_message(message, [("a", TargetResolution())], 5)
    assert rendered is message
