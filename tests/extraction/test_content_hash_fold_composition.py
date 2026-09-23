"""The package hash composes EIGHT folds, and their order is the contract.

``ContentHashStage`` wraps ``hash_files(paths)`` in, innermost first: the
conditional exclusion fingerprint, the PROJECT-only ``MODULE_ID_RULE_VERSION``
token (member-module-ids spec §4), the conditional decision-capture token
(issue #263 — on a project, folded only when ``decision_capture`` digests to
something other than the pinned stock baseline, and the structuring LLM's
identity rides INSIDE it, issue #347, never as a fold of its own; on a
dependency, folded only while ``enabled`` and ``include_deps`` mine it, issue
#346), the
conditional DEPENDENCY-only member-extraction token (issue #347 — folded only
when the composition root's token is non-empty and differs from the pinned
stock token), the conditional reference-capture token on EVERY package (issue #347 —
folded only when ``reference_graph.capture`` normalizes to something other
than the pinned stock token), the unconditional loadable-grammar salt
(analyzers spec §8.2), the unconditional chunk-tree salt (issue #246
close-out), and the identity salt built from the ingestion pipeline hash plus
the package's embed tier (the ingestion-cache-gates fix). No single package
carries all eight: a project carries up to seven (never the member token), a
dependency up to seven too (never the rule token, and the decision token only
while ``include_deps`` mines it). Each fold has its own scope and its own
suite; this one pins what only their COMPOSITION can get wrong — that no fold
swallows another, that the project-only one stays project-only and the
dependency-only one dependency-only, that a stage built
WITHOUT a pipeline hash still produces the inner folds unchanged, that stock
settings drop exactly their own conditional fold, and that each target's order
is the documented one rather than any other permutation.

Every salt is varied through a seam (``MODULE_ID_RULE_VERSION`` as bound in
the stage module, ``_grammar_fingerprint``, ``_chunk_tree_fingerprint``, and
the stage's own ``pipeline_hash`` / ``embed_policy`` / ``decision_capture`` /
``member_extraction_token`` / ``reference_capture`` fields) so the suite says
nothing about which grammar wheels happen to be installed and needs no
embedder. The decision and refs tokens come from the oracle
(``decision_capture_token``, ``reference_capture_token``), never from the
stage; the member tokens are literals.
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
from pydocs_mcp.retrieval.config import DecisionCaptureConfig, LlmConfig, ReferenceCaptureConfig
from tests.extraction._content_hash_oracle import (
    decision_capture_token,
    raw_hash_files,
    reference_capture_token,
)

_USER_EXCLUDES = ProjectExcludes(names=_EXCLUDED_DIRS | {"fixtures"}, anchored=frozenset())
_FAKE_GRAMMARS = ".rs,.ts"
_FAKE_CHUNK_RULES = "chunk-trees/9|feedfacecafe0000"
_FAKE_RULE_TOKEN = "package-root/99"
_FAKE_PIPELINE_HASH = "PIPE-1"
_DEPENDENCY_NAME = "somedep"
_STOCK_DECISIONS = DecisionCaptureConfig()
_TUNED_DECISIONS = DecisionCaptureConfig(merge_jaccard=0.5)
_STRUCTURING_DECISIONS = DecisionCaptureConfig.model_validate(
    {"llm_structuring": {"enabled": True}}
)
# Mines dependencies (issue #346), so a dependency folds the decision token too.
_DEPENDENCY_MINING_DECISIONS = DecisionCaptureConfig(include_deps=True)
_STOCK_LLM = LlmConfig()
_TUNED_LLM = LlmConfig(model_name="model-b", temperature=0.3)
_STOCK_REFS = ReferenceCaptureConfig()
_TUNED_REFS = ReferenceCaptureConfig(kinds=("calls", "imports", "inherits", "mentions"))
_STOCK_MEMBERS = "inspect|depth=1|cap=120|sig=200|doc=1024"
_TUNED_MEMBERS = "inspect|depth=2|cap=1|sig=200|doc=1024"
# ``EmbedPolicy()``'s tier for a dependency — the identity salt's second half.
_DEPENDENCY_TIER = "doc_pages"


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
    refs: ReferenceCaptureConfig = _STOCK_REFS,
    members: str = _STOCK_MEMBERS,
    llm: LlmConfig = _STOCK_LLM,
) -> str:
    stage = ContentHashStage(
        pipeline_hash=pipeline_hash,
        decision_capture=decisions,
        member_extraction_token=members,
        reference_capture=refs,
        llm=llm,
    )
    return (await stage.run(state)).files.content_hash


def _fold_chain(base: str, salts: tuple[str, ...]) -> str:
    for salt in salts:
        base = _fold(base, salt)
    return base


def _all_seven_salts(exclusion_salt: str) -> tuple[str, ...]:
    """Every fold, in the documented order, with both conditional settings tuned."""
    return (
        exclusion_salt,
        _FAKE_RULE_TOKEN,
        decision_capture_token(_TUNED_DECISIONS),
        reference_capture_token(_TUNED_REFS),
        f"grammars:{_FAKE_GRAMMARS}",
        f"chunks:{_FAKE_CHUNK_RULES}",
        _identity_salt(),
    )


def _all_six_dependency_salts(exclusion_salt: str) -> tuple[str, ...]:
    """Every fold a dependency carries while ``include_deps`` is off, in the
    documented order, with both of its conditional settings tuned — never the
    rule or the decision token."""
    return (
        exclusion_salt,
        f"members:{_TUNED_MEMBERS}",
        reference_capture_token(_TUNED_REFS),
        f"grammars:{_FAKE_GRAMMARS}",
        f"chunks:{_FAKE_CHUNK_RULES}",
        _identity_salt(tier=_DEPENDENCY_TIER),
    )


def _all_seven_dependency_salts(exclusion_salt: str) -> tuple[str, ...]:
    """Every fold a dependency can carry, in the documented order: the six above
    plus the decision token ``include_deps`` adds right after the exclusion
    fingerprint (issue #346) — still never the rule token."""
    six = _all_six_dependency_salts(exclusion_salt)
    return (six[0], decision_capture_token(_DEPENDENCY_MINING_DECISIONS), *six[1:])


def _user_excluded_project(one_file: Path) -> tuple[IngestionState, str]:
    return _user_excluded(one_file, TargetKind.PROJECT)


def _user_excluded(one_file: Path, kind: TargetKind) -> tuple[IngestionState, str]:
    state = _state(one_file, kind, _USER_EXCLUDES)
    exclusion_salt = exclusion_fingerprint(_USER_EXCLUDES, _EXCLUDED_DIRS)
    assert exclusion_salt is not None  # a real user exclude, so that fold applies
    return state, exclusion_salt


# ── (a) a project hash moves with ANY of the six non-exclusion folds ──────


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


@pytest.mark.asyncio
async def test_project_hash_moves_when_the_reference_capture_config_changes(
    one_file: Path, pinned_salts: None
) -> None:
    """Stock → tuned folds the token in; tuned → differently tuned moves it —
    under every other salt, so none of them swallows it (issue #347)."""
    stock = await _hash(_state(one_file))
    tuned = await _hash(_state(one_file), refs=_TUNED_REFS)
    disabled = await _hash(_state(one_file), refs=ReferenceCaptureConfig(enabled=False))

    assert len({stock, tuned, disabled}) == 3


@pytest.mark.asyncio
async def test_the_refs_fold_does_not_swallow_the_decision_token(
    one_file: Path, pinned_salts: None
) -> None:
    """The refs token wraps the decision token directly; computing it from an
    earlier digest would make a decision retune invisible to a tuned capture."""
    baseline = await _hash(_state(one_file), decisions=_TUNED_DECISIONS, refs=_TUNED_REFS)

    retuned = DecisionCaptureConfig(merge_jaccard=0.6)

    assert await _hash(_state(one_file), decisions=retuned, refs=_TUNED_REFS) != baseline


@pytest.mark.asyncio
async def test_project_hash_ignores_the_member_extraction_token(
    one_file: Path, pinned_salts: None
) -> None:
    """Project members always come from the AST extractor, whatever the mode,
    depth or caps, so the token must not cost a project a re-extraction —
    byte-equal even with every other project fold in play (issue #347)."""
    state, _exclusion_salt = _user_excluded_project(one_file)
    tuned = {"decisions": _TUNED_DECISIONS, "refs": _TUNED_REFS}
    baseline = await _hash(state, **tuned)

    assert await _hash(state, members="static", **tuned) == baseline
    assert await _hash(state, members=_TUNED_MEMBERS, **tuned) == baseline


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
async def test_dependency_hash_moves_when_the_reference_capture_config_changes(
    one_file: Path, pinned_salts: None
) -> None:
    """Capture does not gate on target kind, so unlike the decision token the
    refs token is NOT project-only (issue #347)."""
    baseline = await _hash(_state(one_file, TargetKind.DEPENDENCY))

    tuned = await _hash(_state(one_file, TargetKind.DEPENDENCY), refs=_TUNED_REFS)

    assert tuned != baseline


@pytest.mark.asyncio
async def test_dependency_hash_moves_when_the_member_extraction_token_changes(
    one_file: Path, pinned_salts: None
) -> None:
    """Stock → static folds the token in; static → tuned inspect moves it —
    under every other salt, so none of them swallows it (issue #347)."""
    state = _state(one_file, TargetKind.DEPENDENCY)
    stock = await _hash(state)
    static = await _hash(state, members="static")
    tuned = await _hash(state, members=_TUNED_MEMBERS)

    assert len({stock, static, tuned}) == 3


@pytest.mark.asyncio
async def test_the_refs_fold_does_not_swallow_the_member_token(
    one_file: Path, pinned_salts: None
) -> None:
    """The refs token wraps the member token directly; computing it from an
    earlier digest would make a member retune invisible to a tuned capture."""
    state = _state(one_file, TargetKind.DEPENDENCY)
    baseline = await _hash(state, members=_TUNED_MEMBERS, refs=_TUNED_REFS)

    assert await _hash(state, members="static", refs=_TUNED_REFS) != baseline


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
    """Without ``include_deps``, ``CaptureDecisionsPipeline.run`` never mines a
    dependency, so a tuned ``decision_capture`` must not cost one a
    re-extraction (issue #263)."""
    baseline = await _hash(_state(one_file, TargetKind.DEPENDENCY))

    tuned = await _hash(_state(one_file, TargetKind.DEPENDENCY), decisions=_TUNED_DECISIONS)

    assert tuned == baseline


@pytest.mark.asyncio
async def test_dependency_hash_moves_when_include_deps_mines_it(
    one_file: Path, pinned_salts: None
) -> None:
    """Stock → mining folds the token in; mining → differently tuned mining
    moves it — under every other salt, so none of them swallows it (#346)."""
    state = _state(one_file, TargetKind.DEPENDENCY)
    stock = await _hash(state)
    mined = await _hash(state, decisions=_DEPENDENCY_MINING_DECISIONS)
    retuned = await _hash(
        state, decisions=DecisionCaptureConfig(include_deps=True, merge_jaccard=0.6)
    )

    assert len({stock, mined, retuned}) == 3


@pytest.mark.asyncio
async def test_the_member_fold_does_not_swallow_the_dependency_decision_token(
    one_file: Path, pinned_salts: None
) -> None:
    """The member token wraps the dependency's decision token directly; computing
    it from the raw digest would make a decision retune invisible to a
    dependency indexed with tuned members."""
    state = _state(one_file, TargetKind.DEPENDENCY)
    baseline = await _hash(state, decisions=_DEPENDENCY_MINING_DECISIONS, members=_TUNED_MEMBERS)

    retuned = DecisionCaptureConfig(include_deps=True, merge_jaccard=0.6)

    assert await _hash(state, decisions=retuned, members=_TUNED_MEMBERS) != baseline


# ── (c) the order ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fold_order_is_exclusion_rule_decisions_refs_grammar_chunks_then_identity(
    one_file: Path, pinned_salts: None
) -> None:
    state, exclusion_salt = _user_excluded_project(one_file)
    base = raw_hash_files(list(state.files.paths))

    expected = _fold(
        _fold(
            _fold(
                _fold(
                    _fold(
                        _fold(_fold(base, exclusion_salt), _FAKE_RULE_TOKEN),
                        decision_capture_token(_TUNED_DECISIONS),
                    ),
                    reference_capture_token(_TUNED_REFS),
                ),
                f"grammars:{_FAKE_GRAMMARS}",
            ),
            f"chunks:{_FAKE_CHUNK_RULES}",
        ),
        _identity_salt(),
    )

    assert await _hash(state, decisions=_TUNED_DECISIONS, refs=_TUNED_REFS) == expected


