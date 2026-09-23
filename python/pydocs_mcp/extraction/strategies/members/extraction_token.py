"""The member-extraction token: which settings a dependency's members come from.

``ProjectIndexer`` extracts a dependency's members AFTER the package cache
check, so a changed ``--no-inspect``, ``--depth`` or ``extraction.members.*``
reaches an already-indexed dependency only through its package hash (issue
#347). Only the composition root knows the first two, so it builds the token
here, from the SAME values it builds the member extractor from, and hands it to
``ContentHashStage`` as a plain string through ``BuildContext`` — a string keeps
``retrieval/serialization.py`` free of extraction types.

The module's own imports are config only and it holds no extractor logic: it
names the settings an extractor reads without depending on any extractor.
Importing it still runs the ``members`` package ``__init__``, which loads both
extractors — harmless at the composition root, which builds one anyway.
"""

from __future__ import annotations

from enum import StrEnum

from pydocs_mcp.extraction.config import MembersConfig


class MemberExtractionMode(StrEnum):
    """How dependency members are extracted — the ``--no-inspect`` switch."""

    INSPECT = "inspect"
    STATIC = "static"


def member_extraction_token(*, use_inspect: bool, depth: int, members: MembersConfig) -> str:
    """The token naming every setting the built member extractor actually reads.

    Static mode is one token whatever the depth and caps: ``AstMemberExtractor``
    reads none of them, so tuning one under ``--no-inspect`` must re-extract
    nothing. Inspect mode names the depth the extractor was given — the CLI's
    ``--depth`` when set, so ``depth`` wins over ``members.inspect_depth`` —
    plus all three caps, each of which changes what ``_extract_by_import``
    emits. Plain ``key=value`` fields rather than a digest keep the stage's
    pinned stock token reviewable.

    Example: ``member_extraction_token(use_inspect=True, depth=1,
    members=MembersConfig())`` returns
    ``'inspect|depth=1|cap=120|sig=200|doc=1024'``; with ``use_inspect=False``
    it returns ``'static'``.
    """
    if not use_inspect:
        return MemberExtractionMode.STATIC.value
    return (
        f"{MemberExtractionMode.INSPECT.value}|depth={depth}"
        f"|cap={members.members_per_module_cap}"
        f"|sig={members.signature_max_chars}"
        f"|doc={members.docstring_max_chars}"
    )


__all__ = ("MemberExtractionMode", "member_extraction_token")
