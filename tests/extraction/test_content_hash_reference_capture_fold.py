"""A non-stock ``reference_graph.capture`` folds into EVERY package hash (#347).

``ReferenceCaptureStage`` captures edges with ``reference_graph.capture`` and
runs BEFORE ``content_hash`` in ``pipelines/ingestion.yaml``, but the settings
reached no cache key. So turning ``mentions`` on, or capture off, left every
already-indexed package a cache hit: the pass captured the new edge set and
then discarded it, forever, healed only by ``index --force``.

Three properties shape the fold, and each has tests here:

- CONDITIONAL: the stock capture settings fold nothing, so every stored hash of
  a stock deployment is byte-identical and upgrading re-extracts nothing. That
  rests on the shipped ``default_config.yaml`` loading to exactly the stock
  model (pinned below) and on the stage comparing against a PINNED token rather
  than a live ``ReferenceCaptureConfig()``, so a later release that changes a
  default still folds for a stock deployment.
- NORMALIZED: capture reads ``frozenset(kinds)``, so kind order and duplicates
  change no edge and must change no hash; with capture off the kinds are moot.
- EVERY PACKAGE: capture does not gate on target kind, so dependencies fold too.

Every expectation is derived from the independent oracle, never from the
stage's own helper.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ContentHashStage
from pydocs_mcp.extraction.pipeline.stages import content_hash as stage_module
from pydocs_mcp.retrieval.config import AppConfig, DecisionCaptureConfig, ReferenceCaptureConfig
from tests.extraction._content_hash_oracle import (
    chunk_tree_folded,
    decision_capture_folded,
    grammar_folded,
    pipeline_folded,
    raw_hash_files,
    reference_capture_folded,
    reference_capture_token,
    rule_folded,
)

_PIPELINE_HASH = "PIPE-1"
_WITH_MENTIONS = ReferenceCaptureConfig(kinds=("calls", "imports", "inherits", "mentions"))
_DISABLED = ReferenceCaptureConfig(enabled=False)

# The token today's stock ``ReferenceCaptureConfig()`` normalizes to — an
# independent golden of the baseline the stage pins, spelled out here so a
# stage that re-pinned itself could not move the expectation along with it.
_SHIPPED_STOCK_TOKEN = "refs:calls,imports,inherits"


class _BumpedDefaultsReferenceCaptureConfig(ReferenceCaptureConfig):
    """What a later release's ``ReferenceCaptureConfig`` would look like had it
    turned MENTIONS on by default (and, with it, what stock capture emits)."""

    kinds: tuple[str, ...] = ("calls", "imports", "inherits", "mentions")


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate every ``AppConfig.load()`` here from the developer's own config:
    ``PYDOCS_CONFIG_PATH``, ``./pydocs-mcp.yaml``,
    ``~/.config/pydocs-mcp/config.yaml`` and ``PYDOCS_REFERENCE_GRAPH*`` env
    vars would otherwise fail the shipped-defaults pins spuriously (the #263
    suite's fixture, retargeted)."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    for name in [name for name in os.environ if name.startswith("PYDOCS_REFERENCE_GRAPH")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


@pytest.fixture
def one_file(tmp_path: Path) -> Path:
    """Written ONCE per test: ``hash_files`` folds each path's mtime, so a
    rewrite between two runs would move the base digest and make every
    "the hash changed" assertion pass for the wrong reason."""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    return f


def _state(f: Path, kind: TargetKind = TargetKind.PROJECT) -> IngestionState:
    bundle = FileBundle(
        target=f.parent,
        target_kind=kind,
        package_name="__project__" if kind is TargetKind.PROJECT else "somedep",
        paths=(str(f),),
    )
    return IngestionState(files=bundle)


async def _run(stage: ContentHashStage, state: IngestionState) -> str:
    return (await stage.run(state)).files.content_hash


async def _hash(state: IngestionState, config: ReferenceCaptureConfig | None = None) -> str:
    stage = ContentHashStage(
        reference_capture=config if config is not None else ReferenceCaptureConfig()
    )
    return await _run(stage, state)


def _stock_framing(f: Path, kind: TargetKind = TargetKind.PROJECT) -> str:
    base = raw_hash_files([str(f)])
    inner = rule_folded(base) if kind is TargetKind.PROJECT else base
    return chunk_tree_folded(grammar_folded(inner))


# ── conditional: a stock deployment folds nothing ─────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [TargetKind.PROJECT, TargetKind.DEPENDENCY])
async def test_a_stock_config_folds_nothing(one_file: Path, kind: TargetKind) -> None:
    state = _state(one_file, kind)

    assert await _run(ContentHashStage(), state) == _stock_framing(one_file, kind)
    assert await _hash(state, ReferenceCaptureConfig()) == _stock_framing(one_file, kind)


def test_the_stock_config_still_carries_the_shipped_baseline_token() -> None:
    """The stage folds nothing only for settings that normalize to the token it
    pinned when this fold shipped; today that is ``ReferenceCaptureConfig()``."""
    assert reference_capture_token(ReferenceCaptureConfig()) == _SHIPPED_STOCK_TOKEN, (
        "ReferenceCaptureConfig's defaults changed, so every stock deployment now "
        "folds a refs token and re-extracts EVERY package once on upgrade. That is "
        "intended when the change alters what stock capture emits: leave the "
        "stage's _STOCK_REFERENCE_CAPTURE_TOKEN alone, update this golden, and say "
        "so in the CHANGELOG."
    )


@pytest.mark.asyncio
async def test_a_later_default_change_folds_for_a_stock_deployment(
    one_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release that moves a capture default changes what a stock deployment
    captures, so its hashes must move once. A stage that compared against a live
    ``ReferenceCaptureConfig()`` would see stock == stock and fold nothing."""
    state = _state(one_file)
    before_upgrade = await _hash(state)
    monkeypatch.setattr(
        stage_module, "ReferenceCaptureConfig", _BumpedDefaultsReferenceCaptureConfig
    )
    bumped_stock = _BumpedDefaultsReferenceCaptureConfig()

    after_upgrade = await _hash(state, bumped_stock)

    assert after_upgrade != before_upgrade
    base = rule_folded(raw_hash_files([str(one_file)]))
    assert after_upgrade == chunk_tree_folded(
        grammar_folded(reference_capture_folded(base, bumped_stock))
    )


