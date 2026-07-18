# Phase 2 evidence — SWE-bench / SWE-bench-Live external formats

- **Date:** 2026-07-18
- **Scope:** external formats Phase 2's parsers must consume — SWE-bench-Live instance schema, gold-patch edge cases, eval-report formats, prediction input format.
- **Methods (all directly executed this session):**
  - HuggingFace datasets-server REST API (`https://datasets-server.huggingface.co/{splits,size,first-rows,rows}`) — downloaded **all 1888 rows** of `SWE-bench-Live/SWE-bench-Live` split `full` (19 pages × 100, zero `truncated_cells`) plus all 300 `lite` rows and classic `SWE-bench/SWE-bench` first-rows; raw JSON cached under the session scratchpad (`swl_full_*.json`, `swl_lite_*.json`).
  - `pip install swebench` (resolved to **swebench 4.1.0** from PyPI) into a scratch venv at `/private/tmp/swebench-venv` (Python 3.11); harness source read from `/private/tmp/swebench-venv/lib/python3.11/site-packages/swebench/harness/`.
  - GitHub raw + API fetches of `microsoft/SWE-bench-Live` at `main` and at the NIPS-era commit `cbc2a3ce1d` (2025-07-18, "feat: add verified set, code, and readme (#13)").
- Everything below is verified unless explicitly tagged **UNVERIFIED**.

---

## 1. Instance schema

### 1.1 SWE-bench-Live (`SWE-bench-Live/SWE-bench-Live`, config `default`)

Splits and row counts (datasets-server `/splits` + `/size`, fetched 2026-07-18):

| split | rows |
|---|---|
| `test` | 1000 |
| `lite` | 300 |
| `verified` | 500 |
| `full` | 1888 |

Features exactly as returned by `/first-rows?dataset=SWE-bench-Live%2FSWE-bench-Live&config=default&split=lite`:

```
repo               -> string
pull_number        -> string
instance_id        -> string
issue_numbers      -> List[string]
base_commit        -> string
patch              -> string          # the GOLD solution diff
test_patch         -> string          # the test-side diff, separate field
problem_statement  -> string
hints_text         -> string
all_hints_text     -> string
commit_urls        -> List[string]
created_at         -> timestamp[s]
commit_url         -> string
test_cmds          -> List[string]    # e.g. ['pytest -rA']
log_parser         -> string          # 'pytest' for all 1887 distinct full-split instances (measured)
difficulty         -> {files: int64, hunks: int64, lines: int64}
FAIL_TO_PASS       -> List[string]    # native list, NOT a JSON-encoded string
PASS_TO_PASS       -> List[string]    # native list, NOT a JSON-encoded string
```

Example row 0 of `lite` (trimmed):

```
instance_id: 'aws-cloudformation__cfn-lint-3798'
repo: 'aws-cloudformation/cfn-lint'   pull_number: '3798'   issue_numbers: ['3782']
base_commit: 'd5c3da9efaa4bbd1d24fa768752df3da343b1d33'
created_at: '2024-10-28T22:14:55'
test_cmds: ['pytest -rA']   log_parser: 'pytest'
difficulty: {'files': 2, 'hunks': 7, 'lines': 26}
patch: 'diff --git a/src/cfnlint/jsonschema/_keywords.py b/src/...' (3653 chars)
test_patch: 'diff --git a/test/integration/jsonschema/test_validator_cfn.py ...' (16515 chars)
FAIL_TO_PASS: ['test/unit/rules/functions/test_dynamic_reference.py::test_validate[Invalid', ...]
PASS_TO_PASS: [... 113445 chars of list ...]
```

### 1.2 Classic SWE-bench (`SWE-bench/SWE-bench`, split `test`) for comparison

Features from `/first-rows?dataset=SWE-bench%2FSWE-bench&config=default&split=test`:

```
repo, instance_id, base_commit, patch, test_patch, problem_statement, hints_text,
created_at (string), version (string), FAIL_TO_PASS (string), PASS_TO_PASS (string),
environment_setup_commit (string)
```

**Schema drift Phase 2 parsers must absorb:**

