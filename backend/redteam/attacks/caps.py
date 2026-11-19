"""Caps: per-channel / all-channel caps, idempotency and cross-campaign races (K01 §caps, K06)."""
from __future__ import annotations

from datetime import timedelta

from ..core import Checks, attack
from ..world import D, World, at, with_lender, with_official_caps
from ._helpers import dr, must, race


@attack("RT-CAP-01", category="caps", component="k06.ledger",
        title="8 parallel workers race for the last B1 SMS cap slot -> exactly one wins",
        covers=("K01-T02", "K01-T03", "K06:parallel-caps"))
def last_slot_race(ctx):
    """B1 borrower already has 2 of 3 SMS reserved. 8 threads (two campaigns) released by a barrier
    each try a different instruction for the 3rd slot. Repeated in 3 fresh worlds."""
    chk = Checks()
    for rnd in range(3):
        w = World(ctx, f"race{rnd}")
        w.borrower("B1", enroll=("C1", "C2"))
        for h in (8, 10):
            must(w.ledger.reserve(w.ins(t=at(h), iid=f"PRE-{rnd}-{h}")).reserved, "pre-reserve")
        slots = [at(h) for h in (12, 14, 16, 18)] * 2
        won = race(len(slots), lambda i: w.ledger.reserve(
            w.ins(t=slots[i], iid=f"R{rnd}-W{i}", camp=("C1", "C2")[i % 2])).reserved, w.ledger)
        chk.equal(f"round {rnd}: workers that reserved", sum(won), 1)
        chk.equal(f"round {rnd}: live SMS touches", len(w.live_touches("B1")), 3)
        chk.equal(f"round {rnd}: independent cap_breaches() recount", w.ledger.cap_breaches(), [])
    return chk.result()


@attack("RT-CAP-02", category="caps", component="k06.executor",
        title="Replaying one instruction id (sequential + 10 threads) never reserves or sends twice",
        covers=("K06:idempotency", "K06:dispatch-once"))
