"""k06.touch conformance: the ledger's touches.status only ever changes along declared transitions.

A seeded random stream of real calls (reserve, Executor.dispatch with scripted provider outcomes,
resolve_uncertain, provider callbacks, STOP, replies, verified payments, K13 exceptions, kill switch,
campaign pause, day roll-over). Before each call the harness predicts, from the declared machine and
facts it computes itself, the status of EVERY touch afterwards — including which queued touches a
STOP / payment / hold is allowed to cancel (scope). After the call the ledger must match exactly.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from kairos_backend.exceptions import ExceptionEvent, handle
from kairos_backend.executor import Executor, FakeProvider
from kairos_backend.ledger import Instruction, stable_instruction_id
from kairos_backend.machines import PROVIDER_STATUS_EVENT, TOUCH
from tests.fsm.world import CHANNELS, D, Coverage, add_borrower, at, new_ledger, random_consents

SEEDS = range(25)
STEPS = 90
BORROWERS = ("B1", "B2", "B3")
HOLD_SCOPE = {"PTP_HOLD", "HARDSHIP_HOLD", "CALLBACK_PENDING", "SUPERVISOR_HOLD"}


class ScriptedProvider(FakeProvider):
    """FakeProvider whose next outcome the harness picks (and therefore knows)."""

    def __init__(self, name):
        super().__init__(name, cost_paise=None, script=lambda t: self.next)
        self.next = "ACCEPTED"


class Harness:
    def __init__(self, policy, path, rng: random.Random):
        self.rng = rng
        self.lg = lg = new_ledger(policy, path)
        for b in BORROWERS:
            add_borrower(lg, b, dpd=rng.choice([3, 12, 12, 45]), consents=random_consents(rng))
        self.providers = {ch: ScriptedProvider(ch) for ch in ("sms", "whatsapp", "email", "ivr")}  # no voicebot adapter
        self.ex = Executor(lg, self.providers)
        self.cov = Coverage()
        self.day = D
        self.n = 0

    # ----- observation -----
    def rows(self) -> dict:
        return {r["instruction_id"]: dict(r) for r in self.lg._conn().execute("SELECT * FROM touches")}

    def check(self, before: dict, expected: dict, what: str) -> None:
        after = {k: v["status"] for k, v in self.rows().items()}
        assert after == expected, f"{what}: " + str({k: (before.get(k, {}).get("status", "PROPOSED"), expected.get(k),
                                                          after.get(k)) for k in set(after) | set(expected)
                                                     if after.get(k) != expected.get(k)})

    def run(self, state: str, steps) -> str:
        for ev, ctx in steps:
            state, t = TOUCH.step(state, ev, ctx)
            self.cov.add(t)
        return state

    def cancel_scope(self, before: dict, pred) -> dict:
        """CANCEL_QUEUED for every touch in scope, whatever its status: the machine says which ones may
        change (queued) and which must not (in flight, sent, final)."""
        exp = {k: r["status"] for k, r in before.items()}
        for k, r in before.items():
            if pred(r):
                exp[k] = self.run(r["status"], [("CANCEL_QUEUED", {})])
        return exp

    def when(self, h=None) -> datetime:
        return at(h if h is not None else self.rng.randrange(7, 21), self.rng.choice((0, 15, 30, 45)), self.day)

    # ----- operations -----
    def op_reserve(self):
        rng, before = self.rng, self.rows()
        b, loan, ch = rng.choice(BORROWERS), rng.choice("AB"), rng.choice(CHANNELS)
        t = self.when()
        tpl = rng.choice([{"sms": "SMS_EN", "whatsapp": "WA_HI", "email": "EM_EN", "ivr": "IVR_HI",
                           "voicebot": "VB_EN"}[ch]] * 9 + [None])
        ins = Instruction(b, f"{b}-{loan}-1", "C1", ch, tpl, t, rng.choice([20, 40, 100, 400]))
        iid = stable_instruction_id(b, ins.obligation_id, ch, self.lg.policy.local(t))
        exp = {k: r["status"] for k, r in before.items()}
        if iid in before:
            exp[iid] = self.run(before[iid]["status"], [("RESERVE", {})])
        else:
            gate_send = self.lg.preview(Instruction(**vars(ins))).allowed
            end = self.run("PROPOSED", [("RESERVE", {"gate_send": gate_send})])
            if end != "REFUSED":
                exp[iid] = end
        r = self.lg.reserve(ins)
        assert r.instruction_id == iid
        self.check(before, exp, f"reserve {iid}")

    def op_replay(self):
        """The same instruction submitted again (retry, second worker): never a second reservation."""
        before = self.rows()
        if not before:
            return
        iid = self.rng.choice(sorted(before))
        exp = {k: r["status"] for k, r in before.items()}
        exp[iid] = self.run(before[iid]["status"], [("RESERVE", {})])
        r = self.lg.reserve(self.lg.instruction_of(before[iid]))
        assert r.idempotent_replay
        self.check(before, exp, f"replay {iid}")

    def op_dispatch(self):
        rng, before = self.rng, self.rows()
        if not before:
            return
        iid = rng.choice(sorted(before))
        row = before[iid]
        sched = self.lg.policy.local(datetime.fromisoformat(row["scheduled_at"]))
        t = sched + timedelta(minutes=rng.choice([0, 0, 30, 120])) if rng.random() < 0.9 else sched.replace(hour=19, minute=10)
        if t.date() != sched.date():
            t = sched
        steps = []
        if row["status"] == "RESERVED":
            ins = self.lg.instruction_of(row)
            ins.at = t
            gate_send = self.lg.preview(ins, exclude=iid).allowed  # same world begin_dispatch will see
            steps.append(("BEGIN_DISPATCH", {"gate_send": gate_send}))
            if gate_send:
                prov = self.providers.get(row["channel"])
                if prov is None:
                    steps.append(("PROVIDER_REJECTED", {}))      # Executor: NO_ADAPTER
                else:
                    prov.next = rng.choice(["ACCEPTED", "ACCEPTED", "REJECTED", "UNKNOWN_BUT_DELIVERED", "UNKNOWN_LOST"])
                    steps.append(({"ACCEPTED": "PROVIDER_ACCEPTED", "REJECTED": "PROVIDER_REJECTED"}
                                  .get(prov.next, "PROVIDER_UNKNOWN"), {}))
        else:
            steps.append(("BEGIN_DISPATCH", {}))
        exp = {k: r["status"] for k, r in before.items()}
        exp[iid] = self.run(row["status"], steps)
        self.ex.dispatch(iid, t)
        self.check(before, exp, f"dispatch {iid} from {row['status']} at {t:%H:%M}")

    def op_resolve(self):
        before = self.rows()
        unc = sorted(k for k, r in before.items() if r["status"] == "UNCERTAIN")
        if not unc:
            return
        iid = self.rng.choice(unc)
        prov = self.providers[before[iid]["channel"]]
        known = prov.status(iid, None)
        ev = {"DELIVERED": "PROVIDER_DELIVERED", "ACCEPTED": "PROVIDER_ACCEPTED"}.get(known, "PROVIDER_REJECTED")
        exp = {k: r["status"] for k, r in before.items()}
        exp[iid] = self.run("UNCERTAIN", [(ev, {})])
        self.ex.resolve_uncertain(iid)
        self.check(before, exp, f"resolve {iid} ({known})")

    def op_callback(self):
        """A provider callback for any touch: one the provider has seen, or (FSM-F1) an orphan / early
        receipt for a touch that is still RESERVED, which must leave it unchanged."""
        before = self.rows()
        if not before:
            return
        iid = self.rng.choice(sorted(before))
        word = self.rng.choice(sorted(PROVIDER_STATUS_EVENT))
        exp = {k: r["status"] for k, r in before.items()}
        exp[iid] = self.run(before[iid]["status"], [(PROVIDER_STATUS_EVENT[word], {})])
        self.lg.record_provider_status(iid, word)
        self.check(before, exp, f"callback {word} on {iid}")

    def op_stop(self):
        before, b = self.rows(), self.rng.choice(BORROWERS)
        exp = self.cancel_scope(before, lambda r: r["borrower_id"] == b)
        self.lg.record_stop(b, self.when(), self.rng.choice(CHANNELS))
        self.check(before, exp, f"STOP {b}")

    def op_opt_in(self):
        b = self.rng.choice(BORROWERS)
        before = self.rows()
        self.lg.opt_in(b, self.rng.choice(CHANNELS), "v2", self.when())
        self.check(before, {k: r["status"] for k, r in before.items()}, "opt-in changes no touch")

    def op_reply(self):
        before, b, t = self.rows(), self.rng.choice(BORROWERS), self.when()
        day = t.date().isoformat()
        exp = self.cancel_scope(before, lambda r: r["borrower_id"] == b and r["day"] == day)
        self.lg.record_response(b, t, self.rng.choice(CHANNELS))
        self.check(before, exp, f"reply {b} (stop-for-day {day})")

    def op_call(self):
        before, b, t = self.rows(), self.rng.choice(BORROWERS), self.when()
        secs = self.rng.choice([3, 10, 11, 40])
        day = t.date().isoformat()
        pick = secs > self.lg.policy.call_pickup_threshold_s
        exp = self.cancel_scope(before, lambda r: pick and r["borrower_id"] == b and r["day"] == day
                               )
        self.lg.record_call(b, t, secs)
        self.check(before, exp, f"call {b} {secs}s")

    def op_payment(self):
        before, b, loan = self.rows(), self.rng.choice(BORROWERS), self.rng.choice("AB")
        ob_id = f"{b}-{loan}-1"
        ob = self.lg._obligation(self.lg._conn(), ob_id)
        due = ob["amount_due_paise"] - ob["amount_paid_paise"]
        if due <= 0:
            return
        amount = due if self.rng.random() < 0.6 else due // 2
        clears = amount >= due
        exp = self.cancel_scope(before, lambda r: clears and r["obligation_id"] == ob_id)
        self.n += 1
        self.lg.record_payment_verified(ob_id, amount, self.when(), f"P{self.n}", f"R{self.n}")
        self.check(before, exp, f"payment {amount} on {ob_id} (clears={clears})")

    def op_exception(self):
        before, b, t = self.rows(), self.rng.choice(BORROWERS), self.when()
        ev = ExceptionEvent(self.rng.choice(["PTP_OFFERED", "HARDSHIP", "CALLBACK_REQUEST", "CALLBACK_DUE", "ABUSIVE",
                                             "NEEDS_TIME", "DISPUTE", "FRAUD_CLAIM"]), "B1",
                            in_window=self.rng.random() < 0.8, date_offset_days=self.rng.choice([None, 1, 3, 9]),
                            callback_attempts=self.rng.randrange(3), callback_answered=self.rng.random() < 0.5)
        hold = handle(self.lg.policy, ev).hold
        day = t.date().isoformat()
        if hold in HOLD_SCOPE:
            scope = lambda r: r["borrower_id"] == b  # noqa: E731
        elif hold == "STOP_FOR_DAY":
            scope = lambda r: r["borrower_id"] == b and r["day"] == day  # noqa: E731
        else:
            scope = lambda r: False  # noqa: E731
        exp = self.cancel_scope(before, scope)
        self.lg.apply_exception(b, ev, t, f"{b}-A-1")
        self.check(before, exp, f"K13 {ev.event} -> {hold}")
        if self.rng.random() < 0.3:
            self.lg.clear_supervisor_hold(b)

    def op_pause(self):
        before = self.rows()
        paused = self.rng.random() < 0.5
        exp = self.cancel_scope(before, lambda r: paused)
        self.lg.set_outbound_paused(paused)
        self.check(before, exp, f"outbound paused={paused}")
        if paused and self.rng.random() < 0.7:
            self.lg.set_outbound_paused(False)

    def op_campaign(self):
        before = self.rows()
        st = self.rng.choice(["PAUSED", "RUNNING", "RUNNING"])
        exp = self.cancel_scope(before, lambda r: st == "PAUSED" and r["campaign_id"] == "C1")
        self.lg.set_campaign_status("C1", st)
        self.check(before, exp, f"campaign {st}")
        if st == "PAUSED" and self.rng.random() < 0.7:
            self.lg.set_campaign_status("C1", "RUNNING")

    def op_new_day(self):
        before = self.rows()
        if self.day > D + timedelta(days=1):
            return
        self.day += timedelta(days=1)
        self.lg.start_day(self.day)
        self.check(before, {k: r["status"] for k, r in before.items()}, "start_day changes no touch")

    OPS = (("op_reserve", 30), ("op_replay", 4), ("op_dispatch", 22), ("op_resolve", 6), ("op_callback", 12), ("op_stop", 2),
           ("op_opt_in", 2), ("op_reply", 4), ("op_call", 3), ("op_payment", 4), ("op_exception", 5), ("op_pause", 2),
           ("op_campaign", 2), ("op_new_day", 2))

    def step(self):
        names, weights = zip(*self.OPS)
        getattr(self, self.rng.choices(names, weights)[0])()


def run_seed(policy, path, seed) -> Harness:
    h = Harness(policy, path, random.Random(seed))
    for _ in range(STEPS):
        h.step()
    assert h.lg.cap_breaches() == []
    return h


@pytest.mark.parametrize("seed", SEEDS)
def test_ledger_takes_only_declared_touch_transitions(policy, tmp_path, seed):
    run_seed(policy, tmp_path / "touch.db", seed).lg.close()


# transitions the public surface cannot reach: DISPATCHING only exists inside one Executor.dispatch call,
# and the Executor itself never reports DELIVERED straight from DISPATCHING.
UNREACHABLE_FROM_OUTSIDE = [("DISPATCHING", "RESERVE"), ("DISPATCHING", "BEGIN_DISPATCH"),
                            ("DISPATCHING", "CANCEL_QUEUED"), ("DISPATCHING", "PROVIDER_DELIVERED")]


def test_every_touch_transition_is_exercised(policy, tmp_path):
    cov = Coverage()
    for seed in SEEDS:
        h = run_seed(policy, tmp_path / f"t{seed}.db", seed)
        cov.fired.update(h.cov.fired)
        h.lg.close()
    assert cov.missing(TOUCH, exempt=UNREACHABLE_FROM_OUTSIDE) == []


def test_pre_gate_control_stop_is_a_refusal(policy, tmp_path):
    """k01.gate PRE_GATE --HIT--> WAIT: the ledger's operational stops refuse the reservation (no row)."""
    lg = new_ledger(policy, tmp_path / "pre.db")
    add_borrower(lg, "P1")
    lg.set_outbound_paused(True)
    r = lg.reserve(Instruction("P1", "P1-A-1", "C1", "sms", "SMS_EN", at(10), 20))
    assert (r.reserved, r.decision.decision, r.decision.rule) == (False, "WAIT", "CONTROL")
    assert lg.touch(r.instruction_id) is None  # PROPOSED --RESERVE[!gate_send]--> REFUSED
    lg.close()


