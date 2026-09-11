"""get_symbol target resolution, end to end on a real src-layout index (spec
2026-09-10 §8 integration list; AC1, AC2, AC4-AC10, AC12, AC14).

The index is built by the REAL ingestion pipeline + ``IndexingService`` (the
``probe_db`` recipe), and every call goes through the production
``build_sqlite_lookup_service`` / ``build_sqlite_symbol_source_service``
factories behind a ``ToolRouter``. The origin/main baseline is the same
composition with ``NullTargetResolver`` swapped in ("Null-wired"), so every
"unchanged" assertion is a byte-for-byte comparison against it rather than a
hand-copied string.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    LookupInput,
    ReferencesInput,
    SymbolInput,
)
from pydocs_mcp.application.target_resolution import NullTargetResolver
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.retrieval.config import AppConfig, TargetResolutionConfig
from tests._src_layout_fixture import (
    DEPENDENCY_SCORER_DOC,
    PROJECT_SCORER_DOC,
    build_router,
    build_src_layout_db,
    run_tool,
)

_RESOLUTION_LOGGER = "pydocs_mcp.application.target_resolution"
_SUGGESTIONS_LOGGER = "pydocs_mcp.application.suggestions"
_LOG_KEYS = ["event", "entry", "rule", "target", "resolved"]

_CANONICAL = "srcpkg.scoring.MaxSimScorer"
_STRIPPED = "src.srcpkg.scoring.MaxSimScorer"
_BARE = "MaxSimScorer"
_DEPTHS = ("summary", "tree", "source")
_DIRECTIONS = ("callers", "callees", "inherits", "impact", "governed_by")
_CLOSEST_SCORER = "Closest indexed names: srcpkg.scoring.MaxSimScorer."
_AMBIGUOUS_MAIN = (
    "Ambiguous name 'main' matches 2 indexed symbols: scripts.run.main, srcpkg.cli.main."
)
_AMBIGUOUS_SCORE = (
    "Ambiguous name 'score' matches 2 indexed symbols: "
    "srcpkg.scoring.CosineScorer.score, srcpkg.scoring.MaxSimScorer.score."
)


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def src_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_src_layout_db(tmp_path_factory.mktemp("src_layout"))


@pytest.fixture(scope="module")
def shadow_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same project plus an indexed dependency also named ``srcpkg``."""
    return build_src_layout_db(tmp_path_factory.mktemp("shadow"), with_shadow_dependency=True)


@pytest.fixture(scope="module")
def wired(src_db: Path) -> ToolRouter:
    return build_router(src_db, AppConfig())


@pytest.fixture(scope="module")
def null(src_db: Path) -> ToolRouter:
    return build_router(src_db, AppConfig(), null_resolver=True)


# ── call helpers ─────────────────────────────────────────────────────────


def _symbol(router: ToolRouter, target: str, depth: str = "summary") -> tuple[object, ...]:
    return run_tool(router.get_symbol(SymbolInput(target=target, depth=depth)))


def _refs(router: ToolRouter, target: str, direction: str = "callers") -> tuple[object, ...]:
    return run_tool(router.get_references(ReferencesInput(target=target, direction=direction)))


def _context(router: ToolRouter, *targets: str) -> tuple[object, ...]:
    return run_tool(router.get_context(ContextInput(targets=list(targets))))


def _resolved(outcome: tuple[object, ...]) -> tuple[object, ...]:
    """Assert ``outcome`` is a rendered response (not a raise) and return it."""
    assert outcome[0] != "raised", f"expected a resolved response, got {outcome!r}"
    return outcome


def _miss(outcome: tuple[object, ...]) -> str:
    """Assert ``outcome`` is a ``NotFoundError`` raise and return its message."""
    assert outcome[:2] == ("raised", "NotFoundError"), f"expected NotFoundError, got {outcome!r}"
    return str(outcome[2])


def _config(**flags: bool) -> AppConfig:
    return AppConfig().model_copy(update={"target_resolution": TargetResolutionConfig(**flags)})


def _fallback_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, str]]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == _RESOLUTION_LOGGER]


# ── AC1 / AC2 / AC4: rewrites on every surface ───────────────────────────


@pytest.mark.parametrize("depth", _DEPTHS)
def test_stripped_source_root_equals_canonical_at_every_depth(wired, depth):
    """AC1: text, items and meta identical to the canonical call."""
    canonical = _resolved(_symbol(wired, _CANONICAL, depth))
    assert _symbol(wired, _STRIPPED, depth) == canonical


