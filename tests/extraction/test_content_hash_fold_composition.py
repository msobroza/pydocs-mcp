"""The package hash composes SIX folds, and their order is the contract.

``ContentHashStage`` wraps ``hash_files(paths)`` in, innermost first: the
conditional exclusion fingerprint, the PROJECT-only ``MODULE_ID_RULE_VERSION``
token (member-module-ids spec §4), the conditional PROJECT-only
decision-capture token (issue #263 — folded only when ``decision_capture``
digests to something other than the pinned stock baseline), the unconditional
loadable-grammar salt
(analyzers spec §8.2), the unconditional chunk-tree salt (issue #246
close-out), and the identity salt built from the ingestion pipeline hash plus
the package's embed tier (the ingestion-cache-gates fix). Each fold has its own
scope and its own suite; this one pins what only their COMPOSITION can get
wrong — that no fold swallows another, that the project-only ones stay
project-only, that a stage built WITHOUT a pipeline hash still produces the
first five folds unchanged, and that the order is the documented one rather
than any of the other seven-hundred-and-nineteen permutations.

Every salt is varied through a seam (``MODULE_ID_RULE_VERSION`` as bound in
the stage module, ``_grammar_fingerprint``, ``_chunk_tree_fingerprint``, and
the stage's own ``pipeline_hash`` / ``embed_policy`` / ``decision_capture``
fields) so the suite says nothing about which grammar wheels happen to be
installed and needs no embedder. The decision token comes from the oracle's
``decision_capture_token`` (the documented ``model_dump_json`` recipe), never
from the stage.
"""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

import pytest

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS
from pydocs_mcp.extraction.embed_policy import EmbedPolicy
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ContentHashStage
from pydocs_mcp.extraction.pipeline.stages import content_hash as stage_module
from pydocs_mcp.project_toml import (
    EMPTY_PROJECT_EXCLUDES,
    ProjectExcludes,
    exclusion_fingerprint,
)
from pydocs_mcp.retrieval.config import DecisionCaptureConfig
from tests.extraction._content_hash_oracle import decision_capture_token, raw_hash_files

_USER_EXCLUDES = ProjectExcludes(names=_EXCLUDED_DIRS | {"fixtures"}, anchored=frozenset())
_FAKE_GRAMMARS = ".rs,.ts"
_FAKE_CHUNK_RULES = "chunk-trees/9|feedfacecafe0000"
_FAKE_RULE_TOKEN = "package-root/99"
_FAKE_PIPELINE_HASH = "PIPE-1"
_DEPENDENCY_NAME = "somedep"
_STOCK_DECISIONS = DecisionCaptureConfig()
_TUNED_DECISIONS = DecisionCaptureConfig(merge_jaccard=0.5)


@pytest.fixture
def one_file(tmp_path: Path) -> Path:
    """Written ONCE per test: ``hash_files`` folds each path's mtime, so a
    rewrite between two runs would move the base digest and make every
    "the hash changed" assertion below pass for the wrong reason."""
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
        package_name="__project__" if kind is TargetKind.PROJECT else _DEPENDENCY_NAME,
        paths=(str(f),),
        effective_excludes=excludes,
    )
    return IngestionState(files=bundle)


def _fold(base: str, salt: str) -> str:
    """The fold formula, re-derived here so a stage regression can't move it."""
    return hashlib.md5(f"{base}\x00{salt}".encode(), usedforsecurity=False).hexdigest()[:16]


def _identity_salt(pipeline_hash: str = _FAKE_PIPELINE_HASH, tier: str = "full") -> str:
    return f"pipeline:{pipeline_hash}|tier:{tier}"


