"""``normalised_repo_url``: the spellings of one GitHub repository compare equal (finding F10)."""

from __future__ import annotations

import pytest

from pydocs_eval.bench_vm.product_repo import PRODUCT_REPO_URL, normalised_repo_url


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/msobroza/pydocs-mcp.git",
        "https://github.com/msobroza/pydocs-mcp",
        "https://github.com/msobroza/pydocs-mcp/",
        "http://github.com/msobroza/pydocs-mcp.git",
        "https://user@github.com/msobroza/pydocs-mcp.git",
        "https://GitHub.com/MSobroza/pydocs-mcp.git",
        "git@github.com:msobroza/pydocs-mcp.git",
        "git@github.com:msobroza/pydocs-mcp",
        "ssh://git@github.com/msobroza/pydocs-mcp.git",
        "ssh://git@github.com:22/msobroza/pydocs-mcp.git",
        "git://github.com/msobroza/pydocs-mcp.git",
        "  https://github.com/msobroza/pydocs-mcp.git\n",
    ],
)
def test_every_spelling_of_the_product_repository_normalises_to_its_url(url: str) -> None:
    assert normalised_repo_url(url) == normalised_repo_url(PRODUCT_REPO_URL)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/someone-else/pydocs-mcp.git",
        "git@github.com:msobroza/pydocs-mcp-fork.git",
        "https://gitlab.com/msobroza/pydocs-mcp.git",
        "/tmp/origin.git",
        "file:///tmp/pydocs-mcp.git",
    ],
)
def test_another_repository_does_not(url: str) -> None:
    assert normalised_repo_url(url) != normalised_repo_url(PRODUCT_REPO_URL)


def test_the_normalised_form_drops_credentials() -> None:
    assert "token" not in normalised_repo_url("https://token@github.com/msobroza/pydocs-mcp.git")
