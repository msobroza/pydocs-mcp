"""The cross-harness prompt pool — ONLY prompts plausibly shared across harnesses.

Assets, not code: this package exists so ``importlib.resources`` can address the
``.j2`` files beside it under wheel installs and zipimport. The loader that reads
them is machinery and lives at ``harness/platform/prompt_pool.py``.

Owner rule (2026-07-26): a template lives here only if a second harness or
task could plausibly reuse it — today the retriever-guidance surface the
optimizer seeds from (``system_v1``, ``rewrite_v1``). A single harness's
feature machinery (e.g. ask-your-docs' vision/reinspect templates) lives in
that harness's own ``prompts/freeze/`` pool instead — FROZEN, because
optimizable tasks consume it as an experimental control (byte-pinned per
harness via ``platform/prompt_freeze.py``). Resolution order per architecture
namespace: ``<harness>/prompts/<architecture>/`` →
``<harness>/prompts/freeze/`` → this pool (``platform/prompt_namespace.py``).

Versioning rule (retrieval/prompts precedent): never edit a shipped ``_vN``
in place — ship ``_vN+1``.
"""
