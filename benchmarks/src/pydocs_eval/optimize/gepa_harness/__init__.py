"""Thin-adapter seams that wire gepa 0.1.4 to the Phase 2/3 machinery (ADR 0017).

gepa is an OPTIONAL, additive ``benchmarks/`` dependency (MIT, zero transitive
deps). Nothing in this package imports ``gepa`` at module load: the two
neutralization seams (``neutralization``) and the acceptance lock
(``acceptance``) are self-contained callables/functions that SATISFY gepa's
public Protocols by shape, so they are constructible and offline-testable
without gepa installed. The seams exist so that — by construction, not by
discipline — GEPA can neither run a rollout nor spend a dollar nor accept a
candidate outside our Phase 2/3 authorities:

- ``BudgetGuardStopper`` makes the campaign ``BudgetGuard``/ledger the SOLE stop
  authority (gepa's internal eval-count stopper is neutralized by the adapter
  returning ``num_metric_calls=0``);
- ``LedgerDebitingReflectionLM`` routes the one place GEPA spends money itself —
  the reflection LM — through a debited spend ledger;
- ``acceptance.decide_acceptance`` is the adapter's ONLY acceptance path and
  consumes nothing but the sanctioned ground-truth gate inputs — the paired
  per-instance ``GroundTruthOutcome`` sequences + the two
  ``trajectory.gate.GateDecision`` aggregates + the pre-registration config — over
  which it runs ADR 0018's paired one-sided exact McNemar test (the ADR 0017
  §Decision 8 gate blind-spot lock, amended).
"""

from __future__ import annotations
