"""K13 holds and K01 consent conformance: borrower_state / consents only move along declared transitions.

One borrower per run, a seeded random stream of real ledger calls over several days: K13 exceptions
(PTP, hardship, callback request / due, routed stops, needs-time, dispute), verified payments (full /
partial, promised EMI or the other one), day roll-overs, supervisor clears, STOP and per-channel
opt-ins. Five machines are stepped side by side with facts the harness reads BEFORE each call; after
the call every machine's state must equal what the database shows (and a BROKEN_PTP must be counted
exactly when the machine says PTP_BROKEN).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from kairos_backend.exceptions import ExceptionEvent, handle
from kairos_backend.machines import CALLBACK_HOLD, CONSENT, HARDSHIP_HOLD, PTP_HOLD, SUPERVISOR_HOLD
from tests.fsm.world import CHANNELS, D, Coverage, add_borrower, at, new_ledger, random_consents

SEEDS = range(30)
B = "H1"
ROUTED_STOP = ("ABUSIVE", "LEGAL_THREAT", "FRAUD_CLAIM")


class Harness:
    def __init__(self, policy, path, rng):
        self.rng = rng
        self.lg = lg = new_ledger(policy, path, end_days=30)
        consents = random_consents(rng)
        if rng.random() < 0.5:  # sometimes no consent row at all for one channel
            consents.pop(rng.choice(CHANNELS))
        add_borrower(lg, B, dpd=rng.choice([3, 12, 45]), consents=consents)
        self.day = D
        self.now = at(9)
        self.k = 0
        self.thr = policy.exceptions["promise_to_pay"]["broken_ptp"]["threshold"]
        self.within = policy.raw["holds"]["HARDSHIP"]["borrower_date_within_days"]
        self.cov = {m.name: Coverage() for m in (PTP_HOLD, HARDSHIP_HOLD, CALLBACK_HOLD, SUPERVISOR_HOLD, CONSENT)}
        st = self.db()
        self.s = {"ptp": "NO_PTP", "hardship": "NONE", "callback": "NONE", "supervisor": "NONE",
                  **{f"consent:{ch}": st["consent"][ch] for ch in CHANNELS}}

    # ----- observation -----
    def db(self) -> dict:
        c = self.lg._conn()
        st = self.lg._state(c, B)
        rows = {r["channel"]: dict(r) for r in c.execute("SELECT * FROM consents WHERE borrower_id=?", (B,))}
        consent = {}
        for ch in CHANNELS:
            r = rows.get(ch)
            consent[ch] = ("OPTED_OUT" if st["stopped"] or (r and r["opted_out"]) else
                           "CONTACTABLE" if r and r["consented"] else "NOT_CONSENTED")
        return {**st, "consent": consent, "rows": rows}

    def hardship_view(self, st, t: datetime) -> str:
        until = self.lg._dt(st["hardship_until"])
        if until is None or until <= self.lg.policy.local(t):
            return "NONE"
        return "HARDSHIP_HANDOFF" if st["hardship_supervisor"] else "HARDSHIP_HOLD"

    def step(self, key, machine, ev, ctx=None):
        new, t = machine.step(self.s[key], ev, ctx or {})
        self.cov[machine.name].add(t)
        self.s[key] = new
        return t

    def clock(self):
        st = self.db()
        until = self.lg._dt(st["hardship_until"])
        self.step("hardship", HARDSHIP_HOLD, "CLOCK",
                  {"until_passed": until is None or until <= self.lg.policy.local(self.now)})

    def compare(self, what, broken_before=None, fired_ptp=None):
        self.clock()
        st = self.db()
        seen = {"ptp": "PTP_HOLD" if st["ptp_date"] else self.s["ptp"] if self.s["ptp"] != "PTP_HOLD" else "<cleared>",
                "hardship": self.hardship_view(st, self.now),
                "callback": "CALLBACK_PENDING" if st["callback_pending"] else "NONE",
                "supervisor": "SUPERVISOR_HOLD" if st["supervisor_hold"] else "NONE",
                **{f"consent:{ch}": st["consent"][ch] for ch in CHANNELS}}
        assert seen == self.s, f"{what}: db {seen} != machines {self.s}"
        if broken_before is not None:
            broke = st["broken_ptp"] - broken_before
            assert broke == int(fired_ptp is not None and fired_ptp.src == "PTP_HOLD" and fired_ptp.dst == "PTP_BROKEN"), what

    def tick(self):
        self.now += timedelta(minutes=self.rng.choice([5, 40, 90]))
        if self.now.hour >= 19:
            self.op_new_day()

    # ----- operations -----
    def op_exception(self):
        self.tick()
        st = self.db()
        name = self.rng.choice(["PTP_OFFERED", "PTP_OFFERED", "HARDSHIP", "HARDSHIP", "CALLBACK_REQUEST",
                                "CALLBACK_DUE", "CALLBACK_DUE", "NEEDS_TIME", "DISPUTE"] + list(ROUTED_STOP))
        off = self.rng.choice([None, 0, 1, 2, 3, 5, 9]) if name in ("PTP_OFFERED", "HARDSHIP") else None
        ev = ExceptionEvent(name, "B1", in_window=True, date_offset_days=off, broken_ptp_count=st["broken_ptp"],
                            callback_attempts=self.rng.randrange(3), callback_answered=self.rng.random() < 0.4)
        out = handle(self.lg.policy, ev)
        self.clock()
        ob = self.rng.choice([f"{B}-A-1", f"{B}-A-1", f"{B}-B-1", None]) if name == "PTP_OFFERED" else f"{B}-A-1"
        refused = False
        if name == "PTP_OFFERED":
            has_emi = ob is not None or bool(self.unpaid())
            t = self.step("ptp", PTP_HOLD, "PTP_OFFERED", {"broken_threshold": st["broken_ptp"] >= self.thr,
                                                           "has_emi": has_emi})
            assert (out.hold == "PTP_HOLD") == (st["broken_ptp"] < self.thr)
            refused = t.guard == "!broken_threshold & !has_emi"
        elif name == "HARDSHIP":
            self.step("hardship", HARDSHIP_HOLD, "HARDSHIP", {"date_beyond": off is not None and off > self.within})
        elif name == "CALLBACK_REQUEST":
            self.step("callback", CALLBACK_HOLD, "CALLBACK_REQUEST")
        elif name == "CALLBACK_DUE":
            self.step("callback", CALLBACK_HOLD, "CALLBACK_DUE",
                      {"answered": ev.callback_answered, "retry_left": ev.callback_attempts < 2})
        elif name in ROUTED_STOP:
            self.step("supervisor", SUPERVISOR_HOLD, "ROUTED_STOP")
        if refused:
            with pytest.raises(ValueError):
                self.lg.apply_exception(B, ev, self.now, ob)
        else:
            self.lg.apply_exception(B, ev, self.now, ob)
        self.compare(f"K13 {name} off={off} ob={ob} -> {out.action}/{out.hold}")

    def unpaid(self) -> list:
        return [r[0] for r in self.lg._conn().execute(
            "SELECT o.obligation_id FROM obligations o JOIN loans l ON l.loan_id=o.loan_id WHERE l.borrower_id=?"
            " AND o.amount_paid_paise < o.amount_due_paise", (B,))]

    def promise_kept(self, st) -> bool:
        """The harness's own reading of K01 holds.PROMISE_TO_PAY + FSM-F6: promised EMI fully paid, or verified
        payments (not reversed) on any of the borrower's EMIs from capture through the PTP date >= the amount
        outstanding on the promised EMI at capture."""
        ob = self.lg._obligation(self.lg._conn(), st["ptp_obligation"]) if st["ptp_obligation"] else None
        if ob is not None and ob["amount_paid_paise"] >= ob["amount_due_paise"]:
            return True
        if not st["ptp_captured_on"] or not st["ptp_amount_paise"]:
            return False
        paid = sum(r["amount_paise"] for r in self.lg._conn().execute(
            "SELECT p.amount_paise, p.paid_on FROM payments p JOIN loans l ON l.loan_id=p.loan_id"
            " WHERE l.borrower_id=? AND p.reversed_on IS NULL", (B,)) if st["ptp_captured_on"] <= r["paid_on"] <= st["ptp_date"])
        return paid >= st["ptp_amount_paise"]

    def op_payment(self):
        self.tick()
        st = self.db()
        ob_id = self.rng.choice([st["ptp_obligation"] or f"{B}-A-1", f"{B}-A-1", f"{B}-B-1"])
        ob = self.lg._obligation(self.lg._conn(), ob_id)
        due = ob["amount_due_paise"] - ob["amount_paid_paise"]
        if due <= 0:
            return
        amount = due if self.rng.random() < 0.6 else due // 2
        self.clock()
        fired = self.step("ptp", PTP_HOLD, "PAYMENT_VERIFIED",
                          {"clears_promised_emi": ob_id == st["ptp_obligation"] and amount >= due})
        self.k += 1
        self.lg.record_payment_verified(ob_id, amount, self.now, f"P{self.k}", f"R{self.k}")
        self.compare(f"payment {amount}/{due} on {ob_id}", st["broken_ptp"], fired)

    def op_new_day(self):
        self.day += timedelta(days=1)
        self.now = at(9, 0, self.day)
        st = self.db()
        self.clock()
        fired = self.step("ptp", PTP_HOLD, "DAY_STARTED", {
            "promise_date_passed": bool(st["ptp_date"]) and st["ptp_date"] < self.day.isoformat(),
            "promise_kept": bool(st["ptp_date"]) and self.promise_kept(st)})
        self.lg.start_day(self.day)
        self.compare(f"start_day {self.day}", st["broken_ptp"], fired)

    def op_clear(self):
        self.tick()
        self.clock()
        self.step("hardship", HARDSHIP_HOLD, "CLEAR_SUPERVISOR")
        self.step("supervisor", SUPERVISOR_HOLD, "CLEAR_SUPERVISOR")
        self.lg.clear_supervisor_hold(B)
        self.compare("clear_supervisor_hold")

    def op_stop(self):
        self.tick()
        for ch in CHANNELS:
            self.step(f"consent:{ch}", CONSENT, "STOP")
        self.lg.record_stop(B, self.now, self.rng.choice(CHANNELS))
        self.compare("STOP")

    def op_opt_in(self):
        self.tick()
        rows = self.db()["rows"]
        target = self.rng.choice(CHANNELS)
        for ch in CHANNELS:
            if ch == target:
                self.step(f"consent:{ch}", CONSENT, "OPT_IN_THIS_CHANNEL")
            else:
                self.step(f"consent:{ch}", CONSENT, "OPT_IN_OTHER_CHANNEL", {"has_consent_row": ch in rows})
        self.lg.opt_in(B, target, "v2", self.now)
        self.compare(f"opt-in {target}")

    OPS = (("op_exception", 40), ("op_payment", 10), ("op_new_day", 14), ("op_clear", 6), ("op_stop", 4),
           ("op_opt_in", 6))


def run_seed(policy, path, seed) -> Harness:
    h = Harness(policy, path, random.Random(seed))
    names, w = zip(*Harness.OPS)
    for _ in range(60):
        getattr(h, h.rng.choices(names, w)[0])()
    for _ in range(12):  # let every dated hold run out
        h.op_new_day()
    return h


@pytest.mark.parametrize("seed", SEEDS)
def test_holds_and_consent_take_only_declared_transitions(policy, tmp_path, seed):
    run_seed(policy, tmp_path / "h.db", seed).lg.close()


def test_every_hold_and_consent_transition_is_exercised(policy, tmp_path):
    cov = {}
    for seed in SEEDS:
        h = run_seed(policy, tmp_path / f"h{seed}.db", seed)
        for k, c in h.cov.items():
            cov.setdefault(k, Coverage()).fired.update(c.fired)
        h.lg.close()
    # voice consent withdrawal and handoffs come from k17-k18 / HITL (not on this branch)
    # broken_ptp only grows on PTP_HOLD -> PTP_BROKEN, so at the threshold the borrower is always in PTP_BROKEN;
    # the other broken_threshold self-loops are what the code does for imported counts, unreachable here.
    unreachable = [(s, "PTP_OFFERED", s, "broken_threshold") for s in ("NO_PTP", "PTP_KEPT", "PTP_HOLD")]
    # a refused capture right after a broken promise needs both EMIs paid in between: rare in the random
    # stream, driven directly by test_ptp_capture_without_an_unpaid_emi_is_refused below
    unreachable.append(("PTP_BROKEN", "PTP_OFFERED", "PTP_BROKEN", "!broken_threshold & !has_emi"))
    assert cov[PTP_HOLD.name].missing(PTP_HOLD, exempt=unreachable) == []
    assert cov[HARDSHIP_HOLD.name].missing(HARDSHIP_HOLD) == []
    assert cov[CALLBACK_HOLD.name].missing(CALLBACK_HOLD) == []
    assert cov[SUPERVISOR_HOLD.name].missing(SUPERVISOR_HOLD, exempt=[("NONE", "HANDOFF"), ("SUPERVISOR_HOLD", "HANDOFF")]) == []
    assert cov[CONSENT.name].missing(CONSENT, exempt=[(s, "VOICE_CONSENT_WITHDRAWN") for s in CONSENT.states]) == []


# ---------- focused tests for the fixed findings ----------
def _one(policy, tmp_path, name="f.db"):
    lg = new_ledger(policy, tmp_path / name, end_days=30)
    add_borrower(lg, B, dpd=12, consents={ch: 1 for ch in CHANNELS})
    return lg


def _pay(lg, ob_id, at_, pid, amount=None):
    ob = lg._obligation(lg._conn(), ob_id)
    lg.record_payment_verified(ob_id, amount or ob["amount_due_paise"] - ob["amount_paid_paise"], at_, pid, f"R-{pid}")


def test_hardship_holds_only_tighten(policy, tmp_path):
    """FSM-F5: a later 'no date' report keeps the earlier beyond-4-days hold and its supervisor flag."""
    lg = _one(policy, tmp_path)
    lg.apply_exception(B, ExceptionEvent("HARDSHIP", "B1", date_offset_days=9), at(9), f"{B}-A-1")
    first = lg._state(lg._conn(), B)
    assert first["hardship_supervisor"] == 1
    r = lg.apply_exception(B, ExceptionEvent("HARDSHIP", "B1"), at(11), f"{B}-A-1")        # no date: +2 days
    st = lg._state(lg._conn(), B)
    assert (st["hardship_until"], st["hardship_supervisor"]) == (first["hardship_until"], 1)
    assert HARDSHIP_HOLD.step("HARDSHIP_HANDOFF", "HARDSHIP", {"date_beyond": False})[0] == "HARDSHIP_HANDOFF"
    kept = [a for a in lg.audit_log(borrower_id=B) if a["type"] == "HARDSHIP_HOLD_KEPT"]
    assert kept and kept[0]["detail"]["requested_supervisor"] == 0 and r["hold"] == "HARDSHIP_HOLD"
    # a shorter dated report does not shorten an existing longer plain hold either
    lg.clear_supervisor_hold(B)
    lg.apply_exception(B, ExceptionEvent("HARDSHIP", "B1", date_offset_days=1), at(12), f"{B}-A-1")
    st = lg._state(lg._conn(), B)
    assert (st["hardship_until"], st["hardship_supervisor"]) == (first["hardship_until"], 0)
    # only a supervisor release clears the flag; once the hold has run out a new report starts fresh
    lg.close()


def test_hardship_after_the_hold_ran_out_starts_fresh(policy, tmp_path):
    lg = _one(policy, tmp_path)
    lg.apply_exception(B, ExceptionEvent("HARDSHIP", "B1", date_offset_days=5), at(9), f"{B}-A-1")
    later = at(10, 0, D + timedelta(days=6))
    lg.apply_exception(B, ExceptionEvent("HARDSHIP", "B1"), later, f"{B}-A-1")
    st = lg._state(lg._conn(), B)
    assert st["hardship_supervisor"] == 0
    assert st["hardship_until"] == lg._iso(later + timedelta(days=2))
    lg.close()


def test_ptp_without_obligation_resolves_the_oldest_unpaid_emi_and_can_be_kept(policy, tmp_path):
    """FSM-F6: obligation_id=None no longer means 'always broken'."""
    lg = _one(policy, tmp_path)
    r = lg.apply_exception(B, ExceptionEvent("PTP_OFFERED", "B1", date_offset_days=1), at(9), None)
    st = lg._state(lg._conn(), B)
    assert r["hold"] == "PTP_HOLD" and r["obligation_id"] == st["ptp_obligation"] in (f"{B}-A-1", f"{B}-B-1")
    assert st["ptp_captured_on"] == D.isoformat() and st["ptp_amount_paise"] > 0
    _pay(lg, st["ptp_obligation"], at(15, 0, D + timedelta(days=1)), "P1")
    assert lg.start_day(D + timedelta(days=2)) == []
    assert lg._state(lg._conn(), B)["broken_ptp"] == 0
    lg.close()


def test_ptp_kept_by_an_equal_payment_on_another_covered_emi(policy, tmp_path):
    lg = _one(policy, tmp_path)
    lg.apply_exception(B, ExceptionEvent("PTP_OFFERED", "B1", date_offset_days=1), at(9), f"{B}-A-1")
    _pay(lg, f"{B}-B-1", at(15), "P2")  # the other loan's EMI (same synthetic amount), on the capture day
    st = lg._state(lg._conn(), B)
    a = lg._obligation(lg._conn(), f"{B}-A-1")
    assert a["amount_paid_paise"] < a["amount_due_paise"]                 # promised EMI itself still unpaid
    assert lg.start_day(D + timedelta(days=2)) == []
    assert any(x["type"] == "PTP_KEPT" for x in lg.audit_log(borrower_id=B))
    assert lg._state(lg._conn(), B)["ptp_date"] is None and st["ptp_amount_paise"] > 0
    lg.close()


def test_ptp_partial_payment_is_still_broken(policy, tmp_path):
    lg = _one(policy, tmp_path)
    lg.apply_exception(B, ExceptionEvent("PTP_OFFERED", "B1", date_offset_days=1), at(9), f"{B}-A-1")
    ob = lg._obligation(lg._conn(), f"{B}-A-1")
    _pay(lg, f"{B}-A-1", at(15), "P3", amount=(ob["amount_due_paise"] - ob["amount_paid_paise"]) // 2)
    assert lg.start_day(D + timedelta(days=2)) == [B]
    assert lg._state(lg._conn(), B)["broken_ptp"] == 1
    lg.close()


def test_ptp_capture_without_an_unpaid_emi_is_refused(policy, tmp_path):
    lg = _one(policy, tmp_path)
    lg.apply_exception(B, ExceptionEvent("PTP_OFFERED", "B1", date_offset_days=1), at(9), f"{B}-A-1")
    assert lg.start_day(D + timedelta(days=2)) == [B]                     # PTP_BROKEN
    _pay(lg, f"{B}-A-1", at(10, 0, D + timedelta(days=2)), "P4")
    _pay(lg, f"{B}-B-1", at(10, 5, D + timedelta(days=2)), "P5")
    with pytest.raises(ValueError):
        lg.apply_exception(B, ExceptionEvent("PTP_OFFERED", "B1", date_offset_days=1, broken_ptp_count=1),
                           at(11, 0, D + timedelta(days=2)), None)
    assert lg._state(lg._conn(), B)["ptp_date"] is None
    assert PTP_HOLD.step("PTP_BROKEN", "PTP_OFFERED", {"broken_threshold": False, "has_emi": False})[0] == "PTP_BROKEN"
    with pytest.raises(ValueError):                                       # someone else's EMI
        lg.apply_exception(B, ExceptionEvent("PTP_OFFERED", "B1", date_offset_days=1), at(12, 0, D + timedelta(days=2)),
                           "NOPE-A-1")
    lg.close()


def test_callback_due_without_a_requested_callback_is_a_normal_retry(policy, tmp_path):
    """FSM-F7: unanswered + retry left only keeps a callback open when one was requested."""
    lg = _one(policy, tmp_path)
    due = ExceptionEvent("CALLBACK_DUE", "B1", callback_attempts=0, callback_answered=False)
    r = lg.apply_exception(B, due, at(10), f"{B}-A-1")
    assert (r["action"], r["hold"], r["ticket_id"]) == ("NO_OPEN_CALLBACK_NORMAL_RETRY", "NONE", None)
    assert lg._state(lg._conn(), B)["callback_pending"] == 0
    lg.apply_exception(B, ExceptionEvent("CALLBACK_REQUEST", "B1"), at(11), f"{B}-A-1")
    r = lg.apply_exception(B, due, at(13), f"{B}-A-1")
    assert (r["action"], r["hold"]) == ("RETRY_CALLBACK_2H", "CALLBACK_PENDING") and r["ticket_id"]
    assert lg._state(lg._conn(), B)["callback_pending"] == 1
    lg.close()