@pytest.mark.asyncio
async def test_a_stock_decision_config_drops_exactly_that_fold(
    one_file: Path, pinned_salts: None
) -> None:
    """The decision fold is CONDITIONAL: under its shipped defaults it drops out
    and every other fold stays where it was (issue #263)."""
    state, exclusion_salt = _user_excluded_project(one_file)
    base = raw_hash_files(list(state.files.paths))
    salts = tuple(
        salt
        for salt in _all_seven_salts(exclusion_salt)
        if salt != decision_capture_token(_TUNED_DECISIONS)
    )

    assert await _hash(state, refs=_TUNED_REFS) == _fold_chain(base, salts)


@pytest.mark.asyncio
async def test_the_llm_identity_rides_inside_the_decision_token_not_a_fold_of_its_own(
    one_file: Path, pinned_salts: None
) -> None:
    """Structuring swaps the decision token for its LLM-aware form in place
    (issue #347): still seven folds at most, every other fold where it was."""
    state, exclusion_salt = _user_excluded_project(one_file)
    base = raw_hash_files(list(state.files.paths))
    plain_decision_token = decision_capture_token(_TUNED_DECISIONS)
    llm_aware_token = decision_capture_token(_STRUCTURING_DECISIONS, _TUNED_LLM)
    salts = tuple(
        llm_aware_token if salt == plain_decision_token else salt
        for salt in _all_seven_salts(exclusion_salt)
    )
    tuned = {"decisions": _STRUCTURING_DECISIONS, "refs": _TUNED_REFS, "llm": _TUNED_LLM}

    assert await _hash(state, **tuned) == _fold_chain(base, salts)


