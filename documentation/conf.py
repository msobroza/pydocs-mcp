"""Sphinx configuration for the pydocs-mcp documentation site.

The package is imported FROM SOURCE (``python/`` on ``sys.path``) so autodoc
needs no maturin/Rust build: ``_fast.py`` falls back to ``_fallback.py`` when the
compiled ``._native`` extension is absent. Only system ``libopenblas`` is needed
at import time (``turbovec`` links CBLAS).
"""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

# Resolve relative to THIS file (not the CWD) so the build works no matter where
# sphinx-build is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

# -- Project information -----------------------------------------------------
project = "pydocs-mcp"
author = "Max Sobroza"
project_copyright = "2026, Max Sobroza"
try:
    release = _pkg_version("pydocs-mcp")
except PackageNotFoundError:  # running from a checkout, package not installed
    release = "0.4.0"
version = release

# -- General configuration ---------------------------------------------------
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",  # Google-style docstrings
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "myst_parser",  # Markdown sources
    "sphinx_design",  # cards / grids / tabs
    "sphinx_copybutton",
    "sphinxcontrib.mermaid",  # render the DB-schema ER diagram
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "superpowers/**", "Thumbs.db", ".DS_Store"]
source_suffix = {".md": "markdown", ".rst": "restructuredtext"}

# Suppress two classes of noise inherent to composing the site from sliced
# canonical files, so `-W` still fails on genuinely new problems:
#   - myst.xref_missing: cross-doc links inside included root files (e.g. the
#     README linking to INSTALL.md, or repo-relative paths) point at non-pages.
#   - myst.header: a section sliced after its `##` heading starts its body at
#     `###`, so a page H1 is followed by H3 (a deliberate, harmless level skip).
# See the docs plan, follow-up #2.
suppress_warnings = ["myst.xref_missing", "myst.header"]

# -- Autodoc / napoleon ------------------------------------------------------
napoleon_google_docstring = True
napoleon_numpy_docstring = False
autodoc_default_options = {
    "members": True,
    "show-inheritance": True,
    "member-order": "bysource",
}
autodoc_typehints = "description"
# If you'd rather NOT install runtime deps + libopenblas for the build, mock the
# heavy/native imports instead and drop the apt-get / dependency install in CI:
# autodoc_mock_imports = ["turbovec", "fastembed", "openai", "mcp", "tiktoken",
#                         "pylate", "fast_plaid", "sentence_transformers",
#                         "transformers", "watchdog"]

# -- intersphinx -------------------------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pydantic": ("https://docs.pydantic.dev/latest/", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}

# -- MyST --------------------------------------------------------------------
myst_enable_extensions = ["colon_fence", "deflist", "attrs_block", "substitution"]
myst_heading_anchors = 3
myst_fence_as_directive = ["mermaid"]  # treat ```mermaid fences as the directive

# -- HTML output -------------------------------------------------------------
html_theme = "furo"
html_title = "pydocs-mcp"
html_logo = "_static/logo.png"
html_favicon = "_static/favicon.png"
html_static_path = ["_static"]
html_theme_options = {
    "source_repository": "https://github.com/msobroza/pydocs-mcp",
    "source_branch": "main",
    "source_directory": "documentation/",
}
