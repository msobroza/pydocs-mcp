"""``gold_location_evidenced`` — did the run's retrieval NAME the gold files?

The answer-text checks (``gold_recall``, ``gold_substring*``) measure what the
agent SAID. This one measures what its retrieval NAMED — the gold file in a
tool call's arguments or among its distilled result ids — read straight off the
ADR 0009 server trace, so a right answer from a run that never reached the gold
file and a wrong one from a run that did are finally distinguishable. Named is
not read, and breadth is not evidence: both limits are pinned below.

Event lines below mirror the REAL recorder schema byte-for-byte (the committed
captures under ``benchmarks/tests/trajectory/fixtures/trajectories/real/``):
``_event``/``tool``/``args``/``result_ids``, ``result_ids`` null on a failed
call, path-only atoms from ``glob``/``get_references``, empty atoms from
``get_why``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.optimize.rubric.checks import Check, check_registry, evaluate_check, score_checks
from pydocs_eval.optimize.rubric.trajectory_evidence import (
    SERVER_EVENTS_FILENAME,
    TrajectoryEvidenceReadError,
    read_server_tool_events,
)
from tests.optimize._trajectories import make_trajectory

_KIND = "gold_location_evidenced"

#: A committed capture written by the real recorder (a two-call rollout over the
#: widgetlib probe repo) — the drift guard the synthesized lines below cannot be.
_REAL_CAPTURE = (
    Path(__file__).resolve().parents[1]
    / "trajectory"
    / "fixtures"
    / "trajectories"
    / "real"
    / "2ccc132b-b05a-4e2f-a0bf-f81a6d787918"
)


def _task(*, file_set: tuple[str, ...] = ("widgetlib/textutil.py",)) -> EvalTask:
    return EvalTask(
        task_id="repoqa-qa/repo_qa/rec-1",
        query="where does slugify live?",
        gold=GoldAnswer(file_set=file_set, extra={}),
        corpus_source=lambda: None,  # type: ignore[arg-type]  # evidence never touches the corpus
    )


def _tool_event(
    tool: str,
    *,
    args: Mapping[str, object] | None = None,
    result_ids: Sequence[Mapping[str, object]] | None = (),
    seq: int = 1,
) -> dict[str, object]:
    """One ``tool_call`` line in the real recorder's shape."""
    return {
        "_event": "tool_call",
        "seq": seq,
        "tool": tool,
        "args": dict(args or {}),
        "error": None,
        "hit_count": None if result_ids is None else len(result_ids),
        "result_ids": None if result_ids is None else [dict(a) for a in result_ids],
        "trajectory_id": "traj",
        "ts": 1784604468.176574,
    }


def _trace(tmp_path: Path, *lines: object) -> Path:
    """Write ``lines`` as a ``server_events.jsonl`` and return its trace dir."""
    trace_dir = tmp_path / "traj"
    trace_dir.mkdir(parents=True, exist_ok=True)
    body = "".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines)
    (trace_dir / SERVER_EVENTS_FILENAME).write_text(body, encoding="utf-8")
    return trace_dir


def _score(trace_dir: Path, task: EvalTask) -> float:
    return evaluate_check(
        Check(name="evidence", kind=_KIND, fail=None),
        task,
        make_trajectory(trace_dir=trace_dir),
    ).score


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def test_the_kind_is_registered_and_params_free() -> None:
    # Params-free is load-bearing: it is what makes the kind legal in an arm's
    # ``scoring.tracked`` list, which mints every observation with NO params.
    assert _KIND in check_registry.names()
    assert getattr(check_registry.build(_KIND), "required_params", ()) == ()


# --------------------------------------------------------------------------- #
# The args route — a path-shaped argument NAMES the gold file
# --------------------------------------------------------------------------- #


def test_a_read_file_path_argument_evidences_the_gold_file(tmp_path: Path) -> None:
    trace = _trace(
        tmp_path,
        _tool_event("read_file", args={"file_path": "widgetlib/textutil.py", "offset": 1}),
    )
    assert _score(trace, _task()) == 1.0


