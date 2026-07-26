"""The prompt-freeze contract: digest every frozen template in a package.

Owner rule (2026-07-26): a prompt consumed by an optimizable task that is
NOT itself an optimization target is an experimental control — frozen.
That covers each harness's local templates (its ``prompts/freeze/`` pool
plus its architecture namespaces) AND the retriever's own template pool
(``retrieval/prompts``, consumed by pipeline steps under ``config_search``
sweeps). Each frozen package is pinned mechanically: a test compares this
function's digests against a committed manifest golden, so editing or
adding a frozen template is a deliberate, reviewed event. The cross-harness
core pool is the optimizable surface and stays outside the freeze (its
bytes are pinned by the artifact seed-parity tests).
"""

from __future__ import annotations

import hashlib
from importlib import resources
from importlib.resources.abc import Traversable


def frozen_prompt_digests(package: str) -> dict[str, str]:
    """SHA-256 per frozen template — the byte surface a freeze manifest pins.

    Digests every ``*.j2`` in ``package``: top-level templates keyed by bare
    name (flat pools like ``retrieval/prompts``), subdirectory templates
    keyed ``"<dir>/<name>"`` (harness ``freeze/`` pools and architecture
    namespaces).

    Example:
        >>> frozen_prompt_digests("pydocs_mcp.retrieval.prompts")
        ... # doctest: +SKIP
    """
    digests: dict[str, str] = {}
    for entry in resources.files(package).iterdir():
        if entry.name.endswith(".j2"):
            digests[entry.name[:-3]] = _digest(entry)
            continue
        if not entry.is_dir() or entry.name == "__pycache__":
            continue
        for template in entry.iterdir():
            if template.name.endswith(".j2"):
                digests[f"{entry.name}/{template.name[:-3]}"] = _digest(template)
    # Sorted so a dumped manifest is byte-stable across regenerations.
    return dict(sorted(digests.items()))


def _digest(template: Traversable) -> str:
    return hashlib.sha256(template.read_bytes()).hexdigest()
