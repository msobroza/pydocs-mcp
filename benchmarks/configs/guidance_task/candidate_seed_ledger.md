# Token ledger for the guidance-task candidate seed

The ledger for `candidate_seed.md` in this directory. That file is the
candidate guidance document of the guidance-task experiment (spec:
`docs/superpowers/specs/2026-11-22-guidance-task-experiment-design.md`,
"The candidate seed"). It is eval-side only and not packaged. An arm loads it
through an overlay's `guidance_sections` key, and the product's loader
validates it. The packaged seed it is compared with is
`python/pydocs_mcp/harness/core/skills/search_guidance_seed.md`.

A test (`benchmarks/tests/campaign/test_guidance_candidate_seed.py`)
recomputes every number and every sentence row below and fails when this file
disagrees with the candidate. If you edit the candidate, edit this ledger in
the same change.

## Section counts

The counts use the loader's arithmetic: characters of the section text in the
parser's normal form (one trailing newline trimmed), and tokens =
characters // `CHARS_PER_TOKEN` (4). The caps are the ones the loader enforces.
"Packaged" columns are the packaged seed at the experiment's base commit.

| Section | Status | Characters | Tokens | Cap | Packaged characters | Packaged tokens |
|---|---|---|---|---|---|---|
| BACKBONE | replaced | 2171 | 542 | 1000 | 2173 | 543 |
| TASK_HEAD: repo_qa | replaced | 961 | 240 | 300 | 990 | 247 |
| TASK_HEAD: vuln | byte-identical | 726 | 181 | 300 | 726 | 181 |
| TASK_HEAD: bug_loc | byte-identical | 922 | 230 | 300 | 922 | 230 |
| HARNESS_TASK_HEAD: ask_your_docs.repo_qa | replaced | 1151 | 287 | 300 | 115 | 28 |
| HARNESS_TASK_HEAD: ask_your_docs.vuln | replaced | 1151 | 287 | 300 | 125 | 31 |
| HARNESS_TASK_HEAD: ask_your_docs.bug_loc | replaced | 1168 | 292 | 300 | 189 | 47 |
| HARNESS_TASK_HEAD: external.repo_qa | byte-identical | 200 | 50 | 300 | 200 | 50 |
| HARNESS_TASK_HEAD: external.vuln | byte-identical | 202 | 50 | 300 | 202 | 50 |
| HARNESS_TASK_HEAD: external.bug_loc | byte-identical | 233 | 58 | 300 | 233 | 58 |

What chat receives for `repo_qa` (`BACKBONE`, `TASK_HEAD: repo_qa`,
`HARNESS_TASK_HEAD: ask_your_docs.repo_qa`) is 1,069 tokens, against 818 for
the packaged seed's three sections, before the two `\n` tier separators.

## Decisions recorded here

- **Signature clause form: dropped.** `TASK_HEAD: repo_qa` no longer carries
  "get_symbol where exact signatures matter" in any form. The conditional form
  was not written. A signature the question needs is reached through the ask
  head's one-source-view rule.
- **The stop rule** is kept word for word. The definition of "already shown"
  sits in `BACKBONE`. The prohibition ("never read again") sits in the ask
  heads. Each is stated once.
- **The three ask heads share one text** for the six moved rules, the effort
  shape and the label sentence. `ask_your_docs.bug_loc` differs in one reason
  clause: its first query routes from the report's own literals. This keeps
  the routing from the packaged head.
- **The label sentence** follows the product's finalize note: "end with one
  line that starts with `Not confirmed:`". The eval suite's outcome reader
  matches the label at the start of a line.
- **The external `repo_qa` fold also changes in `TASK_HEAD: repo_qa`.** That
  head is harness-invariant, so the decided rewrite reaches the external
  harness. For `vuln` and `bug_loc` the external fold differs only inside
  `BACKBONE`. In every case the `external.*` heads are byte-identical. The
  guard test pins exactly this.
- **Labels.** Each delivered sentence is labelled `call-adding` or
  `call-removing`. A sentence that neither adds nor removes a call (framing,
  routing between tools, answer form) is labelled `neutral`. The grill's table
  under Q14 does the same with its "routing" rows. The Note column gives the
  form of each conditional rule (trigger → action → reason) and how the
  sentence changed from the packaged seed.

## Sentence labels: the three sections chat receives for `repo_qa`

A sentence is a span ending in a full stop, inside one paragraph or one
bullet. "Opening" is its first five words, whitespace-collapsed.

