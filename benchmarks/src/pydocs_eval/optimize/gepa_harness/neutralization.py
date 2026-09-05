"""The two GEPA money seams — campaign ledger as sole spend/stop authority (ADR 0017 §2).

gepa 0.1.4 counts spend as a rollout-COUNT gate and spends real money in exactly
one place (the reflection LM). Both are neutralized here without forking gepa:

1. **Budget (stop authority).** gepa's ``MaxMetricCallsStopper`` fires on
   ``state.total_num_evals`` (counted from the adapter's ``num_metric_calls``
   return). The adapter returns ``num_metric_calls=0``, so gepa's internal count
   never trips; instead :class:`BudgetGuardStopper` — a ``StopperProtocol``
   callable — reads the campaign :class:`~pydocs_eval.campaign.budget.BudgetGuard`
   against the ledger's accumulated spend, making the Phase 3 ledger the ONLY
   thing that can stop the loop.

2. **Reflection spend.** gepa's ``LanguageModel`` Protocol accepts any
   ``(str | list[dict]) -> str`` callable, so :class:`LedgerDebitingReflectionLM`
   is our own callable that DEBITS a reflection-spend ledger BEFORE forwarding to
   an injected inner LM — the one place GEPA spends money itself routes through
   our accounting, and the debit lands even if the inner call then raises.

Neither class imports ``gepa`` — they satisfy its Protocols by shape, so the
seams are constructible and testable offline with fakes (no real LLM, no
installed gepa).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pydocs_eval.campaign.budget import BudgetGuard
from pydocs_eval.campaign.ledger import CampaignLedger

# gepa's LanguageModel Protocol: a ``(str | list[dict]) -> str`` callable. The
# reflection prompt is either a plain string or a chat-message list; the return
# is the reflector's proposed text.
ReflectionPrompt = str | list
InnerReflectionLM = Callable[[ReflectionPrompt], str]


@dataclass(frozen=True, slots=True)
class BudgetGuardStopper:
    """gepa ``StopperProtocol`` impl — the campaign ledger is the sole stop authority.

    gepa invokes a stopper as a callable that returns ``True`` to stop; whatever
    engine state gepa passes is IGNORED (accepted as ``*args``/``**kwargs``)
    because the authority is the campaign ledger, never gepa's internal eval
    count. Stops the loop once the :class:`BudgetGuard` trips on the ledger's
    accumulated spend — the same ``>=`` ceiling the runner enforces per rollout.
    """

    guard: BudgetGuard
    ledger: CampaignLedger

    def should_stop(self) -> bool:
        """``True`` once the campaign ledger's spend has reached the guard ceiling."""
        return self.guard.is_exhausted(self.ledger.total_spend())

    def __call__(self, *_args: object, **_kwargs: object) -> bool:
        """gepa ``StopperProtocol`` entry — delegates to :meth:`should_stop`.

        Example:
            >>> from pydocs_eval.campaign.budget import BudgetGuard
            >>> from pydocs_eval.campaign.ledger import CampaignLedger
            >>> import tempfile, pathlib
            >>> d = pathlib.Path(tempfile.mkdtemp())
            >>> guard = BudgetGuard(cost_ceiling_usd=10.0, assumed_cost_on_raise=1.0)
            >>> BudgetGuardStopper(guard, CampaignLedger(d / "q.jsonl"))()
            False
        """
        return self.should_stop()


@dataclass(slots=True)
class ReflectionSpendLedger:
    """A minimal reflection-spend accumulator — the reflection-LM's spend authority.

    Kept separate from the campaign (rollout) ledger because reflection is not a
    ``(cell, instance)`` rollout (ADR 0019: a minor, separate cost head). Debits
    accrue monotonically; a negative debit is a construction bug and is rejected
    with the offending value.
    """

    spent_usd: float = field(default=0.0, init=False)

    def debit(self, amount_usd: float) -> float:
        """Add ``amount_usd`` to the accrued reflection spend; return the new total.

        Raises:
            ValueError: if ``amount_usd`` is negative — a debit credits nothing.
        """
        if amount_usd < 0:
            raise ValueError(
                f"reflection debit must be >= 0, got {amount_usd!r}; "
                "a debit accrues spend and can never be negative"
            )
        self.spent_usd += amount_usd
        return self.spent_usd


@dataclass(frozen=True, slots=True)
class LedgerDebitingReflectionLM:
    """gepa ``reflection_lm`` wrapper — debits the spend ledger BEFORE forwarding.

    The one place GEPA spends money itself. Every call debits ``cost_per_call``
    to :class:`ReflectionSpendLedger` FIRST, then forwards the prompt to the
    injected ``inner`` LM (a real client in the paid arc, a fake in tests). The
    debit lands before the forward so a spend is booked even if ``inner`` raises
    — a failed reflection call still consumed budget.
    """

    inner: InnerReflectionLM
    ledger: ReflectionSpendLedger
    cost_per_call: float

    def __call__(self, prompt: ReflectionPrompt) -> str:
        """Debit the ledger, then forward ``prompt`` to the inner reflection LM."""
        self.ledger.debit(self.cost_per_call)
        return self.inner(prompt)
