# Branch/Diff Task Layer — Plan T0: Seeds and Vocabulary (S0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen the product's task vocabulary to five names (`change_review`, `release_notes` join `repo_qa`, `vuln`, `bug_loc`) with their six seed sections, and land every eval-side primitive the new framings score with — the closed heading/dimension vocabularies, three trajectory check kinds, two heading gates, `gold_recall.key_prefix`, the report's `breakout_key`, the record-keyed partition on every fitness, `validate_checks` at load time, `gold_diff.enclosing_symbols`, and the `swe-bench-verified-test-gap` dataset in its S0 (prompt-supplied diff) shape — so that nothing here depends on the multi-branch P1/P2 surface.

**Architecture:** The widening is enumeration-only (one tuple, six seed sections, a dated comment): the grammar regex and `RENDERER_VERSION` do not move, and every derived surface (section headers, delivery maps, the eval firewall) picks the names up with no local copy. Eval-side additions are registrations (`@check_registry.register`, `@gate_registry.register`) and small typed vocabularies in a new base-install module `datasets/change_tasks.py`; the heading gates build their patterns from that module at call time so no YAML ever spells a heading word. The test-gap dataset mirrors the SWE-bench bug-loc loader (parquet pin, fixture JSONL, fake repo cache) and renders the fix patch into the prompt because P0 servers have no change slice.

**Tech Stack:** Python 3.11+, the product's `harness/core` skill loader and description grammar, `pydocs_eval` (pydantic run config, registries, unidiff), pytest. Eval tests run with `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`; product tests with `pytest tests/ -q`.

**Spec:** `docs/superpowers/specs/2026-09-04-branch-diff-task-layer-design.md` (commit `19f6b3a`) — §6.1 (vocabularies), §6.2–§6.3 (seed drafts), §6.7 (the widening event), §7.1 (`swe-bench-verified-test-gap`, checks, splits), §7.5 (check and gate kinds), §11 AC-1…AC-11, AC-25, AC-26. This is the first of its three plans (§10.1); T1 (live-branch datasets, arm configs, the P2.7 gate) and T2 (landing-unit datasets) follow in their own files.

## Global Constraints

- **`TASK_NAMES` is APPENDED**, exactly `("repo_qa", "vuln", "bug_loc", "change_review", "release_notes")`, so every existing section keeps its position and the enumerated-set error messages read in declaration order (§6.7).
- **The grammar regex `_HEADER_RE` and `RENDERER_VERSION` are byte-identical before and after** (AC-4); `_HEADER_RE` already matches the new names. Hygiene: the six new header lines have zero hits across tracked files before the seed gains them (record the grep in the regex comment block).
- **Seed caps**: each new `TASK_HEAD` / `HARNESS_TASK_HEAD` section ≤ 1,200 characters (300 tokens at `CHARS_PER_TOKEN = 4`); the two `TASK_HEAD` sections contain none of `ask_your_docs`, `catalog`, `pre-injected`; no head names a branch, sha or tag (R11); the seed stays a canonical byte surface (`normalize(seed) == seed`).
- **No MCP surface change**; the registration golden `tests/fixtures/goldens/mcp_registration_surface.json` does not move.
- **The widening lands before any `change_review` / `release_notes` task id is minted** (`parse_framed_task_id` is vocabulary-anchored) — Task 1 is the first commit of this plan.
- **Recorded cost**: the ask `delivery_map_digest()` literal in `tests/harness/ask_your_docs/test_binding.py` moves (regenerate, never hand-edit); the external digest does not; no committed ledger row carries the old ask arm hash; the twelve ADR 0011 real-trajectory fixtures are keyed by `artifact_hash` and are unmoved.
- **Base-install boundary**: `datasets/change_tasks.py`, `gates.py`, `checks.py`, `trajectory_evidence.py`, `_split.py`, `task_ids.py` import no `pydocs_mcp` at module scope.
- **Every `EvalTask.metadata` value is a string** (`Mapping[str, str]`), so counts are spelled `"3"`.
- **Naming**: plain English, `StrEnum` for closed vocabularies with UPPER_SNAKE members; functions 4–20 lines; files under 500 lines; error messages carry the offending value and the expected shape.
- **Git authorship**: commits carry no `Co-Authored-By` trailer, no `--author`, no signing flags.
- **Gates before every push**: `ruff format --check python/ tests/ benchmarks/`, `ruff check python/ tests/ benchmarks/`, `mypy python/pydocs_mcp`, `complexipy python/pydocs_mcp --max-complexity-allowed 15`, `vulture python/pydocs_mcp --min-confidence 80`, `pytest tests/ --ignore=tests/test_parity.py -q`, `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`, `uv lock --check`; restore `complexipy-snapshot.json` after a local complexipy run.

---

## File map

| Path | Status | Owns |
|---|---|---|
| `python/pydocs_mcp/harness/core/skill_artifact_loader.py` | modify | `TASK_NAMES` five names + the dated comment |
| `python/pydocs_mcp/application/description_source.py` | modify | the "seventh event" comment paragraph (no regex change) |
| `python/pydocs_mcp/harness/core/skills/search_guidance_seed.md` | modify | six new sections in canonical order |
| `tests/harness/core/test_skill_artifact_loader.py`, `tests/harness/ask_your_docs/test_binding.py`, `tests/harness/ask_your_docs/test_tool_binding.py`, `tests/harness/core/test_guidance_fold.py` | modify | enumerated-set pins (five / sixteen), digest regeneration, R11 head test, fold parity for `change_review` |
| `benchmarks/tests/datasets/test_task_ids.py`, `benchmarks/tests/optimize/test_change_review_widening.py` | modify / new | vocabulary-joining pin, firewall accept/reject pins |
| `benchmarks/src/pydocs_eval/datasets/change_tasks.py` | new | `ChangeReviewDimension`, `ReviewHeading`, `ReleaseNotesHeading`, `REQUIRED_REVIEW_HEADINGS`, `heading_regex`, `headings_present`, task-name constants |
| `benchmarks/src/pydocs_eval/optimize/rubric/trajectory_evidence.py` | modify | `slice_consulted`, `graph_consulted`, `card_consulted` |
| `benchmarks/src/pydocs_eval/optimize/rubric/gates.py` | modify | `review_headings_present`, `release_headings_present` |
| `benchmarks/src/pydocs_eval/optimize/rubric/checks.py` | modify | `gold_recall` `key_prefix` |
| `benchmarks/src/pydocs_eval/optimize/run_config.py` | modify | `ask_rubric_change_review`, `ask_rubric_release_notes` sections; `validate_checks` wiring |
| `benchmarks/src/pydocs_eval/reporting/report.py` | modify | `breakout_key` parameter |
| `benchmarks/src/pydocs_eval/optimize/_split.py`, `optimize/fitness/paired_agent.py`, `optimize/fitness/retrieval.py`, `optimize/fitness/ask_rubric.py` | modify | `split_tasks_by_record` shared helper; record-keyed partition on every fitness |
| `benchmarks/src/pydocs_eval/trajectory/gold_diff.py` | modify | `enclosing_symbols(patch)` |
| `benchmarks/src/pydocs_eval/datasets/change_review.py` | new | `SweBenchVerifiedTestGapDataset` (S0 shape) |
| `benchmarks/src/pydocs_eval/datasets/__init__.py` | modify | export + registration import |
| `benchmarks/tests/fixtures/swe_bench_verified_test_gap_mini.jsonl` | new | fixture rows with `test_patch` |
| `benchmarks/tests/{datasets,optimize,reporting,trajectory}/test_*.py` | new / modify | AC-8…AC-11 |
| `docs/superpowers/specs/2026-07-27-harness-run-contract-design.md`, `docs/superpowers/specs/2026-07-26-retriever-centric-harness-platform-design.md`, `CHANGELOG.md`, `benchmarks/README.md` | modify | the dated amendments (AC-25), the dataset subsection, README audit (AC-26) |

---

### Task 1: The widening event — five task names, six seed sections, the dated comment, the product pins

**Files:**
- Modify: `python/pydocs_mcp/harness/core/skill_artifact_loader.py:60-61` (`TASK_NAMES`) and the comment block above it
- Modify: `python/pydocs_mcp/application/description_source.py:167-172` (append the seventh-event paragraph to the regex comment stack)
- Modify: `python/pydocs_mcp/harness/core/skills/search_guidance_seed.md`
- Modify: `tests/harness/core/test_skill_artifact_loader.py`, `tests/harness/ask_your_docs/test_binding.py`, `tests/harness/ask_your_docs/test_tool_binding.py`, `tests/harness/core/test_guidance_fold.py`
- Test: `pytest tests/harness -q`

**Interfaces:**
- Produces: `TASK_NAMES == ("repo_qa", "vuln", "bug_loc", "change_review", "release_notes")`; derived `TASK_HEAD_SECTION_HEADERS` (5), `HARNESS_TASK_HEAD_SECTION_HEADERS` (10, harness-major), `SKILL_ARTIFACT_HEADERS` (16); the packaged seed with sixteen sections; `binding.delivery_map_digest()` regenerated.

- [ ] **Step 1: Hygiene grep (must be zero hits BEFORE the seed changes)**

```bash
git grep -n -F -e "=== TASK_HEAD: change_review ===" -e "=== TASK_HEAD: release_notes ===" -e "=== HARNESS_TASK_HEAD: ask_your_docs.change_review ===" -e "=== HARNESS_TASK_HEAD: ask_your_docs.release_notes ===" -e "=== HARNESS_TASK_HEAD: external.change_review ===" -e "=== HARNESS_TASK_HEAD: external.release_notes ===" -- . ':!docs/superpowers'
```

Expected: no output (exit 1). The spec documents are excluded because they quote the header lines as prose.

- [ ] **Step 2: Edit the product pins to the new set (the failing tests)**

In `tests/harness/core/test_skill_artifact_loader.py`:

- `test_task_head_section_header_carries_no_harness_factor`: add
  `assert sal.task_head_section_header("change_review") == "TASK_HEAD: change_review"` and
  `assert sal.task_head_section_header("release_notes") == "TASK_HEAD: release_notes"`.
- `test_task_head_section_header_rejects_unknown_task_naming_the_set`: add `and "change_review" in message and "release_notes" in message`.
- `test_task_names_feed_both_the_task_head_and_harness_task_head_tiers`: the tuple becomes `("repo_qa", "vuln", "bug_loc", "change_review", "release_notes")` and the headers tuple gains `"TASK_HEAD: change_review", "TASK_HEAD: release_notes"`.
- Rename `test_the_ten_section_keys_in_canonical_order` to `test_the_sixteen_section_keys_in_canonical_order` and set the expectations to:

```python
    assert sal.HARNESS_TASK_HEAD_SECTION_HEADERS == (
        "HARNESS_TASK_HEAD: ask_your_docs.repo_qa",
        "HARNESS_TASK_HEAD: ask_your_docs.vuln",
        "HARNESS_TASK_HEAD: ask_your_docs.bug_loc",
        "HARNESS_TASK_HEAD: ask_your_docs.change_review",
        "HARNESS_TASK_HEAD: ask_your_docs.release_notes",
        "HARNESS_TASK_HEAD: external.repo_qa",
        "HARNESS_TASK_HEAD: external.vuln",
        "HARNESS_TASK_HEAD: external.bug_loc",
        "HARNESS_TASK_HEAD: external.change_review",
        "HARNESS_TASK_HEAD: external.release_notes",
    )
    assert sal.SKILL_ARTIFACT_HEADERS == (
        "BACKBONE",
        "TASK_HEAD: repo_qa",
        "TASK_HEAD: vuln",
        "TASK_HEAD: bug_loc",
        "TASK_HEAD: change_review",
        "TASK_HEAD: release_notes",
        *sal.HARNESS_TASK_HEAD_SECTION_HEADERS,
    )
```

- `test_retired_task_names_are_refused_by_the_section_key_builders`: the asserted list literal becomes `"['repo_qa', 'vuln', 'bug_loc', 'change_review', 'release_notes']"`.
- Append the R11 test (§12):

```python
_SHA_LITERAL = re.compile(r"\b[0-9a-f]{7,40}\b")
_BRANCH_LITERAL = re.compile(r"branch=\S+")
_TAG_LITERAL = re.compile(r"\bv\d+\.\d+")
_NINE_TOOLS = (
    "get_overview",
    "search_codebase",
    "get_symbol",
    "get_context",
    "get_references",
    "get_why",
    "grep",
    "glob",
    "read_file",
)


@pytest.mark.parametrize("task_name", sal.TASK_NAMES)
def test_every_task_head_names_a_tool_and_no_branch_sha_or_tag_literal(task_name: str) -> None:
    # R11 (task-layer spec §9): a head that named a branch, sha or tag would be
    # silently overridden by the ask interceptor's pins and would not transfer
    # across corpora. Slice VALUES (scope=diff) are the task's substance and
    # are allowed.
    text = sal.load_packaged_skill().task_head(task_name)
    assert any(tool in text for tool in _NINE_TOOLS), task_name
    assert not _BRANCH_LITERAL.search(text), task_name
    assert not _SHA_LITERAL.search(text), task_name
    assert not _TAG_LITERAL.search(text), task_name
```

(add `import re` to the module imports).

In `tests/harness/ask_your_docs/test_binding.py`: extend the `>=` set in `test_delivery_map_digest_is_stable_and_documents_the_channels` with `"TASK_HEAD: change_review"`, `"TASK_HEAD: release_notes"`, `"HARNESS_TASK_HEAD: ask_your_docs.change_review"`, `"HARNESS_TASK_HEAD: ask_your_docs.release_notes"`; add
`assert "HARNESS_TASK_HEAD: external.change_review" in binding.RECOGNIZED_UNDELIVERED_SECTIONS` and the same for `external.release_notes`; in `test_task_heads_are_delivered_not_merely_recognized` extend the loop tuple with `"TASK_HEAD: change_review", "TASK_HEAD: release_notes"`. Leave the digest literal for Step 6.

