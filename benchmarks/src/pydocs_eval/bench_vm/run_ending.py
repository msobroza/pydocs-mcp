"""How one attempt's run ended: the status to record, and how its final state push goes.

A value the supervisor's steps return and ``supervisor._end_run`` acts on: it
pushes the state as ``state_push`` says, records ``status`` with
``exit_code`` and ``detail``, then deletes the VM when the spec asks and the
status is one the supervisor ends by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydocs_eval.bench_vm.run_record import RunStatus


class FinalStatePush(StrEnum):
    """What the final sync pushes before the ending is recorded."""

    # One incremental push of the whole state directory.
    ALL = "all"
    # A shutdown (spec "Endings"): the files a resume needs first, so a spot
    # VM's 30-second notice still saves the ledgers, then everything else.
    RESUME_FILES_FIRST = "resume_files_first"
    # The state was never restored: pushing it could only regress the good copy in GCS.
    NOTHING = "nothing"


@dataclass(frozen=True, slots=True)
class RunEnding:
    """How the child's run ended: the status to record, the exit code, why, and the final push."""

    status: RunStatus
    exit_code: int | None
    detail: str
    state_push: FinalStatePush = FinalStatePush.ALL