def test_a_dotted_get_symbol_target_evidences_the_gold_file(tmp_path: Path) -> None:
    # The product derives a module qname by stripping ``.py`` and turning ``/``
    # into ``.``; a symbol target extends that prefix.
    trace = _trace(
        tmp_path,
        _tool_event(
            "get_symbol",
            args={"target": "widgetlib.textutil.slugify", "depth": "source"},
            result_ids=None,
        ),
    )
    assert _score(trace, _task()) == 1.0


def test_a_get_why_target_may_be_a_literal_path(tmp_path: Path) -> None:
    # get_why is the ONE target arg whose validator admits ``/`` (PATH|QNAME).
    trace = _trace(
        tmp_path,
        _tool_event("get_why", args={"targets": ["widgetlib/textutil.py"]}, result_ids=[{}]),
    )
    assert _score(trace, _task()) == 1.0


def test_a_glob_pattern_that_names_the_file_counts_but_a_grep_regex_never_does(
    tmp_path: Path,
) -> None:
    # ``glob.pattern`` is a path glob; ``grep.pattern`` is a Python regex and is
    # semantic-only — reading it as a location would credit any query text.
    named = _trace(tmp_path / "a", _tool_event("glob", args={"pattern": "widgetlib/textutil.py"}))
    regexed = _trace(
        tmp_path / "b",
        _tool_event("grep", args={"pattern": "widgetlib/textutil.py", "-n": True}),
    )
    assert _score(named, _task()) == 1.0
    assert _score(regexed, _task()) == 0.0


def test_a_search_query_naming_the_file_is_not_evidence(tmp_path: Path) -> None:
    # ``search_codebase.query`` is free natural language: crediting it would
    # measure the question, not the retrieval.
    trace = _trace(
        tmp_path,
        _tool_event("search_codebase", args={"query": "widgetlib/textutil.py", "scope": "project"}),
    )
    assert _score(trace, _task()) == 0.0


# --------------------------------------------------------------------------- #
# The surfaced route — an atom in ``result_ids`` names the gold file
# --------------------------------------------------------------------------- #


def test_a_surfaced_result_path_evidences_the_gold_file(tmp_path: Path) -> None:
    trace = _trace(
        tmp_path,
        _tool_event(
            "search_codebase",
            args={"query": "slugify", "scope": "project"},
            result_ids=[
                {"id": "27", "path": "tests/test_textutil.py", "qualified_name": "tests.x"},
                {"id": "50", "path": "widgetlib/textutil.py", "start_line": 10},
            ],
        ),
    )
    assert _score(trace, _task()) == 1.0


def test_a_surfaced_qualified_name_evidences_the_gold_file(tmp_path: Path) -> None:
    # Atoms carry a dotted qualified_name as well as a path; either names the file.
    trace = _trace(
        tmp_path,
        _tool_event(
            "search_codebase",
            args={"query": "slugify"},
            result_ids=[{"id": "50", "qualified_name": "widgetlib.textutil.slugify"}],
        ),
    )
    assert _score(trace, _task()) == 1.0


# --------------------------------------------------------------------------- #
# Breadth is not evidence — the surfaced route is not harvestable
# --------------------------------------------------------------------------- #


