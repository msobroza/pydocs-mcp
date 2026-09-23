"""The structuring LLM's identity rides inside the PROJECT decision token (#347).

With ``decision_capture.llm_structuring.enabled``, the LLM configured under
``llm:`` structures every mined decision, and the grounded fields it returns are
persisted on the project's decision records. But the decision token (#263)
digests ``decision_capture`` alone, so switching ``llm.model_name`` reached no
cache key: every pass paid the new model to structure the decisions, then
discarded its answer as a package cache hit — forever, healed only by
``index --force``.

Four properties shape the LLM part, and each has tests here:

- INSIDE THE DECISION TOKEN: ``|llm:<digest>`` is appended to the
  ``decisions:`` token rather than folded on its own, so no fold moves position
  (tests/extraction/test_content_hash_fold_composition.py pins that).
- ALLOWLISTED: provider, model name, temperature and max tokens — never
  ``api_key``. A rotated key changes nothing structuring emits, and a secret has
  no business inside a stored hash.
- GATED exactly where structuring runs: ``decision_capture.enabled`` AND
  ``llm_structuring.enabled`` AND a project target. Anywhere else ``llm:`` is
  ignored entirely.
- STOCK FOLDS NOTHING: structuring is off by default, so a stock deployment's
  hashes stay byte-identical whatever ``llm:`` says.

Every expectation is derived from the independent oracle, never from the
stage's own helper.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ContentHashStage
from pydocs_mcp.retrieval.config import DecisionCaptureConfig, LlmConfig
from tests.extraction._content_hash_oracle import (
    chunk_tree_folded,
    decision_capture_folded,
    decision_capture_token,
    grammar_folded,
    pipeline_folded,
    raw_hash_files,
    rule_folded,
)

_PIPELINE_HASH = "PIPE-1"
_STRUCTURING_ON = DecisionCaptureConfig.model_validate({"llm_structuring": {"enabled": True}})
_MODEL_A = LlmConfig(model_name="model-a")
_MODEL_B = LlmConfig(model_name="model-b")

# ``md5("openai|gpt-4o-mini|0.0|None")[:16]`` — the LLM part a stock ``llm:``
# block carries, spelled out so a stage and oracle that drifted together (say,
# to a different field separator or float format) still fail: every
# structuring deployment would re-extract its project once for nothing.
_DEFAULT_LLM_DIGEST = "232b2b8b9d6530ff"

# Decision settings under which structuring does NOT run, each paired with
# whether the decision token itself folds (off the #263 stock pin).
_STRUCTURING_OFF: dict[str, dict[str, Any]] = {
    "stock": {},
    "tuned-without-structuring": {"merge_jaccard": 0.5},
    "structuring-on-but-capture-off": {"enabled": False, "llm_structuring": {"enabled": True}},
}


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


async def _hash(
    state: IngestionState,
    llm: LlmConfig,
    decisions: DecisionCaptureConfig = _STRUCTURING_ON,
) -> str:
    return await _run(ContentHashStage(decision_capture=decisions, llm=llm), state)


def _project_framing(f: Path, decisions: DecisionCaptureConfig, llm: LlmConfig | None) -> str:
    """The project hash with the decision token folded — the LLM part only when
    ``llm`` is given."""
    base = rule_folded(raw_hash_files([str(f)]))
    return chunk_tree_folded(grammar_folded(decision_capture_folded(base, decisions, llm)))


# ── inside the decision token, where structuring runs ─────────────────────


@pytest.mark.asyncio
async def test_structuring_appends_the_llm_identity_to_the_decision_token(
    one_file: Path,
) -> None:
    expected = _project_framing(one_file, _STRUCTURING_ON, _MODEL_A)

    assert await _hash(_state(one_file), _MODEL_A) == expected
    assert expected != _project_framing(one_file, _STRUCTURING_ON, None)


@pytest.mark.asyncio
async def test_the_llm_part_sits_inside_the_decision_token_under_every_other_salt(
    one_file: Path,
) -> None:
    """The LLM part changes the decision token's TEXT, never the fold order: the
    identity salt still wraps it from outside."""
    stage = ContentHashStage(
        pipeline_hash=_PIPELINE_HASH, decision_capture=_STRUCTURING_ON, llm=_MODEL_B
    )
    inner = decision_capture_folded(
        rule_folded(raw_hash_files([str(one_file)])), _STRUCTURING_ON, _MODEL_B
    )
    expected = pipeline_folded(chunk_tree_folded(grammar_folded(inner)), _PIPELINE_HASH)

    assert await _run(stage, _state(one_file)) == expected


def test_the_default_llm_config_digests_to_the_pinned_recipe() -> None:
    token = decision_capture_token(_STRUCTURING_ON, LlmConfig())

    assert token.endswith(f"|llm:{_DEFAULT_LLM_DIGEST}"), token


@pytest.mark.asyncio
async def test_switching_the_model_moves_the_project_hash(one_file: Path) -> None:
    state = _state(one_file)

    assert await _hash(state, _MODEL_A) != await _hash(state, _MODEL_B)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [{"model_name": "model-b"}, {"temperature": 0.7}, {"max_tokens": 512}],
    ids=["model_name", "temperature", "max_tokens"],
)
async def test_every_allowlisted_field_moves_the_project_hash(
    one_file: Path, changed: dict[str, Any]
) -> None:
    """Each shapes what the model answers, so each must re-extract. ``provider``
    is allowlisted too, but ``LlmConfig`` admits one value today."""
    state = _state(one_file)
    tuned = _MODEL_A.model_copy(update=changed)

    assert await _hash(state, tuned) != await _hash(state, _MODEL_A)


@pytest.mark.asyncio
async def test_the_api_key_alone_does_not_move_the_hash(one_file: Path) -> None:
    """Rotating a key changes nothing structuring emits, so it must re-extract
    nothing — and the secret must never reach a stored hash."""
    state = _state(one_file)
    rotated = [_MODEL_A.model_copy(update={"api_key": key}) for key in ("sk-one", "sk-two")]

    assert await _hash(state, rotated[0]) == await _hash(state, _MODEL_A)
    assert await _hash(state, rotated[1]) == await _hash(state, _MODEL_A)


@pytest.mark.asyncio
async def test_a_yaml_integer_temperature_hashes_like_its_float(one_file: Path) -> None:
    """``temperature: 0`` in YAML parses to the float ``0.0`` — the same
    setting, so the same hash."""
    state = _state(one_file)
    from_yaml = LlmConfig.model_validate({"model_name": "model-a", "temperature": 0})

    assert await _hash(state, from_yaml) == await _hash(state, _MODEL_A)


# ── gated: wherever structuring does not run, ``llm:`` is ignored ─────────


@pytest.mark.asyncio
@pytest.mark.parametrize("overlay", _STRUCTURING_OFF.values(), ids=_STRUCTURING_OFF)
async def test_without_structuring_the_llm_config_is_ignored(
    one_file: Path, overlay: dict[str, Any]
) -> None:
    """No structuring client is built without BOTH gates, so no ``llm:`` knob
    can change what the project extracts; a stock ``decision_capture`` still
    folds nothing at all, whatever ``llm:`` says."""
    decisions = DecisionCaptureConfig.model_validate(overlay)
    state = _state(one_file)
    without_llm_part = await _hash(state, LlmConfig(), decisions)

    assert await _hash(state, _MODEL_B, decisions) == without_llm_part
    if decisions == DecisionCaptureConfig():
        assert without_llm_part == await _run(ContentHashStage(), state)
    else:
        assert without_llm_part == _project_framing(one_file, decisions, None)


@pytest.mark.asyncio
async def test_a_dependency_never_carries_the_llm_part(one_file: Path) -> None:
    """Structuring runs inside the project-only decision capture, so a
    dependency's hash cannot depend on the LLM — folding it would bill every
    dependency a re-extraction for nothing."""
    state = _state(one_file, TargetKind.DEPENDENCY)
    stock_dependency = chunk_tree_folded(grammar_folded(raw_hash_files([str(one_file)])))

    assert await _hash(state, _MODEL_A) == stock_dependency
    assert await _hash(state, _MODEL_B) == stock_dependency


# ── the pass settles across processes ─────────────────────────────────────

_SUBPROCESS_HASH = """
import asyncio, sys
from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ContentHashStage
from pydocs_mcp.retrieval.config import DecisionCaptureConfig, LlmConfig

