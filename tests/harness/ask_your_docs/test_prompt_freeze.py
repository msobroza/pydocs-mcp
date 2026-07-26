"""The ask-your-docs prompt freeze — frozen templates are byte-pinned.

Owner rule (2026-07-26): every prompt a harness consumes during
optimizable tasks that is NOT itself an optimization target is an
experimental control — it must stay byte-stable or recorded campaign
results lose comparability. The manifest golden is that freeze made
executable: editing (or adding) a harness-local template fails here until
the manifest is deliberately regenerated in the same reviewed commit.

The optimizable surface is deliberately OUTSIDE this freeze: the core pool
(``system_v1`` / ``rewrite_v1``) is byte-pinned by the ask_prompt
seed-parity test, and the skill artifact by the loader tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("jinja2")

from pydocs_mcp.harness.core.prompt_freeze import frozen_prompt_digests

_AYD_PACKAGE = "pydocs_mcp.harness.ask_your_docs.prompts"
_MANIFEST = Path(__file__).resolve().parents[3] / (
    "tests/fixtures/goldens/ask_your_docs_prompt_freeze.json"
)


def test_frozen_templates_match_the_committed_manifest() -> None:
    live = frozen_prompt_digests(_AYD_PACKAGE)
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert live == manifest, (
        "frozen prompt templates drifted from the committed freeze manifest. "
        "These templates are experimental controls for optimized tasks: never "
        "edit a shipped _vN in place — ship _vN+1 — and regenerate the "
        "manifest deliberately in the same commit:\n"
        '  PYTHONPATH=python python -c "import json; '
        "from pydocs_mcp.harness.core.prompt_freeze import frozen_prompt_digests; "
        f"print(json.dumps(frozen_prompt_digests('{_AYD_PACKAGE}'), indent=2))\" "
        f"> {_MANIFEST.relative_to(_MANIFEST.parents[3])}"
    )


def test_freeze_covers_every_harness_local_template() -> None:
    # The freeze pool AND every architecture namespace are frozen; only the
    # cross-harness core pool (the optimizable surface) is outside.
    live = frozen_prompt_digests(_AYD_PACKAGE)
    assert "freeze/vision_extraction_v1" in live
    assert "freeze/reinspect_description_v1" in live
    assert "freeze/reinspect_budget_message_v1" in live
    assert "inline/system_suffix_v1" in live
    assert not any(key.split("/")[1] in ("system_v1", "rewrite_v1") for key in live)