In `tests/harness/ask_your_docs/test_tool_binding.py:83` the loop tuple becomes `("repo_qa", "vuln", "bug_loc", "change_review", "release_notes")`.

In `tests/harness/core/test_guidance_fold.py` append:

```python
def test_change_review_folds_the_same_three_tiers_in_both_harnesses(tmp_path) -> None:
    # The fourth framing folds exactly like the third: BACKBONE, its task head,
    # this harness's head — same order, same separator — in both harnesses.
    pytest.importorskip("pydocs_mcp.harness.ask_your_docs.agent")
    from pydocs_mcp.harness.ask_your_docs.agent import _resolved_skill_block
    from pydocs_mcp.harness.core.skill_artifact_loader import load_packaged_skill

    artifact = load_packaged_skill()
    sections = {
        "BACKBONE": artifact.backbone,
        "TASK_HEAD: change_review": artifact.task_head("change_review"),
        "HARNESS_TASK_HEAD: external.change_review": artifact.harness_task_head(
            "external", "change_review"
        ),
        "HARNESS_TASK_HEAD: ask_your_docs.change_review": artifact.harness_task_head(
            "ask_your_docs", "change_review"
        ),
    }
    external = fold_guidance_sections(sections, harness_name="external", task_name="change_review")
    assert external == "\n".join(
        (sections["BACKBONE"], sections["TASK_HEAD: change_review"], sections["HARNESS_TASK_HEAD: external.change_review"])
    )
    assert _resolved_skill_block(None, "change_review") == "\n".join(
        (sections["BACKBONE"], sections["TASK_HEAD: change_review"], sections["HARNESS_TASK_HEAD: ask_your_docs.change_review"])
    )
```

- [ ] **Step 3: Run the product suites to verify they fail**

Run: `pytest tests/harness/core/test_skill_artifact_loader.py tests/harness/ask_your_docs/test_binding.py tests/harness/ask_your_docs/test_tool_binding.py tests/harness/core/test_guidance_fold.py -q`
Expected: FAIL — the tuple is still three names; `task_head_section_header("change_review")` raises.

- [ ] **Step 4: Widen the tuple and record the event**

In `python/pydocs_mcp/harness/core/skill_artifact_loader.py`, append to the comment block above the tuples:

```python
# Fourth and fifth framings, 2026-09-04 (owner directive: the branch/diff task
# layer, docs/superpowers/specs/2026-09-04-branch-diff-task-layer-design.md
# §6.7): ``change_review`` (a review of one change — a live branch or a landed
# unit — against its base) and ``release_notes`` (a changelog section over a
# range of landing units) are APPENDED, in that order. Their sub-variants
# (test gap, conflict pre-check, doc drift, PR description) are DIMENSIONS of
# change_review stamped on the eval side (``metadata["dimension"]``), and the
# API-surface diff is a heading of release_notes — neither is a task name.
# Datasets: change_review <- {pr-review-py, swe-bench-verified-test-gap,
# pr-review-py-doc-drift, pr-review-py-description}, release_notes <-
# {pydocs-self-releases, changelog-tagged-py}. Enumeration-only again.
```

and set

```python
TASK_NAMES = ("repo_qa", "vuln", "bug_loc", "change_review", "release_notes")
```

In `python/pydocs_mcp/application/description_source.py`, append this paragraph to the comment stack immediately above `_HEADER_RE = re.compile(`:

```python
# A seventh event, 2026-09-04 (owner directive: the branch/diff task layer —
# ``change_review`` and ``release_notes`` as the fourth and fifth framings), is
# ENUMERATION-ONLY in the same sense as the fourth and sixth: this regex is
# not touched — both names already match the ``TASK_HEAD: [a-z_]+`` and
# ``HARNESS_TASK_HEAD: [a-z_]+\.[a-z_]+`` shapes — so ``parse_sections`` /
# ``render_sections`` / ``normalize`` stay byte-identical functions and
# ``RENDERER_VERSION`` does not move. Only the per-artifact allowed set grows
# (``skill_artifact_loader.TASK_NAMES``, step (2) of the widening protocol).
# Hygiene held on 2026-09-04: ``=== TASK_HEAD: change_review ===``,
# ``=== TASK_HEAD: release_notes ===`` and the four
# ``=== HARNESS_TASK_HEAD: {ask_your_docs,external}.{change_review,release_notes} ===``
# lines had zero hits across tracked files (the spec documents quote them as
# prose and are excluded) before the seed gained them, so no recorded
# document changes meaning under the widened allowed set.
```

- [ ] **Step 5: Append the six seed sections in canonical order**

`python/pydocs_mcp/harness/core/skills/search_guidance_seed.md` is a delimited document in section-key order; insert the two `TASK_HEAD` sections after the `TASK_HEAD: bug_loc` section (before `=== HARNESS_TASK_HEAD: ask_your_docs.repo_qa ===`), the two `ask_your_docs.*` heads after `HARNESS_TASK_HEAD: ask_your_docs.bug_loc` (before `=== HARNESS_TASK_HEAD: external.repo_qa ===`), and the two `external.*` heads at the end of the file. Each section is the header line, the body, and exactly one trailing newline. The bodies are the spec's drafts, verbatim:

```
=== TASK_HEAD: change_review ===
Review of one change — a live branch or a landed unit — against its base,
and the answer is a review: what changed, what it breaks, what it misses.
Start from the change itself: search_codebase with scope=diff (the hunks) or
scope=changed (the whole symbols) on that branch, never the default scope,
which sees the whole tree. Every hunk hit names its enclosing symbol; follow
the load-bearing ones into the graph with get_references — impact for the
blast radius, callers for the tests that exercise them. A landed unit has no
tree, so run the graph walk on the base branch. Judge each finding against
code you read, not against the diff header. Answer in fixed sections:
summary; findings, each cited to path and symbol and naming the caller it
breaks when it breaks one; blast radius; test gap (changed symbols with no
changed test, each with the test file that should cover it); doc drift
(changed symbols whose docs did not change) when docs are indexed. If the
server offers no diff slice, start from the card's file list instead. Two
failures: reviewing the tree instead of the change, and a finding that
names no symbol.
=== TASK_HEAD: release_notes ===
Release notes for a range of landing units — the first-parent landings
between two tags, or since the last tag — and the answer is a changelog
section. Enumerate the units first: get_overview on the base branch lists
the landed units in the retention window with their tags; get_overview on a
unit's sha gives its subject, files changed and hunk count. Read the change,
not the tree: search_codebase or grep with scope=diff on the unit's sha; a
unit has no tree, so confirm anything further on the base branch. Group by
effect on a user — added, changed, fixed, removed — one bullet per
user-visible effect; several units may share a bullet and internal churn is
left out. Cite each bullet to its unit sha and the paths it touched, and
list changed public signatures under a separate changed-API heading. Two
failures: one bullet per commit subject copied verbatim, and a bullet no
unit in the range supports.
```

```
=== HARNESS_TASK_HEAD: ask_your_docs.change_review ===
The catalog is already in your prompt; skip orientation calls. Any pinned
branch or slice is applied for you; otherwise name the slice yourself. Start
from the diff hits. The review sections are the answer; skip the
example-call snippet.
=== HARNESS_TASK_HEAD: ask_your_docs.release_notes ===
The catalog is already in your prompt; skip orientation calls. The catalog's
branch listing names the base branch to enumerate from. The changelog
section is the answer; skip the example-call snippet.
```

```
=== HARNESS_TASK_HEAD: external.change_review ===
No catalog is pre-injected: orient first — get_overview with the branch, or
the unit's sha, for its card: base, merge-base, files changed. Answers must
be self-contained: every path in full from the repository root, with the
line numbers you read.
=== HARNESS_TASK_HEAD: external.release_notes ===
No catalog is pre-injected: orient first — get_overview on the base branch
for the landed listing, then one get_overview per unit in the range. Answers
must be self-contained: every bullet names its unit sha and full paths.
```

Verify the canonical surface and the caps before running the suite:

```bash
PYTHONPATH=python python -c "
from importlib import resources
from pydocs_mcp.application import description_source as ds
from pydocs_mcp.harness.core import skill_artifact_loader as sal
text = resources.files('pydocs_mcp.harness.core.skills').joinpath('search_guidance_seed.md').read_text('utf-8')
assert ds.normalize(text) == text, 'not canonical'
sections = ds.parse_sections(text, allowed=sal.SKILL_ARTIFACT_HEADERS)
for key in sal.SKILL_ARTIFACT_HEADERS[1:]:
    print(f'{len(sections[key]):5d} chars  {key}')
print('ok', len(sections))
"
```

Expected: sixteen sections, `ok 16`; the `change_review` head prints `1128` chars (≤ 1200), `release_notes` `909`, the four harness heads 237 / 200 / 247 / 223. If `normalize` differs (a missing final newline, a stray blank line), fix the file until it prints `ok`.

- [ ] **Step 6: Regenerate the ask delivery-map digest**

```bash
PYTHONPATH=python python -c "from pydocs_mcp.harness.ask_your_docs import binding; print(binding.delivery_map_digest())"
```

Paste the printed 64-hex value over the literal in `test_delivery_map_digest_is_stable_and_documents_the_channels` (`tests/harness/ask_your_docs/test_binding.py`). Then confirm the external digest did NOT move:

Run: `pytest tests/harness/external/test_binding.py -q -k digest`
Expected: PASS with the literal `0c29b7de…` unchanged (the external map is pattern-keyed).

- [ ] **Step 7: Run the product suites**

Run: `pytest tests/harness -q && pytest tests/test_doc_conformance.py -q`
Expected: PASS — including `test_seed_task_head_sections_carry_no_harness_local_facts` (AC-2), `test_seed_sections_fit_their_caps` (AC-2), `test_seed_is_canonical_byte_surface` (AC-3), `test_packaged_seed_loads_with_every_enumerated_section` (AC-1), the prompt-freeze and seed-parity pins (untouched: the core prompt pool is outside the freeze; `SYSTEM_PROMPT` bytes did not change).

- [ ] **Step 8: Commit**

```bash
git add python/pydocs_mcp/harness/core/skill_artifact_loader.py python/pydocs_mcp/application/description_source.py python/pydocs_mcp/harness/core/skills/search_guidance_seed.md tests/harness/core/test_skill_artifact_loader.py tests/harness/ask_your_docs/test_binding.py tests/harness/ask_your_docs/test_tool_binding.py tests/harness/core/test_guidance_fold.py
git commit -m "harness: widen TASK_NAMES to change_review + release_notes with six seed sections (enumeration-only)"
```

---

### Task 2: Eval-side vocabulary pins — task ids and the load firewall

**Files:**
- Modify: `benchmarks/tests/datasets/test_task_ids.py`
- Create: `benchmarks/tests/optimize/test_change_review_widening.py`

**Interfaces:**
- Consumes: `TASK_NAMES` (Task 1); `parse_framed_task_id`, `record_id_of` (`datasets/task_ids.py`); `load_firewall._require_enumerated_task_name`, `ArmCell`.

- [ ] **Step 1: Write the tests**

Append to `benchmarks/tests/datasets/test_task_ids.py` (inside the class holding `test_bug_loc_joining_the_vocabulary_bites_only_a_repo_named_bug_loc`):

```python
    @pytest.mark.parametrize("name", ["change_review", "release_notes"])
    def test_the_fourth_and_fifth_framings_bite_only_a_repo_named_after_them(self, name: str) -> None:
        # The 2026-09-04 widening's new-vocabulary duty, discharged as before:
        # for either name to change an existing parse some corpus would have to
        # mint it as a middle segment — and nothing shipped does.
        assert parse_framed_task_id(f"swe_qa/{name}/12", task_names=_TASK_NAMES) is not None
        assert parse_framed_task_id(f"swe_qa/{name}/12", task_names=_RETIRED_TASK_NAMES) is None

    def test_a_change_review_id_parses_to_its_record(self) -> None:
        """AC-7."""
        parsed = parse_framed_task_id("pr-review-py/change_review/x", task_names=_TASK_NAMES)
        assert parsed is not None and parsed.record_id == "x"
        assert parsed.dataset == "pr-review-py" and parsed.task_name == "change_review"
```

Create `benchmarks/tests/optimize/test_change_review_widening.py`:

```python
"""The fourth/fifth-framing widening, seen from the eval side — AC-6.

The load firewall reads the product tuple with no local copy, so admitting a
``change_review`` arm is the WHOLE eval-side change; the parent commit's
three-name tuple (spelled here as a literal) still rejects it.
"""

from __future__ import annotations

import pytest

from pydocs_eval.optimize.arms import ArmCell
from pydocs_eval.optimize.ask_binding import known_task_names
from pydocs_eval.optimize.load_firewall import _require_enumerated_task_name

_PARENT_TASK_NAMES = ("repo_qa", "vuln", "bug_loc")


def _arm(task_name: str) -> ArmCell:
    return ArmCell.model_validate(
        {
            "runner": "pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner",
            "settings": {"workspace": "~/pydocs-index", "model": "m"},
            "dataset": "swe-bench-verified-loc",
            "task_name": task_name,
            "guidance": "search_skill",
            "scoring": {"objective": "rubric_verdict", "rubric": "ask_rubric"},
        }
    )


@pytest.mark.parametrize("task_name", ["change_review", "release_notes"])
def test_the_firewall_admits_the_new_names_with_the_live_tuple(task_name: str) -> None:
    from pydocs_mcp.harness.core.skill_artifact_loader import TASK_NAMES

    assert known_task_names() == TASK_NAMES
    _require_enumerated_task_name(_arm(task_name), task_names=known_task_names(), arm_label="arm")


@pytest.mark.parametrize("task_name", ["change_review", "release_notes"])
def test_the_parent_tuple_rejected_them(task_name: str) -> None:
    with pytest.raises(ValueError, match=task_name):
        _require_enumerated_task_name(_arm(task_name), task_names=_PARENT_TASK_NAMES, arm_label="arm")
```

