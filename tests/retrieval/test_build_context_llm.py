"""AC-10: BuildContext gains an llm_client field for steps that need it."""

from __future__ import annotations

from pydocs_mcp.retrieval.serialization import BuildContext
from tests._fakes import FakeLlmClient


def test_build_context_default_llm_client_is_none() -> None:
    ctx = BuildContext()
    assert ctx.llm_client is None


def test_build_context_accepts_llm_client() -> None:
    fake = FakeLlmClient(responses={})
    ctx = BuildContext(llm_client=fake)
    assert ctx.llm_client is fake
