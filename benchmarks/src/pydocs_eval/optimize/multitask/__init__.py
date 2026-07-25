"""Multi-task strategy arms for a combined (swe-qa-pro + crosscommitvuln) pool.

Two orthogonal axes, deliberately separate so an experiment can attribute a
result to one of them:

* :mod:`sampling` — WITHIN a run: how one batch is drawn (``uniform`` control,
  ``stratified``, ``oversample``);
* :mod:`plans` — ACROSS runs: how many runs happen and how they chain
  (``single`` control, ``per_dataset``, ``curriculum``).

They compose: an arm is a (plan, sampler) pair. Both axes ship the status quo as
an explicit arm, because a comparison whose baseline is not one of the arms
cannot tell you whether a change helped.
"""

from __future__ import annotations
