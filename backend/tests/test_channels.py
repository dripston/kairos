"""Channel layer: SIMULATED mocks, callback normalization, and the two phone-demo adapters.
No test here runs osascript or opens a network connection (both are blocked by an autouse fixture)."""
from __future__ import annotations

import json
import subprocess
import urllib.request
from datetime import timedelta
from types import SimpleNamespace

import pytest

from kairos_backend.channels import (MODES, MessagesAppSmsProvider, MockChannelProvider, WhatsAppCloudTestProvider,
                                     apply_all, apply_callback, build_providers, click_to_chat_url, is_stop_text,
                                     mask_number)
from kairos_backend.channels.mock import CHANNELS, IVR_MENU
from kairos_backend.executor import Executor, FakeProvider
from kairos_backend.ledger import RANK, Ledger
from tests.test_ledger import D, add_loan, at, ins

ALL = {"sms": 1, "whatsapp": 1, "email": 1, "ivr": 1, "voicebot": 1}
TPL = {"sms": "SMS_EN", "whatsapp": "WA_HI", "ivr": "IVR_HI", "voicebot": "VB_HI", "email": "EM_EN"}
QUIET = {"duplicate_rate": 0, "late_rate": 0, "timeout_rate": 0, "reject_rate": 0}
PHONE = "+919812345678"
OTHER = "+14155550123"


@pytest.fixture(autouse=True)
def no_side_effects(monkeypatch):
    """Belt and braces: if anything here tried to run a process or open a URL, fail loudly."""
    def boom(*a, **k):
        raise AssertionError("tests must never run osascript or touch the network")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)


@pytest.fixture
def lg(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "ch.db")
    for t, (ch, lang) in {"SMS_EN": ("sms", "en"), "WA_HI": ("whatsapp", "hi"), "EM_EN": ("email", "en"),
                          "IVR_HI": ("ivr", "hi"), "VB_HI": ("voicebot", "hi"), "DEMO_SMS_HELLO": ("sms", "en"),
                          "DEMO_WA_HELLO": ("whatsapp", "en")}.items():
        lg.upsert_template(t, ch, lang)
    lg.upsert_campaign("C1", budget_per_case_paise=100_000, start_date=D, end_date=D + timedelta(days=10))
    lg.enroll("C1", [add_loan(lg, b, consents=ALL) for b in ("B1", "B2", "B3")])
    lg.start_day(D)
    yield lg
    lg.close()


def send(lg, provider, ch, hh=10, bid="B1", tpl=None):
    r = lg.reserve(ins(bid, ch, at(hh), tpl=tpl or TPL[ch]))
    assert r.reserved, r.decision
    out = Executor(lg, {ch: provider}).dispatch(r.instruction_id)
    return r.instruction_id, out


def mock(ch, **overrides):
    return MockChannelProvider(ch, seed=7, profile={**QUIET, **overrides})


def touch_dict(iid="INS-1", to=PHONE, tpl="DEMO_SMS_HELLO", **kw):
    return {"instruction_id": iid, "borrower_id": "B1", "channel": "sms", "template_id": tpl, "to": to, **kw}


# ---------------------------------------------------------------- mocks
@pytest.mark.parametrize("ch", CHANNELS)
def test_mock_is_deterministic_per_seed_and_instruction(ch):
    iids = [f"INS-{i}" for i in range(40)]
    a, b, c = (MockChannelProvider(ch, seed=s, profile="chaos") for s in (1, 1, 2))
    ra = [a.send({"instruction_id": i}) for i in iids]
    rb = [b.send({"instruction_id": i}) for i in reversed(iids)][::-1]   # send order does not matter
    assert [(r.status, r.provider_ref) for r in ra] == [(r.status, r.provider_ref) for r in rb]
    assert [a.callbacks(i) for i in iids] == [b.callbacks(i) for i in iids]
    [c.send({"instruction_id": i}) for i in iids]
    assert [a.callbacks(i) for i in iids] != [c.callbacks(i) for i in iids]
    for i in iids:
        for e in a.callbacks(i):
            assert e["provenance"] == "SIMULATED" and e["channel"] == ch and e["instruction_id"] == i
            assert {"provider_ref", "status", "at", "dtmf", "duration_s", "text"} <= set(e)


