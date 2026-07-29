"""Shipped concrete harnesses — each folder is one harness INSTANCE.

A harness here owns a delivery map, minted ``HARNESS_TASK_HEAD: <harness>.<task>``
sections and ``HarnessRunner`` conformance. Reusable machinery never lives here:
it belongs in ``harness/platform/``, and the optimizable assets in
``harness/assets/``. The dependency rule is one-way — ``builtin`` imports
``platform``, never the reverse.
"""
