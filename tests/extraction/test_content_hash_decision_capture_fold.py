"""A non-default ``decision_capture`` folds into the package hashes it mines (#263, #346).

``CaptureDecisionsPipeline`` reads ``decision_capture`` and emits every mined
decision AS A CHUNK, but nothing about those settings reached a cache key. And
``content_hash`` runs after ``capture_decisions`` and ``embed_chunks`` in
``pipelines/ingestion.yaml`` (mining runs on every pass regardless), so a
changed knob made every pass re-embed the changed decision chunks, then discard
them as a package cache hit — forever, healed only by ``index --force``. The
same loop the chunker tunables had (fixed with the chunk-tree salt).

Two properties shape the fold, and each has tests here:

- CONDITIONAL: today's stock ``DecisionCaptureConfig()`` folds nothing, so
  every stored hash of a stock deployment is byte-identical and upgrading
  re-extracts nothing. That rests on the shipped ``default_config.yaml``
  loading to exactly the stock model — pinned below — and on the stage
  comparing against a PINNED stock digest rather than a live
  ``DecisionCaptureConfig()``, so a later release that changes a default still
  folds for a stock deployment instead of re-opening the loop.
- WHERE MINING RUNS: ``CaptureDecisionsPipeline.run`` mines a dependency only
  while ``enabled`` and ``include_deps`` both hold (#346). Anywhere else a
  dependency's extraction cannot depend on these settings, and folding them
  there would re-extract every dependency for nothing; while they hold, the
  settings decide the dependency's decision chunks, so they must fold.

Every expectation is derived from the independent oracle, never from the
stage's own helper, so a stage that digested the wrong thing cannot move the
expectation along with it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import Field

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ContentHashStage
from pydocs_mcp.extraction.pipeline.stages import content_hash as stage_module
from pydocs_mcp.project_toml import (
    EMPTY_PROJECT_EXCLUDES,
    ProjectExcludes,
    exclusion_fingerprint,
)
from pydocs_mcp.retrieval.config import AppConfig, DecisionCaptureConfig
from tests.extraction._content_hash_oracle import (
    chunk_tree_folded,
    decision_capture_folded,
    decision_capture_token,
    dependency_decision_capture_folded,
    digest_fold,
    grammar_folded,
    pipeline_folded,
    raw_hash_files,
    rule_folded,
)

_PIPELINE_HASH = "PIPE-1"
_USER_EXCLUDES = ProjectExcludes(names=_EXCLUDED_DIRS | {"fixtures"}, anchored=frozenset())

# YAML-shaped overlays, one per knob family, including nested ones — each is
# what an operator's ``decision_capture:`` block would parse to.
_NON_DEFAULT_OVERLAYS: dict[str, dict[str, Any]] = {
    "merge_jaccard": {"merge_jaccard": 0.5},
    "sources_subset": {"sources": ["adr_files", "changelog"]},
    "disabled": {"enabled": False},
    "commit_messages.max_commits": {"commit_messages": {"max_commits": 10}},
    "llm_structuring.batch_size": {"llm_structuring": {"batch_size": 7}},
    "inline_markers.context_lines": {"inline_markers": {"context_lines": 3}},
}


# The token today's stock ``DecisionCaptureConfig()`` would carry — an
# independent golden of the baseline the stage pins, spelled out here so a stage
# that re-pinned itself could not move the expectation along with it.
_SHIPPED_STOCK_TOKEN = "decisions:67e6c428e8c34ab7"


class _BumpedDefaultsDecisionCaptureConfig(DecisionCaptureConfig):
    """What a later release's ``DecisionCaptureConfig`` would look like had it
    moved ONE Field default (and, with it, what stock mining emits)."""

    merge_jaccard: float = Field(0.9, gt=0.0, le=1.0)


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate every ``AppConfig.load()`` here from the developer's own config.

    Two tests below pin the SHIPPED defaults, but a bare load also layers
    ``PYDOCS_CONFIG_PATH``, ``./pydocs-mcp.yaml``,
    ``~/.config/pydocs-mcp/config.yaml`` and ``PYDOCS_DECISION_CAPTURE*`` env
    vars, so a user config tuning one decision knob would fail them spuriously
    (the tests/retrieval/test_reference_graph_config.py precedent, plus HOME).
    """
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    for name in [name for name in os.environ if name.startswith("PYDOCS_DECISION_CAPTURE")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)  # no ./pydocs-mcp.yaml
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.config/pydocs-mcp/config.yaml
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() on Windows