1. Classic stores `FAIL_TO_PASS`/`PASS_TO_PASS` as **JSON-encoded strings** — observed sample: `'["astropy/wcs/wcsapi/tests/test_fitswcs.py::test_non_convergence_warning"]'` — while Live stores **native lists**. A parser must handle both (`json.loads` when `isinstance(x, str)`).
2. Classic has `version` + `environment_setup_commit` (drive the mainline harness's `MAP_REPO_VERSION_TO_SPECS` lookups); Live has **neither** and instead carries per-instance `test_cmds` + `log_parser`.
3. Live-only extras: `pull_number`, `issue_numbers`, `all_hints_text`, `commit_urls`, `commit_url`, `test_cmds`, `log_parser`, `difficulty`.
4. `created_at` is `timestamp[s]` in Live (serialized `'2024-10-28T22:14:55'` by datasets-server) but a plain string in classic.

### 1.3 Measured dataset facts (full split, all 1888 rows)

- **Duplicate instance_id:** `conan-io__conan-18153` appears **twice** in `full` (1888 rows, 1887 distinct ids). Parsers must dedupe by `instance_id` or tolerate duplicates.
- `lite` ⊆ `full`: all 300 lite ids present in full (measured; 0 missing).
- 223 distinct repos in `full`; top 5: conan-io/conan (164), aws-cloudformation/cfn-lint (109), matplotlib/matplotlib (102), deepset-ai/haystack (88), pylint-dev/pylint (62).
- `log_parser` == `'pytest'` for every instance; `test_cmds` vary widely — top values: `['pytest -rA']` (984), `['SKIP_APPLICATIONS_TESTS=True pytest -rA keras']` (33), `['poetry run pytest -rA tests']` (33), `['pdm run pytest -rA']` (30), `['hatch run test:unit -rA']` (26), haystack's `pytest --cov-report xml:coverage.xml --cov="haystack" -m "not integration" -rA` (25).
- `created_at` year distribution: 2021×1, 2022×1, 2023×2, 2024×975, 2025×909 (range 2021-07-22 → 2025-09-02). The dataset card's "tasks created after 2024" is *almost* true; 4 stragglers predate it.
- Sizes: F2P per instance min 1 / median 2 / max 945; P2P per instance min 1 / median 1820 / **max 23953** (Phase 2 metric code must not be O(n²) on P2P lists). Gold patch min 287 / median 2659 / **max 2,114,789 chars**.
- `difficulty.files` == number of `diff --git` sections in `patch` for **all 1888 rows** (0 mismatches) — `difficulty` is derived from the gold patch and can serve as a parser cross-check.
- HF dataset card (fetched 2026-07-18) quotes: "The `lite` and `verified` splits will remain frozen, ensuring fair leaderboard comparisons…", "We've employed a LLM filter to automatically filter full dataset to create SWE-bench-Live Verified. The initial Verified subset contains 500 instances from 2024-07 to 2025-04.", "Each month, we will add 50 newly verified, high-quality issues". **Card drift:** the card's field list mentions an `image_key` field that is NOT present in the actual rows (verified: `'image_key' in row` → False), and its "156 repositories" does not match the 223 measured in `full`.

### 1.4 "Gold files = files modified by `patch`; `test_patch` separate by construction"

Confirmed from three independent directions:

1. **Empirical, exhaustive:** across all 1888 full-split instances, the file sets touched by `patch` and by `test_patch` (parsed from `diff --git a/X b/Y` headers) have **zero overlap in every instance** (measured: `instances with overlap: 0`).
2. **Harness gold-prediction code** (`swebench/harness/utils.py:41-52`, swebench 4.1.0): when `--predictions_path gold`, the prediction is literally the `patch` column —
   ```python
   if predictions_path == "gold":
       print("Using gold predictions")
       dataset = load_swebench_dataset(dataset_name, split)
       return [
           {KEY_INSTANCE_ID: datum[KEY_INSTANCE_ID],
            KEY_PREDICTION: datum["patch"],
            KEY_MODEL: "gold"} for datum in dataset]
   ```
   Same in SWE-bench-Live's current harness (`evaluation/evaluation.py:313`): `instance["pred_patch"] = instance["patch"]`.
3. **Harness eval-script construction** (`swebench/harness/test_spec/python.py:406-416`): before running tests, the harness *resets the files modified by `test_patch` to base and re-applies `test_patch`*, wiping any model edits to test files:
   ```python
   test_files = get_modified_files(test_patch)
   reset_tests_command = f"git checkout {base_commit} {' '.join(test_files)}"
   apply_test_patch_command = f"git apply -v - <<'{HEREDOC_DELIMITER}'\n{test_patch}\n{HEREDOC_DELIMITER}"
   ```
   And `get_modified_files` (`swebench/harness/utils.py:334-343`) is the harness's own patch→files parser — **it uses unidiff `PatchSet`**, takes the *source* side, strips the `a/` prefix, and skips `/dev/null` sources (so newly-added files are absent from the reset list):
   ```python
   def get_modified_files(patch: str) -> list[str]:
       source_files = []
       for file in PatchSet(patch):
           if file.source_file != "/dev/null":
               source_files.append(file.source_file)
       source_files = [x[2:] for x in source_files if x.startswith("a/")]
       return source_files
   ```

---

## 2. Real gold patches and edge cases

### 2.1 Edge-case frequency (measured over ALL 1888 full-split instances)

Per-instance frequencies, **gold `patch` field only**:

| edge case | instances | % of 1887 |
|---|---|---|
| ≥1 file section with multiple `@@` hunks | 1323 | 70.1% |
| multi-file patch (>1 `diff --git`) | 1130 | 59.9% |
| new file (`new file mode` + `--- /dev/null`) | 399 | 21.1% |
| `\ No newline at end of file` marker | 38 | 2.0% |
| deleted file (`deleted file mode` + `+++ /dev/null`) | 25 | 1.3% |
| rename (`rename from`/`rename to` + `similarity index`) | 6 | 0.3% |
| path containing a space in `diff --git` header | 5 | 0.3% |
| binary file (`Binary files … differ`) | 1 | 0.1% (`pyca__cryptography-12812`) |
| symlink (`new file mode 120000`) | 1 | 0.1% (`ansible__ansible-lint-4662`) |

Occurrence counts across `patch`+`test_patch` combined (file-section level): new_file 925, deleted_file 107, rename 20, `Binary files…differ` 6, `GIT binary patch` **0**, quoted `diff --git "a/…"` headers **0**, `old mode`/`new mode`-only chmod sections **0**, no-newline markers 132, sections with `--- /dev/null` 900, `+++ /dev/null` 102. 6452 total gold file sections, of which 3115 have >1 hunk. Gold-patch file extensions: py 4605, rst 470, md 313, yaml 211, json 199, toml 80, html 64, no-extension 53 — **gold patches routinely touch non-Python files** (docs, changelogs, config).

Notes for the parser:
- Paths with spaces appear **unquoted** in `diff --git` lines (git only quotes on special characters, not spaces) — naive `line.split(' ')` header parsing breaks; splitting on `' b/'` or using unidiff is required.
- Zero `GIT binary patch` blobs: binary content is always represented as the un-applyable `Binary files X and Y differ` one-liner (the Live docker images bake content differently; the gold patch is not byte-complete for that one instance).
- 0 quoted headers and 0 pure chmod sections in this dataset today — still worth handling defensively; the dataset refreshes monthly.

### 2.2 Real patch excerpts (fetched from the dataset this session, trimmed)

**(a) Standard shape — multi-file, multi-hunk (`aws-cloudformation__cfn-lint-3798`, gold `patch`, first hunk):**

```diff
diff --git a/src/cfnlint/jsonschema/_keywords.py b/src/cfnlint/jsonschema/_keywords.py
index f88514c6bb..4932b111a4 100644
--- a/src/cfnlint/jsonschema/_keywords.py
+++ b/src/cfnlint/jsonschema/_keywords.py
@@ -323,7 +323,9 @@ def maxItems(
     validator: Validator, mI: Any, instance: Any, schema: dict[str, Any]
 ) -> ValidationResult:  # pylint: disable=arguments-renamed
     if validator.is_type(instance, "array") and len(instance) > mI:
-        yield ValidationError(f"{instance!r} is too long ({mI})")
+        yield ValidationError(
+            f"expected maximum item count: {mI}, found: {len(instance)}"
+        )
```

**(b) New file with `/dev/null` header (`deepset-ai__haystack-8619`, gold `patch`):**

```diff
diff --git a/releasenotes/notes/update-store-full-path-default-value-129f701ba07b944b.yaml b/releasenotes/notes/update-store-full-path-default-value-129f701ba07b944b.yaml
new file mode 100644
index 0000000000..61a0110958
--- /dev/null
+++ b/releasenotes/notes/update-store-full-path-default-value-129f701ba07b944b.yaml
@@ -0,0 +1,4 @@
+---
+upgrade:
+  - |
+    Update default value of `store_full_path` to `False` in converters
```

**(c) Unquoted path with spaces (`pwr-solaar__solaar-2438`, gold `patch`):**

```diff
diff --git a/docs/devices/G502 Lightspeed Wireless Gaming Mouse 407F.txt b/docs/devices/G502 Lightspeed Wireless Gaming Mouse 407F.txt
index f69ae0991e..fefbfae962 100644
--- a/docs/devices/G502 Lightspeed Wireless Gaming Mouse 407F.txt
+++ b/docs/devices/G502 Lightspeed Wireless Gaming Mouse 407F.txt
@@ -1,18 +1,18 @@
-Solaar version 1.1.7
+solaar version 1.1.12rc1
```

**(d) Binary new file in a gold patch (`pyca__cryptography-12812`, gold `patch`):**

```diff
new file mode 100644
index 000000000000..1221dc5e0d6b
Binary files /dev/null and b/vectors/cryptography_vectors/x509/custom/crl_issuer_invalid_printable_string.der differ
```

**(e) Symlink + no-newline marker (`ansible__ansible-lint-4662`, gold `patch`):**

```diff
diff --git a/docs/rules/pattern.md b/docs/rules/pattern.md
new file mode 120000
index 0000000000..f4f296e99c
--- /dev/null
+++ b/docs/rules/pattern.md
@@ -0,0 +1,1 @@
+../../src/ansiblelint/rules/pattern.md
\ No newline at end of file
```

**(f) Rename with similarity index (`koxudaxi__datamodel-code-generator-1999`, `test_patch` — 100% renames have NO hunks at all):**

```diff
diff --git a/tests/data/jsonschema/discriminator_with_external_reference/artificial_folder/type1.json b/tests/data/jsonschema/discriminator_with_external_reference/inner_folder/artificial_folder/type-1.json
similarity index 100%
rename from tests/data/jsonschema/discriminator_with_external_reference/artificial_folder/type1.json
rename to tests/data/jsonschema/discriminator_with_external_reference/inner_folder/artificial_folder/type-1.json
diff --git a/tests/data/jsonschema/discriminator_with_external_reference/schema.json b/tests/data/jsonschema/discriminator_with_external_reference/inner_folder/schema.json
similarity index 59%
rename from tests/data/jsonschema/discriminator_with_external_reference/schema.json
rename to tests/data/jsonschema/discriminator_with_external_reference/inner_folder/schema.json
index 0fb5c52c7..0fce2310c 100644
```

**(g) File deletion (`pylint-dev__pylint-9599`, `test_patch`):**

```diff
diff --git a/tests/functional/s/singledispatch/singledispatch_method_py37.py b/tests/functional/s/singledispatch/singledispatch_method_py37.py
deleted file mode 100644
index c9269f7bf1..0000000000
--- a/tests/functional/s/singledispatch/singledispatch_method_py37.py
+++ /dev/null
@@ -1,23 +0,0 @@
-"""Tests for singledispatch-method"""
```

### 2.3 F2P test-name truncation artifact (dataset-wide, measured)

**644 of 11152 F2P entries (5.8%), across 148 of 1888 instances (7.8%), are parametrized pytest ids truncated at the first space** — e.g. lite row 0 has `'test/unit/rules/functions/test_dynamic_reference.py::test_validate[Invalid'` (open `[`, never closed). Root cause is the harness's own log parser (§3.4): it does `line.split()` and keeps `test_case[1]`, so any test id whose `[param]` block contains a space is stored truncated. Grading stays self-consistent (the same parser produces eval-time names), but **Phase 2 must not assume F2P/P2P entries are valid pytest node ids** (they can't be passed to `pytest <id>` verbatim, and substring/equality matching against real node ids will miss).

