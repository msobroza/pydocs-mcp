"""Named import fakes for the embedder tests' error-path coverage.

``sys.modules[name] = None`` already simulates a package that is NOT
installed (the import raises ``ModuleNotFoundError(name=name)``). What it
cannot simulate is a package that IS installed but whose import breaks — a
moved symbol, or a dependency's missing submodule (optimum-intel 1.15.0 on
openvino 2026: ``No module named 'openvino.runtime'``). ``FailingImportFinder``
covers that case without touching the real packages.
"""

from __future__ import annotations

import importlib.abc
import sys
import types
from collections.abc import Sequence

import pytest


class FailingImportFinder(importlib.abc.MetaPathFinder):
    """Meta-path finder that makes importing exactly ``module_name`` raise ``error``."""

    def __init__(self, module_name: str, error: BaseException) -> None:
        self.module_name = module_name
        self.error = error

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: types.ModuleType | None = None,
    ) -> None:
        if fullname == self.module_name:
            raise self.error
        return None


def install_failing_import(
    monkeypatch: pytest.MonkeyPatch, module_name: str, error: BaseException
) -> None:
    """Make ``import module_name`` raise ``error`` for the rest of the test.

    Parent packages are replaced by empty fake packages, so the import reaches
    the finder whether the real package is absent (a CI job without the extra)
    or already imported (a dev venv with it) — the test is hermetic either way.

    Example::

        install_failing_import(monkeypatch, "pylate.models", ImportError("boom"))
    """
    parts = module_name.split(".")
    for depth in range(1, len(parts)):
        parent = ".".join(parts[:depth])
        package = types.ModuleType(parent)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, parent, package)
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    finder = FailingImportFinder(module_name, error)
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
