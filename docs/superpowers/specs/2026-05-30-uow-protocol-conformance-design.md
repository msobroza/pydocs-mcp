# UoW Protocol Conformance — design

**Status:** proposed
**Branch:** `fix/uow-protocol-conformance` (off `main` @ `0ce8b61`)
**Author:** msobroza

## Problem

`main`'s CI `Typecheck` step is red:

```
python/pydocs_mcp/retrieval/factories.py:77: error: Argument "uow_factory"
to "BuildContext" has incompatible type "Callable[[], CompositeUnitOfWork]";
expected "Callable[[], UnitOfWork] | None"  [arg-type]
```

Reproduced on `0ce8b61` (`uv run mypy python/pydocs_mcp` → 1 error). Every
branch cut from current `main` inherits it, so it blocks unrelated PRs
(the benchmark eval PR and the RepoQA ast_match PR among them).

## Root cause

`BuildContext.uow_factory` correctly depends on the `UnitOfWork` Protocol
(Dependency Inversion). The fault is that `CompositeUnitOfWork` is not a
*static* `UnitOfWork`: it serves the contract by delegating to child UoWs.
mypy names exactly two residual conflicts (verified via a conformance
probe):

1. **`__aexit__` return type.** Protocol declares `-> bool`
   (`storage/protocols.py:231`) and `SqliteUnitOfWork` matches it
   (`storage/sqlite.py:196`), but `CompositeUnitOfWork.__aexit__` returns
   `-> None` (`storage/composite_uow.py:138`).
2. **Read-only vs settable.** The Protocol declares the seven repo members
   (`packages`, `chunks`, `module_members`, `trees`, `references`,
   `vectors`, `multi_vectors`) as *settable variables*
   (`protocols.py:214-228`), but `CompositeUnitOfWork` exposes them as
   read-only `@property` (`composite_uow.py:210-261`) →
   "expected settable variable, got read-only attribute".

The `@property` block + its comment (`composite_uow.py:202-209`) show the
team already started static conformance; it just didn't close because of
these two mismatches.

## Goal

Make `CompositeUnitOfWork` a mypy-verified `UnitOfWork`, let
`build_uow_factory` advertise the abstraction, and turn the CI `Typecheck`
step green — with **zero `cast`s** on the wiring path. (Design choice
"B+C" from the options discussion; rejected alternatives below.)

## Non-goals

- No runtime behavior change beyond `__aexit__` returning `False` instead
  of `None` (both falsy → exception-propagation semantics unchanged).
- No change to which backends are wired or how children are composed.
- Not introducing two-phase commit / true cross-backend atomicity.

## Decisions

### D1 — `UnitOfWork` repo members become read-only properties
In `storage/protocols.py`, declare the seven repo members as
`@property` (read-only) instead of bare settable annotations. Rationale:
consumers only *read* `uow.<repo>`; read-only is the honest contract.
This is a *relaxation* (covariant) — a writable attribute satisfies a
read-only requirement, so existing implementers (`SqliteUnitOfWork`,
`TurboQuantUnitOfWork`, `FastPlaidUnitOfWork`, test fakes) keep
conforming, and `CompositeUnitOfWork`'s read-only properties now conform
too.

### D2 — `CompositeUnitOfWork.__aexit__ -> bool`
Change the annotation to `-> bool` and `return False` (do not suppress
exceptions — preserves current behavior; `None` was already falsy).

### D3 — `build_uow_factory` returns the abstraction, cast-free
In `storage/factories.py`, change the return annotation from
`Callable[[], CompositeUnitOfWork]` to `Callable[[], UnitOfWork]`. After
D1+D2 no `cast` is needed (Composite genuinely conforms). The retrieval
composition root (`retrieval/factories.py:77`) is then type-correct with
no edit.

### D4 — static conformance guard
Add a test that pins the conformance so it cannot silently regress:
`_: type[UnitOfWork] = CompositeUnitOfWork`. The real gate is
`mypy python/pydocs_mcp` going green; this makes the intent explicit and
self-documenting.

### Rejected alternatives
- **A (cast at call site):** scatters unverifiable casts; leaves Composite
  non-conforming; mypy stops checking that spot (future regressions ship).
- **A+B (widen return + cast):** centralizes one cast but conformance
  stays unfixed; still a blind spot.
- **A+B+C:** redundant — once C makes conformance real, the cast is dead
  code that re-blinds the checker C just enabled. Strictly worse than B+C.

## Files to modify
- `python/pydocs_mcp/storage/protocols.py` — D1 (7 members → read-only properties).
- `python/pydocs_mcp/storage/composite_uow.py` — D2 (`__aexit__ -> bool`).
- `python/pydocs_mcp/storage/factories.py` — D3 (widen return; no cast).
- `tests/storage/test_uow_protocol_conformance.py` (new) — D4 + behavior tests.

## Acceptance criteria

- **AC1** — `uv run mypy python/pydocs_mcp` reports **0 errors** (was 1).
- **AC2** — A static conformance guard (`_: type[UnitOfWork] =
  CompositeUnitOfWork`) type-checks clean under mypy.
- **AC3** — `CompositeUnitOfWork.__aexit__` returns `False`, and an
  exception raised inside `async with composite_uow(...)` **still
  propagates** (not suppressed). Runtime test.
- **AC4** — `build_uow_factory`'s return annotation is
  `Callable[[], UnitOfWork]` and `storage/factories.py` contains **no new
  `cast`** for this path; `retrieval/factories.py:77` is unedited.
- **AC5** — `SqliteUnitOfWork`, `TurboQuantUnitOfWork`,
  `FastPlaidUnitOfWork`, and the test fakes still satisfy `UnitOfWork`
  (mypy clean + their existing tests green).
- **AC6** — No production consumer assigns `uow.<repo> = …` through a
  `UnitOfWork`-typed reference (grep gate; read-only Protocol would reject
  it). If a legitimate one exists, reconcile before merge.
- **AC7** — Full unit suite (`pytest -q`) and benchmark suite
  (`PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`) green; `ruff
  check` + `ruff format --check` clean.

## TDD sequence (red → green, mypy-gated)

1. Add `tests/storage/test_uow_protocol_conformance.py`:
   - conformance guard (AC2) — fails to type-check today.
   - `__aexit__` propagation test (AC3) — runtime.
2. Run `mypy` → confirm the existing 1 error (AC1 baseline / red).
3. Apply D2 (`__aexit__ -> bool`) → re-run mypy (still 1 error: the
   read-only conflict remains).
4. Apply D1 (read-only Protocol members) → re-run mypy (error clears).
5. Apply D3 (widen factory return, no cast) → `mypy` 0 errors (AC1 green).
6. Run full pytest + benchmark + ruff (AC5, AC7); grep for `uow.* =`
   assignments (AC6).

## Risks & mitigations
- **3-place coupling** (Protocol props + Composite props + `_DISPATCH_ATTRS`)
  → AC2 guard + the existing "add to this tuple" comment catch drift.
- **Wider mypy blast radius** (central Protocol edit) → may surface a
  second latent conformance bug; AC5 + a full `mypy` run confirm. Healthy
  if it does.
- **`__aexit__` semantics** → AC3 asserts exceptions still propagate.
- **Read-only rejects an assignment** → AC6 grep gate before merge.

## Out of scope
- The RepoQA ast_match scorer fix (separate PR #59).
- The benchmark eval scaffolding (separate PR #58).
- Cross-backend two-phase commit.
