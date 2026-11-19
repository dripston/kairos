"""Uncertain sends: a provider timeout is UNCERTAIN, and nothing is re-sent without a status check (K06)."""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from kairos_backend.executor import ProviderResult

from ..core import Checks, attack
from ..world import CHANNELS, World, at
from ._helpers import dr, must


class TimeoutProvider:
    """Every send raises (network timeout); the provider's status API never knows either."""

    def __init__(self, name: str):
        self.name = name
        self.attempts = 0

    def send(self, touch: dict) -> ProviderResult:
        self.attempts += 1
        raise TimeoutError("provider did not answer in 10 s")

    def status(self, instruction_id: str, provider_ref: Optional[str]) -> str:
        return "UNKNOWN"


@attack("RT-UNC-01", category="uncertain", component="k06.executor",
        title="Timeout -> UNCERTAIN; re-dispatch and dispatch_due never re-send; resolve asks the provider",
        covers=("K06:check-uncertain-before-retry",))
def timeout_never_resent(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    ex = w.executor(script=lambda t: "UNKNOWN_BUT_DELIVERED")
    r = w.ledger.reserve(w.ins())
    must(r.reserved, "reserve")
    chk.equal("first dispatch", ex.dispatch(r.instruction_id, at(10)).outcome, "UNCERTAIN")
    chk.equal("dispatch again", ex.dispatch(r.instruction_id, at(10, 5)).outcome, "SKIPPED")
    chk.equal("dispatch_due picks it up", [d.instruction_id for d in ex.dispatch_due(at(18))], [])
    chk.equal("provider sends before resolve", len(ex.providers["sms"].sent), 1)
    chk.equal("resolve_uncertain", ex.resolve_uncertain(r.instruction_id), "DELIVERED")
    chk.equal("provider sends after resolve", len(ex.providers["sms"].sent), 1)

    w.borrower("B2")
    tp = {ch: TimeoutProvider(ch) for ch in CHANNELS}
    ex2 = w.executor(providers=tp)
    r2 = w.ledger.reserve(w.ins("B2"))
    chk.equal("raising provider -> UNCERTAIN (not FAILED)", ex2.dispatch(r2.instruction_id, at(10)).outcome, "UNCERTAIN")
    chk.equal("resolve with provider still unsure", ex2.resolve_uncertain(r2.instruction_id), "UNCERTAIN")
    ex2.dispatch(r2.instruction_id, at(12))
    ex2.dispatch_due(at(18))
    chk.equal("send attempts", tp["sms"].attempts, 1)
    return chk.result()


@attack("RT-UNC-02", category="uncertain", component="k06.executor",
        title="Lost send is FAILED only after the provider says NOT_FOUND; unresolved ones keep their cap slot",
        covers=("K06:check-uncertain-before-retry", "K01-T03"))
def lost_and_unknown(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    ex = w.executor(script=lambda t: "UNKNOWN_LOST")
    r = w.ledger.reserve(w.ins())
    must(r.reserved, "reserve")
    ex.dispatch(r.instruction_id, at(10))
    chk.equal("committed while UNCERTAIN", w.ledger.campaign_state("C1")["committed_paise"], 20)
    chk.equal("resolve (provider: NOT_FOUND)", ex.resolve_uncertain(r.instruction_id), "FAILED")
    chk.equal("budget released", w.ledger.campaign_state("C1")["committed_paise"], 0)
    chk.equal("no automatic resend", [len(ex.providers["sms"].sent), len(w.touches("B1"))], [0, 1])
    re = w.ledger.reserve(replace(w.ins(t=at(10)), version=2))
    chk.equal("re-plan needs a NEW instruction (v2) that passes every guardrail", list(dr(re)), ["SEND", "ELIGIBLE"])

    w.borrower("B2")
    tp = {ch: TimeoutProvider(ch) for ch in CHANNELS}
    ex2 = w.executor(providers=tp)
    for h in (8, 10, 12):
        rr = w.ledger.reserve(w.ins("B2", t=at(h)))
        must(rr.reserved, f"B2 {h}")
        ex2.dispatch(rr.instruction_id, at(h))
    chk.equal("3 UNCERTAIN SMS", [t["status"] for t in w.touches("B2")], ["UNCERTAIN"] * 3)
    chk.equal("4th SMS while 3 are UNCERTAIN", list(dr(w.ledger.reserve(w.ins("B2", t=at(14))))), ["WAIT", "CHANNEL_CAP"])
    return chk.result()
