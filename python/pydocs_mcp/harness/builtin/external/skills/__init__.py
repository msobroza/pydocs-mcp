"""This harness's own skill-asset home (empty by design — see ``freeze/README.md``).

The optimizable ``BACKBONE`` / ``TASK_HEAD:`` / ``HARNESS_TASK_HEAD: external.*``
sections are a CROSS-harness artifact: one document, several harnesses' slices,
trained together. They therefore live in ``harness/assets/skills`` and are NOT
duplicated here. What belongs here is the opposite kind of asset: text this
harness consumes that is deliberately NOT an optimization target — a frozen
experimental control. ``freeze/`` is its sanctioned home and states the rule.
"""
