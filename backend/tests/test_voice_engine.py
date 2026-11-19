"""K17/K18 voice engine: bot registry (select_bot), unified disposition schema, confidence thresholds
(both sides of every boundary), ledger effects, idempotency, learner reward, CRM envelope, API.

All data is synthetic; phone numbers are all-zero placeholders. SIMULATED."""
from __future__ import annotations

import copy
import importlib
import json
import re
from datetime import date, datetime, timedelta

import pytest

from kairos_backend.contracts import RecordingSink, slot_of
from kairos_backend.executor import Executor, FakeProvider
from kairos_backend.learner import BetaBernoulliLearner
from kairos_backend.ledger import Instruction, Ledger
from kairos_backend.outcomes import OutcomeFinalizer
from kairos_backend.voice.dispositions import (DISPOSITIONS, UNCLEAR, parse_confidence, parse_disposition,
                                               report_level, scrub_text)
from kairos_backend.voice.ingest import VoiceIngester, decide, normalize_payload
from kairos_backend.voice.registry import (ab_arm, check_variables, first_name, inputs_from_ledger, load_registry,
                                           load_thresholds, select_bot, spoken_date)
from tests.spine_helpers import add_loan

D = date(2026, 11, 20)            # Friday, a contact day
VOICE_TPL = "VB_KAIROS_CALLBACK"  # registry CALLBACK gate_template_id
APP = {"EMI": "Kairos---EM-15f2766f-4eba", "PTP_FU": "Kairos---PT-07a93e21-1a9b"}
VOICE_CONSENT = {"sms": 1, "whatsapp": 1, "email": 1, "ivr": 1, "voicebot": 1}
FAKE_PHONE = "+91 00000 00000"


def at(hh, mm=0, day=D):
    return datetime(day.year, day.month, day.day, hh, mm)


REG = load_registry()
THR = load_thresholds()


# =====================================================================================
# fixtures
# =====================================================================================
@pytest.fixture
def env(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "voice.db")
    lg.upsert_campaign("C1", budget_per_case_paise=100_000, start_date=D, end_date=D + timedelta(days=20))
    lg.upsert_template(VOICE_TPL, "voicebot", "hi")
    lg.upsert_template("SMS_EN", "sms", "en")
    lg.start_day(D)
    learner = BetaBernoulliLearner(lg)
    fin = OutcomeFinalizer(lg, learner)
    sink = RecordingSink()
    ing = VoiceIngester(lg, finalizer=fin, sink=sink)
    ex = Executor(lg, {"voicebot": FakeProvider("voicebot")})

    class Env:
        pass

    e = Env()
    e.lg, e.fin, e.sink, e.ing, e.ex, e.policy = lg, fin, sink, ing, ex, policy
    yield e
    lg.close()


def place_call(e, bid, dpd=12, hh=10):
    """Borrower with a loan `dpd` days past due, a voicebot touch reserved, dispatched (SENT) and registered."""
    ob = add_loan(e.lg, bid, D, dpd=dpd, consents=VOICE_CONSENT, campaign="C1")
    r = e.lg.reserve(Instruction(bid, ob, "C1", "voicebot", VOICE_TPL, at(hh), 300))
    assert r.reserved, r.decision.as_dict()
    assert e.ex.dispatch(r.instruction_id).outcome == "SENT"
    band = e.policy.band_for(dpd)
    e.fin.register_execution(r.instruction_id, bid, band, "voicebot", slot_of(at(hh)), at(hh))
    return ob, r.instruction_id


def payload(call_id, touch_id, disposition, conf, app="EMI", ended=None, status="completed", duration=45, **extra):
    v = {"touch_id": touch_id, "disposition": disposition, "disposition_confidence": conf, **extra}
    return {"call_id": call_id, "app_id": APP[app], "status": status, "duration_seconds": duration,
            "ended_at": (ended or at(10, 5)).isoformat(), "variables": v}


def synced(e, day):
    """The daily LMS refresh for `day` (K01 stale-DPD rule): previews on later days need it."""
    e.lg.mark_spine_synced(datetime.combine(day, datetime.min.time()))


def state(e, bid):
    return e.lg._state(e.lg._conn(), bid)


def tickets(e, bid):
    return [dict(r) for r in e.lg._conn().execute("SELECT * FROM tickets WHERE borrower_id=?", (bid,))]


def consent(e, bid, ch):
    return dict(e.lg._conn().execute("SELECT * FROM consents WHERE borrower_id=? AND channel=?", (bid, ch)).fetchone())


def outcome_row(e, iid):
    return e.fin.row(iid)


# =====================================================================================
# 1. bot registry / select_bot
# =====================================================================================
def borrower(**kw):
    b = {"borrower_id": "BRW-1", "full_name": "Asha Kulkarni", "language": "en", "state": None}
    b.update(kw)
    return b


def ob(band="B1", dpd=12, due="2026-11-05"):
    return {"obligation_id": "OB-1", "due_date": due, "band": band, "dpd": dpd}


CTX = {"as_of": "2026-11-19", "touch_id": "INS-0a1b2c3d4e5f6a7b"}


def test_registry_has_six_use_cases_with_real_agents_and_known_voices():
    assert set(REG["use_cases"]) == {"PRE_DUE_REMINDER", "EMI_REMINDER_EARLY", "CALLBACK", "MID_BUCKET_RESOLUTION",
                                     "PTP_FOLLOW_UP", "HARDSHIP_TRIAGE"}
    assert REG["use_cases"]["EMI_REMINDER_EARLY"]["agent_id"] == "Kairos---EM-15f2766f-4eba"
    assert REG["use_cases"]["CALLBACK"]["agent_id"] == "Kairos---Ca-f080ffa8-2d1d"
    assert all(s["agent_id"] and s["agent_id"] != "TBD" for s in REG["use_cases"].values())
    assert REG["voices"]["Ritu"]["internal_id"] == "01a0cf16-5817-79d0-bf32-972e399087db"
    assert REG["voices"]["Advait"]["internal_id"] == "01a0cf16-73d4-78aa-a416-c593287484db"
    assert REG["experiment"]["enabled"] is False  # A/B off by default


@pytest.mark.parametrize("band,use_case,voice", [
    ("PRE_DUE", "PRE_DUE_REMINDER", "Ritu"), ("B1", "EMI_REMINDER_EARLY", "Ritu"),
    ("B2", "MID_BUCKET_RESOLUTION", "Advait"), ("B3", "MID_BUCKET_RESOLUTION", "Advait")])
def test_use_case_by_band(band, use_case, voice):
    s = select_bot(borrower(), ob(band), CTX)
    assert (s["use_case"], s["voice"], s["ready"]) == (use_case, voice, True)
    assert s["agent_id"] == REG["use_cases"][use_case]["agent_id"]


def test_b4_and_missing_band_get_no_bot():
    assert select_bot(borrower(), ob("B4", 120), CTX)["use_case"] is None
    s = select_bot(borrower(), {"obligation_id": "OB-1", "due_date": "2026-11-05"}, CTX)
    assert s["use_case"] is None and "NO_BAND" in s["reason"] and s["variables"] == {}


def test_context_precedence_hardship_needs_human_approval():
    s = select_bot(borrower(), ob(), {**CTX, "hardship": True, "callback_requested": True})
    assert s["use_case"] is None and "HARDSHIP_NEEDS_HUMAN_APPROVAL" in s["reason"]
    s = select_bot(borrower(), ob(), {**CTX, "hardship": True, "human_approved": True})
    assert s["use_case"] == "HARDSHIP_TRIAGE" and s["voice"] == "Advait" and s["requires_human_approval"]
    assert s["variables"]["max_ptp_date"] == ""


def test_callback_and_ptp_follow_up_extras():
    s = select_bot(borrower(), ob("B2", 40), {**CTX, "callback_requested": True})
    assert s["use_case"] == "CALLBACK" and s["voice"] == "Advait"
    assert s["variables"]["callback_reason"] == "you asked us to call you back"
    s = select_bot(borrower(), ob("B2", 40), {**CTX, "ptp_due": True, "promise_date": "2026-11-18"})
    assert s["use_case"] == "PTP_FOLLOW_UP" and s["voice"] == "Ritu"
    assert s["variables"]["promise_date"] == "18 November"
    assert s["variables"]["max_ptp_date"] == "22 November"  # PTP_FOLLOW_UP: 3 days regardless of band


def test_call_start_variables_exact_and_spoken():
    s = select_bot(borrower(), ob("B2", 40), CTX)
    assert set(s["variables"]) == {"user_name", "lender_name", "emi_due_date", "touch_id", "preferred_language",
                                   "call_date", "max_ptp_date", "overdue_days"}
    v = s["variables"]
    assert v["user_name"] == "Asha" and v["emi_due_date"] == "5 November" and v["call_date"] == "2026-11-19"
    assert v["max_ptp_date"] == "24 November"  # B2: 5 days
    assert v["overdue_days"] == "40"
    assert select_bot(borrower(), ob("B1"), CTX)["variables"]["max_ptp_date"] == "26 November"  # B1: 7
    assert select_bot(borrower(), ob("B3", 70), CTX)["variables"]["max_ptp_date"] == "22 November"  # B3: 3
    assert "overdue_days" not in select_bot(borrower(), ob("B1"), CTX)["variables"]