path = sys.argv[1]
state = IngestionState(files=FileBundle(
    target=path, target_kind=TargetKind.PROJECT, package_name="__project__", paths=(path,)))
stage = ContentHashStage(
    decision_capture=DecisionCaptureConfig.model_validate({"llm_structuring": {"enabled": True}}),
    llm=LlmConfig(model_name=sys.argv[2], temperature=float(sys.argv[3]), max_tokens=int(sys.argv[4])),
)
print(asyncio.run(stage.run(state)).files.content_hash)
"""


@pytest.mark.asyncio
async def test_a_structuring_hash_is_identical_in_a_fresh_interpreter(one_file: Path) -> None:
    """A per-process LLM part (say, a float formatted by locale or a set
    iterated under hash randomization) would make a structuring deployment miss
    its own stored hash on every pass — hence a real subprocess under a
    randomized hash seed (the test_chunk_tree_fingerprint.py precedent)."""
    llm = LlmConfig(model_name="model-a", temperature=0.3, max_tokens=512)
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_HASH, str(one_file), "model-a", "0.3", "512"],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": "random"},
    )

    assert result.stdout.strip() == await _hash(_state(one_file), llm)


# ── wiring: read from the app config, never serialized ────────────────────


def test_from_dict_reads_the_llm_config_from_the_app_config() -> None:
    """The same object the structuring client is built from
    (``build_llm_client(app_config.llm)``), so the stage that hashes sees the
    model the stage that structures used."""
    context = SimpleNamespace(app_config=SimpleNamespace(llm=_MODEL_B))

    assert ContentHashStage.from_dict({}, context).llm == _MODEL_B


@pytest.mark.parametrize(
    "context",
    [object(), SimpleNamespace(app_config=SimpleNamespace(decision_capture=None))],
    ids=["no-app-config", "no-llm"],
)
def test_from_dict_without_an_llm_config_uses_the_default(context: object) -> None:
    assert ContentHashStage.from_dict({}, context).llm == LlmConfig()


def test_the_llm_config_is_wiring_not_a_stage_tunable() -> None:
    """It belongs to the ``llm:`` block, not to this stage's YAML — and an
    unchanged ``to_dict`` keeps every pipeline YAML byte where it was."""
    stage = ContentHashStage(decision_capture=_STRUCTURING_ON, llm=_MODEL_B)

    assert stage.to_dict() == {"type": "content_hash"}
