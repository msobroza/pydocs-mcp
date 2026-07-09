"""``--report`` with a missing parent directory must not lose the report.

Regression guard: ``main()`` used to call ``args.report.write_text(report)``
directly (runner.py, in the ``main()`` try-block) with no parent-directory
creation. When the parent didn't exist, ``write_text`` raised
``FileNotFoundError`` AFTER the whole sweep had already run — and because
the raise happened before the ``print(report)`` line, the report reached
neither the file nor stdout. Hours of sweep output would be reduced to
whatever the JSONL tracker happened to persist. ``plotting.py`` already
auto-creates parent dirs for exports (precedent); the report writer did
not.

Drives ``main()`` end-to-end via monkeypatched ``sys.argv`` (mirrors the
``sys.argv`` pattern in ``test_ci_compare.py``) against the shared
``repoqa_mini.json`` fixture so the sweep itself is fast and needs no
network / HuggingFace download.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.runner import main

_FIXTURE = Path(__file__).parent / "fixtures" / "repoqa_mini.json"


def _empty_overlay(tmp_path: Path) -> Path:
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")
    return overlay


def test_report_flag_creates_missing_parent_directories(tmp_path: Path, monkeypatch) -> None:
    """``--report results/new_dir/report.md`` where ``new_dir`` doesn't
    exist must still land the report on disk — not raise after the sweep
    has already run.
    """
    overlay = _empty_overlay(tmp_path)
    # WHY: nested + non-existent parent chain (two missing levels) pins
    # that a single mkdir(parents=False) fix would still be insufficient.
    report_path = tmp_path / "results" / "new_dir" / "sub" / "report.md"
    assert not report_path.parent.exists()

    monkeypatch.setattr(
        "sys.argv",
        [
            "runner",
            "--configs",
            str(overlay),
            "--dataset",
            "repoqa",
            "--fixture",
            str(_FIXTURE),
            "--limit",
            "1",
            "--report",
            str(report_path),
        ],
    )

    main()

    assert report_path.exists(), "report must be written even though the parent dir was missing"
    content = report_path.read_text()
    assert "recall@1" in content or "1 tasks" in content or len(content) > 0
