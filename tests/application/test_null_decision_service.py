"""get_why without decision capture must raise, never mislead (spec §D9 Null rule)."""

import asyncio

import pytest

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
from pydocs_mcp.application.null_services import NullDecisionService


def test_every_mode_raises_with_yaml_pointer() -> None:
    svc = NullDecisionService()
    for call in (
        lambda: svc.search("why sqlite"),
        lambda: svc.for_targets(["a.py"]),
        lambda: svc.dashboard(),
    ):
        with pytest.raises(ServiceUnavailableError, match="decision_capture"):
            asyncio.run(call())
