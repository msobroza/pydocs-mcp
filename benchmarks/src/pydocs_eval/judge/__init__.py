"""The answer judge: code-first ``needle cited``, then the Jev judge and the LLM roles.

ADR 0025 orders the judging tiers: the deterministic check gates correctness
(``needle_citation``), the Jev judge is second and the escalation judge third.

- ``needle_citation`` — the first tier.
- ``jev_requests`` / ``jev_questions`` — what Jev is asked about an answer, per
  slice; ``jev_client`` sends it (``jev_wire`` is the wire format and the
  ``JevJudge`` port, ``jev_cache`` the input-hash cache) and ``jev_scoring``
  turns the answers into rows under the fitted ``thresholds``.
  ``planted_injections`` reads the injections vendored in ``data/``.
- ``openrouter_chat`` — the one chat-completion client the escalation judge, the
  alignment labellers and the reference writer share (``openrouter_batch`` for
  the ``:batch`` models; ``chat_wire`` is its wire format and the
  ``ChatCompleter`` port). ``openrouter_http`` is the call every client makes,
  ``openrouter_body`` how a response body is read and quoted, ``model_ids`` how
  every id is read and checked, and ``judge_errors`` what a call can raise.
- ``config`` / ``role_config`` / ``roles`` — the role-named configuration, the
  deployment's pins, and the family rule.
- ``reference_writer`` — reference answers written once from ground truth, in
  rounds of batches: ``reference_sources`` is what the writer is shown,
  ``reference_prompt`` how it is asked, ``reference_checks`` the two code checks
  a kept answer passes, and ``reference_journal`` the batch ids a later run
  collects or deletes. The rows are stored by ``pydocs_eval.datasets.reference_answers``.

Import floor: nothing here imports ``pydocs_mcp``. The product is the thing a
before/after run measures, so the scorer reads the product's literals through
the eval's own mirrors (``trajectory.ask_outcome``), never the product itself.
A judge module's own third-party imports stop at httpx and the eval's config
stack (pydantic, pyyaml); the eval's ``trajectory`` package, which the
code-first check reads, brings its own.
"""
