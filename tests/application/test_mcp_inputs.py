"""Tests for SearchInput / LookupInput Pydantic models (sub-PR #6 §4.3)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.application.mcp_inputs import LookupInput, SearchInput


# ─── SearchInput ──────────────────────────────────────────────────────────


def test_search_input_defaults() -> None:
    m = SearchInput(query="hello")
    assert m.query == "hello"
    assert m.kind == "any"
    assert m.package == ""
    assert m.scope == "all"
    assert m.limit == 10


def test_search_input_empty_query_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchInput(query="")


def test_search_input_huge_query_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchInput(query="x" * 30001)


def test_search_input_bad_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchInput(query="x", kind="weird")  # type: ignore[arg-type]


def test_search_input_bad_scope_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchInput(query="x", scope="galaxy")  # type: ignore[arg-type]


def test_search_input_limit_out_of_range() -> None:
    with pytest.raises(ValidationError):
        SearchInput(query="x", limit=0)
    with pytest.raises(ValidationError):
        SearchInput(query="x", limit=1001)


@pytest.mark.parametrize(
    "package",
    ["fastapi", "__project__", "Flask-Login", "scikit-learn", "a_b", "pkg.sub"],
)
def test_search_input_package_accepts_valid(package: str) -> None:
    SearchInput(query="x", package=package)


@pytest.mark.parametrize(
    "package",
    ["has space", "!", "-leading-dash", "trailing.", ".leading"],
)
def test_search_input_package_rejects_invalid(package: str) -> None:
    with pytest.raises(ValidationError):
        SearchInput(query="x", package=package)


def test_search_input_empty_package_ok() -> None:
    SearchInput(query="x", package="")


# ─── LookupInput ──────────────────────────────────────────────────────────


def test_lookup_input_defaults() -> None:
    m = LookupInput()
    assert m.target == ""
    assert m.show == "default"


@pytest.mark.parametrize(
    "target",
    [
        "",
        "fastapi",
        "fastapi.routing",
        "fastapi.routing.APIRouter",
        "fastapi.routing.APIRouter.include_router",
        "__project__",
    ],
)
def test_lookup_input_target_accepts_valid(target: str) -> None:
    LookupInput(target=target)


@pytest.mark.parametrize(
    "target",
    [
        "has spaces",
        "foo..bar",
        "foo.",
        ".foo",
        "1bad",
        "foo!",
    ],
)
def test_lookup_input_target_rejects_invalid(target: str) -> None:
    with pytest.raises(ValidationError):
        LookupInput(target=target)


def test_lookup_input_bad_show_rejected() -> None:
    with pytest.raises(ValidationError):
        LookupInput(show="invalid")  # type: ignore[arg-type]