def test_stripped_source_root_equals_canonical_in_references_and_context(wired):
    """AC1: get_references(callers) and get_context, heading on the canonical name."""
    assert _refs(wired, _STRIPPED) == _resolved(_refs(wired, _CANONICAL))
    context = _context(wired, _STRIPPED)
    assert context == _resolved(_context(wired, _CANONICAL))
    assert f"# Context for `{_CANONICAL}`" in str(context[0])


@pytest.mark.parametrize("depth", ["summary", "tree"])
def test_stripped_module_resolves_to_module_outline(wired, depth):
    """AC2: a module-only strip renders the module outline in get_symbol."""
    outline = _resolved(_symbol(wired, "srcpkg.scoring", depth))
    assert _symbol(wired, "src.srcpkg.scoring", depth) == outline


def test_stripped_module_context_keeps_todays_message(wired, null):
    """AC2: a module-only rewrite is dropped for get_context — byte for byte."""
    message = _miss(_context(wired, "src.srcpkg.scoring"))
    assert message == _miss(_context(null, "src.srcpkg.scoring"))


@pytest.mark.parametrize("depth", _DEPTHS)
def test_unique_bare_name_equals_canonical_at_every_depth(wired, depth):
    """AC4: bare ``MaxSimScorer`` is the only eligible exact leaf."""
    assert _symbol(wired, _BARE, depth) == _resolved(_symbol(wired, _CANONICAL, depth))


def test_unique_bare_name_equals_canonical_in_references_and_context(wired):
    """AC4: the other two surfaces."""
    assert _refs(wired, _BARE) == _resolved(_refs(wired, _CANONICAL))
    assert _context(wired, _BARE) == _resolved(_context(wired, _CANONICAL))


# ── AC5 / AC6 / AC7 / AC8: misses ────────────────────────────────────────


@pytest.mark.parametrize(
    ("target", "sentence"), [("main", _AMBIGUOUS_MAIN), ("score", _AMBIGUOUS_SCORE)]
)
def test_ambiguous_bare_name_appends_sentence_to_todays_message(wired, null, target, sentence):
    """AC5: today's message is the exact prefix; ``Score`` is excluded by case."""
    today = _miss(_symbol(null, target))
    assert _miss(_symbol(wired, target)) == f"{today}. {sentence}"
    today_source = _miss(_symbol(null, target, "source"))
    assert _miss(_symbol(wired, target, "source")) == f"{today_source} {sentence}"


@pytest.mark.parametrize("target", ["md", "toml", "__imports__"])
@pytest.mark.parametrize("depth", ["summary", "source"])
def test_non_code_leaves_keep_todays_message(wired, null, target, depth):
    """AC6: AGENTS.md / pyproject.toml / the __imports__ pseudo-node never match."""
    assert _miss(_symbol(wired, target, depth)) == _miss(_symbol(null, target, depth))


def test_typo_at_source_depth_keeps_pointer_then_candidates(wired, null):
    """AC7: today's message (with its raw search pointer) is the exact prefix."""
    today = _miss(_symbol(null, "MaxSimScorr", "source"))
    assert today == "'MaxSimScorr' has no indexed source. [[next:search:MaxSimScorr]]"
    assert _miss(_symbol(wired, "MaxSimScorr", "source")) == f"{today} {_CLOSEST_SCORER}"


def test_reexport_is_never_rewritten_and_only_gains_candidates(wired, null, caplog):
    """AC8: the ``srcpkg`` re-export of MaxSimScorer stays a miss."""
    caplog.set_level(logging.INFO, logger=_RESOLUTION_LOGGER)
    today = _miss(_symbol(null, "srcpkg.MaxSimScorer"))
    assert today == "'srcpkg.MaxSimScorer' not found in srcpkg"
    assert _miss(_symbol(wired, "srcpkg.MaxSimScorer")) == f"{today}. {_CLOSEST_SCORER}"
    assert _fallback_events(caplog) == []


# ── AC9: byte-identity of every working target, wired vs Null ────────────

