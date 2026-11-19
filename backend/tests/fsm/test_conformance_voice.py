"""K17 voice machines conformance (runs only when kairos_backend.voice is present, i.e. once
k17-k18/voice-engine is merged): disposition x confidence -> action, the payment-claim lifecycle,
and call-end webhook idempotency.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

import pytest

pytest.importorskip("kairos_backend.voice")

from kairos_backend.executor import Executor, FakeProvider  # noqa: E402
from kairos_backend.ledger import Instruction  # noqa: E402
from kairos_backend.machines import PAYMENT_CLAIM, VOICE_CALL, load_voice_thresholds, voice_disposition_machine  # noqa: E402
from kairos_backend.voice.dispositions import DISPOSITIONS, UNCLEAR, Disposition  # noqa: E402
from kairos_backend.voice.ingest import VoiceIngester, decide  # noqa: E402
from kairos_backend.voice.registry import load_registry, max_ptp_days  # noqa: E402
from tests.fsm.world import D, Coverage, add_borrower, at, new_ledger  # noqa: E402

THR = load_voice_thresholds()
REG = load_registry()
USE_CASES = [None] + sorted(REG["use_cases"])


def random_disposition(rng, when: datetime) -> Disposition:
    disp = rng.choice(list(DISPOSITIONS) + [UNCLEAR])
    conf = rng.choice([0.0, 0.3, 0.49, 0.5, 0.59, 0.6, 0.69, 0.7, 0.79, 0.8, 0.95, 1.0])
    ptp = "" if rng.random() < 0.2 else (when.date() + timedelta(days=rng.randrange(-2, 10))).isoformat()
    cb = "" if rng.random() < 0.2 else (when + timedelta(hours=rng.choice([-2, 1, 3, 10, 20, 26]))).isoformat()
    return Disposition(disp, conf, ptp_date=ptp, callback_at=cb)


@pytest.mark.parametrize("seed", range(10))
def test_decide_is_the_declared_disposition_machine(policy, seed):
    m = voice_disposition_machine(THR)
    rng = random.Random(seed)
    cov = Coverage()
    for _ in range(600):
        when = policy.local(datetime(2026, 11, 20, rng.randrange(8, 19), rng.choice((0, 30))))
        d = random_disposition(rng, when)
        band = rng.choice([None, "PRE_DUE", "B1", "B2", "B3", "B4"])
        uc = rng.choice(USE_CASES)
        ev = d.disposition if d.disposition in THR["rules"] else "UNCLEAR"
        ctx = {"disposition": ev, "confidence": d.confidence, "ptp_date": d.ptp_date, "callback_at": d.callback_at}
        if d.ptp_date:
            off = (date.fromisoformat(d.ptp_date) - when.date()).days
            ctx.update(date_in_past=off < 0, beyond_max_days=off > max_ptp_days(THR, band, uc))
        if d.callback_at:
            cb = policy.local(datetime.fromisoformat(d.callback_at))
            ctx.update(time_in_past=cb <= when, in_contact_window=policy.in_contact_window(cb.strftime("%H:%M")))
        state, t = m.step("RECEIVED", ev, ctx)
        cov.add(t)
        assert state == decide(d, band, uc, when, THR, policy).action, (d, band, uc)


def test_disposition_machine_alphabet_is_the_bot_schema():
    m = voice_disposition_machine(THR)
    assert set(m.events) == set(THR["rules"]) | {"UNCLEAR"}
    assert set(THR["rules"]) <= set(DISPOSITIONS)


# ---------- payment claims + webhook idempotency through the real ingester ----------
APP_EMI = REG["use_cases"]["EMI_REMINDER_EARLY"]["agent_id"]


def env(policy, path):
    lg = new_ledger(policy, path, budget_per_case_paise=100_000, end_days=20)
    lg.upsert_template(REG["use_cases"]["CALLBACK"].get("gate_template_id") or "VB_KAIROS_CALLBACK", "voicebot", "hi")
    ing = VoiceIngester(lg)
    return lg, ing, Executor(lg, {"voicebot": FakeProvider("voicebot")})


def call(lg, ex, bid, when):
    r = lg.reserve(Instruction(bid, f"{bid}-A-1", "C1", "voicebot", "VB_EN", when, 300))
    if r.reserved:
        ex.dispatch(r.instruction_id, when)
    return r.instruction_id if r.reserved else None


def claims(lg) -> dict:
    return {r["call_id"]: r["status"] for r in lg._conn().execute("SELECT call_id, status FROM voice_payment_claims")}


def calls(lg) -> dict:
    return {r["call_id"]: r["status"] for r in lg._conn().execute("SELECT call_id, status FROM voice_calls")}


def run_claims(policy, path, seed) -> Coverage:
    rng = random.Random(seed)
    lg, ing, ex = env(policy, path)
    voice = {"sms": 1, "whatsapp": 1, "email": 1, "ivr": 1, "voicebot": 1}
    for b in ("V1", "V2"):
        add_borrower(lg, b, dpd=12, consents=voice)
    claim_state, call_state, cov = {}, {}, Coverage()
    now, k = at(9), 0
    for _ in range(40):
        roll = rng.random()
        now += timedelta(hours=rng.choice([0, 1, 3, 20]))
        if now.hour < 8 or now.hour >= 19:
            now = at(9, 0, now.date() + timedelta(days=1))
        lg.start_day(now.date())
        if roll < 0.45:  # a call ends
            b = rng.choice(("V1", "V2"))
            iid = call(lg, ex, b, now) if rng.random() < 0.85 else "NO-SUCH-TOUCH"
            k += 1
            cid = f"CALL{k}" if rng.random() < 0.8 or k == 1 else f"CALL{rng.randrange(1, k)}"
            conf = rng.choice([0.3, 0.59, 0.6, 0.9])
            p = {"call_id": cid, "app_id": APP_EMI, "status": "completed", "duration_seconds": 40,
                 "ended_at": now.isoformat(), "variables": {"touch_id": iid, "disposition": "ALREADY_PAID",
                                                            "disposition_confidence": conf}}
            known = iid is not None and lg.touch(iid) is not None
            st = call_state.get(cid, "NEW")
            new, t = VOICE_CALL.step(st, "CALL_END", {"borrower_known": known})
            cov.add(t)
            out = ing.ingest(p)
            if new == "PROCESSING":
                new, t = VOICE_CALL.step(new, "PROCESSED")
                cov.add(t)
                if conf >= THR["rules"]["ALREADY_PAID"]["accept_at"]:
                    new_c, tc = PAYMENT_CLAIM.step(claim_state.get(cid, "NONE"), "CLAIM")
                    cov.add(tc)
                    claim_state[cid] = new_c
            assert out["status"] == {"DONE": "PROCESSED", "UNMATCHED": "UNMATCHED"}[new] if st == "NEW" else "DUPLICATE"
            call_state[cid] = new
        elif roll < 0.65:  # D7 / LMS verifies a payment
            b = rng.choice(("V1", "V2"))
            ob = lg._obligation(lg._conn(), f"{b}-A-1")
            due = ob["amount_due_paise"] - ob["amount_paid_paise"]
            if due > 0:
                k += 1
                lg.record_payment_verified(f"{b}-A-1", due if rng.random() < 0.6 else due // 2, now, f"P{k}", f"R{k}")
        else:  # the claim clock
            rows = {r["call_id"]: dict(r) for r in lg._conn().execute("SELECT * FROM voice_payment_claims")}
            for cid, r in rows.items():
                ob = lg._obligation(lg._conn(), r["obligation_id"]) if r["obligation_id"] else None
                ctx = {"emi_paid": ob is not None and ob["amount_paid_paise"] >= ob["amount_due_paise"],
                       "pause_over": lg._iso(now) >= r["pause_until"]}
                new_c, tc = PAYMENT_CLAIM.step(claim_state[cid], "RESOLVE", ctx)
                cov.add(tc)
                claim_state[cid] = new_c
            ing.resolve_payment_claims(now)
        assert claims(lg) == claim_state
        assert calls(lg) == call_state
        # a claim never pays anything: every paid EMI has a verified payment row behind it
        for r in lg._conn().execute("SELECT obligation_id, amount_paid_paise FROM obligations WHERE amount_paid_paise > 0"):
            paid = lg._conn().execute("SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE obligation_id=?"
                                      " AND reversed_on IS NULL", (r["obligation_id"],)).fetchone()[0]
            assert paid == r["amount_paid_paise"]
    lg.close()
    return cov


@pytest.mark.parametrize("seed", range(12))
def test_payment_claims_and_call_webhooks_follow_their_machines(policy, tmp_path, seed):
    run_claims(policy, tmp_path / "v.db", seed)


def test_every_claim_and_call_transition_is_exercised(policy, tmp_path):
    cov = Coverage()
    for seed in range(12):
        cov.fired.update(run_claims(policy, tmp_path / f"v{seed}.db", seed).fired)
    assert cov.missing(PAYMENT_CLAIM) == []
    # a crash mid-processing (row deleted for a retry) is covered by tests/test_voice_engine.py
    assert cov.missing(VOICE_CALL, exempt=[("PROCESSING", "ERROR"), ("PROCESSING", "CALL_END")]) == []