def test_helpers():
    assert spoken_date("2026-11-05") == "5 November" and spoken_date("bad") == "" and spoken_date(None) == ""
    assert first_name("Dr. Asha Kulkarni") == "Asha" and first_name("") == "" and first_name("R2D2 X") == "RD"


# ---------- language ----------
@pytest.mark.parametrize("st,lang", [
    ("Maharashtra", "Marathi"), ("MH", "Marathi"), ("Tamil Nadu", "Tamil"), ("KA", "Kannada"),
    ("Andhra Pradesh", "Telugu"), ("Telangana", "Telugu"), ("TG", "Telugu"), ("West Bengal", "Bengali"),
    ("Gujarat", "Gujarati"), ("Bihar", "Hindi"), ("Delhi", "Hindi"), (None, "Hindi"),
    ("Kerala", "Hindi"), ("Punjab", "Hindi"), ("Odisha", "Hindi")])  # last three: mapped but unsupported
def test_language_from_state_map_and_fallback(st, lang):
    s = select_bot(borrower(state=st), ob(), CTX)
    assert s["language"] == lang and s["variables"]["preferred_language"] == lang
    assert s["language"] in REG["supported_languages"]


def test_language_preference_and_unsupported():
    # spine default 'en' is NOT an English opt-in
    assert select_bot(borrower(language="en", state="MH"), ob(), CTX)["language"] == "Marathi"
    # explicit English opt-in
    assert select_bot(borrower(state="MH", preferred_language="English"), ob(), CTX)["language"] == "English"
    assert select_bot(borrower(state="MH"), ob(), {**CTX, "preferred_language": "en-IN"})["language"] == "English"
    # a real spine language code other than the default is honoured
    assert select_bot(borrower(language="ta", state="MH"), ob(), CTX)["language"] == "Tamil"
    # supported preference beats the state map
    assert select_bot(borrower(state="TN", preferred_language="gu"), ob(), CTX)["language"] == "Gujarati"
    # unsupported preference -> Hindi (not the state language)
    s = select_bot(borrower(state="MH", preferred_language="Malayalam"), ob(), CTX)
    assert s["language"] == "Hindi" and "unsupported" in s["reasons"]["language"]
    s = select_bot(borrower(preferred_language="Klingon"), ob(), CTX)
    assert s["language"] == "Hindi"
    # observed language (from an earlier LANGUAGE_BARRIER call) beats the state map, not a preference
    assert select_bot(borrower(state="MH", observed_language="Bengali"), ob(), CTX)["language"] == "Bengali"


# ---------- voice ----------
def test_voice_lender_override_and_borrower_preference():
    reg = copy.deepcopy(REG)
    reg["lender_overrides"] = {"L1": {"display_name": "Example Finance", "voices": {"EMI_REMINDER_EARLY": "Advait"}}}
    s = select_bot(borrower(lender_id="L1"), ob(), CTX, registry=reg)
    assert s["voice"] == "Advait" and s["reasons"]["voice"] == "lender override"
    assert s["voice_id"] == REG["voices"]["Advait"]["internal_id"]
    assert s["variables"]["lender_name"] == "Example Finance"
    s = select_bot(borrower(lender_id="L1", preferred_voice="Ritu"), ob(), CTX, registry=reg)
    assert s["voice"] == "Ritu" and s["reasons"]["voice"] == "borrower preference"
    # unknown voice names are ignored, never sent
    assert select_bot(borrower(preferred_voice="Nobody"), ob(), CTX)["voice"] == "Ritu"


def test_ab_experiment_deterministic_and_off_by_default():
    ids = [f"BRW-{i}" for i in range(200)]
    assert {select_bot(borrower(borrower_id=i), ob(), CTX)["voice"] for i in ids} == {"Ritu"}  # off
    reg = copy.deepcopy(REG)
    reg["experiment"]["enabled"] = True
    first = [select_bot(borrower(borrower_id=i), ob(), CTX, registry=reg)["voice"] for i in ids]
    again = [select_bot(borrower(borrower_id=i), ob(), CTX, registry=reg)["voice"] for i in ids]
    assert first == again and set(first) == {"Ritu", "Advait"}
    assert first == [ab_arm(i, "voice-ab-001", ["Ritu", "Advait"]) for i in ids]
    # use cases outside the experiment keep their default
    assert select_bot(borrower(borrower_id="BRW-1"), ob("B2", 40), CTX, registry=reg)["voice"] == "Advait"
    # borrower preference beats the experiment
    assert all(select_bot(borrower(borrower_id=i, preferred_voice="Advait"), ob(), CTX, registry=reg)["voice"]
               == "Advait" for i in ids[:20])


# ---------- variable safety ----------
DIGITS3 = re.compile(r"\d{3,}")


def _assert_no_pii(variables):
    for k, v in variables.items():
        assert isinstance(v, str)
        assert "₹" not in v and "@" not in v and "+9" not in v, (k, v)
        if k not in ("call_date", "touch_id"):
            assert not DIGITS3.search(v), (k, v)


@pytest.mark.parametrize("ctx", [
    {}, {"callback_requested": True, "callback_reason": f"call me on {FAKE_PHONE} about Rs 5000"},
    {"ptp_due": True, "promise_date": "2026-11-18"}, {"hardship": True, "human_approved": True}])
@pytest.mark.parametrize("band", ["PRE_DUE", "B1", "B2", "B3"])
def test_variables_never_carry_amounts_or_phones(band, ctx):
    b = borrower(full_name="Ravi 0000000000 Kumar", phone="0000000000", email="x@example.com")
    o = {**ob(band, 40), "amount_due_paise": 500_000, "loan_id": "LN-123456"}
    s = select_bot(b, o, {**CTX, **ctx, "lender_name": "Finco ₹50000 call +910000000000", "amount": 5000})
    assert s["use_case"] is not None
    _assert_no_pii(s["variables"])
    assert s["variables"]["user_name"] == "Ravi"
    allowed = set(REG["call_start_variables"]) | {"overdue_days", "promise_date", "callback_reason"}
    assert set(s["variables"]) <= allowed
    assert not any(k in s["variables"] for k in ("amount", "phone", "email", "loan_id", "amount_due_paise"))


def test_check_variables_blocks_and_select_blanks_unsafe_touch_id():
    bad = check_variables({"user_name": "Asha 00000", "lender_name": "Rs 500 due", "emi_due_date": "5 November",
                           "touch_id": "0000000000", "amount": "5000", "call_date": "19-11-2026"}, REG)
    assert set(bad) == {"user_name", "lender_name", "touch_id", "amount", "call_date"}
    s = select_bot(borrower(), ob(), {**CTX, "touch_id": "+910000000000"})
    assert s["variables"]["touch_id"] == "" and "touch_id" in s["blocked_variables"]


# =====================================================================================
# 2. disposition schema
# =====================================================================================
def test_parse_disposition_strict():
    d = parse_disposition({"disposition": "PTP", "disposition_confidence": "0.83", "ptp_date": "2026-11-25",
                           "ptp_amount_type": "full", "callback_at": "", "hardship_type": "", "language_used": "Hindi",
                           "needs_human": "false", "call_summary": f"Will pay ₹5,000 call {FAKE_PHONE}",
                           "rule_path": "greet>ask>ptp"})
    assert (d.disposition, d.confidence, d.ptp_date, d.ptp_amount_type) == ("PTP", 0.83, "2026-11-25", "FULL")
    assert d.needs_human is False and d.hardship_type == "NONE" and d.rule_path == "greet>ask>ptp"
    assert "5,000" not in d.call_summary and "00000" not in d.call_summary


@pytest.mark.parametrize("raw", ["MAYBE_PAY", "", None, "stop please", 42])
def test_unknown_enum_is_unclear_with_zero_confidence(raw):
    d = parse_disposition({"disposition": raw, "disposition_confidence": 0.99})
    assert d.disposition == UNCLEAR and d.confidence == 0.0 and "UNKNOWN_DISPOSITION" in d.errors


@pytest.mark.parametrize("v,exp", [("0.5", 0.5), (0.49, 0.49), ("1", 1.0), ("abc", 0.0), (None, 0.0),
                                   ("1.2", 0.0), (-0.1, 0.0), ("nan", 0.0), (True, 0.0), (" 0.7 ", 0.7)])
def test_confidence_parsing(v, exp):
    assert parse_confidence(v) == exp


def test_bad_dates_become_empty():
    d = parse_disposition({"disposition": "PTP", "disposition_confidence": 0.9, "ptp_date": "25/11/2026",
                           "callback_at": "tomorrow 5pm"})
    assert d.ptp_date == "" and d.callback_at == "" and {"BAD_PTP_DATE", "BAD_CALLBACK_AT"} <= set(d.errors)
    assert parse_disposition({"disposition": "PTP", "ptp_date": "2026-02-30"}).ptp_date == ""
    assert parse_disposition({"disposition": "HARDSHIP", "hardship_type": "weird"}).hardship_type == "OTHER"
    assert parse_disposition({"disposition": "HARDSHIP", "hardship_type": "job loss"}).hardship_type == "JOB_LOSS"


