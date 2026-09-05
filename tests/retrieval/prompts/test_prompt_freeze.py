"""The retrieval prompt freeze — the backbone's templates are byte-pinned.

Owner rule (2026-07-26): the retrieval templates (LLM tree-reasoning) are
consumed by pipeline steps during optimizable tasks — `config_search`
sweeps the pipeline YAML, never this text — so they are experimental
controls, frozen exactly like the harness-local prompts. Editing (or
adding) one fails here until the manifest is deliberately regenerated in
the same reviewed commit (never edit a shipped _vN in place — ship _vN+1).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_mcp.harness.core.prompt_freeze import frozen_prompt_digests

_PACKAGE = "pydocs_mcp.retrieval.prompts"
_MANIFEST = Path(__file__).resolve().parents[3] / (
    "tests/fixtures/goldens/retrieval_prompt_freeze.json"
)


def test_frozen_retrieval_templates_match_the_committed_manifest() -> None:
    live = frozen_prompt_digests(_PACKAGE)
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert live == manifest, (
        "retrieval prompt templates drifted from the committed freeze "
        "manifest. They are experimental controls for optimized tasks: ship "
        "_vN+1 instead of editing, and regenerate the manifest deliberately "
        "in the same commit:\n"
        '  PYTHONPATH=python python -c "import json; '
        "from pydocs_mcp.harness.core.prompt_freeze import frozen_prompt_digests; "
        f"print(json.dumps(frozen_prompt_digests('{_PACKAGE}'), indent=2))\" "
        f"> {_MANIFEST.relative_to(_MANIFEST.parents[3])}"
    )


def test_freeze_covers_the_tree_reasoning_templates() -> None:
    live = frozen_prompt_digests(_PACKAGE)
    assert "tree_reasoning_pydocs_v1" in live
    assert "tree_reasoning_pageindex_v1" in live