def test_mock_send_is_idempotent_and_labelled():
    p = MockChannelProvider("sms", seed=3, profile="perfect")
    r1, r2 = p.send({"instruction_id": "X"}), p.send({"instruction_id": "X"})
    assert r1.status == r2.status == "ACCEPTED" and r1.provider_ref == r2.provider_ref
    assert p.sent == ["X"] and p.provenance == "SIMULATED" and r1.cost_paise == 15


def test_mock_realistic_rates_and_sms_never_reports_read():
    p = MockChannelProvider("sms", seed=11, profile="chaos")
    statuses = []
    for i in range(2000):
        p.send({"instruction_id": f"I{i}"})
        statuses.append({e["status"] for e in p.callbacks(f"I{i}")})
    delivered = sum("DELIVERED" in s for s in statuses) / len(statuses)
    assert 0.75 < delivered < 0.95
    assert not any("READ" in s for s in statuses)
    wa = MockChannelProvider("whatsapp", seed=11)
    reads = 0
    for i in range(1000):
        wa.send({"instruction_id": f"I{i}"})
        reads += any(e["status"] == "READ" for e in wa.callbacks(f"I{i}"))
    assert 0.5 < reads / 1000 < 0.75


def test_profiles_reject_unknown_fields():
    with pytest.raises(ValueError):
        MockChannelProvider("sms", profile={"delivery_rte": 1})
    with pytest.raises(ValueError):
        MockChannelProvider("email")


# ---------------------------------------------------------------- normalization (E04, E06, E10, E02)
def test_sms_delivered_without_read_stays_unknown(lg):
    p = mock("sms", delivery_rate=1, reply_rate=0, stop_rate=0)
    iid, out = send(lg, p, "sms")
    assert out.outcome == "SENT"
    envs = apply_all(lg, p.callbacks(iid))
    assert [e["status"] for e in envs] == ["ACCEPTED", "DELIVERED"]
    assert all(e["read_status"] == "UNKNOWN" and not e["counts_as_reply"] for e in envs)
    assert envs[-1]["event_type"] == "provider.delivery" and envs[-1]["provenance"] == "SIMULATED"
    assert lg.touch(iid)["status"] == "DELIVERED"
    # a bogus SMS "READ" is not recorded as a read
    bogus = apply_callback(lg, {**p.callbacks(iid)[-1], "status": "READ"})
    assert bogus["read_status"] == "UNKNOWN" and bogus["ignored_reason"] == "SMS_READ_NOT_TRACKED"
    assert lg.borrower_state("B1")["stop_for_day"] == []


def test_duplicate_and_late_callbacks_never_change_state(lg):
    p = mock("whatsapp", delivery_rate=1, read_rate=0)
    iid, _ = send(lg, p, "whatsapp")
    sent, delivered = p.callbacks(iid)
    envs = apply_all(lg, [delivered, sent, dict(delivered)])   # SENT arrives late, DELIVERED twice
    assert envs[0]["applied"] and lg.touch(iid)["status"] == "DELIVERED"
    assert (envs[1]["applied"], envs[1]["ignored_reason"]) == (False, "STALE")
    assert (envs[2]["applied"], envs[2]["duplicate"], envs[2]["ignored_reason"]) == (False, True, "DUPLICATE")
    assert envs[2]["dedupe_key"] == envs[0]["dedupe_key"]
    assert lg.touch(iid)["status"] == "DELIVERED"