---

## 3. Eval report format

### 3.1 Mainline swebench (PyPI 4.1.0) — per-instance `report.json`

Built in `swebench/harness/grading.py:235-295` (`get_eval_report`). Shape — **top-level dict keyed by instance_id**:

```json
{
  "<instance_id>": {
    "patch_is_None": false,
    "patch_exists": true,
    "patch_successfully_applied": true,
    "resolved": true,
    "tests_status": {
      "FAIL_TO_PASS": {"success": ["..."], "failure": ["..."]},
      "PASS_TO_PASS": {"success": ["..."], "failure": ["..."]},
      "FAIL_TO_FAIL": {"success": [], "failure": []},
      "PASS_TO_FAIL": {"success": [], "failure": []}
    }
  }
}
```

(Shape constructed from the quoted code below — I did not run a docker evaluation this session.) Key code, `grading.py`:

```python
report_map[instance_id] = {
    "patch_is_None": False,
    "patch_exists": False,
    "patch_successfully_applied": False,
    "resolved": False,
}
if prediction[KEY_PREDICTION] is None:
    report_map[instance_id]["patch_is_None"] = True
    return report_map
report_map[instance_id]["patch_exists"] = True
eval_status_map, found = get_logs_eval(test_spec, test_log_path)
if not found:
    return report_map
report_map[instance_id]["patch_successfully_applied"] = True
...
if get_resolution_status(report) == ResolvedStatus.FULL.value:
    report_map[instance_id]["resolved"] = True
if include_tests_status:
    report_map[instance_id]["tests_status"] = report
```

