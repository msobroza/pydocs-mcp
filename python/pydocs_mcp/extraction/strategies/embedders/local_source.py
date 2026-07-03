"""Local-directory model resolution shared by every embedder (airgap).

Spec: docs/superpowers/specs/2026-07-03-airgap-local-embedders-design.md
(D1: model_name-as-directory detection; D5: HF offline hardening).
"""

from __future__ import annotations

import os
from pathlib import Path


def local_model_dir(model_name: str) -> Path | None:
    """Return the model directory when ``model_name`` is a local path, else None.

    A repo id like ``BAAI/bge-small-en-v1.5`` never names an existing
    directory relative to the server's cwd in practice; anything that IS an
    existing directory is treated as side-loaded weights (spec D1 overloads
    ``model_name`` instead of adding a second YAML field).
    """
    # A blank YAML value must fall through to "not a local dir": Path("") is
    # Path("."), which is_dir() — it would silently point at the server cwd.
    if not model_name.strip():
        return None
    # Fail closed to repo-id mode when ~ can't be resolved (e.g. no HOME).
    try:
        path = Path(model_name).expanduser()
    except RuntimeError:
        return None
    if path.is_dir():
        return path
    return None


def enable_hf_offline() -> None:
    """Force huggingface_hub / transformers offline for airgap loads.

    ``setdefault`` so an operator's explicit setting (including an explicit
    opt-out ``HF_HUB_OFFLINE=0``) always wins over ours. Process-wide by
    design: in local mode the whole process is meant to be offline (D5).
    """
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


__all__ = ("enable_hf_offline", "local_model_dir")