# ---------- FSM-F1 (fixed): provider callbacks never move a touch that was not dispatched ----------
@pytest.mark.parametrize("word", ["ACCEPTED", "SENT", "DELIVERED", "READ", "UNKNOWN", "UNCERTAIN", "FAILED", "REJECTED"])
def test_provider_callback_on_a_reserved_touch_is_an_orphan_receipt(policy, tmp_path, word):
    lg = new_ledger(policy, tmp_path / "f1.db")
    add_borrower(lg, "F1")
    iid = lg.reserve(Instruction("F1", "F1-A-1", "C1", "sms", "SMS_EN", at(10), 20)).instruction_id
    committed = lg.campaign_state("C1")["committed_paise"]
    assert lg.record_provider_status(iid, word) == "RESERVED"
    assert lg.touch(iid)["status"] == "RESERVED"
    assert lg.campaign_state("C1")["committed_paise"] == committed          # FAILED released nothing
    assert not lg.borrower_state("F1")["events"] or all(e["event_type"] != "touch_sent"
                                                         for e in lg.borrower_state("F1")["events"])
    orphan = [a for a in lg.audit_log(borrower_id="F1") if a["type"] == "PROVIDER_STATUS_ORPHAN"]
    assert orphan and orphan[0]["reason"] == word and orphan[0]["instruction_id"] == iid
    # the real dispatch still happens, through the send-time re-check
    assert Executor(lg, {"sms": FakeProvider("sms")}).dispatch(iid, at(10)).outcome == "SENT"
    assert lg.touch(iid)["status"] == "SENT"
    lg.close()


@pytest.mark.parametrize("word", ["RESERVED", "DISPATCHING", "CANCELED", "PENDING_APPROVAL", "bogus", ""])
def test_internal_statuses_are_not_provider_words(policy, tmp_path, word):
    lg = new_ledger(policy, tmp_path / "f1w.db")
    add_borrower(lg, "F1")
    iid = lg.reserve(Instruction("F1", "F1-A-1", "C1", "sms", "SMS_EN", at(10), 20)).instruction_id
    with pytest.raises(ValueError):
        lg.record_provider_status(iid, word)
    assert lg.touch(iid)["status"] == "RESERVED"
    lg.close()


def test_provider_vocabulary_matches_the_declared_events():
    from kairos_backend.ledger import PROVIDER_STATUSES
    assert set(PROVIDER_STATUSES) == set(PROVIDER_STATUS_EVENT)