Resolution semantics (`grading.py:215-232`): `resolved` ⇔ `ResolvedStatus.FULL` ⇔ F2P rate == 1 AND P2P rate == 1 (`RESOLVED_FULL` / `RESOLVED_PARTIAL` / `RESOLVED_NO` enum values, `constants/__init__.py:46-49`; empty F2P or P2P list counts as rate 1, `grading.py:194-212`). **Missing-test semantics:** `test_failed(case, sm)` returns True when `case not in sm` (`grading.py:31-35`) — a P2P test that never ran counts as FAILED in mainline.

`tests_status` categories: `FAIL_TO_PASS`, `PASS_TO_PASS` always present; `FAIL_TO_FAIL`, `PASS_TO_FAIL` present but only populated when `calculate_to_fail=True` (`grading.py:170-190`). `TestStatus` enum: `FAILED/PASSED/SKIPPED/ERROR/XFAIL` (`constants/__init__.py:52-57`); XFAIL counts as passed (`grading.py:27-28`).

### 3.2 Mainline — per-instance log directory and infra-error separation

Per-instance dir: `logs/run_evaluation/{run_id}/{model_name_or_path with / → __}/{instance_id}/` containing `run_instance.log` (`LOG_INSTANCE`), `patch.diff`, `eval.sh`, `test_output.txt` (`LOG_TEST_OUTPUT`), `report.json` (`LOG_REPORT`) — constants at `constants/__init__.py:74-76`, path assembly `reporting.py:61-67`, files written in `run_evaluation.py:141-251`.