def test_chaos_stream_never_rewinds_a_touch(lg):
    """Many borrowers, chaos profile (dupes, reordering, timeouts): status rank never decreases."""
    p = MockChannelProvider("whatsapp", seed=5, profile="chaos")
    lg.upsert_campaign("C9", budget_per_case_paise=100_000, start_date=D, end_date=D + timedelta(days=10))
    for n in range(25):
        lg.enroll("C9", [add_loan(lg, f"Z{n}", consents=ALL)])
    ex = Executor(lg, {"whatsapp": p})
    dup_seen = 0
    for n in range(25):
        r = lg.reserve(ins(f"Z{n}", "whatsapp", at(10), camp="C9", tpl="WA_HI"))
        ex.dispatch(r.instruction_id)
        rank = RANK.get(lg.touch(r.instruction_id)["status"], -1)
        for ev in p.callbacks(r.instruction_id):
            env = apply_callback(lg, ev)
            st = lg.touch(r.instruction_id)["status"]
            assert env["touch_status"] == st
            dup_seen += env["duplicate"]
            if env["ignored_reason"] in ("DUPLICATE", "STALE"):
                assert not env["applied"]
            if st != "FAILED":
                assert RANK[st] >= rank           # never rewinds (E04)
                rank = RANK[st]
    assert dup_seen > 0
    assert lg.cap_breaches() == []


def test_click_updates_reachability_but_is_not_a_reply(lg):
    p = mock("whatsapp", delivery_rate=1, read_rate=1, click_rate=1, reply_rate=0, stop_rate=0)
    iid, _ = send(lg, p, "whatsapp", hh=10)
    queued = lg.reserve(ins("B1", "sms", at(14))).instruction_id
    envs = apply_all(lg, p.callbacks(iid))
    click = envs[-1]
    assert click["status"] == "CLICKED" and click["event_type"] == "provider.delivery"
    assert (click["counts_as_reply"], click["reachability"], click["read_status"]) == (False, "REACHABLE", "READ")
    assert lg.touch(queued)["status"] == "RESERVED"          # no stop-for-day: not a reply
    assert lg.borrower_state("B1")["stop_for_day"] == []


def test_whatsapp_reply_is_a_reply_and_stops_for_the_day(lg):
    p = mock("whatsapp", delivery_rate=1, read_rate=1, click_rate=0, reply_rate=1, stop_rate=0)
    iid, _ = send(lg, p, "whatsapp", hh=10)
    queued = lg.reserve(ins("B1", "sms", at(14))).instruction_id
    msg = apply_all(lg, p.callbacks(iid))[-1]
    assert msg["event_type"] == "borrower.message" and msg["counts_as_reply"] and not msg["stop"]
    assert lg.touch(queued)["status"] == "CANCELED"
    assert any(e["event_type"] == "reply_received" for e in lg.borrower_state("B1")["events"])


def _call_event(iid, ch, status, **kw):
    return {"channel": ch, "instruction_id": iid, "provider_ref": "REF", "status": status, "at": 30,
            "provenance": "SIMULATED", **kw}


def test_ivr_invalid_digit_and_no_answer_produce_no_reply(lg):
    p = mock("ivr", answer_rate=1, valid_digit_rate=0, invalid_digit_rate=1, stop_rate=0)
    iid, _ = send(lg, p, "ivr", hh=10)
    (ev,) = p.callbacks(iid)
    assert ev["dtmf"] not in IVR_MENU
    queued = lg.reserve(ins("B1", "sms", at(14))).instruction_id
    env = apply_callback(lg, {**ev, "duration_s": 6})          # under the 10 s pickup threshold
    assert (env["event_type"], env["outcome"], env["intent"], env["counts_as_reply"]) == \
           ("call.outcome", "ANSWERED_INVALID_INPUT", None, False)
    dup = apply_callback(lg, {**ev, "duration_s": 6})          # duplicate completion -> one outcome
    assert dup["duplicate"] and not dup["applied"]
    assert lg.touch(queued)["status"] == "RESERVED"
    assert not any(e["event_type"] == "reply_received" for e in lg.borrower_state("B1")["events"])

    iid2, _ = send(lg, mock("ivr", answer_rate=0, busy_rate=0), "ivr", hh=12)
    na = apply_callback(lg, _call_event(iid2, "ivr", "NO_ANSWER", duration_s=0))
    assert (na["outcome"], na["intent"], na["counts_as_reply"], na["reachability"]) == ("NO_ANSWER", None, False, None)
    assert lg.touch(queued)["status"] == "RESERVED"