def test_report_ordinal():
    labels = [report_level(False, None, None, None)[1], report_level(True, False, None, "NO_RESPONSE")[1],
              report_level(True, True, 5, "NO_RESPONSE")[1], report_level(True, True, 40, UNCLEAR)[1],
              report_level(True, True, 40, "PTP")[1]]
    assert labels == ["No data", "Sent", "Delivered", "Read", "Responded"]
    assert [report_level(True, True, 40, "PTP")[0], report_level(False, None, None, None)[0]] == [5, 0]


def test_scrub_text():
    assert scrub_text("pay Rs. 4,500 to a@b.co or 0000000000") == "pay [amount] to [email] or [number]"


# =====================================================================================
# 3. thresholds — pure decide()
# =====================================================================================
def _d(disp, conf, **kw):
    return parse_disposition({"disposition": disp, "disposition_confidence": conf, **kw})


def test_thresholds_file_is_a_versioned_draft_covering_every_disposition():
    assert THR["status"] == "DRAFT" and THR["policy_version"].endswith("draft")
    assert set(THR["rules"]) == set(DISPOSITIONS)
    assert THR["min_confidence_any"] == 0.6


@pytest.mark.parametrize("disp,cut,acc,below", [
    ("STOP_REQUESTED", 0.5, "RECORD_STOP_ALL_CHANNELS", "SUPERVISOR_HOLD_AND_REVIEW"),
    ("HARDSHIP", 0.5, "HARDSHIP_HOLD_AND_HANDOFF", "REVIEW"),
    ("ABUSIVE", 0.6, "K13_HOLD_AND_HANDOFF", "REVIEW"),
    ("DISPUTE", 0.6, "K13_HOLD_AND_HANDOFF", "REVIEW"),
    ("WRONG_PERSON", 0.8, "STOP_VOICE_MARK_UNVERIFIED", "REVIEW"),
    ("THIRD_PARTY", 0.8, "STOP_VOICE_MARK_UNVERIFIED", "REVIEW"),
    ("ALREADY_PAID", 0.6, "PAYMENT_CLAIM_PENDING", "REVIEW")])
def test_decide_boundaries(policy, disp, cut, acc, below):
    hi = decide(_d(disp, cut), "B1", None, at(10), THR, policy)
    lo = decide(_d(disp, round(cut - 0.01, 2)), "B1", None, at(10), THR, policy)
    assert (hi.action, hi.accepted, hi.rule) == (acc, True, f"K17.{disp}.ACCEPT")
    assert (lo.action, lo.accepted, lo.rule) == (below, False, f"K17.{disp}.BELOW")


@pytest.mark.parametrize("disp", ["PAY_NOW_LINK", "NEEDS_TIME_NO_DATE", "REFUSED_TO_PAY", "HUMAN_REQUESTED",
                                  "LANGUAGE_BARRIER"])
def test_uncovered_below_min_confidence_is_unclear(policy, disp):
    lo = decide(_d(disp, 0.59), "B1", None, at(10), THR, policy)
    assert (lo.effective, lo.action) == (UNCLEAR, "REVIEW")
    assert decide(_d(disp, 0.6), "B1", None, at(10), THR, policy).accepted


@pytest.mark.parametrize("band,days,ok", [("PRE_DUE", 7, True), ("PRE_DUE", 8, False), ("B1", 7, True),
                                          ("B1", 8, False), ("B2", 5, True), ("B2", 6, False), ("B3", 3, True),
                                          ("B3", 4, False), ("B4", 3, True), ("B4", 4, False), (None, 3, True),
                                          (None, 4, False)])
def test_ptp_max_days_by_band(policy, band, days, ok):
    v = decide(_d("PTP", 0.9, ptp_date=(D + timedelta(days=days)).isoformat()), band, None, at(10), THR, policy)
    assert v.accepted is ok
    assert v.effective == ("PTP" if ok else "NEEDS_TIME_NO_DATE")
    if not ok:
        assert v.rule == "K17.PTP.DOWNGRADE.BEYOND_MAX_DAYS" and v.action == "DOWNGRADE_NEEDS_TIME_NO_DATE"


def test_ptp_downgrades(policy):
    later = (D + timedelta(days=2)).isoformat()
    assert decide(_d("PTP", 0.8, ptp_date=later), "B1", None, at(10), THR, policy).accepted
    low = decide(_d("PTP", 0.79, ptp_date=later), "B1", None, at(10), THR, policy)
    assert (low.effective, low.rule) == ("NEEDS_TIME_NO_DATE", "K17.PTP.DOWNGRADE.LOW_CONFIDENCE")
    assert decide(_d("PTP", 0.95), "B1", None, at(10), THR, policy).rule == "K17.PTP.DOWNGRADE.NO_DATE"
    past = decide(_d("PARTIAL_PTP", 0.95, ptp_date=(D - timedelta(days=1)).isoformat()), "B1", None, at(10), THR, policy)
    assert past.rule == "K17.PARTIAL_PTP.DOWNGRADE.DATE_IN_PAST"
    # a PTP_FOLLOW_UP call allows only 3 days whatever the band
    four = (D + timedelta(days=4)).isoformat()
    assert decide(_d("PTP", 0.9, ptp_date=four), "B1", None, at(10), THR, policy).accepted
    assert not decide(_d("PTP", 0.9, ptp_date=four), "B1", "PTP_FOLLOW_UP", at(10), THR, policy).accepted


def test_callback_decide(policy):
    ok = decide(_d("CALLBACK_REQUESTED", 0.7, callback_at=at(11, 0, D + timedelta(days=1)).isoformat()),
                "B1", None, at(10), THR, policy)
    assert ok.action == "SCHEDULE_CALLBACK"
    lo = decide(_d("CALLBACK_REQUESTED", 0.69, callback_at=at(11, 0, D + timedelta(days=1)).isoformat()),
                "B1", None, at(10), THR, policy)
    assert (lo.action, lo.rule) == ("REVIEW", "K17.CALLBACK_REQUESTED.BELOW")
    late = decide(_d("CALLBACK_REQUESTED", 0.9, callback_at=at(20, 0).isoformat()), "B1", None, at(10), THR, policy)
    assert late.rule == "K17.CALLBACK_REQUESTED.OUTSIDE_WINDOW" and late.action == "REVIEW"
    early = decide(_d("CALLBACK_REQUESTED", 0.9, callback_at=at(7, 59, D + timedelta(days=1)).isoformat()),
                   "B1", None, at(10), THR, policy)
    assert early.rule == "K17.CALLBACK_REQUESTED.OUTSIDE_WINDOW"
    assert decide(_d("CALLBACK_REQUESTED", 0.9), "B1", None, at(10), THR, policy).rule == "K17.CALLBACK_REQUESTED.NO_TIME"


# =====================================================================================
# 4. ingestion — ledger effects on both sides of each boundary
# =====================================================================================
def test_stop_at_050_records_stop_all_channels(env):
    _, iid = place_call(env, "B-STOP")
    r = env.ing.ingest(payload("call-s1", iid, "STOP_REQUESTED", "0.5"))
    assert r["action"] == "RECORD_STOP_ALL_CHANNELS" and r["rule"] == "K17.STOP_REQUESTED.ACCEPT"
    st = state(env, "B-STOP")
    assert st["stopped"] == 1 and st["supervisor_hold"] == 0
    assert all(consent(env, "B-STOP", ch)["opted_out"] == 1 for ch in VOICE_CONSENT)


def test_stop_at_049_is_supervisor_hold_and_review_never_keeps_calling(env):
    ob_id, iid = place_call(env, "B-MAYBE")
    # something queued for tomorrow must be canceled by the hold
    synced(env, D + timedelta(days=1))
    q = env.lg.reserve(Instruction("B-MAYBE", ob_id, "C1", "voicebot", VOICE_TPL, at(12, 0, D + timedelta(days=1)), 300))
    assert q.reserved
    r = env.ing.ingest(payload("call-s2", iid, "STOP_REQUESTED", 0.49))
    assert r["action"] == "SUPERVISOR_HOLD_AND_REVIEW" and r["rule"] == "K17.STOP_REQUESTED.BELOW"
    st = state(env, "B-MAYBE")
    assert st["stopped"] == 0 and st["supervisor_hold"] == 1
    assert env.lg.touch(q.instruction_id)["status"] == "CANCELED"
    assert any(t["type"] == "HUMAN_HANDOFF" for t in tickets(env, "B-MAYBE"))
    nxt = env.lg.preview(Instruction("B-MAYBE", ob_id, "C1", "sms", None, at(11, 0, D + timedelta(days=1)), 20))
    assert nxt.reason == "SUPERVISOR_HOLD"


def test_hardship_050_hold_and_handoff_vs_049_review(env):
    _, iid = place_call(env, "B-H1")
    r = env.ing.ingest(payload("call-h1", iid, "HARDSHIP", 0.5, hardship_type="MEDICAL"))
    st = state(env, "B-H1")
    assert r["action"] == "HARDSHIP_HOLD_AND_HANDOFF" and st["hardship_until"] and st["supervisor_hold"] == 1
    assert r["effects"]["handoff"]["via"] == "ledger"
    _, iid2 = place_call(env, "B-H2")
    r2 = env.ing.ingest(payload("call-h2", iid2, "HARDSHIP", 0.49))
    st2 = state(env, "B-H2")
    assert r2["action"] == "REVIEW" and st2["hardship_until"] is None and st2["supervisor_hold"] == 0
    assert [t["type"] for t in tickets(env, "B-H2")] == ["VOICE_REVIEW_HARDSHIP"]


