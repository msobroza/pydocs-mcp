"""The package hash composes THREE folds, and their order is the contract.

``ContentHashStage`` wraps ``hash_files(paths)`` in, innermost first: the
conditional exclusion fingerprint, the PROJECT-only ``MODULE_ID_RULE_VERSION``
token (member-module-ids spec §4), and the unconditional loadable-grammar salt
(analyzers spec §8.2). Each fold has its own scope and its own suite; this one
pins what only their COMPOSITION can get wrong — that neither independent fold
swallows the other, that the project-only one stays project-only, and that the
order is the documented one rather than any of the other five permutations.

The two salts are varied through their seams (``MODULE_ID_RULE_VERSION`` as
bound in the stage module, ``_grammar_fingerprint``) so the suite says nothing
about which grammar wheels happen to be installed.
"""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

import pytest

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ContentHashStage
from pydocs_mcp.extraction.pipeline.stages import content_hash as stage_module
from pydocs_mcp.project_toml import (
    EMPTY_PROJECT_EXCLUDES,
    ProjectExcludes,
    exclusion_fingerprint,
)
from tests.extraction._content_hash_oracle import raw_hash_files

_USER_EXCLUDES = ProjectExcludes(names=_EXCLUDED_DIRS | {"fixtures"}, anchored=frozenset())
_FAKE_GRAMMARS = ".rs,.ts"
_FAKE_RULE_TOKEN = "package-root/99"


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
        target=f.parent, target_kind=kind, paths=(str(f),), effective_excludes=excludes
    )
    return IngestionState(files=bundle)


def _fold(base: str, salt: str) -> str:
    """The fold formula, re-derived here so a stage regression can't move it."""
    return hashlib.md5(f"{base}\x00{salt}".encode(), usedforsecurity=False).hexdigest()[:16]


@pytest.fixture
def pinned_salts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both independent salts held at known values, so a hash moves only when
    the test moves one of them."""
    monkeypatch.setattr(stage_module, "MODULE_ID_RULE_VERSION", _FAKE_RULE_TOKEN)
    monkeypatch.setattr(stage_module, "_grammar_fingerprint", lambda: _FAKE_GRAMMARS)


async def _hash(state: IngestionState) -> str:
    return (await ContentHashStage().run(state)).files.content_hash


# ── (a) a project hash moves with EITHER independent fold ─────────────────


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
    """The grammar salt is the OUTERMOST fold; a naive implementation that
    computed it from the raw digest would swallow the rule token entirely."""
    baseline = await _hash(_state(one_file))

    monkeypatch.setattr(stage_module, "MODULE_ID_RULE_VERSION", "package-root/100")

    assert await _hash(_state(one_file)) != baseline


# ── (b) a dependency hash moves with the grammar salt ONLY ────────────────


@pytest.mark.asyncio
async def test_dependency_hash_moves_when_the_grammar_fingerprint_changes(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_salts: None
) -> None:
    baseline = await _hash(_state(one_file, TargetKind.DEPENDENCY))

    monkeypatch.setattr(stage_module, "_grammar_fingerprint", lambda: ".rs")

    assert await _hash(_state(one_file, TargetKind.DEPENDENCY)) != baseline


@pytest.mark.asyncio
async def test_dependency_hash_ignores_the_rule_token(
    one_file: Path, monkeypatch: pytest.MonkeyPatch, pinned_salts: None
) -> None:
    """Dependencies have no member module-id rule to heal, so the token must
    not cost them a re-extraction (member-module-ids spec §4)."""
    baseline = await _hash(_state(one_file, TargetKind.DEPENDENCY))

    monkeypatch.setattr(stage_module, "MODULE_ID_RULE_VERSION", "package-root/100")

    assert await _hash(_state(one_file, TargetKind.DEPENDENCY)) == baseline


# ── (c) the order ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fold_order_is_exclusion_then_rule_then_grammar(
    one_file: Path, pinned_salts: None
) -> None:
    state = _state(one_file, TargetKind.PROJECT, _USER_EXCLUDES)
    exclusion_salt = exclusion_fingerprint(_USER_EXCLUDES, _EXCLUDED_DIRS)
    assert exclusion_salt is not None  # a real user exclude, so all three fold

    base = raw_hash_files(list(state.files.paths))
    expected = _fold(
        _fold(_fold(base, exclusion_salt), _FAKE_RULE_TOKEN), f"grammars:{_FAKE_GRAMMARS}"
    )

    assert await _hash(state) == expected


@pytest.mark.asyncio
async def test_every_other_fold_permutation_is_a_different_hash(
    one_file: Path, pinned_salts: None
) -> None:
    """The order is not cosmetic: each of the other five orderings yields a
    hash the stage must not produce (so a refactor that reorders the folds
    fails here rather than silently invalidating every stored hash)."""
    state = _state(one_file, TargetKind.PROJECT, _USER_EXCLUDES)
    exclusion_salt = exclusion_fingerprint(_USER_EXCLUDES, _EXCLUDED_DIRS)
    assert exclusion_salt is not None

    base = raw_hash_files(list(state.files.paths))
    salts = (exclusion_salt, _FAKE_RULE_TOKEN, f"grammars:{_FAKE_GRAMMARS}")
    actual = await _hash(state)

    for order in itertools.permutations(salts):
        folded = base
        for salt in order:
            folded = _fold(folded, salt)
        if order == salts:
            assert folded == actual
        else:
            assert folded != actual, f"permutation {order} collides with the pinned order"
