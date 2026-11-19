"""Contact window and quiet days, with IST conversion of UTC / foreign-offset inputs (K01 contact_window)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from kairos_backend.gate import Facts, evaluate

from ..core import Checks, attack
from ..world import HOLIDAY, SUNDAY, ApiWorld, World, at
from ._helpers import dr, must

UTC = timezone.utc


@attack("RT-TIME-01", category="time", component="k06.ledger",
        title="Window edges: 07:59 and 19:00 (end exclusive) wait; 08:00 and 18:59 send",
        covers=("K01-T12", "K01-T13", "K01-T14"))
def window_edges(ctx):
    chk = Checks()
    w = World(ctx)
    cases = [((0, 0), "WAIT"), ((7, 59), "WAIT"), ((8, 0), "SEND"), ((18, 59), "SEND"), ((19, 0), "WAIT"),
             ((23, 59), "WAIT")]
    for i, ((h, m), want) in enumerate(cases):
        bid = f"T{i}"
        w.borrower(bid)
        d = w.ledger.reserve(w.ins(bid, t=at(h, m))).decision
        chk.equal(f"{h:02d}:{m:02d} IST", [d.decision, d.reason],
                  [want, "ELIGIBLE" if want == "SEND" else "CONTACT_WINDOW"])
    return chk.result()


@attack("RT-TIME-02", category="time", component="k06.ledger",
        title="Sunday and a national holiday are quiet days; the next contact day is not",
        covers=("K01-T15", "K01-T16"))
def quiet_days(ctx):
    chk = Checks()
    w = World(ctx, "sunday")
    w.borrower("B1")
    w.ledger.start_day(SUNDAY)
    chk.equal("Sunday 10:00", list(dr(w.ledger.reserve(w.ins(t=at(10, day=SUNDAY))))), ["WAIT", "QUIET_DAY"])
    h = World(ctx, "holiday", start=HOLIDAY - timedelta(days=6), days=10)
    h.borrower("B1", as_of=HOLIDAY)
    h.ledger.start_day(HOLIDAY)
    chk.equal(f"{HOLIDAY} (Republic Day) 10:00", list(dr(h.ledger.reserve(h.ins(t=at(10, day=HOLIDAY))))),
              ["WAIT", "QUIET_DAY"])
    nxt = HOLIDAY + timedelta(days=1)
    h.ledger.start_day(nxt)
    chk.equal(f"{nxt} (next contact day) 10:00", list(dr(h.ledger.reserve(h.ins(t=at(10, day=nxt))))),
              ["SEND", "ELIGIBLE"])
    return chk.result()


@attack("RT-TIME-03", category="time", component="k06.ledger",
        title="UTC and foreign-offset timestamps are judged in IST (no DST, fixed +05:30)",
        covers=("K01:timezone=Asia/Kolkata", "K01-T12", "K01-T13", "K01-T15"))
def utc_inputs_converted(ctx):
    ny = timezone(timedelta(hours=-5))
    tokyo = timezone(timedelta(hours=9))
    cases = [
        ("02:29Z = 07:59 IST", datetime(2026, 11, 20, 2, 29, tzinfo=UTC), "WAIT", "CONTACT_WINDOW"),
        ("02:30Z = 08:00 IST", datetime(2026, 11, 20, 2, 30, tzinfo=UTC), "SEND", "ELIGIBLE"),
        ("13:29Z = 18:59 IST", datetime(2026, 11, 20, 13, 29, tzinfo=UTC), "SEND", "ELIGIBLE"),
        ("13:30Z = 19:00 IST", datetime(2026, 11, 20, 13, 30, tzinfo=UTC), "WAIT", "CONTACT_WINDOW"),
        ("08:30-05:00 = 19:00 IST", datetime(2026, 11, 20, 8, 30, tzinfo=ny), "WAIT", "CONTACT_WINDOW"),
        ("11:29+09:00 = 07:59 IST", datetime(2026, 11, 20, 11, 29, tzinfo=tokyo), "WAIT", "CONTACT_WINDOW"),
        ("Sat 20:00-05:00 = Sun 06:30 IST", datetime(2026, 11, 21, 20, 0, tzinfo=ny), "WAIT", "QUIET_DAY"),
        ("Sat 22:30Z = Sun 04:00 IST", datetime(2026, 11, 21, 22, 30, tzinfo=UTC), "WAIT", "QUIET_DAY"),
        ("Sun 21:00-05:00 = Mon 07:30 IST", datetime(2026, 11, 22, 21, 0, tzinfo=ny), "WAIT", "CONTACT_WINDOW"),
        ("Mon 02:30Z = Mon 08:00 IST", datetime(2026, 11, 23, 2, 30, tzinfo=UTC), "SEND", "ELIGIBLE"),
    ]
    chk = Checks()
    w = World(ctx)
    for i, (label, t, want, reason) in enumerate(cases):
        bid = f"U{i}"
        w.borrower(bid)
        w.ledger.mark_spine_synced(t - timedelta(hours=1))  # keep DPD fresh so the window rule is reached
        r = w.ledger.reserve(w.ins(bid, t=t))
        chk.equal(label, list(dr(r)), [want, reason])
        if r.reserved:
            tch = w.ledger.touch(r.instruction_id)
            local = t.astimezone(timezone(timedelta(hours=5, minutes=30)))
            chk.equal(f"{label}: stored day/time is IST", [tch["day"], tch["scheduled_at"]],
                      [local.date().isoformat(), local.isoformat(timespec="seconds")])
    return chk.result()


@attack("RT-TIME-04", category="time", component="k06.executor",
        title="A touch reserved at 18:00 but dispatched at 19:05 is blocked at send time",
        covers=("K01-T13", "K06:send-time-recheck"))
def late_dispatch_blocked(ctx):
    chk = Checks()
    w = World(ctx)
    w.borrower("B1")
    r = w.ledger.reserve(w.ins(t=at(18)))
    must(r.reserved, "reserve 18:00")
    ex = w.executor()
    out = ex.dispatch(r.instruction_id, at(19, 5))
    chk.equal("dispatch at 19:05", [out.outcome, out.reason], ["BLOCKED", "CONTACT_WINDOW"])
    chk.equal("provider sends", len(ex.providers["sms"].sent), 0)
    chk.equal("budget released", w.ledger.campaign_state("C1")["committed_paise"], 0)
    return chk.result()


@attack("RT-TIME-05", category="time", component="k06.gate", finding="RT-F04", requires=("fastapi",),
        title="Stateless Gate (gate.evaluate and POST /gate/evaluate) judges UTC input in IST",
        covers=("K01-T13", "K01:timezone=Asia/Kolkata"))
def stateless_gate_timezone(ctx):
    """14:00Z is 19:30 IST: outside the window. The Ledger converts; the stateless Gate must too."""
    chk = Checks()
    t = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
    d = evaluate(ctx.policy, Facts(at=t, channel="sms", dpd=12))
    chk.equal("gate.evaluate(14:00Z)", [d.decision, d.reason], ["WAIT", "CONTACT_WINDOW"])
    hawaii = datetime(2026, 11, 21, 15, 0, tzinfo=timezone(timedelta(hours=-10)))  # = Sun 06:30 IST
    d2 = evaluate(ctx.policy, Facts(at=hawaii, channel="sms", dpd=12))
    chk.equal("gate.evaluate(Sat 15:00-10:00 = Sun 06:30 IST)", [d2.decision, d2.reason], ["WAIT", "QUIET_DAY"])
    api = ApiWorld(ctx)
    r = api.call("POST", "/gate/evaluate", json={"at": t.isoformat(), "channel": "sms", "dpd": 12})
    chk.equal("POST /gate/evaluate 14:00+00:00", [r["status"], r["body"].get("decision") if isinstance(r["body"], dict) else None],
              [200, "WAIT"])
    return chk.result()