**Infrastructure errors are distinguishable from genuine test failure by the ABSENCE of `report.json`:**

- Patch-apply failure: all three apply attempts fail (`GIT_APPLY_CMDS = ["git apply --verbose", "git apply --verbose --reject", "patch --batch --fuzz=5 -p1 -i"]`, `run_evaluation.py:64-68`) → logs `>>>>> Patch Apply Failed` to `run_instance.log` and raises `EvaluationError` (`run_evaluation.py:180-186`) → **no report.json written** → instance counted in `error_ids`.
- Timeout: `exec_run_with_timeout` returns `timed_out` → appends `"\n\nTimeout error: {timeout} seconds exceeded."` to `test_output.txt` and raises `EvaluationError` (`run_evaluation.py:206-220`) → no report.json → `error_ids`.
- Docker build failure: `BuildImageError` from `build_container` caught at `run_evaluation.py:253` → no report.json → `error_ids`.
- Genuine test failure: `report.json` exists with `patch_successfully_applied: true, resolved: false` → `unresolved_ids`.

Additionally the grader treats these **marker strings inside `test_output.txt`** as "patch did not apply / infra bad" (returns `status_map={}, applied=False` → `patch_successfully_applied: false`), `grading.py:60-76` + `constants/__init__.py:80-91`:

```
APPLY_PATCH_FAIL = ">>>>> Patch Apply Failed"
RESET_FAILED     = ">>>>> Reset Failed"
TESTS_ERROR      = ">>>>> Tests Errored"
TESTS_TIMEOUT    = ">>>>> Tests Timed Out"
START_TEST_OUTPUT = ">>>>> Start Test Output"   # grading parses only between these two
END_TEST_OUTPUT   = ">>>>> End Test Output"     # markers (fallback: whole log if empty)
```

