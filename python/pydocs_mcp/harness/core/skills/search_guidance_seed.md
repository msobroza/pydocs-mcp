=== BACKBONE ===
Search policy for driving the pydocs-mcp retriever. This section carries
policy — which retrieval move to make when — not a tool catalogue; parameter
shapes live in the tool descriptions.

Route by expected lexical overlap between the question and the evidence:

- The question names things the code also names (identifiers, error strings,
  config keys, file names) — lexical tools are strong: grep for the literal,
  glob for file layout, get_symbol for a known dotted path.
- The question describes behavior in words the code never spells out —
  semantic search is the direct route: search_codebase phrased in behavior
  terms. Do not grep the question's own words; with only literal tools
  bound, translate the behavior into identifiers the code would plausibly
  use (verbs, error strings, config keys), grep those candidates, and widen
  from file layout.
- Who-calls / what-breaks / what-extends — get_references. Design rationale —
  get_why, before proposing changes. Context around known symbols —
  get_context, with ALL targets in ONE call: one shared budget beats N
  sequential calls.

If a tool named here is not bound in this harness, take the nearest bound
alternative rather than skipping the step.

Decompose before retrieving: at most three sharp search queries
(search_codebase or grep — graph walks and file reads do not count), one
behavior each. Learn the repo's own vocabulary from early results and
re-phrase in its terms; two focused queries beat one sprawling sentence
that mixes concerns.

Spend a ~5-document precision budget: keep about five retrieved documents
in play at a time — answer quality saturates around there regardless of
context window, so precision at small k binds, not recall. Retire hits you
have exhausted rather than accumulating marginal ones.

Stop searching the moment a result names the file and symbol that must
contain the answer: retrieval locates, the file confirms. Read exactly what
the result cites (read_file, or get_symbol depth="source") instead of
re-querying for a cleaner phrasing. If three search queries surface no
location, re-orient — widen scope or map the layout — rather than
rephrasing in a loop.
=== TASK_HEAD: repo_qa ===
Repository-comprehension QA across corpora. Match the answer to the
question's probe: Where — name the repo-relative file path(s) that hold the
answer, plus the symbol when the question asks which function or class;
What / How — the mechanism, via get_context on the load-bearing symbols and
get_symbol where exact signatures matter; Why — the get_why lane before any
speculation. Questions arrive in both shapes — some name identifiers the
code also uses, some only describe behavior — so read which one you have
and route it per the backbone policy rather than assuming either. Spell
every path and symbol exactly as the code spells them; a bare basename or a
paraphrased name does not answer a location question. Confirm the candidate
location by reading it before committing to it; an unconfirmed name is the
failure mode this task punishes. Name the exact file path for every claim,
add only the mechanism the question actually asked for, and keep the answer
concise and code-grounded.
=== TASK_HEAD: vuln ===
Security needle-search: the snapshot contains one exploitable condition,
and security questions phrased by impact usually share no identifiers
with the code — search by behavior, not by the question's own words.
Begin where external input is parsed or received and where operations
with security consequences live; then walk the flow with get_references —
callees forward from the entry point, callers backward from the risky
operation — until source and sink connect, reading the cited files along
the way. Report three things, each cited to file and symbol: where
untrusted input enters, the dangerous operation it reaches, and the
vulnerability class derived from the abuse path you actually traced —
never a guessed label.
=== HARNESS_TASK_HEAD: ask_your_docs.repo_qa ===
The catalog is already in your prompt; skip orientation calls and route the
first query straight from the question.
=== HARNESS_TASK_HEAD: ask_your_docs.vuln ===
The catalog is already in your prompt; skip orientation calls. Skip the
usual example-call snippet; the report is the answer.
=== HARNESS_TASK_HEAD: external.repo_qa ===
No catalog is pre-injected: orient first — get_overview on an unfamiliar
project — then route per the backbone policy. Answers must be
self-contained: name the file path and the line numbers you read.
=== HARNESS_TASK_HEAD: external.vuln ===
Orient first — no catalog is pre-injected. With only literal tools
bound, grep confirms a located site rather than finding one: translate
the behavior into plausible identifiers per the backbone policy.
