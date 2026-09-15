# pydocs-mcp

A local documentation and code index served to AI coding assistants through nine task-shaped tools. This glossary fixes the words used when describing what those tools return and how one call leads to the next.

## Language

### Tool responses

**Pointer**:
A ready-made follow-up call rendered in a response, naming the tool and the arguments for the next step.
_Avoid_: hint, next step, link

**Pointer bundle**:
The set of pointers one response offers, split into calls that are independent of each other ("together") and calls that need a prior result first ("then").
_Avoid_: next steps, suggestions

**Recovery pointer**:
A pointer that retrieves the content a response elided under a cap.
_Avoid_: truncation hint

**Suggestion**:
A rule-triggered, structured hint attached to a response when a deterministic condition fires, such as an empty result or a capped listing. Distinct from a pointer, which lives in the text.
_Avoid_: pointer, tip

**Batch call**:
One call carrying several targets in place of several single-target calls.
_Avoid_: fan-out, multi-call

**Pointer table**:
The single table that says which pointers each kind of response offers, and in which group.
_Avoid_: next-step map, hint table

### Tool calls

**Needed call**:
A tool call whose result adds evidence the answer uses that was not already in context.
_Avoid_: useful call, good call

**Needless call**:
A tool call that resurfaces seen content, yields nothing usable, fans out where one batch call would do, or uses a tool that does not fit the shape of its input.
_Avoid_: wasted call, redundant call

### Repetition

**Resurfacing**:
Rendering a span that an earlier response in the same session already rendered.
_Avoid_: duplicate result, re-read

**Self-pointing**:
A pointer whose target is content the same response already rendered.
_Avoid_: circular pointer

**Cross-tool overlap**:
Two tools in one turn rendering the same content at the same depth.
_Avoid_: redundancy

### Symbol views

**Symbol card**:
The default, always-small view of a symbol: its signature, its first doc line and the names of its immediate children. It is what the summary depth returns.
_Avoid_: summary card, node summary, stub

**Outline**:
The node-only view of a symbol's document tree: title, kind, qualified name and line span for each node, with no source text. It is what the tree depth returns.
_Avoid_: page index, tree dump, node representation

**Level cut**:
Reducing an outline by whole levels of nesting until it fits its budget.
_Avoid_: depth limit, pruning

### Branches in the index

**Indexed branch**:
A git branch whose file manifest and chunk membership are stamped into a bundle; one per bundle today, several once multi-branch indexing lands.
_Avoid_: ref, checkout (for the index row)

**Base branch**:
The branch a diff is computed against: the repository's main branch, auto-detected and overridable in YAML, never chosen per request.
_Avoid_: trunk, target branch, main (as a generic word)

**Base tip**:
The base branch's current commit, the remote-tracking one when it exists.
_Avoid_: origin/main (as a concept)

**Merge-base pair**:
The pair (merge-base commit, branch head) that identifies the diff a branch's slices were generated from.
_Avoid_: diff key, anchor

**Changed slice**:
The symbols in the files a branch changed against its merge-base; what `scope=changed` searches.
_Avoid_: changed files (as the slice's name), delta

**Diff slice**:
The hunks of a branch's diff against its merge-base, indexed as chunks and searched only on request through `scope=diff`.
_Avoid_: hunks (alone), patch

**Landing unit**:
One first-parent step of the base branch that landed a branch (a merge, a squash or a single commit), addressable by its commit sha, keeping the branch's diff after the branch itself is gone.
_Avoid_: merge commit, squash (alone), PR

**Landing sha**:
The commit sha that names a landing unit; what the branch selector accepts for a merged branch.
_Avoid_: merged_into (the column), merge sha

**Retention window**:
The set of landing units whose diff slice is kept: the landings since the last release tags, with a floor and a cap.
_Avoid_: history, diff cache

**Non-git sentinel**:
The single placeholder branch row stamped for a project indexed outside any git repository; it is never shown as a branch.
_Avoid_: "no git" (on screen), detached

### Asking with a scope

**Search target**:
One (project, branch) pair the next question searches, chosen in the "Searching in" strip; several targets run as separate searches with one labeled answer block each.
_Avoid_: pin, cell (on screen), scope

**Only these**:
The choice that keeps every search inside the selected targets, even when a question names another project; off, the agent may look elsewhere.
_Avoid_: strict, hard pin, lock

**Typed token**:
An `in:<project>` or `on:<branch>` word inside a question that searches there for that one question only, checked against the indexed names.
_Avoid_: mention, tag, prefix

**One-shot scope**:
A scope that applies to a single question (a typed token, a follow-up button) and leaves the strip's targets unchanged.
_Avoid_: temporary pin, override
