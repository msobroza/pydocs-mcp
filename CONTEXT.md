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

### What a response shows

**Rendered rows**:
The rows of a response's structured result whose content the response text put in front of the model. A response may return more rows than its text renders, so a returned row is not a read row.
_Avoid_: shown items, visible items, displayed results

**Visible hit**:
A hit that is both relevant to the question and among the response's rendered rows.
_Avoid_: seen hit, surfaced hit, top hit

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
