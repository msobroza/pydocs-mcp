"""The judge package imports nothing from the product it measures.

The code-first check must score a run of any product version — including one
that predates the constants it reads — so the judge package, and everything it
imports, keeps a zero-``pydocs_mcp`` floor. The check runs in a fresh
interpreter where ``sys.modules["pydocs_mcp"] = None`` makes ``import
pydocs_mcp`` raise ``ImportError``: the import fails if anything on the judge's
import path reaches the product, and this process's modules stay untouched (an
in-process re-import would leave the parent packages pointing at the copies).
"""

from __future__ import annotations

import os
import subprocess
import sys

_SCORE_WITHOUT_THE_PRODUCT = """
import sys
sys.modules["pydocs_mcp"] = None
from pydocs_eval.judge.config import load_judge_config
from pydocs_eval.judge.needle_citation import NeedleSite, score_needle_citation
citation = score_needle_citation(
    "It is `pkg.mod.run`.",
    [NeedleSite("src/pkg/mod.py", "run")],
    extensions=load_judge_config().jev.citation_extensions,
)
assert citation.needle_cited, citation
loaded = sorted(name for name in sys.modules if name.startswith("pydocs_mcp."))
assert not loaded, loaded
"""


def test_the_judge_scores_an_answer_without_the_product() -> None:
    # The child sees exactly the import roots this process does, source trees included.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}

    result = subprocess.run(
        [sys.executable, "-c", _SCORE_WITHOUT_THE_PRODUCT],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
