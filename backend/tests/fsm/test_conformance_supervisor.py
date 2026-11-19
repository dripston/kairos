"""k06.supervisor.plan / .dispatch conformance: every Transition SupervisorFSM records is declared.

Seeded random borrowers (holds, STOP, payments, consent gaps, filled caps, pauses), random candidate
sets (outside the window, slot mismatch, unapproved templates, empty) and learners (normal, WAIT,
rogue). Each recorded (from, event, to) is replayed through the declared machine with the recorded
rule as the guard fact. Then a random interleaving of STOP / payment / pause before dispatch.
"""
from __future__ import annotations

import random
from datetime import datetime

import pytest

from kairos_backend.contracts import RecordingSink
from kairos_backend.exceptions import ExceptionEvent
from kairos_backend.executor import Executor, FakeProvider
from kairos_backend.machines import SUPERVISOR_DISPATCH, SUPERVISOR_PLAN
from kairos_backend.supervisor import SupervisorFSM
from tests.fsm.world import CHANNELS, D, TableLearner, add_borrower, at, candidate, new_ledger, random_consents
from tests.fsm.world import Coverage

SEEDS = range(30)


def replay(machine, transitions, cov: Coverage):
    state = None
    for tr in transitions:
        src = tr.frm or "NEW"
        assert state in (None, src), f"recorded path jumps from {state} to {src}"
        new, fired = machine.step(src, tr.event, {"rule": tr.detail.get("rule")})
        assert new == tr.to, (tr, fired)
        cov.add(fired)
        state = new
    assert state in machine.terminal
    return state


def scenario(lg, rng: random.Random, bid: str) -> None:
    """Put the borrower in a random situation using the real ledger APIs."""
    p = lambda x: rng.random() < x  # noqa: E731
    if p(0.08):
        lg.record_stop(bid, at(8, 5), rng.choice(CHANNELS))
        if p(0.5):
            lg.opt_in(bid, "sms", "v2", at(8, 10))
    if p(0.25):
        ev = rng.choice(["PTP_OFFERED", "HARDSHIP", "HARDSHIP", "CALLBACK_REQUEST", "ABUSIVE", "NEEDS_TIME", "DISPUTE"])
        off = rng.choice([None, 1, 2, 6]) if ev in ("PTP_OFFERED", "HARDSHIP") else None
        lg.apply_exception(bid, ExceptionEvent(ev, "B1", date_offset_days=off), at(8, 15), f"{bid}-A-1")
    if p(0.08):
        lg.record_payment_verified(f"{bid}-A-1", 500_000, at(8, 20), f"PAY-{bid}", f"REF-{bid}")
    if p(0.1):
        lg.record_response(bid, at(8, 25), "sms")
    for _ in range(rng.choice([0, 0, 0, 2, 3])):  # fill caps with live touches
        from kairos_backend.ledger import Instruction
        h = rng.randrange(9, 18)
        lg.reserve(Instruction(bid, f"{bid}-B-1", "C1", "sms", "SMS_EN", at(h, rng.choice((0, 30))), 20))


def random_candidates(rng: random.Random):
    out = []
    for _ in range(rng.choice([0, 1, 2, 3, 3, 4])):
        ch = rng.choice(CHANNELS)
        t = at(rng.choice([7, 9, 10, 12, 15, 18, 19, 20]), rng.choice((0, 30)))
        slot = rng.choice([None, None, None, "17-19"])  # sometimes a slot that does not match the time
        tpl = rng.choice(["default"] * 8 + [None, "SMS_EN"])
        out.append(candidate(ch, t, slot, tpl))
    return out


