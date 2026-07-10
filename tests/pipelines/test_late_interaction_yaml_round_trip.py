"""Late-interaction preset YAMLs ship in ``python/pydocs_mcp/pipelines/``.

Three presets land together:

- ``ingestion_late_interaction.yaml`` — ingestion variant that swaps
  ``embed_chunks`` for ``embed_chunks_multi_vector`` so the multi-vector
  embedder populates ``uow.multi_vectors`` during indexing.
- ``chunk_search_late_interaction.yaml`` — hybrid BM25 + late-interaction
  parallel retrieval, fused via RRF, with the composite token-budget
  formatter for MCP output.
- ``chunk_search_late_interaction_ranked.yaml`` — benchmark variant of
  the above with the trailing ``token_budget_formatter`` stripped so
  ``state.candidates`` carries the ranked list for offline evaluation.

Tests assert each file ships at the shipped pipelines path AND that the
parsed step / stage structure matches the expected contract. Heavy
pipeline-instantiation coverage lives in the existing retrieval-pipeline
integration tests; the goal here is to lock the YAML payload + path
contract without requiring a full BuildContext.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _shipped(name: str) -> Path:
    """Resolve a preset YAML path under the shipped pipelines directory."""
    import pydocs_mcp

    pkg_dir = Path(pydocs_mcp.__file__).parent
    return pkg_dir / "pipelines" / name


def _step_types(steps: list[dict[str, Any]]) -> set[str]:
    """Flatten ``parallel_retrieval`` branches and collect every step ``type``.

    A retrieval preset's top-level ``steps`` list may contain a
    ``parallel_retrieval`` step whose ``params.branches`` each carry
    their own nested ``steps``; this helper walks one level deep so
    callers can assert against the full set of step types referenced
    anywhere in the preset.
    """
    seen: set[str] = set()
    for step in steps:
        t = step.get("type")
        if isinstance(t, str):
            seen.add(t)
        params = step.get("params") or {}
        for branch in params.get("branches", []) or []:
            for nested in branch.get("steps", []) or []:
                nt = nested.get("type")
                if isinstance(nt, str):
                    seen.add(nt)
    return seen


def test_ingestion_late_interaction_ships() -> None:
    """Preset file lives next to ``ingestion.yaml`` in the shipped dir."""
    path = _shipped("ingestion_late_interaction.yaml")
    assert path.is_file(), f"missing preset at {path}"


def test_ingestion_late_interaction_swaps_embedder_stage() -> None:
    """The ingestion preset references ``embed_chunks_multi_vector``."""
    doc = yaml.safe_load(
        _shipped("ingestion_late_interaction.yaml").read_text(encoding="utf-8"),
    )
    stage_types = {stage.get("type") for stage in doc["stages"]}
    assert "embed_chunks_multi_vector" in stage_types
    # The swap is the whole point — single-vector ``embed_chunks`` must
    # not coexist (would double-embed every chunk).
    assert "embed_chunks" not in stage_types


def test_ingestion_late_interaction_captures_decisions_and_dep_doc_pages() -> None:
    """Decision mining + dependency doc pages ship in the LI preset too.

    Both stages landed in ``ingestion.yaml`` after this preset was created
    and were missed here (drift): a late-interaction deployment silently
    lost index-time decision capture (``get_why``'s data source) and
    per-module dependency doc pages. Mirror the default preset's ordering
    — both sit between ``flatten`` and ``assign_chunk_content_hash`` so
    the chunks they emit get content hashes and embeddings.
    """
    doc = yaml.safe_load(
        _shipped("ingestion_late_interaction.yaml").read_text(encoding="utf-8"),
    )
    types = [stage.get("type") for stage in doc["stages"]]
    flatten_idx = types.index("flatten")
    hash_idx = types.index("assign_chunk_content_hash")
    assert flatten_idx < types.index("capture_decisions") < hash_idx
    assert flatten_idx < types.index("dependency_doc_pages") < hash_idx


def test_ingestion_late_interaction_tracks_default_preset() -> None:
    """Anti-drift guard: stage list == ``ingestion.yaml`` modulo two deltas.

    The only sanctioned differences from the default preset:

    - ``embed_chunks`` → ``embed_chunks_multi_vector`` (the preset's point).
    - ``synthesize_similar_edges`` omitted: its stage only reads
      single-vector embeddings (``_eligible`` skips multi-vector chunks),
      so it would be inert in a preset where every chunk is multi-vector.

    A stage added to ``ingestion.yaml`` fails this test until someone
    decides whether the late-interaction preset needs it too.
    """
    default_doc = yaml.safe_load(
        _shipped("ingestion.yaml").read_text(encoding="utf-8"),
    )
    li_doc = yaml.safe_load(
        _shipped("ingestion_late_interaction.yaml").read_text(encoding="utf-8"),
    )
    expected = [
        "embed_chunks_multi_vector" if t == "embed_chunks" else t
        for t in (stage.get("type") for stage in default_doc["stages"])
        if t != "synthesize_similar_edges"
    ]
    assert [stage.get("type") for stage in li_doc["stages"]] == expected


def test_chunk_search_late_interaction_ships() -> None:
    """Preset file ships in the shipped pipelines directory."""
    path = _shipped("chunk_search_late_interaction.yaml")
    assert path.is_file(), f"missing preset at {path}"


def test_chunk_search_late_interaction_has_scorer() -> None:
    """The chunk_search preset references the ``late_interaction_scorer`` step."""
    doc = yaml.safe_load(
        _shipped("chunk_search_late_interaction.yaml").read_text(encoding="utf-8"),
    )
    assert "late_interaction_scorer" in _step_types(doc["steps"])


def test_chunk_search_late_interaction_ranked_ships() -> None:
    """The ranked / benchmark variant ships next to its composite sibling."""
    path = _shipped("chunk_search_late_interaction_ranked.yaml")
    assert path.is_file(), f"missing preset at {path}"


def test_chunk_search_late_interaction_ranked_omits_token_budget() -> None:
    """The ``_ranked`` variant strips ``token_budget_formatter`` for benchmarks.

    Asserts against the *parsed step list* — the file's docstring may
    mention ``token_budget_formatter`` in comments explaining what was
    dropped, but the executable step graph must not contain it.
    """
    doc = yaml.safe_load(
        _shipped("chunk_search_late_interaction_ranked.yaml").read_text(
            encoding="utf-8",
        ),
    )
    types = _step_types(doc["steps"])
    assert "late_interaction_scorer" in types
    assert "token_budget_formatter" not in types
