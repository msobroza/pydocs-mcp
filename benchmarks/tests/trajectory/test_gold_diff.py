"""Gold-patch parser tests — real SWE-bench-Live patch excerpts as fixtures.

Excerpts are trimmed copies of the patches recorded in
``docs/superpowers/research/2026-07-18-phase2-evidence-swebench-formats.md`` §2.2,
one per measured edge case.
"""

from __future__ import annotations

import pytest

from pydocs_eval.trajectory.gold_diff import (
    GoldPatchError,
    coerce_test_names,
    dedupe_instances,
    modified_files,
    parse_gold_patch,
)

# (a) multi-file, multi-hunk — cfn-lint-3798 shape.
MULTI = """diff --git a/src/cfnlint/jsonschema/_keywords.py b/src/cfnlint/jsonschema/_keywords.py
index f88514c6bb..4932b111a4 100644
--- a/src/cfnlint/jsonschema/_keywords.py
+++ b/src/cfnlint/jsonschema/_keywords.py
@@ -323,2 +323,4 @@ def maxItems(
     if x:
-        yield ValidationError("a")
+        yield ValidationError(
+            "b"
+        )
@@ -400,1 +401,1 @@ def other(
-    old
+    new
diff --git a/src/cfnlint/other.py b/src/cfnlint/other.py
index aaa..bbb 100644
--- a/src/cfnlint/other.py
+++ b/src/cfnlint/other.py
@@ -1,1 +1,1 @@
-x
+y
"""

# (b) new file with /dev/null source — haystack-8619 shape.
NEW_FILE = """diff --git a/releasenotes/notes/update-129f701ba07b944b.yaml b/releasenotes/notes/update-129f701ba07b944b.yaml
new file mode 100644
index 0000000000..61a0110958
--- /dev/null
+++ b/releasenotes/notes/update-129f701ba07b944b.yaml
@@ -0,0 +1,2 @@
+---
+upgrade: yes
"""

# (c) unquoted path containing spaces — solaar-2438 shape.
SPACE_PATH = """diff --git a/docs/devices/G502 Lightspeed 407F.txt b/docs/devices/G502 Lightspeed 407F.txt
index f69ae0991e..fefbfae962 100644
--- a/docs/devices/G502 Lightspeed 407F.txt
+++ b/docs/devices/G502 Lightspeed 407F.txt
@@ -1,2 +1,2 @@
-Solaar version 1.1.7
+solaar version 1.1.12rc1
 tail
"""

# (d) binary new file one-liner — cryptography-12812 shape.
BINARY = """diff --git a/vectors/crl_issuer_invalid.der b/vectors/crl_issuer_invalid.der
new file mode 100644
index 000000000000..1221dc5e0d6b
Binary files /dev/null and b/vectors/crl_issuer_invalid.der differ
"""

# (e) symlink + no-newline marker — ansible-lint-4662 shape.
SYMLINK = """diff --git a/docs/rules/pattern.md b/docs/rules/pattern.md
new file mode 120000
index 0000000000..f4f296e99c
--- /dev/null
+++ b/docs/rules/pattern.md
@@ -0,0 +1,1 @@
+../../src/ansiblelint/rules/pattern.md
\\ No newline at end of file
"""

# (f) rename: one hunkless 100%, one 59% with a hunk — datamodel-code-generator-1999 shape.
RENAMES = """diff --git a/tests/data/x/type1.json b/tests/data/y/artificial/type-1.json
similarity index 100%
rename from tests/data/x/type1.json
rename to tests/data/y/artificial/type-1.json
diff --git a/tests/data/x/schema.json b/tests/data/y/schema.json
similarity index 59%
rename from tests/data/x/schema.json
rename to tests/data/y/schema.json
index 0fb5c52c7..0fce2310c 100644
--- a/tests/data/x/schema.json
+++ b/tests/data/y/schema.json
@@ -1,1 +1,1 @@
-old
+new
"""

# (g) file deletion — pylint-9599 shape.
DELETION = """diff --git a/tests/functional/s/singledispatch_method_py37.py b/tests/functional/s/singledispatch_method_py37.py
deleted file mode 100644
index c9269f7bf1..0000000000
--- a/tests/functional/s/singledispatch_method_py37.py
+++ /dev/null
@@ -1,2 +0,0 @@
-# doc
-x = 1
"""


