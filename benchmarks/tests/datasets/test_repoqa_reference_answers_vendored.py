"""The vendored repoqa-qa reference answers: byte-pinned, each row checked, each row provenanced.

The file is written by ``write-references`` and never edited by hand; a changed
row is a visible digest move. Each row names its gold in its task id
(``repoqa-qa/repo_qa/<owner>/<repo>@<sha7>/<path>::<function>``), so the repoqa
code check re-runs here with no dataset download.
"""

from __future__ import annotations

import hashlib
import importlib.resources as ir
import re

import pytest

from pydocs_eval.datasets.reference_answers import (
    REPOQA_REFERENCE_FILE,
    ReferenceRow,
    read_reference_rows,
    vendored_repoqa_reference_path,
)
from pydocs_eval.gold_extensions import GOLD_FILE_EXTENSIONS
from pydocs_eval.judge.config import load_judge_deployment
from pydocs_eval.judge.model_ids import served_model_matches
from pydocs_eval.judge.needle_citation import NeedleSite
from pydocs_eval.judge.reference_checks import check_repoqa_reference

from ..judge._judge_fakes import DEPLOYMENT_YAML

# Regenerated ONLY when a reference batch lands — and a row edit is then a visible diff.
_ROWS_SHA256 = "ab2b1a6906e048c7b9c70593499e901b45268dc2c6113ad7f8780c3cdb602814"
_TASK_ID = re.compile(r"repoqa-qa/repo_qa/[^@]+@[0-9a-f]{7}/(?P<path>.+)::(?P<function>[^:]+)")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_WRITER = load_judge_deployment(DEPLOYMENT_YAML).reference_writer
_ROWS = read_reference_rows(vendored_repoqa_reference_path())


def _needle_of(row: ReferenceRow) -> NeedleSite:
    match = _TASK_ID.fullmatch(row.task_id)
    assert match is not None, row.task_id
    return NeedleSite(match["path"], match["function"])


def test_the_rows_are_byte_pinned() -> None:
    digest = hashlib.sha256(vendored_repoqa_reference_path().read_bytes()).hexdigest()

    assert digest == _ROWS_SHA256


def test_the_rows_ship_as_package_data() -> None:
    package = ir.files("pydocs_eval.datasets.data.repoqa_qa")

    assert package.joinpath(REPOQA_REFERENCE_FILE).read_bytes() == (
        vendored_repoqa_reference_path().read_bytes()
    )


@pytest.mark.parametrize("row", _ROWS, ids=[row.task_id for row in _ROWS])
def test_every_row_passes_the_repoqa_code_check(row: ReferenceRow) -> None:
    check = check_repoqa_reference(
        row.reference.text, _needle_of(row), extensions=GOLD_FILE_EXTENSIONS
    )

    assert check.problems == ()


@pytest.mark.parametrize("row", _ROWS, ids=[row.task_id for row in _ROWS])
def test_every_row_names_its_writer_and_its_prompt(row: ReferenceRow) -> None:
    by_writer = served_model_matches(_WRITER.model, row.reference.model_id)
    by_fallback = served_model_matches(_WRITER.fallback_model, row.reference.model_id)

    assert _SHA256_HEX.fullmatch(row.reference.prompt_hash)
    assert by_writer or by_fallback, row.reference.model_id
    assert by_fallback == bool(row.fallback_reason), (
        "the fallback wrote it iff the writer's job failed"
    )