class TestBreadthIsNotEvidence:
    """One broad call must not bank the check's weight without localizing.

    This is an optimizer FITNESS term, so anything a candidate skill can spell
    once and harvest on every sample is free mass. Repo-wide enumeration is the
    cheapest such move.
    """

    def test_a_repo_wide_glob_surfacing_the_gold_file_is_not_evidence(self, tmp_path: Path) -> None:
        trace = _trace(
            tmp_path,
            _tool_event(
                "glob",
                args={"pattern": "**/*"},
                result_ids=[{"path": "widgetlib/textutil.py"}, {"path": "widgetlib/db.py"}],
            ),
        )
        assert _score(trace, _task()) == 0.0

    def test_a_glob_whose_pattern_names_the_file_still_scores(self, tmp_path: Path) -> None:
        # Only the SURFACED route is dropped for enumerators — a pattern that
        # literally names the gold file is a genuine localization.
        trace = _trace(
            tmp_path,
            _tool_event(
                "glob",
                args={"pattern": "widgetlib/textutil.py"},
                result_ids=[{"path": "widgetlib/textutil.py"}],
            ),
        )
        assert _score(trace, _task()) == 1.0

    def test_a_whole_package_overview_is_not_evidence(self, tmp_path: Path) -> None:
        trace = _trace(
            tmp_path,
            _tool_event(
                "get_overview",
                args={"package": "__project__"},
                result_ids=[{"id": "widgetlib.textutil", "qualified_name": "widgetlib.textutil"}],
            ),
        )
        assert _score(trace, _task()) == 0.0

    def test_only_a_calls_ranked_head_is_credited(self, tmp_path: Path) -> None:
        # ``grep(pattern='.', output_mode='files_with_matches', head_limit=10000)``
        # would otherwise name every file in the repo in ONE call.
        listed = [{"path": f"widgetlib/mod_{i}.py"} for i in range(12)]
        buried = _trace(
            tmp_path / "buried",
            _tool_event(
                "grep",
                args={"pattern": ".", "output_mode": "files_with_matches"},
                result_ids=[*listed, {"path": "widgetlib/textutil.py"}],
            ),
        )
        headed = _trace(
            tmp_path / "headed",
            _tool_event(
                "grep",
                args={"pattern": "slugify", "output_mode": "files_with_matches"},
                result_ids=[{"path": "widgetlib/textutil.py"}, *listed],
            ),
        )
        assert _score(buried, _task()) == 0.0
        assert _score(headed, _task()) == 1.0


def test_an_absolute_surfaced_path_evidences_the_relative_gold_path(tmp_path: Path) -> None:
    # Dependency files surface ABSOLUTE display paths; gold is repo-relative.
    trace = _trace(
        tmp_path,
        _tool_event("read_file", args={"file_path": "/abs/checkout/widgetlib/textutil.py"}),
    )
    assert _score(trace, _task()) == 1.0


# --------------------------------------------------------------------------- #
# What must NOT count
# --------------------------------------------------------------------------- #


def test_unrelated_calls_score_zero(tmp_path: Path) -> None:
    trace = _trace(
        tmp_path,
        _tool_event("read_file", args={"file_path": "widgetlib/inventory.py"}, seq=1),
        _tool_event(
            "glob", args={"pattern": "*.md"}, result_ids=[{"path": "docs/readme.md"}], seq=2
        ),
    )
    assert _score(trace, _task()) == 0.0


def test_a_partial_path_component_never_matches(tmp_path: Path) -> None:
    # The suffix is compared COMPONENT-wise: 'src/db.py' must not be evidenced
    # by 'src/dbx.py', which a plain string-suffix test would accept.
    trace = _trace(tmp_path, _tool_event("read_file", args={"file_path": "src/dbx.py"}))
    assert _score(trace, _task(file_set=("src/db.py",))) == 0.0


def test_a_dotted_prefix_stops_at_a_component_boundary(tmp_path: Path) -> None:
    trace = _trace(tmp_path, _tool_event("get_symbol", args={"target": "widgetlib.textutils.x"}))
    assert _score(trace, _task()) == 0.0


def test_a_nested_same_suffix_copy_does_count_as_evidence(tmp_path: Path) -> None:
    # DOCUMENTED acceptance, not an oversight: the path suffix is unanchored so
    # an ABSOLUTE captured path matches repo-relative gold, and no checkout root
    # is available here to anchor against. A vendored duplicate is the price.
    trace = _trace(tmp_path, _tool_event("read_file", args={"file_path": "vendor/widgetlib/db.py"}))
    assert _score(trace, _task(file_set=("widgetlib/db.py",))) == 1.0


