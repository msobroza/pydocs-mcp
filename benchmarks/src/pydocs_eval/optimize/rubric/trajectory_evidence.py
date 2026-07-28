"""Trajectory-grounded evidence — was the gold file NAMED by the run's retrieval?

Every deterministic metric shipped before this one reads ``trajectory.answer``:
``gold_recall`` and the ``gold_substring*`` gates measure what the agent SAID.
That leaves the two most interesting samples indistinguishable — a right answer
produced by a run whose retrieval never reached the gold file, and a wrong one
produced by a run whose retrieval did. ``gold_location_evidenced`` closes that
by reading the ADR 0009 server trace directly: the fraction of
``gold.file_set`` that ANY server-recorded tool call named, through either
route the trace exposes:

* **args** — a path- or target-shaped ARGUMENT names the file (``read_file``'s
  ``file_path``, ``get_symbol`` / ``get_references``'s dotted ``target``, a
  ``glob`` pattern that literally names it);
* **surfaced** — an atom in the call's ``result_ids`` names it (the distilled
  ``path`` / ``id`` / ``node_id`` / ``qualified_name`` identifiers).

Both routes are deliberate. Args-only would miss the common shape where a
semantic ``search_codebase`` query surfaces the gold file the agent then reads
about; surfaced-only would miss a direct ``read_file`` of a path the agent
already knew. Neither route is a proxy for the other.

**What the surfaced route does NOT claim.** ``result_distiller``'s own schema
note is explicit that ``result_ids`` presence "must NOT be read as 'shown to
the model'" — items can exceed the token-budgeted text envelope. So this check
measures *the run's retrieval named the file*, never *the model read it*; the
prose here, in the shipped ``checks:`` blocks and in the CHANGELOG is worded to
that weaker claim on purpose. Judging model-visible surfacing would mean
dereferencing the text blob, which is a different (and much more expensive)
measurement.

**Breadth is not evidence.** Crediting every atom of every call would make the
metric harvestable: one ``glob('**/*')`` enumerates the repo, so a candidate
skill whose first instruction is "list everything" would bank the check's whole
weight on every sample without retrieving anything. Two rules keep the surfaced
route a localization signal — the pure enumerators are excluded from it
entirely (they still earn credit through their location ARGS), and only the
ranked head of any result list is credited. See ``_ENUMERATING_TOOLS`` /
``_CREDITED_RESULT_ATOMS``.

**Why this module owns its own trace reader.** The rubric core is base-install
and must not import ``pydocs_mcp`` at runtime (ADR 0009/0010's 2026-07-27
amendment: base modules stay *format-coupled, not type-coupled*). So the two
discriminators below mirror ``observability.trace_writer`` /
``trace_recorder`` byte-for-byte instead of importing them — the same
contract-not-import rule ``trajectory/merge.py`` already follows for the same
file. A drift breaks the tests, loudly.

**Semantic args are excluded by construction.** ``search_codebase.query``,
``grep.pattern`` and ``get_why.query`` are free text a caller can spell
anything into; crediting them would measure the QUESTION rather than the
retrieval. The per-tool key map below is an allowlist for exactly that reason.

**Known undercount, deliberately not fixed here.** Two tools surface no atom
this check can use: ``get_why`` distills to ``{}`` per item and
``get_references`` surfaces ``path``/span only — their qualified names and
affected files sit outside ``result_distiller._RESULT_ID_KEYS``. So a run that
localizes purely through those two reads lower than it earned (the args route
covers them only partially). Widening what the distiller keeps is an ADR 0009
WRITE-SIDE change — it moves the recorded schema and every downstream consumer
— so this module is written against the schema as recorded today. Open item 8
in ``docs/superpowers/specs/2026-07-27-harness-run-contract-design.md`` §10.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.optimize.rubric.checks import check_registry

if TYPE_CHECKING:
    # WHY TYPE_CHECKING only: like ``gates`` / ``checks``, this module only
    # READS attributes off the trajectory, so the contract type is a typing
    # concern and never an import-time one.
    from pydocs_mcp.harness.core.run_contract import Trajectory

__all__ = [
    "SERVER_EVENTS_FILENAME",
    "GoldLocationEvidenced",
    "TrajectoryEvidenceReadError",
    "read_server_tool_events",
]

# Raw server-recorder discriminators (CONTRACT, not imports — see the module
# docstring). Byte-identical to ``observability.trace_writer`` /
# ``trace_recorder`` and to ``trajectory/merge.py``'s own copy.
SERVER_EVENTS_FILENAME = "server_events.jsonl"
_EVENT_DISCRIMINATOR = "_event"
_RAW_TOOL_EVENT = "tool_call"

# WHICH argument of WHICH tool can NAME a location. An allowlist, per tool,
# because the same key means different things across the nine: ``glob.pattern``
# is a path glob while ``grep.pattern`` is a Python regex. Selectors (``project``,
# ``package``, ``scope``), knobs (``limit``, ``depth``, ``offset``) and free-text
# queries are absent by design.
_ARG_LOCATION_KEYS: Mapping[str, tuple[str, ...]] = {
    "read_file": ("file_path",),
    # ``grep.path`` / ``glob.path`` are DIRECTORY prefixes, so they match a gold
    # FILE only in the degenerate case; they are listed because a caller may
    # spell a full path there and the cost of reading them is nil.
    "grep": ("path", "glob"),
    "glob": ("path", "pattern"),
    "get_symbol": ("target",),
    "get_references": ("target",),
    "get_context": ("targets",),
    # The one target arg whose validator admits ``/`` — documented PATH|QNAME.
    "get_why": ("targets",),
}

# The distilled identifier atoms a result item can carry (``result_distiller``'s
# ``_RESULT_ID_KEYS`` minus the pure span fields, which name no location).
_RESULT_ATOM_KEYS = ("path", "id", "node_id", "qualified_name")

# Tools whose ``result_ids`` are an ENUMERATION rather than a ranked retrieval:
# ``glob`` lists whatever the pattern matched and ``get_overview`` lists a whole
# package's modules, so both surface the gold file for free the moment they are
# called broadly (``glob('**/*')``, ``get_overview('__project__')``). Their
# surfaced route is dropped, not their args route — a ``glob`` whose PATTERN
# names the gold file is a genuine localization and still scores.
_ENUMERATING_TOOLS = frozenset({"glob", "get_overview"})

# How many result atoms of one call can evidence anything. The rest of a broad
# call's list is breadth, not localization: ``grep(pattern='.',
# output_mode='files_with_matches', head_limit=10000)`` would otherwise name
# every file in the repo in ONE call (the product's ceilings are
# ``max_head_limit`` 10000 / ``search.output.max_limit`` 1000). Results are
# emitted in rank order, so the head is also the part a run plausibly acted on;
# a gold file ranked below it has to be reached some other way to score.
_CREDITED_RESULT_ATOMS = 10

# Python modules lose this suffix when the indexer derives their dotted qname;
# ``.md`` / ``.ipynb`` keep theirs as a trailing dotted segment, which is why
# only this one is stripped.
_PY_SUFFIX = ".py"

# A package's ``__init__`` module IS the package: the product pops this segment
# when deriving the qname, so ``pkg/__init__.py`` is ``pkg``.
_INIT_STEM = "__init__"


class TrajectoryEvidenceReadError(ValueError):
    """A ``server_events.jsonl`` line this reader cannot parse.

    Mirrors the product ``trace_reader``'s LOUD malformed-line policy: a trace
    that exists must be readable, because silently dropping a line would
    silently lower an evidence score and look like an agent behavior change.
    """


def read_server_tool_events(trace_dir: Path) -> tuple[Mapping[str, object], ...]:
    """The ``tool_call`` records in ``trace_dir``'s server capture, in file order.

    A MISSING file reads as no events — the ADR 0009 legitimate absence (a
    trace-disabled or never-started rollout); it is the caller's job to decide
    what that means. Blank lines are skipped and non-``tool_call`` records
    (the header, ``suggestion_fired``) are filtered out.

    Raises:
        TrajectoryEvidenceReadError: a non-blank line is not a JSON object,
            naming the file and the 1-indexed line number.

    Example:
        >>> read_server_tool_events(Path("does") / "not" / "exist")
        ()
    """
    path = Path(trace_dir) / SERVER_EVENTS_FILENAME
    if not path.is_file():
        return ()
    events: list[Mapping[str, object]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        payload = _decode_line(line, path=path, number=number)
        if payload is not None and payload.get(_EVENT_DISCRIMINATOR) == _RAW_TOOL_EVENT:
            events.append(payload)
    return tuple(events)


def _decode_line(line: str, *, path: Path, number: int) -> Mapping[str, object] | None:
    """Decode one trace line; ``None`` for a blank one.

    Raises:
        TrajectoryEvidenceReadError: the line is not a JSON object.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise TrajectoryEvidenceReadError(
            f"{path}: line {number} is not valid JSON ({exc}); a server trace that "
            "exists must be readable end to end"
        ) from exc
    if not isinstance(payload, dict):
        raise TrajectoryEvidenceReadError(
            f"{path}: line {number} decoded to {type(payload).__name__}, expected a JSON object"
        )
    return payload


