# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root: the domain glossary. It does not exist yet; `/domain-modeling` (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates it lazily the first time a term is actually resolved.
- **`docs/adr/`**: read the ADRs that touch the area you're about to work in. Twenty-two exist (`0001` to `0022`); the next free number is `0023`.
- **`docs/tool-contracts.md`**: the frozen contract for the nine task-shaped MCP tools. It is normative in the same way an ADR is, and every proposal that touches the tool surface must be checked against it.

If `CONTEXT.md` doesn't exist, **proceed silently**. Don't flag its absence; don't suggest creating it upfront.

Until `CONTEXT.md` exists, the vocabulary lives in two places: the `StrEnum` vocabularies in `python/pydocs_mcp/models.py` (the single source of the domain vocabulary, per CLAUDE.md) and the "Project Overview" section of CLAUDE.md. When `CONTEXT.md` is created it holds the glossary only; CLAUDE.md keeps the rules. Do not duplicate one into the other.

## File structure

Single-context repo:

```
/
├── CLAUDE.md                          ← rules and conventions (not the glossary)
├── CONTEXT.md                         ← glossary, created lazily by /domain-modeling
├── docs/
│   ├── tool-contracts.md              ← normative: the frozen nine-tool surface
│   ├── adr/                           ← normative: NNNN-kebab-slug.md
│   └── superpowers/{specs,plans,research}/   ← historical tier: kept verbatim, filenames frozen
└── python/pydocs_mcp/
```

`docs/README.md` defines the two tiers: `tool-contracts.md` and `adr/` are binding on current code; `superpowers/*` is context written for the implementer at the time and loses to the code and the normative tier where they disagree.

The `benchmarks/` directory is a second distribution (`pydocs-mcp-eval`) with its own README and CHANGELOG, but it shares the product's vocabulary. Glossary entries for eval-suite terms go in the same root `CONTEXT.md`; switch to a `CONTEXT-MAP.md` only if the eval suite ever needs a glossary of its own.

## ADR conventions in this repo

- Filename `docs/adr/NNNN-kebab-slug.md`, four digits, sequential.
- First line `# ADR NNNN — Title`, then a `**Status:**` line (`Proposed` / `Accepted`, with ratification notes) and a `**Date:**` line.
- Sections, in order: `Context`, `Evidence`, `Options considered`, `Decision`, `Consequences`, `Action items`.
- ADRs are amended by newer ADRs, not edited. The one sanctioned in-place path is an owner-ratified contract-line amendment recorded in the ADR's status line (the ADR 0007 precedent).
- Adding a tool, a tool parameter, or an envelope field is a versioning event for every external client; it needs its own ADR and a `docs/tool-contracts.md` amendment, never a code-only change.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

All naming is plain English (owner rule, CLAUDE.md "Coding Rules for AI Agents"): glossary terms follow the same rule.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR and contract conflicts

If your output contradicts an existing ADR or `docs/tool-contracts.md`, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0003 (frozen nine-tool surface), but worth reopening because…_