- [ ] **Step 2: Run the eval tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_task_ids.py benchmarks/tests/optimize/test_change_review_widening.py benchmarks/tests/optimize/test_bug_loc_arms.py benchmarks/tests/optimize/test_repo_qa_arms.py benchmarks/tests/optimize/candidates/test_firewall_parity.py benchmarks/tests/optimize/test_search_skill_artifact.py -q`
Expected: PASS (the arms tests assert `known_task_names() == TASK_NAMES` live; the firewall-parity negative pin and the seed round-trip are unaffected). If the `ArmCell` shape in `_arm` rejects a key, copy the exact keys from `optimize_search_skill_bug_loc.yaml`'s `arms:` block.

- [ ] **Step 3: Commit**

```bash
git add benchmarks/tests/datasets/test_task_ids.py benchmarks/tests/optimize/test_change_review_widening.py
git commit -m "eval: pin the change_review / release_notes vocabulary through task ids and the load firewall"
```

---

### Task 3: `datasets/change_tasks.py` — dimensions, headings, the heading grammar

**Files:**
- Create: `benchmarks/src/pydocs_eval/datasets/change_tasks.py`
- Test: `benchmarks/tests/datasets/test_change_tasks.py`

**Interfaces:**
- Produces: `CHANGE_REVIEW_TASK_NAME = "change_review"`, `RELEASE_NOTES_TASK_NAME = "release_notes"`, `DIMENSION_METADATA_KEY = "dimension"`; `ChangeReviewDimension`, `ReviewHeading`, `ReleaseNotesHeading` (`StrEnum`s); `REQUIRED_REVIEW_HEADINGS: Mapping[ChangeReviewDimension, tuple[ReviewHeading, ...]]`; `heading_regex(heading: str) -> re.Pattern[str]`; `headings_present(answer: str, headings: Iterable[str]) -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/datasets/test_change_tasks.py`:

```python
"""Closed vocabularies of the change-review / release-notes framings — AC-8."""

from __future__ import annotations

from enum import StrEnum

from pydocs_eval.datasets.change_tasks import (
    REQUIRED_REVIEW_HEADINGS,
    ChangeReviewDimension,
    ReleaseNotesHeading,
    ReviewHeading,
    heading_regex,
    headings_present,
)


def test_the_three_vocabularies_are_str_enums():
    assert issubclass(ChangeReviewDimension, StrEnum)
    assert issubclass(ReviewHeading, StrEnum)
    assert issubclass(ReleaseNotesHeading, StrEnum)
    assert [d.value for d in ChangeReviewDimension] == [
        "blast_radius",
        "test_gap",
        "conflict_precheck",
        "doc_drift",
        "pr_description",
    ]
    assert [h.value for h in ReleaseNotesHeading] == ["added", "changed", "fixed", "removed", "changed api"]


def test_required_headings_cover_every_dimension():
    assert set(REQUIRED_REVIEW_HEADINGS) == set(ChangeReviewDimension)
    assert REQUIRED_REVIEW_HEADINGS[ChangeReviewDimension.DOC_DRIFT][-1] is ReviewHeading.DOC_DRIFT
    assert REQUIRED_REVIEW_HEADINGS[ChangeReviewDimension.PR_DESCRIPTION] == (ReviewHeading.SUMMARY,)
    assert ReviewHeading.DOC_DRIFT not in REQUIRED_REVIEW_HEADINGS[ChangeReviewDimension.BLAST_RADIUS]


def test_heading_grammar_accepts_markdown_and_colon_forms_case_insensitively():
    answer = "## Summary\nfine.\n\nBLAST RADIUS:\n- a\n\n  test gap  \nnone\nfindings are below\n"
    assert headings_present(answer, ReviewHeading) == (
        ReviewHeading.SUMMARY,
        ReviewHeading.BLAST_RADIUS,
        ReviewHeading.TEST_GAP,
    )
    assert heading_regex("findings").search("findings are below") is None  # a heading is a whole line
    assert heading_regex("changed api").search("### Changed API:\n") is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_change_tasks.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the module**

```python
"""Closed vocabularies of the change-review and release-notes framings.

Task-layer design §6.1: ``change_review`` answers in fixed sections and its
sub-variants are DIMENSIONS stamped into ``EvalTask.metadata["dimension"]``
(not task names); ``release_notes`` answers under Keep-a-Changelog headings.
The heading gates (``rubric/gates.py``) build their patterns from THIS module
at call time, so no config file ever spells a heading word (R9).

BASE install by contract: stdlib only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum

CHANGE_REVIEW_TASK_NAME = "change_review"
RELEASE_NOTES_TASK_NAME = "release_notes"
#: The ``EvalTask.metadata`` key a change_review dataset stamps its dimension into.
DIMENSION_METADATA_KEY = "dimension"


class ChangeReviewDimension(StrEnum):
    """Which change_review question a dataset asks (one dataset per dimension)."""

    BLAST_RADIUS = "blast_radius"  # the default dimension
    TEST_GAP = "test_gap"
    CONFLICT_PRECHECK = "conflict_precheck"
    DOC_DRIFT = "doc_drift"
    PR_DESCRIPTION = "pr_description"


class ReviewHeading(StrEnum):
    """Headings of a change_review answer, in answer order."""

    SUMMARY = "summary"
    FINDINGS = "findings"
    BLAST_RADIUS = "blast radius"
    TEST_GAP = "test gap"
    DOC_DRIFT = "doc drift"


class ReleaseNotesHeading(StrEnum):
    """Headings of a release_notes answer, in answer order."""

    ADDED = "added"
    CHANGED = "changed"
    FIXED = "fixed"
    REMOVED = "removed"
    CHANGED_API = "changed api"


# Which headings a dimension REQUIRES; the review_headings_present gate reads
# this. DOC_DRIFT is required under its own dimension and optional otherwise
# (the change_review head's "when docs are indexed" clause).
REQUIRED_REVIEW_HEADINGS: Mapping[ChangeReviewDimension, tuple[ReviewHeading, ...]] = {
    ChangeReviewDimension.BLAST_RADIUS: (
        ReviewHeading.SUMMARY,
        ReviewHeading.FINDINGS,
        ReviewHeading.BLAST_RADIUS,
        ReviewHeading.TEST_GAP,
    ),
    ChangeReviewDimension.TEST_GAP: (
        ReviewHeading.SUMMARY,
        ReviewHeading.FINDINGS,
        ReviewHeading.BLAST_RADIUS,
        ReviewHeading.TEST_GAP,
    ),
    ChangeReviewDimension.CONFLICT_PRECHECK: (ReviewHeading.SUMMARY, ReviewHeading.FINDINGS),
    ChangeReviewDimension.DOC_DRIFT: (
        ReviewHeading.SUMMARY,
        ReviewHeading.FINDINGS,
        ReviewHeading.BLAST_RADIUS,
        ReviewHeading.TEST_GAP,
        ReviewHeading.DOC_DRIFT,
    ),
    ChangeReviewDimension.PR_DESCRIPTION: (ReviewHeading.SUMMARY,),
}


def heading_regex(heading: str) -> re.Pattern[str]:
    """The §6.1 heading grammar: ``^\\s*(#{1,6}\\s*)?<heading>\\s*:?\\s*$``, one line,
    case-insensitive — a markdown heading, a bare line, or a colon-suffixed label."""
    return re.compile(
        rf"^\s*(?:#{{1,6}}\s*)?{re.escape(heading)}\s*:?\s*$", re.IGNORECASE | re.MULTILINE
    )


def headings_present(answer: str, headings: Iterable[str]) -> tuple[str, ...]:
    """The members of ``headings`` that ``answer`` carries as a heading line, in order."""
    return tuple(h for h in headings if heading_regex(str(h)).search(answer) is not None)


__all__ = [
    "CHANGE_REVIEW_TASK_NAME",
    "DIMENSION_METADATA_KEY",
    "RELEASE_NOTES_TASK_NAME",
    "REQUIRED_REVIEW_HEADINGS",
    "ChangeReviewDimension",
    "ReleaseNotesHeading",
    "ReviewHeading",
    "heading_regex",
    "headings_present",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_change_tasks.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/pydocs_eval/datasets/change_tasks.py benchmarks/tests/datasets/test_change_tasks.py
git commit -m "eval: change_review dimensions, review/release headings, heading grammar"
```

---

### Task 4: Three check kinds, two heading gates, `gold_recall.key_prefix`

**Files:**
- Modify: `benchmarks/src/pydocs_eval/optimize/rubric/trajectory_evidence.py` (append the three checks)
- Modify: `benchmarks/src/pydocs_eval/optimize/rubric/gates.py` (append the two gates)
- Modify: `benchmarks/src/pydocs_eval/optimize/rubric/checks.py` (`GoldRecall`)
- Modify: `benchmarks/tests/optimize/test_rubric_gates.py` (`_SHIPPED_KINDS`)
- Test: `benchmarks/tests/optimize/test_change_checks.py`, `benchmarks/tests/optimize/test_rubric_checks.py`

