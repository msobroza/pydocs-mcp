"""The late-interaction tests must skip — never error — on a broken native wheel.

numkong >= 7.5 ships macOS-arm64 wheels that fail at dlopen on macOS 14, and
usearch (fast-plaid's index) then fails on ``_nk_capabilities``. The guard turns
that into a skip; these tests pin the guard's two outcomes and its call sites.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from tests.integration import _late_interaction_guard as guard

_GUARDED_TESTS = (
    Path(__file__).parent / "test_index_pass_settles.py",
    Path(__file__).parent / "test_unstamped_bundle_still_serves.py",
)


def test_guard_skips_when_the_native_stack_cannot_load(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(name: str) -> types.ModuleType:
        raise ImportError(f"symbol not found in flat namespace '_nk_capabilities' ({name})")

    monkeypatch.setattr(guard.importlib, "import_module", _boom)

    with pytest.raises(pytest.skip.Exception) as excinfo:
        guard.skip_unless_fast_plaid_native_loads()

    assert "[late-interaction]" in str(excinfo.value)
    assert "_nk_capabilities" in str(excinfo.value)


def test_guard_returns_when_the_native_stack_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guard.importlib, "import_module", lambda name: types.ModuleType(name))

    assert guard.skip_unless_fast_plaid_native_loads() is None


@pytest.mark.parametrize("path", _GUARDED_TESTS, ids=lambda p: p.name)
def test_late_interaction_tests_use_the_shared_guard(path: Path) -> None:
    """importorskip re-raises ImportError by default from pytest 9.1 — the very
    collection error this guard exists to avoid — so the call sites must not use it."""
    source = path.read_text(encoding="utf-8")

    assert "skip_unless_fast_plaid_native_loads()" in source, f"{path.name} lost the guard"
    assert "importorskip" not in source, f"{path.name} still uses pytest.importorskip"
