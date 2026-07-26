"""The eval-suite ask_prompt seed must equal the live product prompts (AC-4).

This is the CI half of the AC-4 regeneration contract: the committed seed
file under benchmarks/ is package data of the eval suite, whose tests are a
local-only gate — so the byte-parity pin lives HERE, in the product suite CI
always runs. Editing SYSTEM_PROMPT or the rewrite template without
regenerating the seed fails this test.

The expected bytes are composed in the shared delimited grammar
(one `=== KEY ===` header line + content + one newline per section — the
canonical implementation is benchmarks/src/pydocs_eval/optimize/artifacts/
_delimited.py; the grammar is frozen by design, so the three-line inline
composition here cannot drift silently).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.ask_your_docs.prompts import SYSTEM_PROMPT, render_shared

_SEED_PATH = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "src"
    / "pydocs_eval"
    / "optimize"
    / "artifacts"
    / "ask_prompt_seed.md"
)


@pytest.mark.skipif(not _SEED_PATH.exists(), reason="benchmarks tree not present")
def test_seed_file_matches_the_live_product_prompts() -> None:
    rewrite_seed = render_shared("rewrite_v1", history="{history}", question="{question}")
    expected = f"=== SYSTEM_PROMPT ===\n{SYSTEM_PROMPT}\n=== REWRITE_PROMPT ===\n{rewrite_seed}\n"
    actual = _SEED_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "ask_prompt_seed.md drifted from the live product prompts — regenerate "
        'it: PYTHONPATH=benchmarks/src python -c "from pydocs_eval.optimize.'
        "artifacts.ask_prompt import AskPromptArtifact; import pathlib; "
        "pathlib.Path('benchmarks/src/pydocs_eval/optimize/artifacts/"
        "ask_prompt_seed.md').write_text(AskPromptArtifact().render())\""
    )