@check_registry.register("gold_location_evidenced")
@dataclass(frozen=True, slots=True)
class GoldLocationEvidenced:
    """Fraction of ``gold.file_set`` any server tool call named or surfaced.

    Params-free by contract — that is what makes the kind legal in an arm's
    ``scoring.tracked`` list, which mints every observation with no params.

    An EMPTY gold file set scores ``1.0``, mirroring ``gold_recall``'s vacuous
    pass: no evidence to check is not a failure, and taxing every corpus whose
    gold carries no file set would make the metric non-comparable across them.
    """

    def __call__(
        self, task: EvalTask, trajectory: Trajectory, params: Mapping[str, object]
    ) -> float:
        _ = params
        gold = tuple(task.gold.file_set)
        if not gold:
            return 1.0
        located = tuple(_located_strings(read_server_tool_events(trajectory.trace_dir)))
        if not located:
            return 0.0
        return sum(1 for path in gold if _is_evidenced(path, located)) / len(gold)


def _located_strings(events: Sequence[Mapping[str, object]]) -> Iterator[str]:
    """Every location-shaped string the events name or surface."""
    for event in events:
        yield from _arg_strings(event)
        yield from _surfaced_strings(event)


def _arg_strings(event: Mapping[str, object]) -> Iterator[str]:
    """The allowlisted location args of one call (semantic args excluded)."""
    args = event.get("args")
    keys = _ARG_LOCATION_KEYS.get(str(event.get("tool", "")), ())
    if not keys or not isinstance(args, Mapping):
        return
    for key in keys:
        yield from _strings_of(args.get(key))