@pytest.mark.parametrize("disp", ["ABUSIVE", "DISPUTE"])
def test_abusive_dispute_060_k13_and_handoff_vs_059_review(env, disp):
    _, iid = place_call(env, f"B-{disp}-hi")
    r = env.ing.ingest(payload(f"call-{disp}-hi", iid, disp, 0.6))
    assert r["action"] == "K13_HOLD_AND_HANDOFF"
    assert state(env, f"B-{disp}-hi")["supervisor_hold"] == 1
    assert {t["type"] for t in tickets(env, f"B-{disp}-hi")} == {disp}  # K13 ticket is the handoff case
    _, iid2 = place_call(env, f"B-{disp}-lo")
    r2 = env.ing.ingest(payload(f"call-{disp}-lo", iid2, disp, 0.59))
    assert r2["action"] == "REVIEW" and state(env, f"B-{disp}-lo")["supervisor_hold"] == 0


@pytest.mark.parametrize("disp", ["WRONG_PERSON", "THIRD_PARTY"])
def test_wrong_person_080_stops_voice_only_vs_079_review(env, disp):
    ob_id, iid = place_call(env, f"B-{disp}")
    r = env.ing.ingest(payload(f"call-{disp}", iid, disp, 0.8))
    assert r["action"] == "STOP_VOICE_MARK_UNVERIFIED"
    assert consent(env, f"B-{disp}", "voicebot")["consented"] == 0
    assert consent(env, f"B-{disp}", "ivr")["consented"] == 0
    assert consent(env, f"B-{disp}", "sms")["consented"] == 1  # not an opt-out of the borrower
    assert state(env, f"B-{disp}")["stopped"] == 0
    flag = env.lg._conn().execute("SELECT status FROM voice_contact_flags WHERE borrower_id=?", (f"B-{disp}",)).fetchone()
    assert flag["status"] == "UNVERIFIED"
    nxt = env.lg.preview(Instruction(f"B-{disp}", ob_id, "C1", "voicebot", VOICE_TPL, at(11, 0, D + timedelta(days=1)), 300))
    assert nxt.reason.startswith("NO_CONSENT") or nxt.rule == "CONSENT"
    # not the borrower talking: no reply event, no reward
    ev = env.lg._conn().execute("SELECT COUNT(*) FROM events WHERE borrower_id=? AND event_type='reply_received'",
                                (f"B-{disp}",)).fetchone()[0]
    assert ev == 0 and outcome_row(env, iid)["reply_at"] is None
    _, iid2 = place_call(env, f"B-{disp}-lo")
    r2 = env.ing.ingest(payload(f"call-{disp}-lo", iid2, disp, 0.79))
    assert r2["action"] == "REVIEW" and consent(env, f"B-{disp}-lo", "voicebot")["consented"] == 1


def test_ptp_080_within_days_holds_until_promise_then_follow_up(env):
    ob_id, iid = place_call(env, "B-PTP", dpd=12)
    promise = D + timedelta(days=7)  # B1: 7 days max
    r = env.ing.ingest(payload("call-p1", iid, "PTP", 0.8, ptp_date=promise.isoformat(), ptp_amount_type="FULL"))
    assert r["action"] == "PTP_HOLD" and r["effective_disposition"] == "PTP"
    assert state(env, "B-PTP")["ptp_date"] == promise.isoformat()
    synced(env, promise)
    on_promise = env.lg.preview(Instruction("B-PTP", ob_id, "C1", "sms", None, at(11, 0, promise), 20))
    assert on_promise.reason == "PTP_HOLD"
    assert r["effects"]["follow_up_due_on"] == (promise + timedelta(days=1)).isoformat()
    assert env.ing.due_followups(promise) == []
    assert [f["borrower_id"] for f in env.ing.due_followups(promise + timedelta(days=1))] == ["B-PTP"]
    # selection on the follow-up day picks PTP_FOLLOW_UP
    env.lg.start_day(promise + timedelta(days=1))
    b, o, ctx = inputs_from_ledger(env.lg, "B-PTP", at(10, 0, promise + timedelta(days=1)))
    s = select_bot(b, o, ctx)
    assert s["use_case"] == "PTP_FOLLOW_UP" and s["variables"]["promise_date"] == spoken_date(promise)
    # a follow-up call closes it
    env.ing.ingest(payload("call-p1-fu", None, "NO_RESPONSE", 0.9, app="PTP_FU",
                           ended=at(10, 30, promise + timedelta(days=1))), borrower_id="B-PTP")
    assert env.ing.due_followups(promise + timedelta(days=1)) == []


def test_ptp_079_or_beyond_max_days_downgraded_no_hold(env):
    _, iid = place_call(env, "B-PTP-LO", dpd=12)
    r = env.ing.ingest(payload("call-p2", iid, "PTP", 0.79, ptp_date=(D + timedelta(days=2)).isoformat()))
    assert r["effective_disposition"] == "NEEDS_TIME_NO_DATE" and r["action"] == "DOWNGRADE_NEEDS_TIME_NO_DATE"
    assert state(env, "B-PTP-LO")["ptp_date"] is None
    _, iid2 = place_call(env, "B-PTP-FAR", dpd=40)  # B2: max 5 days
    r2 = env.ing.ingest(payload("call-p3", iid2, "PARTIAL_PTP", 0.95, ptp_date=(D + timedelta(days=6)).isoformat()))
    assert r2["rule"] == "K17.PARTIAL_PTP.DOWNGRADE.BEYOND_MAX_DAYS"
    assert state(env, "B-PTP-FAR")["ptp_date"] is None
    assert env.ing.due_followups(D + timedelta(days=30)) == []


def test_already_paid_never_marks_paid_and_pauses_48h(env):
    ob_id, iid = place_call(env, "B-PAID")
    before = env.lg._obligation(env.lg._conn(), ob_id)["amount_paid_paise"]
    r = env.ing.ingest(payload("call-ap", iid, "ALREADY_PAID", 0.6))
    assert r["action"] == "PAYMENT_CLAIM_PENDING" and r["effects"]["marked_paid"] is False
    assert r["envelope"]["payload"]["payment_verified"] is False
    c = env.lg._conn()
    assert env.lg._obligation(c, ob_id)["amount_paid_paise"] == before
    assert c.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
    assert outcome_row(env, iid)["payment_verified_at"] is None
    paused = {r["day"]: r["trigger"] for r in c.execute("SELECT day, trigger FROM stop_for_day WHERE borrower_id='B-PAID'")}
    assert set(paused) == {(D + timedelta(days=k)).isoformat() for k in (0, 1, 2)}  # every day the 48 h touch
    assert paused[(D + timedelta(days=1)).isoformat()] == "PAYMENT_CLAIM_PENDING"
    synced(env, D + timedelta(days=1))
    d = env.lg.preview(Instruction("B-PAID", ob_id, "C1", "sms", None, at(11, 0, D + timedelta(days=1)), 20))
    assert d.reason == "STOP_FOR_DAY"
    synced(env, D + timedelta(days=3))
    assert env.lg.preview(Instruction("B-PAID", ob_id, "C1", "sms", "SMS_EN", at(11, 0, D + timedelta(days=3)), 20)).allowed
    assert env.ing.resolve_payment_claims(at(9, 0, D + timedelta(days=1))) == []
    assert env.ing.resolve_payment_claims(at(10, 6, D + timedelta(days=2)))[0]["status"] == "EXPIRED"
    assert env.lg._obligation(c, ob_id)["amount_paid_paise"] == before  # still never paid


def test_already_paid_claim_verified_only_by_ledger(env):
    ob_id, iid = place_call(env, "B-PAID2")
    env.ing.ingest(payload("call-ap2", iid, "ALREADY_PAID", 0.9))
    env.lg.record_payment_verified(ob_id, 500_000, at(15), "PAY-V-1", "UTR-V-1")
    assert env.ing.resolve_payment_claims(at(16))[0]["status"] == "VERIFIED"


def test_already_paid_059_review_no_pause(env):
    ob_id, iid = place_call(env, "B-PAID3")
    r = env.ing.ingest(payload("call-ap3", iid, "ALREADY_PAID", 0.59))
    assert r["action"] == "REVIEW"
    synced(env, D + timedelta(days=1))
    nxt = env.lg.preview(Instruction("B-PAID3", ob_id, "C1", "sms", None, at(11, 0, D + timedelta(days=1)), 20))
    assert nxt.reason != "STOP_FOR_DAY"


def test_callback_070_scheduled_through_supervisor(env):
    _, iid = place_call(env, "B-CB")
    env.lg.mark_spine_synced(at(10))           # LMS refreshed this morning: tomorrow 09:30 is < 24 h away
    when = at(9, 30, D + timedelta(days=1))
    r = env.ing.ingest(payload("call-cb", iid, "CALLBACK_REQUESTED", 0.7, callback_at=when.isoformat()))
    assert r["effects"]["callback"] == "SCHEDULED"
    t = env.lg.touch(r["effects"]["callback_instruction_id"])
    assert t["status"] == "RESERVED" and t["channel"] == "voicebot" and t["scheduled_at"].startswith(when.date().isoformat())
    _, iid2 = place_call(env, "B-CB-LO")
    r2 = env.ing.ingest(payload("call-cb-lo", iid2, "CALLBACK_REQUESTED", 0.69, callback_at=when.isoformat()))
    assert r2["action"] == "REVIEW" and "callback" not in r2["effects"]