def test_ivr_pickup_over_threshold_stops_day_but_is_not_a_reply(lg):
    iid, _ = send(lg, mock("ivr"), "ivr", hh=10)
    queued = lg.reserve(ins("B1", "sms", at(14))).instruction_id
    env = apply_callback(lg, _call_event(iid, "ivr", "ANSWERED", duration_s=25, dtmf=None))
    assert (env["outcome"], env["counts_as_reply"]) == ("ANSWERED_NO_INPUT", False)
    assert lg.touch(queued)["status"] == "CANCELED"
    assert lg.borrower_state("B1")["stop_for_day"][0]["trigger"] == "CALL_PICKUP"


def test_ivr_valid_digit_is_a_captured_response_and_9_is_stop(lg):
    iid, _ = send(lg, mock("ivr"), "ivr", hh=10)
    env = apply_callback(lg, _call_event(iid, "ivr", "ANSWERED", duration_s=5, dtmf="1"))
    assert (env["outcome"], env["intent"], env["counts_as_reply"]) == \
           ("ANSWERED_RESPONSE", "PAYMENT_LINK_REQUESTED", True)
    iid2, _ = send(lg, mock("ivr"), "ivr", hh=12, bid="B2")
    env = apply_callback(lg, _call_event(iid2, "ivr", "ANSWERED", duration_s=5, dtmf="9"))
    assert env["stop"] and lg.borrower_state("B2")["state"]["stopped"] == 1


def test_voicebot_outcomes(lg):
    iid, _ = send(lg, mock("voicebot"), "voicebot", hh=10)
    env = apply_callback(lg, _call_event(iid, "voicebot", "COMPLETED", duration_s=60,
                                         intent={"name": "PROMISE_TO_PAY", "ptp_in_days": 2}))
    assert (env["intent"], env["counts_as_reply"], env["outcome"]) == ("PROMISE_TO_PAY", True, "ANSWERED_RESPONSE")
    iid2, _ = send(lg, mock("voicebot"), "voicebot", hh=12, bid="B2")
    env = apply_callback(lg, _call_event(iid2, "voicebot", "DECLINED", duration_s=3))
    assert (env["outcome"], env["intent"], env["counts_as_reply"]) == ("DECLINED", None, False)
    iid3, _ = send(lg, mock("voicebot"), "voicebot", hh=14, bid="B3")
    env = apply_callback(lg, _call_event(iid3, "voicebot", "COMPLETED", duration_s=40, intent=None))
    assert (env["outcome"], env["intent"], env["counts_as_reply"]) == ("ANSWERED_NO_INPUT", None, False)


def test_stop_on_sms_cancels_queued_whatsapp(lg):
    p = mock("sms", delivery_rate=1, stop_rate=1)
    iid, _ = send(lg, p, "sms", hh=10)
    wa = lg.reserve(ins("B1", "whatsapp", at(12))).instruction_id
    ivr = lg.reserve(ins("B1", "ivr", at(14))).instruction_id
    envs = apply_all(lg, p.callbacks(iid))
    stop = envs[-1]
    assert stop["event_type"] == "borrower.message" and stop["stop"] and not stop["counts_as_reply"]
    assert stop["canceled"] == 2
    for q in (wa, ivr):
        t = lg.touch(q)
        assert (t["status"], t["reason"]) == ("CANCELED", "STOP_OPT_OUT")
    assert all(c["opted_out"] == 1 for c in lg.borrower_state("B1")["consents"])
    assert is_stop_text("  stop. ") and is_stop_text("STOP calling me") and not is_stop_text("stopped paying?")


def test_provider_timeout_goes_uncertain_then_status_resolves(lg):
    p = mock("sms", timeout_rate=1, timeout_lost_share=0, delivery_rate=1)
    iid, out = send(lg, p, "sms")
    assert out.outcome == "UNCERTAIN" and lg.touch(iid)["status"] == "UNCERTAIN"
    ex = Executor(lg, {"sms": p})
    assert ex.resolve_uncertain(iid) == "DELIVERED"
    assert p.sent == [iid]                                   # asked, never resent
    envs = apply_all(lg, p.callbacks(iid))                   # late callbacks still fine
    assert lg.touch(iid)["status"] == "DELIVERED" and envs[-1]["provider_ref"].startswith("SIM-SMS-")

    lost = mock("sms", timeout_rate=1, timeout_lost_share=1)
    iid2, out2 = send(lg, lost, "sms", hh=12)
    assert out2.outcome == "UNCERTAIN"
    assert Executor(lg, {"sms": lost}).resolve_uncertain(iid2) == "FAILED"
    assert lost.callbacks(iid2) == [] and lost.sent == []