def _tuned(overlay: dict[str, Any]) -> DecisionCaptureConfig:
    config = DecisionCaptureConfig.model_validate(overlay)
    assert config != DecisionCaptureConfig(), f"overlay {overlay!r} is not a change"
    return config


@pytest.fixture
def one_file(tmp_path: Path) -> Path:
    """Written ONCE per test: ``hash_files`` folds each path's mtime, so a
    rewrite between two runs would move the base digest and make every
    "the hash changed" assertion pass for the wrong reason."""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    return f


def _state(
    f: Path,
    kind: TargetKind = TargetKind.PROJECT,
    excludes: ProjectExcludes = EMPTY_PROJECT_EXCLUDES,
) -> IngestionState:
    bundle = FileBundle(
        target=f.parent,
        target_kind=kind,
        package_name="__project__" if kind is TargetKind.PROJECT else "somedep",
        paths=(str(f),),
        effective_excludes=excludes,
    )
    return IngestionState(files=bundle)


async def _run(stage: ContentHashStage, state: IngestionState) -> str:
    return (await stage.run(state)).files.content_hash


async def _hash(state: IngestionState, config: DecisionCaptureConfig | None = None) -> str:
    stage = ContentHashStage(
        decision_capture=config if config is not None else DecisionCaptureConfig()
    )
    return await _run(stage, state)


# ── conditional: a stock deployment folds nothing ─────────────────────────


@pytest.mark.asyncio
async def test_a_stock_config_folds_nothing_into_a_project_hash(one_file: Path) -> None:
    state = _state(one_file)
    stock_framing = chunk_tree_folded(grammar_folded(rule_folded(raw_hash_files([str(one_file)]))))

    assert await _run(ContentHashStage(), state) == stock_framing
    assert await _hash(state, DecisionCaptureConfig()) == stock_framing


@pytest.mark.asyncio
async def test_a_stock_config_folds_nothing_into_a_dependency_hash(one_file: Path) -> None:
    state = _state(one_file, TargetKind.DEPENDENCY)

    assert await _hash(state) == chunk_tree_folded(grammar_folded(raw_hash_files([str(one_file)])))


def test_the_stock_config_still_carries_the_shipped_baseline_token() -> None:
    """The stage folds nothing only for settings whose digest equals the baseline
    it pinned when this fold shipped; today that is ``DecisionCaptureConfig()``."""
    assert decision_capture_token(DecisionCaptureConfig()) == _SHIPPED_STOCK_TOKEN, (
        "DecisionCaptureConfig's defaults or fields changed, so every stock "
        "deployment now folds a decisions token and re-extracts its project ONCE "
        "on upgrade. That is intended when the change alters what stock mining "
        "emits: leave the stage's _STOCK_DECISION_CAPTURE_DIGEST alone (a stale "
        "stock baseline re-opens the #263 loop), update this golden and the "
        "stock-deployment tests, and say so in the CHANGELOG. Re-pin the stage "
        "ONLY for a change that leaves stock mining output unchanged."
    )


