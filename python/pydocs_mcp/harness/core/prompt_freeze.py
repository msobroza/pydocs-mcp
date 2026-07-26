"""The prompt-freeze contract: digest every harness-local template.

Owner rule (2026-07-26): a prompt consumed by an optimizable task that is
NOT itself an optimization target is an experimental control — frozen. A
harness expresses the frozen set structurally (its ``prompts/freeze/`` pool
plus its architecture namespaces) and pins it mechanically: a per-harness
test compares this function's digests against a committed manifest golden,
so editing or adding a frozen template is a deliberate, reviewed event.
The cross-harness core pool is the optimizable surface and stays outside
the freeze (its bytes are pinned by the artifact seed-parity tests).
"""

from __future__ import annotations

import hashlib
from importlib import resources


def frozen_prompt_digests(package: str) -> dict[str, str]:
    """SHA-256 per harness-local template, keyed ``"<dir>/<name>"``.

    Walks every subdirectory of a harness prompt package (``freeze/`` and
    each architecture namespace) and digests each ``*.j2`` — the byte
    surface the freeze manifest pins.

    Example:
        >>> frozen_prompt_digests("pydocs_mcp.harness.ask_your_docs.prompts")
        ... # doctest: +SKIP
    """
    digests: dict[str, str] = {}
    for directory in resources.files(package).iterdir():
        if not directory.is_dir() or directory.name == "__pycache__":
            continue
        for entry in directory.iterdir():
            if entry.name.endswith(".j2"):
                digest = hashlib.sha256(entry.read_bytes()).hexdigest()
                digests[f"{directory.name}/{entry.name[:-3]}"] = digest
    # Sorted so a dumped manifest is byte-stable across regenerations.
    return dict(sorted(digests.items()))