def test_callback_refused_by_gate_is_wait_with_reason(env):
    _, iid = place_call(env, "B-CB-T")
    r = env.ing.ingest(payload("call-cbt", iid, "CALLBACK_REQUESTED", 0.9, callback_at=at(16).isoformat()))
    assert (r["effects"]["callback"], r["effects"]["callback_reason"]) == ("WAIT", "STOP_FOR_DAY")  # K01: replied today
    _, iid2 = place_call(env, "B-CB-S")
    sunday = at(11, 0, D + timedelta(days=2))
    synced(env, sunday.date())                 # freshness passes; the quiet day still refuses
    r2 = env.ing.ingest(payload("call-cbs", iid2, "CALLBACK_REQUESTED", 0.9, callback_at=sunday.isoformat()))
    assert r2["effects"]["callback"] == "WAIT" and r2["effects"]["callback_reason"] == "QUIET_DAY"
    assert "callback_queued_for" not in r2["effects"]


def test_callback_with_stale_dpd_is_wait_and_kept_for_that_day(env):
    _, iid = place_call(env, "B-CB-L")
    when = at(15, 0, D + timedelta(days=1))    # > 24 h after the last LMS refresh -> DPD_VALID refuses now
    r = env.ing.ingest(payload("call-cbl", iid, "CALLBACK_REQUESTED", 0.9, callback_at=when.isoformat()))
    eff = r["effects"]
    assert (eff["callback"], eff["callback_rule"], eff["callback_queued_for"]) == ("WAIT", "DPD_VALID", when.date().isoformat())
    assert [f["use_case"] for f in env.ing.due_followups(when.date())] == ["CALLBACK"]
    b, o, ctx = inputs_from_ledger(env.lg, "B-CB-L", at(9, 0, when.date()))
    assert select_bot(b, o, ctx)["use_case"] == "CALLBACK"
    env.ing.ingest({"call_id": "call-cbl-2", "app_id": "Kairos---Ca-f080ffa8-2d1d", "ended_at": when.isoformat(),
                    "variables": {"disposition": "NO_RESPONSE", "disposition_confidence": 1}}, borrower_id="B-CB-L")
    assert env.ing.due_followups(when.date()) == []


def test_unknown_enum_goes_to_review_with_no_automated_action(env):
    _, iid = place_call(env, "B-UNK")
    r = env.ing.ingest(payload("call-unk", iid, "MAYBE_LATER", "0.99"))
    assert (r["effective_disposition"], r["confidence"], r["action"]) == (UNCLEAR, 0.0, "REVIEW")
    st = state(env, "B-UNK")
    assert not any((st["stopped"], st["supervisor_hold"], st["callback_pending"], st["ptp_date"], st["hardship_until"]))
    assert [t["type"] for t in tickets(env, "B-UNK")] == ["VOICE_REVIEW_UNCLEAR_UNCLEAR"]
    ev = env.lg._conn().execute("SELECT COUNT(*) FROM events WHERE borrower_id='B-UNK' AND event_type='reply_received'").fetchone()[0]
    assert ev == 0 and r["meaningful_reply"] is False and outcome_row(env, iid)["reply_at"] is None


def test_low_confidence_generic_is_unclear_review(env):
    _, iid = place_call(env, "B-LINK")
    r = env.ing.ingest(payload("call-link", iid, "PAY_NOW_LINK", 0.59))
    assert r["effective_disposition"] == UNCLEAR and r["rule"] == "K17.UNCLEAR.LOW_CONFIDENCE.PAY_NOW_LINK"
    assert r["reward"] is None and outcome_row(env, iid)["reply_at"] is None


# ---------- learner reward ----------
@pytest.mark.parametrize("disp,conf,rewarded", [
    ("PAY_NOW_LINK", 0.6, True), ("PAY_NOW_LINK", 0.59, False), ("NEEDS_TIME_NO_DATE", 0.6, True),
    ("HUMAN_REQUESTED", 0.6, True), ("DISPUTE", 0.6, True), ("HARDSHIP", 0.6, True),
    ("NO_RESPONSE", 0.99, False), ("STOP_REQUESTED", 0.99, False), ("REFUSED_TO_PAY", 0.9, False),
    ("ABUSIVE", 0.9, False)])
def test_meaningful_reply_reward(env, disp, conf, rewarded):
    _, iid = place_call(env, f"B-R-{disp}-{conf}")
    r = env.ing.ingest(payload(f"call-r-{disp}-{conf}", iid, disp, conf))
    assert r["meaningful_reply"] is rewarded
    assert (outcome_row(env, iid)["reply_at"] is not None) is rewarded
    assert (r["reward"] == "REPLY_ATTRIBUTED") is rewarded


def test_no_response_is_reach_only(env):
    _, iid = place_call(env, "B-NR")
    r = env.ing.ingest(payload("call-nr", iid, "NO_RESPONSE", 0.9, duration=4))
    assert env.lg.touch(iid)["status"] == "DELIVERED"  # provider delivery: connected
    row = outcome_row(env, iid)
    assert row["reached"] == 1 and row["reply_at"] is None and r["report_label"] == "Delivered"
    _, iid2 = place_call(env, "B-NC")
    r2 = env.ing.ingest(payload("call-nc", iid2, "NO_RESPONSE", 0.9, status="no-answer", duration=0))
    assert env.lg.touch(iid2)["status"] == "SENT" and outcome_row(env, iid2)["reached"] == 0
    assert r2["report_label"] == "Sent"


# ---------- idempotency + envelope ----------
def test_duplicate_webhook_does_nothing(env):
    _, iid = place_call(env, "B-DUP")
    p = payload("call-dup", iid, "STOP_REQUESTED", 0.4)
    first = env.ing.ingest(p)
    audit_n = len(env.lg.audit_log(limit=1000))
    tickets_n = len(tickets(env, "B-DUP"))
    again = env.ing.ingest(copy.deepcopy(p))
    assert first["status"] == "PROCESSED" and again["status"] == "DUPLICATE"
    assert again["first"]["rule"] == "K17.STOP_REQUESTED.BELOW"
    assert len(env.lg.audit_log(limit=1000)) == audit_n and len(tickets(env, "B-DUP")) == tickets_n
    assert len(env.sink.envelopes) == 1


def test_crm_envelope_carries_policy_version_rule_and_rule_path(env):
    _, iid = place_call(env, "B-ENV")
    r = env.ing.ingest(payload("call-env", iid, "NEEDS_TIME_NO_DATE", "0.75", rule_path="greet>ask>needs_time",
                               call_summary=f"Borrower needs time, call {FAKE_PHONE}"))
    env_ = env.sink.envelopes[-1]
    assert env_ is r["envelope"]
    assert env_["event_type"] == "voice.call_disposition" and env_["provenance"] == "SIMULATED"
    assert env_["dedupe_key"] == "k17:call:call-env" and env_["touch_id"] == iid
    pl = env_["payload"]
    assert pl["policy_version"] == THR["policy_version"] and pl["rule"] == "K17.NEEDS_TIME_NO_DATE.ACCEPT"
    assert pl["rule_path"] == "greet>ask>needs_time" and pl["confidence"] == 0.75
    assert "00000" not in json.dumps(env_)
    assert env.ing.call("call-env")["report_label"] == "Responded"


def test_unmatched_call_is_recorded_without_effects(env):
    r = env.ing.ingest({"interaction_id": "20261120/abc", "app_id": APP["EMI"],
                        "post_interaction_variables": {"disposition": "STOP_REQUESTED", "disposition_confidence": 0.9}})
    assert r["status"] == "UNMATCHED"
    assert env.ing.ingest({"interaction_id": "20261120/abc"})["status"] == "DUPLICATE"
    with pytest.raises(ValueError):
        env.ing.ingest({"variables": {}})


def test_normalize_payload_shapes():
    p = normalize_payload({"interaction": {"id": "X1", "status": "completed", "duration": "33"},
                           "app_id": "A", "agent_variables": [{"name": "touch_id", "value": "INS-1234abcd"}],
                           "post_interaction_variables": {"disposition": {"value": "PTP"}}})
    assert (p["call_id"], p["touch_id"], p["connected"], p["duration_seconds"]) == ("X1", "INS-1234abcd", True, 33.0)
    assert p["variables"]["disposition"] == "PTP"


def test_language_barrier_sets_observed_language_for_next_call(env):
    _, iid = place_call(env, "B-LANG")
    r = env.ing.ingest(payload("call-lang", iid, "LANGUAGE_BARRIER", 0.8, language_used="Tamil"))
    assert r["effects"]["observed_language"] == "Tamil"
    b, o, ctx = inputs_from_ledger(env.lg, "B-LANG", at(10, 0, D + timedelta(days=1)))
    assert select_bot(b, o, ctx)["language"] == "Tamil"