**Interfaces:**
- Consumes: `read_server_tool_events`, `check_registry` (trajectory_evidence / checks); `gate_registry`, `GatePredicate` (gates); `REQUIRED_REVIEW_HEADINGS`, `ReleaseNotesHeading`, `headings_present`, `ChangeReviewDimension` (Task 3).
- Produces: check kinds `slice_consulted(scopes)`, `graph_consulted(directions)`, `card_consulted(tools, min_calls)`; gate kinds `review_headings_present(dimension)`, `release_headings_present()`; `gold_recall` params `key_prefix` (mutually exclusive with `keys`).

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/optimize/test_change_checks.py`:

```python
"""The three trajectory check kinds and the two heading gates — AC-9."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.optimize.rubric.checks import Check, check_registry, evaluate_check
from pydocs_eval.optimize.rubric.gates import evaluate_gate, gate_registry
from pydocs_eval.optimize.rubric.model import GateCheck
from tests.optimize._trajectories import make_trajectory
from tests.optimize.test_trajectory_evidence import _tool_event, _trace


def _task(extra: dict[str, object] | None = None) -> EvalTask:
    return EvalTask(
        task_id="pr-review-py/change_review/r1",
        query="review this branch",
        gold=GoldAnswer(file_set=("pkg/mod.py",), extra=dict(extra or {})),
        corpus_source=lambda: None,  # type: ignore[arg-type]
    )


def _score(kind: str, params: dict, trace_dir: Path | None) -> float:
    trajectory = make_trajectory(trace_dir=trace_dir)
    return evaluate_check(Check(name=kind, kind=kind, params=params, fail=None), _task(), trajectory).score


def test_registries_carry_the_new_kinds():
    assert {"slice_consulted", "graph_consulted", "card_consulted"} <= set(check_registry.names())
    assert {"review_headings_present", "release_headings_present"} <= set(gate_registry.names())


def test_slice_consulted_reads_scope_on_the_two_slice_tools(tmp_path):
    hit = _trace(tmp_path / "a", _tool_event("search_codebase", args={"query": "q", "scope": "diff"}))
    miss = _trace(tmp_path / "b", _tool_event("search_codebase", args={"query": "q", "scope": "project"}))
    other_tool = _trace(tmp_path / "c", _tool_event("get_symbol", args={"target": "x", "scope": "diff"}))
    no_scope = _trace(tmp_path / "d", _tool_event("grep", args={"pattern": "p"}))
    params = {"scopes": ["changed", "diff"]}
    assert _score("slice_consulted", params, hit) == 1.0
    assert _score("slice_consulted", params, miss) == 0.0
    assert _score("slice_consulted", params, other_tool) == 0.0
    assert _score("slice_consulted", params, no_scope) == 0.0  # a missing scope never counts
    assert _score("slice_consulted", params, None) == 0.0  # no events at all


def test_graph_consulted_defaults_a_missing_direction_to_callers(tmp_path):
    impact = _trace(tmp_path / "a", _tool_event("get_references", args={"target": "x", "direction": "impact"}))
    omitted = _trace(tmp_path / "b", _tool_event("get_references", args={"target": "x"}))
    inherits = _trace(tmp_path / "c", _tool_event("get_references", args={"target": "x", "direction": "inherits"}))
    assert _score("graph_consulted", {"directions": ["impact", "callers"]}, impact) == 1.0
    assert _score("graph_consulted", {"directions": ["callers"]}, omitted) == 1.0
    assert _score("graph_consulted", {"directions": ["impact"]}, inherits) == 0.0


def test_card_consulted_counts_calls_to_the_named_tools(tmp_path):
    two = _trace(
        tmp_path / "a",
        _tool_event("get_overview", args={"branch": "main"}, seq=1),
        _tool_event("get_overview", args={"branch": "abc"}, seq=2),
    )
    one = _trace(tmp_path / "b", _tool_event("get_overview", args={}))
    params = {"tools": ["get_overview"], "min_calls": 2}
    assert _score("card_consulted", params, two) == 1.0
    assert _score("card_consulted", params, one) == 0.0
    assert _score("card_consulted", {"tools": ["get_overview"], "min_calls": 1}, one) == 1.0


def _gate(kind: str, answer: str, params: dict | None = None) -> bool:
    return evaluate_gate(GateCheck(name=kind, kind=kind, params=dict(params or {})), _task(), make_trajectory(answer=answer))


def test_review_headings_gate_builds_from_the_dimension():
    full = "## Summary\nx\n## Findings\n- a\n## Blast radius\nb\n## Test gap\nnone\n"
    assert _gate("review_headings_present", full, {"dimension": "blast_radius"})
    assert not _gate("review_headings_present", full, {"dimension": "doc_drift"})  # DOC_DRIFT missing
    assert _gate("review_headings_present", full + "## Doc drift\nnone\n", {"dimension": "doc_drift"})
    assert _gate("review_headings_present", "Summary:\nonly\n", {"dimension": "pr_description"})
    with pytest.raises(ValueError):
        _gate("review_headings_present", full, {"dimension": "TEST_GAP"})  # the value spelling, not the member


def test_release_headings_gate_needs_any_one_heading():
    assert _gate("release_headings_present", "## Fixed\n- x (abc1234; a.py)\n")
    assert not _gate("release_headings_present", "Here is what changed: nothing.\n")


def test_gold_recall_key_prefix_scores_exactly_the_prefixed_keys():
    task = _task({"landing_0": "abc1234", "landing_1": "def5678", "bullet_0": "abc1234 fixed it", "tag_to": "v0.5.1"})
    check = Check(name="cov", kind="gold_recall", params={"key_prefix": "landing_"}, fail=None)
    assert evaluate_check(check, task, make_trajectory(answer="see abc1234")).score == 0.5
    assert evaluate_check(check, task, make_trajectory(answer="abc1234 and def5678")).score == 1.0
    both = Check(name="cov", kind="gold_recall", params={"key_prefix": "landing_", "keys": ["file_set"]}, fail=None)
    with pytest.raises(ValueError, match="key_prefix"):
        evaluate_check(both, task, make_trajectory(answer=""))
```

In `benchmarks/tests/optimize/test_rubric_gates.py`, `_SHIPPED_KINDS` becomes the nine names in the registry's order and the test is renamed `test_registry_ships_exactly_the_nine_kinds`:

```python
_SHIPPED_KINDS = (
    "answer_regex",
    "gold_substring",
    "gold_substring_all",
    "max_turns",
    "max_wall_seconds",
    "min_answer_chars",
    "release_headings_present",
    "review_headings_present",
    "used_indexed_tools",
)
```

(If `gate_registry.names()` returns registration order rather than sorted order, append the two new names at the end instead — the assertion pins whatever order the registry reports.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/optimize/test_change_checks.py benchmarks/tests/optimize/test_rubric_gates.py -q`
Expected: FAIL — unknown kinds; `key_prefix` ignored.

- [ ] **Step 3: Add the three checks**

Append to `benchmarks/src/pydocs_eval/optimize/rubric/trajectory_evidence.py` (add `from typing import ClassVar` to the imports and extend `__all__` with the three class names):

```python
# --- Task-layer trajectory checks (task-layer design §7.5) --------------------
#
# Three predicates over the same ``tool_call`` records ``gold_location_evidenced``
# reads. They measure HOW the run retrieved — which slice, which graph
# direction, how many cards — not what it said, so a review that sounds
# grounded but never left the default scope scores 0.0 here.

#: The two tools that accept the changed / diff slice values.
SLICE_TOOLS = frozenset({"search_codebase", "grep"})
#: The contract default when ``get_references`` is called without a direction.
_DEFAULT_REFERENCES_DIRECTION = "callers"


def _param_names(params: Mapping[str, object], key: str) -> frozenset[str]:
    value = params.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"params[{key!r}] must be a list of names; got {value!r}")
    return frozenset(str(v) for v in value)


def _event_args(event: Mapping[str, object]) -> Mapping[str, object]:
    args = event.get("args")
    return args if isinstance(args, Mapping) else {}


@check_registry.register("slice_consulted")
@dataclass(frozen=True, slots=True)
class SliceConsulted:
    """1.0 iff any search_codebase / grep call sent ``scope`` in ``params["scopes"]``.

    A missing ``scope`` never counts as a slice (the server default is the
    whole tree), and the value is read from the ARGS the server recorded.
    """

    required_params: ClassVar[tuple[str, ...]] = ("scopes",)

    def __call__(
        self, task: EvalTask, trajectory: Trajectory, params: Mapping[str, object]
    ) -> float:
        _ = task
        scopes = _param_names(params, "scopes")
        for event in read_server_tool_events(trajectory.trace_dir):
            if str(event.get("tool", "")) not in SLICE_TOOLS:
                continue
            if str(_event_args(event).get("scope", "")) in scopes:
                return 1.0
        return 0.0


@check_registry.register("graph_consulted")
@dataclass(frozen=True, slots=True)
class GraphConsulted:
    """1.0 iff any get_references call used a direction in ``params["directions"]``
    (an omitted direction is the contract default ``callers``)."""

    required_params: ClassVar[tuple[str, ...]] = ("directions",)

    def __call__(
        self, task: EvalTask, trajectory: Trajectory, params: Mapping[str, object]
    ) -> float:
        _ = task
        directions = _param_names(params, "directions")
        for event in read_server_tool_events(trajectory.trace_dir):
            if str(event.get("tool", "")) != "get_references":
                continue
            direction = str(_event_args(event).get("direction") or _DEFAULT_REFERENCES_DIRECTION)
            if direction in directions:
                return 1.0
        return 0.0


@check_registry.register("card_consulted")
@dataclass(frozen=True, slots=True)
class CardConsulted:
    """1.0 iff at least ``params["min_calls"]`` calls went to ``params["tools"]``."""

    required_params: ClassVar[tuple[str, ...]] = ("tools",)

    def __call__(
        self, task: EvalTask, trajectory: Trajectory, params: Mapping[str, object]
    ) -> float:
        _ = task
        tools = _param_names(params, "tools")
        minimum = int(params.get("min_calls", 1))  # type: ignore[call-overload]
        calls = sum(
            1
            for event in read_server_tool_events(trajectory.trace_dir)
            if str(event.get("tool", "")) in tools
        )
        return 1.0 if calls >= minimum else 0.0
```

- [ ] **Step 4: Add the two gates**

Append to `benchmarks/src/pydocs_eval/optimize/rubric/gates.py` (add the import `from pydocs_eval.datasets.change_tasks import REQUIRED_REVIEW_HEADINGS, ChangeReviewDimension, ReleaseNotesHeading, headings_present`):

```python
@gate_registry.register("review_headings_present")
@dataclass(frozen=True, slots=True)
class ReviewHeadingsPresent:
    """Every heading ``REQUIRED_REVIEW_HEADINGS[params["dimension"]]`` names is
    a heading line of the answer (task-layer design §6.1 grammar, §7.5).

    Built from the enum and the mapping at call time so no config spells a
    heading word (R9). ``dimension`` is the enum VALUE (``"test_gap"``).
    """

    required_params: ClassVar[tuple[str, ...]] = ("dimension",)

    def __call__(
        self, task: EvalTask, trajectory: Trajectory, params: Mapping[str, object]
    ) -> bool:
        _ = task
        dimension = ChangeReviewDimension(str(params["dimension"]))
        required = REQUIRED_REVIEW_HEADINGS[dimension]
        return len(headings_present(trajectory.answer, required)) == len(required)


@gate_registry.register("release_headings_present")
@dataclass(frozen=True, slots=True)
class ReleaseHeadingsPresent:
    """At least one ``ReleaseNotesHeading`` is a heading line of the answer."""

    def __call__(
        self, task: EvalTask, trajectory: Trajectory, params: Mapping[str, object]
    ) -> bool:
        _ = (task, params)
        return bool(headings_present(trajectory.answer, ReleaseNotesHeading))
```

- [ ] **Step 5: Add `key_prefix` to `gold_recall`**

In `benchmarks/src/pydocs_eval/optimize/rubric/checks.py`, replace `GoldRecall.__call__` and add the helper:

```python
    def __call__(
        self, task: EvalTask, trajectory: Trajectory, params: Mapping[str, object]
    ) -> float:
        candidates = _gold_recall_candidates(task, params)
        if not candidates:
            return 1.0
        return sum(1 for c in candidates if c in trajectory.answer) / len(candidates)


def _gold_recall_candidates(task: EvalTask, params: Mapping[str, object]) -> list[str]:
    """``keys`` (the gate filter) OR ``key_prefix`` (every ``extra`` key with that
    prefix — per-record key counts such as ``landing_<i>``), never both."""
    keys, prefix = params.get("keys"), params.get("key_prefix")
    if keys is not None and prefix is not None:
        raise ValueError(
            f"gold_recall takes keys OR key_prefix, not both; got keys={keys!r} "
            f"and key_prefix={prefix!r}"
        )
    if prefix is None:
        return _all_gate_candidates(task, keys)
    return [
        value
        for key, value in task.gold.extra.items()
        if key.startswith(str(prefix)) and isinstance(value, str)
    ]
```

Update the `GoldRecall` docstring's last sentence: ``` ``params["keys"]`` reuses the gate candidate filter (``file_set``, ``cve_id``, ``cwe_id_0``…); ``params["key_prefix"]`` selects every ``extra`` key with that prefix instead. ```

- [ ] **Step 6: AC-8's "no config spells a heading word" test**

Append to `benchmarks/tests/optimize/test_change_checks.py`:

```python
def test_no_shipped_config_spells_a_heading_word_in_a_regex_param():
    """AC-8 (R9): heading lists live in the enums, never in YAML patterns."""
    from importlib import resources

    import yaml

    from pydocs_eval.datasets.change_tasks import ReleaseNotesHeading, ReviewHeading

    words = {h.value for h in (*ReviewHeading, *ReleaseNotesHeading)}
    configs = [p for p in resources.files("pydocs_eval.optimize.configs").iterdir() if p.name.endswith(".yaml")]
    assert configs
    for config in configs:
        document = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        for section in document.values():
            if not isinstance(section, dict):
                continue
            rows = [*section.get("gates", []), *section.get("checks", [])]
            for row in rows:
                pattern = str((row.get("params") or {}).get("pattern", "")).lower()
                assert not any(word in pattern for word in words), (config.name, row)
```

- [ ] **Step 7: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/optimize/test_change_checks.py benchmarks/tests/optimize/test_rubric_gates.py benchmarks/tests/optimize/test_rubric_checks.py benchmarks/tests/optimize/test_trajectory_evidence.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add benchmarks/src/pydocs_eval/optimize/rubric/trajectory_evidence.py benchmarks/src/pydocs_eval/optimize/rubric/gates.py benchmarks/src/pydocs_eval/optimize/rubric/checks.py benchmarks/tests/optimize/test_change_checks.py benchmarks/tests/optimize/test_rubric_gates.py
git commit -m "eval: slice/graph/card trajectory checks, review/release heading gates, gold_recall key_prefix"
```

---

### Task 5: The two declared rubric sections and `validate_checks` at load time

**Files:**
- Modify: `benchmarks/src/pydocs_eval/optimize/run_config.py` (two fields, two rows in `_configured_rubric_sections`, one validation call)
- Test: `benchmarks/tests/optimize/test_change_review_sections.py`

**Interfaces:**
- Consumes: `validate_checks`, `deterministic_checks` (`rubric/checks.py`); `ArmCell.scoring.rubric`, `ArmCell.dataset`; the check/gate kinds of Task 4.
- Produces: `OptimizeRunConfig.ask_rubric_change_review`, `OptimizeRunConfig.ask_rubric_release_notes` (both `AskRubricSettings | None`); `_validate_section_checks_per_arm_dataset(cfg)` called from `load_run_config`.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/optimize/test_change_review_sections.py`:

```python
"""The fourth and fifth rubric objectives + validate_checks at load — AC-10."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.optimize.run_config import (
    OptimizeRunConfig,
    _configured_rubric_sections,
    load_run_config,
)

