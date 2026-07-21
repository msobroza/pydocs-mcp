"""Path-normalizer tests — the three verified conventions + dependency exclusion
(ADR 0011 "one path normalizer")."""

from __future__ import annotations

import pytest

from pydocs_eval.trajectory.path_normalizer import normalize_path

WS = "/home/runner/work/repo"


def test_project_relative_posix_passthrough():
    got = normalize_path("src/pkg/module.py", workspace_root=WS)
    assert got.value == "src/pkg/module.py"
    assert got.gold_matchable is True


def test_index_root_relative_dot_prefix_cleaned():
    got = normalize_path("./src/pkg/module.py", workspace_root=WS)
    assert got.value == "src/pkg/module.py"
    assert got.gold_matchable is True


def test_absolute_under_workspace_relativized():
    got = normalize_path(f"{WS}/src/pkg/module.py", workspace_root=WS)
    assert got.value == "src/pkg/module.py"
    assert got.gold_matchable is True


def test_absolute_dependency_outside_workspace_excluded():
    got = normalize_path("/usr/lib/python3.11/site-packages/dep/x.py", workspace_root=WS)
    assert got.value == "/usr/lib/python3.11/site-packages/dep/x.py"
    assert got.gold_matchable is False


def test_trailing_slash_workspace_root_relativizes():
    # A workspace root passed with a trailing slash still relativizes cleanly.
    got = normalize_path(f"{WS}/a.py", workspace_root=WS + "/")
    assert got.value == "a.py"
    assert got.gold_matchable is True


def test_workspace_prefix_sibling_dir_not_a_false_match():
    # /home/runner/work/repo-other must NOT be read as under /home/runner/work/repo.
    got = normalize_path(f"{WS}-other/x.py", workspace_root=WS)
    assert got.gold_matchable is False
    assert got.value == f"{WS}-other/x.py"


def test_macos_private_var_firmlink_relativizes_against_var_workspace():
    # Real-trajectory bug (3c63ee67): the loop Read tool canonicalizes the temp
    # workspace to /private/var/... while the rollout driver recorded the
    # workspace_root under the /var/... firmlink alias. macOS firmlinks
    # (/var -> /private/var) denote the same location; the normalizer must fold
    # them so a genuinely in-workspace Read is not mis-excluded as a dependency.
    root = "/var/folders/k4/j07/T/tmp.M4PW6PHfYE"
    raw = "/private/var/folders/k4/j07/T/tmp.M4PW6PHfYE/widgetlib/calculator.py"
    got = normalize_path(raw, workspace_root=root)
    assert got.value == "widgetlib/calculator.py"
    assert got.gold_matchable is True


def test_macos_firmlink_symmetric_private_workspace_var_raw():
    # Symmetric direction: workspace recorded as /private/..., raw under /var/....
    root = "/private/tmp/ws"
    got = normalize_path("/tmp/ws/pkg/x.py", workspace_root=root)
    assert got.value == "pkg/x.py"
    assert got.gold_matchable is True


def test_private_non_firmlink_prefix_not_collapsed():
    # Only the known firmlink roots (/var, /tmp, /etc) collapse; an unrelated
    # /private/... dependency path stays absolute and non-matchable.
    got = normalize_path("/private/custom/dep/x.py", workspace_root="/var/ws")
    assert got.gold_matchable is False
    assert got.value == "/private/custom/dep/x.py"


def test_empty_path_raises():
    with pytest.raises(ValueError, match="empty path"):
        normalize_path("", workspace_root=WS)


def test_relative_workspace_root_raises():
    with pytest.raises(ValueError, match="absolute"):
        normalize_path("a.py", workspace_root="relative/ws")