def replay_same_instruction(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    first = w.ledger.reserve(w.ins(iid="DUP-1"))
    must(first.reserved, "first reserve")
    replays = race(10, lambda i: w.ledger.reserve(w.ins(iid="DUP-1")), w.ledger)
    replays += [w.ledger.reserve(w.ins(iid="DUP-1")) for _ in range(3)]
    chk.expect("every replay is flagged idempotent_replay", all(r.idempotent_replay for r in replays),
               flags=sorted({r.idempotent_replay for r in replays}))
    chk.equal("touch rows for DUP-1", len(w.touches("B1")), 1)
    chk.equal("budget committed once (paise)", w.ledger.campaign_state("C1")["committed_paise"], 20)
    ex = w.executor()
    outs = race(6, lambda i: ex.dispatch("DUP-1", at(10)).outcome, w.ledger)
    outs.append(ex.dispatch("DUP-1", at(10)).outcome)
    chk.equal("provider sends", len(ex.providers["sms"].sent), 1)
    chk.equal("dispatch outcomes", sorted(outs), ["SENT"] + ["SKIPPED"] * 6)
    return chk.result()


@attack("RT-CAP-03", category="caps", component="k06.ledger", finding="RT-F07",
        title="Reused instruction id with a different payload is not answered 'reserved'",
        covers=("K06:idempotency",))
def id_reuse_different_payload(ctx):
    """A buggy or hostile worker re-sends instruction id SHARED-1 for another borrower (and for an
    unknown campaign). The ledger must not tell it SEND/reserved=true for a touch that does not exist."""
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    w.borrower("B2")
    must(w.ledger.reserve(w.ins("B1", iid="SHARED-1")).reserved, "B1 reserve")
    other = w.ledger.reserve(w.ins("B2", t=at(12), iid="SHARED-1"))
    chk.expect("same id, other borrower -> not reported reserved", not other.reserved,
               got=list(dr(other)), reserved=other.reserved)
    try:
        bogus = w.ledger.reserve(w.ins("B1", camp="NO-SUCH-CAMPAIGN", iid="SHARED-1"))
        chk.expect("same id, unknown campaign -> rejected", not bogus.reserved, got=list(dr(bogus)),
                   reserved=bogus.reserved)
    except KeyError as e:
        chk.expect("same id, unknown campaign -> rejected", True, got=f"KeyError {e}")
    chk.equal("touch rows for B2", len(w.touches("B2")), 0)
    chk.equal("touch rows in total", len(w.touches()), 1)
    return chk.result()


@attack("RT-CAP-04", category="caps", component="k06.ledger",
        title="Two campaigns propose the same borrower slot at once -> one touch, one send (E03)",
        covers=("E03", "K01:caps.scope=per_borrower_across_all_campaigns"))
def cross_campaign_duplicate(ctx):
    chk = Checks()
    w = World(ctx)
    for i in range(5):  # explicit, different instruction ids from each campaign, raced
        bid = f"E{i}"
        w.borrower(bid, enroll=("C1", "C2"))
        won = race(2, lambda k: w.ledger.reserve(w.ins(bid, t=at(11), camp=("C1", "C2")[k],
                                                       iid=f"{bid}-C{k + 1}")).reserved, w.ledger)
        chk.equal(f"{bid}: reservations across C1+C2", sum(won), 1)
    w.borrower("S1", enroll=("C1", "C2"))  # no ids given: the stable id collapses both proposals
    a = w.ledger.reserve(w.ins("S1", t=at(11), camp="C1"))
    b = w.ledger.reserve(w.ins("S1", t=at(11), camp="C2"))
    ex = w.executor()
    ex.dispatch(a.instruction_id, at(11))
    ex.dispatch(b.instruction_id, at(11))
    chk.equal("S1: touch rows", len(w.touches("S1")), 1)
    chk.equal("S1: provider sends", len(ex.providers["sms"].sent), 1)
    w.borrower("X1", enroll=("C1", "C2"))
    for h in (8, 10, 12):
        must(w.ledger.reserve(w.ins("X1", t=at(h), camp="C1")).reserved, "C1 fills cap")
    chk.equal("X1: 4th SMS from C2 after C1 used the cap", list(dr(w.ledger.reserve(w.ins("X1", t=at(14), camp="C2")))),
              ["WAIT", "CHANNEL_CAP"])
    chk.equal("cap_breaches()", w.ledger.cap_breaches(), [])
    return chk.result()


@attack("RT-CAP-05", category="caps", component="k06.ledger",
        title="Official and lender caps tighter than policy win (per-channel, IVR, gap, all-channel, weekly)",
        covers=("K01-T26", "K01-T33", "K01-T34", "K01-T35", "K01-T36", "K01-T37"))
def tighter_caps_win(ctx):
    chk = Checks()
    off = World(ctx, "official", policy=with_official_caps(ctx.policy, {"per_channel_per_day_by_band": {"B1": 1}}))
    off.borrower("B1")
    must(off.ledger.reserve(off.ins(t=at(8))).reserved, "official first")
    r = off.ledger.reserve(off.ins(t=at(10)))
    chk.equal("official cap B1=1: 2nd SMS", list(dr(r)), ["WAIT", "CHANNEL_CAP"])
    chk.equal("official cap value used", r.decision.detail.get("cap"), 1)

    w = World(ctx, "lender")
    for bid in ("L1", "L2", "L3", "L4"):
        w.borrower(bid)
        w.ledger.set_lender(bid, "EXAMPLE")
    for h in (8, 11):
        must(w.ledger.reserve(w.ins("L1", t=at(h))).reserved, "lender sms")
    chk.equal("lender B1 SMS cap 2: 3rd SMS", list(dr(w.ledger.reserve(w.ins("L1", t=at(14))))), ["WAIT", "CHANNEL_CAP"])
    must(w.ledger.reserve(w.ins("L2", "whatsapp", t=at(8))).reserved, "lender wa")
    g = w.ledger.reserve(w.ins("L2", "whatsapp", t=at(10, 30)))
    chk.equal("lender gap 180 > policy 120: WhatsApp after 150 min", list(dr(g)), ["WAIT", "SAME_CHANNEL_GAP"])
    must(w.ledger.reserve(w.ins("L3", "ivr", t=at(9))).reserved, "lender ivr")
    chk.equal("lender IVR override 1/day: 2nd IVR", list(dr(w.ledger.reserve(w.ins("L3", "ivr", t=at(13))))),
              ["WAIT", "CHANNEL_CAP"])
    for ch in ("sms", "whatsapp", "email", "ivr"):
        must(w.ledger.reserve(w.ins("L4", ch, t=at(9))).reserved, f"L4 {ch}")
    chk.equal("lender all-channel B1=4/day: 5th contact (voicebot)",
              list(dr(w.ledger.reserve(w.ins("L4", "voicebot", t=at(9))))), ["WAIT", "ALL_CHANNEL_DAY_CAP"])
    w.borrower("L5")
    w.ledger.set_lender("L5", "EXAMPLE")
    days = [D + timedelta(days=k) for k in (0, 1, 3, 4)]  # Fri, Sat, Mon, Tue (Sunday is quiet)
    for day in days:
        w.ledger.mark_spine_synced(at(7, day=day))
        for h in (8, 11):
            must(w.ledger.reserve(w.ins("L5", t=at(h, day=day))).reserved, f"L5 {day} {h}")
    wed = D + timedelta(days=5)
    w.ledger.mark_spine_synced(at(7, day=wed))
    chk.equal("lender weekly B1 SMS cap 8 (rolling 7 days): 9th SMS on Wednesday",
              list(dr(w.ledger.reserve(w.ins("L5", t=at(10, day=wed))))), ["WAIT", "WEEKLY_CAP"])
    chk.equal("cap_breaches()", w.ledger.cap_breaches(), [])
    return chk.result()


@attack("RT-CAP-06", category="caps", component="k06.ledger",
        title="A lender file that tries to LOOSEN caps or the gap is ignored (strictest wins)",
        covers=("K01:effective_cap_rule",))
def lender_cannot_loosen(ctx):
    loose = {"lender_id": "LOOSE", "per_channel_per_day_by_band": {"B1": 10},
             "all_channels_per_day_by_band": {"PRE_DUE": 20}, "min_gap_minutes_same_channel": 30}
    chk = Checks()
    w = World(ctx, policy=with_lender(ctx.policy, "LOOSE", loose))
    w.borrower("B1")
    w.borrower("B2")
    w.borrower("P1", dpd=-3)
    for bid in ("B1", "B2", "P1"):
        w.ledger.set_lender(bid, "LOOSE")
    for h in (8, 10, 12):
        must(w.ledger.reserve(w.ins("B1", t=at(h))).reserved, "B1 sms")
    r = w.ledger.reserve(w.ins("B1", t=at(14)))
    chk.equal("lender B1=10 vs policy 3: 4th SMS", list(dr(r)), ["WAIT", "CHANNEL_CAP"])
    chk.equal("effective cap stays 3", r.decision.detail.get("cap"), 3)
    must(w.ledger.reserve(w.ins("B2", t=at(8))).reserved, "B2 sms")
    g = w.ledger.reserve(w.ins("B2", t=at(9)))
    chk.equal("lender gap 30 vs policy 120: SMS after 60 min", list(dr(g)), ["WAIT", "SAME_CHANNEL_GAP"])
    chk.equal("effective gap stays 120", g.decision.detail.get("gap_min"), 120)
    for ch in ("sms", "whatsapp", "email"):
        must(w.ledger.reserve(w.ins("P1", ch, t=at(9))).reserved, f"P1 {ch}")
    chk.equal("lender PRE_DUE 20 vs policy 3: 4th contact", list(dr(w.ledger.reserve(w.ins("P1", "ivr", t=at(9))))),
              ["WAIT", "PRE_DUE_TOTAL_CAP"])
    return chk.result()


@attack("RT-CAP-07", category="caps", component="k06.ledger",
        title="Pre-due all-channel cap: 3 contacts across channels, the 4th waits",
        covers=("K01-T10", "K01-T11"))
def pre_due_total_cap(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("P1", dpd=-3)
    for ch in ("sms", "whatsapp", "email"):
        chk.expect(f"{ch} reserved", w.ledger.reserve(w.ins("P1", ch, t=at(10))).reserved)
    for ch in ("ivr", "voicebot"):
        chk.equal(f"{ch} after 3 pre-due contacts", list(dr(w.ledger.reserve(w.ins("P1", ch, t=at(10))))),
                  ["WAIT", "PRE_DUE_TOTAL_CAP"])
    return chk.result()
