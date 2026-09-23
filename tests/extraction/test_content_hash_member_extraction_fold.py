"""A non-stock member-extraction identity folds into DEPENDENCY hashes (#347).

``ProjectIndexer`` extracts a dependency's members AFTER the package cache
check, with the extractor ``build_project_indexer`` wires from ``--no-inspect``,
``--depth`` and ``extraction.members.*``. None of those reached a cache key, so
changing one left every already-indexed dependency a cache hit: its members
stayed whatever the settings it was first indexed with produced, forever,
healed only by ``index --force``.

Four properties shape the fold, and each has tests here:

- EFFECTIVE: the token names what the extractor actually uses. Static mode is
  one token, because ``AstMemberExtractor`` ignores the depth and all three
  caps; inspect mode spells out the depth the CLI resolved and every cap.
- CONDITIONAL: the stock identity (inspect mode at the shipped defaults) folds
  nothing, so every stored hash of a stock deployment is byte-identical. That
  rests on the shipped ``default_config.yaml`` loading to exactly
  ``MembersConfig()`` (pinned below) and on the stage comparing against a PINNED
  token, so a later release that changes a default still folds by itself.
- DEPENDENCY ONLY: project members always come from ``AstMemberExtractor``
  (``InspectMemberExtractor`` hands the project to its static fallback), so no
  member setting can change them and a project fold would only re-extract. The
  invariant is pinned through the real composition root in
  tests/storage/test_build_project_indexer.py.
- WIRING, NOT A TUNABLE: only the composition root knows ``use_inspect`` and the
  resolved ``--depth``, so the token rides ``BuildContext``; ``""`` (every
  hand-wired root) folds nothing, and ``to_dict`` does not move.

Every expected hash is derived from the independent oracle, and every token is
a literal golden, never the stage's or the helper's own output.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from pydocs_mcp.extraction import config as config_module
from pydocs_mcp.extraction.config import MembersConfig
from pydocs_mcp.extraction.factories import build_ingestion_pipeline
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ContentHashStage
from pydocs_mcp.extraction.pipeline.stages import content_hash as stage_module
from pydocs_mcp.extraction.strategies.members import extraction_token as token_module
from pydocs_mcp.extraction.strategies.members.extraction_token import member_extraction_token
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.serialization import BuildContext
from tests._fakes import MockEmbedder, make_fake_uow_factory
from tests.extraction._content_hash_oracle import (
    chunk_tree_folded,
    grammar_folded,
    member_extraction_folded,
    raw_hash_files,
    rule_folded,
)

# The token today's CLI default (inspect mode, ``MembersConfig()``) produces —
# an independent golden of the baseline the stage pins, spelled out so a stage
# that re-pinned itself could not move the expectation along with it.
_SHIPPED_STOCK_TOKEN = "inspect|depth=1|cap=120|sig=200|doc=1024"
_STATIC = "static"
_ONE_MEMBER_PER_MODULE = "inspect|depth=1|cap=1|sig=200|doc=1024"


class _BumpedDefaultsMembersConfig(MembersConfig):
    """What a later release's ``MembersConfig`` would look like had it raised the
    per-module cap by default (and, with it, what stock extraction emits)."""

    members_per_module_cap: int = 500


# The token a stock deployment derives under ``_BumpedDefaultsMembersConfig``.
_BUMPED_STOCK_TOKEN = "inspect|depth=1|cap=500|sig=200|doc=1024"


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate every ``AppConfig.load()`` here from the developer's own config:
    ``PYDOCS_CONFIG_PATH``, ``./pydocs-mcp.yaml``,
    ``~/.config/pydocs-mcp/config.yaml`` and ``PYDOCS_EXTRACTION*`` env vars
    would otherwise fail the shipped-defaults pins spuriously (the #263 suite's
    fixture, retargeted)."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    for name in [name for name in os.environ if name.startswith("PYDOCS_EXTRACTION")]:
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


def _state(f: Path, kind: TargetKind = TargetKind.DEPENDENCY) -> IngestionState:
    bundle = FileBundle(
        target=f.parent,
        target_kind=kind,
        package_name="__project__" if kind is TargetKind.PROJECT else "somedep",
        paths=(str(f),),
    )
    return IngestionState(files=bundle)


async def _hash(state: IngestionState, token: str = "") -> str:
    stage = ContentHashStage(member_extraction_token=token)
    return (await stage.run(state)).files.content_hash


def _stock_framing(f: Path, kind: TargetKind = TargetKind.DEPENDENCY) -> str:
    base = raw_hash_files([str(f)])
    inner = rule_folded(base) if kind is TargetKind.PROJECT else base
    return chunk_tree_folded(grammar_folded(inner))


# ── effective: the token names what the extractor actually uses ───────────


@pytest.mark.parametrize(
    ("depth", "members"),
    [
        (1, MembersConfig()),
        (4, MembersConfig(inspect_depth=4)),
        (1, MembersConfig(members_per_module_cap=1, signature_max_chars=5)),
        (1, MembersConfig(docstring_max_chars=7)),
    ],
    ids=["defaults", "depth", "caps", "docstring"],
)
def test_static_mode_is_one_token_whatever_the_depth_and_caps(
    depth: int, members: MembersConfig
) -> None:
    """``AstMemberExtractor`` reads none of them, so tuning one under
    ``--no-inspect`` must re-extract nothing."""
    assert member_extraction_token(use_inspect=False, depth=depth, members=members) == _STATIC


def test_inspect_mode_at_the_shipped_defaults_is_the_stock_token() -> None:
    token = member_extraction_token(use_inspect=True, depth=1, members=MembersConfig())

    assert token == _SHIPPED_STOCK_TOKEN, (
        "MembersConfig's defaults changed, so every stock deployment now folds a "
        "members token and re-extracts EVERY dependency once on upgrade. That is "
        "intended when the change alters what stock extraction emits: leave the "
        "stage's _STOCK_MEMBER_EXTRACTION_TOKEN alone, update this golden, and say "
        "so in the CHANGELOG."
    )


@pytest.mark.parametrize(
    ("depth", "members", "expected"),
    [
        (2, MembersConfig(), "inspect|depth=2|cap=120|sig=200|doc=1024"),
        (1, MembersConfig(members_per_module_cap=1), _ONE_MEMBER_PER_MODULE),
        (1, MembersConfig(signature_max_chars=50), "inspect|depth=1|cap=120|sig=50|doc=1024"),
        (1, MembersConfig(docstring_max_chars=10), "inspect|depth=1|cap=120|sig=200|doc=10"),
    ],
    ids=["depth", "cap", "signature", "docstring"],
)
def test_each_inspect_setting_moves_the_token(
    depth: int, members: MembersConfig, expected: str
) -> None:
    assert member_extraction_token(use_inspect=True, depth=depth, members=members) == expected


def test_the_depth_argument_wins_over_the_yaml_depth() -> None:
    """The composition root resolves ``--depth`` against the YAML depth and
    hands the extractor the result; the token must name that same value."""
    members = MembersConfig(inspect_depth=1)

    token = member_extraction_token(use_inspect=True, depth=3, members=members)

    assert token == "inspect|depth=3|cap=120|sig=200|doc=1024"


def test_the_shipped_defaults_load_to_the_stock_members_config() -> None:
    """The conditional fold's whole upgrade guarantee: a shipped YAML that
    loaded to anything but ``MembersConfig()`` would fold the token and
    re-extract every dependency of every stock deployment on upgrade."""
    assert AppConfig.load().extraction.members == MembersConfig()


# ── conditional: a stock or an absent token folds nothing ─────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [TargetKind.PROJECT, TargetKind.DEPENDENCY])
@pytest.mark.parametrize("token", ["", _SHIPPED_STOCK_TOKEN], ids=["absent", "stock"])
async def test_a_stock_or_absent_token_folds_nothing(
    one_file: Path, kind: TargetKind, token: str
) -> None:
    """``""`` is every hand-wired root (the eval suite, the test harnesses) and
    a bare stage; the stock token is every default CLI deployment."""
    state = _state(one_file, kind)

    assert await _hash(state, token) == _stock_framing(one_file, kind)
    assert await _hash(state) == _stock_framing(one_file, kind)


@pytest.mark.asyncio
async def test_a_later_default_change_folds_for_a_stock_deployment(
    one_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release that moves a member default changes what a stock deployment
    extracts, so its dependency hashes must move once. Simulated by making
    every module's ``MembersConfig`` the bumped model — the config module that
    defines it, the stage module and the token module, ``raising=False`` where
    the name is not bound — and hashing the token such a stock deployment
    derives: a stage that compared against a live ``MembersConfig()`` would see
    stock == stock and fold nothing."""
    for module in (config_module, stage_module, token_module):
        monkeypatch.setattr(module, "MembersConfig", _BumpedDefaultsMembersConfig, raising=False)

    after_upgrade = await _hash(_state(one_file), _BUMPED_STOCK_TOKEN)

    assert after_upgrade != _stock_framing(one_file)
    expected = member_extraction_folded(raw_hash_files([str(one_file)]), _BUMPED_STOCK_TOKEN)
    assert after_upgrade == chunk_tree_folded(grammar_folded(expected))


