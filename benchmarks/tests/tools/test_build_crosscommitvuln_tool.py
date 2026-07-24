"""Unit tests for the pure decision logic of the network build tool.
The tool is NOT an installed module — load it from its file path. Git and
network are stubbed; the heavy construction run is a documented one-time
step (design §6.3), not a test."""

from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_TOOL = Path(__file__).parents[2] / "tools" / "build_crosscommitvuln.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("build_crosscommitvuln", _TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _annotation(cve_id: str) -> dict:
    return {
        "cve_id": cve_id,
        "repo": "https://github.com/exampleorg/exampleproj",
        "ecosystem": "PyPI",
        "cwe_ids": ["CWE-78"],
        "severity_combined": "high",
        "summary": "OS command injection via exec_cmd()",
        "fix_commit": "a" * 40,
        "annotation_status": "complete+sast",
        "contributing_commits": [
            {
                "hash": "b" * 40,
                "short_hash": "bbbbbbbb",
                "date": "2024-01-15",
                "subject": "refactor",
                "role": "SINK — exec_cmd with user input",
                "files_changed": ["app/jobs.py"],
            }
        ],
        "vulnerability_chain": {"description": "taint flows to exec_cmd sink."},
    }


def test_co_resident_cves_excludes_self_and_uses_predicate() -> None:
    tool = _load_tool()
    record = _annotation("CVE-2099-0001")
    siblings = [record, _annotation("CVE-2099-0002"), _annotation("CVE-2099-0003")]
    co = tool.co_resident_cves(
        record, siblings, is_assembled=lambda other: other["cve_id"] == "CVE-2099-0002"
    )
    # The drop path (design §5.2): CVE-0002 co-resides -> the caller DROPS
    # this record; self and non-assembled CVE-0003 are never reported.
    assert co == ("CVE-2099-0002",)


def test_build_record_shape_and_leak_check_wired() -> None:
    tool = _load_tool()
    record, banned_row = tool.build_record(_annotation("CVE-2099-0001"), "f" * 40)
    assert record["task_id"] == "cve-2099-0001"
    assert record["repo_url"] == "https://github.com/exampleorg/exampleproj"
    assert record["prefix_sha"] == "f" * 40
    assert record["gold"]["cve_id"] == "CVE-2099-0001"
    assert record["gold"]["cwe_ids"] == ["CWE-78"]
    assert record["gold"]["files"] == ["app/jobs.py"]
    assert record["gold"]["mechanism"] == "taint flows to exec_cmd sink."
    assert record["metadata"]["co_resident_cves"] == ""
    assert banned_row["task_id"] == "cve-2099-0001" and "exec_cmd" in banned_row["banned"]
    # The query the record carries already passed assert_query_clean inside
    # build_record; verify it re-passes against the stored ban list.
    from pydocs_eval.datasets._crosscommitvuln_build import assert_query_clean

    assert_query_clean(record["query"], banned_row["banned"])


# --- Hermetic ancestry + main() orchestration coverage (no real git/network) ---


@dataclass
class _FakeRepoCache:
    """Stand-in for ``RepoCache`` (mirrors the SWE-QA fake): no git, no network.

    ``checkout`` records the (url, sha) it was asked for and returns a per-sha
    synthetic path; ``file_tree`` is unused by the build tool but present so the
    fake satisfies the same shape as the production cache."""

    checked_out: list[tuple[str, str]] = field(default_factory=list)

    def checkout(self, url: str, sha: str) -> Path:
        self.checked_out.append((url, sha))
        return Path("/fake") / sha

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return ()


