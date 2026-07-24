"""Pure construction-helper tests for the CrossCommitVuln QA transform
(design §5) — hermetic, synthetic annotation dicts only, no network."""

from __future__ import annotations

import copy
import logging

import pytest

from pydocs_eval.datasets._crosscommitvuln_build import (
    assert_query_clean,
    build_file_set,
    build_gold_extra,
    build_mechanism,
    build_query,
    gold_from_record,
    is_included,
    mine_banned_tokens,
    repo_slug,
)

# A tiny synthetic annotation shaped like a real CrossCommitVuln-Bench
# annotation.json (design §4.1). Fake CVE id on purpose: this dict is NOT
# vendored gold, so it may live inline in a .py test file (design §6.6).
_ANNOTATION: dict = {
    "cve_id": "CVE-2099-0001",
    "ghsa_id": "GHSA-xxxx-yyyy-zzzz",
    "repo": "https://github.com/exampleorg/exampleproj",
    "ecosystem": "PyPI",
    "cwe_ids": ["CWE-78"],
    "severity_combined": "high",
    "summary": "Exampleproj OS Command Injection via exec_cmd() with user input",
    "fix_commit": "a" * 40,
    "annotation_status": "complete+sast",
    "contributing_commits": [
        {
            "hash": "b" * 40,
            "short_hash": "bbbbbbbb",
            "date": "2024-01-15",
            "subject": "refactor exec_cmd path handling",
            "role": "SINK — exec_cmd(f-string) with user-controlled mailbox path",
            "files_changed": ["app/jobs.py", "app/models/mailbox.py"],
        },
        {
            "hash": "c" * 40,
            "short_hash": "cccccccc",
            "date": "2024-11-23",
            "subject": "wire additional inputs",
            "role": "SOURCE EXPANSION — wires user data into exec_cmd paths",
            "files_changed": ["app/sysutils.py"],
        },
    ],
    "vulnerability_chain": {
        "description": (
            "User-controlled mailbox path flows into exec_cmd which runs "
            "subprocess with shell=True."
        ),
        "attack_vector": "network",
    },
}


def _annotation(**overrides: object) -> dict:
    a = copy.deepcopy(_ANNOTATION)
    a.update(overrides)
    return a


def test_is_included_accepts_filled_chain() -> None:
    assert is_included(_ANNOTATION)


def test_is_included_rejects_skeleton_status() -> None:
    assert not is_included(_annotation(annotation_status="skeleton — pending"))
    assert not is_included(_annotation(annotation_status="SKIP — documented negative"))


def test_is_included_rejects_todo_or_empty_description() -> None:
    assert not is_included(_annotation(vulnerability_chain={"description": "TODO: fill"}))
    assert not is_included(_annotation(vulnerability_chain={"description": ""}))
    assert not is_included(_annotation(vulnerability_chain=None))


def test_mine_banned_tokens_covers_ids_files_symbols_dates_and_framing() -> None:
    tokens = mine_banned_tokens(_ANNOTATION)
    # cve/ghsa/cwe ids (CWE-78 AND bare 78)
    for expected in ("CVE-2099-0001", "GHSA-xxxx-yyyy-zzzz", "CWE-78", "78"):
        assert expected in tokens
    # fix + contributing hashes, full and short
    for expected in ("a" * 40, "a" * 8, "b" * 40, "bbbbbbbb", "c" * 40):
        assert expected in tokens
    # file paths AND basenames
    for expected in ("app/jobs.py", "jobs.py", "mailbox.py", "sysutils.py"):
        assert expected in tokens
    # sink/API symbols from role + subject + chain description
    for expected in ("exec_cmd", "shell=True", "subprocess"):
        assert expected in tokens
    # flaw-class keywords from summary + CWE names
    assert "command injection" in tokens
    # every commit date
    assert "2024-01-15" in tokens and "2024-11-23" in tokens
    # v2 framing vocabulary (design §5.2)
    for expected in ("commit", "commits", "multi-commit", "benign", "sast", "scanner"):
        assert expected in tokens