# ── dependency only: a non-stock token moves a dependency, never the project ─


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [_STATIC, _ONE_MEMBER_PER_MODULE], ids=["static", "cap"])
async def test_a_non_stock_token_moves_a_dependency_hash(one_file: Path, token: str) -> None:
    moved = await _hash(_state(one_file), token)

    base = member_extraction_folded(raw_hash_files([str(one_file)]), token)
    assert moved == chunk_tree_folded(grammar_folded(base))
    assert moved != _stock_framing(one_file)


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [_STATIC, _ONE_MEMBER_PER_MODULE], ids=["static", "cap"])
async def test_a_non_stock_token_never_moves_the_project_hash(one_file: Path, token: str) -> None:
    """Project members ignore every member setting, so folding the token there
    would re-extract every static-mode project for no change in output."""
    state = _state(one_file, TargetKind.PROJECT)

    assert await _hash(state, token) == _stock_framing(one_file, TargetKind.PROJECT)


@pytest.mark.asyncio
async def test_static_and_tuned_inspect_are_different_dependency_hashes(one_file: Path) -> None:
    state = _state(one_file)

    assert await _hash(state, _STATIC) != await _hash(state, _ONE_MEMBER_PER_MODULE)


# ── wiring: carried by the build context, never serialized ────────────────