def test_unknown_instruction_is_ignored(lg):
    env = apply_callback(lg, _call_event("INS-nope", "ivr", "NO_ANSWER"))
    assert env["ignored_reason"] == "UNKNOWN_INSTRUCTION" and not env["applied"]


def test_full_mock_run_all_channels(lg):
    providers = build_providers("mock", seed=3, env={})
    ex = Executor(lg, providers)
    lg.upsert_campaign("C8", budget_per_case_paise=100_000, start_date=D, end_date=D + timedelta(days=10))
    envs = []
    for n in range(15):
        lg.enroll("C8", [add_loan(lg, f"M{n}", consents=ALL)])
        for hh, ch in zip((8, 9, 10, 11), CHANNELS):
            r = lg.reserve(ins(f"M{n}", ch, at(hh), camp="C8", tpl=TPL[ch]))
            if r.reserved:
                ex.dispatch(r.instruction_id)
                envs += apply_all(lg, providers[ch].callbacks(r.instruction_id))
    assert envs and all(e["provenance"] == "SIMULATED" for e in envs)
    assert all(e["read_status"] == "UNKNOWN" for e in envs if e["channel"] == "sms")
    assert not any(e["counts_as_reply"] for e in envs if e["status"] in ("DELIVERED", "READ", "CLICKED"))
    assert lg.cap_breaches() == []


# ---------------------------------------------------------------- phone demo: Messages.app SMS
class Recorder:
    def __init__(self, returncodes=(0,)):
        self.calls, self.codes = [], list(returncodes)

    def __call__(self, cmd):
        self.calls.append(cmd)
        return SimpleNamespace(returncode=self.codes.pop(0) if self.codes else 0, stdout="", stderr="no SMS account")


SMS_ENV = {"KAIROS_DEMO_SMS": "1", "KAIROS_DEMO_ALLOWLIST": f"{PHONE}, +447700900123"}


def test_messages_sms_refuses_without_env_flag():
    rec = Recorder()
    p = MessagesAppSmsProvider(env={"KAIROS_DEMO_ALLOWLIST": PHONE}, runner=rec, dry_run=False, platform="darwin")
    r = p.send(touch_dict())
    assert r.status == "REJECTED" and r.error.startswith("DEMO_DISABLED") and rec.calls == []


def test_messages_sms_refuses_non_allowlisted_number_and_masks_it():
    rec = Recorder()
    p = MessagesAppSmsProvider(env=SMS_ENV, runner=rec, dry_run=False, platform="darwin")
    r = p.send(touch_dict(to=OTHER))
    assert r.status == "REJECTED" and r.error.startswith("NOT_ALLOWLISTED") and rec.calls == []
    assert "4155550123" not in r.error and r.error.endswith("0123")


def test_messages_sms_refuses_unapproved_template_and_free_text():
    p = MessagesAppSmsProvider(env=SMS_ENV, runner=Recorder(), dry_run=False, platform="darwin")
    assert p.send(touch_dict(tpl="SMS_EN")).error.startswith("TEMPLATE_NOT_APPROVED")
    assert p.send(touch_dict(tpl=None, text="pay now!!")).error.startswith("TEMPLATE_NOT_APPROVED")


def test_messages_sms_defaults_to_dry_run_and_builds_osascript():
    rec = Recorder()
    p = MessagesAppSmsProvider(env=SMS_ENV, runner=rec)
    assert p.dry_run is True
    r = p.send(touch_dict())
    assert (r.status, r.error) == ("REJECTED", "DRY_RUN") and rec.calls == []
    (pv,) = p.dry_runs
    assert pv.recipient_masked == "+********5678" and PHONE not in json.dumps(pv.request)
    cmd = MessagesAppSmsProvider.osascript_command(PHONE, "hi")
    assert cmd == ["osascript",
                   "-e", "on run argv",
                   "-e", 'tell application "Messages"',
                   "-e", "set targetAccount to 1st account whose service type = SMS",
                   "-e", "set targetParticipant to participant (item 1 of argv) of targetAccount",
                   "-e", "send (item 2 of argv) to targetParticipant",
                   "-e", "end tell",
                   "-e", "end run",
                   PHONE, "hi"]