@pytest.mark.asyncio
async def test_a_stock_reference_capture_config_drops_exactly_that_fold(
    one_file: Path, pinned_salts: None
) -> None:
    """The refs fold is CONDITIONAL too: under the shipped capture settings it
    drops out and every other fold stays where it was (issue #347)."""
    state, exclusion_salt = _user_excluded_project(one_file)
    base = raw_hash_files(list(state.files.paths))
    salts = tuple(
        salt
        for salt in _all_seven_salts(exclusion_salt)
        if salt != reference_capture_token(_TUNED_REFS)
    )

    assert await _hash(state, decisions=_TUNED_DECISIONS) == _fold_chain(base, salts)


@pytest.mark.asyncio
async def test_stock_settings_keep_the_five_fold_framing_every_stored_hash_has(
    one_file: Path, pinned_salts: None
) -> None:
    """Both conditional folds drop out together under the shipped defaults, so
    upgrading costs a stock deployment no re-extraction (issues #263, #347)."""
    state, exclusion_salt = _user_excluded_project(one_file)
    base = raw_hash_files(list(state.files.paths))
    five_folds = (
        exclusion_salt,
        _FAKE_RULE_TOKEN,
        f"grammars:{_FAKE_GRAMMARS}",
        f"chunks:{_FAKE_CHUNK_RULES}",
        _identity_salt(),
    )

    assert await _hash(state) == _fold_chain(base, five_folds)


