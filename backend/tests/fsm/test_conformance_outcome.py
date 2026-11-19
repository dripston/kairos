"""k09.outcome conformance: touch_outcomes only moves along declared transitions; the learner sees
exactly the REPLY / NO_REPLY finalizations, once each.

Seeded random streams of executed touches (provider ACCEPTED / REJECTED / UNKNOWN, later DELIVERED
callbacks), reach callbacks, meaningful and non-meaningful replies (in and out of the window, with and
without a channel), and finalize_due() at random clock times. The harness predicts each row's state
from the declared machine with facts it computes from the database BEFORE the call (which touch a
reply is attributed to, in-window, ledger status, reach known).
"""
from __future__ import annotations

import random
from collections import Counter
from datetime import timedelta

import pytest

from kairos_backend.contracts import slot_of
from kairos_backend.executor import Executor, FakeProvider
from kairos_backend.ledger import Instruction
from kairos_backend.machines import OUTCOME
from kairos_backend.outcomes import OutcomeFinalizer
from tests.fsm.world import D, Coverage, add_borrower, at, new_ledger

SEEDS = range(25)
BORROWERS = ("O1", "O2")
CHS = ("sms", "whatsapp", "email")
TPL = {"sms": "SMS_EN", "whatsapp": "WA_HI", "email": "EM_EN"}


class SpyLearner:
    def __init__(self):
        self.calls: Counter = Counter()
        self.rewards = {}

    def apply_outcome(self, o) -> bool:
        self.calls[o.instruction_id] += 1
        self.rewards[o.instruction_id] = o.reward
        return self.calls[o.instruction_id] == 1

    def rank(self, *a, **k):  # pragma: no cover - not used
        raise NotImplementedError

    def estimates(self, band=None):
        return []


def project(r) -> str:
    if r["status"] == "PENDING":
        return "PENDING_REPLIED" if r["reply_at"] else "PENDING"
    return r["status"]