_EXACT_SYMBOL_TARGETS = (
    "__project__",
    "srcpkg",
    "srcpkg.scoring",
    "srcpkg.cli",
    _CANONICAL,
    "srcpkg.scoring.MaxSimScorer.score",
    "srcpkg.scoring.CosineScorer",
    "srcpkg.scoring.Score",
    "srcpkg.cli.main",
    "scripts.run",
    "scripts.run.main",
    "AGENTS.md",
)
_EXACT_REF_TARGETS = (_CANONICAL, "srcpkg.scoring.MaxSimScorer.score", "srcpkg.cli.main")
_EXACT_CONTEXT_TARGETS = (
    (_CANONICAL,),
    ("srcpkg.cli.main",),
    ("srcpkg.cli.main", "srcpkg.scoring.MaxSimScorer.score"),
)


@pytest.mark.parametrize("depth", _DEPTHS)
@pytest.mark.parametrize("target", _EXACT_SYMBOL_TARGETS)
def test_exact_symbol_targets_are_byte_identical_to_null(wired, null, target, depth):
    assert _symbol(wired, target, depth) == _symbol(null, target, depth)


@pytest.mark.parametrize("direction", _DIRECTIONS)
@pytest.mark.parametrize("target", _EXACT_REF_TARGETS)
def test_exact_reference_targets_are_byte_identical_to_null(wired, null, target, direction):
    assert _refs(wired, target, direction) == _refs(null, target, direction)


@pytest.mark.parametrize("targets", _EXACT_CONTEXT_TARGETS)
def test_exact_context_targets_are_byte_identical_to_null(wired, null, targets):
    assert _context(wired, *targets) == _context(null, *targets)


def test_package_list_is_byte_identical_to_null(wired, null):
    """The empty-target package listing (the lookup service's first branch)."""

    def _listing(router: ToolRouter) -> object:
        return asyncio.run(router.services[0].lookup.lookup_with_items(LookupInput(target="")))

    assert _listing(wired) == _listing(null)


def test_exact_targets_resolve_before_the_resolver(wired):
    """AC9 sanity: the canonical target and its method resolve on the exact path."""
    _resolved(_symbol(wired, _CANONICAL))
    _resolved(_symbol(wired, "srcpkg.scoring.MaxSimScorer.score", "source"))


# ── AC9 / AC10: a dependency named like the project's top-level package ──


@pytest.fixture(scope="module")
def shadow_wired(shadow_db: Path) -> ToolRouter:
    return build_router(shadow_db, AppConfig())


@pytest.fixture(scope="module")
def shadow_null(shadow_db: Path) -> ToolRouter:
    return build_router(shadow_db, AppConfig(), null_resolver=True)


@pytest.mark.parametrize("target", [_STRIPPED, _BARE])
@pytest.mark.parametrize("depth", _DEPTHS)
def test_rewrite_renders_project_node_despite_same_named_dependency(shadow_wired, target, depth):
    """AC10: the retry is pinned to __project__, never re-parsed."""
    text = str(_resolved(_symbol(shadow_wired, target, depth))[0])
    assert PROJECT_SCORER_DOC in text
    assert DEPENDENCY_SCORER_DOC not in text


def test_rewrite_context_focus_row_is_the_project_node(shadow_wired):
    """AC10 on get_context: the focus row is the __project__ tree node."""
    items = _resolved(_context(shadow_wired, _STRIPPED))[1]
    assert items[0]["qualified_name"] == _CANONICAL
    assert items[0]["path"] == "src/srcpkg/scoring.py"


@pytest.mark.parametrize("depth", ["summary", "tree"])
def test_exact_target_keeps_dependency_precedence(shadow_wired, shadow_null, depth):
    """AC9: an indexed dependency still wins an exact ``srcpkg.…`` target."""
    outcome = _resolved(_symbol(shadow_wired, _CANONICAL, depth))
    assert outcome == _symbol(shadow_null, _CANONICAL, depth)
    assert DEPENDENCY_SCORER_DOC in str(outcome[0])


@pytest.mark.parametrize("depth", _DEPTHS)
@pytest.mark.parametrize("target", _EXACT_SYMBOL_TARGETS)
def test_shadowed_exact_targets_match_null(shadow_wired, shadow_null, target, depth):
    """AC9 where main resolves; AC7 prefix where the dependency shadows it.

    WHY both branches: with a dependency named ``srcpkg`` indexed, an exact
    ``srcpkg.…`` target routes to the dependency, so a project-only symbol
    (e.g. ``srcpkg.scoring.Score``) is a miss on main too — Rule 1 is shadowed
    by design (spec §2.4 Rule 1.2), and the miss only gains candidates.
    """
    baseline = _symbol(shadow_null, target, depth)
    outcome = _symbol(shadow_wired, target, depth)
    if baseline[0] == "raised":
        assert _miss(outcome).startswith(_miss(baseline))
        return
    assert outcome == baseline