def test_the_shipped_defaults_load_to_the_stock_config() -> None:
    """The conditional fold's whole upgrade guarantee: a shipped YAML that
    loaded to anything but ``ReferenceCaptureConfig()`` would fold the token and
    re-extract every package of every stock deployment on upgrade."""
    assert AppConfig.load().reference_graph.capture == ReferenceCaptureConfig()


@pytest.mark.asyncio
async def test_the_shipped_defaults_wired_through_from_dict_hash_like_a_bare_stage(
    one_file: Path,
) -> None:
    wired = ContentHashStage.from_dict({}, SimpleNamespace(app_config=AppConfig.load()))

    assert wired.reference_capture == ReferenceCaptureConfig()
    assert await _run(wired, _state(one_file)) == await _run(ContentHashStage(), _state(one_file))


# ── normalized: order, duplicates and disabled kinds are not changes ──────


@pytest.mark.asyncio
async def test_kind_order_and_duplicates_do_not_move_a_stock_hash(one_file: Path) -> None:
    reordered = ReferenceCaptureConfig(kinds=("inherits", "calls", "imports", "calls"))

    assert await _hash(_state(one_file), reordered) == _stock_framing(one_file)


@pytest.mark.asyncio
async def test_kind_order_and_duplicates_do_not_move_a_tuned_hash(one_file: Path) -> None:
    state = _state(one_file)
    shuffled = ReferenceCaptureConfig(kinds=("mentions", "inherits", "calls", "imports", "calls"))

    assert await _hash(state, shuffled) == await _hash(state, _WITH_MENTIONS)


@pytest.mark.asyncio
async def test_disabled_capture_ignores_the_kinds(one_file: Path) -> None:
    """Capture off emits nothing whatever the kinds, so they must not fold."""
    state = _state(one_file)
    disabled_with_mentions = ReferenceCaptureConfig(enabled=False, kinds=_WITH_MENTIONS.kinds)

    assert await _hash(state, disabled_with_mentions) == await _hash(state, _DISABLED)


# ── a non-stock config moves every package, at the documented place ───────


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [TargetKind.PROJECT, TargetKind.DEPENDENCY])
@pytest.mark.parametrize("config", [_WITH_MENTIONS, _DISABLED], ids=["mentions", "disabled"])
async def test_a_non_stock_config_moves_the_hash(
    one_file: Path, kind: TargetKind, config: ReferenceCaptureConfig
) -> None:
    """Capture runs for dependencies too, so they must fold as well — otherwise
    a dependency keeps the edges of the settings it was first indexed with."""
    state = _state(one_file, kind)

    assert await _hash(state, config) != await _hash(state)


