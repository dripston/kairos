"""k06.hitl.approval and k06.touch+hitl conformance (runs only when kairos_backend.hitl is present,
i.e. once k06/hitl-control is merged).

Seeded random reserves (some matching an approval rule), approvals (in time / expired / maker = checker /
guardrail failing at re-check), rejections, expiry ticks, human cancels, STOP and dispatch attempts.
Before each call the harness predicts the approval and touch states from the declared machines; after it
the database must agree.
"""
from __future__ import annotations

import random
from datetime import timedelta

import pytest

hitl_mod = pytest.importorskip("kairos_backend.hitl")

from kairos_backend.executor import Executor, FakeProvider  # noqa: E402
from kairos_backend.ledger import Instruction, stable_instruction_id  # noqa: E402
from kairos_backend.machines import HITL_APPROVAL, TOUCH_HITL  # noqa: E402
from tests.fsm.world import D, Coverage, add_borrower, at, new_ledger  # noqa: E402

SEEDS = range(20)
TPL = {"sms": "SMS_EN", "whatsapp": "WA_HI", "ivr": "IVR_HI"}


def approval_state(dec, status) -> str:
    if dec is None:
        return "PENDING" if status == "PENDING_APPROVAL" else "WITHDRAWN"
    return dec


class Harness:
    def __init__(self, policy, path, rng):
        self.rng = rng
        self.lg = lg = new_ledger(policy, path)
        for b, dpd in (("A1", 12), ("A2", 75)):
            add_borrower(lg, b, dpd=dpd, consents={"sms": 1, "whatsapp": 1, "email": 1, "ivr": 1, "voicebot": 1})
        self.h = hitl_mod.HITL(lg)
        for tpl, inten in (("SMS_EN", "LOW"), ("WA_HI", "STANDARD"), ("IVR_HI", "LOW")):
            self.h.register_template(tpl, inten, None, "ops", "policy_owner", "catalog", now=at(8))
        self.ex = Executor(lg, {ch: FakeProvider(ch) for ch in TPL})
        self.cov_t, self.cov_a = Coverage(), Coverage()
        self.now = at(8, 30)
        self.k = 0

    def snap(self):
        c = self.lg._conn()
        touches = {r["instruction_id"]: r["status"] for r in c.execute("SELECT instruction_id, status FROM touches")}
        appr = {r["instruction_id"]: approval_state(r["decision"], touches.get(r["instruction_id"]))
                for r in c.execute("SELECT instruction_id, decision FROM hitl_approvals")}
        return touches, appr

    def run(self, machine, cov, state, steps):
        for ev, ctx in steps:
            state, t = machine.step(state, ev, ctx)
            cov.add(t)
        return state

    def expect(self, what, touches_exp, appr_exp):
        touches, appr = self.snap()
        assert touches == touches_exp, f"{what}: touches {touches} != {touches_exp}"
        assert appr == appr_exp, f"{what}: approvals {appr} != {appr_exp}"

    def gate_now(self, iid, when):
        row = self.lg.touch(iid)
        ins = self.lg.instruction_of(row)
        ins.at = when
        return self.lg.preview(ins, exclude=iid).allowed

    # ----- ops -----
    def op_reserve(self):
        b, ch = self.rng.choice(("A1", "A2")), self.rng.choice(("sms", "whatsapp", "ivr", "ivr"))
        t = at(self.rng.randrange(9, 18), self.rng.choice((0, 30)))
        ins = Instruction(b, f"{b}-{self.rng.choice('AB')}-1", "C1", ch, TPL[ch], t, 20)
        iid = stable_instruction_id(b, ins.obligation_id, ch, self.lg.policy.local(t))
        touches, appr = self.snap()
        if iid in touches:
            return
        d = self.lg.preview(Instruction(**vars(ins)))
        with self.lg.tx() as c:
            rules = self.h.approval_rules_for(c, ins, d.detail.get("dpd")) if d.allowed else []
        end = self.run(TOUCH_HITL, self.cov_t, "PROPOSED",
                       [("RESERVE", {"gate_send": d.allowed, "approval_required": bool(rules)})])
        if end != "REFUSED":
            touches[iid] = end
        if end == "PENDING_APPROVAL":
            appr[iid] = self.run(HITL_APPROVAL, self.cov_a, "NEW", [("RESERVE", {"gate_send": True, "approval_required": True})])
        self.lg.reserve(ins)
        self.expect(f"reserve {ch} {b}", touches, appr)

    def pending(self):
        touches, _ = self.snap()
        return sorted(k for k, s in touches.items() if s == "PENDING_APPROVAL")

    def op_approve(self):
        p = self.pending()
        if not p:
            return
        iid = self.rng.choice(p)
        when = self.lg.policy.local(self.now + timedelta(hours=self.rng.choice([0, 0, 1, 12])))
        a = self.lg._conn().execute("SELECT * FROM hitl_approvals WHERE instruction_id=?", (iid,)).fetchone()
        expired = when >= self.lg._dt(a["expires_at"])
        sched = self.lg._dt(self.lg.touch(iid)["scheduled_at"])
        gate = self.gate_now(iid, max(sched, when))
        touches, appr = self.snap()
        ctx = {"maker_is_checker": False, "expired": expired, "gate_send": gate}
        appr[iid] = self.run(HITL_APPROVAL, self.cov_a, "PENDING", [("APPROVE", ctx)])
        touches[iid] = self.run(TOUCH_HITL, self.cov_t, "PENDING_APPROVAL", [("APPROVE", ctx)])
        self.h.approve(iid, "ravi", "supervisor", "ok", now=when)
        self.expect(f"approve expired={expired} gate={gate}", touches, appr)

    def op_reject(self):
        p = self.pending()
        if not p:
            return
        iid = self.rng.choice(p)
        touches, appr = self.snap()
        appr[iid] = self.run(HITL_APPROVAL, self.cov_a, "PENDING", [("REJECT", {})])
        touches[iid] = self.run(TOUCH_HITL, self.cov_t, "PENDING_APPROVAL", [("REJECT", {})])
        self.h.reject(iid, "asha", "operator", "no", now=self.now)
        self.expect("reject", touches, appr)

    def op_tick(self):
        when = self.now + timedelta(hours=self.rng.choice([0, 6, 11, 30]))
        touches, appr = self.snap()
        for iid in self.pending():
            a = self.lg._conn().execute("SELECT expires_at FROM hitl_approvals WHERE instruction_id=?", (iid,)).fetchone()
            ctx = {"expired": self.lg._dt(a["expires_at"]) <= self.lg.policy.local(when)}
            appr[iid] = self.run(HITL_APPROVAL, self.cov_a, "PENDING", [("EXPIRE_DUE", ctx)])
            if ctx["expired"]:
                touches[iid] = self.run(TOUCH_HITL, self.cov_t, "PENDING_APPROVAL", [("EXPIRE", {})])
        self.h.expire_due(when)
        self.expect("expire_due", touches, appr)

    def op_cancel(self):
        touches, appr = self.snap()
        q = sorted(k for k, s in touches.items() if s in ("RESERVED", "PENDING_APPROVAL"))
        if not q:
            return
        iid = self.rng.choice(q)
        if touches[iid] == "PENDING_APPROVAL":
            appr[iid] = self.run(HITL_APPROVAL, self.cov_a, "PENDING", [("HUMAN_CANCEL", {})])
        touches[iid] = self.run(TOUCH_HITL, self.cov_t, touches[iid], [("HUMAN_CANCEL", {})])
        self.h.cancel_scheduled(iid, "asha", "operator", "not needed", now=self.now)
        self.expect("cancel_scheduled", touches, appr)

    def op_stop(self):
        b = self.rng.choice(("A1", "A2"))
        touches, appr = self.snap()
        mine = {r["instruction_id"] for r in self.lg._conn().execute("SELECT instruction_id FROM touches WHERE borrower_id=?", (b,))}
        for iid in sorted(mine):
            if touches[iid] == "PENDING_APPROVAL":
                appr[iid] = self.run(HITL_APPROVAL, self.cov_a, "PENDING", [("CANCEL_QUEUED", {})])
            touches[iid] = self.run(TOUCH_HITL, self.cov_t, touches[iid], [("CANCEL_QUEUED", {})])
        self.lg.record_stop(b, self.now, "sms")
        self.expect("STOP", touches, appr)

    def op_dispatch(self):
        touches, appr = self.snap()
        p = self.pending()
        if not p:
            return
        iid = self.rng.choice(p)
        touches[iid] = self.run(TOUCH_HITL, self.cov_t, "PENDING_APPROVAL", [("BEGIN_DISPATCH", {})])
        assert self.ex.dispatch(iid, at(17)).outcome == "SKIPPED"
        self.expect("dispatch of a pending send", touches, appr)

    def op_template(self):
        """Revoke / re-approve a template: a pending send can then fail its approval-time re-check."""
        ch = self.rng.choice(sorted(TPL))
        before = self.snap()
        self.lg.upsert_template(TPL[ch], ch, {"sms": "en", "whatsapp": "hi", "ivr": "hi"}[ch],
                                approved=self.rng.random() < 0.5)
        self.expect("template toggle changes no state", *before)

    OPS = (("op_reserve", 40), ("op_template", 5), ("op_approve", 15), ("op_reject", 6), ("op_tick", 6), ("op_cancel", 6),
           ("op_stop", 2), ("op_dispatch", 5))