### 3.3 Mainline — aggregate run report

`swebench/harness/reporting.py:17-160` (`make_run_report`) writes `{model_name_or_path with / → __}.{run_id}.json` in the CWD:

```json
{
  "total_instances": 0, "submitted_instances": 0, "completed_instances": 0,
  "resolved_instances": 0, "unresolved_instances": 0, "empty_patch_instances": 0,
  "error_instances": 0,
  "completed_ids": [], "incomplete_ids": [], "empty_patch_ids": [],
  "submitted_ids": [], "resolved_ids": [], "unresolved_ids": [], "error_ids": [],
  "schema_version": 2
}
```

Classification loop (`reporting.py:51-87`): no prediction → `incomplete_ids`; empty/None `model_patch` → `empty_patch_ids`; `report.json` missing, empty, unparseable, or missing keys → `error_ids`; else `resolved_ids` / `unresolved_ids` from `report[instance_id]["resolved"]`. Container/image leftovers (`unstopped_containers`, `unremoved_images`) are appended (note: the code adds them `if not client` — an upstream quirk, `reporting.py:144-151`).

### 3.4 Log parser (pytest)

`swebench/harness/log_parsers/python.py:7-26` — the source of the truncation artifact in §2.3:

```python
for line in log.split("\n"):
    if any([line.startswith(x.value) for x in TestStatus]):
        if line.startswith(TestStatus.FAILED.value):
            line = line.replace(" - ", " ")
        test_case = line.split()
        if len(test_case) <= 1:
            continue
        test_status_map[test_case[1]] = test_case[0]
```

It keys on lines starting with `FAILED`/`PASSED`/`SKIPPED`/`ERROR`/`XFAIL` (this is why every Live `test_cmds` carries `-rA` — the pytest summary section prints those lines) and takes the **second whitespace token** as the test name.

### 3.5 SWE-bench-Live current harness (`microsoft/SWE-bench-Live` `evaluation/evaluation.py` @ main, fetched 2026-07-18) — DIFFERENT report format

Per-instance `report.json` (`evaluation.py:171-194`) — **flat, NOT keyed by instance_id, no `tests_status` wrapper, no patch_* flags**:

```json
{
  "instance_id": "...",
  "resolved": false,
  "PASS_TO_PASS": {"success": [], "failure": []},
  "FAIL_TO_PASS": {"success": [], "failure": []}
}
```

Resolved criterion (`evaluation.py:183-188`): no P2P failures AND no F2P failures AND all F2P in success. **Semantic drift vs mainline:** here a P2P test missing from the log entirely is silently fine (only observed `fail` statuses count as failures, `evaluation.py:169-176`), whereas mainline counts missing P2P as FAILED (§3.1). A run graded by one harness can be `resolved` and by the other not, given flaky/missing tests.

Per-instance side files: `post_patch_log.txt` (raw test log) and `status.json` (test → `pass|fail|skip` map), `evaluation.py:122-131`. Statuses are lower-cased tri-state (`default_pytest_parser`, `evaluation.py:59-68`) instead of mainline's five-value enum.

Aggregate `results.json` in `--output_dir` (`evaluation.py:211-248`):

```json
{
  "submitted": 0, "submitted_ids": [],
  "empty_patch": 0, "empty_patch_ids": [],
  "success_ids": [], "failure_ids": [], "error_ids": [], "incomplete_ids": [],
  "success": 0, "failure": 0, "error": 0, "incomplete": 0
}
```

Infra separation here: any exception in the worker thread → `error_ids` (`evaluation.py:239-241`); `--collect-only` marks unevaluated instances `resolved: None` → `incomplete_ids`. Gold runs additionally write `gold_patch_evaluated_instances.jsonl` (only instances whose gold patch resolved — the "still valid on your machine" set, `evaluation.py:325-329`). Timeout: single container `command_timeout = 150*60` s (`evaluation.py:14,109`). Docker images: `starryzhang/sweb.eval.x86_64.{instance_id with __ → _1776_, lowercased}` (`evaluation.py:70-77`); `evaluation/README.md` confirms "Instance-level Docker images are hosted on DockerHub". Patch application: `container.apply_patch(test_patch)` then solution patch (`evaluation.py:110-111`) — note the Live harness applies test_patch FIRST and does not reset test files the way mainline does.

### 3.6 Which harness graded which SWE-bench-Live results (version-drift map)