def _surfaced_strings(event: Mapping[str, object]) -> Iterator[str]:
    """The identifier atoms one call surfaced, bounded to its ranked head.

    ``result_ids`` is ``null`` on a recorded FAILURE (``record_tool_failure``)
    and an atom is ``{}`` when the item carried no identifier (``get_why``), so
    both shapes are tolerated rather than iterated blindly. Enumerating tools
    are skipped and the list is truncated — see ``_ENUMERATING_TOOLS`` /
    ``_CREDITED_RESULT_ATOMS`` for why breadth must not buy evidence.
    """
    atoms = event.get("result_ids")
    if str(event.get("tool", "")) in _ENUMERATING_TOOLS:
        return
    if not isinstance(atoms, Sequence) or isinstance(atoms, (str, bytes)):
        return
    for atom in atoms[:_CREDITED_RESULT_ATOMS]:
        if isinstance(atom, Mapping):
            for key in _RESULT_ATOM_KEYS:
                yield from _strings_of(atom.get(key))


def _strings_of(value: object) -> Iterator[str]:
    """Non-empty strings in ``value``, which may be a scalar or a list."""
    if isinstance(value, str):
        if value:
            yield value
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if isinstance(item, str) and item:
                yield item


def _is_evidenced(gold_path: str, located: Sequence[str]) -> bool:
    """Whether any located string names ``gold_path`` by path or by dotted qname."""
    gold_parts = _path_parts(gold_path)
    prefixes = _dotted_module_prefixes(gold_parts)
    return any(
        _path_suffix_matches(gold_parts, candidate) or _dotted_prefix_matches(prefixes, candidate)
        for candidate in located
    )