def test_needs_human_flag_opens_review(env):
    _, iid = place_call(env, "B-NH")
    env.ing.ingest(payload("call-nh", iid, "REFUSED_TO_PAY", 0.9, needs_human="true"))
    assert [t["type"] for t in tickets(env, "B-NH")] == ["VOICE_REVIEW_NEEDS_HUMAN"]


def test_hitl_handoff_hook_used_when_present(env):
    calls = []

    class FakeHitl:
        def act(self, borrower_id, action, actor, role, reason, now=None, **params):
            calls.append((borrower_id, action, role, reason))
            return {"borrower_id": borrower_id, "action": action, "ticket_id": "TKT-HITL"}

    ing = VoiceIngester(env.lg, finalizer=env.fin, sink=RecordingSink(), hitl=FakeHitl())
    _, iid = place_call(env, "B-HITL")
    r = ing.ingest(payload("call-hitl", iid, "STOP_REQUESTED", 0.3))
    assert calls == [("B-HITL", "handoff", "operator", "voice bot: POSSIBLE_STOP_REQUESTED")]
    assert r["effects"]["handoff"]["via"] == "hitl"


# =====================================================================================
# 4b. Sarvam webhook payload (sarvam://schemas/webhook-payload, read 2026-11-19)
#     Outbound campaign attempt (sent whether or not it connected) and inbound deployment call, built from the
#     schema's fields with synthetic values and all-zero placeholder phone numbers. Plus the documented
#     fallback: the live workflow report body (status, string duration, final_agent_variables as a JSON
#     string, `event`) and the first inferred shapes.
# =====================================================================================
META = {"batch_id": "batch-0001", "outbound_campaign_id": "camp-0001", "source": "kairos.k17"}
USER_PHONE, AGENT_PHONE = "+910000000000", "+910000000001"   # placeholders, never stored


