"""The judge package imports nothing from the product it measures, and no vendor SDK.

The code-first check must score a run of any product version — including one
that predates the constants it reads — so the judge package, and everything it
imports, keeps a zero-``pydocs_mcp`` floor. The check runs in a fresh
interpreter where ``sys.modules["pydocs_mcp"] = None`` makes ``import
pydocs_mcp`` raise ``ImportError``: the import fails if anything on the judge's
import path reaches the product, and this process's modules stay untouched (an
in-process re-import would leave the parent packages pointing at the copies).

Its clients speak plain HTTP through httpx — no TypeSafe or OpenAI SDK — and
its configuration is the eval's own config stack (pydantic models read from
YAML), so those are the only third-party imports a judge module may make
itself. What the eval's other packages import (``pydocs_eval.trajectory``, read
by the code-first check) is theirs: the AST check reads the judge's own modules
only, and the subprocess check proves no product and no vendor SDK loads.
"""

from __future__ import annotations

import ast
import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import pydocs_eval.judge

_JUDGE_ROOT = Path(pydocs_eval.judge.__file__).parent
# httpx for every client; pydantic and pyyaml for the typed YAML configuration.
_THIRD_PARTY_ALLOWED = frozenset({"httpx", "pydantic", "yaml"})

_SCORE_WITHOUT_THE_PRODUCT = """
import importlib
import pkgutil
import sys
sys.modules["pydocs_mcp"] = None
import pydocs_eval.judge
for module in pkgutil.walk_packages(pydocs_eval.judge.__path__, "pydocs_eval.judge."):
    importlib.import_module(module.name)
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
vendor_sdks = sorted(
    name for name in sys.modules if name.split(".")[0] in {"typesafe_sdk", "openai", "anthropic"}
)
assert not vendor_sdks, vendor_sdks
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


def _imported_roots(source: Path) -> set[str]:
    """The top-level package of every absolute import in ``source``."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_a_judge_module_imports_only_the_stdlib_httpx_and_the_config_stack() -> None:
    modules = sorted(_JUDGE_ROOT.rglob("*.py"))
    assert {module.stem for module in modules} >= {"jev_client", "openrouter_chat", "config"}

    outside = {
        str(module.relative_to(_JUDGE_ROOT)): sorted(foreign)
        for module in modules
        if (
            foreign := _imported_roots(module)
            - set(sys.stdlib_module_names)
            - _THIRD_PARTY_ALLOWED
            - {"pydocs_eval"}
        )
    }

    assert outside == {}


def test_the_floor_check_walks_every_judge_module() -> None:
    """The subprocess check imports what ``walk_packages`` finds, so it must find the clients."""
    found = {module.name for module in pkgutil.walk_packages(pydocs_eval.judge.__path__)}

    assert {
        "jev_client",
        "jev_requests",
        "judge_errors",
        "model_ids",
        "openrouter_batch",
        "openrouter_chat",
    } <= found