@pytest.mark.asyncio
async def test_every_other_fold_permutation_is_a_different_hash(
    one_file: Path, pinned_salts: None
) -> None:
    """The order is not cosmetic: each of the other 5039 orderings yields a hash
    the stage must not produce (so a refactor that reorders the folds fails here
    rather than silently invalidating every stored hash). 5040 seven-fold md5
    chains — a few hundredths of a second."""
    state, exclusion_salt = _user_excluded_project(one_file)
    base = raw_hash_files(list(state.files.paths))
    salts = _all_seven_salts(exclusion_salt)
    actual = await _hash(state, decisions=_TUNED_DECISIONS, refs=_TUNED_REFS)

    for order in itertools.permutations(salts):
        folded = _fold_chain(base, order)
        if order == salts:
            assert folded == actual
        else:
            assert folded != actual, f"permutation {order} collides with the pinned order"


@pytest.mark.asyncio
async def test_dependency_fold_order_is_exclusion_members_refs_grammar_chunks_then_identity(
    one_file: Path, pinned_salts: None
) -> None:
    state, exclusion_salt = _user_excluded(one_file, TargetKind.DEPENDENCY)
    base = raw_hash_files(list(state.files.paths))

    expected = _fold(
        _fold(
            _fold(
                _fold(
                    _fold(_fold(base, exclusion_salt), f"members:{_TUNED_MEMBERS}"),
                    reference_capture_token(_TUNED_REFS),
                ),
                f"grammars:{_FAKE_GRAMMARS}",
            ),
            f"chunks:{_FAKE_CHUNK_RULES}",
        ),
        _identity_salt(tier=_DEPENDENCY_TIER),
    )

    assert await _hash(state, members=_TUNED_MEMBERS, refs=_TUNED_REFS) == expected


