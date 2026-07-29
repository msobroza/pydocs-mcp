"""The external CLI harness — the first COMPOSED harness (spec §2 amendment).

It owns the corpus, the trace, the guidance policy and the ``Trajectory``, and
HAS-A CLI agent adapter from ``harness/platform/engines/`` for the argv and the
transcript. No optional extra gates it: stdlib ``subprocess`` is the whole
runtime, so it ships in the product wheel and costs nothing until an arm runs
it.

``skills/`` is this harness's asset home. It starts almost empty on purpose:
the optimizable ``external.*`` skill sections are a CROSS-harness artifact and
stay in ``harness/assets/skills`` — see ``skills/freeze/README.md`` for the
governance rule that decides which assets may live here.
"""