@pytest.fixture
def pinned_salts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both module-level salts held at known values, so a hash moves only when
    the test moves one of them. The identity salt needs no patching — it is
    constructor state on the stage itself."""
    monkeypatch.setattr(stage_module, "MODULE_ID_RULE_VERSION", _FAKE_RULE_TOKEN)
    monkeypatch.setattr(stage_module, "_grammar_fingerprint", lambda: _FAKE_GRAMMARS)
    monkeypatch.setattr(stage_module, "_chunk_tree_fingerprint", lambda _cfg: _FAKE_CHUNK_RULES)


async def _hash(
    state: IngestionState,
    pipeline_hash: str = _FAKE_PIPELINE_HASH,
    decisions: DecisionCaptureConfig = _STOCK_DECISIONS,
) -> str:
    stage = ContentHashStage(pipeline_hash=pipeline_hash, decision_capture=decisions)
    return (await stage.run(state)).files.content_hash


# ── (a) a project hash moves with ANY of the five non-exclusion folds ─────


@pytest.mark.asyncio
async def test_project_hash_moves_when_the_grammar_fingerprint_changes(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_salts: None
) -> None:
    baseline = await _hash(_state(one_file))

    monkeypatch.setattr(stage_module, "_grammar_fingerprint", lambda: ".rs")

    assert await _hash(_state(one_file)) != baseline


@pytest.mark.asyncio
async def test_project_hash_moves_when_the_rule_token_changes(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_salts: None
) -> None:
    """The rule token is the INNERMOST of the three unconditional-ish folds; an
    implementation that computed either outer salt from the raw digest would
    swallow the token entirely."""
    baseline = await _hash(_state(one_file))

    monkeypatch.setattr(stage_module, "MODULE_ID_RULE_VERSION", "package-root/100")

    assert await _hash(_state(one_file)) != baseline


@pytest.mark.asyncio
async def test_project_hash_moves_when_the_chunk_tree_salt_changes(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_salts: None
) -> None:
    """Sits between the grammar salt and the identity salt; either neighbour
    computing from the wrong base would swallow it."""
    baseline = await _hash(_state(one_file))

    monkeypatch.setattr(stage_module, "_chunk_tree_fingerprint", lambda _cfg: "chunk-trees/10|0")

    assert await _hash(_state(one_file)) != baseline


@pytest.mark.asyncio
async def test_project_hash_moves_when_the_identity_salt_changes(
    one_file: Path, pinned_salts: None
) -> None:
    """Without this the package-level cache gate cannot see a pipeline change,
    and the pass re-embeds then discards the result forever."""
    baseline = await _hash(_state(one_file))

    assert await _hash(_state(one_file), pipeline_hash="PIPE-2") != baseline


@pytest.mark.asyncio
async def test_project_hash_moves_when_the_decision_capture_config_changes(
    one_file: Path, pinned_salts: None
) -> None:
    """Stock → tuned folds the token in; tuned → differently tuned moves it —
    under every other salt, so none of them swallows it (issue #263)."""
    stock = await _hash(_state(one_file))
    tuned = await _hash(_state(one_file), decisions=_TUNED_DECISIONS)
    retuned = await _hash(_state(one_file), decisions=DecisionCaptureConfig(merge_jaccard=0.6))

    assert len({stock, tuned, retuned}) == 3


@pytest.mark.asyncio
async def test_the_decision_fold_does_not_swallow_the_rule_token(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_salts: None
) -> None:
    """The decision token wraps the rule token directly; computing it from the
    raw digest instead would make a rule bump invisible to a tuned project."""
    baseline = await _hash(_state(one_file), decisions=_TUNED_DECISIONS)

    monkeypatch.setattr(stage_module, "MODULE_ID_RULE_VERSION", "package-root/100")

    assert await _hash(_state(one_file), decisions=_TUNED_DECISIONS) != baseline


# ── (b) a dependency hash: grammar + chunks + identity yes, rule token never ─


@pytest.mark.asyncio
async def test_dependency_hash_moves_when_the_grammar_fingerprint_changes(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_salts: None
) -> None:
    baseline = await _hash(_state(one_file, TargetKind.DEPENDENCY))

    monkeypatch.setattr(stage_module, "_grammar_fingerprint", lambda: ".rs")

    assert await _hash(_state(one_file, TargetKind.DEPENDENCY)) != baseline


@pytest.mark.asyncio
async def test_dependency_hash_moves_when_the_chunk_tree_salt_changes(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_salts: None
) -> None:
    """Dependencies run through the same chunkers, and code extensions reach the
    dependency scope through YAML (ADR 0022) — so unlike the rule token, this
    salt is NOT project-only."""
    baseline = await _hash(_state(one_file, TargetKind.DEPENDENCY))

    monkeypatch.setattr(stage_module, "_chunk_tree_fingerprint", lambda _cfg: "chunk-trees/10|0")

    assert await _hash(_state(one_file, TargetKind.DEPENDENCY)) != baseline


@pytest.mark.asyncio
async def test_dependency_hash_moves_when_the_identity_salt_changes(
    one_file: Path, pinned_salts: None
) -> None:
    """Both halves of the identity salt reach a dependency: the pipeline hash
    (shared) and its own embed tier (promoting it must miss the package gate)."""
    baseline = await _hash(_state(one_file, TargetKind.DEPENDENCY))

    assert await _hash(_state(one_file, TargetKind.DEPENDENCY), pipeline_hash="PIPE-2") != baseline

    promoted = ContentHashStage(
        pipeline_hash=_FAKE_PIPELINE_HASH,
        embed_policy=EmbedPolicy(full_index_dependencies=(_DEPENDENCY_NAME,)),
    )
    promoted_hash = (await promoted.run(_state(one_file, TargetKind.DEPENDENCY))).files.content_hash
    assert promoted_hash != baseline


@pytest.mark.asyncio
async def test_dependency_hash_ignores_the_rule_token(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_salts: None
) -> None:
    """Dependencies have no member module-id rule to heal, so the token must
    not cost them a re-extraction (member-module-ids spec §4) — byte-equal
    across a rule-token change even with the two outer salts in play."""
    baseline = await _hash(_state(one_file, TargetKind.DEPENDENCY))

    monkeypatch.setattr(stage_module, "MODULE_ID_RULE_VERSION", "package-root/100")

    assert await _hash(_state(one_file, TargetKind.DEPENDENCY)) == baseline


@pytest.mark.asyncio
async def test_dependency_hash_ignores_the_decision_capture_config(
    one_file: Path, pinned_salts: None
) -> None:
    """``CaptureDecisionsPipeline.run`` never mines a dependency, so a tuned
    ``decision_capture`` must not cost one a re-extraction (issue #263)."""
    baseline = await _hash(_state(one_file, TargetKind.DEPENDENCY))

    tuned = await _hash(_state(one_file, TargetKind.DEPENDENCY), decisions=_TUNED_DECISIONS)

    assert tuned == baseline


# ── (c) the order ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fold_order_is_exclusion_rule_decisions_grammar_chunks_then_identity(
    one_file: Path, pinned_salts: None
) -> None:
    state = _state(one_file, TargetKind.PROJECT, _USER_EXCLUDES)
    exclusion_salt = exclusion_fingerprint(_USER_EXCLUDES, _EXCLUDED_DIRS)
    assert exclusion_salt is not None  # a real user exclude, so all six fold

    base = raw_hash_files(list(state.files.paths))
    expected = _fold(
        _fold(
            _fold(
                _fold(
                    _fold(_fold(base, exclusion_salt), _FAKE_RULE_TOKEN),
                    decision_capture_token(_TUNED_DECISIONS),
                ),
                f"grammars:{_FAKE_GRAMMARS}",
            ),
            f"chunks:{_FAKE_CHUNK_RULES}",
        ),
        _identity_salt(),
    )

    assert await _hash(state, decisions=_TUNED_DECISIONS) == expected


