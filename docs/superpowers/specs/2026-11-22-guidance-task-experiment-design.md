# The guidance-task experiment (#391): candidate seed, three variants, and the bar for flipping the chat default

- **Tracker record:** issue #391, child of the turn-efficiency umbrella #366. This document is the spec for that issue; no new issue is opened for it.
- **Date:** 2026-11-22 (Europe/Paris). **Read at:** `060dd545`.
- **Binding inputs:** the owner's five answers of 2026-11-22 to the grill round, and the owner's confirmation of the review page the same day (all four open points accepted as proposed). The review page and the grill notes are under `docs/review/`.
- **Supersedes, for this experiment only:** the `ask_your_docs.guidance_task` item under "Experiments", the `BACKBONE` and `TASK_HEAD: repo_qa` rows of the placement table, and ledger line 17's description in `2026-09-25-ask-turn-efficiency-program-design.md`; and the parts of issue #391's body that describe a chat-primary slice, a single variant and a cost of +800–900 tokens per Turn. Decisions B1–B3 of the decision log stand except where the owner's 2026-11-22 answers change them; each change is named below. Neither earlier document is rewritten.
- **Context only:** the zero-hit pointer experiment (#394) was closed as not planned on a $0 count below its pre-set floor. It no longer blocks final confirmation (#396), and ledger line 15 is not spent.

Three kinds of statement appear below. **Decided** items carry the owner's word. **Mechanics** items are facts read from the code at the commit above. **Flagged** items are places where a confirmed figure or sentence does not yet match the mechanics; they are listed again in Further Notes and none of them blocks the $0 work.

## Problem Statement

The ask-your-docs chat agent answers from one hand-written system prompt, `system_v3`. The search-guidance seed — the shared `BACKBONE` section, the per-task heads and the per-harness heads that the platform treats as its trainable adapter — is packaged with the product but chat never loads it. So the one harness most people use gets none of the guidance the platform is built to improve, and the multitask optimizer would start from text that chat has never run.

The owner wants to know whether loading that guidance into chat makes the agent reach the Needle in fewer Turns, and wants to flip the chat default only on evidence that holds up. The experiment as first written could not give that evidence:

- It measured a state that would never ship. The decided arm was `system_v3` plus the seed, which carries six rules twice in two wordings. A win would have shipped a slimmed `system_v4` plus the seed, which no arm measured.
- A loss could not be read. Nothing separated what the seed says from how long it is, or from the duplicated rules.
- The seed still told the agent to search more. Two passages prescribe breadth, and one clause restates an instruction that `system_v3` had deleted as call-inducing.
- The acceptance rule passes a change with no effect about half the time on 20 tasks, and it does not look at cost. The knob adds several hundred tokens to every Turn, so a small apparent gain could make every question more expensive while reading as a win.
- The cost was understated (+800–900 tokens per Turn; the real figure is about +820–1,090 for the arm as first designed).
- It was to be decided on the chat adoption set, which stopped being an acceptance slice on 2026-09-28.

The people affected are chat users (whose default prompt would change), deployers (who need a way back), users of the `vuln` and `bug_loc` tasks and of the external harness (who read the same `BACKBONE` text and are not measured here), and the owner, who pays for the arms and signs the flip.

## Solution

Run the experiment so that its result can be trusted and read, and build everything it needs at $0 before any money is spent.

1. **A knob that is off.** `ask_your_docs.guidance_task` accepts `none`, `repo_qa`, `vuln` and `bug_loc`. Its default is `none`, and under `none` the prompt chat sends is byte-identical to today's. Any other value folds the packaged seed's three sections for that task into the prompt through the channel that already exists.
2. **A candidate seed, kept on the eval side.** A full ten-section guidance document that restates the chat-derived rules in the layers the platform names, removes the call-adding sentences, and stays under every section cap. It is not packaged; the product's seed does not change unless the experiment wins.
3. **Three variants against today's product**, on `repoqa-qa/small_dev` with the pinned chat model: (a) a `system_v4` draft plus the candidate seed, which is the state a win would ship and the only variant that decides; (b) `system_v3` plus the candidate seed, to show what carrying the rules twice does; (c) `system_v3` plus placebo guidance of the same size, to show what length alone does.
4. **A higher bar for the flip.** The existing acceptance rule, plus three clauses that apply to this experiment only: the owner signs off a PASS that costs more tokens per task; no unanswered outcome class may rise; a PASS with a Turn gain under 0.6 is re-run on the 100-task slice first.
5. **A stated guard for the readers nobody measures.** The `vuln`, `bug_loc` and external heads keep their own text byte for byte, the sentences they depend on in `BACKBONE` are pinned, and the changelog of a winning PR says the `BACKBONE` change is unmeasured for them.
6. **Two clean endings.** A win flips the default to `repo_qa` and ships the candidate as the packaged seed together with `system_v4`. A loss closes unmerged, `system_v3` stays, and the knob never lands.

The work that follows this spec is the $0 machinery only. The paid run needs the owner's go, and nothing is merged before the owner decides on its result.

## User Stories

Chat users and deployers

1. As a chat user, I want the prompt my questions run under to stay exactly as it is until a change has been measured, so that my answers do not shift because of an experiment.
2. As a chat user, I want the agent to stop reading content a result already showed, so that I get my answer in fewer Turns.
3. As a chat user, I want the agent to break a question into several searches only when the question holds several behaviours, so that a simple question is not answered with a search campaign.
4. As a chat user, I want the agent to keep saying plainly when the tools found nothing, so that a slimmer prompt does not start inventing answers.
5. As a chat user, I want the agent to keep batching calls and to take an offered follow-up instead of searching again, so that the slim does not lose the rules that already save Turns.
6. As a chat user, I want an answer the agent could not fully confirm to say so on its `Not confirmed:` line, so that I know which parts to check.
7. As a deployer, I want `guidance_task: none` to be the shipped default while the experiment runs, so that upgrading changes nothing.
8. As a deployer, I want a mistyped `guidance_task` value refused at startup with the accepted values named, so that I do not run with guidance I did not choose.
9. As a deployer, I want a cap breach in any section of the packaged seed to fail chat startup loudly, so that a truncated or partial guidance block is never served silently.
10. As a deployer, I want one documented setting that brings back the old prompt after a win, so that I can revert without downgrading.
11. As a deployer, I want the knob to be a YAML key and nothing else, so that the nine-tool MCP surface my clients depend on does not move.

The owner

12. As the owner, I want the variant that decides the flip to be the state that would ship, so that I am not shipping an untested prompt.
13. As the owner, I want a diagnostic variant that keeps `system_v3` and adds the seed, so that I can see what the duplicated rules cost or buy.
14. As the owner, I want a placebo variant of the same size, so that a result can be attributed to what the guidance says and not to how long it is.
15. As the owner, I want the flip refused when any unanswered outcome class rises, so that a lower mean cannot hide more token-limit or timeout stops.
16. As the owner, I want a PASS that costs more tokens per task to wait for my recorded sign-off, so that I decide whether the Turn gain is worth the bill.
17. As the owner, I want a PASS with a Turn gain under 0.6 re-run on 100 tasks before the flip, so that noise on 20 tasks does not change the default for every user.
18. As the owner, I want the drop order fixed in advance — the placebo first, then the `system_v3` diagnostic — so that a budget stop never removes the variant that decides.
19. As the owner, I want every paid arm to wait for my go and to name its dollar cap, so that the program's stop-and-ask is respected.
20. As the owner, I want the result table recorded in the umbrella issue whether the experiment wins or loses, so that the evidence outlives the pull request.
21. As the owner, I want a loss to leave nothing behind a switch that stays off, so that the product does not collect unused settings.
22. As the owner, I want the changelog of a winning PR to say that the evidence is one chat model on one slice, so that nobody reads it as a general result.
23. As the owner, I want the case "the shipping variant fails and the diagnostic passes" to mean no flip and a revised draft, so that a win on the wrong arm is not adopted by accident.

People who read the shared guidance

24. As a user of the `vuln` task, I want my task head byte-identical after the seed rewrite, so that the source-to-sink walk it prescribes is untouched.
25. As a user of the `bug_loc` task, I want my task head byte-identical and the sentence that graph walks and file reads do not count toward the query limit kept, so that the outward walk the task needs is still allowed.
26. As a user of the external harness, I want the stop rule I am told to follow "per the backbone policy" still present, so that my harness does not lose its only stopping instruction.
27. As a user of any task this experiment does not measure, I want the changelog to say that the `BACKBONE` change is unmeasured for my task and how to revert it, so that I can judge the risk myself.
28. As the maintainer of the multitask optimizer, I want the packaged seed to carry text that chat has actually run and that was validated on a slice, so that the optimizer starts from measured text.
29. As the maintainer of the multitask optimizer, I want the task names, the harness names, the section-to-channel map and the freeze-pool digests unmoved, so that no recorded identity is orphaned by this experiment.
30. As the maintainer of the multitask optimizer, I want each fingerprint that moves on a win recorded in the umbrella issue, so that a re-key is a recorded event.

Implementers

31. As an implementer, I want one table that gives every `system_v3` rule exactly one home, so that slimming the prompt cannot drop a rule that nobody decided to drop.
32. As an implementer, I want a token ledger covering all ten seed sections, so that I know a cap is respected before chat startup tells me otherwise.
33. As an implementer, I want each rewritten rule written as trigger, action and reason, so that a conditional rule is not read as a bare prohibition.
34. As an implementer, I want every seed sentence labelled call-adding or call-removing in the ledger, so that a reviewer can see what the rewrite changed in each direction.
35. As an implementer, I want the candidate seed validated by the product's own loader, so that what the arm serves is what the product would accept.
36. As an implementer, I want the placebo defined by a construction rule I can test, so that "same size, irrelevant" is not a judgement call.
37. As an implementer, I want to know that my tickets stop before the paid run, the default flip and the merge, so that I do not spend money or change the product by finishing my work.
38. As an implementer, I want the code tickets free of changelog edits and one trailing ticket that describes the entry a winning PR would carry, so that a batch of tickets does not conflict on one file.
39. As an implementer of the later completeness arm (step 8), I want this experiment's result to name my channel, so that I build the arm on the guidance file if the knob won and on the system-prompt override if it lost.

Eval operators and reviewers

40. As an eval operator, I want each variant to be one overlay file with its plan command written in its header, so that I can plan every arm at $0 and run it with one flag after the go.
41. As an eval operator, I want an overlay that names an invalid guidance file refused at plan time, so that a typo costs a plan and not a rollout.
42. As an eval operator, I want the baseline run once and reused by the other arms, so that I do not pay for the same arm four times.
43. As an eval operator, I want the compare verb to print each added clause's finding next to PASS or FAIL, so that the acceptance record is one printed verdict and not a hand calculation.
44. As an eval operator, I want searches per task named in advance as a row to watch, so that a breadth effect is visible beside the Turn count.
45. As an eval operator, I want the tokens rows and the outcome tally in the same table as the verdict, so that the three clauses can be checked from one report.
46. As a reviewer, I want a test that fails when any untouched section of the candidate seed differs from the packaged seed, so that "untouched" is checked by a machine.
47. As a reviewer, I want a test that the prompt the knob produces equals the prompt the eval arm produced for the same guidance, so that the measured state and the shipped state are the same bytes.
48. As a reviewer, I want the default-off byte-identity test to compare against the committed `system_v3` golden, so that it keeps meaning "today's prompt" after a win.
49. As a reader of the release notes, I want the entry of a winning PR to name the knob, the seed rewrite and the new prompt version in one place, so that I understand the default change from one bullet.

## Implementation Decisions

### Scope and order of work

- **Decided.** This spec covers #391 only. The work has three stages: the $0 machinery; the paid run, which is owner-gated and belongs to no ticket written from this spec; and the outcome, which is either a winning PR or a close without merge.
- **Decided.** All $0 work lands on one experiment branch and its pull request. It is not merged before the owner decides on the result. This follows decision Q11 of the log (nothing merges behind a switch that stays off): the branch carries the knob with default `none`, and the knob reaches the main branch only in a winning PR.
- **Decided.** Tickets written from this spec must not flip the default, must not run a paid arm, and must not merge.
- **Decided.** The candidate seed text may be committed now. The rule that seed text chat may receive is committed only after the held-out chat set is frozen is satisfied (#369 is closed).
- The $0 units are: the knob; the candidate seed with its ledger and guards; the `system_v4` draft with its sentence-map tests; the placebo guidance; the eval overlay loader change with the three overlay files; the acceptance clauses in the compare verb; and one trailing unit that describes the changelog entry of a winning PR without writing it.

### The knob

- **Decided (unchanged from B1).** `ask_your_docs.guidance_task` is a `GuidanceTask` closed vocabulary with four members, `NONE`, `REPO_QA`, `VULN` and `BUG_LOC`, whose values are `none`, `repo_qa`, `vuln` and `bug_loc`. The default is `none`, defined once in the typed config model and restated in the shipped default config file for readers.
- The members other than `NONE` equal `TASK_NAMES`, in the same order, and a parity test holds them together. The vocabulary lives with the config models and not in the harness, so importing the config stays free of harness imports.
- **Resolution.** There is one function through which every ask-your-docs agent is built, and the task whose guidance is folded is resolved there: an explicit task name wins; otherwise the knob's value is used; `none` means no guidance at all. The chat page, the command-line entry and the eval runner all build through it, so all three honour the knob with no code of their own.
- **`none` is byte-identical.** Under `none` no skill document is loaded and the assembled prompt is the bytes it is today. `delivery_map_digest()` and `ask_binding_identity()` do not move. Nothing else that the program lists as "must not move" moves either: the nine-tool surface, `DELIVERED_SECTION_CHANNELS`, `HARNESS_NAMES`, `TASK_NAMES` and the freeze-pool digests.
- **Delivery.** With any other value the folded text is the `BACKBONE` section, then the task's `TASK_HEAD` section, then the `HARNESS_TASK_HEAD: ask_your_docs.<task>` section, joined as the existing fold joins them, and it reaches the model through the existing `skill_block` channel after the catalog and the session-start pack. No new channel is added.
- **Chat borrows `repo_qa`.** There is no `chat` task name; that proposal stays refuted.
- **Refusal.** A value outside the vocabulary is refused when the config is loaded, and the message names the offending value and the accepted set.
- **Loud failure.** With the knob on and no override named, the whole packaged seed is loaded and validated, all ten sections. A section over its cap stops chat startup with the loader's typed error. There is no fallback to an unguided prompt.
- **Identity.** An arm overlay that sets the knob moves that arm's settings digest, because the knob is an ordinary product key of the overlay.
- The `vuln` and `bug_loc` values exist because the vocabulary mirrors `TASK_NAMES`. Chat under those values is reachable and is not measured by this experiment (see the guard plan).
- The knob is configuration, not an MCP parameter. This is the rule for every behaviour setting in this repository and it keeps the frozen tool contract untouched.

### The candidate seed

- **Decided.** The candidate is a complete guidance document with all ten sections. It lives on the eval side beside the other arm configs, is not packaged, and reaches an arm through the overlay's `guidance_sections` key. The packaged seed is not edited by any ticket of this spec.
- **Decided.** Every edit replaces a whole section. The pull request carries a token ledger for all ten sections, counted the way the loader counts (`CHARS_PER_TOKEN = 4`, integer division over the section text), against the caps the loader enforces: 1,000 tokens for `BACKBONE`, 300 for each task head and 300 for each harness task head.
- Section by section:

| Section | Tokens at HEAD (cap) | What the candidate does |
|---|---|---|
| `BACKBONE` | 543 (1,000) | Replaced. Two rewrites, below. Must not grow past its size at HEAD. |
| `TASK_HEAD: repo_qa` | 247 (300) | Replaced. Two rewrites, below. |
| `TASK_HEAD: vuln` | 181 (300) | Byte-identical to the packaged seed. |
| `TASK_HEAD: bug_loc` | 230 (300) | Byte-identical to the packaged seed. |
| `HARNESS_TASK_HEAD: ask_your_docs.repo_qa` | 28 (300) | Rewritten: the chat-derived policy, below. |
| `HARNESS_TASK_HEAD: ask_your_docs.vuln` | 31 (300) | Rewritten on the same rules (B2 covers all three ask heads). |
| `HARNESS_TASK_HEAD: ask_your_docs.bug_loc` | 47 (300) | Rewritten on the same rules. |
| `HARNESS_TASK_HEAD: external.repo_qa` | 50 (300) | Byte-identical to the packaged seed. |
| `HARNESS_TASK_HEAD: external.vuln` | 50 (300) | Byte-identical to the packaged seed. |
| `HARNESS_TASK_HEAD: external.bug_loc` | 58 (300) | Byte-identical to the packaged seed. |

  The HEAD counts come from a read-only probe that applies the loader's arithmetic to the packaged file; the grill's probe differs by one token on two sections. The ledger the ticket generates through the loader is the authoritative one.

- **`BACKBONE`, the stop-rule passage (B3, unchanged).** The passage keeps its stop rule — the sentence that tells the agent to stop searching the moment a result names the file and symbol that must contain the answer stays word for word — and gains the definition of "already shown": a hit whose follow-up calls offer no source call has already shown its whole span, and lines a view reports as not in the index are read exactly once. The sentence about re-orienting after three searches that surface no location keeps its meaning.
- **`BACKBONE`, the breadth passages (decided 2026-11-22; this overrides B2 for these sentences).** The "decompose before retrieving" passage becomes conditional: decompose only when the question holds several behaviours; a question about one behaviour gets one query. The "five-document precision budget" passage states five documents as a ceiling, not as a quantity to reach. The clause saying that graph walks and file reads do not count toward the query limit stays word for word, because the `vuln` and `bug_loc` walks depend on it. B2's wider rule still holds for everything else: no other policy is hand-promoted into `BACKBONE`.
- **`TASK_HEAD: repo_qa`, the confirming read (B3, unchanged).** "Confirm the candidate location by reading it" becomes conditional: read to confirm only when the located hit did not already show the span.
- **`TASK_HEAD: repo_qa`, the signature clause (decided 2026-11-22).** "get_symbol where exact signatures matter" is dropped, or kept only in conditional form with the trigger "the question asks for a signature that no result has shown". It must not read as a default move. This is the clause `system_v3` removed as call-inducing; the ledger records which of the two forms was written.
- **The three `ask_your_docs.*` heads (B2, unchanged).** They are the home of all chat-derived policy: the six `system_v3` rules the sentence map moves (follow-ups are offers; never re-read what was shown; one source view and the card only for a member list; windowed reads; `get_overview` only after a search misses or for a question about the project's structure, never beside the first search; an Example only from a signature or usage already shown), the effort shape, and the instruction to use the `Not confirmed:` line, quoting `NOT_CONFIRMED_LABEL` and never respelling it. Each head drops the claim that the catalog is already in the prompt and the dead "skip the example-call snippet".
- The effort shape is the one the program spec words for step 8: the first Turn locates, the second reads the cited spans not yet rendered in one batch, the third answers, and any call past that names the claim it supports.
- **Mechanics.** The six moved rules are 1,531 characters in `system_v3`, about 383 tokens, against a 300-token cap for the head that must also hold the effort shape and the label sentence. The head is therefore a compressed rewording, not a copy, and it will sit near its cap. If the content cannot be made to fit while every rule keeps its trigger, the ticket stops and reports the ledger; it does not drop a rule on its own authority.
- **Mechanics.** The candidate is therefore not `system_v3` moved to another place. It rewords six rules and adds the effort shape and the label sentence, which `system_v3` does not carry. Variants (a) and (b) measure that whole text against today's prompt; neither isolates one of its parts.
- **Writing rules.** A rule that B2, B3 or the 2026-11-22 decisions make conditional is written as trigger, then action, then reason ("When a hit already shows the whole span, do not read it again: the read returns the same lines"), not as a bare "only when". The ledger labels every sentence of the three sections chat receives as call-adding or call-removing, starting from the grill's table. No arm tests wording style.
- **Consistency with tool output.** The heads describe what the agent does after a search misses in terms that stay true under the Zero-hit fallback (ADR 0027): an API zero hit is already answered with fallback rows in the same response, so no head tells the agent to repeat the search under another kind. No head restates or rewords a Suggestion; that wording is fixed in the server (ADR 0007).

### The `system_v4` draft and the sentence map

- **Decided.** Variant (a) serves a draft of `system_v4` from the eval side, as a `SYSTEM_PROMPT` section of the guidance file, through the existing prompt-override channel. No product template is added or edited for the experiment; `system_v3` and the earlier templates stay byte-pinned.
- **Decided.** The draft drops what the seed carries and keeps everything else. One home per rule:

| `system_v3` text | Home in the variant-(a) state |
|---|---|
| Preamble (answer only from tool results; each tool's description says when to call it) | `system_v4` |
| Rule 1 (infer the project; search unscoped first when unsure) | `system_v4` |
| Rule 2 (rewrite follow-up questions into self-contained queries) | `system_v4` |
| Rule 3 (one short clarifying question when ambiguous) | `system_v4` |
| Rule 4 (be concise, cite, fence code; "If the tools found nothing, say so plainly") | `system_v4` |
| Rule 5 (an Example only from a signature or usage already shown) | seed, `ask_your_docs.*` heads |
| Rule 6 (pinned scope) | `system_v4` |
| Rule 7 (follow-ups are offers; "Together" in one Turn, "Then" later) | seed, `ask_your_docs.*` heads |
| Rule 8 (take the offered follow-up instead of searching again) | `system_v4` — protected |
| Rule 9 (never read what a result already showed) | seed: the prohibition in the `ask_your_docs.*` heads, the definition of "already shown" in `BACKBONE`, each stated once |
| Rule 10 (one call with many targets) | `system_v4` — protected |
| Rule 11 (an exact string goes to `grep` and `read_file`; a conceptual question to `search_codebase`) | `system_v4` |
| Rule 12 (one source view; the Symbol card only for a member list) | seed, `ask_your_docs.*` heads |
| Rule 13 (windowed `read_file`) | seed, `ask_your_docs.*` heads |
| Rule 14 (`get_overview` only after a miss or for a structure question) | seed, `ask_your_docs.*` heads |
| Rule 15 (context and uniqueness check in one Turn) | `system_v4` — protected |
| Rule 16 (the gated branch rule) | the packaged `system_v4` template of a winning PR; absent from the draft, as it is from the baseline's render while no server advertises a branch argument |

- Rules 8, 10 and 15 are marked protected because neither the list of rules that move nor the earlier description of what `system_v4` keeps named them; they are the ones a slim could have dropped without a decision.
- **Mechanics.** The validator for the ask prompt requires the system text to name every routed tool (`search_codebase`, `get_symbol`, `get_context`, `grep`, `read_file`, `get_overview`). Moving rules 12 and 14 removes the only mentions of `get_symbol` and `get_overview`. The draft therefore carries one routing sentence that names them and points to the guidance for their rules, without repeating a moved rule's instruction.
- The draft's rules are numbered contiguously. It is expected to be about 500 tokens (the preamble and kept rules are about 455 of `system_v3`'s 842) and must stay under the ask prompt's 1,200-token budget.
- **What ships is what was measured.** If the experiment wins, the packaged seed and the no-variable render of the packaged `system_v4` template equal the measured candidate seed and draft, byte for byte after the document grammar's one normalisation pass. A wording change after the run is a new measurement.

### The placebo guidance

- **Decided.** Variant (c) loads placebo guidance: text of the same size as the guidance under test, about something the task does not need, so that the effect of length can be told apart from the effect of content.
- Construction rule. The placebo is a valid ten-section guidance document. Its three sections that chat receives for `repo_qa` (`BACKBONE`, `TASK_HEAD: repo_qa`, `HARNESS_TASK_HEAD: ask_your_docs.repo_qa`) each have the same character count as the candidate's section of the same name, so the token counts match exactly. Its other seven sections are byte-identical to the candidate's; they are never delivered to this arm.
- Content rule. The three delivered sections are written in the same instruction register as the seed, about a task this agent never performs. They name no tool, no file path convention and no symbol convention, and say nothing about searching, reading, stopping, citing or answering. A suitable subject is laying out a printed conference schedule (rooms, time slots, signage); the ticket may choose another that meets the rule.
- The placebo is eval-side only and is never packaged.

### Eval-side instruments ($0)

- **Mechanics (flagged).** At HEAD the eval overlay loader validates a `guidance_sections` file as a skill document against the ten skill headers only, so a file that also holds a `SYSTEM_PROMPT` section is refused at plan time, although the ask harness's binding already routes that section to the prompt-override channel. Read from the code; this session could not import the product to run it. Variant (a) therefore needs one eval-side change: the overlay loader accepts the ask-only prompt section beside the ten skill sections, validates the skill sections with the product loader exactly as today, and validates the prompt section with the ask prompt's own rules (non-empty, under 1,200 tokens, every routed tool named). The arm's recorded settings and its settings digest already cover the whole guidance mapping, so the draft's text is part of the arm's identity.
- **Three overlay files**, one per variant, each with its free plan command in its header (the Members-line arm config is the precedent) and none with a paid command pre-filled beyond the note that `--confirm-spend` needs the owner's go:
  - (a) names a guidance file holding the candidate seed plus the `SYSTEM_PROMPT` draft;
  - (b) names a guidance file holding the candidate seed alone;
  - (c) names the placebo document.

  Each overlay holds the guidance key and nothing else, so the variants differ from the baseline only in the text they load; every other arm setting is the before/after default the ladder's `repoqa-qa` arms use. The ten skill sections of (a)'s file and (b)'s file are held equal by a test, so the seed has one text.
- **The overlays do not set `guidance_task`.** On the eval path the binding hands the build an explicit task name whenever an arm carries guidance, and an explicit name wins over the knob, so the knob would add nothing to what is served. What ties the measurement to the knob is a test: for the same guidance document, the prompt a build serves under `guidance_task: repo_qa` equals, byte for byte, the prompt the eval path serves for a `repo_qa` task. This replaces the issue's criterion that the overlay carry the knob.
- Once the product has the knob, an overlay may set it and the loader accepts it. Three eval tests use the knob today as the key a product refuses (in the arm-settings, variant-command and block-probe test modules); they become tests that it is accepted and moves the settings digest, and the refusal cases take another unknown key.
- **The acceptance clauses in the compare verb.** The three clauses are coded as an opt-in mode of the compare verb, following the precedent of its existing opt-in rule for the completeness arm: without the mode the verb's output is byte-identical to today. With it, each variant's line carries the standard verdict and then one finding per clause. The verb's printed verdict stays the acceptance record (ADR 0025); the clauses are not applied by hand. The mode needs no new measurement: it reads rows the report already has. The clause for the minimum gain takes the slice's noise edge as an argument and is evaluated only when the argument is given.
- **Pre-registered rows.** No new metric is added. The table names in advance, as reported and not gating:
  - `search calls` per task (the agent's own `search_codebase` calls; the row exists). Expected direction after the breadth rewrite: not up.
  - Turns after needle with its two call companions, and the Turns before the Needle was first shown (the headline less Turns after needle).
  - `tokens in (per task)`, with the uncached-tokens rows beside it.
  - The outcome tally, one count per outcome.

### The measurement (owner-gated; no ticket of this spec runs it)

- **Decided.** The only slice a PASS or FAIL reads is `repoqa-qa/small_dev`, 20 tasks, each with a single-site Needle. The chat adoption set is informational: no chat arm is planned or priced here, and one added later needs its own ledger entry and go.
- **Decided.** The chat model is the pinned block: `qwen/qwen3.8-27b` in thinking mode, the existing serving file, a 12-Turn budget. One model; no second-model arm.
- **Decided.** The arms, all on one commit (the head of the experiment branch):

| Arm | System prompt | Guidance | Role | Tokens per Turn against today |
|---|---|---|---|---|
| Baseline | `system_v3` | none | Today's product | — |
| (a) | `system_v4` draft | candidate seed, `repo_qa` | Decides the flip | about +480–750 |
| (b) | `system_v3` | candidate seed, `repo_qa` | Diagnostic: the cost or gain of carrying six rules twice | about +820–1,090 |
| (c) | `system_v3` | placebo guidance | Diagnostic: the effect of length alone | the same as (b), by construction |

- Three variants is the most the compare verb's Holm correction covers. Holm changes the printed p values only, never PASS or FAIL.
- **Decided.** Only (a) decides. If (a) fails and (b) passes, nothing flips in this PR and the `system_v4` draft is revised; measuring a revised draft is a new paid run with its own go.
- **Decided.** If the program's $25 stop-and-ask bites, drop (c) first, then (b).
- **Mechanics (flagged).** The compare verb computes the Correctness band from an A/A pair and refuses a replicate that is not the baseline's twin (same commit, model, Turn budget and settings digest). No stored A/A pair exists for the current accepted state, and none can exist for the experiment commit. The run therefore needs a fifth 20-task arm: the baseline's A/A replicate. The baseline is run once, reused by every other arm, and the replicate is run with the flag that permits identical arms.
- **Mechanics.** The band the acceptance rule reads is the larger of the pair's `needle cited` difference and one task. On 20 tasks one task is 0.050, the figure on record; if the new pair differs by more than one task the band is wider, and the table states the band used.

### The acceptance rule

- **Decided.** The existing rule (Q10, as coded in the compare verb): the penalised Turns-to-answer goes down as a point estimate; the Budget-exhaustion rate does not rise; `needle cited` stays within the Correctness band below the baseline.
- **Decided.** Three clauses are added, for this experiment only:
  - **(a) Tokens.** A PASS whose `tokens in (per task)` is above the baseline's flips the default only with the owner's recorded sign-off. The sign-off quotes that row and the uncached-tokens rows.
  - **(b) Unanswered outcomes.** No unanswered outcome class may rise. Budget exhaustion keeps its existing guard (the rate over both exhaustion outcomes); in addition the counts of `starved_reply`, `timeout` and `unanswered_empty` in the variant must not exceed the baseline's. The confirmed text names token-limit stops, timeouts and Budget exhaustion; `unanswered_empty` is included on the literal reading of "no unanswered outcome class", since ADR 0025 counts it among them.
  - **(c) Minimum gain.** A PASS whose Turn gain is under 0.6 — the A/A edge at 20 tasks — is re-run on the 100-task `repoqa-qa/dev` slice before the flip.
- Order of reading, for variant (a):

| Step | Reading | What follows |
|---|---|---|
| 1 | FAIL under the existing rule | No flip. If (b) passed, the draft is revised; otherwise the PR closes unmerged. |
| 2 | PASS, and an unanswered outcome class rose | No flip (clause b). |
| 3 | PASS, Turn gain under 0.6 | Re-run on 100 tasks (clause c). The re-run is read by the existing rule and clause (b); a FAIL there means no flip. |
| 4 | The reading that stands (the re-run if one ran, else the 20-task run) has `tokens in (per task)` above baseline | Flip only with the owner's recorded sign-off (clause a). |
| 5 | None of the above | The winning PR flips the default, on the owner's go. |

- **Decided.** The re-run is read with the same rule as the 20-task run. The minimum-gain clause is not applied a second time: 0.6 is the noise edge of the 20-task slice and there is no larger slice to escalate to.
- **Mechanics.** `small_dev` is drawn from `dev`, so the re-run contains the 20 tasks already run. It is a larger sample of the same pool, not a fresh one. It consumes no test split.
- These clauses do not change the rule for any other step or experiment of the program, and they need no ADR: ADR 0025 records how an acceptance is scored, and the verdict stays the compare verb's.

### The guard plan for the readers this experiment does not measure

- **Decided.** No arm of this experiment measures the `vuln` head, the `bug_loc` head or any external head. All of them read `BACKBONE`, because guidance folds as `BACKBONE`, then the task head, then the harness task head. They are guarded as follows.
- **Scope guard.** The rewrite lives only in the eval-side candidate file until a winning PR. The packaged seed is the only text the other harnesses and tasks read, and it changes in that PR and nowhere else. A loss changes nothing for any reader.
- **$0 test guards**, in the experiment PR:
  - `TASK_HEAD: vuln`, `TASK_HEAD: bug_loc` and the three `external.*` heads are byte-identical to the packaged seed;
  - the stop-rule sentence is present word for word;
  - the clause that graph walks and file reads do not count toward the query limit is present word for word;
  - `BACKBONE` is no longer than at HEAD;
  - the ledger covers all ten sections and every section is under its cap.
- **Wording guard.** The breadth rewrite states conditions; it deletes no permission. The multi-step walks of `vuln` and `bug_loc` stay governed by their own task heads.
- **What is measured.** For `ask_your_docs` on `repo_qa`: variants (a) and (b), with `search calls` per task beside Turns.
- **Follow-up, not part of this spec's work.** The ledger's one bug-localization item (line 22, a pilot of about ten `lca-bug-loc` tasks) may be run under both `BACKBONE` texts when it is scheduled. It is informational, it does not gate the flip, and no ticket written from this spec runs it.
- **Stated, not measured.** There is no ledger item for `vuln` and no config binds the external harness to the before/after campaign. For those readers the changelog of a winning PR says that the `BACKBONE` change is unmeasured and that restoring the two passages restores the old text.

### Outcomes

- **A win** (variant (a) clears the table above, and the owner gives the go). The same PR then:
  - flips the shipped default of `ask_your_docs.guidance_task` to `repo_qa`;
  - ships the candidate as the packaged seed, including the `BACKBONE` rewrite;
  - ships `system_v4` as a new template, with the gated branch rule appended as its last rule; earlier templates are not edited;
  - regenerates the goldens and the optimizer-side seeds that follow the active prompt;
  - records two fingerprint moves in the umbrella issue: the ask prompt's a second time, the search skill's once;
  - keeps every routed tool named in the template a live tool;
  - carries the root changelog entry described below.
- **A loss.** The PR closes unmerged with its table in the umbrella issue. `system_v3` stays the product and the optimizer's ask-prompt seed. The knob never lands.
- **Decided (B4).** After a win, step 8 runs its arm as a guidance file. After a loss it takes the system-prompt override channel.
- **The changelog entry of a winning PR** (described here, written only in that PR). One root entry: the knob now defaults to `repo_qa`; the packaged seed is rewritten, including the two `BACKBONE` breadth passages; `system_v4` is the active prompt. It states that the evidence is one chat model on one slice; that the `BACKBONE` change is unmeasured for `vuln`, `bug_loc` and the external harness, with how to restore the old passages; and that `guidance_task: none` restores the old prompt. The eval suite's own changelog gets the entry for the overlay loader and the compare verb mode in the same PR. No changelog file is edited before the owner's decision.
- **Flagged.** The sentence "`guidance_task: none` restores the old prompt" is true only if a build under `none` still serves the `system_v3` text after the win. The earlier design had one active-template switch, under which `none` after a win would serve `system_v4` with no guidance: a prompt missing six rules, which is neither the old prompt nor a measured one. The winning PR must therefore make the served template follow the knob (`none` serves `system_v3`; any other value serves `system_v4`), or the owner rewords the sentence. This needs the owner's word before the winning PR; it changes nothing in the $0 work, whose `none` test already compares against the committed `system_v3` golden.

### Cost and budget

- **Decided.** Program limits are unchanged: ceiling $35, stop-and-ask at $25, the owner's go before every paid run, each run bounded by its own `--max-usd`. The spend is at the model provider; the Bench VM cap does not apply.
- **Decided.** The baseline and three variants on `small_dev` are about $0.35–0.55 at recorded prices ($0.08–0.13 per 20-task arm), inside ledger line 17 ($0.60–1.20).
- **Decided.** The 100-task re-run, if clause (c) applies, is about $1.0–1.3 for the baseline and variant (a). With it #391 reaches about $1.35–1.85, above line 17's high of $1.20.
- **Booking (stated here, approved by the owner with the go for the re-run).** The excess is booked against line 17 as an overrun and is covered by the $1.00–2.00 that line 15 no longer needs; the program ceiling does not move.
- **Flagged.** Those figures price four arms and two arms. The A/A replicate the compare verb requires adds one 20-task arm (about $0.08–0.13, so about $0.40–0.65 for the 20-task run) and, for the re-run, one 100-task arm (about $0.40–0.65, so about $1.2–1.95 for the re-run). The worst case for #391 is then about $2.60, still inside lines 17 and 15 together. The owner's go for each run names the cap with this in view.

## Testing Decisions

A good test here observes a boundary a user or an operator can see: the system message a real build serves, the error a bad config produces, the sections a loader accepts, the plan an overlay yields, the verdict the compare verb prints. It does not assert how a function is wired inside. Fakes are the named doubles the two suites already have; no test makes a model, embedding or network call, and none reads a key.

Two seams carry almost everything. On the product side it is **the served prompt**: the system message an ask-your-docs build hands the model. On the eval side it is **the plan and the verdict**: what the overlay loader accepts at plan time and what the compare verb decides over fixture arms. The remaining tests are freeze pins.

- **The served-prompt seam (product).** Prior art: `test_prompt_seam.py`, `test_system_prompt_v3.py`, the committed `system_v3` golden.
  - Under `guidance_task: none`, and with the key absent, the assembled prompt equals today's bytes: the `system_v3` golden, the catalog, the session-start pack, no guidance block.
  - Under `repo_qa`, the message ends with the three sections of the packaged seed in fold order, after the catalog and the pack.
  - An explicit task name wins over the knob.
  - For one guidance document, the knob's prompt and the eval path's prompt for a `repo_qa` task are the same bytes.
- **The config seam (product).** A value outside the vocabulary is refused and the message names it and the accepted set. The vocabulary's task members equal `TASK_NAMES` in order. The shipped default config restates `none`. Prior art: `test_config_ask_your_docs_scope.py` for the config block, and the provider-registry parity tests for a vocabulary held equal to a tuple defined elsewhere.
- **Identity pins.** `delivery_map_digest()` keeps its pinned literal (`test_binding.py`); `ask_binding_identity()` keeps its pin (`test_ask_binding.py`); the freeze-pool golden and the seed-parity test (`test_prompt_freeze.py`, `test_prompt_seed_parity.py`) pass untouched. A failing pin here means the experiment moved something it promised not to move.
- **The guidance-document seam.** Prior art: `test_skill_artifact_loader.py` (caps, canonical surface, unknown headers) and `test_guidance_fold.py` (the cross-harness fold parity).
  - The candidate and the placebo load through the product loader with every section under its cap.
  - The five untouched sections of the candidate equal the packaged seed's, byte for byte.
  - `BACKBONE` is no longer than the packaged one and contains the stop-rule sentence and the walks-and-reads clause word for word.
  - The repo-QA task head no longer carries the unconditional signature clause or the unconditional confirming read; no ask head carries the catalog claim or the example-snippet sentence.
  - The ask heads quote the `Not confirmed:` label from the shared constant.
  - The placebo's three delivered sections match the candidate's character counts, and its seven others equal the candidate's.
  - The ledger in the PR is produced by the same arithmetic the test asserts, for all ten sections.
  - The external fold over the candidate differs from the fold over the packaged seed only inside `BACKBONE`.
- **The sentence-map seam.** One assertion per `system_v3` rule, in the style of `test_system_prompt_v3.py`: each kept rule's sentence is in the draft; each moved rule's sentence is absent from the draft and its instruction is present in the ask head; rules 8, 10 and 15 are asserted by name. The draft passes the ask prompt's validator (`test_ask_prompt_artifact.py`): six routed tools named, under budget, rules numbered contiguously.
- **The overlay seam (eval).** Prior art: `test_before_after_arm_settings.py`, `test_before_after_variant_command.py`, `test_before_after_block_probe.py`, `test_members_line_arm_config.py`.
  - A guidance file with a `SYSTEM_PROMPT` section beside the ten skill sections is parsed at plan time, and a draft that fails the ask prompt's rules is refused there.
  - Each of the three overlays plans against the baseline with a distinct settings digest and a printed settings difference.
  - Each overlay holds only the guidance key; (a)'s skill sections equal (b)'s.
  - `guidance_task` in an overlay is accepted and moves the digest (the three tests that use it as the refused key change with the product).
  - An arm without an overlay still hands every rollout no guidance.
- **The binding seam.** Prior art: `test_binding.py` (prompt sections route to the override seam; skill sections persist a validated candidate document). A guidance mapping holding both the draft and the skill sections serves the draft as the system text, folds the three sections after it, and writes the candidate document beside the trajectory.
- **The verdict seam (eval).** Prior art: `test_before_after_acceptance.py`, `test_before_after_compare.py` and the outcome fixtures. A truth table over fixture estimates for the added mode: each row of the reading table above, including a PASS with a gain under the edge, a PASS with one more `starved_reply`, and a PASS with more tokens in per task. With the mode off, the verdict text is byte-identical. The rows the clauses read are tested from fixture trajectories through the row readers, not through a report golden.
- **The gate set.** The full CI gate set of the repository before any push, plus the local eval-suite gate. A change without a passing full run is not finished.

## Out of Scope

- The paid run, its go and its dollar cap; any OpenRouter, embedding or cloud call.
- Flipping the default to `repo_qa`, merging the experiment PR, or closing it. These follow the owner's decision on the result.
- Editing the packaged seed, adding a packaged `system_v4` template, regenerating goldens or optimizer seeds, or recording fingerprint moves. These belong to a winning PR only.
- Writing any changelog entry now.
- The zero-hit pointer experiment (#394): closed, no arm, no code.
- A second-model arm of any kind. A chat arm as an acceptance slice. A `chat` task name.
- Edits to the `vuln`, `bug_loc` or external heads, and any measurement of them. The `bug_loc` pilot under both `BACKBONE` texts is a later, informational follow-up.
- Hand promotion of head text into `BACKBONE` beyond the two rewrites named here; that stays the multitask optimizer's job.
- An arm for wording style.
- The acceptance rule of any other step or experiment. The three clauses are this experiment's only.
- The completeness rule and its arm (step 8), the `SERVER_INSTRUCTIONS` alignment, and any edit to the tool descriptions.
- A tenth tool, a new tool parameter or a new envelope field.
- Glossary edits and ADR changes. Two glossary gaps are noted below for a later domain-modeling pass.
- How the served template follows the knob after a win (flagged above); it is the winning PR's design.
- Posting to, editing or closing any GitHub issue; this spec's tickets do none of that.

## Further Notes

**Open points for the owner, none blocking the $0 work.**

1. *The A/A replicate.* The compare verb needs the baseline's twin on every slice it reads. The confirmed estimates price four 20-task arms and two 100-task arms; with the replicates the figures are about $0.40–0.65 and about $1.2–1.95. To confirm at the go for each run.
2. *`none` after a win.* The confirmed changelog sentence requires `none` to keep serving `system_v3`. To settle before the winning PR.
3. *Which tokens row clause (a) reads.* The confirmed wording is "tokens-in per task", and the report has a row of that name; the grill's draft said "uncached tokens-in per task", for which only total and per-Turn rows exist. This spec follows the confirmed wording and has the sign-off quote both.
4. *The band figure.* 0.050 is one task of 20 and is the floor; the run's own A/A pair sets the band and may make it wider.

**Lookups that turned out not to be needed.** The arm directory of the current accepted state is not required: every arm of this experiment, the baseline included, runs on the experiment commit, and the default-off byte-identity tests are what make that baseline today's product. The fresh `repoqa-qa` draw and the plumbing for same-commit variant arms are already on the main branch.

**If the experiment loses.** Two eval-side pieces are useful beyond it: the overlay loader accepting a system-prompt section, which step 8 needs on its fallback channel (B4), and the compare verb's opt-in mode. Whether they are carried into their own eval-only change when the experiment PR is closed is a choice for that moment; this spec does not plan it.

**Vocabulary.** This spec uses the glossary's terms: Arm, A/A pair, Noise band, Correctness band, Needle, Turn, Turns-to-answer, Budget exhaustion, Turns after needle, Suggestion, Zero-hit fallback, Symbol card. Two terms it needs are not in the glossary yet and are drafted outside the repository: **Variant** (a candidate arm compared against a baseline arm that other candidate arms share) and **Placebo guidance** (guidance text of the same size as the guidance under test, about something the task does not need). `BACKBONE`, task head, harness task head and seed are section names of the guidance document, defined in the platform design spec.

**Evidence.** The grill notes and the review page in `docs/review/` hold the file-and-line evidence for every fact not marked Mechanics here, and the literature the design leans on: a length-matched irrelevant control as the minimum bar for a guidance experiment (arXiv:2608.23067), breadth advice raising a Qwen model's tool calls (arXiv:2611.22539), and the wording form for conditional rules (arXiv:2611.04367, a four-page study, which is why no arm is spent on it).

**ADRs respected.** ADR 0005 (one shared tool-description document; untouched). ADR 0007 (Suggestion wording is fixed in the server; guidance does not restate it). ADR 0025 (the compare verb's verdict is the acceptance record; `needle cited` is the gating correctness guard; every unanswered outcome counts budget + 1 in the penalised mean). ADR 0027 (an API zero hit is answered in the same response; guidance about misses stays true under it). ADRs 0001–0004 and the tool contract (the nine-tool surface does not move).
