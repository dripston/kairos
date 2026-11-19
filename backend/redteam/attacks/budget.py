"""Budget: pool, daily pacing, reconcile-once, collection-target stop (K01 budget, collection_target)."""
from __future__ import annotations

from datetime import timedelta

from ..core import Checks, attack
from ..world import D, World, at
from ._helpers import dr, must

EXHAUSTED = ["WAIT", "BUDGET_EXHAUSTED"]


@attack("RT-BUD-01", category="budget", component="k06.ledger",
        title="Pool exhausted -> WAIT; a send that fits exactly is allowed; never over-committed",
        covers=("K01-T28", "K01:budget.reserve_before_send"))
def pool_exhausted(ctx):
    chk = Checks()
    w = World(ctx, campaigns=("CB",), days=0, budget_per_case_paise=50)  # one-day campaign, pool 50 paise
    w.borrower("B1", enroll=("CB",))
    must(w.ledger.reserve(w.ins(ch="whatsapp", camp="CB", cost=40)).reserved, "first 40 paise")
    chk.equal("40 paise more with 10 left", list(dr(w.ledger.reserve(w.ins(ch="sms", camp="CB", cost=40)))), EXHAUSTED)
    chk.equal("10 paise (exact fit)", list(dr(w.ledger.reserve(w.ins(ch="email", camp="CB", cost=10)))), ["SEND", "ELIGIBLE"])
    chk.equal("1 paise more", list(dr(w.ledger.reserve(w.ins(ch="ivr", camp="CB", cost=1)))), EXHAUSTED)
    camp = w.ledger.campaign_state("CB")
    chk.equal("committed == pool", [camp["committed_paise"], camp["pool_paise"]], [50, 50])
    return chk.result()


@attack("RT-BUD-02", category="budget", component="k06.ledger",
        title="Daily pacing caps today's spend even when the pool still has money",
        covers=("K01:budget.daily_pacing",))
def daily_pacing(ctx):
    chk = Checks()
    w = World(ctx, campaigns=("CP",), days=9, budget_per_case_paise=800)  # D..D+9: 8 contact days -> 100/day
    w.borrower("B1", enroll=("CP",))
    must(w.ledger.reserve(w.ins(ch="whatsapp", camp="CP", cost=60)).reserved, "60 of today's 100")
    r = w.ledger.reserve(w.ins(ch="sms", camp="CP", cost=60))
    b = r.decision.detail.get("budget", {})
    chk.equal("second 60 paise today", list(dr(r)), EXHAUSTED)
    chk.expect("pool still had enough", b.get("pool_left_paise", 0) >= 60, budget=b)
    chk.equal("40 paise (rest of today's allowance)", list(dr(w.ledger.reserve(w.ins(ch="email", camp="CP", cost=40)))),
              ["SEND", "ELIGIBLE"])
    nxt = D + timedelta(days=1)
    w.ledger.start_day(nxt)
    chk.equal("next day the unspent pool paces again", list(dr(w.ledger.reserve(
        w.ins(ch="sms", camp="CP", cost=60, t=at(10, day=nxt))))), ["SEND", "ELIGIBLE"])
    return chk.result()


@attack("RT-BUD-03", category="budget", component="k06.executor",
        title="Actual provider charge is reconciled exactly once (executor + manual retries)",
        covers=("K01:budget.reconcile_actual_charge", "K06:reconcile-once"))
def reconcile_once(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    r = w.ledger.reserve(w.ins(cost=20))
    must(r.reserved, "reserve")
    ex = w.executor(cost_paise=35)
    chk.equal("dispatch", ex.dispatch(r.instruction_id, at(10)).outcome, "SENT")
    chk.equal("committed after reconcile", w.ledger.campaign_state("C1")["committed_paise"], 35)
    chk.equal("manual reconcile again (same amount)", w.ledger.reconcile(r.instruction_id, 35), False)
    chk.equal("manual reconcile again (different amount)", w.ledger.reconcile(r.instruction_id, 999), False)
    chk.equal("committed unchanged", w.ledger.campaign_state("C1")["committed_paise"], 35)
    c = w.ledger.reserve(w.ins(ch="email", t=at(12), cost=5))
    w.ledger.record_stop("B1", at(11))
    chk.equal("reconcile a canceled touch", w.ledger.reconcile(c.instruction_id, 5), False)
    chk.equal("committed after cancel", w.ledger.campaign_state("C1")["committed_paise"], 35)
    return chk.result()


@attack("RT-BUD-04", category="budget", component="k06.ledger",
        title="Collection target reached -> campaign stops new sends",
        covers=("K01-T29", "K01:collection_target"))
def target_met_stops(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    w.ledger.upsert_campaign("C1", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=10),
                             target_amount_pct=40)
    chk.equal("before any payment", list(dr(w.ledger.reserve(w.ins(t=at(10))))), ["SEND", "ELIGIBLE"])
    w.ledger.record_payment_verified("B1-A-1", 250_000, at(11), "PAY-T", "UTR-T-1")  # 50% of overdue
    chk.equal("after 50% collected (target 40%)", list(dr(w.ledger.reserve(w.ins(ch="whatsapp", t=at(13), cost=40)))),
              ["WAIT", "CAMPAIGN_TARGET_MET"])
    return chk.result()
