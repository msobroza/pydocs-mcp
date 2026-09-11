"""ContentHashStage folds MODULE_ID_RULE_VERSION into PROJECT hashes only.

WHY (spec 2026-09-10-member-module-ids-design §4): the project cache skip
runs before member extraction, so a member module-id rule fix alone never
reaches an existing index. Folding the rule token into the ``__project__``
package hash makes every stored project hash miss exactly once, with no
schema bump; no dependency hash may move BECAUSE OF the rule token, so this
fix re-extracts no dependency. (Every package, project and dependency alike,
also carries the unconditional loadable-grammar salt from the multilanguage
analyzers work — the pins below wrap it around the rule fold because that is
the documented order.) AC-13, plus the MODULE_ID_RULE_VERSION half of AC-9.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import pydocs_mcp
from pydocs_mcp import db
from pydocs_mcp.extraction.config import _EXCLUDED_DIRS
from pydocs_mcp.extraction.pipeline import IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle
from pydocs_mcp.extraction.pipeline.stages.content_hash import ContentHashStage
from pydocs_mcp.project_toml import (
    EMPTY_PROJECT_EXCLUDES,
    ProjectExcludes,
    exclusion_fingerprint,
)
from tests.extraction._content_hash_oracle import (
    digest_fold,
    grammar_folded,
    raw_hash_files,
    rule_folded,
)

_USER_EXCLUDES = ProjectExcludes(names=_EXCLUDED_DIRS | {"fixtures"}, anchored=frozenset())
_RULE_TOKEN_NAME = "MODULE_ID_RULE_VERSION"


def _state(tmp_path: Path, kind: TargetKind, excludes: ProjectExcludes | None) -> IngestionState:
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    bundle = FileBundle(
        target=tmp_path,
        target_kind=kind,
        paths=(str(f),),
        effective_excludes=excludes or EMPTY_PROJECT_EXCLUDES,
    )
    return IngestionState(files=bundle)


async def _stage_hash(state: IngestionState) -> str:
    out = await ContentHashStage().run(state)
    return out.files.content_hash


@pytest.mark.asyncio
async def test_project_hash_is_rule_folded_base(tmp_path: Path) -> None:
    state = _state(tmp_path, TargetKind.PROJECT, None)

    assert await _stage_hash(state) == grammar_folded(
        rule_folded(raw_hash_files(list(state.files.paths)))
    )


@pytest.mark.asyncio
async def test_project_fold_composes_after_exclusion_fold(tmp_path: Path) -> None:
    state = _state(tmp_path, TargetKind.PROJECT, _USER_EXCLUDES)
    fingerprint = exclusion_fingerprint(_USER_EXCLUDES, _EXCLUDED_DIRS)
    assert fingerprint is not None  # a real user exclude, so the fold applies

    base = raw_hash_files(list(state.files.paths))

    assert await _stage_hash(state) == grammar_folded(rule_folded(digest_fold(base, fingerprint)))


@pytest.mark.asyncio
async def test_dependency_hash_has_no_rule_fold(tmp_path: Path) -> None:
    """A dependency carries the grammar salt like every package, but never
    the PROJECT-only rule token."""
    plain = _state(tmp_path, TargetKind.DEPENDENCY, None)
    excluded = _state(tmp_path, TargetKind.DEPENDENCY, _USER_EXCLUDES)
    base = raw_hash_files(list(plain.files.paths))
    fingerprint = exclusion_fingerprint(_USER_EXCLUDES, _EXCLUDED_DIRS)
    assert fingerprint is not None

    assert await _stage_hash(plain) == grammar_folded(base)
    # Today's dependency behavior with a supplied set: exclusion fold only,
    # under the same unconditional grammar salt every package carries.
    assert await _stage_hash(excluded) == grammar_folded(digest_fold(base, fingerprint))


def test_schema_version_unchanged() -> None:
    # The member module-id fix itself claims no schema version: a bump lets an
    # older running process wipe the index (spec §4), so the fold had to reach
    # existing indexes through the project hash instead. v17 went to the
    # grammar stamp (issue #246 item 3, additive `index_metadata` column with
    # its own migration); the multi-branch P1 plan, which reserved v17 and is
    # still unexecuted, takes the next free version.
    assert db.SCHEMA_VERSION == 17


def _assigns_rule_token(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id == _RULE_TOKEN_NAME for t in targets):
            return True
    return False


def test_rule_version_single_definition() -> None:
    package_dir = Path(pydocs_mcp.__file__).parent
    defining = {
        path.relative_to(package_dir).as_posix()
        for path in package_dir.rglob("*.py")
        if _assigns_rule_token(ast.parse(path.read_text(encoding="utf-8")))
    }

    assert defining == {"extraction/strategies/python_module_id.py"}