class TestLayoutIndependence:
    """Identical agent behavior must score identically on any repo layout.

    The product re-roots a module qname on the discovered package root, so the
    recorded qname of ``src/widgetlib/textutil.py`` is ``widgetlib.textutil``.
    Matching only the full-path spelling made a direct, correct symbol
    interrogation score 1.0 on a flat repo and 0.0 on a ``src/`` one.
    """

    def test_a_dotted_target_matches_gold_under_a_src_layout(self, tmp_path: Path) -> None:
        trace = _trace(
            tmp_path,
            _tool_event(
                "get_references",
                args={"target": "widgetlib.textutil.slugify", "direction": "callers"},
                result_ids=[{"path": "tests/test_textutil.py", "start_line": 1}],
            ),
        )
        assert _score(trace, _task(file_set=("src/widgetlib/textutil.py",))) == 1.0

    def test_a_package_target_matches_its_init_module(self, tmp_path: Path) -> None:
        # ``pkg/__init__.py`` IS ``pkg`` — the product pops the trailing segment.
        trace = _trace(tmp_path, _tool_event("get_symbol", args={"target": "widgetlib"}))
        assert _score(trace, _task(file_set=("python/widgetlib/__init__.py",))) == 1.0

    def test_a_different_top_level_package_never_matches(self, tmp_path: Path) -> None:
        # Leading components are tolerated on the GOLD side (unknown package
        # root) but never on the candidate: a recorded qname is already rooted.
        trace = _trace(tmp_path, _tool_event("get_symbol", args={"target": "other.textutil.x"}))
        assert _score(trace, _task(file_set=("src/widgetlib/textutil.py",))) == 0.0


def test_non_tool_call_lines_are_ignored(tmp_path: Path) -> None:
    # The server file also carries the header and suggestion records, and a
    # CLIENT-observed line would be a foreign shape entirely; only ``tool_call``
    # events are evidence.
    trace = _trace(
        tmp_path,
        {"_event": "trace_header", "trajectory_id": "traj", "args": {"file_path": "x"}},
        {"_event": "suggestion_fired", "seq": 1, "result_ids": [{"path": "widgetlib/textutil.py"}]},
        {
            "_event": "client_tool_call",
            "tool": "read_file",
            "args": {"file_path": "widgetlib/textutil.py"},
        },
    )
    assert _score(trace, _task()) == 0.0


def test_a_failed_call_with_null_result_ids_is_tolerated(tmp_path: Path) -> None:
    # ``record_tool_failure`` writes ``result_ids: null`` — iterating it would
    # crash the whole measurement on one service error.
    trace = _trace(
        tmp_path,
        _tool_event("get_context", args={"targets": ["widgetlib.calculator"]}, result_ids=None),
    )
    assert _score(trace, _task()) == 0.0


# --------------------------------------------------------------------------- #
# Fractions, free passes, absent traces
# --------------------------------------------------------------------------- #


def test_the_score_is_the_evidenced_fraction_of_the_gold_set(tmp_path: Path) -> None:
    trace = _trace(
        tmp_path,
        _tool_event("read_file", args={"file_path": "widgetlib/textutil.py"}, seq=1),
        _tool_event(
            "get_references",
            args={"target": "widgetlib.textutil.slugify", "direction": "callers"},
            result_ids=[{"path": "tests/test_textutil.py", "start_line": 1}],
            seq=2,
        ),
    )
    gold = ("widgetlib/textutil.py", "tests/test_textutil.py", "widgetlib/inventory.py", "a/b.py")
    assert _score(trace, _task(file_set=gold)) == 0.5


def test_an_empty_gold_set_passes_free(tmp_path: Path) -> None:
    # The ``gold_recall`` precedent: no evidence to check is not a failure, and
    # scoring 0.0 would tax every corpus whose gold carries no file set.
    trace = _trace(tmp_path, _tool_event("read_file", args={"file_path": "x.py"}))
    assert _score(trace, _task(file_set=())) == 1.0


