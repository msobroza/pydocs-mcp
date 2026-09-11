"""Shared skip guard for the tests that need fast-plaid's native stack."""

from __future__ import annotations

import importlib

import pytest

_FAST_PLAID_NATIVE = "fast_plaid.search"


def skip_unless_fast_plaid_native_loads() -> None:
    """Skip the calling test unless fast-plaid's native index imports.

    The ``[late-interaction]`` extra is opt-in and CI's test job does not install
    it, so the import can be missing. It can also be *installed yet unloadable*:
    numkong >= 7.5's macOS-arm64 wheels are built against the macOS 26 SDK and
    import ``___sme_memset``, a libSystem symbol only macOS 15+ exports, so on
    macOS 14 ``import numkong`` dies at dlopen and usearch then fails on
    ``_nk_capabilities``. ``fast_plaid`` itself is pure Python and imports either
    way, which is why the probe targets ``fast_plaid.search``.

    WHY not ``pytest.importorskip``: it warns on an ImportError under pytest 9.0
    and, from 9.1, re-raises instead of skipping — turning this guard back into
    the collection error it exists to prevent. ``pytest >= 7`` is the dev floor,
    so the ``exc_type=`` opt-out is not available on every supported pytest.

    Example:
        def test_needs_fast_plaid() -> None:
            skip_unless_fast_plaid_native_loads()
    """
    try:
        importlib.import_module(_FAST_PLAID_NATIVE)
    except ImportError as exc:
        pytest.skip(f"[late-interaction] extra not usable here: {exc}")
