"""Tests for SearchInput / LookupInput Pydantic models (sub-PR #6 §4.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.application.mcp_inputs import (
    LookupInput,
    SearchInput,
    _ConfigShape,
    configure_from_app_config,
)


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


# ─── _ConfigShape Protocol (I11) ─────────────────────────────────────────


def test_config_shape_is_runtime_checkable() -> None:
    """``_ConfigShape`` is a ``@runtime_checkable`` Protocol — bare ducks with
    the right attribute set pass ``isinstance`` checks.

    Replaces the previous ``cfg: Any`` parameter of
    :func:`configure_from_app_config`. The structural test pins the
    contract: any object exposing ``reference_graph`` and ``search``
    attributes satisfies the Protocol — no nominal subclassing required.
    """

    class _Duck:
        def __init__(self) -> None:
            self.reference_graph = object()
            self.search = object()
            self.symbol_source = object()

    assert isinstance(_Duck(), _ConfigShape)


def test_config_shape_rejects_object_missing_attrs() -> None:
    """A plain object with no ``reference_graph`` / ``search`` attributes is
    NOT a ``_ConfigShape``. Sanity check that ``@runtime_checkable`` is
    actually exercising the structural shape."""
    assert not isinstance(object(), _ConfigShape)


def test_configure_from_app_config_accepts_real_app_config() -> None:
    """End-to-end smoke: the actual ``AppConfig`` instance satisfies
    ``_ConfigShape`` and ``configure_from_app_config`` accepts it without
    error. Guards against accidental Protocol drift away from the real
    config model."""
    # Local import — keeps tests/ free of the heavy retrieval import chain
    # except where the test needs it. Mirrors the lazy import pattern
    # inside ``configure_from_app_config`` itself.
    from pydocs_mcp.retrieval.config import AppConfig

    cfg = AppConfig()
    assert isinstance(cfg, _ConfigShape)
    # Must not raise — the function reads ``cfg.reference_graph`` and
    # ``cfg.search`` sub-trees, both present on AppConfig.
    configure_from_app_config(cfg)