@pytest.mark.asyncio
async def test_dependency_fold_order_with_a_mined_decision_token(
    one_file: Path, pinned_salts: None
) -> None:
    """``include_deps`` adds the decision token at the SAME position the project
    folds it — after the exclusion fingerprint, before every later fold — so the
    other six stay where they were (issue #346)."""
    state, exclusion_salt = _user_excluded(one_file, TargetKind.DEPENDENCY)
    base = raw_hash_files(list(state.files.paths))

    expected = _fold(
        _fold(
            _fold(
                _fold(
                    _fold(
                        _fold(
                            _fold(base, exclusion_salt),
                            decision_capture_token(_DEPENDENCY_MINING_DECISIONS),
                        ),
                        f"members:{_TUNED_MEMBERS}",
                    ),
                    reference_capture_token(_TUNED_REFS),
                ),
                f"grammars:{_FAKE_GRAMMARS}",
            ),
            f"chunks:{_FAKE_CHUNK_RULES}",
        ),
        _identity_salt(tier=_DEPENDENCY_TIER),
    )

    mined = {"decisions": _DEPENDENCY_MINING_DECISIONS, "members": _TUNED_MEMBERS}
    assert await _hash(state, refs=_TUNED_REFS, **mined) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("stock_token", [_STOCK_MEMBERS, ""], ids=["stock", "absent"])
async def test_a_stock_or_absent_member_token_drops_exactly_that_fold(
    one_file: Path, pinned_salts: None, stock_token: str
) -> None:
    """The member fold is CONDITIONAL: the shipped inspect defaults and a root
    that supplies no token both drop it, and every other dependency fold stays
    where it was (issue #347)."""
    state, exclusion_salt = _user_excluded(one_file, TargetKind.DEPENDENCY)
    base = raw_hash_files(list(state.files.paths))
    salts = tuple(
        salt
        for salt in _all_six_dependency_salts(exclusion_salt)
        if salt != f"members:{_TUNED_MEMBERS}"
    )

    assert await _hash(state, members=stock_token, refs=_TUNED_REFS) == _fold_chain(base, salts)


@pytest.mark.asyncio
async def test_every_other_dependency_fold_permutation_is_a_different_hash(
    one_file: Path, pinned_salts: None
) -> None:
    """The dependency order is not cosmetic either: each of the other 5039
    orderings of its seven folds — the decision token included, as
    ``include_deps`` adds it (issue #346) — yields a hash the stage must not
    produce."""
    state, exclusion_salt = _user_excluded(one_file, TargetKind.DEPENDENCY)
    base = raw_hash_files(list(state.files.paths))
    salts = _all_seven_dependency_salts(exclusion_salt)
    mined = {"decisions": _DEPENDENCY_MINING_DECISIONS, "members": _TUNED_MEMBERS}
    actual = await _hash(state, refs=_TUNED_REFS, **mined)

    for order in itertools.permutations(salts):
        folded = _fold_chain(base, order)
        if order == salts:
            assert folded == actual
        else:
            assert folded != actual, f"permutation {order} collides with the pinned order"


# ── (d) no pipeline hash → the first six folds, unchanged ─────────────────


@pytest.mark.asyncio
async def test_without_an_identity_salt_the_first_six_folds_are_unchanged(
    one_file: Path, pinned_salts: None
) -> None:
    """A bare ``ContentHashStage()`` (stage-isolation callers, legacy decoders)
    has no pipeline hash, which is the documented no-fold path. It must produce
    exactly the pre-identity-salt framing — that is what keeps every suite
    pinning a hash without the identity salt valid."""
    state, exclusion_salt = _user_excluded_project(one_file)
    base = raw_hash_files(list(state.files.paths))
    six_folds = _fold_chain(base, _all_seven_salts(exclusion_salt)[:-1])

    tuned = {"decisions": _TUNED_DECISIONS, "refs": _TUNED_REFS}
    assert await _hash(state, pipeline_hash="", **tuned) == six_folds
    # …and the identity salt is exactly what separates the two.
    assert await _hash(state, **tuned) == _fold(six_folds, _identity_salt())
