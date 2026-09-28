"""The answer judge: code-first ``needle cited``, then the Jev judge and the LLM roles.

ADR 0025 orders the judging tiers: the deterministic check gates correctness
(``needle_citation``), the Jev judge is second and the escalation judge third.

- ``needle_citation`` — the first tier, stdlib only.
- ``jev_requests`` / ``jev_questions`` — what Jev is asked about an answer, per
  slice; ``jev_client`` sends it (``jev_wire`` is the wire format, ``jev_cache``
  the input-hash cache) and ``jev_scoring`` turns the answers into rows under
  the fitted ``thresholds``.
- ``openrouter_chat`` — the one chat-completion client the escalation judge, the
  alignment labellers and the reference writer share (``openrouter_batch`` for
  the ``:batch`` models, ``chat_wire`` its wire format); ``openrouter_http`` is
  the call every client makes.
- ``config`` / ``role_config`` / ``roles`` — the role-named configuration, the
  deployment's pins, and the family rule.

Import floor: nothing here imports ``pydocs_mcp``. The product is the thing a
before/after run measures, so the scorer reads the product's literals through
the eval's own mirrors (``trajectory.ask_outcome``), never the product itself.
Third-party imports stop at httpx and the eval's config stack (pydantic, pyyaml).
"""
