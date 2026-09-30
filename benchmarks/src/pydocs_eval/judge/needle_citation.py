"""Code-first ``needle cited``: what an answer cites, matched exactly against the gold.

The primary correctness guard of ADR 0025, computed before any judge call.

Example:
    >>> extract_citations("See `src/a.py:3-9` and README.md.", extensions=(".py", ".md"))
    ('src/a.py', 'README.md')
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache

from pydocs_eval.trajectory.ask_outcome import ASK_NOT_CONFIRMED_LABEL

# The pseudo-qrel extractor's path rule (``datasets._citations``), generalised to
# any extension set. The boundary after the extension stops a dotted module name
# from citing a file: 'matplotlib.pyplot' is not 'matplotlib.py'.
_PATH_CHARS = r"[A-Za-z0-9_\-./]"


@lru_cache(maxsize=8)
def _citation_pattern(extensions: tuple[str, ...]) -> re.Pattern[str]:
    # Longest first, so one extension that prefixes another never wins by order.
    alternatives = "|".join(
        re.escape(ext.removeprefix(".")) for ext in sorted(extensions, key=len, reverse=True)
    )
    # Anchored at the start of a run of path characters: unanchored, a long run
    # with no citable extension was rescanned from every position (a 120 KB
    # runaway answer took 265 s). A greedy match already ends at the run's last
    # citable extension, so no match ever started mid-run: the matches are the same.
    return re.compile(
        rf"(?<!{_PATH_CHARS})(?P<path>{_PATH_CHARS}+\.(?:{alternatives}))(?![A-Za-z0-9_])"
    )


def extract_citations(answer: str, *, extensions: Sequence[str]) -> tuple[str, ...]:
    """Every distinct file path ``answer`` cites, in first-cited order."""
    pattern = _citation_pattern(tuple(extensions))
    return tuple(dict.fromkeys(match.group("path") for match in pattern.finditer(answer)))


_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
# A chain starts where no identifier character or dot precedes it.
_CHAIN = re.compile(rf"(?<![A-Za-z0-9_.]){_IDENTIFIER}(?:\.{_IDENTIFIER})*")
_CALLED = re.compile(rf"(?<![A-Za-z0-9_.])({_IDENTIFIER})\(")
_CODE = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)
# A token holding a slash is a path or a URL: its segments are not names.
# Anchored at a token's start, so a long token with no slash is scanned once,
# not from every position (``\S*/\S*`` took 42-81 s on a 120 KB runaway token).
_PATH_TOKEN = re.compile(r"(?<!\S)[^\s/]*+/\S*")
# A prose word only code would spell: an underscore, or a capital inside the
# word. "search" is English; "get_params" and "MaxSimScorer" are not.
_CODE_SHAPED = re.compile(r"_|[a-z0-9][A-Z]")
# A name written against its file (``a/b.py::fn``, ``a/b.py:Cls.fn``) or as the
# text of a link to one (``[fn](a/b.py#L3)``) is code whatever its shape. Its
# token holds a slash and is blanked as a path, so it is read before that.
_NAME_ON_A_PATH = re.compile(rf"\.py::?({_IDENTIFIER}(?:\.{_IDENTIFIER})*)")
# The same, nested as pytest writes it (``a/b.py::Cls::fn``). Only a one-function
# needle reads it (#410): widening _NAME_ON_A_PATH would move every multi-site
# score and the Jev audit's candidates, which the owner kept as they were.
_NESTED_NAME_ON_A_PATH = re.compile(rf"\.py::?({_IDENTIFIER}(?:(?:\.|::){_IDENTIFIER})*)")
# The link target stops at the next ``[``, which keeps the scan linear.
_NAME_LINKED_TO_A_PATH = re.compile(rf"\[`?({_IDENTIFIER}(?:\.{_IDENTIFIER})*)`?\]\([^)\s\[]+\)")


def extract_dotted_names(answer: str) -> frozenset[str]:
    """Every name ``answer`` writes as code, with every contiguous part of each chain.

    Code means inside backticks or a fenced block, a dotted chain, a called
    name, a word spelled as only code is (snake_case, camelCase), or a name
    written against its file (``a/b.py::fn``, ``[fn](a/b.py#L3)``). A plain
    prose word is not a name: an answer that uses the word "search" does not
    cite a symbol named ``search``, while a bare ``get_params`` does.
    """
    return _names_in_chains([*_written_chains(answer), *_NAME_ON_A_PATH.findall(answer)])


def _written_chains(answer: str) -> list[str]:
    """The chains ``answer`` writes as code, less a name on a path.

    Each reading adds that name with its own regex, flat or nested (#410).
    """
    code = _PATH_TOKEN.sub(" ", " ".join(_CODE.findall(answer)))
    prose = _PATH_TOKEN.sub(" ", _CODE.sub(" ", answer))
    return [
        *_CHAIN.findall(code),
        *(chain for chain in _CHAIN.findall(prose) if _written_as_code(chain)),
        *_CALLED.findall(prose),
        *_NAME_LINKED_TO_A_PATH.findall(answer),
    ]


def _names_in_chains(chains: Iterable[str]) -> frozenset[str]:
    """Every name the chains spell: each chain's contiguous parts (:func:`_chain_parts`)."""
    # Performance: each distinct chain is expanded once. The chat fixture's answers
    # write 1,109 chains, 470 of them distinct, and a runaway ``a.py::a.a…`` is found
    # both as a chain and as a name on its path (120 KB: 574 → 300 ms).
    return frozenset(part for chain in set(chains) for part in _chain_parts(chain))


def _one_function_names(answer: str, extensions: Sequence[str]) -> frozenset[str]:
    """The names a one-function needle reads in ``answer`` (#410).

    Two readings differ from :func:`extract_dotted_names`, which every other
    needle reads: a file name is a path and names nothing (``visit.py`` is no
    ``visit``), and a name nested against its file is read whole (pytest's
    ``b.py::Cls::fn`` names ``Cls.fn``).
    """
    written = (chain for chain in _written_chains(answer) if not _is_file_name(chain, extensions))
    nested = (chain.replace("::", ".") for chain in _NESTED_NAME_ON_A_PATH.findall(answer))
    return _names_in_chains([*written, *nested])


def _is_file_name(chain: str, extensions: Sequence[str]) -> bool:
    """Whether ``chain`` is spelled as a file name: one stem and a citable extension (``visit.py``).

    A longer chain is a dotted name, even one ending like a file
    (``requests.models.Response.json``) or holding one (``flask.json.dumps``).
    ``Response.json`` alone reads as a file; no repoqa needle is named so.
    """
    parts = chain.split(".")
    return len(parts) == 2 and f".{parts[1]}" in extensions


def _written_as_code(prose_chain: str) -> bool:
    """A chain in prose is code when it is dotted or spelled as only code is."""
    return "." in prose_chain or _CODE_SHAPED.search(prose_chain) is not None


# Longer than any module-qualified name a gold site spells. Without a bound the
# enumeration below is cubic in a chain's length: a runaway answer repeating
# ``a.a.a…`` for 12 KB took 256 s to score (the #373 simplify pass).
_MAX_NAME_PARTS = 16


def _chain_parts(chain: str) -> set[str]:
    """Every contiguous part of ``chain``: ``a.b.c`` → ``a``, ``a.b``, ``b``, ``b.c``, ``c``, …

    Each is a name the answer wrote: ``pkg.mod.Class.method`` names the module,
    the class and the method. Parts longer than :data:`_MAX_NAME_PARTS` are no name.
    """
    parts = chain.split(".")
    return {
        ".".join(parts[start:end])
        for start in range(len(parts))
        for end in range(start + 1, min(start + _MAX_NAME_PARTS, len(parts)) + 1)
    }


@dataclass(frozen=True, slots=True)
class NeedleSite:
    """One place an answer must name: a repo path and, when the gold has one, its symbol."""

    path: str
    symbol: str = ""


@dataclass(frozen=True, slots=True)
class GoldAliases:
    """Every spelling of one site an answer may write: as a file, or as a name."""

    paths: frozenset[str]
    names: frozenset[str]


_SOURCE_PREFIX = "src/"
_PYTHON_SUFFIX = ".py"
_PACKAGE_INIT = "__init__"
# Every package has one, so the bare file name cannot tell which one is meant.
_AMBIGUOUS_BASENAMES = frozenset({"__init__.py", "__main__.py"})


def gold_aliases(site: NeedleSite) -> GoldAliases:
    """The file spellings of ``site`` and the names it goes by.

    Paths: the path itself, without its ``src/`` prefix, and each trailing part
    down to the bare file name (never a bare ``__init__.py``). Names: the
    module the path is, and the symbol bare, qualified and under the module.
    """
    return GoldAliases(paths=_path_aliases(site.path), names=_name_aliases(site))


def _path_aliases(path: str) -> frozenset[str]:
    unprefixed = path.removeprefix(_SOURCE_PREFIX)
    return frozenset({path, unprefixed, *_trailing_paths(path)} - _AMBIGUOUS_BASENAMES)


def _name_aliases(site: NeedleSite) -> frozenset[str]:
    """The module the path is, and every spelling of the site's symbol."""
    return frozenset(_symbol_aliases(site) | ({module_of(site.path)} - {""}))


def _symbol_aliases(site: NeedleSite) -> frozenset[str]:
    """The spellings that name the site's symbol: bare, qualified and under its module."""
    module = module_of(site.path)
    symbols = _bare_symbols(site.symbol)
    qualified = {f"{module}.{symbol}" for symbol in symbols} if module else set()
    return frozenset(symbols | qualified)


def _trailing_paths(path: str) -> set[str]:
    """``src/a/b/c.py`` → ``b/c.py``, ``c.py``: the path's trailing parts below its root."""
    parts = path.removeprefix(_SOURCE_PREFIX).split("/")
    return {"/".join(parts[start:]) for start in range(1, len(parts))}


def _bare_symbols(symbol: str) -> set[str]:
    """``Class.method`` → itself and ``method``; nothing for a site without a symbol."""
    return {symbol, symbol.rsplit(".", 1)[-1]} - {""}


def _short_aliases(site: NeedleSite) -> frozenset[str]:
    """The spellings that do not pin a file: trailing path parts and bare symbols."""
    return frozenset(_trailing_paths(site.path) | _bare_symbols(site.symbol))


@dataclass(frozen=True, slots=True)
class _CitableSite:
    """One site of a needle beside the spellings that can cite it within that needle.

    A needle of one symbol is the exception: only its symbol's spellings cite it
    (:func:`_one_symbol_cited`).
    """

    site: NeedleSite
    aliases: GoldAliases


def _citable_sites(sites: Sequence[NeedleSite]) -> tuple[_CitableSite, ...]:
    """Each site with its aliases, less the short ones it shares with a site in another file.

    ``matches_filter`` in four files names none of them on its own: a bare name
    can only credit a site when no other file of the same needle answers to it.
    Sites in one file keep sharing their short names — citing the file cites them all.
    """
    paired = [_CitableSite(site, gold_aliases(site)) for site in sites]
    shared = _spellings_shared_across_files(paired)
    return tuple(
        replace(each, aliases=_without(each.aliases, shared & _short_aliases(each.site)))
        for each in paired
    )


def _spellings_shared_across_files(paired: Sequence[_CitableSite]) -> frozenset[str]:
    """Every spelling that aliases sites in more than one file of the needle."""
    files_by_spelling: dict[str, set[str]] = {}
    for each in paired:
        for spelling in each.aliases.paths | each.aliases.names:
            files_by_spelling.setdefault(spelling, set()).add(each.site.path)
    return frozenset(spelling for spelling, files in files_by_spelling.items() if len(files) > 1)


def _without(alias: GoldAliases, spellings: frozenset[str]) -> GoldAliases:
    return GoldAliases(paths=alias.paths - spellings, names=alias.names - spellings)


def module_of(path: str) -> str:
    """``src/pkg/mod.py`` → ``pkg.mod``, an ``__init__.py`` → its package, else ``""``.

    One rule for the alias builder and for the module a judge request states.

    Example:
        >>> module_of("src/pkg/mod.py"), module_of("pkg/__init__.py"), module_of("README.md")
        ('pkg.mod', 'pkg', '')
    """
    if not path.endswith(_PYTHON_SUFFIX):
        return ""
    parts = path.removeprefix(_SOURCE_PREFIX).removesuffix(_PYTHON_SUFFIX).split("/")
    if parts[-1] == _PACKAGE_INIT:
        parts = parts[:-1]
    return ".".join(parts)


@dataclass(frozen=True, slots=True)
class NeedleCitation:
    """Which gold sites one answer cites, and which of the files it cites are gold.

    ``needle_cited`` needs every site — the guard the acceptance rule reads;
    ``any_site_cited`` and ``gold_site_coverage`` are its companions, and on a
    single site the three coincide. A needle inside one file is a single site
    (:func:`score_needle_citation`), so ``sites_cited`` then holds one flag and
    ``multi_location`` is False. ``cited_files`` counts a file once however many
    spellings cite it.
    """

    sites_cited: tuple[bool, ...]
    cited_files: tuple[str, ...]
    cited_gold_files: tuple[str, ...]
    multi_location: bool

    @property
    def needle_cited(self) -> bool:
        return all(self.sites_cited)

    @property
    def any_site_cited(self) -> bool:
        return any(self.sites_cited)

    @property
    def gold_site_coverage(self) -> float:
        return sum(self.sites_cited) / len(self.sites_cited)

    @property
    def cited_path_precision(self) -> float | None:
        """The share of the files the answer cites that are gold; ``None`` when it cites none."""
        if not self.cited_files:
            return None
        return len(self.cited_gold_files) / len(self.cited_files)


# The line a finalized answer opens its unverified list with: what follows it is
# named, not cited, so it confirms no site.
_NOT_CONFIRMED_LINE = re.compile(rf"^[\s>*_#-]*{re.escape(ASK_NOT_CONFIRMED_LABEL)}", re.MULTILINE)


def score_needle_citation(
    answer: str, sites: Sequence[NeedleSite], *, extensions: Sequence[str]
) -> NeedleCitation:
    """Match what ``answer`` cites against every site of its needle.

    The multi-site rule starts at two gold files (#373: ``gold_file_count ≥ 2``;
    at one file the three numbers coincide): the spans of a needle inside one
    file are one site, cited by any of their spellings — q11's two
    ``release.yml`` spans are both the release workflow. A needle of exactly
    one gold site with a symbol (the repoqa shape: one function) is cited only
    by the symbol's name (:func:`_one_symbol_cited`).

    Raises:
        ValueError: ``sites`` is empty — an empty needle would be cited vacuously.
    """
    if not sites:
        raise ValueError(f"needle sites = {sites!r}, expected at least one site")
    confirmed = _confirmed_part(answer)
    cited = _AnswerCitations(
        paths=extract_citations(confirmed, extensions=extensions),
        names=extract_dotted_names(confirmed),
        one_function_names=_one_function_names(confirmed, extensions),
    )
    citable = _citable_sites(sites)
    gold_paths = frozenset(site.path for site in sites)
    files = _distinct_files((_file_of(path, citable) for path in cited.paths), gold_paths)
    multi_location = is_multi_location(gold_paths)
    flags = _site_flags(citable, cited)
    return NeedleCitation(
        sites_cited=flags if multi_location else (any(flags),),
        cited_files=files,
        cited_gold_files=tuple(file for file in files if file in gold_paths),
        multi_location=multi_location,
    )


@dataclass(frozen=True, slots=True)
class _AnswerCitations:
    """What one answer cites: the file paths it writes and the names it writes as code.

    A one-function needle reads ``one_function_names`` (:func:`_one_function_names`);
    every other needle reads ``names``.
    """

    paths: tuple[str, ...]
    names: frozenset[str]
    one_function_names: frozenset[str]


def is_multi_location(paths: Iterable[str]) -> bool:
    """Whether ``paths`` name more than one file — where every site must be cited.

    Example:
        >>> is_multi_location(["a.py", "a.py"]), is_multi_location(["a.py", "b.md"])
        (False, True)
    """
    return len(set(paths)) > 1


def _confirmed_part(answer: str) -> str:
    """``answer`` up to its ``Not confirmed:`` line, or whole when it has none."""
    match = _NOT_CONFIRMED_LINE.search(answer)
    return answer if match is None else answer[: match.start()]


def _site_flags(citable: Sequence[_CitableSite], cited: _AnswerCitations) -> tuple[bool, ...]:
    """Whether ``cited`` names each site; a needle of one symbol needs that symbol's name."""
    if len(citable) == 1 and citable[0].site.symbol:
        return (_one_symbol_cited(citable[0].site, cited),)
    return tuple(_site_cited(each.aliases, cited) for each in citable)


def _one_symbol_cited(site: NeedleSite, cited: _AnswerCitations) -> bool:
    """The symbol's name — bare, qualified or under its module — and nothing less.

    The file's path, or the bare module, points at the right place but not at
    the answer: "the right file, the wrong function" is the plausible-but-wrong
    stop the correctness guard exists to catch (owner decision on #366,
    2026-09-28, amending spec 9a for one-symbol needles; the spec's Jev
    question on a repoqa needle reads it the same way). A bare file name is
    that path too, even when it is spelled like the function (#410).

    Written limits (#410): a function named like a plain word (``visit``, 12 of
    the 100 repoqa needles) is cited only when written as code, never by the
    prose word. The name beside another file still cites it: a namesake
    elsewhere is Jev's call, not code's. A module whose last part is the
    function's name cites it through that part, as ``glob`` in ``src/glob.py``
    does (no repoqa needle is so named). ``Response.json`` is spelled as a file
    name, so it cites no method ``json`` (:func:`_is_file_name`).
    """
    return bool(_symbol_aliases(site) & cited.one_function_names)


def _site_cited(aliases: GoldAliases, cited: _AnswerCitations) -> bool:
    """A site is cited by any of its names, or by any path spelling of its file."""
    return bool(aliases.names & cited.names) or any(
        _cites_path(path, aliases.paths) for path in cited.paths
    )


def _cites_path(cited: str, aliases: frozenset[str]) -> bool:
    """Exact at path-component granularity: equal to an alias, or ending in a multi-part one.

    The multi-part rule lets an absolute path or a URL cite a file below the repo
    root, while a bare file name elsewhere in the tree (``other/strategies.py``)
    never does. A root-level file (``README.md``) is therefore cited only by its
    bare or ``./`` spelling: an absolute path to it cannot be told from a deeper
    namesake without the repo root.
    """
    path = _normalized(cited)
    return path in aliases or any("/" in alias and path.endswith(f"/{alias}") for alias in aliases)


def _normalized(cited: str) -> str:
    """A cited path without a leading ``./`` or ``/``."""
    return cited.removeprefix("./").lstrip("/")


def _file_of(cited: str, citable: Sequence[_CitableSite]) -> str:
    """The file one cited spelling names: its gold site's path when it cites one, else itself."""
    cited_sites = (each.site.path for each in citable if _cites_path(cited, each.aliases.paths))
    return next(cited_sites, _normalized(cited))


def _distinct_files(files: Iterable[str], gold_paths: frozenset[str]) -> tuple[str, ...]:
    """``files``, each once: a non-gold ``x.py`` beside ``a/x.py`` is that same file.

    A gold path is a whole repo path, never a partial spelling: ``README.md`` at
    the root and ``docs/README.md`` are two files.
    """
    ordered = sorted(set(files), key=lambda each: (-len(each), each))
    return tuple(
        file
        for file in ordered
        if file in gold_paths or not _is_trailing_path_of_any(file, ordered)
    )


def _is_trailing_path_of_any(file: str, others: Sequence[str]) -> bool:
    """Whether a longer path in ``others`` ends with ``file`` at a component boundary."""
    return any(other.endswith(f"/{file}") for other in others)


__all__ = (
    "GoldAliases",
    "NeedleCitation",
    "NeedleSite",
    "extract_citations",
    "extract_dotted_names",
    "gold_aliases",
    "is_multi_location",
    "module_of",
    "score_needle_citation",
)
