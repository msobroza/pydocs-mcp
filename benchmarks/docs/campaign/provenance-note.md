# Provenance-note pattern

**What a shipped description points back to, and by what mechanism.** If Phase 4
ever ships an optimized description as the product default, that default must be
traceable — one hash — to the exact candidate the optimizer produced, the evidence
that selected it, and the mutation lineage that made it. This note documents the
**mechanism** that makes such a pointer truthful. It is not yet an instance: the
optimization campaign is paid and owner-gated, so no candidate has been shipped.
The pattern exists now so that when one is, the provenance note is a hash lookup,
not an archaeology project.

The design authority is `docs/adr/0017` (candidate identity + injection) and
`docs/adr/0020` (final selection, freeze, immutable artifact).

## The one identity: the serve-truthful artifact hash

Every description surface in this system — packaged seed, injected candidate, or
shipped default — has exactly one identity: the **artifact hash**, a SHA-256 over
the renderer version plus the normalized description surface. The product computes
it two ways that agree by construction:

- `current_artifact_hash()` — over the *live* description attributes (instructions,
  tool view, session preamble) actually registered on the running server.
- `packaged_artifact_hash()` — over the packaged seed document.

(`python/pydocs_mcp/application/description_source.py`.) The optimizer's
`Candidate.candidate_hash` (`optimize/candidates/candidate.py`) re-implements the
**same payload format publicly** — `renderer:v{RENDERER_VERSION}` + the normalized
surface — so a candidate's hash equals what a real serve of that candidate would
stamp, byte for byte. A parity test pins that equality against the product's own
`apply_source` return value, so a payload-format change fails loudly rather than
silently splitting the two hash spaces.

**Consequence:** the candidate hash IS the serve hash IS the shipped-default hash.
There is one number, not three that must be reconciled.

## The chain — from a shipped default back to its evidence

```
shipped default document
  │  packaged_artifact_hash()  ==  candidate_hash
  ▼
candidate super-ledger entry           (optimize/candidates/ledger.py, candidates.jsonl)
  ├─ document_ref            → blobs/<sha256>   the exact source document
  ├─ lineage_parent          → parent candidate_hash (walk the mutation tree to the seed)
  ├─ mutation_record         → which section changed, which proposer/selector, model id
  ├─ reflector_input_refs    → blobs/<sha256>   the reflection inputs that drove the edit
  ├─ gate                    → the val GateDecision that screened it
  └─ campaign_ids            → the per-candidate campaign lockfiles (each its own campaign_id)
        │
        ▼
   campaign lockfile + queue.jsonl     (campaign/lockfile.py, campaign/ledger.py)
     └─ artifact_hash folded into campaign_id, per-rollout traces
          │  trace header: artifact_hash = current_artifact_hash()
          ▼
     every rollout self-identifies its candidate
```

Each arrow is a hash lookup, never a name match:

1. **Shipped default → candidate.** Compute `packaged_artifact_hash()` of the
   shipped document; it equals a `candidate_hash` in the super-ledger. That entry
   is the candidate.
2. **Candidate → mutation lineage.** Walk `lineage_parent` up the super-ledger to
   the seed (`lineage_parent = None`); each hop's `mutation_record` +
   `reflector_input_refs` say *what changed and why*. This is the qualitative
   diff the closing report narrates (ADR 0020).
3. **Candidate → selecting evidence.** The entry's `gate` is the val
   `GateDecision` that screened it; its `campaign_ids` name the per-candidate
   campaigns whose lockfiles + ledgers hold the paired rollouts. The frozen-test
   contrast that confirmed it (ADR 0020) is recorded against the same
   `candidate_hash`.
4. **Candidate → every rollout.** Each rollout's trace header stamps
   `artifact_hash = current_artifact_hash()` (`observability/trace_recorder.py`),
   so a trace pulled from any campaign self-identifies which candidate produced
   it — no external index required.

## The provenance note itself (the shipped-default artifact)

When the owner authorizes a shipped default (the third Phase 4 owner checkpoint),
the recommendation carries a **provenance note**: a short record whose single
load-bearing field is the winning `candidate_hash`. Everything else — the source
document, the lineage to the seed, the selecting gate decision, the frozen-test
result, the campaign IDs — is *derivable* from that hash by the chain above, so
the note stores the hash and references, not copies. Rules the note follows:

- **The hash is computed, not asserted.** The note's `candidate_hash` must equal
  `packaged_artifact_hash()` of the shipped document at ship time — re-derive it,
  do not transcribe it. A mismatch means the shipped bytes are not the candidate
  the evidence selected: a hard stop.
- **Every referenced artifact is content-addressed.** `document_ref` and
  `reflector_input_refs` point at `blobs/<sha256>`; the note never inlines a
  document body, so it cannot drift from the bytes it names.
- **It points at the frozen registration too.** The candidate was accepted under a
  specific pre-registration; the note carries `registration_hash()`
  (`optimize/prereg/config.py`) so the acceptance rule the number was earned under
  is itself pinned.
- **It is immutable.** Like the campaign lockfile, the provenance note is frozen
  once the artifact ships; a changed default is a new note with a new
  `candidate_hash`, never an edit.

## Why the mechanism, not an instance, ships now

The no-spend stage builds and dry-runs the whole chain — candidate rendering,
serve-truthful hashing, the super-ledger schema, the trace stamp — and pins the
hash equalities with parity tests. What it cannot produce is a *populated* note:
that needs a selected candidate, which needs the paid campaign and the owner
freeze authorization. So the standing deliverable is the **mechanism** (this note
+ the tested seams); the first populated provenance note is written at close-out,
against the winning `candidate_hash`, as part of the shipped-default recommendation
the owner decides on.
