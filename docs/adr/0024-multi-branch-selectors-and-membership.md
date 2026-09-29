# ADR 0024 — Multi-branch indexing: the `branch` selector on every tool, `scope` on `glob`, the `changed` / `diff` slices, and content-addressed branch membership

**Status:** Accepted — owner decisions O2, O3, O4, O5, O12, O14, O15, O16, O17, O18 of the multi-branch design ratified interactively 2026-09-15 (`docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md` §11 records each one); the contract lines are amended by the implementation PRs and ratified on the ADR 0007 path. The `branch` half of decision 1 (issue #315) amends `docs/tool-contracts.md` §2.3 (the landing-unit hint), §2.4 (`meta.branch`, the section this record first called §2.2), the §3 `branch` paragraph, the §3.1–§3.9 parameter rows and the §3.8 ordering note, §4.1, §4.2, the §5.2 sanctioned-parameter list and §6 migration row 9, each line marked *(amended per ADR 0024)*: ratified with the 0.8.2 amendment by merging this PR. The `scope` half (`changed` / `diff`, and `scope` on `glob`) amends its lines with the diff slices, the second half of the same event. ·
**Date:** 2026-09-15 · **Phase:** feature (after 0.7.0; the first stage, P0, shipped in 0.7.0 as schema v16)

- **Decision area:** several git branches in one index bundle; how a request names a branch; how a branch's diff is searched; what happens to a branch's diff once the branch has landed; which of these are deployment behavior (YAML) and which are per-request selectors.
- **Siblings:** ADR 0003 (the frozen nine-tool surface everything here lands behind), ADR 0007 (the owner-ratification path for contract-line amendments), ADR 0023 (pointer bundles — the six tools without a suggestion field, which is why a landing sha raises there), ADR 0021 / 0022 (the grammar fingerprint that P1's extraction cache key must include, issue #261).

## Context

A bundle holds exactly one branch: `git checkout` rewrites every differing file, the whole project re-extracts and re-embeds, and a question can never say "on `feature/x`" or "what did this branch change". The eval side hit the same wall (bug-localization corpora need a commit pair per sample). The design (spec above) keeps the surface frozen — zero new tools, one new selector, two new `scope` values, one additive `meta` field — and stores chunks once per unique blob so an extra branch costs its diff, never a second full index.

## Evidence

- Every pull request on this repository lands as a squash: of 238 first-parent steps on `origin/main`, 233 have one parent (182 squashed PRs) and four are merge commits — so `is_ancestor` alone would never recognize a merged branch; a patch-id match over recent first-parent steps does (spec §6.8a, Q10).
- 200 landings of patch-id lookback cost about 0.8 s once at start, then only the new landings (spec O16); 47 landings since `v0.5.1` sit well inside a 500 cap (spec O15).
- Release tags interleave `v*` with `eval-v*`, so a "since N tags" retention needs a tag pattern (spec O15).
- The chat page already holds one serve session per browser tab and fans a pinned question out through the same handler, so a multi-branch pin costs calls, not processes (UI spec O4, closed by fact 2026-09-15).

## Options considered

- **One bundle per branch** — REJECTED: each branch pays a full extraction and embedding pass, N× disk, and no diff is ever computable across bundles.
- **A `branch` column on every row** — REJECTED: text, FTS rows and vectors duplicated per branch; every repository and query changes.
- **Content-addressed chunks + branch membership tables** — CHOSEN (spec §5): chunks, vectors, trees and members stored once per unique `(blob, path)`; a branch pays its diff; the tree-derived tables carry the branch.
- **`branch` as a value of `scope`, or a combined `ref@diff` string** — REJECTED: `branch` is a corpus selector like `project`, and the slices are `scope` values like `deps`, so they compose (`branch=feature/x, scope=diff`).
- **Keeping `glob` without `scope`, reading the changed files from the branch card only** — REJECTED by the owner (O2, 2026-09-15): `glob` takes `scope` with the same `changed` / `diff` values, one more sanctioned corpus-scope selector.
- **A per-request diff base (`changed@<base>`)** — DEFERRED (O3): the base is deployment behavior in v1, `git.base_branch` in YAML; a per-request base would be a new parameter on the frozen surface.
- **Re-purposing `merged_into` to hold the landing sha** — REJECTED (O18): two columns keep the shipped v16 comment true and the error message needs both facts.
- **Answering empty for a landing sha on the tools without a suggestion field** — REJECTED (O17): those tools have no empty-result shape and the envelope is frozen, so they raise `InvalidArgumentError`.

## Decision

1. `branch: str = ""` on all nine tools, a sibling of `project`; it also accepts a landing sha. `scope` gains `changed` and `diff` on `search_codebase`, `grep` **and `glob`**. `meta.branch` is the one additive envelope field.
2. Storage is content-addressed with branch membership tables (schema v18 in P1, v19 in P2); `merged_into` keeps the base-name meaning and `landing_sha` is a new column.
3. The diff base is the base branch's current tip, remote-tracking when present, anchored at the merge-base; YAML only.
4. Defaults: tracked branches = `checked_out` + `retain_recent: 8`; auto-fetch off; a deleted branch's rows purged after a 7-day grace window; the diff of a landed branch kept through its landing unit for `since_tags: 2` (`tag_pattern: "v*"`, `fallback_landings: 50`, `max_landings: 500`); merge detection looks back 200 landings, independent of the retention window.
5. A landing sha on `get_symbol`, `get_context`, `get_references`, `get_why`, `glob` or `read_file` raises `InvalidArgumentError`; the diff tools and the branch card answer it.
6. P1 and P2 ship together as one contract event, **0.8.2** (version amended 2026-09-22: 0.8.0 and 0.8.1 shipped on 2026-09-15 without P1).

## Consequences

- The ask-your-docs stages U1 (branch choice, several branches per project, `on:` tokens, "Ask this on … too", "Compare with …") and U2 ("Which files", "Show what changed", the merged group) unlock only when the served bundle advertises these selectors; until then the controls stay captions.
- P1 also computes freshness per served project (`meta.index_stale`, `meta.indexed_git_head`), not from the first bundle's probe, so the chat footer's staleness sentence holds per project (UI design O6 → spec O19).
- P1's extraction cache key must include the loadable-grammar fingerprint and the chunk-tree salt (issue #261), never folded into `ingestion_pipeline_hash`.
- Comparing two arbitrary branches stays out of scope until a per-request base is designed; the first version compares a branch with its base.
