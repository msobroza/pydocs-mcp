# Review before to-spec: the zero-hit pointer and guidance-knob experiments

This folder holds one review, dated 2026-11-22 (Europe/Paris), of two turn-efficiency experiments for the ask-your-docs chat agent:

- **the zero-hit pointer experiment** — change the line the agent reads when a search finds nothing;
- **the guidance-knob experiment** — load the search-guidance seed into chat through `ask_your_docs.guidance_task`, and ship a slimmed system prompt if that wins.

The pull request that adds this folder asks the owner to **confirm the design before a spec is written**. It changes no product code, writes no spec and requests no spend.

## Start here

Open [`experiments-394-391.html`](experiments-394-391.html) in a browser. It is one self-contained page: for each experiment, the design as first written beside the design as decided, the owner's five answers, the count below, the cost, and the points still to confirm.

## What is in the folder

| File | What it holds |
|---|---|
| [`experiments-394-391.html`](experiments-394-391.html) | The review page. Read this first. |
| [`394-zero-hit-count.md`](394-zero-hit-count.md) | The $0 count of searches that returned nothing in stored benchmark traces, scored against a floor fixed in advance, with the table prepared for the program's umbrella issue. Prepared, not posted. |
| [`GRILL-394-391.md`](GRILL-394-391.md) | The notes behind the review: each fact resolved from the code and the docs, with file and line evidence, and the five questions as they were put to the owner. |

## The result in two lines

- **Zero-hit pointer experiment:** the count is below the floor (at most 9 of 20 tasks had an empty search; the floor is 12), so the experiment closes without a paid run.
- **Guidance-knob experiment:** goes to the spec with three variant arms, a rewrite of the breadth sentences in the shared `BACKBONE` section, and three added acceptance clauses.
- **After the review:** the owner confirmed the design on 2026-11-22; the guidance-knob experiment's spec is [`2026-11-22-guidance-task-experiment-design.md`](../superpowers/specs/2026-11-22-guidance-task-experiment-design.md).

## What is not here

The benchmark traces the count reads are not in this repository and must stay out of it. The full per-task evidence and the scripts that produced the count live with those traces, at the path named in `394-zero-hit-count.md`.
