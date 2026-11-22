=== BACKBONE ===
Search policy for the pydocs-mcp retriever: which retrieval move to make
when, not a tool catalogue; parameter shapes live in the tool descriptions.

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

When the question holds several behaviors, decompose it: at most three
sharp search queries (search_codebase or grep — graph walks and file reads
do not count), one behavior each, in the repo's terms. When it asks
about one behavior, send one query: a rephrasing finds the same.

Keep at most about five documents in play — a ceiling, not a quota:
answer quality saturates there, so precision at small k binds.
Retire exhausted hits; do not pile up marginal ones.

Stop searching the moment a result names the file and symbol that must
contain the answer: retrieval locates, the file confirms. Read what the
result cites and has not already shown (read_file, or get_symbol
depth="source") rather than re-querying. Already shown: a hit whose
follow-up calls offer no source call has shown its whole span; lines a
view reports as not in the index are read exactly once. If three search
queries surface no location, re-orient — widen scope or map the layout —
rather than rephrasing in a loop.
=== TASK_HEAD: repo_qa ===
Repository-comprehension QA across corpora. Match the answer to the
question's probe: Where — name the repo-relative file path(s) that hold the
answer, plus the symbol when the question asks which function or class;
What / How — the mechanism, via get_context on the load-bearing symbols;
Why — the get_why lane before any speculation. Questions arrive in both
shapes — some name identifiers the code also uses, some only describe
behavior — so read which one you have and route it per the backbone policy
rather than assuming either. Spell every path and symbol exactly as the
code spells them; a bare basename or a paraphrased name does not answer a
location question. When the located hit has not already shown its span,
read it before committing to it: an unconfirmed name is the failure mode
this task punishes. Name the exact file path for every claim, add only the
mechanism the question actually asked for, and keep the answer concise and
code-grounded.
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
=== TASK_HEAD: bug_loc ===
File-level bug localization: a bug or issue report plus a repository
snapshot, and the answer is the repo-relative path of every file that must
change to fix it. Reports mix both routes — a traceback frame, error string,
failing API name or config key is a literal the code also spells, so route
those at the lexical tools first; a report that only describes wrong
behavior takes the semantic route. From the first located site walk outward
with get_references before answering: a fix often spans the caller that
passes the bad value or the sibling sharing the broken helper, and stopping
at one plausible file is this task's most common failure. Read every
candidate and confirm the reported behavior is really produced there;
a file named on resemblance alone is the other failure. Answer with the
paths themselves, spelled exactly as the repository spells them, one line
each, and leave out files you could not confirm.
=== HARNESS_TASK_HEAD: ask_your_docs.repo_qa ===
Follow-up calls on a result are offers: take only those the question
needs. Issue the calls on a "Together:" line in one turn, since they are
independent; leave a "Then:" line for a later turn, since it needs earlier
results. Never read again what a result already showed: the read returns
the same lines. When you need a symbol's code, make one get_symbol
depth="source" call or one windowed read_file call; ask for the symbol card
only when the question needs its member list, since the card adds no code.
Give read_file an offset and limit around the cited lines, and widen only
to follow a reference you need. Call get_overview only after a search
misses or for a question about the project's structure, never beside the
first search: the question routes the first query. When a result showed a
signature or usage, end with a short Example built from it; otherwise leave
it out. Effort: the first turn locates, the second reads the cited spans
not yet rendered in one batch, the third answers; any call past that names
the claim it supports. When part of the answer is unconfirmed, end with one
line that starts with "Not confirmed:" and names it.
=== HARNESS_TASK_HEAD: ask_your_docs.vuln ===
Follow-up calls on a result are offers: take only those the question
needs. Issue the calls on a "Together:" line in one turn, since they are
independent; leave a "Then:" line for a later turn, since it needs earlier
results. Never read again what a result already showed: the read returns
the same lines. When you need a symbol's code, make one get_symbol
depth="source" call or one windowed read_file call; ask for the symbol card
only when the question needs its member list, since the card adds no code.
Give read_file an offset and limit around the cited lines, and widen only
to follow a reference you need. Call get_overview only after a search
misses or for a question about the project's structure, never beside the
first search: the question routes the first query. When a result showed a
signature or usage, end with a short Example built from it; otherwise leave
it out. Effort: the first turn locates, the second reads the cited spans
not yet rendered in one batch, the third answers; any call past that names
the claim it supports. When part of the answer is unconfirmed, end with one
line that starts with "Not confirmed:" and names it.
=== HARNESS_TASK_HEAD: ask_your_docs.bug_loc ===
Follow-up calls on a result are offers: take only those the question
needs. Issue the calls on a "Together:" line in one turn, since they are
independent; leave a "Then:" line for a later turn, since it needs earlier
results. Never read again what a result already showed: the read returns
the same lines. When you need a symbol's code, make one get_symbol
depth="source" call or one windowed read_file call; ask for the symbol card
only when the question needs its member list, since the card adds no code.
Give read_file an offset and limit around the cited lines, and widen only
to follow a reference you need. Call get_overview only after a search
misses or for a question about the project's structure, never beside the
first search: route the first query from the report's own literals. When a
result showed a signature or usage, end with a short Example built from it;
otherwise leave it out. Effort: the first turn locates, the second reads
the cited spans not yet rendered in one batch, the third answers; any call
past that names the claim it supports. When part of the answer is
unconfirmed, end with one line that starts with "Not confirmed:" and names
it.
=== HARNESS_TASK_HEAD: external.repo_qa ===
No catalog is pre-injected: orient first — get_overview on an unfamiliar
project — then route per the backbone policy. Answers must be
self-contained: name the file path and the line numbers you read.
=== HARNESS_TASK_HEAD: external.vuln ===
Orient first — no catalog is pre-injected. With only literal tools
bound, grep confirms a located site rather than finding one: translate
the behavior into plausible identifiers per the backbone policy.
=== HARNESS_TASK_HEAD: external.bug_loc ===
Orient first — no catalog is pre-injected: get_overview on an unfamiliar
project, then map the layout with glob before grepping the report's
literals. Answers must be self-contained: give every path in full, from
the repository root.