@pytest.mark.asyncio
async def test_a_stock_decision_config_drops_exactly_that_fold(
    one_file: Path, pinned_salts: None
) -> None:
    """The decision fold is CONDITIONAL: under the shipped defaults the framing
    is the five-fold one every stored hash was written with, so upgrading costs
    a stock deployment no re-extraction (issue #263)."""
    state = _state(one_file, TargetKind.PROJECT, _USER_EXCLUDES)
    exclusion_salt = exclusion_fingerprint(_USER_EXCLUDES, _EXCLUDED_DIRS)
    assert exclusion_salt is not None

    base = raw_hash_files(list(state.files.paths))
    expected = _fold(
        _fold(
            _fold(
                _fold(_fold(base, exclusion_salt), _FAKE_RULE_TOKEN),
                f"grammars:{_FAKE_GRAMMARS}",
            ),
            f"chunks:{_FAKE_CHUNK_RULES}",
        ),
        _identity_salt(),
    )

    assert await _hash(state, decisions=_STOCK_DECISIONS) == expected


@pytest.mark.asyncio
async def test_every_other_fold_permutation_is_a_different_hash(
    one_file: Path, pinned_salts: None
) -> None:
    """The order is not cosmetic: each of the other seven-hundred-and-nineteen
    orderings yields a hash the stage must not produce (so a refactor that
    reorders the folds fails here rather than silently invalidating every
    stored hash). 720 six-fold md5 chains — milliseconds."""
    state = _state(one_file, TargetKind.PROJECT, _USER_EXCLUDES)
    exclusion_salt = exclusion_fingerprint(_USER_EXCLUDES, _EXCLUDED_DIRS)
    assert exclusion_salt is not None

    base = raw_hash_files(list(state.files.paths))
    salts = (
        exclusion_salt,
        _FAKE_RULE_TOKEN,
        decision_capture_token(_TUNED_DECISIONS),
        f"grammars:{_FAKE_GRAMMARS}",
        f"chunks:{_FAKE_CHUNK_RULES}",
        _identity_salt(),
    )
    actual = await _hash(state, decisions=_TUNED_DECISIONS)

    for order in itertools.permutations(salts):
        folded = base
        for salt in order:
            folded = _fold(folded, salt)
        if order == salts:
            assert folded == actual
        else:
            assert folded != actual, f"permutation {order} collides with the pinned order"


# ── (d) no pipeline hash → the first five folds, unchanged ────────────────


@pytest.mark.asyncio
async def test_without_an_identity_salt_the_first_five_folds_are_unchanged(
    one_file: Path, pinned_salts: None
) -> None:
    """A bare ``ContentHashStage()`` (stage-isolation callers, legacy decoders)
    has no pipeline hash, which is the documented no-fold path. It must produce
    exactly the pre-identity-salt framing — that is what keeps every suite
    pinning a hash without the identity salt valid."""
    state = _state(one_file, TargetKind.PROJECT, _USER_EXCLUDES)
    exclusion_salt = exclusion_fingerprint(_USER_EXCLUDES, _EXCLUDED_DIRS)
    assert exclusion_salt is not None

    base = raw_hash_files(list(state.files.paths))
    five_folds = _fold(
        _fold(
            _fold(
                _fold(_fold(base, exclusion_salt), _FAKE_RULE_TOKEN),
                decision_capture_token(_TUNED_DECISIONS),
            ),
            f"grammars:{_FAKE_GRAMMARS}",
        ),
        f"chunks:{_FAKE_CHUNK_RULES}",
    )

    assert await _hash(state, pipeline_hash="", decisions=_TUNED_DECISIONS) == five_folds
    # …and the identity salt is exactly what separates the two.
    assert await _hash(state, decisions=_TUNED_DECISIONS) == _fold(five_folds, _identity_salt())