def run_seed(policy, path, seed):
    h = Harness(policy, path, random.Random(seed))
    names, w = zip(*Harness.OPS)
    for _ in range(50):
        getattr(h, h.rng.choices(names, w)[0])()
    assert h.lg.cap_breaches() == []
    return h


@pytest.mark.parametrize("seed", SEEDS)
def test_hitl_takes_only_declared_transitions(policy, tmp_path, seed):
    run_seed(policy, tmp_path / "hitl.db", seed).lg.close()


def test_every_reachable_hitl_transition_is_exercised(policy, tmp_path):
    cov = Coverage()
    for seed in SEEDS:
        h = run_seed(policy, tmp_path / f"h{seed}.db", seed)
        cov.fired.update(h.cov_a.fired)
        h.lg.close()
    exempt = [("PENDING", "APPROVE", "PENDING", "maker_is_checker"), ("PENDING", "RESCHEDULE")] + \
             [(s, e) for s in HITL_APPROVAL.terminal for e in ("APPROVE", "REJECT")]
    assert cov.missing(HITL_APPROVAL, exempt=exempt) == []


def manual_env(policy, tmp_path):
    h = Harness(policy, tmp_path / "m.db", random.Random(0))
    r = h.h.schedule_manual("A1", "sms", "SMS_EN", at(11), "maker", "operator", "borrower asked", 20, now=at(9))
    return h, r["results"][0]["instruction_id"]