def test_from_dict_reads_the_token_from_the_build_context() -> None:
    context = SimpleNamespace(member_extraction_token=_STATIC)

    assert ContentHashStage.from_dict({}, context).member_extraction_token == _STATIC


@pytest.mark.parametrize(
    "context",
    [object(), SimpleNamespace(app_config=None), BuildContext()],
    ids=["foreign-context", "no-token", "bare-build-context"],
)
def test_from_dict_without_a_token_folds_nothing(context: object) -> None:
    assert ContentHashStage.from_dict({}, context).member_extraction_token == ""


def test_build_ingestion_pipeline_hands_the_token_to_the_content_hash_stage() -> None:
    """The keyword travels factory → ``BuildContext`` → ``from_dict``; dropping it
    at any hop would silently disable the fold for every CLI deployment."""
    pipeline = build_ingestion_pipeline(
        AppConfig.load(),
        embedder=MockEmbedder(),
        uow_factory=make_fake_uow_factory(),
        member_extraction_token=_ONE_MEMBER_PER_MODULE,
    )

    stages = [s for s in pipeline.stages if isinstance(s, ContentHashStage)]
    assert [s.member_extraction_token for s in stages] == [_ONE_MEMBER_PER_MODULE]


def test_the_member_token_is_wiring_not_a_stage_tunable() -> None:
    """It comes from the CLI flags and the ``extraction.members`` block, not
    from this stage's YAML — and an unchanged ``to_dict`` keeps every pipeline
    YAML byte where it was."""
    stage = ContentHashStage(member_extraction_token=_STATIC)

    assert stage.to_dict() == {"type": "content_hash"}
