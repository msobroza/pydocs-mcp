"""The code checks a reference answer passes before it is kept, one per dataset (judge 9c).

A reference is written from ground truth, so it must name the ground truth and
nothing beside it: the judges compare agent answers against it. A reference
that fails is regenerated; one that still fails is listed for the owner, never
kept. A path counts as the gold's when it is one of its aliases
(:func:`~pydocs_eval.judge.needle_citation.gold_aliases`): a reference may
shorten a path it has already named in full.

Example:
    >>> site = NeedleSite("pkg/mod.py", "run")
    >>> check_repoqa_reference("`pkg/mod.py` — `run`", site, extensions=(".py",)).passed
    True
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_eval.judge.needle_citation import (
    NeedleSite,
    extract_citations,
    extract_dotted_names,
    gold_aliases,
)
from pydocs_eval.judge.reference_sources import ReferenceShape, ReferenceSource


@dataclass(frozen=True, slots=True)
class ReferenceCheck:
    """Everything a reference lacks or wrongly adds; it passes when that is nothing."""

    problems: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.problems


def check_repoqa_reference(
    text: str, needle: NeedleSite, *, extensions: Sequence[str]
) -> ReferenceCheck:
    """The repoqa check: the gold path and symbol present, and no other file path.

    Example:
        >>> check_repoqa_reference("`run`", NeedleSite("pkg/mod.py", "run"), extensions=(".py",)).problems
        ("names no path 'pkg/mod.py'",)
    """
    return _check_sites(text, (needle,), extensions)


def check_chat_reference(
    text: str, sites: Sequence[NeedleSite], *, extensions: Sequence[str]
) -> ReferenceCheck:
    """The chat check: every gold site's path and symbol present, and no path outside them.

    Example:
        >>> sites = [NeedleSite("a.py", "run"), NeedleSite("b.md", "Usage")]
        >>> check_chat_reference("`a.py` `run`, `b.md` `Usage`", sites, extensions=(".py", ".md")).passed
        True
    """
    return _check_sites(text, sites, extensions)


def check_reference(
    source: ReferenceSource, text: str, *, extensions: Sequence[str]
) -> ReferenceCheck:
    """``text`` checked the way ``source``'s dataset is: repoqa or chat.

    Example:
        >>> check_reference(source, "`pkg/mod.py` — `run`", extensions=(".py",)).passed  # doctest: +SKIP
        True
    """
    sites = [code.site for code in source.code]
    if source.shape is ReferenceShape.REPOQA:
        (needle,) = sites
        return check_repoqa_reference(text, needle, extensions=extensions)
    return check_chat_reference(text, sites, extensions=extensions)


def _check_sites(
    text: str, sites: Sequence[NeedleSite], extensions: Sequence[str]
) -> ReferenceCheck:
    cited = extract_citations(text, extensions=extensions)
    names = extract_dotted_names(text)
    missing = [problem for site in sites for problem in _missing_from(site, cited, names)]
    gold_spellings = frozenset().union(*(gold_aliases(site).paths for site in sites))
    foreign = [
        f"names a file outside the gold: {path!r}" for path in cited if path not in gold_spellings
    ]
    return ReferenceCheck(tuple(missing + foreign))


def _missing_from(site: NeedleSite, cited: Sequence[str], names: frozenset[str]) -> list[str]:
    """What the reference leaves out of ``site``: its path written in full, its symbol."""
    missing = [] if site.path in cited else [f"names no path {site.path!r}"]
    if site.symbol and site.symbol not in names:
        missing.append(f"names no symbol {site.symbol!r} (for {site.path!r})")
    return missing


__all__ = (
    "ReferenceCheck",
    "check_chat_reference",
    "check_reference",
    "check_repoqa_reference",
)