- **NIPS-era (README @ commit `cbc2a3ce1d`, 2025-07-18):** evaluation ran through an **in-repo fork of SWE-bench/SWE-bench** — verbatim: "The evaluation code in this repo is forked from SWE-bench/SWE-bench, with only minimal modifications" — invoked as:
  ```bash
  python -m swebench.harness.run_evaluation \
      --dataset_name SWE-bench-Live/SWE-bench-Live \
      --split <lite/full> \
      --namespace starryzhang \
      --predictions_path <path_to_your_preds or gold> \
      --max_workers <num_workers> --run_id <run_id>
  ```
  The fork (fetched at `cbc2a3ce1d`: `swebench/harness/{grading.py,test_spec/test_spec.py}`) keeps the **mainline report shape** (same `report_map[instance_id] = {patch_is_None, …, tests_status}` code) and adds per-instance-field support: `make_test_spec` does `version = instance.get("version", "none")`, prefers `instance["test_cmds"]` over `MAP_REPO_VERSION_TO_SPECS` (`test_spec.py:212`), and maps `instance["log_parser"]` → parser callable (`test_spec.py:235-240`); `get_logs_eval` prefers `test_spec.log_parser` (fork `grading.py:51-54`).
- **Current (`main`, since commit `cc0ccddbf6` 2025-12-10 "add evaluation scripts"):** the fork is gone from the repo tree; `evaluation/evaluation.py` (§3.5) is the shipped harness. Its README states backward compatibility with the Python dataset "which uses swebench library for evaluation", and the code special-cases `parser == "pytest"` + empty `print_cmds` for exactly that dataset (`evaluation.py:115-126`).
- **Mainline PyPI swebench 4.1.0 canNOT grade Live instances:** `get_test_cmds` requires `MAP_REPO_VERSION_TO_SPECS[instance["repo"]][instance["version"]]` (`test_spec/utils.py:12-16`) and Live instances have no `version` and unknown repos (KeyError). Phase 2 must therefore expect **two report dialects** for Live results in the wild: fork-era mainline-shaped reports and current flat reports.
- `microsoft/SWE-bench-Live` `pyproject.toml` (@main) depends on `swebench` **unpinned** (verbatim dependency list includes bare "swebench") — a live drift risk for anyone re-running their tooling.

---

## 4. Prediction input format

### 4.1 Mainline swebench 4.1.0 (and the NIPS-era fork)

Loaded by `get_predictions_from_file` (`swebench/harness/utils.py:41-77`):

- `--predictions_path gold` → synthesized from the dataset (`model_patch = datum["patch"]`, `model_name_or_path = "gold"`).
- `.json` → either a **list** of prediction dicts or a **dict keyed by instance_id** ("compatible with SWE-agent predictions" — values used, keys discarded).
- `.jsonl` → one JSON object per line.
- Validation: every prediction must be a dict containing `instance_id`; error strings verbatim: `"Each prediction must contain 'instance_id'"`, `"Predictions path must be .json or .jsonl"`.

Canonical keys (`constants/__init__.py:66-68`):

```python
KEY_INSTANCE_ID = "instance_id"
KEY_MODEL = "model_name_or_path"
KEY_PREDICTION = "model_patch"
```

`model_name_or_path` is required in practice: it names the per-instance log dir (`reporting.py:64`) and the final report filename (`reporting.py:152-156`). `model_patch` may be `None`/`""` (→ `patch_is_None` / `empty_patch_ids`). So Phase 2's trajectory record must emit, per instance:

```json
{"instance_id": "<id>", "model_name_or_path": "<runner-id>", "model_patch": "<unified git diff>"}
```

as JSONL (one per line) — the most interoperable of the accepted encodings.

### 4.2 SWE-bench-Live current harness

`evaluation/evaluation.py:264-310` + `evaluation/README.md` — a single `.json` **dict keyed by instance_id**; only `model_patch` is read (`preds[instance_id]["model_patch"]`); `model_name_or_path` is ignored. README verbatim:

```json
{
    "instance_id1": {"model_patch": "git diff", ...},
    "instance_id2": {"model_patch": "git diff", ...}
}
```

A JSONL list is NOT accepted by this harness (plain `json.load`). Producing the mainline JSONL and offering a trivial `{p["instance_id"]: p for p in preds}` conversion covers both.