_YAML = """
artifact: search_skill
optimizer: skillopt
ladder:
  - [ask_rubric, 6, 4]
  - [ask_rubric, 24, 1]
accept_margin: 0.02
budget: { max_trials: 20, max_usd: 40.0, wall_timeout_seconds: 14400 }
dataset: { name: swe-bench-verified-loc }
rng_seed: 0

ask_rubric_change_review:
  runner:
    model: claude-sonnet-5
    architecture: text_react
  gates:
    - { name: grounded, kind: used_indexed_tools, params: { n: 1 } }
  checks:
    - { name: findings_located, kind: gold_recall, params: { keys: [file_set] }, weight: 0.3, required: false, fail: null, weight_by_type: { WEIGHT_KEY: 0.5 } }
    - { name: located_by_evidence, kind: gold_location_evidenced, weight: 0.3, required: false, fail: null }
    - { name: change_consulted, kind: slice_consulted, params: { scopes: [changed, diff] }, weight: 0.2, required: false, fail: null }
    - { name: graph_consulted, kind: graph_consulted, params: { directions: [impact, callers] }, weight: 0.1, required: false, fail: null }
    - { name: sections_present, kind: review_headings_present, params: { dimension: test_gap }, weight: 0.0, required: true, fail: 1.0 }
  gate_weight: 0.5
  rubric_weight: 0.5
  keep_deterministic_on_skip: true
  criteria:
    - { name: findings_real, weight: 0.5, description: "Findings are real and cited to path and symbol." }
    - { name: review_not_tree, weight: 0.5, description: "Reviews the change, not the tree." }

arms:
  - runner: pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner
    settings: { workspace: ~/pydocs-index, model: claude-sonnet-5 }
    tool_names: null
    dataset: swe-bench-verified-loc
    task_name: change_review
    guidance: search_skill
    scoring:
      objective: rubric_verdict
      rubric: ask_rubric_change_review
      tracked: [gold_recall, gold_location_evidenced]
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_both_sections_are_declared_fields() -> None:
    assert {"ask_rubric_change_review", "ask_rubric_release_notes"} <= set(OptimizeRunConfig.model_fields)


def test_the_change_review_section_loads_and_binds(tmp_path: Path) -> None:
    cfg = load_run_config(_write(tmp_path, _YAML.replace("WEIGHT_KEY", "swe-bench-verified-loc")))
    assert sorted(_configured_rubric_sections(cfg)) == ["ask_rubric_change_review"]
    assert {arm.task_name for arm in cfg.arms} == {"change_review"}


def test_a_dimension_named_weight_by_type_is_rejected_at_load(tmp_path: Path) -> None:
    # Dimensions are DATASETS: weight_by_type keys on the dataset prefix.
    with pytest.raises(ValueError, match="test_gap"):
        load_run_config(_write(tmp_path, _YAML.replace("WEIGHT_KEY", "test_gap")))


def test_a_bound_section_without_a_required_check_is_rejected_at_load(tmp_path: Path) -> None:
    text = _YAML.replace("WEIGHT_KEY", "swe-bench-verified-loc")
    text = text.replace("  gates:\n    - { name: grounded, kind: used_indexed_tools, params: { n: 1 } }\n", "  gates: []\n")
    text = "\n".join(line for line in text.splitlines() if "sections_present" not in line) + "\n"
    with pytest.raises(ValueError, match="no required applicable check"):
        load_run_config(_write(tmp_path, text))


def test_shipped_configs_still_load() -> None:
    from importlib import resources

    for name in ("optimize_search_skill_bug_loc.yaml", "optimize_search_skill_repo_qa.yaml", "optimize_search_skill.yaml"):
        load_run_config(Path(str(resources.files("pydocs_eval.optimize.configs").joinpath(name))))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/optimize/test_change_review_sections.py -q`
Expected: FAIL — `extra="forbid"` rejects `ask_rubric_change_review`.

- [ ] **Step 3: Declare the sections and wire the validation**

In `benchmarks/src/pydocs_eval/optimize/run_config.py`, after `ask_rubric_file_localization`:

```python
    #: The FOURTH named rubric objective — a change review scored on the
    #: findings' paths, their trajectory evidence, and the slices / graph
    #: directions the run consulted (task-layer design §7.1). Bound by the
    #: ``change_review`` arms; per-dimension weights key on the DATASET prefix.
    ask_rubric_change_review: AskRubricSettings | None = None
    #: The FIFTH — a release section scored on landing-unit coverage over the
    #: per-record ``landing_<i>`` keys plus the cards consulted (§7.2).
    ask_rubric_release_notes: AskRubricSettings | None = None
```

In `_configured_rubric_sections` add the rows `"ask_rubric_change_review": cfg.ask_rubric_change_review,` and `"ask_rubric_release_notes": cfg.ask_rubric_release_notes,` (and amend its docstring's last sentence to `…and what the third (2026-07-28) and the fourth / fifth (2026-09-04) actually cost.`).

Add the import `from pydocs_eval.optimize.rubric.checks import check_registry, deterministic_checks, validate_checks` (replacing the existing `check_registry` import line) and, in `load_run_config`, after `_assert_registry_keys(cfg)`:

```python
    _validate_section_checks_per_arm_dataset(cfg)
```

with

```python
def _validate_section_checks_per_arm_dataset(cfg: OptimizeRunConfig) -> None:
    """AC-10: ``validate_checks`` at load, per bound section.

    The task types are the DATASET names of the arms binding the section —
    ``task_id_prefix`` of a framed id is its dataset — so a ``weight_by_type``
    keyed on a dimension name fails here, not at trial 14. Gates are folded in
    through ``deterministic_checks`` (a gate is the required screen a section
    otherwise lacks). Sections no arm binds are left to ``validate_rubric_config``.
    """
    for name, section in _configured_rubric_sections(cfg).items():
        datasets = sorted({arm.dataset for arm in cfg.arms if arm.scoring.rubric == name})
        if not datasets:
            continue
        rubric = section.rubric_config
        validate_checks(deterministic_checks(rubric.gates, rubric.checks), known_task_types=datasets)
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/optimize/test_change_review_sections.py benchmarks/tests/optimize/test_run_config.py benchmarks/tests/optimize/test_bug_loc_arms.py benchmarks/tests/optimize/test_repo_qa_arms.py benchmarks/tests/optimize/test_arms.py -q`
Expected: PASS (every shipped arm config's bound sections carry a required gate, so the new call accepts them).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/pydocs_eval/optimize/run_config.py benchmarks/tests/optimize/test_change_review_sections.py
git commit -m "eval: ask_rubric_change_review / ask_rubric_release_notes sections; validate_checks per bound arm dataset at load"
```

---

### Task 6: The report's `breakout_key`

**Files:**
- Modify: `benchmarks/src/pydocs_eval/reporting/report.py`
- Test: `benchmarks/tests/reporting/test_report_breakout_key.py`

**Interfaces:**
- Produces: `format_report(..., breakout_key: str = "qa_type")`; `_format_category_breakout(task_rows, key)`, `_distinct_categories(task_rows, key)`, `_category_mean(task_rows, category, metric_name, key)`.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/reporting/test_report_breakout_key.py`:

```python
"""``format_report(breakout_key=…)`` — AC-8a: one breakout row per dimension."""

from __future__ import annotations

from pydocs_eval.reporting.report import format_report

_RESULTS = {("pydocs-mcp", "baseline"): {"recall@1": (0.5, 0.3, 0.7), "mrr": (0.6, 0.4, 0.8)}}


def _rows(key: str, values: tuple[str, ...]):
    return {
        ("pydocs-mcp", "baseline"): tuple(
            {"metadata": {key: value}, "scores": {"recall@1": 1.0 if i % 2 == 0 else 0.0, "mrr": 0.5}}
            for i, value in enumerate(values)
        )
    }


def test_dimension_breakout_renders_one_row_per_dimension():
    report = format_report(
        sweep_results=_RESULTS,
        dataset_name="change-review-sweep",
        n_tasks=3,
        task_rows=_rows("dimension", ("test_gap", "blast_radius", "test_gap")),
        breakout_key="dimension",
    )
    assert "## By dimension" in report
    section = report.split("## By dimension", 1)[1]
    assert section.count("| test_gap |") == 1 and section.count("| blast_radius |") == 1
    assert "| dimension |" in section  # the header cell names the key


def test_default_key_is_byte_identical_to_the_pre_parameter_render():
    rows = _rows("qa_type", ("What", "Where"))
    default = format_report(sweep_results=_RESULTS, dataset_name="d", n_tasks=2, task_rows=rows)
    explicit = format_report(
        sweep_results=_RESULTS, dataset_name="d", n_tasks=2, task_rows=rows, breakout_key="qa_type"
    )
    assert default == explicit and "## By qa_type" in default


def test_rows_without_the_key_render_no_breakout():
    report = format_report(
        sweep_results=_RESULTS,
        dataset_name="d",
        n_tasks=2,
        task_rows=_rows("qa_type", ("What", "Where")),
        breakout_key="dimension",
    )
    assert "## By" not in report
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/reporting/test_report_breakout_key.py -q`
Expected: FAIL — `TypeError: unexpected keyword argument 'breakout_key'`.

- [ ] **Step 3: Thread the key**

In `benchmarks/src/pydocs_eval/reporting/report.py`:

- `format_report` gains the keyword `breakout_key: str = _CATEGORY_KEY` after `metric_specs`, documents it (`breakout_key: The ``metadata`` key the category breakout groups on. Defaults to ``qa_type``; a change_review sweep passes ``"dimension"``.`), and calls `_format_category_breakout(task_rows, breakout_key)`.
- `_distinct_categories(task_rows: TaskRows, key: str = _CATEGORY_KEY)` reads `metadata.get(key)`.
- `_category_mean(task_rows, category, metric_name, key: str = _CATEGORY_KEY)` compares `metadata.get(key) != category`.
- `_format_category_breakout(task_rows: TaskRows | None, key: str = _CATEGORY_KEY)` passes `key` to both, uses `header_cells = [key, *_METRIC_ROW_ORDER]`, and returns `f"## By {key}\n\n{table}"`; its docstring's first line becomes `A ``## By <key>`` section: one row per category, one metric column each.`

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/reporting -q`
Expected: PASS (the existing `test_report_category_breakout.py` is byte-identical under the default).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/pydocs_eval/reporting/report.py benchmarks/tests/reporting/test_report_breakout_key.py
git commit -m "eval report: breakout_key parameter (qa_type default; dimension for change_review sweeps)"
```

---

### Task 7: Record-keyed partition on every fitness

**Files:**
- Modify: `benchmarks/src/pydocs_eval/optimize/_split.py` (`split_tasks_by_record`)
- Modify: `benchmarks/src/pydocs_eval/optimize/fitness/ask_rubric.py:311-330` (`_split_tasks` uses the helper)
- Modify: `benchmarks/src/pydocs_eval/optimize/fitness/paired_agent.py:158-167` (`_split_task_ids`)
- Modify: `benchmarks/src/pydocs_eval/optimize/fitness/retrieval.py:33-38, :74-76`
- Test: `benchmarks/tests/optimize/test_record_keyed_partition.py`

**Interfaces:**
- Consumes: `partition_task_ids` (`_split.py`), `record_id_of` (`datasets/task_ids.py`), `EvalTask`.
- Produces: `split_tasks_by_record(tasks: Sequence[EvalTask], *, split: str, task_names: Sequence[str] = ()) -> tuple[EvalTask, ...]`.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/optimize/test_record_keyed_partition.py`:

```python
"""Sibling rows over one record share a split side (platform spec §5.4 item 2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.optimize._agent_track_binding import AgentTrackConfig
from pydocs_eval.optimize._split import partition_task_ids, split_tasks_by_record, task_split
from pydocs_eval.optimize.fitness.paired_agent import ArtifactInjection, PairedAgentFitness


def _task(task_id: str, record_id: str = "") -> EvalTask:
    return EvalTask(task_id, "q", GoldAnswer(), lambda: Path("/dev/null"), record_id=record_id)


def _straddling_pair() -> tuple[EvalTask, EvalTask]:
    """Two framings of ONE record whose task ids hash to different sides."""
    for i in range(1000):
        loc = f"swe-bench-verified-loc/bug_loc/inst-{i}"
        gap = f"swe-bench-verified-test-gap/change_review/inst-{i}"
        if task_split(loc) != task_split(gap):
            return _task(loc, f"inst-{i}"), _task(gap, f"inst-{i}")
    raise AssertionError("no straddling pair in 1000 ids")


def test_sibling_rows_share_a_side():
    loc, gap = _straddling_pair()
    filler = [_task(f"other/repo_qa/{i}", f"other-{i}") for i in range(6)]
    train = split_tasks_by_record([loc, gap, *filler], split="train")
    holdout = split_tasks_by_record([loc, gap, *filler], split="holdout")
    on_train = {t.task_id for t in train} >= {loc.task_id, gap.task_id}
    on_holdout = {t.task_id for t in holdout} >= {loc.task_id, gap.task_id}
    assert on_train != on_holdout  # together, on exactly one side


def test_pre_framing_rows_keep_their_task_id_side():
    ids = [f"swe-qa-pro:{i:04d}" for i in range(8)]
    tasks = [_task(task_id) for task_id in ids]
    train, holdout = partition_task_ids(ids)
    assert tuple(t.task_id for t in split_tasks_by_record(tasks, split="train")) == train
    assert tuple(t.task_id for t in split_tasks_by_record(tasks, split="holdout")) == holdout


def test_the_framed_id_parse_is_the_fallback_for_rows_without_a_record_field():
    loc, gap = _straddling_pair()
    bare = [_task(loc.task_id), _task(gap.task_id), *[_task(f"o/{i}") for i in range(6)]]
    names = ("repo_qa", "vuln", "bug_loc", "change_review", "release_notes")
    train = {t.task_id for t in split_tasks_by_record(bare, split="train", task_names=names)}
    assert (loc.task_id in train) == (gap.task_id in train)


class _SiblingDataset:
    name = "siblings"
    revision = "0"

    async def tasks(self) -> AsyncIterator[EvalTask]:
        loc, gap = _straddling_pair()
        for task in (loc, gap, *[_task(f"o/{i}", f"o-{i}") for i in range(6)]):
            yield task


@pytest.mark.asyncio
async def test_paired_agent_fitness_partitions_on_records(tmp_path: Path):
    fitness = PairedAgentFitness(
        runner=object(),
        judge=object(),
        dataset=_SiblingDataset(),
        ledger_path=tmp_path / "ledger.jsonl",
        agent_cfg=AgentTrackConfig(max_tasks=8, max_usd=1.0, task_name="bug_loc"),
        seed_artifact=object(),  # type: ignore[arg-type]
        inject=lambda artifact: ArtifactInjection(),
    )
    loc, gap = _straddling_pair()
    train = await fitness._split_task_ids("train")
    assert (loc.task_id in train) == (gap.task_id in train)
```

(If the suite's asyncio mode is `strict`, the `pytest.mark.asyncio` marker is required as written; if it is `auto`, the marker is harmless.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/optimize/test_record_keyed_partition.py -q`
Expected: FAIL — `ImportError: split_tasks_by_record`.

- [ ] **Step 3: Add the helper and use it on every fitness**

Append to `benchmarks/src/pydocs_eval/optimize/_split.py` (imports: `from collections.abc import Iterable, Sequence`, `from pydocs_eval.datasets.base_dataset import EvalTask`, `from pydocs_eval.datasets.task_ids import record_id_of`; the module docstring's last paragraph gains: `The record-keyed variant, ``split_tasks_by_record``, partitions on ``record_id_of`` so every framing minted from one record lands on one side — byte-identical for pre-framing corpora, whose record is the task id.`):

```python
def split_tasks_by_record(
    tasks: Sequence[EvalTask], *, split: str, task_names: Sequence[str] = ()
) -> tuple[EvalTask, ...]:
    """The tasks whose RECORD lands on ``split`` (platform spec §5.4 item 2).

    Partitioned on ``record_id_of``, never the row's task id: two framings of
    one record (``…-loc/bug_loc/<id>`` and ``…-test-gap/change_review/<id>``)
    must not straddle train and holdout, or the split leaks. ``task_names`` is
    the framing vocabulary for rows that carry no ``record_id`` field.

    Example:
        >>> split_tasks_by_record((), split="train")
        Traceback (most recent call last):
        ValueError: train split is empty across 0 task(s): ...
    """
    records = [record_id_of(task, task_names=task_names) for task in tasks]
    train, _holdout = partition_task_ids(records)
    train_records = set(train)
    wants_train = split == _TRAIN
    return tuple(
        task
        for task, record in zip(tasks, records, strict=True)
        if (record in train_records) == wants_train
    )
```

`ask_rubric._split_tasks` — replace its body from `tasks = [...]` through the `selected = [...]` comprehension with:

```python
        tasks = [task async for task in self.dataset.tasks()]
        selected = list(split_tasks_by_record(tasks, split=split, task_names=enumerated_task_names()))
```

(import `split_tasks_by_record` next to `partition_task_ids`; drop the now-unused `partition_task_ids` import if nothing else in the module uses it).

`paired_agent._split_task_ids`:

```python
    async def _split_task_ids(self, split: str) -> frozenset[str]:
        """Collect the dataset's tasks and return the requested split's ids.

        Partitioned on the RECORD (``split_tasks_by_record``), so sibling
        framings of one record share a side; ``partition_task_ids`` fires its
        loud non-empty-split guard HERE on the real path (spec §D3).
        """
        tasks = [task async for task in self.dataset.tasks()]
        return frozenset(task.task_id for task in split_tasks_by_record(tasks, split=split))
```

`retrieval.py` — rename `_dataset_task_ids` to `_dataset_tasks` returning `tuple[EvalTask, ...]` (`return tuple([task async for task in dataset.tasks()])`, import `EvalTask` from `datasets.base_dataset`) and in `evaluate` replace the two lines `ids = …` / `train, holdout = …` / `selected = …` with:

```python
        tasks = await _dataset_tasks(self.dataset_name, self.dataset_kwargs)
        selected = frozenset(task.task_id for task in split_tasks_by_record(tasks, split=split))
```

(update both modules' imports from `partition_task_ids` to `split_tasks_by_record`).

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/optimize/test_record_keyed_partition.py benchmarks/tests/optimize/test_paired_agent_fitness.py benchmarks/tests/optimize/test_retrieval_fitness.py benchmarks/tests/optimize/test_ask_rubric_fitness.py benchmarks/tests/optimize/test_split_and_ladder.py -q`
Expected: PASS — the existing fixtures use pre-framing ids, whose record is the task id, so their sides are unchanged.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/pydocs_eval/optimize/_split.py benchmarks/src/pydocs_eval/optimize/fitness/ask_rubric.py benchmarks/src/pydocs_eval/optimize/fitness/paired_agent.py benchmarks/src/pydocs_eval/optimize/fitness/retrieval.py benchmarks/tests/optimize/test_record_keyed_partition.py
git commit -m "eval: record-keyed split on every fitness (sibling framings share a side)"
```

---

### Task 8: `gold_diff.enclosing_symbols`

**Files:**
- Modify: `benchmarks/src/pydocs_eval/trajectory/gold_diff.py`
- Test: `benchmarks/tests/trajectory/test_gold_diff_symbols.py`

**Interfaces:**
- Produces: `enclosing_symbols(patch: str) -> dict[str, tuple[tuple[tuple[int, int], str], ...]]` — per modified file, `((target_start, target_end), symbol)` for every hunk whose `@@ … @@` context names a `def` / `async def` / `class`.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/trajectory/test_gold_diff_symbols.py`:

```python
"""``enclosing_symbols`` — the hunk-context symbol the change_review gold cites."""

from __future__ import annotations

from pydocs_eval.trajectory.gold_diff import enclosing_symbols

_PATCH = (
    "diff --git a/astropy/modeling/separable.py b/astropy/modeling/separable.py\n"
    "--- a/astropy/modeling/separable.py\n"
    "+++ b/astropy/modeling/separable.py\n"
    "@@ -242,7 +242,7 @@ def _cstack(left, right):\n"
    "         cright = _coord_matrix(right, 'right', noutp)\n"
    "     else:\n"
    "         cright = np.zeros((noutp, right.shape[1]))\n"
    "-        cright[-right.shape[0]:, -right.shape[1]:] = 1\n"
    "+        cright[-right.shape[0]:, -right.shape[1]:] = right\n"
    " \n"
    "     return np.hstack([cleft, cright])\n"
    "@@ -300,3 +300,4 @@ class Separable:\n"
    "     a = 1\n"
    "+    b = 2\n"
    "     c = 3\n"
    "     d = 4\n"
    "diff --git a/pkg/top.py b/pkg/top.py\n"
    "--- a/pkg/top.py\n"
    "+++ b/pkg/top.py\n"
    "@@ -1,2 +1,3 @@\n"
    " import os\n"
    "+import sys\n"
    " x = 1\n"
)


def test_symbols_and_target_spans_per_hunk():
    assert enclosing_symbols(_PATCH) == {
        "astropy/modeling/separable.py": (((242, 248), "_cstack"), ((300, 303), "Separable")),
    }


def test_hunks_without_a_symbol_context_and_empty_patches_yield_nothing():
    assert "pkg/top.py" not in enclosing_symbols(_PATCH)
    assert enclosing_symbols("") == {}


def test_async_def_and_deleted_files_are_handled():
    patch = (
        "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
        "@@ -10,2 +10,3 @@ async def fetch(url):\n     a\n+    b\n     c\n"
        "diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n"
        "@@ -1,2 +0,0 @@ def old():\n-    a\n-    b\n"
    )
    assert enclosing_symbols(patch) == {"m.py": (((10, 12), "fetch"),)}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/trajectory/test_gold_diff_symbols.py -q`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement**

Append to `benchmarks/src/pydocs_eval/trajectory/gold_diff.py` (add `import re` to the imports and `"enclosing_symbols"` to `__all__` if the module declares one):

```python
# The function/class line git prints after the second ``@@`` of a hunk header
# (its "funcname" context). Only a def / async def / class context names a
# symbol; a module-level hunk carries no context and is skipped.
_SYMBOL_CONTEXT = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)")


def enclosing_symbols(patch: str) -> dict[str, tuple[tuple[tuple[int, int], str], ...]]:
    """Per modified file, each symbol-anchored hunk's TARGET line span and symbol.

    The change_review gold cites the enclosing symbol of every finding
    (task-layer design §7.1 ``symbol_<i>``); the hunk header's context is the
    diff's own answer to "which def/class does this hunk sit in". Deletions
    (``target_length == 0``) and context-less hunks contribute nothing.

    Example:
        >>> p = ("diff --git a/x.py b/x.py\\n--- a/x.py\\n+++ b/x.py\\n"
        ...      "@@ -5,2 +5,3 @@ def f():\\n a\\n+b\\n c\\n")
        >>> enclosing_symbols(p)
        {'x.py': (((5, 7), 'f'),)}
    """
    out: dict[str, list[tuple[tuple[int, int], str]]] = {}
    for section in PatchSet(patch or ""):
        target = getattr(section, "target_file", _DEV_NULL)
        if target == _DEV_NULL:
            continue
        for hunk in section:
            match = _SYMBOL_CONTEXT.match(hunk.section_header or "")
            if match is None or hunk.target_length == 0:
                continue
            span = (hunk.target_start, hunk.target_start + hunk.target_length - 1)
            out.setdefault(_strip_ab(target), []).append((span, match.group(1)))
    return {path: tuple(items) for path, items in out.items()}
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/trajectory/test_gold_diff_symbols.py benchmarks/tests/trajectory -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/pydocs_eval/trajectory/gold_diff.py benchmarks/tests/trajectory/test_gold_diff_symbols.py
git commit -m "eval: gold_diff.enclosing_symbols — hunk-context symbols for change_review gold"
```

---

### Task 9: `swe-bench-verified-test-gap` in its S0 shape

**Files:**
- Create: `benchmarks/src/pydocs_eval/datasets/change_review.py`
- Modify: `benchmarks/src/pydocs_eval/datasets/__init__.py` (import + `__all__`), `benchmarks/src/pydocs_eval/datasets/change_tasks.py` (`TEST_PATH_GLOBS`)
- Create: `benchmarks/tests/fixtures/swe_bench_verified_test_gap_mini.jsonl`
- Test: `benchmarks/tests/datasets/test_change_review_test_gap.py`

**Interfaces:**
- Consumes: `SWE_BENCH_VERIFIED_PIN`, `CORPUS_GLOBS`, `_corpus_source`, `_load_rows`, `_log_yield`, `_GITHUB_URL` (`datasets/bug_localization.py`); `paths_from_unified_diff`, `non_test_paths`, `is_test_path`, `BugLocGoldError` (`_bug_loc_gold.py`); `enclosing_symbols` (Task 8); `CHANGE_REVIEW_TASK_NAME`, `DIMENSION_METADATA_KEY`, `ChangeReviewDimension` (Task 3); `mint_framed_task_id`.
- Produces: registered dataset `swe-bench-verified-test-gap` (`SweBenchVerifiedTestGapDataset(fixture_path=, repo_cache=, cache_dir=)`), `SWE_BENCH_VERIFIED_TEST_GAP_NAME`, `TestGapGoldError`, `render_test_gap_prompt(issue, patch)`; `change_tasks.TEST_PATH_GLOBS`.

- [ ] **Step 1: Commit the fixture**

Create `benchmarks/tests/fixtures/swe_bench_verified_test_gap_mini.jsonl` (four rows, one per line; the diffs are minimal but well-formed):

```json
{"instance_id": "astropy__astropy-12907", "repo": "astropy/astropy", "base_commit": "d16bfe05a744909de4b27f5875fe0d4ed41ce607", "problem_statement": "Modeling's `separability_matrix` does not compute separability correctly for nested CompoundModels.", "patch": "diff --git a/astropy/modeling/separable.py b/astropy/modeling/separable.py\n--- a/astropy/modeling/separable.py\n+++ b/astropy/modeling/separable.py\n@@ -242,7 +242,7 @@ def _cstack(left, right):\n         cright = _coord_matrix(right, 'right', noutp)\n     else:\n         cright = np.zeros((noutp, right.shape[1]))\n-        cright[-right.shape[0]:, -right.shape[1]:] = 1\n+        cright[-right.shape[0]:, -right.shape[1]:] = right\n \n     return np.hstack([cleft, cright])\n \n", "test_patch": "diff --git a/astropy/modeling/tests/test_separable.py b/astropy/modeling/tests/test_separable.py\n--- a/astropy/modeling/tests/test_separable.py\n+++ b/astropy/modeling/tests/test_separable.py\n@@ -28,6 +28,7 @@ def test_separable():\n     a = 1\n+    b = 2\n     c = 3\n     d = 4\n     e = 5\n     f = 6\n", "version": "4.3", "difficulty": "15 min - 1 hour"}
{"instance_id": "django__django-11099", "repo": "django/django", "base_commit": "d26b2424437dabeeca94d7900b37d2df4410da0c", "problem_statement": "UsernameValidator allows trailing newline in usernames.", "patch": "diff --git a/django/contrib/auth/validators.py b/django/contrib/auth/validators.py\n--- a/django/contrib/auth/validators.py\n+++ b/django/contrib/auth/validators.py\n@@ -7,7 +7,7 @@ class ASCIIUsernameValidator(validators.RegexValidator):\n-    regex = r'^[\\w.@+-]+$'\n+    regex = r'^[\\w.@+-]+\\Z'\n     message = _(\n         'Enter a valid username. This value may contain only English letters, '\n         'numbers, and @/./+/-/_ characters.'\n     )\n     flags = re.ASCII\n \n", "test_patch": "diff --git a/tests/auth_tests/test_validators.py b/tests/auth_tests/test_validators.py\n--- a/tests/auth_tests/test_validators.py\n+++ b/tests/auth_tests/test_validators.py\n@@ -237,6 +237,7 @@ class UsernameValidatorsTests(SimpleTestCase):\n     a = 1\n+    b = 2\n     c = 3\n     d = 4\n     e = 5\n     f = 6\n", "version": "3.0", "difficulty": "<15 min fix"}
{"instance_id": "fixture__no-test-patch-1", "repo": "example/repo", "base_commit": "0000000000000000000000000000000000000001", "problem_statement": "A change with no test change.", "patch": "diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1,2 +1,3 @@ def f():\n a\n+b\n c\n", "test_patch": "", "version": "1.0", "difficulty": "<15 min fix"}
{"instance_id": "fixture__empty-patch-1", "repo": "example/repo", "base_commit": "0000000000000000000000000000000000000002", "problem_statement": "Nothing changed.", "patch": "", "test_patch": "diff --git a/tests/test_x.py b/tests/test_x.py\n--- a/tests/test_x.py\n+++ b/tests/test_x.py\n@@ -1,2 +1,3 @@ def test_x():\n a\n+b\n c\n", "version": "1.0", "difficulty": "<15 min fix"}
```

- [ ] **Step 2: Write the failing tests**

Create `benchmarks/tests/datasets/test_change_review_test_gap.py`:

```python
"""``swe-bench-verified-test-gap`` in its S0 shape — AC-11 (+ the test-glob agreement pin)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import Dataset
from pydocs_eval.datasets.bug_localization import CORPUS_GLOBS, SweBenchVerifiedLocDataset
from pydocs_eval.datasets.change_review import (
    SWE_BENCH_VERIFIED_TEST_GAP_NAME,
    SweBenchVerifiedTestGapDataset,
    render_test_gap_prompt,
)
from pydocs_eval.datasets.change_tasks import TEST_PATH_GLOBS, ChangeReviewDimension
from pydocs_eval.optimize.rubric.checks import Check, evaluate_check
from pydocs_eval.registries import dataset_registry
from tests.optimize._trajectories import make_trajectory

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_GAP_FIXTURE = _FIXTURES / "swe_bench_verified_test_gap_mini.jsonl"
_LOC_FIXTURE = _FIXTURES / "swe_bench_verified_loc_mini.jsonl"
_CORPUS_DIR = _FIXTURES / "bug_loc_corpus"


@dataclass
class _FakeRepoCache:
    corpus_dir: Path = field(default=_CORPUS_DIR)
    requested: list[tuple[str, str]] = field(default_factory=list)

    def checkout(self, url: str, sha: str) -> Path:
        self.requested.append((url, sha))
        return self.corpus_dir

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return ()


def _tasks(cache: _FakeRepoCache | None = None):
    dataset = SweBenchVerifiedTestGapDataset(fixture_path=_GAP_FIXTURE, repo_cache=cache or _FakeRepoCache())

    async def _collect():
        return [task async for task in dataset.tasks()]

    return asyncio.run(_collect())


def test_registered_under_its_arm_facing_name():
    assert isinstance(dataset_registry.build(SWE_BENCH_VERIFIED_TEST_GAP_NAME), Dataset)
    assert SWE_BENCH_VERIFIED_TEST_GAP_NAME == "swe-bench-verified-test-gap"


def test_gold_is_the_test_patch_paths_and_extra_carries_the_change_set():
    tasks = {t.record_id: t for t in _tasks()}
    astropy = tasks["astropy__astropy-12907"]
    assert astropy.gold.file_set == ("astropy/modeling/tests/test_separable.py",)
    assert astropy.gold.extra["changed_0"] == "astropy/modeling/separable.py"
    assert astropy.gold.extra["symbol_0"] == "_cstack"
    assert astropy.gold.extra["base_sha"] == "d16bfe05a744909de4b27f5875fe0d4ed41ce607"
    assert astropy.gold.extra["branch"] == ""
    assert astropy.gold.ast_body is None
    django = tasks["django__django-11099"]
    assert django.gold.extra["symbol_0"] == "ASCIIUsernameValidator"


def test_ids_and_metadata():
    (astropy,) = [t for t in _tasks() if t.record_id == "astropy__astropy-12907"]
    assert astropy.task_id == "swe-bench-verified-test-gap/change_review/astropy__astropy-12907"
    assert astropy.metadata["dimension"] == ChangeReviewDimension.TEST_GAP.value
    assert astropy.metadata["gold_file_count"] == "1" and astropy.metadata["changed_file_count"] == "1"
    assert astropy.metadata["surface_stage"] == "S0"
    assert all(isinstance(v, str) for v in astropy.metadata.values())


def test_the_record_id_is_shared_with_the_bug_loc_framing():
    loc = SweBenchVerifiedLocDataset(fixture_path=_LOC_FIXTURE, repo_cache=_FakeRepoCache())

    async def _collect():
        return {t.record_id for t in [task async for task in loc.tasks()]}

    loc_records = asyncio.run(_collect())
    assert {t.record_id for t in _tasks()} <= loc_records | {"django__django-11099"}
    assert "astropy__astropy-12907" in {t.record_id for t in _tasks()}


def test_rows_without_a_test_patch_or_a_patch_are_dropped_and_counted(caplog):
    with caplog.at_level(logging.INFO):
        tasks = _tasks()
    assert {t.record_id for t in tasks} == {"astropy__astropy-12907", "django__django-11099"}
    assert "dropped 2" in caplog.text


def test_the_s0_prompt_carries_the_issue_and_the_diff():
    (astropy,) = [t for t in _tasks() if t.record_id == "astropy__astropy-12907"]
    assert astropy.query == render_test_gap_prompt(
        "Modeling's `separability_matrix` does not compute separability correctly for nested CompoundModels.",
        astropy.gold.extra["patch"],
    )
    assert "unified diff" in astropy.query and "def _cstack" in astropy.query


def test_the_corpus_is_the_wider_bug_loc_scope_and_history_less():
    cache = _FakeRepoCache()
    (astropy,) = [t for t in _tasks(cache) if t.record_id == "astropy__astropy-12907"]
    corpus = astropy.corpus_source()
    try:
        assert cache.requested == [("https://github.com/astropy/astropy.git", "d16bfe05a744909de4b27f5875fe0d4ed41ce607")]
        assert (corpus / "astropy" / "modeling" / "separable.py").is_file()
        assert not (corpus / ".git").exists()
    finally:
        import shutil

        shutil.rmtree(corpus)
    from pydocs_eval.datasets.change_review import TEST_GAP_CORPUS_GLOBS

    assert TEST_GAP_CORPUS_GLOBS == CORPUS_GLOBS


def test_findings_located_scores_the_test_paths_not_the_symbols():
    """AC-11's scoring half."""
    (astropy,) = [t for t in _tasks() if t.record_id == "astropy__astropy-12907"]
    check = Check(name="findings_located", kind="gold_recall", params={"keys": ["file_set"]}, fail=None)
    named = make_trajectory(answer="## Test gap\nastropy/modeling/separable.py · _cstack → astropy/modeling/tests/test_separable.py\n")
    symbols_only = make_trajectory(answer="## Test gap\n_cstack has no test\n")
    assert evaluate_check(check, astropy, named).score == 1.0
    assert evaluate_check(check, astropy, symbols_only).score == 0.0


@pytest.mark.parametrize(
    "path",
    [
        "tests/unit/test_router.py",
        "pkg/router_test.py",
        "tests/conftest.py",
        "conftest.py",
        "Tests/Sub/helper.py",
        "test/x.py",
        "src/testing/runtests.py",
        "a/_tests/b.py",
    ],
)
def test_every_predicate_accepted_path_matches_a_trajectory_glob(path: str):
    """§6.4.1: the head's test globs agree with the eval's ``is_test_path``."""
    # WHY the private import: the agreement is with the PRODUCT's glob dialect
    # (``*`` never crosses ``/``, ``**/`` matches zero or more directories),
    # which is what the grep tool applies to the head's globs.
    from pydocs_mcp.application.file_tools import _glob_to_regex

    from pydocs_eval.datasets._bug_loc_gold import is_test_path

    assert is_test_path(path)
    assert any(_glob_to_regex(glob).match(path) for glob in TEST_PATH_GLOBS), path


@pytest.mark.parametrize("path", ["src/pkg/latest/router.py", "docs/testing.md", "attest/x.py"])
def test_no_glob_matches_a_non_test_path(path: str):
    from pydocs_mcp.application.file_tools import _glob_to_regex

    from pydocs_eval.datasets._bug_loc_gold import is_test_path

    assert not is_test_path(path)
    assert not any(_glob_to_regex(glob).match(path) for glob in TEST_PATH_GLOBS), path
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_change_review_test_gap.py -q`
Expected: FAIL — `ModuleNotFoundError: change_review`; `ImportError: TEST_PATH_GLOBS`.

- [ ] **Step 4: Add `TEST_PATH_GLOBS` to `change_tasks.py`**

```python
#: The grep globs a change_review trajectory uses to find test files — one call
#: per glob (§6.4.1). Kept in agreement with the eval's ``is_test_path``
#: predicate (``_bug_loc_gold.py``) by a pinned test: every path the predicate
#: accepts matches one of these under the product's glob dialect.
TEST_PATH_GLOBS: tuple[str, ...] = (
    "**/test_*.py",
    "**/*_test.py",
    "**/tests/**",
    "**/test/**",
    "**/testing/**",
    "**/_tests/**",
    "**/_test/**",
    "**/conftest.py",
)
```

(add `"TEST_PATH_GLOBS"` to `__all__`).

- [ ] **Step 5: Create the loader**

`benchmarks/src/pydocs_eval/datasets/change_review.py`:

```python
"""The ``change_review`` framing, dimension TEST_GAP, over SWE-bench Verified.

S0 shape (task-layer design §7.1, §10): the corpus is the history-less base
commit — exactly the ``swe-bench-verified-loc`` corpus — and the fix ``patch``
is rendered INTO THE PROMPT, because a P0 server has no change slice. Gold is
the ``test_patch`` paths: the test files the answer's TEST_GAP arrows must
name. Because the change carries ``patch`` only, every changed symbol is a
gap by construction — the dataset measures whether the agent names the right
test file for each gap, not whether it detects one (README subsection).

S1 (same records, same ids) replaces the prompt-supplied diff with a
synthetic branch carrying ``patch`` and ``extra["branch"] = "change/<id>"``;
the S0 score is NOT comparable with S2a (§10).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..registries import dataset_registry
from ..trajectory.gold_diff import enclosing_symbols
from ._bug_loc_gold import BugLocGoldError, non_test_paths, paths_from_unified_diff
from ._repo_cache import RepoCache, RepoCacheLike
from .base_dataset import EvalTask, GoldAnswer
from .bug_localization import (
    _GITHUB_URL,  # noqa: PLC2701 — one acquisition path for both framings of the record
    CORPUS_GLOBS,
    SWE_BENCH_VERIFIED_PIN,
    _corpus_source,
    _load_rows,
    _log_yield,
)
from .change_tasks import CHANGE_REVIEW_TASK_NAME, DIMENSION_METADATA_KEY, ChangeReviewDimension
from .task_ids import mint_framed_task_id

log = logging.getLogger(__name__)

SWE_BENCH_VERIFIED_TEST_GAP_NAME = "swe-bench-verified-test-gap"
#: Same scope as the bug_loc framing over the same records (a shared corpus).
TEST_GAP_CORPUS_GLOBS: tuple[str, ...] = CORPUS_GLOBS
#: The S0 prompt: the review request, the issue, the diff. The heads never
#: name a branch; on S0 "the change itself" IS this prompt (§10).
_S0_PROMPT = (
    "Review the change below for test gaps: for every changed non-test symbol "
    "with no changed test, name the test file that should exercise it. Answer in "
    "fixed sections (summary, findings, blast radius, test gap).\n\n"
    "Issue:\n{issue}\n\n"
    "The change under review (unified diff):\n\n{patch}"
)
_COLUMNS = [
    "instance_id",
    "repo",
    "base_commit",
    "patch",
    "test_patch",
    "problem_statement",
    "version",
    "difficulty",
]


class TestGapGoldError(ValueError):
    """A record whose test-gap gold cannot be derived (no test_patch, no patch)."""


def render_test_gap_prompt(issue: str, patch: str) -> str:
    """The S0 query text (the single place the prompt shape lives)."""
    return _S0_PROMPT.format(issue=issue, patch=patch)


def _symbol_names(patch: str) -> tuple[str, ...]:
    """Every hunk-context symbol of ``patch``, first-appearance order, deduped."""
    seen: dict[str, None] = {}
    for hunks in enclosing_symbols(patch).values():
        for _span, symbol in hunks:
            seen.setdefault(symbol)
    return tuple(seen)


def _gold_extra(patch: str, changed: tuple[str, ...], base_commit: str) -> dict[str, object]:
    extra: dict[str, object] = {f"changed_{i}": path for i, path in enumerate(changed)}
    extra.update({f"symbol_{i}": name for i, name in enumerate(_symbol_names(patch))})
    extra["base_sha"] = base_commit
    extra["branch"] = ""  # S0: no branch; S1 stamps change/<instance_id>
    extra["patch"] = patch  # the judge's reference (never a scored key)
    return extra


@dataset_registry.register(SWE_BENCH_VERIFIED_TEST_GAP_NAME)
@dataclass
class SweBenchVerifiedTestGapDataset:
    """SWE-bench Verified as change_review / TEST_GAP (S0: diff in the prompt)."""

    name: str = SWE_BENCH_VERIFIED_TEST_GAP_NAME
    revision: str = SWE_BENCH_VERIFIED_PIN.revision
    fixture_path: Path | None = None
    repo_cache: RepoCacheLike = field(default_factory=RepoCache)
    cache_dir: Path | None = None
    _rows_cache: list[dict[str, Any]] | None = field(default=None, init=False, repr=False)

    async def tasks(self) -> AsyncIterator[EvalTask]:
        rows = await self._rows()
        yielded, dropped = 0, 0
        for row in rows:
            try:
                task = self._row_to_task(row)
            except (TestGapGoldError, BugLocGoldError) as exc:
                dropped += 1
                log.info("%s: dropping %r — %s", self.name, row.get("instance_id"), exc)
                continue
            yielded += 1
            yield task
        _log_yield(self.name, yielded=yielded, dropped=dropped)

    async def _rows(self) -> list[dict[str, Any]]:
        if self._rows_cache is None:
            self._rows_cache = await _load_rows(
                fixture_path=self.fixture_path,
                pin=SWE_BENCH_VERIFIED_PIN,
                columns=_COLUMNS,
                cache_dir=self.cache_dir,
            )
        return self._rows_cache

    def _row_to_task(self, row: dict[str, Any]) -> EvalTask:
        instance_id = str(row["instance_id"])
        patch = str(row.get("patch") or "")
        test_patch = str(row.get("test_patch") or "")
        if not test_patch.strip():
            raise TestGapGoldError(
                f"record {instance_id!r} has an empty test_patch; the test-gap gold is its paths"
            )
        if not patch.strip():
            raise TestGapGoldError(f"record {instance_id!r} has an empty patch; nothing to review")
        test_paths = paths_from_unified_diff(test_patch)
        changed = non_test_paths(paths_from_unified_diff(patch))
        if not changed:
            raise TestGapGoldError(
                f"record {instance_id!r} changes only test files; expected at least one non-test path"
            )
        owner, _, repo_name = str(row["repo"]).partition("/")
        base_commit = str(row["base_commit"])
        return EvalTask(
            task_id=mint_framed_task_id(
                dataset=self.name, task_name=CHANGE_REVIEW_TASK_NAME, record_id=instance_id
            ),
            record_id=instance_id,
            query=render_test_gap_prompt(str(row.get("problem_statement") or ""), patch),
            gold=GoldAnswer(file_set=test_paths, extra=_gold_extra(patch, changed, base_commit)),
            corpus_source=_corpus_source(
                self.repo_cache, _GITHUB_URL.format(owner=owner, name=repo_name), base_commit
            ),
            metadata={
                "repo": str(row["repo"]),
                DIMENSION_METADATA_KEY: ChangeReviewDimension.TEST_GAP.value,
                "gold_file_count": str(len(test_paths)),
                "changed_file_count": str(len(changed)),
                "version": str(row.get("version") or ""),
                "difficulty": str(row.get("difficulty") or ""),
                "surface_stage": "S0",
            },
        )


__all__ = [
    "SWE_BENCH_VERIFIED_TEST_GAP_NAME",
    "TEST_GAP_CORPUS_GLOBS",
    "SweBenchVerifiedTestGapDataset",
    "TestGapGoldError",
    "render_test_gap_prompt",
]
```

If `ruff` flags the private-name imports (`_GITHUB_URL`, `_corpus_source`, `_load_rows`, `_log_yield`) under a rule the repo enables, promote those four names in `bug_localization.py` to public spellings (`GITHUB_URL`, `corpus_source_for`, `load_rows`, `log_yield`) with the old names kept as aliases in that module, and import the public ones here.

In `benchmarks/src/pydocs_eval/datasets/__init__.py` add `from .change_review import SweBenchVerifiedTestGapDataset` and `"SweBenchVerifiedTestGapDataset"` to `__all__` (alphabetical position).

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_change_review_test_gap.py benchmarks/tests/datasets/test_bug_localization.py benchmarks/tests/test_registry_population.py -q`
Expected: PASS for the two dataset modules; `test_registry_population.py` keeps its two pre-existing version-skew failures if they were failing before this task (verify with `git stash`-free means: run it on the parent commit first — `git log -1` then `git checkout HEAD~1 -- benchmarks/tests/test_registry_population.py`? No: just run it before Step 5 and compare the failure set).

- [ ] **Step 7: Commit**

```bash
git add benchmarks/src/pydocs_eval/datasets/change_review.py benchmarks/src/pydocs_eval/datasets/change_tasks.py benchmarks/src/pydocs_eval/datasets/__init__.py benchmarks/tests/fixtures/swe_bench_verified_test_gap_mini.jsonl benchmarks/tests/datasets/test_change_review_test_gap.py
git commit -m "eval: swe-bench-verified-test-gap (change_review / TEST_GAP, S0 prompt-supplied diff)"
```

---

### Task 10: Documents, README, gates

**Files:**
- Modify: `docs/superpowers/specs/2026-07-27-harness-run-contract-design.md` (after item 6 of the 2026-07-28 amendment, before `## 6.`)
- Modify: `docs/superpowers/specs/2026-07-26-retriever-centric-harness-platform-design.md` (§5.2 blockquote)
- Modify: `CHANGELOG.md` (`### Added` under `[0.6.0] — Unreleased`)
- Modify: `benchmarks/README.md` (a subsection after "Bug localization")

- [ ] **Step 1: Run-contract §5 amendment**

Insert before `## 6. Arms are data; identity is a fingerprint`:

```markdown
### Amendment 2026-09-04 — the fourth and fifth framings: `change_review` and `release_notes`

Owner directive: the branch/diff task layer
(`docs/superpowers/specs/2026-09-04-branch-diff-task-layer-design.md`). Two
framings join the set, which is now `repo_qa`, `vuln`, `bug_loc`,
`change_review`, `release_notes` — APPENDED, so every existing section keeps
its position. Enumeration-only again: the grammar regex and
`RENDERER_VERSION` are untouched (both names match the existing shapes), the
`TASK_HEAD:` tier goes three → five, the `HARNESS_TASK_HEAD:` cross product
six → ten, and the skill artifact's required set ten → sixteen. Hygiene held:
the six new header lines had zero hits across tracked files before the seed
gained them.

1. **`change_review`** — a review of one change (a live branch or a landed
   unit) against its base: what changed, what it breaks, what it misses, in
   fixed sections. Its sub-variants (test gap, conflict pre-check, doc drift,
   PR description) are DIMENSIONS stamped into `metadata["dimension"]` by
   one dataset each, never task names; per-dimension rubric weights key on
   the dataset prefix. Task → datasets: `change_review` ← {pr-review-py,
   swe-bench-verified-test-gap, pr-review-py-doc-drift,
   pr-review-py-description}. The first dataset ships in its S0 shape (the
   fix patch rendered into the prompt over the history-less base commit;
   its record id is shared with `swe-bench-verified-loc`, so the record-keyed
   split keeps both framings on one side).
2. **`release_notes`** — a changelog section over a range of landing units
   (the first-parent landings between two tags, or since the last tag),
   grouped by effect on a user with per-bullet unit citations. Task →
   datasets: `release_notes` ← {pydocs-self-releases, changelog-tagged-py}
   (both land with the multi-branch P2 surface).
3. **Recorded cost.** The ask delivery-map digest moves (regenerated; no
   committed ledger row carries the old ask arm hash); the external digest,
   the synthetic arm-fingerprint golden and the twelve ADR 0011 real
   trajectories (keyed by `artifact_hash`) are unmoved. The registration
   golden does not move.
4. **Eval-side primitives that landed with the event** (base-install, no
   product import): the `ChangeReviewDimension` / `ReviewHeading` /
   `ReleaseNotesHeading` vocabularies; the `slice_consulted`,
   `graph_consulted`, `card_consulted` check kinds and the
   `review_headings_present` / `release_headings_present` gate kinds (built
   from the enums at call time — no config spells a heading word);
   `gold_recall.key_prefix`; the report's `breakout_key`; the record-keyed
   partition on every fitness; `validate_checks` at load time per bound arm
   dataset; `gold_diff.enclosing_symbols`.
```

- [ ] **Step 2: Platform spec §5.2 blockquote**

Append a third paragraph to the blockquote at the top of §5.2:

```markdown
>
> **Amended 2026-09-04 (the fourth and fifth framings).** `change_review` and `release_notes` (the branch/diff task layer) join the set, which is now `{repo_qa, vuln, bug_loc, change_review, release_notes}`: five `TASK_HEAD:` sections and ten `HARNESS_TASK_HEAD:`, for a **sixteen-key** `SKILL_ARTIFACT_HEADERS`. Enumeration-only once more — the grammar and `RENDERER_VERSION` are untouched — and the derived delivery maps picked the new sections up with no code edit. The change-review sub-variants are dimensions of one task, not task names (the axis stays the task *name*). Normative record: run-contract design §5 Amendment 2026-09-04.
```

- [ ] **Step 3: CHANGELOG and README**

`CHANGELOG.md`, under `### Added` of `[0.6.0] — Unreleased`:

```markdown
- **Task vocabulary: `change_review` and `release_notes`** — the harness
  skill artifact's enumerated task names grow from three to five (enumeration
  only; the grammar is untouched) with six new seed sections: a review of one
  change against its base, and a changelog section over a range of landed
  units. The eval suite gains the `slice_consulted` / `graph_consulted` /
  `card_consulted` trajectory checks, the `review_headings_present` /
  `release_headings_present` gates, `gold_recall(key_prefix=…)`, a
  per-dimension report breakout, a record-keyed split on every fitness,
  load-time `validate_checks`, and the `swe-bench-verified-test-gap` dataset
  (the fix patch rendered into the prompt until the change slice ships).
```

`benchmarks/README.md`, after the "Bug localization" subsection:

```markdown
### Change review — test gap (`swe-bench-verified-test-gap`)

**What it measures.** The first `change_review` dataset (dimension
`test_gap`): given a change and its issue, name, for every changed non-test
symbol without a changed test, the test file that should exercise it. Gold is
the record's `test_patch` paths; `extra` carries the change set
(`changed_<i>`) and each hunk's enclosing symbol (`symbol_<i>`).

**Shape today (S0).** The corpus is the history-less base commit — the
`swe-bench-verified-loc` corpus, same records, same record ids — and the fix
patch is rendered into the prompt, because the server has no change slice yet.
Because the change carries the fix patch only, **every changed symbol is a gap
by construction**: the dataset measures whether the agent names the right test
file for each gap, not whether it detects one. When the change slice lands
(S1), the same records index a synthetic branch carrying the patch and the
`change_consulted` check scores at full weight; S0 and S1/S2a numbers are not
comparable and are reported with their `surface_stage`.

**Which metrics.** The `ask_rubric_change_review` objective: `gold_recall`
over the test paths (`findings_located`), `gold_location_evidenced`,
`slice_consulted` (weight 0 on S0), `graph_consulted`, the
`review_headings_present` gate, and the judge criteria. The report breaks
scores out per `dimension` (`breakout_key="dimension"`).
```

- [ ] **Step 4: README audit and the full gate set**

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" -not -path "*/node_modules/*" -not -path "*/.git/*" | xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
ruff format python/ tests/ benchmarks/ && ruff check python/ tests/ benchmarks/ && mypy python/pydocs_mcp && complexipy python/pydocs_mcp --max-complexity-allowed 15 && vulture python/pydocs_mcp --min-confidence 80
pytest tests/ --ignore=tests/test_parity.py -q
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
uv lock --check
git checkout -- complexipy-snapshot.json
```

Expected: no audit matches; every gate green (the two pre-existing `test_registry_population.py` version-skew failures, if present before this plan, are the only tolerated red and must be listed in the PR description).

- [ ] **Step 5: Commit and open the T0 PR**

```bash
git add docs/superpowers/specs/2026-07-27-harness-run-contract-design.md docs/superpowers/specs/2026-07-26-retriever-centric-harness-platform-design.md CHANGELOG.md benchmarks/README.md
git commit -m "docs: fourth/fifth framing amendments, changelog, test-gap dataset README"
```

Gate: AC-1…AC-11 (S0 shape), AC-25 (the two spec amendments + CHANGELOG; the multi-branch R12 / §6.5a and UI §6.9 amendments are T1/T2 items), AC-26.

---

## Deviations from the spec (recorded, not silent)

| # | Spec says | Plan does | Why |
|---|---|---|---|
| D1 | the trajectory's test globs are the three spellings `**/test_*.py`, `**/*_test.py`, `**/tests/**` (§6.4.1) and a test pins that every `is_test_path`-accepted path matches one | `TEST_PATH_GLOBS` in `change_tasks.py` carries eight globs (adds `**/test/**`, `**/testing/**`, `**/_tests/**`, `**/_test/**`, `**/conftest.py`) and the pin runs both directions over a sample | the predicate accepts `test/`, `testing/`, `_tests/` segments and `conftest.py`, which the three spellings miss; the head text is unchanged ("the test globs") |
| D2 | `validate_checks(known_task_types=<the arms' dataset names>)` wired next to `validate_rubric_config` (§7.1) | per bound section, known types = the datasets of the arms binding THAT section, over `deterministic_checks(gates, checks)` | a section's gates are its required screens; validating checks alone would reject every shipped section whose measures are `required: false` |
| D3 | the record-keyed partition is "a change to `partition_task_ids`' caller" | one shared `split_tasks_by_record` in `_split.py` used by the ask, paired-agent and retrieval fitnesses (`ask_rubric` already partitioned on records) | one derivation, three callers |
| D4 | S0 prompt shape unspecified beyond "the diff must be in the prompt" | `render_test_gap_prompt(issue, patch)` — request + issue + unified diff | one function owns the text; the S1 loader replaces the call |
| D5 | `extra["symbol_<i>"]` "the enclosing symbol of finding *i*" | the hunk-context symbol names (bare identifiers, first-appearance order) | the pull-request-review corpus that carries findings is T1; on the test-gap dataset the symbols are the changed symbols |
| D6 | — | `extra["patch"]` and `metadata["surface_stage"]` added | the judge needs the diff as reference on S0; the stage tag keeps S0 numbers from being pooled with S1/S2a |
| D7 | — | private helpers (`_corpus_source`, `_load_rows`, `_log_yield`, `_GITHUB_URL`) are imported from `bug_localization.py` | one acquisition path for both framings of the record; promote to public names if lint objects |

## Spec coverage

| AC | Task | AC | Task |
|---|---|---|---|
| AC-1, AC-2, AC-3, AC-4 | 1 | AC-8a | 6 |
| AC-5 | 1 (digest), 2 | AC-9 | 4 |
| AC-6, AC-7 | 2 | AC-10 | 5 |
| AC-8 | 3, 4 | AC-11 | 8, 9 |
| §7.1 splits (record-keyed) | 7 | AC-25 (§5 / §5.2 / CHANGELOG), AC-26 | 10 |

Not in this plan (by §10.1): `pr-review-py` and its dimension datasets, the two arm configs, `materialize_corpus_with_history`, the P2.7 gate (T1); the self-corpus, `pydocs-self-landing-loc`, `crosscommitvuln-fix-landing`, the smoke gate (T2); the multi-branch card blocks G1/G5/G6 and the G4 amendment (multi-branch P2 plan sub-rows); the `RELEASE_NOTES` chip (UI U2 amendment).

## Handoff

One PR against `main` (Tasks 1–10, ten commits). Owner decisions to settle first (spec §13): **O1** ratify the two names and the placements of §6.1 — this plan encodes them; the rest of §13 (self-corpus gold, `validate_checks` at load, the `RELEASE_NOTES` chip, `ask_your_docs.task_head`, the external-harness leak, `hunk qualified_name`, the pull-request corpus pin) gate T1/T2, not this plan.