def test_messages_sms_real_send_uses_injected_runner_and_keeps_delivery_unknown(lg):
    rec = Recorder()
    p = MessagesAppSmsProvider(env=SMS_ENV, runner=rec, dry_run=False, platform="darwin")
    r = p.send(touch_dict(tpl="DEMO_SMS_REMINDER_EN", params={"amount": "4,999"}))
    assert r.status == "ACCEPTED" and len(rec.calls) == 1
    assert rec.calls[0][-2] == PHONE and "Rs 4,999" in rec.calls[0][-1] and "STOP" in rec.calls[0][-1]
    assert p.send(touch_dict(tpl="DEMO_SMS_REMINDER_EN")).status == "ACCEPTED" and len(rec.calls) == 1  # once
    assert p.status("INS-1", r.provider_ref) == "ACCEPTED" and p.status("INS-x", None) == "UNKNOWN"
    assert [e["status"] for e in p.callbacks("INS-1")] == ["ACCEPTED"]
    assert MessagesAppSmsProvider(env=SMS_ENV, runner=Recorder(), dry_run=False, platform="linux") \
        .send(touch_dict()).error.startswith("NOT_MACOS")


def test_messages_sms_imessage_fallback_only_when_configured():
    rec = Recorder(returncodes=(1, 0))
    p = MessagesAppSmsProvider(env=SMS_ENV, runner=rec, dry_run=False, platform="darwin")
    assert p.send(touch_dict()).error.startswith("OSASCRIPT_FAILED") and len(rec.calls) == 1
    rec = Recorder(returncodes=(1, 0))
    p = MessagesAppSmsProvider(env={**SMS_ENV, "KAIROS_DEMO_SMS_IMESSAGE_FALLBACK": "1"}, runner=rec,
                               dry_run=False, platform="darwin")
    assert p.send(touch_dict()).status == "ACCEPTED"
    assert "service type = iMessage" in rec.calls[1][6]


def test_phone_demo_dry_run_through_executor_releases_budget(lg):
    """Ledger borrower phone is the allow-listed demo phone; dry run -> FAILED(DRY_RUN), nothing counted."""
    with lg._spine() as c:
        c.execute("UPDATE borrowers SET phone=? WHERE borrower_id='B1'", (PHONE,))
    providers = build_providers("phone-demo", ledger=lg, env=SMS_ENV, runner=Recorder())
    r = lg.reserve(ins("B1", "sms", at(10), tpl="DEMO_SMS_HELLO"))
    out = Executor(lg, providers).dispatch(r.instruction_id)
    assert (out.outcome, out.reason) == ("FAILED", "DRY_RUN")
    assert lg.campaign_state("C1")["committed_paise"] == 0
    assert providers["sms"].dry_runs[0].recipient_masked == "+********5678"


# ---------------------------------------------------------------- phone demo: WhatsApp Cloud API
WA_ENV = {"KAIROS_DEMO_WHATSAPP": "1", "KAIROS_DEMO_ALLOWLIST": PHONE, "WA_TOKEN": "EAAJBtesttoken1234",
          "WA_PHONE_NUMBER_ID": "106540352242922"}


class FakeTransport:
    def __init__(self, code=200, body=None):
        self.calls, self.code = [], code
        self.body = body if body is not None else {
            "messaging_product": "whatsapp", "contacts": [{"input": "919812345678", "wa_id": "919812345678"}],
            "messages": [{"id": "wamid.TEST123"}]}

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body)))
        return self.code, self.body


def wa_touch(**kw):
    return {"instruction_id": "INS-W1", "borrower_id": "B1", "channel": "whatsapp", "template_id": "DEMO_WA_HELLO",
            "to": PHONE, **kw}