| Section | # | Opening | Label | Note |
|---|---|---|---|---|
| BACKBONE | 1 | Search policy for the pydocs-mcp | neutral | Framing; tightened from the packaged three-line preamble, same meaning. |
| BACKBONE | 2 | Route by expected lexical overlap | neutral | Routing; unchanged. |
| BACKBONE | 3 | - The question names things | neutral | Routing to lexical tools; unchanged. |
| BACKBONE | 4 | - The question describes behavior | neutral | Routing to semantic search; unchanged. |
| BACKBONE | 5 | Do not grep the question's | call-adding | Only in a literal-only harness, not chat; unchanged. |
| BACKBONE | 6 | - Who-calls / what-breaks / | neutral | Routing; unchanged. |
| BACKBONE | 7 | Design rationale — get_why, before | call-adding | On change questions only; unchanged. |
| BACKBONE | 8 | Context around known symbols — | call-removing | One call with all targets; unchanged. |
| BACKBONE | 9 | If a tool named here | call-adding | Mildly: take the nearest bound tool; unchanged. |
| BACKBONE | 10 | When the question holds several | call-adding | Rewritten as conditional: trigger (several behaviors) → decompose (at most three queries) → one behavior each. Was the unconditional "Decompose before retrieving". The walks-and-reads clause is kept word for word. |
| BACKBONE | 11 | When it asks about one | call-removing | New: trigger (one behavior) → one query → reason (a rephrasing finds the same). |
| BACKBONE | 12 | Keep at most about five | call-removing | Rewritten: five documents as a ceiling, not a quota. Was "Spend a ~5-document precision budget", which could read as a target. |
| BACKBONE | 13 | Retire exhausted hits; do not | call-removing | Same meaning as the packaged sentence, shorter. |
| BACKBONE | 14 | Stop searching the moment a | call-removing | The stop rule; word for word. |
| BACKBONE | 15 | Read what the result cites | call-adding | One read, now conditional: only what the result has not already shown. Was unconditional "Read exactly what the result cites". |
| BACKBONE | 16 | Already shown: a hit whose | call-removing | New: the definition of "already shown" (system_v3 rule 9's definition, stated once). |
| BACKBONE | 17 | If three search queries surface | call-adding | After three misses only; the re-orient sentence keeps its meaning. |
| TASK_HEAD: repo_qa | 1 | Repository-comprehension QA across corpora. | neutral | Framing; unchanged. |
| TASK_HEAD: repo_qa | 2 | Match the answer to the | call-adding | get_context on load-bearing symbols; get_why on why-questions. The signature clause is dropped (was call-adding). |
| TASK_HEAD: repo_qa | 3 | Questions arrive in both shapes | neutral | Routing; unchanged. |
| TASK_HEAD: repo_qa | 4 | Spell every path and symbol | neutral | Answer form; unchanged. |
| TASK_HEAD: repo_qa | 5 | When the located hit has | call-adding | Rewritten as conditional: trigger (hit has not shown its span) → read before committing → reason (an unconfirmed name is the failure this task punishes). Was the unconditional "Confirm the candidate location by reading it". |
| TASK_HEAD: repo_qa | 6 | Name the exact file path | neutral | Answer form; unchanged. |
| HARNESS_TASK_HEAD: ask_your_docs.repo_qa | 1 | Follow-up calls on a result | call-removing | system_v3 rule 7: follow-ups are offers; take only those needed. |
| HARNESS_TASK_HEAD: ask_your_docs.repo_qa | 2 | Issue the calls on a | call-removing | system_v3 rule 7: "Together:" calls in one Turn, "Then:" later; each with its reason. |
| HARNESS_TASK_HEAD: ask_your_docs.repo_qa | 3 | Never read again what a | call-removing | system_v3 rule 9's prohibition, with its reason (the read returns the same lines). |
| HARNESS_TASK_HEAD: ask_your_docs.repo_qa | 4 | When you need a symbol's | call-removing | system_v3 rule 12: one source view; the Symbol card only when the question needs a member list (reason: the card adds no code). |
| HARNESS_TASK_HEAD: ask_your_docs.repo_qa | 5 | Give read_file an offset and | call-removing | system_v3 rule 13: windowed reads; widen only to follow a needed reference. |
| HARNESS_TASK_HEAD: ask_your_docs.repo_qa | 6 | Call get_overview only after a | call-removing | system_v3 rule 14: only after a miss or for a structure question, never beside the first search. Replaces the packaged "catalog is already in your prompt; skip orientation calls". |
| HARNESS_TASK_HEAD: ask_your_docs.repo_qa | 7 | When a result showed a | neutral | system_v3 rule 5: an Example only from a signature or usage already shown; answer form. |
| HARNESS_TASK_HEAD: ask_your_docs.repo_qa | 8 | Effort: the first turn locates, | call-removing | New: the effort shape (locate, read in one batch, answer; any further call names its claim). |
| HARNESS_TASK_HEAD: ask_your_docs.repo_qa | 9 | When part of the answer | call-removing | New: the `Not confirmed:` line, so an unconfirmed part is labelled rather than verified by more calls. |

## Packaged sentences the candidate removes

These sentences of the packaged seed's three `repo_qa` sections are gone or
reworded. The label is the packaged sentence's own label.

| Packaged sentence (short) | Label | What replaced it |
|---|---|---|
| "Decompose before retrieving: at most three sharp search queries … one behavior each." | call-adding | Sentences 10 and 11 of `BACKBONE`. |
| "Learn the repo's own vocabulary from early results and re-phrase in its terms; two focused queries beat one sprawling sentence that mixes concerns." | call-adding | Folded into sentence 10 ("in the repo's terms"). The breadth claim is gone. |
| "Spend a ~5-document precision budget: keep about five retrieved documents in play …" | call-adding (risk: read as a target) | Sentence 12 of `BACKBONE`. |
| "Read exactly what the result cites … instead of re-querying for a cleaner phrasing." | call-adding (+1 read when already shown) | Sentences 15 and 16 of `BACKBONE`. |
| "… and get_symbol where exact signatures matter" | call-adding | Dropped. |
| "Confirm the candidate location by reading it before committing to it …" | call-adding | Sentence 5 of `TASK_HEAD: repo_qa`. |
| "The catalog is already in your prompt; skip orientation calls and route the first query straight from the question." | call-removing | Sentence 6 of the ask head. The catalog claim is dropped. |

The two other ask heads also drop their catalog claim and their "skip the
example-call snippet" sentence.