def test_multi_file_multi_hunk_targets():
    assert modified_files(MULTI) == frozenset(
        {"src/cfnlint/jsonschema/_keywords.py", "src/cfnlint/other.py"}
    )


def test_new_file_dev_null_source_included():
    assert modified_files(NEW_FILE) == frozenset(
        {"releasenotes/notes/update-129f701ba07b944b.yaml"}
    )


def test_unquoted_space_path_survives():
    assert modified_files(SPACE_PATH) == frozenset({"docs/devices/G502 Lightspeed 407F.txt"})


def test_binary_one_liner_target_extracted():
    assert modified_files(BINARY) == frozenset({"vectors/crl_issuer_invalid.der"})


def test_symlink_new_file_no_newline():
    assert modified_files(SYMLINK) == frozenset({"docs/rules/pattern.md"})


def test_rename_contributes_source_and_target():
    assert modified_files(RENAMES) == frozenset(
        {
            "tests/data/x/type1.json",
            "tests/data/y/artificial/type-1.json",
            "tests/data/x/schema.json",
            "tests/data/y/schema.json",
        }
    )


def test_deletion_contributes_source():
    assert modified_files(DELETION) == frozenset(
        {"tests/functional/s/singledispatch_method_py37.py"}
    )


def test_empty_patch_yields_empty_set():
    assert modified_files("") == frozenset()
    assert modified_files("   \n") == frozenset()


def test_parse_gold_patch_disjoint_ok():
    test_patch = """diff --git a/tests/test_it.py b/tests/test_it.py
index 1..2 100644
--- a/tests/test_it.py
+++ b/tests/test_it.py
@@ -1,1 +1,1 @@
-a
+b
"""
    gold = parse_gold_patch("repo__pkg-1", MULTI, test_patch)
    assert gold.gold_files == frozenset(
        {"src/cfnlint/jsonschema/_keywords.py", "src/cfnlint/other.py"}
    )
    assert gold.test_files == frozenset({"tests/test_it.py"})
    # Property: gold files never intersect test-patch files.
    assert not (gold.gold_files & gold.test_files)


def test_parse_gold_patch_overlap_raises():
    overlapping_test = """diff --git a/src/cfnlint/other.py b/src/cfnlint/other.py
index 1..2 100644
--- a/src/cfnlint/other.py
+++ b/src/cfnlint/other.py
@@ -1,1 +1,1 @@
-a
+b
"""
    with pytest.raises(GoldPatchError, match="overlap"):
        parse_gold_patch("repo__pkg-1", MULTI, overlapping_test)


@pytest.mark.parametrize(
    "patch,test_patch",
    [
        (NEW_FILE, ""),
        (SPACE_PATH, ""),
        (BINARY, ""),
        (SYMLINK, ""),
        (RENAMES, DELETION),
    ],
)
def test_disjointness_property_holds(patch, test_patch):
    gold = parse_gold_patch("id-x", patch, test_patch)
    assert not (gold.gold_files & gold.test_files)


def test_dedupe_instances_first_wins():
    rows = [
        {"instance_id": "conan-io__conan-18153", "patch": "p1"},
        {"instance_id": "conan-io__conan-18153", "patch": "p2"},
        {"instance_id": "other-1", "patch": "p3"},
    ]
    kept = dedupe_instances(rows)
    assert [r["instance_id"] for r in kept] == ["conan-io__conan-18153", "other-1"]
    assert kept[0]["patch"] == "p1"


def test_dedupe_missing_instance_id_raises():
    with pytest.raises(GoldPatchError, match="instance_id"):
        dedupe_instances([{"patch": "p"}])


def test_coerce_test_names_native_list():
    assert coerce_test_names(["a::b", "c::d"]) == ("a::b", "c::d")


def test_coerce_test_names_json_string():
    assert coerce_test_names('["a::b"]') == ("a::b",)


def test_coerce_test_names_bad_type_raises():
    with pytest.raises(GoldPatchError, match="list"):
        coerce_test_names(42)