def end_utc(hh, mm=0, day=D):
    """Local IST wall time -> the UTC 'Z' string Sarvam sends in end_datetime."""
    return (datetime(day.year, day.month, day.day, hh, mm) - timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def outbound(attempt_id, touch_id, disposition=None, conf=None, connectivity="connected", completion="completed",
             duration=45, end=None, next_action=None, retry=0, failure_reason=None, app="EMI", metadata=META,
             variables_as="object", user_identifier=None, **extra_vars):
    out_vars = dict(extra_vars)
    if disposition is not None:
        out_vars.update(disposition=disposition, disposition_confidence=conf)
    initial = {"touch_id": touch_id, "user_name": "Customer", "preferred_language": "Hindi"}
    final = {**initial, **out_vars}
    enc = (lambda v: json.dumps(v)) if variables_as == "string" else (lambda v: v)
    connected = connectivity == "connected"
    return {"app_id": APP[app], "app_version": 3, "attempt_id": attempt_id, "campaign_id": "camp-0001",
            "cohort_id": "cohort-0001", "completion_status": completion,
            "user_identifier": user_identifier or touch_id, "user_phone_number": USER_PHONE,
            "agent_phone_number": AGENT_PHONE, "experiment": {"experiment_id": "exp-0001", "variant_id": "var-b"},
            "connectivity_status": connectivity, "next_action_status": next_action, "retry_attempt": retry,
            "failure_reason": failure_reason, "duration": duration if connected else None,
            "interaction_id": f"ix-{attempt_id}" if connected else None, "recording_url": None,
            "executed_at": "2026-11-20T10:00:00+05:30", "start_datetime": end_utc(10, 4),
            "end_datetime": end or end_utc(10, 5),
            "initial_agent_variables": enc(initial), "final_agent_variables": enc(final) if connected else None,
            "output_agent_variables": enc(out_vars) if connected else None,
            "interaction_transcript": [{"role": "agent", "en_text": "Hello, this is a reminder.", "indic_text": None},
                                       {"role": "user", "en_text": f"Call me on {USER_PHONE}", "indic_text": None}]
            if connected else None,
            "metadata": metadata}


def inbound(interaction_id, **kw):
    p = {"app_id": APP["EMI"], "app_version": 3, "deployment_id": "dep-0001", "interaction_id": interaction_id,
         "user_phone_number": USER_PHONE, "agent_phone_number": AGENT_PHONE, "duration": 30,
         "end_datetime": end_utc(10, 5), "output_agent_variables": {}}
    p.update(kw)
    return p


def _no_phone_anywhere(e, call_id):
    row = e.lg._conn().execute("SELECT * FROM voice_calls WHERE call_id=?", (call_id,)).fetchone()
    blob = json.dumps(dict(row), default=str) + json.dumps(e.lg.audit_log(limit=1000), default=str)
    blob += json.dumps(e.sink.envelopes, default=str)
    assert "0000000000" not in blob and "0000000001" not in blob


def test_outbound_connected_with_disposition(env):
    _, iid = place_call(env, "B-SV1")
    r = env.ing.ingest(outbound("att-0001", iid, "STOP_REQUESTED", 0.9))
    assert r["status"] == "PROCESSED" and r["shape"] == "outbound" and r["terminal"] is True
    assert r["attempt_id"] == "att-0001" and r["call_id"] == "att-0001"
    assert r["action"] == "RECORD_STOP_ALL_CHANNELS" and r["call_status"] == "CONNECTED" and r["connected"] is True
    assert r["completion_status"] == "completed" and r["variant_id"] == "var-b"
    assert env.lg.touch(iid)["status"] == "DELIVERED"
    row = env.ing.call("att-0001")
    assert row["duration_seconds"] == 45.0 and row["interaction_id"] == "ix-att-0001" and row["variant_id"] == "var-b"
    assert row["ended_at"].startswith("2026-11-20T10:05")          # end_datetime (UTC) -> local IST
    assert row["attempt_detail"]["executed_at"] == "2026-11-20T10:00:00+05:30"   # offset kept, not assumed Z
    assert row["attempt_detail"]["transcript_turns"] == 2 and row["attempt_detail"]["app_version"] == 3
    env_pl = r["envelope"]["payload"]
    assert (env_pl["variant_id"], env_pl["experiment_id"], env_pl["app_version"]) == ("var-b", "exp-0001", 3)
    _no_phone_anywhere(env, "att-0001")
    assert "reminder" not in json.dumps(dict(env.lg._conn().execute("SELECT * FROM voice_calls").fetchone()))


def test_output_agent_variables_win_then_final_then_initial():
    p = normalize_payload({**outbound("a1", "INS-1234abcd"),
                           "initial_agent_variables": {"touch_id": "INS-1234abcd", "disposition": "", "x": "init"},
                           "final_agent_variables": {"disposition": "CALLBACK_REQUESTED", "x": "final"},
                           "output_agent_variables": {"disposition": "PTP"}})
    assert p["variables"]["disposition"] == "PTP" and p["variables"]["x"] == "final"
    assert p["variables"]["touch_id"] == "INS-1234abcd"
    assert "user_phone_number" not in json.dumps(p, default=str) and "0000000000" not in json.dumps(p, default=str)


def test_variables_as_json_string_tolerated(env):
    _, iid = place_call(env, "B-SV2")
    r = env.ing.ingest(outbound("att-0002", iid, "PAY_NOW_LINK", 0.6, variables_as="string"))
    assert r["rule"] == "K17.PAY_NOW_LINK.ACCEPT" and r["meaningful_reply"] is True
    assert r["reward"] == "REPLY_ATTRIBUTED"


def test_join_order_initial_touch_then_user_identifier_then_metadata(env):
    _, iid = place_call(env, "B-JOIN")
    # touch id only as user_identifier (initial variables carry no touch_id)
    p = outbound("att-join-1", iid, "NEEDS_TIME_NO_DATE", 0.7)
    p["initial_agent_variables"] = {"user_name": "Customer"}
    p["final_agent_variables"] = p["output_agent_variables"]
    assert env.ing.ingest(p)["borrower_id"] == "B-JOIN"
    # only metadata.touch_id resolves (user_identifier is the lender's own id)
    _, iid2 = place_call(env, "B-JOIN2")
    p2 = outbound("att-join-2", iid2, "NEEDS_TIME_NO_DATE", 0.7, user_identifier="CUST-0001",
                  metadata={**META, "touch_id": iid2})
    p2["initial_agent_variables"] = {"user_name": "Customer"}
    p2["final_agent_variables"] = p2["output_agent_variables"]
    assert env.ing.ingest(p2)["borrower_id"] == "B-JOIN2"
    # nothing resolves -> UNMATCHED, no effects
    p3 = outbound("att-join-3", "INS-unknown0000", "STOP_REQUESTED", 0.9, user_identifier="CUST-0002")
    assert env.ing.ingest(p3)["status"] == "UNMATCHED"


@pytest.mark.parametrize("conn,reason,canon", [
    ("no_answer", "NO_ANSWER", "NO_ANSWER"), ("busy", "USER_BUSY", "BUSY"), ("failed", "SIP 503", "FAILED")])
def test_terminal_not_connected_is_reach_only_no_response(env, conn, reason, canon):
    bid = f"B-NC-{conn}"
    _, iid = place_call(env, bid)
    p = outbound(f"att-nc-{conn}", iid, connectivity=conn, completion="failed", failure_reason=reason)
    # stale bot variables on a not-connected attempt must not become intent
    p["output_agent_variables"] = {"disposition": "STOP_REQUESTED", "disposition_confidence": 0.99}
    r = env.ing.ingest(p)
    assert r["status"] == "PROCESSED" and r["call_status"] == canon and r["connected"] is False and r["terminal"]
    assert (r["disposition"], r["effective_disposition"], r["rule"], r["action"]) == (
        "NO_RESPONSE", "NO_RESPONSE", "K17.NO_RESPONSE", "NONE")
    assert r["reason"] == f"NOT_CONNECTED:{canon}"
    assert r["meaningful_reply"] is False and r["reward"] is None and r["failure_reason"] == reason
    assert env.lg.touch(iid)["status"] == "SENT"                      # not delivered
    row = outcome_row(env, iid)
    assert row["reached"] == 0 and row["reply_at"] is None            # terminal: reach finalized (not reached)
    st = state(env, bid)
    assert (st["stopped"], st["supervisor_hold"]) == (0, 0)
    assert tickets(env, bid) == [] and r["report_label"] == "Sent"
    assert env.ing.call(f"att-nc-{conn}")["failure_reason"] == reason
    assert r["envelope"]["payload"]["failure_reason"] == reason
    _no_phone_anywhere(env, f"att-nc-{conn}")


@pytest.mark.parametrize("nxt", ["retry", "short_duration_retry", "provider_error_retry"])
def test_non_terminal_attempt_is_recorded_only(env, nxt):
    bid = f"B-RT-{nxt}"
    _, iid = place_call(env, bid)
    audit_n = len(env.lg.audit_log(limit=1000))
    r = env.ing.ingest(outbound(f"att-rt-{nxt}", iid, connectivity="no_answer", completion="failed", next_action=nxt))
    assert r["status"] == "ATTEMPT_RECORDED" and r["terminal"] is False and r["next_action_status"] == nxt
    assert outcome_row(env, iid)["reached"] is None                 # outcome not finalized, no NO_RESPONSE
    assert env.lg.touch(iid)["status"] == "SENT" and env.sink.envelopes == []
    assert len(env.lg.audit_log(limit=1000)) == audit_n and tickets(env, bid) == []
    assert env.ing.call(f"att-rt-{nxt}")["status"] == "ATTEMPT"
    # the retry (same attempt_id, retry_attempt 1) is terminal and finalizes
    r2 = env.ing.ingest(outbound(f"att-rt-{nxt}", iid, connectivity="busy", completion="failed", retry=1))
    assert r2["status"] == "PROCESSED" and r2["call_id"] == f"att-rt-{nxt}#r1" and r2["terminal"] is True
    assert outcome_row(env, iid)["reached"] == 0 and r2["reward"] is None


def test_non_terminal_connected_intent_is_still_acted_on(env):
    """A STOP captured on a short call Sarvam will redial is not held back for the retry."""
    _, iid = place_call(env, "B-RT-STOP")
    r = env.ing.ingest(outbound("att-rt-stop", iid, "STOP_REQUESTED", 0.9, duration=6,
                                next_action="short_duration_retry"))
    assert r["status"] == "PROCESSED" and r["terminal"] is False and r["action"] == "RECORD_STOP_ALL_CHANNELS"
    assert state(env, "B-RT-STOP")["stopped"] == 1


def test_non_terminal_follow_up_stays_due_terminal_closes_it(env):
    ob, iid = place_call(env, "B-FU")
    env.ing.ingest(payload("call-fu-ptp", iid, "PTP", 0.9, ptp_date=(D + timedelta(days=2)).isoformat()))
    assert len(env.ing.due_followups(D + timedelta(days=30))) == 1
    d3 = D + timedelta(days=3)
    r = env.ing.ingest(outbound("att-fu", iid, connectivity="no_answer", completion="failed", app="PTP_FU",
                                next_action="retry", end=end_utc(11, 0, d3)))
    assert r["status"] == "ATTEMPT_RECORDED"
    assert len(env.ing.due_followups(D + timedelta(days=30))) == 1   # retry pending: still due
    r = env.ing.ingest(outbound("att-fu", iid, connectivity="no_answer", completion="failed", app="PTP_FU",
                                retry=1, end=end_utc(13, 0, d3)))
    assert r["effective_disposition"] == "NO_RESPONSE"
    assert len(env.ing.due_followups(D + timedelta(days=30))) == 1   # terminal but nobody reached: still due
    r = env.ing.ingest(outbound("att-fu-2", iid, "NEEDS_TIME_NO_DATE", 0.7, app="PTP_FU", end=end_utc(15, 0, d3)))
    assert r["status"] == "PROCESSED" and env.ing.due_followups(D + timedelta(days=30)) == []   # use case from app_id


def test_duplicate_attempt_id_does_nothing(env):
    _, iid = place_call(env, "B-ATT-DUP")
    p = outbound("att-dup", iid, "STOP_REQUESTED", 0.4)
    first = env.ing.ingest(p)
    audit_n, tickets_n = len(env.lg.audit_log(limit=1000)), len(tickets(env, "B-ATT-DUP"))
    again = env.ing.ingest(copy.deepcopy(p))
    assert first["status"] == "PROCESSED" and again["status"] == "DUPLICATE" and again["call_id"] == "att-dup"
    assert len(env.lg.audit_log(limit=1000)) == audit_n and len(tickets(env, "B-ATT-DUP")) == tickets_n
    assert len(env.sink.envelopes) == 1


def test_inbound_keyed_by_interaction_id(env):
    r = env.ing.ingest(inbound("ix-in-0001"), borrower_id=None)
    assert r["status"] == "UNMATCHED" and r["shape"] == "inbound" and r["call_id"] == "ix-in-0001"
    assert env.ing.ingest(inbound("ix-in-0001"))["status"] == "DUPLICATE"
    _no_phone_anywhere(env, "ix-in-0001")
    _, iid = place_call(env, "B-IN")
    r = env.ing.ingest(inbound("ix-in-0002", output_agent_variables={"touch_id": iid, "disposition": "CALLBACK_REQUESTED",
                                                                     "disposition_confidence": 0.5}))
    assert r["status"] == "PROCESSED" and r["call_status"] == "INBOUND" and r["connected"] is True


def test_metadata_passed_through(env):
    _, iid = place_call(env, "B-META")
    r = env.ing.ingest(outbound("att-meta", iid, "NEEDS_TIME_NO_DATE", 0.7))
    assert r["metadata"] == META
    assert r["envelope"]["payload"]["metadata"] == META
    assert env.ing.call("att-meta")["metadata"] == META


def test_duration_and_time_normalised():
    base = outbound("a", "INS-1")
    assert normalize_payload({**base, "duration": "12.5"})["duration_seconds"] == 12.5   # string tolerated
    assert normalize_payload({**base, "duration": None})["duration_seconds"] is None
    p = normalize_payload({**base, "end_datetime": "2026-11-20T04:35:00"})            # schema: UTC even if naive
    assert p["ended_at"].isoformat() == "2026-11-20T04:35:00+00:00"
    assert normalize_payload({**base, "end_datetime": None})["ended_at"].isoformat() == "2026-11-20T04:34:00+00:00"


@pytest.mark.parametrize("drop", sorted(["app_id", "app_version", "attempt_id", "campaign_id", "cohort_id",
                                         "completion_status", "user_identifier", "user_phone_number"]))
def test_outbound_missing_required_field_rejected(env, drop):
    from kairos_backend.voice.ingest import PayloadError
    p = outbound("att-req", "INS-1")
    del p[drop]
    if drop == "campaign_id":
        p["campaign_id"] = None      # still outbound by key, but the required value is missing
    with pytest.raises(PayloadError, match=drop):
        env.ing.ingest(p)


@pytest.mark.parametrize("drop", ["app_id", "app_version", "interaction_id", "user_phone_number", "agent_phone_number"])
def test_inbound_missing_required_field_rejected(env, drop):
    from kairos_backend.voice.ingest import PayloadError
    p = inbound("ix-req")
    del p[drop]
    with pytest.raises(PayloadError, match=drop):
        env.ing.ingest(p)


@pytest.mark.parametrize("patch", [
    {"completion_status": "done"}, {"app_version": "3"}, {"app_version": True}, {"retry_attempt": "1"},
    {"final_agent_variables": "{not json"}, {"output_agent_variables": 7}, {"duration": "forty"},
    {"end_datetime": "yesterday"}, {"executed_at": "soon"}, {"metadata": "batch-0001"}, {"experiment": "var-b"},
    {"interaction_transcript": "hello"}, {"connectivity_status": 3}])
def test_outbound_garbage_rejected(env, patch):
    from kairos_backend.voice.ingest import PayloadError
    with pytest.raises(PayloadError):
        env.ing.ingest({**outbound("att-bad", "INS-1"), **patch})
    assert env.lg._conn().execute("SELECT COUNT(*) FROM voice_calls").fetchone()[0] == 0


def test_phone_never_in_error_message(env):
    from kairos_backend.voice.ingest import PayloadError
    with pytest.raises(PayloadError) as ei:
        env.ing.ingest({**outbound("att-e", "INS-1"), "app_version": "x"})
    assert "0000000000" not in str(ei.value)


# ---------- documented fallback: live workflow report body / first inferred shapes ----------
def live_attempt(attempt_id, touch_id, disposition=None, conf=None, status="completed", duration="45", end=None,
                 failure_reason="", **extra_vars):
    fav = {"touch_id": touch_id, **extra_vars}
    if disposition is not None:
        fav.update(disposition=disposition, disposition_confidence=conf)
    return {"v": 2, "event": "call_ended", "attempt_id": attempt_id, "status": status, "duration": duration,
            "end_datetime": end or end_utc(10, 5), "failure_reason": failure_reason,
            "final_agent_variables": json.dumps(fav), "metadata": META}


def test_fallback_live_workflow_body(env):
    _, iid = place_call(env, "B-LIVE")
    r = env.ing.ingest(live_attempt("att-live", iid, "HUMAN_REQUESTED", "0.6"))
    assert r["status"] == "PROCESSED" and r["shape"] == "legacy" and r["rule"] == "K17.HUMAN_REQUESTED.ACCEPT"
    assert env.ing.call("att-live")["duration_seconds"] == 45.0


@pytest.mark.parametrize("status,canon", [("voicemail", "VOICEMAIL"), ("No-Answer", "NO_ANSWER")])
def test_fallback_status_aliases_not_connected(env, status, canon):
    _, iid = place_call(env, f"B-ALIAS-{canon}")
    r = env.ing.ingest(live_attempt(f"att-alias-{canon}", iid, "STOP_REQUESTED", 0.9, status=status, duration="0"))
    assert r["call_status"] == canon and r["connected"] is False and r["effective_disposition"] == "NO_RESPONSE"


def test_fallback_connectivity_status_wins_over_status(env):
    _, iid = place_call(env, "B-CS")
    r = env.ing.ingest({**live_attempt("att-cs", iid, duration=""), "connectivity_status": "busy"})
    assert r["call_status"] == "BUSY" and r["connected"] is False


def test_fallback_before_call_gate_event_is_a_no_op(env):
    _, iid = place_call(env, "B-GATE")
    audit_n = len(env.lg.audit_log(limit=1000))
    r = env.ing.ingest({"v": 2, "event": "before_call", "step": "PRE_DUE:0", "line": "line-1",
                        "workflow": "wf-0001", "status": "", "attempt_id": "", "final_agent_variables": "",
                        "metadata": META})
    assert r["status"] == "IGNORED" and r["event"] == "before_call"
    assert len(env.lg.audit_log(limit=1000)) == audit_n and env.sink.envelopes == []
    assert env.lg._conn().execute("SELECT COUNT(*) FROM voice_calls").fetchone()[0] == 0
    assert env.lg.touch(iid)["status"] == "SENT"


def test_fallback_ids_and_garbage():
    from kairos_backend.voice.ingest import PayloadError
    p = normalize_payload({"attempt_id": "att-9", "call_id": "legacy-1", "interaction_id": "ix-9", "status": "busy"})
    assert p["call_id"] == "att-9" and p["interaction_id"] == "ix-9"
    assert normalize_payload({"call_id": "legacy-1"})["call_id"] == "legacy-1"
    for bad in ({"attempt_id": "a", "final_agent_variables": "42"}, {"attempt_id": "a", "status": 3},
                {"attempt_id": ["a"]}, {"attempt_id": "a", "duration": "forty"}):
        with pytest.raises(PayloadError):
            normalize_payload(bad)


def test_old_voice_calls_table_gets_new_columns(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "old.db")
    lg._conn().execute("CREATE TABLE voice_calls (call_id TEXT PRIMARY KEY, app_id TEXT, use_case TEXT, borrower_id TEXT,"
                       " obligation_id TEXT, touch_id TEXT, status TEXT NOT NULL, received_at TEXT NOT NULL,"
                       " connected INTEGER, duration_seconds REAL, disposition TEXT, effective_disposition TEXT,"
                       " confidence REAL, rule TEXT, action TEXT, report_ordinal INTEGER, report_label TEXT,"
                       " policy_version TEXT, detail TEXT)")
    VoiceIngester(lg, sink=RecordingSink())
    cols = {r[1] for r in lg._conn().execute("PRAGMA table_info(voice_calls)")}
    assert {"call_status", "raw_status", "failure_reason", "interaction_id", "ended_at", "metadata", "shape",
            "completion_status", "next_action_status", "retry_attempt", "variant_id", "attempt_detail"} <= cols
    lg.close()


# =====================================================================================
# 5. API
# =====================================================================================
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "voice_api.db"))
    monkeypatch.delenv("SARVAM_WEBHOOK_SECRET", raising=False)
    import kairos_backend.api as api
    api = importlib.reload(api)
    return TestClient(api.app), api