def test_whatsapp_refuses_without_flag_or_allowlist_or_creds():
    t = FakeTransport()
    assert WhatsAppCloudTestProvider(env={**WA_ENV, "KAIROS_DEMO_WHATSAPP": "0"}, transport=t, dry_run=False) \
        .send(wa_touch()).error.startswith("DEMO_DISABLED")
    assert WhatsAppCloudTestProvider(env=WA_ENV, transport=t, dry_run=False) \
        .send(wa_touch(to=OTHER)).error.startswith("NOT_ALLOWLISTED")
    assert WhatsAppCloudTestProvider(env={k: v for k, v in WA_ENV.items() if k != "WA_TOKEN"}, transport=t,
                                     dry_run=False).send(wa_touch()).error.startswith("MISSING_CREDENTIALS")
    assert WhatsAppCloudTestProvider(env=WA_ENV, transport=t, dry_run=False) \
        .send(wa_touch(template_id="WA_HI")).error.startswith("TEMPLATE_NOT_APPROVED")
    assert t.calls == []


def test_whatsapp_builds_graph_api_template_request():
    p = WhatsAppCloudTestProvider(env=WA_ENV)
    req = p.build_request(wa_touch(), PHONE)
    assert req["method"] == "POST"
    assert req["url"] == "https://graph.facebook.com/v23.0/106540352242922/messages"
    assert req["headers"] == {"Authorization": "Bearer EAAJBtesttoken1234", "Content-Type": "application/json"}
    assert req["json"] == {"messaging_product": "whatsapp", "recipient_type": "individual", "to": "919812345678",
                           "type": "template", "template": {"name": "hello_world", "language": {"code": "en_US"}},
                           "biz_opaque_callback_data": "INS-W1"}
    p2 = WhatsAppCloudTestProvider(env={**WA_ENV, "WA_GRAPH_VERSION": "v27.0"},
                                   templates={"T": {"name": "emi_due", "language": "en", "body_params": ["amount"]}})
    req2 = p2.build_request(wa_touch(template_id="T", params={"amount": "5,000"}), PHONE)
    assert req2["url"].startswith("https://graph.facebook.com/v27.0/")
    assert req2["json"]["template"]["components"] == [{"type": "body", "parameters": [{"type": "text", "text": "5,000"}]}]


def test_whatsapp_defaults_to_dry_run_with_masked_preview():
    t = FakeTransport()
    p = WhatsAppCloudTestProvider(env=WA_ENV, transport=t)
    r = p.send(wa_touch())
    assert (r.status, r.error) == ("REJECTED", "DRY_RUN") and t.calls == []
    dumped = json.dumps(p.dry_runs[0].request)
    assert "EAAJBtesttoken1234" not in dumped and "9812345678" not in dumped
    assert p.dry_runs[0].request["json"]["to"] == "+********5678"


def test_whatsapp_real_send_with_injected_transport_and_webhook_roundtrip(lg):
    t = FakeTransport()
    p = WhatsAppCloudTestProvider(env=WA_ENV, transport=t, dry_run=False,
                                  recipient_lookup=lambda touch: PHONE)
    iid, out = send(lg, p, "whatsapp", tpl="DEMO_WA_HELLO")
    assert (out.outcome, out.provider_ref) == ("SENT", "wamid.TEST123") and len(t.calls) == 1
    assert t.calls[0][2]["biz_opaque_callback_data"] == iid
    hook = {"entry": [{"changes": [{"value": {
        "statuses": [{"id": "wamid.TEST123", "status": "delivered", "timestamp": "1795150000", "recipient_id": "919812345678"},
                     {"id": "wamid.TEST123", "status": "sent", "timestamp": "1795149990", "recipient_id": "919812345678"},
                     {"id": "wamid.TEST123", "status": "read", "timestamp": "1795150100", "recipient_id": "919812345678"}],
        "messages": [{"from": "919812345678", "id": "wamid.IN1", "timestamp": "1795150200", "type": "text",
                      "text": {"body": "STOP"}}]}}]}]}
    envs = apply_all(lg, p.ingest_webhook(hook))
    assert [e["status"] for e in envs] == ["DELIVERED", "SENT", "READ", "REPLY"]
    assert envs[1]["ignored_reason"] == "STALE" and envs[2]["read_status"] == "READ"
    assert envs[3]["stop"] and envs[0]["provenance"] == "DEMO"
    assert lg.borrower_state("B1")["state"]["stopped"] == 1