@dataclass
class _FakeGit:
    """In-memory git runner: a child->parent map answers the exact three verbs
    the tool issues — ``rev-parse <sha>^``, ``merge-base --is-ancestor A B`` and
    ``show -s --format=%cs <sha>`` — with canned outputs, no subprocess."""

    parent: dict[str, str]  # child_sha -> parent_sha; root shas are absent
    dates: dict[str, str] = field(default_factory=dict)
    timeout_shas: frozenset[str] = frozenset()

    def _ancestors(self, sha: str) -> set[str]:
        seen, cur = {sha}, sha
        while cur in self.parent:
            cur = self.parent[cur]
            seen.add(cur)
        return seen

    def __call__(self, checkout: Path, *args: str) -> str:
        if any(a in self.timeout_shas for a in args):
            raise subprocess.TimeoutExpired(cmd="git", timeout=1)
        if args[0] == "rev-parse":
            target = args[1]
            base = target[:-1] if target.endswith("^") else target
            if target.endswith("^") and base not in self.parent:
                raise subprocess.CalledProcessError(128, ["git", *args], stderr="no parent")
            return self.parent[base] if target.endswith("^") else target
        if args[:2] == ("merge-base", "--is-ancestor"):
            ancestor, descendant = args[2], args[3]
            if ancestor in self._ancestors(descendant):
                return ""
            raise subprocess.CalledProcessError(1, ["git", *args])
        if args[0] == "show":
            return self.dates.get(args[-1], "2024-01-01")
        raise AssertionError(f"unexpected git args: {args}")


# Linear R1 history root->tip: c1 -> c2 -> preA -> fixA -> preB -> fixB. So at
# CVE-A's prefix (preA) CVE-B's chain (c2) is present and STILL unfixed (fixB is
# a descendant) -> A is co-resident-dropped; at CVE-B's prefix (preB) CVE-A is
# already fixed (fixA is an ancestor) -> B survives. fixD's parent resolves but
# its contributing hash times out; rootC has no parent so its `^` fails.
_PARENTS = {
    "c2": "c1",
    "preA": "c2",
    "fixA": "preA",
    "preB": "fixA",
    "fixB": "preB",
    "fixD": "preD",
}


def _scenario_annotation(cve: str, repo: str, fix: str, chash: str, cwe: str, sink: str) -> dict:
    return {
        "cve_id": cve,
        "repo": repo,
        "ecosystem": "PyPI",
        "cwe_ids": [cwe],
        "severity_combined": "high",
        "summary": f"flaw via {sink}",
        "fix_commit": fix,
        "annotation_status": "complete+sast",
        "contributing_commits": [
            {"hash": chash, "date": "2024-01-15", "role": f"SINK {sink}", "files_changed": ["m.py"]}
        ],
        "vulnerability_chain": {"description": f"taint reaches {sink}."},
    }


def test_resolve_prefix_sha_checks_out_fix_then_resolves_parent(monkeypatch) -> None:
    tool = _load_tool()
    cache = _FakeRepoCache()
    monkeypatch.setattr(tool, "_git", _FakeGit(parent=_PARENTS))
    checkout, prefix_sha = tool.resolve_prefix_sha(cache, "https://github.com/org/multi", "fixA")
    # It checks out AT the fix commit, then pins the fix's PARENT as prefix_sha.
    assert cache.checked_out == [("https://github.com/org/multi", "fixA")]
    assert checkout == Path("/fake/fixA")
    assert prefix_sha == "preA"


def test_ancestry_predicates_with_canned_git(monkeypatch) -> None:
    tool = _load_tool()
    monkeypatch.setattr(tool, "_git", _FakeGit(parent=_PARENTS))
    ck = Path("/fake")
    assert tool.is_ancestor(ck, "c1", "preA") is True
    assert tool.is_ancestor(ck, "fixB", "preA") is False  # descendant, not ancestor
    a = _scenario_annotation("CVE-1", "https://github.com/org/multi", "fixA", "c1", "CWE-78", "sh")
    b = _scenario_annotation("CVE-2", "https://github.com/org/multi", "fixB", "c2", "CWE-89", "q")
    assert tool.contributing_hashes_present(ck, "preA", a) is True
    # B's whole chain is assembled and still unfixed at A's prefix snapshot...
    assert tool.chain_assembled_at(ck, "preA", b) is True
    # ...but A is already fixed at B's prefix snapshot, so A is NOT assembled there.
    assert tool.chain_assembled_at(ck, "preB", a) is False


def _write_annotations(root: Path, annotations: list[dict]) -> None:
    for a in annotations:
        d = root / "dataset" / str(a["cve_id"])
        d.mkdir(parents=True, exist_ok=True)
        (d / "annotation.json").write_text(json.dumps(a))


