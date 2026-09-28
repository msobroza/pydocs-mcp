"""The answer judge: code-first ``needle cited`` before any model is asked.

ADR 0025 orders the judging tiers: the deterministic check here gates
correctness, the Jev judge is second and the escalation judge third. This
package holds the first tier and the configuration the later ones share.

Import floor: nothing here imports ``pydocs_mcp``. The product is the thing a
before/after run measures, so the scorer reads the product's literals through
the eval's own mirrors (``trajectory.ask_outcome``), never the product itself.
"""