@pytest.mark.asyncio
async def test_a_later_default_change_folds_for_a_stock_deployment(
    one_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release that moves a ``DecisionCaptureConfig`` default changes what a
    stock deployment mines, so its project hash must move once — otherwise the
    new decision chunks are re-embedded and discarded on every pass, the #263
    loop again. Simulated by making the stage module's own
    ``DecisionCaptureConfig`` the bumped model: a stage that compared against a
    live ``DecisionCaptureConfig()`` would see stock == stock and fold nothing."""
    state = _state(one_file)
    before_upgrade = await _hash(state)
    monkeypatch.setattr(stage_module, "DecisionCaptureConfig", _BumpedDefaultsDecisionCaptureConfig)
    bumped_stock = _BumpedDefaultsDecisionCaptureConfig()
    base = raw_hash_files([str(one_file)])

    after_upgrade = await _hash(state, bumped_stock)

    assert after_upgrade != before_upgrade
    assert after_upgrade == chunk_tree_folded(
        grammar_folded(decision_capture_folded(rule_folded(base), bumped_stock))
    )


def test_the_decision_token_input_is_identical_in_a_fresh_interpreter() -> None:
    """The token must be bit-identical across PROCESSES, not merely within one:
    a per-process value would make a tuned deployment miss its own stored hash on
    every pass. The in-process settle tests share one interpreter and cannot see
    it, and the oracle derives the token the same way, so a nondeterministic
    serialization (a set-typed knob under hash randomization) would slip past
    both — hence a real subprocess under a randomized hash seed (the
    test_chunk_tree_fingerprint.py precedent). The token is ``md5`` of exactly
    this blob, so equal blobs mean equal tokens."""
    overlay = {key: value for knob in _NON_DEFAULT_OVERLAYS.values() for key, value in knob.items()}
    source = (
        "import json, sys;"
        "from pydocs_mcp.retrieval.config import DecisionCaptureConfig;"
        "print(DecisionCaptureConfig.model_validate(json.loads(sys.argv[1])).model_dump_json())"
    )
    result = subprocess.run(
        [sys.executable, "-c", source, json.dumps(overlay)],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": "random"},
    )

    assert result.stdout.strip() == _tuned(overlay).model_dump_json()


def test_the_shipped_defaults_load_to_the_stock_config() -> None:
    """The conditional fold's whole upgrade guarantee: if ``default_config.yaml``
    ever loaded to anything but ``DecisionCaptureConfig()`` (a restated value
    drifting from its Field default), every stock deployment would fold the
    token and re-extract its project on upgrade."""
    assert AppConfig.load().decision_capture == DecisionCaptureConfig()


@pytest.mark.asyncio
async def test_the_shipped_defaults_wired_through_from_dict_hash_like_a_bare_stage(
    one_file: Path,
) -> None:
    """The golden-stable guarantee end to end: the composition root's decoder,
    fed the shipped defaults, produces the SAME hash as a stage that never heard
    of ``decision_capture``."""
    wired = ContentHashStage.from_dict({}, SimpleNamespace(app_config=AppConfig.load()))

    assert wired.decision_capture == DecisionCaptureConfig()
    assert await _run(wired, _state(one_file)) == await _run(ContentHashStage(), _state(one_file))


# ── a non-default project config moves the hash, at the documented place ──


@pytest.mark.asyncio
@pytest.mark.parametrize("overlay", _NON_DEFAULT_OVERLAYS.values(), ids=_NON_DEFAULT_OVERLAYS)
async def test_a_non_default_knob_moves_the_project_hash(
    one_file: Path, overlay: dict[str, Any]
) -> None:
    """Every knob, nested ones included: the digest covers the whole model, so a
    knob added later folds itself rather than waiting for someone to list it."""
    state = _state(one_file)

    assert await _hash(state, _tuned(overlay)) != await _hash(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("overlay", _NON_DEFAULT_OVERLAYS.values(), ids=_NON_DEFAULT_OVERLAYS)
async def test_the_decision_token_folds_between_the_rule_token_and_the_grammar_salt(
    one_file: Path, overlay: dict[str, Any]
) -> None:
    config = _tuned(overlay)
    base = raw_hash_files([str(one_file)])

    assert await _hash(_state(one_file), config) == chunk_tree_folded(
        grammar_folded(decision_capture_folded(rule_folded(base), config))
    )


@pytest.mark.asyncio
async def test_the_full_six_fold_framing_with_excludes_and_an_identity_salt(
    one_file: Path,
) -> None:
    """All six folds in play, with the real salts: exclusion → rule token →
    decision token → grammars → chunk-tree → identity."""
    config = _tuned(_NON_DEFAULT_OVERLAYS["merge_jaccard"])
    fingerprint = exclusion_fingerprint(_USER_EXCLUDES, _EXCLUDED_DIRS)
    assert fingerprint is not None  # a real user exclude, so that fold applies too
    stage = ContentHashStage(pipeline_hash=_PIPELINE_HASH, decision_capture=config)

    base = raw_hash_files([str(one_file)])
    inner = decision_capture_folded(rule_folded(digest_fold(base, fingerprint)), config)
    expected = pipeline_folded(chunk_tree_folded(grammar_folded(inner)), _PIPELINE_HASH)

    assert await _run(stage, _state(one_file, excludes=_USER_EXCLUDES)) == expected


# ── the pass settles: equal configs agree, different ones do not ──────────


@pytest.mark.asyncio
async def test_equal_non_default_configs_produce_one_hash(one_file: Path) -> None:
    """Two passes under the same overlay build two stages from two separately
    parsed configs. They must agree, or the tuned deployment would miss its own
    stored hash on every pass — the loop this fold exists to end."""
    overlay = _NON_DEFAULT_OVERLAYS["commit_messages.max_commits"]
    state = _state(one_file)

    assert await _hash(state, _tuned(overlay)) == await _hash(state, _tuned(overlay))


@pytest.mark.asyncio
async def test_different_non_default_configs_produce_different_hashes(one_file: Path) -> None:
    state = _state(one_file)
    first = _tuned({"merge_jaccard": 0.5})
    second = _tuned({"merge_jaccard": 0.6})

    assert await _hash(state, first) != await _hash(state, second)


# ── a dependency folds it only while include_deps mines it (#346) ─────────


@pytest.mark.asyncio
@pytest.mark.parametrize("overlay", _NON_DEFAULT_OVERLAYS.values(), ids=_NON_DEFAULT_OVERLAYS)
async def test_a_dependency_hash_ignores_decision_capture(
    one_file: Path, overlay: dict[str, Any]
) -> None:
    """Without ``include_deps`` (every overlay here leaves it off),
    ``CaptureDecisionsPipeline.run`` returns a dependency's state untouched, so
    no knob can change what a dependency extracts — folding one would bill
    every dependency a re-extraction for nothing."""
    state = _state(one_file, TargetKind.DEPENDENCY)

    assert await _hash(state, _tuned(overlay)) == await _hash(state)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overlay",
    [{"include_deps": True}, {"include_deps": True, "merge_jaccard": 0.5}],
    ids=["include_deps", "include_deps+merge_jaccard"],
)
async def test_a_dependency_folds_the_decision_token_while_include_deps_mines_it(
    one_file: Path, overlay: dict[str, Any]
) -> None:
    """Mining now decides the dependency's decision chunks, so the whole config
    folds at the #263 position — the same plain token a project gets."""
    config = _tuned(overlay)
    base = raw_hash_files([str(one_file)])

    folded = await _hash(_state(one_file, TargetKind.DEPENDENCY), config)

    assert folded == chunk_tree_folded(
        grammar_folded(dependency_decision_capture_folded(base, config))
    )
    assert folded != await _hash(_state(one_file, TargetKind.DEPENDENCY))


@pytest.mark.asyncio
async def test_include_deps_with_capture_disabled_folds_nothing_into_a_dependency(
    one_file: Path,
) -> None:
    """``enabled: false`` switches all mining off, dependencies included, so the
    dependency hash stays the stock one (while the project, whose decision rows
    the switch deletes, still folds)."""
    state = _state(one_file, TargetKind.DEPENDENCY)
    config = _tuned({"enabled": False, "include_deps": True})

    assert await _hash(state, config) == await _hash(state)
    assert await _hash(_state(one_file), config) != await _hash(_state(one_file))


@pytest.mark.asyncio
async def test_include_deps_leaves_the_project_framing_unchanged(one_file: Path) -> None:
    """The project branch is untouched: ``include_deps`` is one more knob in the
    whole-config digest, folded where every project knob folds."""
    config = _tuned({"include_deps": True})
    base = raw_hash_files([str(one_file)])

    assert await _hash(_state(one_file), config) == chunk_tree_folded(
        grammar_folded(decision_capture_folded(rule_folded(base), config))
    )


# ── wiring: read from the app config, never serialized ────────────────────


def test_from_dict_reads_decision_capture_from_the_app_config() -> None:
    """Wired exactly as ``CaptureDecisionsPipeline.from_dict`` wires it, so the
    stage that mines and the stage that hashes see the same settings."""
    config = _tuned(_NON_DEFAULT_OVERLAYS["sources_subset"])
    context = SimpleNamespace(app_config=SimpleNamespace(decision_capture=config))

    assert ContentHashStage.from_dict({}, context).decision_capture == config


def test_from_dict_without_an_app_config_uses_the_stock_config() -> None:
    assert ContentHashStage.from_dict({}, object()).decision_capture == DecisionCaptureConfig()


def test_decision_capture_is_wiring_not_a_stage_tunable() -> None:
    """It belongs to the ``decision_capture:`` block, not to this stage's YAML."""
    stage = ContentHashStage(decision_capture=_tuned(_NON_DEFAULT_OVERLAYS["disabled"]))

    assert stage.to_dict() == {"type": "content_hash"}