@pytest.mark.asyncio
async def test_mentions_and_disabled_are_different_hashes(one_file: Path) -> None:
    state = _state(one_file)

    assert await _hash(state, _WITH_MENTIONS) != await _hash(state, _DISABLED)


@pytest.mark.asyncio
async def test_the_refs_token_folds_between_the_decision_token_and_the_grammar_salt(
    one_file: Path,
) -> None:
    decisions = DecisionCaptureConfig(merge_jaccard=0.5)
    stage = ContentHashStage(
        pipeline_hash=_PIPELINE_HASH, decision_capture=decisions, reference_capture=_WITH_MENTIONS
    )
    base = rule_folded(raw_hash_files([str(one_file)]))
    inner = reference_capture_folded(decision_capture_folded(base, decisions), _WITH_MENTIONS)
    expected = pipeline_folded(chunk_tree_folded(grammar_folded(inner)), _PIPELINE_HASH)

    assert await _run(stage, _state(one_file)) == expected


@pytest.mark.asyncio
async def test_a_dependency_folds_the_refs_token_without_the_rule_token(one_file: Path) -> None:
    base = raw_hash_files([str(one_file)])
    expected = chunk_tree_folded(grammar_folded(reference_capture_folded(base, _DISABLED)))

    assert await _hash(_state(one_file, TargetKind.DEPENDENCY), _DISABLED) == expected


# ── the pass settles across processes ─────────────────────────────────────

_SUBPROCESS_HASH = """
import asyncio, sys
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ContentHashStage
from pydocs_mcp.retrieval.config import ReferenceCaptureConfig

path, kinds = sys.argv[1], tuple(sys.argv[2].split(","))
state = IngestionState(files=FileBundle(
    target=path, target_kind=TargetKind.PROJECT, package_name="__project__", paths=(path,)))
stage = ContentHashStage(reference_capture=ReferenceCaptureConfig(kinds=kinds))
print(asyncio.run(stage.run(state)).files.content_hash)
"""


@pytest.mark.asyncio
async def test_a_tuned_hash_is_identical_in_a_fresh_interpreter(one_file: Path) -> None:
    """The token must be bit-identical across PROCESSES: a per-process value
    (a set iterated under hash randomization) would make a tuned deployment
    miss its own stored hash on every pass. The in-process tests share one
    interpreter and cannot see it — hence a real subprocess under a randomized
    hash seed (the test_chunk_tree_fingerprint.py precedent)."""
    kinds = ("mentions", "inherits", "calls", "imports", "calls")
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_HASH, str(one_file), ",".join(kinds)],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": "random"},
    )

    in_process = await _hash(_state(one_file), ReferenceCaptureConfig(kinds=kinds))
    assert in_process != _stock_framing(one_file), "the tuned kinds must actually fold"
    assert result.stdout.strip() == in_process


# ── wiring: read from the app config, never serialized ────────────────────


def test_from_dict_reads_the_capture_settings_from_the_app_config() -> None:
    """The same object ``ReferenceCaptureStage.from_dict`` binds, so the stage
    that hashes sees the settings the stage that captures used."""
    graph = SimpleNamespace(capture=_WITH_MENTIONS)
    context = SimpleNamespace(app_config=SimpleNamespace(reference_graph=graph))

    assert ContentHashStage.from_dict({}, context).reference_capture == _WITH_MENTIONS


@pytest.mark.parametrize(
    "context",
    [object(), SimpleNamespace(app_config=SimpleNamespace(decision_capture=None))],
    ids=["no-app-config", "no-reference-graph"],
)
def test_from_dict_without_capture_settings_uses_the_stock_config(context: object) -> None:
    assert ContentHashStage.from_dict({}, context).reference_capture == ReferenceCaptureConfig()


def test_reference_capture_is_wiring_not_a_stage_tunable() -> None:
    """It belongs to the ``reference_graph:`` block, not to this stage's YAML —
    and an unchanged ``to_dict`` keeps every pipeline YAML byte where it was."""
    stage = ContentHashStage(reference_capture=_DISABLED)

    assert stage.to_dict() == {"type": "content_hash"}
