"""The sidebar's scope pickers — which slice of the workspace a question may search.

Moved out of ``app.py`` (line budget); widgets, keys and pins are unchanged. The project pin
is forced onto every tool call; the package and own-vs-dependency pins constrain the search
tools (``scope_pin.pinned_args``). Call it inside the page's ``with st.sidebar:`` block.

Example:
    project_pin, package_pin, code_pin = render_scope_pickers(workspace, load_catalog)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

import streamlit as st

_CODE_CHOICES = {"All code": "all", "Own code": "project", "Dependencies": "deps"}


class ScopePins(NamedTuple):
    """The picked pins; ``""`` / ``"all"`` pin nothing."""

    project: str
    package: str
    code: str


_NO_PINS = ScopePins("", "", "all")


def render_scope_pickers(
    workspace: str, load_catalog: Callable[[str], dict[str, list[str]]]
) -> ScopePins:
    """Render the Project / Code / Package pickers for ``workspace`` and return the pins."""
    if not workspace:
        return _NO_PINS
    try:
        projects = load_catalog(workspace)
    except Exception as exc:  # unreadable dir, no bundles, corrupt db
        st.warning(f"Couldn't scan workspace: {exc}")
        return _NO_PINS
    return _render_pickers(projects) if projects else _NO_PINS


def _render_pickers(projects: dict[str, list[str]]) -> ScopePins:
    st.markdown('<div class="side-label">Scope</div>', unsafe_allow_html=True)
    picked = st.selectbox("Project", ["All projects", *projects], key="scope_project")
    project_pin = "" if picked == "All projects" else picked
    code_pin = _CODE_CHOICES[
        st.radio("Code", list(_CODE_CHOICES), horizontal=True, key="scope_code")
    ]
    package_pin = _render_package_picker(projects, project_pin, code_pin)
    st.caption("Searches run only inside this scope.")
    return ScopePins(project_pin, package_pin, code_pin)


def _render_package_picker(projects: dict[str, list[str]], project_pin: str, code_pin: str) -> str:
    pool = sorted(
        {
            p
            for name, pkgs in projects.items()
            if not project_pin or name == project_pin
            for p in pkgs
        }
    )
    # No picker when own code is pinned (packages are dependencies) or
    # the pinned slice has no dependency packages indexed.
    if code_pin == "project" or not pool:
        return ""
    picked = st.selectbox("Package", ["All packages", *pool], key="scope_package")
    return "" if picked == "All packages" else picked