def test_whatsapp_error_responses():
    bad = FakeTransport(400, {"error": {"code": 131030, "message": "Recipient phone number not in allowed list"}})
    r = WhatsAppCloudTestProvider(env=WA_ENV, transport=bad, dry_run=False).send(wa_touch())
    assert r.status == "REJECTED" and "131030" in r.error
    r = WhatsAppCloudTestProvider(env=WA_ENV, transport=FakeTransport(503, {}), dry_run=False).send(wa_touch())
    assert r.status == "UNKNOWN"


def test_click_to_chat_url_and_masking():
    assert click_to_chat_url("+91 98123-45678", "EMI due: Rs 5,000 & thanks") == \
        "https://wa.me/919812345678?text=EMI%20due%3A%20Rs%205%2C000%20%26%20thanks"
    assert mask_number(PHONE) == "+********5678"
    assert mask_number("9812345678") == "******5678"
    assert mask_number(None) == "<none>"


def test_build_providers_modes(monkeypatch):
    m = build_providers("mock", env={})
    assert set(m) == {"sms", "whatsapp", "ivr", "voicebot", "email"}
    assert all(isinstance(m[c], MockChannelProvider) for c in CHANNELS)
    assert isinstance(m["email"], FakeProvider)          # K20 email module absent on this branch
    d = build_providers("phone-demo", env={"KAIROS_DEMO_SMS": "1"})
    assert isinstance(d["sms"], MessagesAppSmsProvider) and isinstance(d["whatsapp"], WhatsAppCloudTestProvider)
    assert d["sms"].dry_run and d["whatsapp"].dry_run
    assert isinstance(d["ivr"], MockChannelProvider)
    assert isinstance(build_providers(env={"KAIROS_CHANNELS": "phone-demo", "KAIROS_DEMO_SMS": "1"})["sms"],
                      MessagesAppSmsProvider)
    # no SMS demo flag: phone-demo falls back to the SIMULATED sms mock (Android > Messages > mock)
    assert isinstance(build_providers("phone-demo", env={})["sms"], MockChannelProvider)
    with pytest.raises(ValueError):
        build_providers("live", env={})
    assert MODES == ("mock", "phone-demo")


# ---------------------------------------------------------------- FSM-F1: receipts for touches never dispatched
def test_receipt_for_a_queued_touch_is_an_orphan_and_does_not_burn_the_dedupe_key(lg):
    p = mock("sms")
    iid = lg.reserve(ins("B1", "sms", at(10), tpl="SMS_EN")).instruction_id
    early = {"channel": "sms", "instruction_id": iid, "provider_ref": "R1", "status": "DELIVERED", "at": 5,
             "provenance": "SIMULATED"}
    env = apply_callback(lg, early)
    assert env["ignored_reason"] == "NOT_DISPATCHED" and not env["applied"] and env["touch_status"] == "RESERVED"
    assert [a["type"] for a in lg.audit_log(borrower_id="B1")].count("PROVIDER_STATUS_ORPHAN") == 1
    assert lg.touch(iid)["status"] == "RESERVED"
    # the real dispatch still runs through the send-time re-check; the same receipt now applies
    assert Executor(lg, {"sms": p}).dispatch(iid).outcome == "SENT"
    again = apply_callback(lg, early)
    assert not again["duplicate"] and again["applied"] and lg.touch(iid)["status"] == "DELIVERED"


def test_call_outcome_for_a_queued_touch_changes_nothing(lg):
    iid = lg.reserve(ins("B1", "ivr", at(10), tpl="IVR_HI")).instruction_id
    env = apply_callback(lg, _call_event(iid, "ivr", "COMPLETED", duration_s=40, dtmf="1"))
    assert env["ignored_reason"] == "NOT_DISPATCHED" and not env["applied"] and not env["counts_as_reply"]
    assert lg.touch(iid)["status"] == "RESERVED"
    assert lg.borrower_state("B1")["stop_for_day"] == []
