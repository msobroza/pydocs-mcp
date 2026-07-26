=== ADAPTER ===
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
=== HEAD: ask_your_docs.sweqapro ===
Repo-comprehension QA inside the ask-your-docs chat harness. The indexed
catalog is already in your prompt — do not call get_overview just to
discover what exists; route the first query straight from the question.
Match the answer to the question's probe: Where — name the repo-relative
file path(s) that hold the answer; What / How — the mechanism, via
get_context on the load-bearing symbols and get_symbol where exact
signatures matter; Why — the get_why lane before any speculation. Name the
exact file path for every claim and keep the answer concise.
=== HEAD: ask_your_docs.ccv ===
Security needle-search in the chat harness: the snapshot contains one
exploitable condition, and security questions phrased by impact usually
share no identifiers with the code — literal search on question words is a
dead start here; go semantic first. Begin with search_codebase over where
external input is parsed or received, and over operations whose misuse has
security consequences; then walk the flow with get_references — callees
forward from the entry point, callers backward from the risky operation —
until source and sink connect. The catalog is already in your prompt; skip
orientation calls. Report three things, each cited to file and symbol:
where untrusted input enters, the dangerous operation it reaches, and the
vulnerability class derived from the abuse path you actually traced —
never a guessed label. Skip the usual example-call snippet; the report is
the answer.
=== HEAD: external.sweqapro ===
Repo-comprehension QA from a generic MCP client: no catalog is pre-injected,
so orient first — get_overview on an unfamiliar project — then route per the
adapter policy. Answers must be self-contained: name the repo-relative file
path (and the line numbers you read) for every claim, and match the
question's probe — Where names locations, How explains the mechanism end to
end, Why goes through get_why before speculation.
=== HEAD: external.ccv ===
Security needle-search from a generic MCP client. Orient first — no catalog
is pre-injected — then trace source to sink. Security questions phrased by
impact usually share no identifiers with the code, so search by behavior —
semantic where available, otherwise grep for identifiers the behavior
implies rather than the question's own words — to find the entry point.
Walk get_references until untrusted input connects to the operation it
should never reach, reading the cited files along the way. Report, with
file citations: the entry point of untrusted input, the dangerous operation
it reaches, and the vulnerability class derived from that traced path.