def test_an_absent_trace_scores_zero_without_raising() -> None:
    # The reader contract (ADR 0009): a missing file is a legitimate absence, so
    # the check degrades to "nothing evidenced" rather than exploding a run.
    # This is exactly the synthetic trajectory the tracked drift-guard executes.
    assert _score(Path("nowhere") / "traj", _task()) == 0.0


def test_a_trace_dir_that_is_a_real_directory_without_the_file_scores_zero(
    tmp_path: Path,
) -> None:
    # ``dry_doubles.dry_trajectory`` sets ``trace_dir=Path('.')`` — the CWD,
    # which DOES exist. Testing the directory instead of the file would read
    # whatever happens to sit there.
    assert _score(tmp_path, _task()) == 0.0


# --------------------------------------------------------------------------- #
# The reader
# --------------------------------------------------------------------------- #


class TestRecorderContract:
    """The format coupling, pinned against the PRODUCT — not against itself.

    Every discriminator below is a copy of the recorder's, so a rename there
    would silently return zero events, score every sample 0.0, and turn the
    0.25 evidence term of three shipped rubric sections into a constant while
    the synthetic tests above stayed green.
    """

    def test_the_filename_matches_the_product_writers(self) -> None:
        trace_writer = pytest.importorskip("pydocs_mcp.observability.trace_writer")
        assert SERVER_EVENTS_FILENAME == trace_writer.SERVER_EVENTS_FILENAME

    def test_a_committed_real_capture_still_reads_as_evidence(self) -> None:
        # A REAL recorder capture, not a synthesized line: its search_codebase
        # results and its get_symbol target both name widgetlib/textutil.py.
        events = read_server_tool_events(_REAL_CAPTURE)
        assert [e["tool"] for e in events] == ["search_codebase", "get_symbol"]
        assert _score(_REAL_CAPTURE, _task()) == 1.0


class TestReader:
    def test_a_missing_file_reads_as_no_events(self, tmp_path: Path) -> None:
        assert read_server_tool_events(tmp_path / "absent") == ()

    def test_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        trace = _trace(tmp_path, _tool_event("read_file"), "", "   ")
        assert len(read_server_tool_events(trace)) == 1

    def test_a_malformed_line_fails_loud_naming_the_file_and_line(self, tmp_path: Path) -> None:
        trace = _trace(tmp_path, _tool_event("read_file"), "{not json")
        with pytest.raises(TrajectoryEvidenceReadError) as exc:
            read_server_tool_events(trace)
        assert SERVER_EVENTS_FILENAME in str(exc.value)
        assert "line 2" in str(exc.value)


# --------------------------------------------------------------------------- #
# Verdict math — the shipped deterministic composite
# --------------------------------------------------------------------------- #


class TestDeterministicCompositeWeights:
    """The shipped {gold_recall 0.75, gold_location_evidenced 0.25} apportionment."""

    @staticmethod
    def _composite(*, answer: str, trace_dir: Path) -> float:
        checks = (
            Check(name="gold_recall", kind="gold_recall", weight=0.75, required=False, fail=None),
            Check(name="location_evidence", kind=_KIND, weight=0.25, required=False, fail=None),
        )
        return score_checks(
            checks, _task(), make_trajectory(answer=answer, trace_dir=trace_dir)
        ).score

    def test_a_wrong_answer_with_full_evidence_scores_the_evidence_weight(
        self, tmp_path: Path
    ) -> None:
        trace = _trace(
            tmp_path, _tool_event("read_file", args={"file_path": "widgetlib/textutil.py"})
        )
        assert self._composite(answer="no idea", trace_dir=trace) == 0.25

    def test_a_right_answer_with_no_evidence_scores_the_recall_weight(self, tmp_path: Path) -> None:
        trace = _trace(tmp_path, _tool_event("search_codebase", args={"query": "slugify"}))
        assert self._composite(answer="widgetlib/textutil.py", trace_dir=trace) == 0.75
