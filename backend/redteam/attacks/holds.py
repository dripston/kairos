"""K13 holds and human controls: routed exceptions, PTP, hardship, callback, kill switch."""
from __future__ import annotations

from datetime import timedelta

from kairos_backend.exceptions import ExceptionEvent

from ..core import Checks, attack
from ..world import CHANNELS, D, World, at
from ._helpers import dr, must


@attack("RT-HOLD-01", category="holds", component="k06.ledger",
        title="Abusive / legal threat / fraud claim -> supervisor hold on every channel until a human clears it",
        covers=("K13-T23", "K13-T24", "K13-T25"))
def routed_stop_events(ctx):
    chk = Checks()
    w = World(ctx)
    nxt = D + timedelta(days=1)
    for i, ev in enumerate(("ABUSIVE", "LEGAL_THREAT", "FRAUD_CLAIM")):
        bid = f"H{i}"
        w.borrower(bid)
        q = w.ledger.reserve(w.ins(bid, t=at(16)))
        must(q.reserved, "queue")
        out = w.ledger.apply_exception(bid, ExceptionEvent(ev, "B1"), at(10), f"{bid}-A-1", ("routed", ""))
        chk.equal(f"{ev}: hold + ticket", [out["hold"], bool(out["ticket_id"]), out["canceled"]],
                  ["SUPERVISOR_HOLD", True, 1])
        w.ledger.start_day(nxt)
        blocked = {ch: dr(w.ledger.reserve(w.ins(bid, ch, t=at(10, day=nxt), cost=5)))[1] for ch in CHANNELS}
        chk.expect(f"{ev}: next day every channel waits", set(blocked.values()) == {"SUPERVISOR_HOLD"}, got=blocked)
        w.ledger.clear_supervisor_hold(bid)
        chk.equal(f"{ev}: after human clear", list(dr(w.ledger.reserve(w.ins(bid, t=at(12, day=nxt))))), ["SEND", "ELIGIBLE"])
        w.ledger.start_day(D)
    return chk.result()


@attack("RT-HOLD-02", category="holds", component="k06.ledger",
        title="PTP, hardship and callback-pending holds block every channel the next day",
        covers=("K01-T22", "K01-T24", "K13-T07", "K13-T13", "K13-T17", "E07", "E08", "E09"))
def ptp_hardship_callback(ctx):
    chk = Checks()
    w = World(ctx)
    nxt = D + timedelta(days=1)
    cases = (("PTP", ExceptionEvent("PTP_OFFERED", "B1", date_offset_days=2), "PTP_HOLD"),
             ("HARDSHIP", ExceptionEvent("HARDSHIP", "B1"), "HARDSHIP_HOLD"),
             ("CALLBACK", ExceptionEvent("CALLBACK_REQUEST", "B1"), "CALLBACK_PENDING"))
    for bid, ev, _ in cases:
        w.borrower(bid)
        w.ledger.apply_exception(bid, ev, at(10), f"{bid}-A-1")
    w.ledger.start_day(nxt)
    for bid, ev, want in cases:
        got = {ch: dr(w.ledger.reserve(w.ins(bid, ch, t=at(10, day=nxt), cost=5)))[1] for ch in CHANNELS}
        chk.expect(f"{ev.event}: every channel -> {want}", set(got.values()) == {want}, got=got)
    return chk.result()


@attack("RT-CTL-01", category="holds", component="k06.ledger",
        title="Outbound kill switch and campaign pause cancel queued work and block new sends",
        covers=("K06:human-controls",))
def kill_switch(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    q = w.ledger.reserve(w.ins(t=at(10)))
    must(q.reserved, "queue")
    chk.equal("pause cancels queued", w.ledger.set_outbound_paused(True, reason="red-team"), 1)
    chk.equal("while paused", list(dr(w.ledger.reserve(w.ins(ch="email", t=at(11), cost=5)))), ["WAIT", "OUTBOUND_PAUSED"])
    ex = w.executor()
    chk.equal("dispatch of the canceled touch", ex.dispatch(q.instruction_id, at(10)).outcome, "SKIPPED")
    w.ledger.set_outbound_paused(False)
    w.ledger.set_campaign_status("C1", "PAUSED")
    chk.equal("campaign paused", list(dr(w.ledger.reserve(w.ins(ch="email", t=at(12), cost=5)))), ["WAIT", "CAMPAIGN_PAUSED"])
    w.ledger.set_campaign_status("C1", "RUNNING")
    chk.equal("after resume", list(dr(w.ledger.reserve(w.ins(ch="email", t=at(13), cost=5)))), ["SEND", "ELIGIBLE"])
    chk.equal("provider sends", sum(len(p.sent) for p in ex.providers.values()), 0)
    return chk.result()
