"""Member extractors — one file per strategy.

Two implementations of
:class:`~pydocs_mcp.application.protocols.MemberExtractor`:

- :mod:`.ast_extractor` — :class:`AstMemberExtractor` (static, safe on
  untrusted code)
- :mod:`.inspect_extractor` — :class:`InspectMemberExtractor`
  (live-import for deps, AST for projects per spec §9.2)

Re-exports the underscore helper ``_path_under_excluded`` (used by
:mod:`tests.extraction.test_members`) so the existing test import path
keeps working across the split.
"""

from __future__ import annotations

from pydocs_mcp.extraction.strategies.members.ast_extractor import (
    AstMemberExtractor,
    _path_under_excluded,
)
from pydocs_mcp.extraction.strategies.members.base_extractor import MemberExtractor
from pydocs_mcp.extraction.strategies.members.inspect_extractor import (
    InspectMemberExtractor,
)

__all__ = (
    "AstMemberExtractor",
    "InspectMemberExtractor",
    "MemberExtractor",
    "_path_under_excluded",
)