@pytest.mark.parametrize("seed", SEEDS)
def test_plan_and_dispatch_take_only_declared_transitions(policy, tmp_path, seed):
    rng = random.Random(seed)
    lg = new_ledger(policy, tmp_path / "sup.db", budget_per_case_paise=rng.choice([10_000, 10_000, 30]))
    providers = {ch: FakeProvider(ch, script=lambda t, r=rng: r.choice(["ACCEPTED", "ACCEPTED", "REJECTED", "UNKNOWN_LOST"]))
                 for ch in ("sms", "whatsapp", "email", "ivr")}
    sink = RecordingSink()
    cov_p, cov_d = Coverage(), Coverage()
    for i in range(12):
        bid = f"S{seed}x{i}"
        add_borrower(lg, bid, dpd=rng.choice([-5, 3, 12, 12, 45, 75, 100, 200]), consents=random_consents(rng))
        scenario(lg, rng, bid)
        if rng.random() < 0.05:
            lg.set_outbound_paused(True)
        learner = TableLearner(force_wait=rng.random() < 0.1, rogue=rng.random() < 0.1)
        fsm = SupervisorFSM(lg, Executor(lg, providers), learner, sink)
        r = fsm.plan(bid, f"{bid}-A-1", "C1", rng.choice([None, "B1"]), random_candidates(rng), at(8, 30))
        assert replay(SUPERVISOR_PLAN, r.transitions, cov_p) == "DECIDED"
        assert r.decision == r.transitions[-1].event
        if r.decision == "SEND":
            assert lg.touch(r.instruction_id)["status"] == "RESERVED"
            # something may happen before the send time
            roll = rng.random()
            if roll < 0.15:
                lg.record_stop(bid, at(9), "whatsapp")
            elif roll < 0.25:
                lg.record_payment_verified(f"{bid}-A-1", 500_000, at(9), f"PAYD-{bid}", f"REFD-{bid}")
            elif roll < 0.3:
                lg.set_outbound_paused(True)
            when = datetime.fromisoformat(r.send_at)
            if rng.random() < 0.1:
                when = when.replace(hour=19, minute=30)  # after the window: blocked at send time
            out = fsm.dispatch(r.instruction_id, when)
            assert replay(SUPERVISOR_DISPATCH, out.transitions, cov_d) in SUPERVISOR_DISPATCH.terminal
            if rng.random() < 0.3:  # a second dispatch of the same instruction is never a second send
                again = fsm.dispatch(r.instruction_id, when)
                assert replay(SUPERVISOR_DISPATCH, again.transitions, cov_d) in ("SKIPPED", "BLOCKED")
        lg.set_outbound_paused(False)
    assert lg.cap_breaches() == []
    lg.close()


def test_every_supervisor_transition_is_exercised(policy, tmp_path):
    """Across the seeds, the real SupervisorFSM takes every declared transition except the reserve races,
    which need two writers interleaving between preview and reserve (tests/test_supervisor_fsm.py)."""
    cov_p, cov_d = Coverage(), Coverage()
    for seed in SEEDS:
        rng = random.Random(seed)
        lg = new_ledger(policy, tmp_path / f"cov{seed}.db", budget_per_case_paise=rng.choice([10_000, 10_000, 30]))
        providers = {ch: FakeProvider(ch, script=lambda t, r=rng: r.choice(["ACCEPTED", "REJECTED", "UNKNOWN_LOST"]))
                     for ch in ("sms", "whatsapp", "email", "ivr")}
        for i in range(12):
            bid = f"C{seed}x{i}"
            add_borrower(lg, bid, dpd=rng.choice([-5, 3, 12, 12, 45, 75, 100, 200]), consents=random_consents(rng))
            scenario(lg, rng, bid)
            fsm = SupervisorFSM(lg, Executor(lg, providers), TableLearner(force_wait=rng.random() < 0.1,
                                                                          rogue=rng.random() < 0.1), RecordingSink())
            r = fsm.plan(bid, f"{bid}-A-1", "C1", rng.choice([None, "B1"]), random_candidates(rng), at(8, 30))
            replay(SUPERVISOR_PLAN, r.transitions, cov_p)
            if r.decision == "SEND":
                when = datetime.fromisoformat(r.send_at)
                if rng.random() < 0.2:
                    lg.record_stop(bid, at(9), "sms")
                replay(SUPERVISOR_DISPATCH, fsm.dispatch(r.instruction_id, when).transitions, cov_d)
                replay(SUPERVISOR_DISPATCH, fsm.dispatch(r.instruction_id, when).transitions, cov_d)
        lg.close()
    # directed: STOP then a per-channel opt-in -> the other channels CANCEL at ELIGIBILITY
    lg = new_ledger(policy, tmp_path / "directed.db")
    add_borrower(lg, "Z1")
    lg.record_stop("Z1", at(8), "whatsapp")
    lg.opt_in("Z1", "sms", "v2", at(8, 1))
    fsm = SupervisorFSM(lg, Executor(lg, {}), TableLearner(), RecordingSink())
    r = fsm.plan("Z1", "Z1-A-1", "C1", None, [candidate("sms", at(20)), candidate("whatsapp", at(10))], at(8, 30))
    assert (r.decision, r.rule) == ("CANCEL", "STOP_OPT_OUT") and r.states[-2] == "ELIGIBILITY"
    replay(SUPERVISOR_PLAN, r.transitions, cov_p)
    lg.close()
    races = [("RESERVE", "WAIT"), ("RESERVE", "CANCEL"), ("RESERVE", "HANDOFF")]
    assert cov_p.missing(SUPERVISOR_PLAN, exempt=races) == []
    assert cov_d.missing(SUPERVISOR_DISPATCH) == []