def test_api_registry_select_and_webhook(client, monkeypatch):
    c, api = client
    reg = c.get("/voice/registry").json()
    assert reg["registry"]["use_cases"]["CALLBACK"]["voice"] == "Advait"
    assert reg["thresholds"]["policy_version"] == THR["policy_version"]

    lg = api.ledger
    lg.upsert_campaign("C1", budget_per_case_paise=100_000, start_date=D, end_date=D + timedelta(days=20))
    lg.upsert_template(VOICE_TPL, "voicebot", "hi")
    lg.start_day(D)
    ob_id = add_loan(lg, "B-API", D, dpd=40, consents=VOICE_CONSENT, campaign="C1")
    with lg._spine() as conn:
        conn.execute("UPDATE borrowers SET state='Gujarat' WHERE borrower_id='B-API'")
    s = c.post("/voice/select", json={"borrower_id": "B-API", "at": at(10).isoformat(),
                                      "touch_id": "INS-0a1b2c3d4e5f"}).json()
    assert (s["use_case"], s["language"], s["voice"]) == ("MID_BUCKET_RESOLUTION", "Gujarati", "Advait")
    assert s["variables"]["overdue_days"] == "40" and s["variables"]["emi_due_date"] == spoken_date(D - timedelta(days=40))
    _assert_no_pii(s["variables"])
    assert c.post("/voice/select", json={"borrower_id": "NOPE"}).status_code == 404

    r = lg.reserve(Instruction("B-API", ob_id, "C1", "voicebot", VOICE_TPL, at(10), 300))
    body = {"call_id": "sv-1", "app_id": APP["EMI"], "status": "completed", "duration_seconds": 30,
            "ended_at": at(10, 5).isoformat(),
            "variables": {"touch_id": r.instruction_id, "disposition": "STOP_REQUESTED",
                          "disposition_confidence": "0.9"}}
    out = c.post("/webhooks/sarvam/call-end", json=body).json()
    assert out["action"] == "RECORD_STOP_ALL_CHANNELS" and out["envelope"]["payload"]["rule"] == "K17.STOP_REQUESTED.ACCEPT"
    assert c.post("/webhooks/sarvam/call-end", json=body).json()["status"] == "DUPLICATE"
    assert c.get("/voice/calls/sv-1").json()["effective_disposition"] == "STOP_REQUESTED"
    assert c.get("/voice/calls/nope").status_code == 404

    # real call-attempt shape, gate event, garbage
    ob2 = add_loan(lg, "B-API2", D, dpd=12, consents=VOICE_CONSENT, campaign="C1")
    r2 = lg.reserve(Instruction("B-API2", ob2, "C1", "voicebot", VOICE_TPL, at(11), 300))
    assert r2.reserved
    att = outbound("att-api-1", r2.instruction_id, connectivity="busy", completion="failed",
                   failure_reason="USER_BUSY", end=end_utc(11, 1))
    res = c.post("/webhooks/sarvam/call-end", json=att)
    assert res.status_code == 200 and res.json()["effective_disposition"] == "NO_RESPONSE"
    assert res.json()["failure_reason"] == "USER_BUSY"
    assert c.post("/webhooks/sarvam/call-end", json=att).json()["status"] == "DUPLICATE"
    gate = c.post("/webhooks/sarvam/call-end", json={"event": "before_call", "v": 2, "step": "s"})
    assert gate.status_code == 200 and gate.json()["status"] == "IGNORED"
    retry = c.post("/webhooks/sarvam/call-end", json={**att, "attempt_id": "att-api-2", "next_action_status": "retry"})
    assert retry.status_code == 200 and retry.json()["status"] == "ATTEMPT_RECORDED"
    missing = {k: v for k, v in att.items() if k != "cohort_id"}
    res = c.post("/webhooks/sarvam/call-end", json=missing)
    assert res.status_code == 422 and "cohort_id" in res.text and "0000000000" not in res.text
    for bad in ({"status": "completed"}, {"attempt_id": "x", "final_agent_variables": "{oops"}, [1, 2],
                {**att, "completion_status": "done"}):
        assert c.post("/webhooks/sarvam/call-end", json=bad).status_code == 422

    monkeypatch.setenv("SARVAM_WEBHOOK_SECRET", "s3cret")
    assert c.post("/webhooks/sarvam/call-end", json={**body, "call_id": "sv-2"}).status_code == 401
    ok = c.post("/webhooks/sarvam/call-end", json={**body, "call_id": "sv-2"}, headers={"X-Kairos-Webhook-Secret": "s3cret"})
    assert ok.status_code == 200