def test_maker_cannot_check_and_reschedule_success_conform(policy, tmp_path):
    h, iid = manual_env(policy, tmp_path)
    assert h.lg.touch(iid)["status"] == "PENDING_APPROVAL"
    with pytest.raises(hitl_mod.Forbidden):
        h.h.approve(iid, "maker", "supervisor", "self-approve", now=at(9, 5))
    assert HITL_APPROVAL.step("PENDING", "APPROVE", {"maker_is_checker": True, "expired": False, "gate_send": True})[0] == "PENDING"
    assert h.snap()[1][iid] == "PENDING"
    out = h.h.reschedule(iid, at(15), "maker", "supervisor", "later", now=at(9, 10))
    assert out["new"]["outcome"] != "REFUSED"
    assert h.snap()[1][iid] == HITL_APPROVAL.step("PENDING", "RESCHEDULE")[0] == "RESCHEDULED"
    assert h.lg.touch(iid)["status"] == TOUCH_HITL.step("PENDING_APPROVAL", "HUMAN_CANCEL")[0] == "CANCELED"
    h.lg.close()


@pytest.mark.xfail(strict=True, reason="FSM-F2: HITL.reschedule restores a CANCELED touch (CANCELED -> PENDING_APPROVAL "
                                       "/ RESERVED) when the new slot is refused; CANCELED is final in k06.touch")
def test_reschedule_refused_does_not_resurrect_a_canceled_touch(policy, tmp_path):
    h, iid = manual_env(policy, tmp_path)
    out = h.h.reschedule(iid, at(21), "maker", "supervisor", "after hours", now=at(9, 10))  # outside the window
    assert out["new"]["outcome"] == "REFUSED"
    status = h.lg.touch(iid)["status"]
    h.lg.close()
    assert status == "CANCELED", f"CANCELED -> {status} is not a declared transition"
