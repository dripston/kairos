"""Consent and STOP: opt-out is global, immediate, and only undone per channel (K01 holds.STOP_OPT_OUT)."""
from __future__ import annotations

from ..core import Checks, attack
from ..world import CHANNELS, World, at
from ._helpers import dr, must


@attack("RT-CON-01", category="consent", component="k06.ledger",
        title="STOP on WhatsApp blocks every channel in the same cycle and cancels queued sends (E02)",
        covers=("E02", "K01-T19", "K01-T30"))
def stop_one_channel_blocks_all(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    q1 = w.ledger.reserve(w.ins(ch="sms", t=at(10)))
    q2 = w.ledger.reserve(w.ins(ch="email", t=at(12), cost=5))
    must(q1.reserved and q2.reserved, "queue two touches")
    n = w.ledger.record_stop("B1", at(9, 30), channel="whatsapp")
    chk.equal("queued touches canceled by STOP", n, 2)
    chk.equal("budget released", w.ledger.campaign_state("C1")["committed_paise"], 0)
    for ch in CHANNELS:
        chk.equal(f"{ch} decision right after STOP", list(dr(w.ledger.reserve(w.ins(ch=ch, t=at(14), cost=5)))),
                  ["CANCEL", "STOP_OPT_OUT"])
    ex = w.executor()
    for r in (q1, q2):
        ex.dispatch(r.instruction_id, at(12))
    chk.equal("provider sends after STOP", sum(len(p.sent) for p in ex.providers.values()), 0)
    return chk.result()


@attack("RT-CON-02", category="consent", component="k06.executor",
        title="STOP arriving between reserve and dispatch -> nothing is sent",
        covers=("E02", "K06:send-time-recheck"))
def stop_between_reserve_and_dispatch(ctx):
    """Two paths: (a) STOP through the ledger (cancels the queue); (b) an opt-out written straight into
    the K03 consents table by another feed, which only the send-time re-check can catch."""
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    ex = w.executor()
    a = w.ledger.reserve(w.ins(ch="sms", t=at(10)))
    must(a.reserved, "reserve sms")
    w.ledger.record_stop("B1", at(9, 59), channel="whatsapp")
    out = ex.dispatch(a.instruction_id, at(10))
    chk.expect("(a) dispatch after STOP is not SENT", out.outcome in ("SKIPPED", "BLOCKED"), got=out.outcome,
               reason=out.reason)
    chk.equal("(a) due touches after STOP", w.ledger.due_touches(at(18)), [])

    w.borrower("B2")
    c = w.ledger.reserve(w.ins("B2", ch="email", t=at(15), cost=5))
    must(c.reserved, "reserve email")
    conn = w.ledger._spine()
    with conn:
        conn.execute("UPDATE consents SET opted_out=1 WHERE borrower_id='B2' AND channel='email'")
    out_c = ex.dispatch(c.instruction_id, at(15))
    chk.equal("(b) email opted out after reserve -> blocked at send time", [out_c.outcome, out_c.reason],
              ["BLOCKED", "STOP_OPT_OUT"])
    chk.equal("provider sends (sms + email)", len(ex.providers["sms"].sent) + len(ex.providers["email"].sent), 0)
    return chk.result()


@attack("RT-CON-03", category="consent", component="k06.ledger",
        title="Opt-in on SMS after STOP does not re-enable any other channel",
        covers=("K01:holds.STOP_OPT_OUT.re_entry",))
def opt_in_is_per_channel(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    w.ledger.record_stop("B1", at(9))
    w.ledger.opt_in("B1", "sms", "consent-v3", at(9, 30))
    chk.expect("SMS reserved after opt-in", w.ledger.reserve(w.ins(ch="sms", t=at(10))).reserved)
    for ch in ("whatsapp", "email", "ivr", "voicebot"):
        chk.equal(f"{ch} still blocked", list(dr(w.ledger.reserve(w.ins(ch=ch, t=at(11), cost=5)))),
                  ["CANCEL", "STOP_OPT_OUT"])
    rows = {r["channel"]: r["opted_out"] for r in w.ledger.borrower_state("B1")["consents"]}
    chk.equal("K03 consents opted_out per channel", rows, {"sms": 0, "whatsapp": 1, "email": 1, "ivr": 1, "voicebot": 1})
    return chk.result()


@attack("RT-CON-04", category="consent", component="k06.ledger",
        title="No consent row / consent=0 for a channel -> WAIT NO_CONSENT",
        covers=("K01-T20",))
def no_consent(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1", consents={"sms": 1, "whatsapp": 0})
    chk.equal("whatsapp consent=0", list(dr(w.ledger.reserve(w.ins(ch="whatsapp", cost=40)))), ["WAIT", "NO_CONSENT"])
    chk.equal("email no consent row", list(dr(w.ledger.reserve(w.ins(ch="email", cost=5)))), ["WAIT", "NO_CONSENT"])
    chk.expect("sms (consented) still reserved", w.ledger.reserve(w.ins(ch="sms")).reserved)
    return chk.result()
