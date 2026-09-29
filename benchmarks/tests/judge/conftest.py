"""Judge-test fixtures: the bearer every client reads at call time."""

from __future__ import annotations

import pytest

from ._judge_fakes import PLANTED_BEARER


@pytest.fixture
def bearer(monkeypatch: pytest.MonkeyPatch) -> str:
    """``$OPENROUTER_API_KEY`` set to the planted bearer, for the test's duration."""
    monkeypatch.setenv("OPENROUTER_API_KEY", PLANTED_BEARER)
    return PLANTED_BEARER