### 4.3 Patch application at eval time (what `model_patch` must survive)

Mainline applies the model patch with three fallbacks (`run_evaluation.py:64-68,166-186`): `git apply --verbose` → `git apply --verbose --reject` → `patch --batch --fuzz=5 -p1 -i`, inside `/testbed` as root. Live's current harness uses `container.apply_patch` (from the `launch` submodule) with a best-effort cd-to-git-root wrapper (`evaluation.py:79-95`). Practical consequence: `model_patch` should be a plain `git diff`-style unified diff rooted at the repo top (`a/…`, `b/…` prefixes, `-p1` compatible).

---

## 5. Consolidated parser requirements extracted from evidence

1. Accept both list-typed (Live) and JSON-string-typed (classic) `FAIL_TO_PASS`/`PASS_TO_PASS`.
2. Dedupe instances by `instance_id` (real duplicate: `conan-io__conan-18153` ×2 in `full`).
3. Gold-file extraction must mirror `get_modified_files` semantics (unidiff PatchSet; renames report source AND target; `/dev/null` sources are new files) and must survive: unquoted paths with spaces, new/deleted files, renames without hunks (`similarity index 100%`), `Binary files … differ` sections with no hunks, symlink sections whose single hunk body is a path string, `\ No newline at end of file`, up to 2.1 MB patches.
4. Never treat F2P/P2P entries as well-formed pytest node ids (5.8% of F2P entries are space-truncated parametrized ids); match with the same `line.split()[1]` normalization the harness uses.
5. Read BOTH report dialects: mainline/fork `{iid: {patch_is_None, patch_exists, patch_successfully_applied, resolved, tests_status{F2P/P2P/F2F/P2F × success/failure}}}` and Live-current flat `{instance_id, resolved, PASS_TO_PASS{success,failure}, FAIL_TO_PASS{success,failure}}`; plus both aggregates (`<model>.<run_id>.json` schema_version 2 vs `results.json` success/failure/error/incomplete).
6. Classify infra failures as: missing/empty/unparseable per-instance `report.json`, `error_ids` membership in the aggregate, or marker strings (`>>>>> Patch Apply Failed`, `>>>>> Reset Failed`, `>>>>> Tests Errored`, `>>>>> Tests Timed Out`) / `Timeout error: … seconds exceeded.` in the logs — all distinct from `resolved: false` with `patch_successfully_applied: true` (genuine failure).
7. Emit predictions as JSONL of `{instance_id, model_name_or_path, model_patch}`; derive the Live dict-keyed `.json` by re-keying.

---

## 6. UNVERIFIED items

- **Did not execute any docker evaluation** — all report.json/results.json shapes are reconstructed from the quoted harness source (exact code paths cited), not from an observed run artifact.
- Whether the official SWE-bench-Live leaderboard currently accepts fork-format or flat-format reports for submission — the submissions repo (`swe-bench-live/submission`) was not inspected.
- The fork-era pip distribution name (if the in-repo fork was ever published to PyPI under a separate name) — not found; evaluation was documented as running from the cloned repo (`pip install -e .`).
- `TestStatus.XFAIL` handling in the Live current harness: `default_pytest_parser` maps anything containing "pass" → `pass`; XFAIL contains no "pass" so it maps to `fail` — inferred from code (`evaluation.py:59-68`), not tested against a real XFAIL log.
- HF dataset card claims (`image_key` field, "156 repositories", monthly +50 cadence) are quoted from the card; the first two contradict direct measurement and are flagged as card drift, cadence not independently checkable.
- swebench PyPI 4.1.0 is the version pip resolved on 2026-07-18; whether SWE-bench-Live's unpinned `swebench` dependency resolves to the same at their CI time cannot be pinned from here.

## 7. Raw artifacts kept for the reconciler

Scratchpad (`/private/tmp/claude-501/…/scratchpad/`): `swl_full_{0..1800}.json` (all 1888 full-split rows), `swl_lite_*.json`, `sw_classic_first_rows.json`, `swl_evaluation.py` + `swl_validation.py` (Live harness @ main), `fork_grading.py` + `fork_test_spec.py` + `fork_test_spec_python.py` (fork @ `cbc2a3ce1d`), analysis scripts `analyze_patches.py`, `per_instance.py`, `extract_excerpts.py`. Scratch venv with swebench 4.1.0: `/private/tmp/swebench-venv`.