def test_mine_banned_tokens_drops_slug_collisions_and_logs(caplog) -> None:
    # Framing ban "across" occurs inside the repo slug -> repo identity wins:
    # the token is dropped (logged), NOT raised on later (design §5.2).
    a = _annotation(repo="https://github.com/across-net/across")
    with caplog.at_level(logging.INFO):
        tokens = mine_banned_tokens(a)
    assert "across" not in tokens
    assert any("across" in r.getMessage() and "slug" in r.getMessage() for r in caplog.records)
    # And the generated query (which contains the slug) stays clean.
    assert_query_clean(build_query(a), tokens)


def test_build_query_interpolates_only_slug_ecosystem_severity() -> None:
    query = build_query(_ANNOTATION)
    assert query == (
        "This snapshot of the PyPI project exampleorg/exampleproj contains a "
        "high-severity security vulnerability. Locate the exploitable "
        "condition: identify where untrusted input enters, the dangerous "
        "operation it reaches, and classify the vulnerability by its exploit."
    )
    assert repo_slug(_ANNOTATION) == "exampleorg/exampleproj"


def test_generated_query_is_clean_against_its_own_banned_tokens() -> None:
    assert_query_clean(build_query(_ANNOTATION), mine_banned_tokens(_ANNOTATION))


def test_assert_query_clean_raises_naming_the_leaking_token() -> None:
    banned = mine_banned_tokens(_ANNOTATION)
    leaking = "The flaw is exec_cmd in app/jobs.py, introduced gradually."
    with pytest.raises(ValueError, match="exec_cmd"):
        assert_query_clean(leaking, banned)


def test_assert_query_clean_is_word_boundary_aware_and_case_insensitive() -> None:
    with pytest.raises(ValueError, match="benign"):
        assert_query_clean("A Benign looking change", ("benign",))
    # "commit" must NOT fire inside "committee" (boundary), nor "78" inside "1978".
    assert_query_clean("The committee met in 1978.", ("commit", "78"))


def test_build_file_set_unions_ordered_distinct() -> None:
    assert build_file_set(_ANNOTATION) == (
        "app/jobs.py",
        "app/models/mailbox.py",
        "app/sysutils.py",
    )


def test_build_file_set_falls_back_to_role_paths_then_raises() -> None:
    a = _annotation(
        contributing_commits=[
            {"hash": "d" * 40, "role": "SINK — taint reaches app/runner.py", "files_changed": []}
        ]
    )
    assert build_file_set(a) == ("app/runner.py",)
    empty = _annotation(
        contributing_commits=[{"hash": "d" * 40, "role": "SINK — no file", "files_changed": []}]
    )
    with pytest.raises(ValueError, match="CVE-2099-0001"):
        build_file_set(empty)


def test_build_gold_extra_keys() -> None:
    a = _annotation(cwe_ids=["CWE-78", "CWE-88"])
    assert build_gold_extra(a) == {
        "cve_id": "CVE-2099-0001",
        "cwe_id_0": "CWE-78",
        "cwe_id_1": "CWE-88",
    }


def test_build_mechanism_normalizes_whitespace() -> None:
    a = _annotation(vulnerability_chain={"description": "  source \n flows  to sink.  "})
    assert build_mechanism(a) == "source flows to sink."


def test_gold_from_record_maps_fields() -> None:
    rec = {
        "gold": {
            "cve_id": "CVE-2099-0001",
            "cwe_ids": ["CWE-78"],
            "mechanism": "source flows to sink.",
            "files": ["app/jobs.py", "app/sysutils.py"],
        }
    }
    gold = gold_from_record(rec)
    assert gold.ast_body == "source flows to sink."
    assert gold.file_set == ("app/jobs.py", "app/sysutils.py")
    assert gold.extra == {"cve_id": "CVE-2099-0001", "cwe_id_0": "CWE-78"}
