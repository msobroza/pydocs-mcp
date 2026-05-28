"""P2-4: every pydocs-mcp custom exception inherits from PydocsMCPError.

Lets embedders catch any library-originated failure with one
`except PydocsMCPError` clause, without swallowing unrelated bugs.
"""

from __future__ import annotations


def test_pydocs_mcp_error_root_exists() -> None:
    from pydocs_mcp.exceptions import PydocsMCPError

    assert issubclass(PydocsMCPError, Exception)


def test_mcp_tool_errors_inherit_from_root() -> None:
    from pydocs_mcp.application.mcp_errors import (
        InvalidArgumentError,
        MCPToolError,
        NotFoundError,
        ServiceUnavailableError,
    )
    from pydocs_mcp.exceptions import PydocsMCPError

    assert issubclass(MCPToolError, PydocsMCPError)
    assert issubclass(InvalidArgumentError, PydocsMCPError)
    assert issubclass(NotFoundError, PydocsMCPError)
    assert issubclass(ServiceUnavailableError, PydocsMCPError)


def test_uow_not_entered_error_inherits_from_root() -> None:
    """Preserves the RuntimeError lineage (legacy catch-all in some
    callers) while adding the PydocsMCPError handle."""
    from pydocs_mcp.exceptions import PydocsMCPError
    from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError

    assert issubclass(UnitOfWorkNotEnteredError, PydocsMCPError)
    assert issubclass(UnitOfWorkNotEnteredError, RuntimeError)


def test_pipeline_load_error_inherits_from_root() -> None:
    """Preserves the ValueError lineage."""
    from pydocs_mcp.exceptions import PydocsMCPError
    from pydocs_mcp.retrieval.pipeline.code_pipeline import PipelineLoadError

    assert issubclass(PipelineLoadError, PydocsMCPError)
    assert issubclass(PipelineLoadError, ValueError)


def test_catch_any_pydocs_error_with_single_except() -> None:
    """The whole point of the root: callers can catch every pydocs-mcp
    failure with one handle without swallowing unrelated bugs."""
    from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
    from pydocs_mcp.exceptions import PydocsMCPError

    try:
        raise ServiceUnavailableError("test")
    except PydocsMCPError as e:
        assert str(e) == "test"
