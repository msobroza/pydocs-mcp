"""The image recipes' Python scripts (``infra/bench-vm/docker/``), loaded as the image runs them."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

BENCH_VM_DIR = Path(__file__).resolve().parents[3] / "infra" / "bench-vm"
DOCKER_DIR = BENCH_VM_DIR / "docker"


def load_image_script(name: str) -> ModuleType:
    """Import ``<name>.py`` from the recipes' directory under its top-level name."""
    spec = importlib.util.spec_from_file_location(name, DOCKER_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # smoke.py imports image_deps by its top-level name, as it does in the image.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
