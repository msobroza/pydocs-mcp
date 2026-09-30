"""The reference writer's rounds: keep what passes, regenerate what fails, fall back on a failed job.

The fallback model is asked only for a row whose primary job failed — never for
a row whose batch is merely still running (collected later by id, so it is not
paid for twice) and never for a row that failed its code check (regenerated on
the same model within ``retries``). A row still failing is listed, never kept.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import pytest

from pydocs_eval.gold_extensions import GOLD_FILE_EXTENSIONS
from pydocs_eval.judge.chat_wire import ChatFailureKind
from pydocs_eval.judge.needle_citation import NeedleSite
from pydocs_eval.judge.openrouter_chat import FakeChatReply, FakeOpenRouterChatClient
from pydocs_eval.judge.reference_prompt import reference_prompt
from pydocs_eval.judge.reference_sources import ReferenceShape, ReferenceSource, ShownCode
from pydocs_eval.judge.reference_writer import (
    CollectedBatch,
    ReferenceGapKind,
    ReferenceWriteResult,
    SubmittedBatch,
    WriterClients,
    WriterRole,
    write_reference_answers,
)

_OPUS = "anthropic/claude-opus-5.5"
_SONNET = "anthropic/claude-sonnet-5"


def _repoqa_source(task_id: str, path: str, symbol: str) -> ReferenceSource:
    code = ShownCode(NeedleSite(path, symbol), 3, 4, 3, (f"def {symbol}():", "    return 1"))
    return ReferenceSource(task_id, f"Where is {symbol}?", ReferenceShape.REPOQA, (code,))


_T1 = _repoqa_source("t1", "pkg/alpha.py", "alpha")
_T2 = _repoqa_source("t2", "pkg/beta.py", "beta")
_GOOD = {
    "t1": {"answer": "`pkg/alpha.py` — `alpha` (lines 3–4)\n\n`alpha` returns 1."},
    "t2": {"answer": "`pkg/beta.py` — `beta` (lines 3–4)\n\n`beta` returns 1."},
}
_WRONG_FILE = {"answer": "`pkg/alpha.py` — `alpha` (lines 3–4); see `pkg/other.py`."}


def _clients(
    primary: Mapping[str, FakeChatReply | Sequence[FakeChatReply]],
    fallback: Mapping[str, FakeChatReply | Sequence[FakeChatReply]] | None = None,
) -> tuple[FakeOpenRouterChatClient, FakeOpenRouterChatClient, WriterClients]:
    opus = FakeOpenRouterChatClient(scripted=primary, served_model=_OPUS)
    sonnet = FakeOpenRouterChatClient(scripted=fallback or {}, served_model=_SONNET)
    return opus, sonnet, WriterClients(primary=opus, fallback=sonnet)


def _write(
    sources: Sequence[ReferenceSource],
    clients: WriterClients,
    *,
    retries: int = 2,
    on_submitted: Callable[[SubmittedBatch], None] | None = None,
    collected: Sequence[CollectedBatch] = (),
) -> ReferenceWriteResult:
    return write_reference_answers(
        sources,
        clients,
        retries=retries,
        extensions=GOLD_FILE_EXTENSIONS,
        on_submitted=on_submitted,
        collected=collected,
    )


def test_a_passing_answer_is_kept_with_its_model_and_prompt_hash() -> None:
    _, sonnet, clients = _clients(_GOOD)

    result = _write([_T1, _T2], clients)

    assert [row.task_id for row in result.rows] == ["t1", "t2"]
    (first, _) = result.rows
    assert first.reference.text == _GOOD["t1"]["answer"]
    assert first.reference.model_id == _OPUS
    assert first.reference.prompt_hash == reference_prompt(_T1).prompt_hash
    assert (first.fallback_reason, result.gaps, sonnet.requests) == ("", (), [])


def test_a_failed_check_is_regenerated_naming_its_problems() -> None:
    opus, _, clients = _clients({"t1": [_WRONG_FILE, _GOOD["t1"]]})

    result = _write([_T1], clients)

    (row,) = result.rows
    retry = opus.requests[-1]
    assert "names a file outside the gold: 'pkg/other.py'" in retry.messages[-1].content
    rejected = ("names a file outside the gold: 'pkg/other.py'",)
    assert row.reference.prompt_hash == reference_prompt(_T1, rejected=rejected).prompt_hash
    assert row.reference.model_id == _OPUS


def test_a_row_still_failing_after_its_retries_is_listed_never_kept() -> None:
    opus, sonnet, clients = _clients({"t1": [_WRONG_FILE] * 5})

    result = _write([_T1], clients, retries=2)

    assert result.rows == ()
    (gap,) = result.gaps
    assert (gap.task_id, gap.kind) == ("t1", ReferenceGapKind.FAILED_CHECK)
    assert "pkg/other.py" in gap.reason
    assert (len(opus.requests), sonnet.requests) == (3, [])


def test_an_unusable_answer_is_regenerated_on_the_same_model() -> None:
    opus, sonnet, clients = _clients({"t1": [ChatFailureKind.UNUSABLE_ANSWER, _GOOD["t1"]]})

    (row,) = _write([_T1], clients).rows

    assert (row.reference.model_id, len(opus.requests), sonnet.requests) == (_OPUS, 2, [])


def test_an_answer_without_its_string_is_regenerated() -> None:
    _, _, clients = _clients({"t1": [{"answer": ""}, _GOOD["t1"]]})

    (row,) = _write([_T1], clients).rows

    assert row.reference.text == _GOOD["t1"]["answer"]


def test_only_a_failed_job_goes_to_the_fallback_and_the_row_says_why() -> None:
    opus, sonnet, clients = _clients(
        {"t1": ChatFailureKind.JOB_FAILED, "t2": _GOOD["t2"]}, {"t1": _GOOD["t1"]}
    )

    result = _write([_T1, _T2], clients)

    by_task = {row.task_id: row for row in result.rows}
    assert by_task["t1"].reference.model_id == _SONNET
    assert "job_failed" in by_task["t1"].fallback_reason
    assert (by_task["t2"].reference.model_id, by_task["t2"].fallback_reason) == (_OPUS, "")
    assert [request.custom_id for request in sonnet.requests] == ["t1"]
    assert sonnet.requests[0] == reference_prompt(_T1).request, "the fallback gets the same prompt"


def test_the_fallback_model_appears_only_on_rows_whose_primary_job_failed() -> None:
    wrong_beta = {"answer": "`pkg/beta.py` — `beta`; see `pkg/other.py`."}
    _, _, clients = _clients(
        {"t1": ChatFailureKind.JOB_FAILED, "t2": [wrong_beta, _GOOD["t2"]]}, _GOOD
    )

    rows = _write([_T1, _T2], clients).rows

    assert [(row.task_id, row.reference.model_id) for row in rows] == [
        ("t1", _SONNET),
        ("t2", _OPUS),
    ]
    for row in rows:
        assert (row.reference.model_id == _SONNET) == bool(row.fallback_reason), row


def test_a_batch_still_running_is_listed_by_id_and_never_bought_again() -> None:
    opus, sonnet, clients = _clients({"t1": ChatFailureKind.STILL_RUNNING})

    result = _write([_T1], clients)

    (gap,) = result.gaps
    assert (gap.kind, gap.batch_id) == (ReferenceGapKind.STILL_RUNNING, "fake_batch_1")
    assert (len(opus.requests), sonnet.requests) == (1, [])
    assert result.ended_batches == ()


def test_a_row_never_submitted_is_listed_and_not_asked_again() -> None:
    opus, sonnet, clients = _clients({"t1": ChatFailureKind.NOT_SUBMITTED})

    (gap,) = _write([_T1], clients).gaps

    assert gap.kind is ReferenceGapKind.NOT_SUBMITTED
    assert (len(opus.requests), sonnet.requests) == (1, [])


def test_a_row_whose_fallback_job_also_failed_is_listed() -> None:
    _, _, clients = _clients({"t1": ChatFailureKind.JOB_FAILED}, {"t1": ChatFailureKind.JOB_FAILED})

    (gap,) = _write([_T1], clients).gaps

    assert gap.kind is ReferenceGapKind.BOTH_MODELS_FAILED


def test_every_submitted_batch_is_announced_with_its_role_and_rows() -> None:
    _, _, clients = _clients({"t1": ChatFailureKind.JOB_FAILED, "t2": _GOOD["t2"]}, _GOOD)
    announced: list[SubmittedBatch] = []

    _write([_T1, _T2], clients, on_submitted=announced.append)

    assert [(batch.batch_id, batch.role) for batch in announced] == [
        ("fake_batch_1", WriterRole.PRIMARY),
        ("fake_batch_1", WriterRole.FALLBACK),
    ]
    assert [row.task_id for row in announced[0].rows] == ["t1", "t2"]
    assert announced[0].rows[0].prompt_hash == reference_prompt(_T1).prompt_hash
    assert "job_failed" in announced[1].rows[0].fallback_reason


def test_a_collected_batch_is_absorbed_before_anything_is_asked() -> None:
    opus, sonnet, clients = _clients({"t1": _GOOD["t1"], "t2": _GOOD["t2"]})
    submitted = SubmittedBatch.of(
        "batch_old", WriterRole.FALLBACK, [(reference_prompt(_T1), "opus job failed")]
    )
    outcomes = FakeOpenRouterChatClient(scripted=_GOOD, served_model=_SONNET).collect(
        "batch_old", ["t1"]
    )
    collected = CollectedBatch(submitted, outcomes)

    result = _write([_T1, _T2], clients, collected=[collected])

    by_task = {row.task_id: row for row in result.rows}
    assert (by_task["t1"].reference.model_id, by_task["t1"].fallback_reason) == (
        _SONNET,
        "opus job failed",
    )
    assert [request.custom_id for request in opus.requests] == ["t2"]
    assert sonnet.requests == []
    assert "batch_old" in result.ended_batches


def test_a_collected_batch_still_running_stays_listed() -> None:
    opus, _, clients = _clients({})
    submitted = SubmittedBatch.of("batch_old", WriterRole.PRIMARY, [(reference_prompt(_T1), "")])
    running = FakeOpenRouterChatClient(
        scripted={"t1": ChatFailureKind.STILL_RUNNING}, served_model=_OPUS
    ).collect("batch_old", ["t1"])

    result = _write([_T1], clients, collected=[CollectedBatch(submitted, running)])

    (gap,) = result.gaps
    assert (gap.kind, gap.batch_id) == (ReferenceGapKind.STILL_RUNNING, "batch_old")
    assert opus.requests == []


def test_a_collected_batch_whose_rows_were_all_stored_still_ends() -> None:
    """A crash after storing the rows but before settling the batch must not strand it."""
    _, _, clients = _clients({})
    submitted = SubmittedBatch.of("batch_old", WriterRole.PRIMARY, [(reference_prompt(_T1), "")])
    outcomes = FakeOpenRouterChatClient(scripted=_GOOD, served_model=_OPUS).collect(
        "batch_old", ["t1"]
    )

    result = _write([], clients, collected=[CollectedBatch(submitted, outcomes)])

    assert (result.rows, result.ended_batches) == ((), ("batch_old",))


def test_a_chat_source_is_checked_against_every_gold_site() -> None:
    first = ShownCode(NeedleSite("a.py", "run"), 1, 1, 1, ("def run(): ...",))
    second = ShownCode(NeedleSite("b.md", "Usage"), 4, 4, 4, ("## Usage",))
    chat = ReferenceSource("c1", "How?", ReferenceShape.CHAT, (first, second))
    half = {"answer": "`a.py` — `run` (lines 1–1) runs it."}
    whole = {"answer": "`a.py` — `run` (lines 1–1)\n`b.md` — `Usage` (lines 4–4)\n\nIt runs."}
    opus, _, clients = _clients({"c1": [half, whole]})

    (row,) = _write([chat], clients).rows

    assert row.reference.text == whole["answer"]
    assert "names no path 'b.md'" in opus.requests[-1].messages[-1].content


def test_the_cost_of_every_answer_is_summed() -> None:
    _, _, clients = _clients(_GOOD)

    result = _write([_T1, _T2], clients)

    assert result.cost_usd == 0.0
    assert result.uncosted_answers == 2


def test_negative_retries_are_refused() -> None:
    _, _, clients = _clients(_GOOD)

    with pytest.raises(ValueError, match="retries"):
        _write([_T1], clients, retries=-1)