# ── AC12: per-rule flag ablation ─────────────────────────────────────────


def test_source_root_strip_off_errors_as_today(src_db, null):
    router = build_router(src_db, _config(source_root_strip=False))
    today = _miss(_symbol(null, _STRIPPED))
    assert _miss(_symbol(router, _STRIPPED)) == f"{today}. {_CLOSEST_SCORER}"
    assert _symbol(router, _BARE) == _resolved(_symbol(router, _CANONICAL))


def test_unique_bare_name_off_errors_but_keeps_candidates(src_db, null):
    router = build_router(src_db, _config(unique_bare_name=False))
    today = _miss(_symbol(null, _BARE))
    assert _miss(_symbol(router, _BARE)) == f"{today}. {_CLOSEST_SCORER}"
    assert _symbol(router, _STRIPPED) == _resolved(_symbol(router, _CANONICAL))


def test_miss_candidates_off_appends_no_sentence(src_db, null):
    router = build_router(src_db, _config(miss_candidates=False))
    for target, depth in (("main", "summary"), ("MaxSimScorr", "source")):
        assert _miss(_symbol(router, target, depth)) == _miss(_symbol(null, target, depth))
    assert _miss(_symbol(router, "srcpkg.MaxSimScorer")) == _miss(
        _symbol(null, "srcpkg.MaxSimScorer")
    )
    assert _symbol(router, _STRIPPED) == _resolved(_symbol(router, _CANONICAL))
    assert _symbol(router, _BARE) == _resolved(_symbol(router, _CANONICAL))


_ALL_OFF_PROBES = (_STRIPPED, _BARE, "src.srcpkg.scoring", "main", "score", "MaxSimScorr")


def test_all_flags_off_wires_null_and_matches_null_byte_for_byte(src_db, null):
    router = build_router(
        src_db, _config(source_root_strip=False, unique_bare_name=False, miss_candidates=False)
    )
    assert isinstance(router.services[0].lookup.target_resolver, NullTargetResolver)
    for target in _ALL_OFF_PROBES:
        for depth in _DEPTHS:
            assert _symbol(router, target, depth) == _symbol(null, target, depth)
        assert _refs(router, target) == _refs(null, target)
        assert _context(router, target) == _context(null, target)


# ── AC14: one structured log line per resolved fallback ──────────────────

_RESOLVED_CALLS = (
    ("symbol", _STRIPPED, "lookup", "source_root_strip"),
    ("symbol", _BARE, "lookup", "unique_bare_name"),
    ("source", _STRIPPED, "source", "source_root_strip"),
    ("source", _BARE, "source", "unique_bare_name"),
    ("refs", _STRIPPED, "lookup", "source_root_strip"),
    ("context", _BARE, "context", "unique_bare_name"),
)


def _call(router: ToolRouter, surface: str, target: str) -> tuple[object, ...]:
    if surface == "refs":
        return _refs(router, target)
    if surface == "context":
        return _context(router, target)
    return _symbol(router, target, "source" if surface == "source" else "summary")


@pytest.mark.parametrize(("surface", "target", "entry", "rule"), _RESOLVED_CALLS)
def test_resolved_fallback_logs_exactly_one_event(wired, caplog, surface, target, entry, rule):
    caplog.set_level(logging.INFO, logger=_RESOLUTION_LOGGER)
    _resolved(_call(wired, surface, target))
    events = _fallback_events(caplog)
    assert events == [
        {
            "event": "target_fallback_resolved",
            "entry": entry,
            "rule": rule,
            "target": target,
            "resolved": _CANONICAL,
        }
    ]
    assert list(events[0]) == _LOG_KEYS
    assert not [r for r in caplog.records if r.name == _SUGGESTIONS_LOGGER]


@pytest.mark.parametrize(
    ("surface", "target"),
    [
        ("symbol", "main"),
        ("source", "MaxSimScorr"),
        ("symbol", "md"),
        ("context", "src.srcpkg.scoring"),
        ("symbol", _CANONICAL),
        ("source", _CANONICAL),
        ("refs", _CANONICAL),
        ("context", _CANONICAL),
    ],
)
def test_misses_and_exact_hits_log_nothing(wired, caplog, surface, target):
    caplog.set_level(logging.INFO, logger=_RESOLUTION_LOGGER)
    _call(wired, surface, target)
    assert _fallback_events(caplog) == []