class Harness:
    def __init__(self, policy, path, rng):
        self.rng = rng
        self.lg = lg = new_ledger(policy, path, end_days=20)
        for b in BORROWERS:
            add_borrower(lg, b, dpd=12)
        lg.set_setting("enforce_daily_pacing", False)
        self.prov = {ch: FakeProvider(ch, script=lambda t: self.next) for ch in CHS}
        self.next = "ACCEPTED"
        self.ex = Executor(lg, self.prov)
        self.learner = SpyLearner()
        self.fin = OutcomeFinalizer(lg, self.learner)
        self.now = at(8)
        self.cov = Coverage()
        self.state = {}       # iid -> machine state the harness tracks
        self.k = 0

    def rows(self) -> dict:
        return {r["instruction_id"]: dict(r) for r in self.lg._conn().execute("SELECT * FROM touch_outcomes")}

    def iso(self, dt) -> str:
        return self.lg.policy.local(dt).isoformat(timespec="seconds")

    def advance(self):
        self.now += timedelta(hours=self.rng.choice([0, 1, 2, 5, 11, 23, 26]), minutes=self.rng.choice([0, 7, 30]))
        if self.now.hour < 8 or self.now.hour >= 19:  # executions happen inside the window
            nxt = self.now.date() + (timedelta(days=1) if self.now.hour >= 19 else timedelta(0))
            self.now = at(8, self.rng.choice((0, 30)), nxt)
        self.lg.start_day(self.now.date())

    def step(self, iid, ev, ctx=None):
        new, t = OUTCOME.step(self.state[iid], ev, ctx or {})
        self.cov.add(t)
        self.state[iid] = new

    def compare(self, what):
        rows = self.rows()
        assert {k: project(r) for k, r in rows.items()} == self.state, what

    # ----- operations -----
    def op_execute(self):
        b, ch = self.rng.choice(BORROWERS), self.rng.choice(CHS)
        self.k += 1
        ins = Instruction(b, f"{b}-{self.rng.choice('AB')}-1", "C1", ch, TPL[ch], self.now, 20,
                          instruction_id=f"X{self.k}")
        r = self.lg.reserve(ins)
        if not r.reserved:
            return
        self.next = self.rng.choice(["ACCEPTED", "ACCEPTED", "REJECTED", "UNKNOWN_LOST", "UNKNOWN_BUT_DELIVERED"])
        self.ex.dispatch(r.instruction_id, self.now)
        self.state[r.instruction_id] = "NEW"
        self.step(r.instruction_id, "REGISTER")
        assert self.fin.register_execution(r.instruction_id, b, "B1", ch, slot_of(self.lg.policy.local(self.now)), self.now)
        if self.rng.random() < 0.2:  # duplicate registration is a no-op
            self.step(r.instruction_id, "REGISTER")
            assert not self.fin.register_execution(r.instruction_id, b, "B1", ch, "08-11", self.now)
        self.compare("register")

    def op_reregister(self):
        if not self.state:
            return
        iid = self.rng.choice(sorted(self.state))
        r = self.rows()[iid]
        self.step(iid, "REGISTER")
        assert not self.fin.register_execution(iid, r["borrower_id"], r["band"], r["channel"], r["slot"], self.now)
        self.compare("re-register")

    def op_provider(self):
        live = [k for k in self.state if self.lg.touch(k)["status"] in ("SENT", "UNCERTAIN")]
        if live:
            iid = self.rng.choice(sorted(live))
            self.lg.record_provider_status(iid, self.rng.choice(["DELIVERED", "READ"]))
            self.compare("provider status changes no outcome row")

    def op_reach(self):
        if not self.state:
            return
        iid = self.rng.choice(sorted(self.state))
        self.step(iid, "REACH")
        self.fin.record_reach(iid, self.rng.random() < 0.6, self.now)
        self.compare("reach")

    def op_reply(self):
        b = self.rng.choice(BORROWERS)
        meaningful = self.rng.random() < 0.7
        ch = self.rng.choice([None, None] + list(CHS))
        at_ = self.now + timedelta(minutes=self.rng.choice([0, 5, 60]))
        at_iso = self.iso(at_)
        rows = [r for r in self.rows().values() if r["borrower_id"] == b and r["executed_at"] <= at_iso
                and (ch is None or r["channel"] == ch)]
        target = max(rows, key=lambda r: (r["executed_at"], r["instruction_id"]), default=None)
        if target is not None:
            if meaningful:
                self.step(target["instruction_id"], "MEANINGFUL_REPLY", {"in_window": at_iso <= target["window_end"]})
            else:
                self.step(target["instruction_id"], "CLICK_OR_READ")
        self.fin.record_reply(b, at_, meaningful, channel=ch)
        self.compare(f"reply {b} meaningful={meaningful} ch={ch}")

    def op_finalize(self):
        self.advance()
        now_iso = self.iso(self.now)
        before = self.rows()
        newly = []
        for iid, r in before.items():
            if r["status"] == "PENDING" and r["window_end"] <= now_iso:
                ts = self.lg.touch(iid)["status"]
                self.step(iid, "WINDOW_CLOSED", {"never_sent": ts in ("FAILED", "CANCELED"),
                                                  "reach_known": r["reached"] is not None,
                                                  "ledger_sent": ts in ("SENT", "DELIVERED")})
                newly.append(iid)
            elif r["status"] != "PENDING":
                self.step(iid, "WINDOW_CLOSED")
        out = self.fin.finalize_due(self.now)
        assert sorted(o.instruction_id for o in out) == sorted(newly)
        self.compare("finalize")
        for iid in newly:  # REPLY / NO_REPLY reach the learner exactly once; UNRESOLVED never
            want = {"REPLY": 1, "NO_REPLY": 0}.get(self.state[iid])
            assert self.learner.calls[iid] == (0 if want is None else 1)
            if want is not None:
                assert self.learner.rewards[iid] == want

    OPS = (("op_execute", 30), ("op_reregister", 5), ("op_provider", 6), ("op_reach", 8), ("op_reply", 30), ("op_finalize", 14))


def run_seed(policy, path, seed) -> Harness:
    h = Harness(policy, path, random.Random(seed))
    names, w = zip(*Harness.OPS)
    for _ in range(80):
        getattr(h, h.rng.choices(names, w)[0])()
    h.now += timedelta(days=3)
    h.op_finalize()
    assert all(s in OUTCOME.terminal for s in h.state.values())
    assert all(n == 1 for n in h.learner.calls.values())
    return h


@pytest.mark.parametrize("seed", SEEDS)
def test_outcomes_take_only_declared_transitions(policy, tmp_path, seed):
    run_seed(policy, tmp_path / "o.db", seed).lg.close()


def test_every_outcome_transition_is_exercised(policy, tmp_path):
    cov = Coverage()
    for seed in SEEDS:
        h = run_seed(policy, tmp_path / f"o{seed}.db", seed)
        cov.fired.update(h.cov.fired)
        h.lg.close()
    assert cov.missing(OUTCOME) == []