def test_main_drops_co_resident_and_broken_then_emits_survivors(
    monkeypatch, tmp_path, caplog
) -> None:
    tool = _load_tool()
    source = tmp_path / "clone"
    out = tmp_path / "out"
    annotations = [
        _scenario_annotation(
            "CVE-2099-0001", "https://github.com/org/multi", "fixA", "c1", "CWE-78", "sh"
        ),
        _scenario_annotation(
            "CVE-2099-0002", "https://github.com/org/multi", "fixB", "c2", "CWE-89", "q"
        ),
        _scenario_annotation(
            "CVE-2099-0003", "https://github.com/org/broken", "rootC", "rootC", "CWE-94", "ev"
        ),
        _scenario_annotation(
            "CVE-2099-0004",
            "https://github.com/org/timeout",
            "fixD",
            "timeout_hash",
            "CWE-22",
            "op",
        ),
    ]
    _write_annotations(source, annotations)
    monkeypatch.setattr(tool, "_VENDORED_DIR", out)
    monkeypatch.setattr(tool, "RepoCache", _FakeRepoCache)
    monkeypatch.setattr(
        tool,
        "_git",
        _FakeGit(
            parent=_PARENTS, dates={"fixB": "2024-06-15"}, timeout_shas=frozenset({"timeout_hash"})
        ),
    )

    with caplog.at_level(logging.INFO, logger="build_crosscommitvuln"):
        rc = tool.main(["prog", str(source)])
    assert rc == 0

    records = [json.loads(line) for line in (out / "records.jsonl").read_text().splitlines()]
    banned = [json.loads(line) for line in (out / "banned_tokens.jsonl").read_text().splitlines()]
    # Only CVE-2099-0002 survives: 0001 co-resident-dropped, 0003 parent-resolution
    # failure, 0004 git-timeout in the ancestry phase (widened guard drops it, not
    # the whole build).
    assert [r["task_id"] for r in records] == ["cve-2099-0002"]
    assert records[0]["metadata"]["co_resident_cves"] == ""  # cleared by construction
    assert records[0]["metadata"]["fix_commit_date"] == "2024-06-15"  # resolved from git
    assert records[0]["prefix_sha"] == "preB"
    assert [row["task_id"] for row in banned] == ["cve-2099-0002"]

    messages = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "ancestry-dropped 1 (CVE-2099-0001)" in messages
    assert "broken-dropped 2 (CVE-2099-0003, CVE-2099-0004)" in messages
    assert "vendored 1 record(s)" in messages


def test_main_git_timeout_in_ancestry_phase_drops_one_record_not_the_build(
    monkeypatch, tmp_path, caplog
) -> None:
    # Minor-fix regression: a subprocess.TimeoutExpired raised inside the LATER
    # ancestry calls (contributing_hashes_present) must drop THAT record and let
    # the build continue — previously it escaped the resolve-only guard.
    tool = _load_tool()
    source = tmp_path / "clone"
    out = tmp_path / "out"
    annotations = [
        _scenario_annotation(
            "CVE-2099-0004",
            "https://github.com/org/timeout",
            "fixD",
            "timeout_hash",
            "CWE-22",
            "op",
        ),
        _scenario_annotation(
            "CVE-2099-0002", "https://github.com/org/multi", "fixB", "c2", "CWE-89", "q"
        ),
    ]
    _write_annotations(source, annotations)
    monkeypatch.setattr(tool, "_VENDORED_DIR", out)
    monkeypatch.setattr(tool, "RepoCache", _FakeRepoCache)
    monkeypatch.setattr(
        tool, "_git", _FakeGit(parent=_PARENTS, timeout_shas=frozenset({"timeout_hash"}))
    )

    with caplog.at_level(logging.INFO, logger="build_crosscommitvuln"):
        rc = tool.main(["prog", str(source)])
    assert rc == 0  # build completed despite the git timeout

    records = [json.loads(line) for line in (out / "records.jsonl").read_text().splitlines()]
    assert [r["task_id"] for r in records] == ["cve-2099-0002"]  # survivor emitted
    messages = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "CVE-2099-0004" in messages and "git failure" in messages
    assert "broken-dropped 1 (CVE-2099-0004)" in messages