def _path_parts(value: str) -> tuple[str, ...]:
    """Path components, absolute-root and ``.`` segments dropped."""
    return tuple(part for part in PurePosixPath(value).parts if part not in ("/", "."))


def _path_suffix_matches(gold_parts: tuple[str, ...], candidate: str) -> bool:
    """Whole-component suffix match.

    Component-wise, never a string suffix: gold ``src/db.py`` IS evidenced by
    ``/abs/checkout/src/db.py`` and is NOT evidenced by ``src/dbx.py``, which a
    plain ``endswith`` would accept.

    The suffix is deliberately UNANCHORED — a candidate may carry any leading
    components — because dependency files surface absolute display paths while
    gold is repo-relative, and no checkout root is available here to anchor
    against. The accepted cost is a nested same-suffix duplicate: on a repo that
    vendors a dependency, gold ``foo/parser.py`` is evidenced by
    ``third_party/foo/parser.py`` too. Anchoring instead would break every
    absolute-path capture, which is the common case; the duplicate is the rare
    one.
    """
    candidate_parts = _path_parts(candidate)
    if not gold_parts or len(candidate_parts) < len(gold_parts):
        return False
    return candidate_parts[-len(gold_parts) :] == gold_parts


def _dotted_module_prefixes(gold_parts: tuple[str, ...]) -> tuple[str, ...]:
    """Every dotted module qname the indexer could derive from a gold path.

    Not one qname, because the product does not derive it from the full path:
    ``ast_python._module_from_path`` re-roots on the discovered PYTHON PACKAGE
    ROOT (``_python_package_root``, the parent of the topmost ``__init__.py``)
    and pops a trailing ``__init__``. So ``src/widgetlib/textutil.py`` is
    recorded as ``widgetlib.textutil``, never ``src.widgetlib.textutil`` — and
    HOW MANY leading directories were dropped is invisible from the gold path
    alone. Matching only the full-path spelling made an agent that interrogates
    the gold symbol directly score 1.0 on a flat repo and 0.0 on a ``src/``- or
    ``python/``-layout one, for identical behavior.

    Every trailing run of components is therefore a candidate qname, mirroring
    the leading-component tolerance ``_path_suffix_matches`` already has:
    ``src/db.py`` → ``("src.db", "db")``, ``pkg/__init__.py`` → ``("pkg",)``,
    while ``pkg/foo.md`` keeps its suffix as a trailing segment
    (``("pkg.foo.md", "foo.md")``).
    """
    parts = list(gold_parts)
    if parts and parts[-1].endswith(_PY_SUFFIX):
        parts[-1] = parts[-1][: -len(_PY_SUFFIX)]
        if parts[-1] == _INIT_STEM:
            parts.pop()
    parts = [part for part in parts if part]
    return tuple(".".join(parts[index:]) for index in range(len(parts)))


def _dotted_prefix_matches(prefixes: Sequence[str], candidate: str) -> bool:
    """Whether ``candidate`` is one of the module qnames, or a symbol inside it.

    The trailing ``.`` boundary is what keeps ``widgetlib.textutil`` from being
    evidenced by ``widgetlib.textutils.x``. Extra leading components on the
    CANDIDATE are never tolerated (unlike the path route): a recorded qname is
    already rooted at its package, so ``other.textutil`` is a different module.
    """
    return any(
        bool(prefix) and (candidate == prefix or candidate.startswith(prefix + "."))
        for prefix in prefixes
    )
