"""CM-03 live WhatsApp mode (cm03/live.py). Meta is NEVER called: every test injects a fake HTTP transport.

The live number below is a fictional +1 555 number; no real number or token appears in this file.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kairos_backend.cm03 import live as L
from kairos_backend.cm03.api import build_router

from tests.cm03_helpers import B2001, Chat

NUM = "+15550001234"            # fictional (555) - the "user's phone" in these tests
OTHER = "+15550009876"          # fictional, NOT allow-listed
TOKEN = "EAAtest-TOKEN-must-never-be-logged-9f8e7d"
APP_SECRET = "test-app-secret-must-never-be-logged"
PNID = "106540352242922"


class FakeTransport:
    """Stands in for urllib: records every request, answers like the Cloud API send endpoint."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "json": json.loads(body)})
        return 200, {"messaging_product": "whatsapp", "contacts": [{"input": NUM[1:], "wa_id": NUM[1:]}],
                     "messages": [{"id": f"wamid.TEST{len(self.calls):04d}"}]}

    def bodies(self):
        return [c["json"]["text"]["body"] for c in self.calls]


class FakeTime:
    def __init__(self):
        self.mono, self.wall, self.slept = 1000.0, 1_763_600_000.0, []

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.mono += s


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("KAIROS_DEMO_RECIPIENTS_FILE", str(tmp_path / "no_recipients.json"))  # never the real file
    monkeypatch.setenv("KAIROS_CM03_LIVE_WHATSAPP", "1")
    monkeypatch.setenv("WA_TOKEN", TOKEN)
    monkeypatch.setenv("WA_PHONE_NUMBER_ID", PNID)
    monkeypatch.setenv("WA_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("KAIROS_DEMO_ALLOWLIST", NUM)
    monkeypatch.delenv("WA_GRAPH_VERSION", raising=False)
    monkeypatch.delenv("KAIROS_CM03_LIVE_MAX_PER_HOUR", raising=False)
    return monkeypatch


@pytest.fixture
def world(env, policy, tmp_path):
    chat = Chat(policy, tmp_path)
    fake, ft = FakeTransport(), FakeTime()
    adapter = L.install(chat.engine, transport=fake, sleep=ft.sleep, monotonic=lambda: ft.mono, wall=lambda: ft.wall)
    app = FastAPI()
    app.include_router(build_router(chat.engine))
    return TestClient(app), chat, adapter, fake, ft


def _sign(raw: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def post(client, payload, secret=APP_SECRET, sign=True):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = _sign(raw, secret)
    return client.post("/cm03/whatsapp/inbound", content=raw, headers=headers)


def wh(frm, text=None, mid="wamid.IN1", image=None):
    """A Cloud API 'messages' webhook (shape: Meta webhooks reference, messages)."""
    if image is None:
        m = {"from": frm.lstrip("+"), "id": mid, "timestamp": "1763600000", "type": "text", "text": {"body": text}}
    else:
        m = {"from": frm.lstrip("+"), "id": mid, "timestamp": "1763600000", "type": "image",
             "image": {"id": "media-1", "mime_type": "image/jpeg", "caption": text}}
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA-ID", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "15550000000", "phone_number_id": PNID},
        "contacts": [{"profile": {"name": "Demo"}, "wa_id": frm.lstrip("+")}], "messages": [m]}}]}]}


def wh_status(wamid, status, recipient=NUM):
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA-ID", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "15550000000", "phone_number_id": PNID},
        "statuses": [{"id": wamid, "status": status, "timestamp": "1763600100", "recipient_id": recipient.lstrip("+")}]}}]}]}


def otp_from(fake):
    for b in reversed(fake.bodies()):
        m = re.search(r"(?<!\d)(\d{6})(?!\d)", b)
        if m:
            return m.group(1)
    return None


def say(client, text, n=[0]):  # noqa: B006 - unique message ids across one test
    n[0] += 1
    r = post(client, wh(NUM, text, mid=f"wamid.IN{n[0]}"))
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


# ------------------------------------------------------------------ off unless every flag is set
@pytest.mark.parametrize("drop", ["KAIROS_CM03_LIVE_WHATSAPP", "WA_TOKEN", "WA_PHONE_NUMBER_ID", "WA_APP_SECRET",
                                  "KAIROS_DEMO_ALLOWLIST"])
def test_live_mode_is_off_unless_every_flag_is_set(env, policy, tmp_path, drop):
    assert L.live_config().enabled
    env.delenv(drop)
    cfg = L.live_config()
    assert not cfg.enabled and cfg.missing
    chat = Chat(policy, tmp_path)
    fake = FakeTransport()
    adapter = L.install(chat.engine, transport=fake)
    adapter.note_inbound(NUM)
    res = adapter.send(NUM, "hello", kind="reply")
    assert res["status"] == "DRY_RUN" and fake.calls == []


@pytest.mark.parametrize("key,value", [("KAIROS_CM03_LIVE_WHATSAPP", "0"), ("KAIROS_CM03_LIVE_WHATSAPP", "true"),
                                       ("WA_PHONE_NUMBER_ID", "not-a-number"),
                                       ("KAIROS_DEMO_ALLOWLIST", NUM + "," + OTHER),     # exactly one number
                                       ("KAIROS_DEMO_ALLOWLIST", "+910000002001"),       # synthetic never counts
                                       ("KAIROS_CM03_LIVE_BORROWER", "B-REAL-1")])
def test_live_mode_off_for_bad_values(env, key, value):
    env.setenv(key, value)
    assert not L.live_config().enabled


def test_default_is_dry_run_exactly_as_today(policy, tmp_path, monkeypatch):
    for k in ("KAIROS_CM03_LIVE_WHATSAPP", "WA_TOKEN", "WA_PHONE_NUMBER_ID", "WA_APP_SECRET", "KAIROS_DEMO_ALLOWLIST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KAIROS_DEMO_RECIPIENTS_FILE", str(tmp_path / "none.json"))
    chat = Chat(policy, tmp_path)
    fake = FakeTransport()
    L.install(chat.engine, transport=fake)
    r = chat.say(B2001, "hi")
    assert r["state"] == "AWAIT_OTP" and chat.otp(B2001)          # the simulated inbox still works
    assert fake.calls == []


# ------------------------------------------------------------------ inbound filter + signature
def test_non_allowlisted_number_is_ignored_audited_and_never_answered(world):
    client, chat, adapter, fake, _ = world
    r = post(client, wh(OTHER, "Hi, what is my balance?"))
    assert r.status_code == 200
    assert r.json()["results"] == [] and r.json()["ignored"] == 1
    assert fake.calls == []
    conn = chat.ledger._conn()
    assert conn.execute("SELECT COUNT(*) FROM cm03_conversations").fetchone()[0] == 0
    rows = conn.execute("SELECT detail FROM audit WHERE type='CM03_LIVE_IGNORED_SENDER'").fetchall()
    assert len(rows) == 1 and "9876" in rows[0][0] and "5550009876" not in rows[0][0]


def test_signature_required_in_live_mode(world, env):
    client, _, _, fake, _ = world
    assert post(client, wh(NUM, "Hi"), sign=False).status_code == 401
    assert post(client, wh(NUM, "Hi"), secret="wrong-secret").status_code == 401
    env.delenv("WA_APP_SECRET")       # flag on, secret missing: refuse everything (fail closed)
    assert post(client, wh(NUM, "Hi"), sign=False).status_code == 503
    assert fake.calls == []
    env.setenv("WA_APP_SECRET", APP_SECRET)
    assert post(client, wh(NUM, "Hi", mid="wamid.SIGNED")).status_code == 200
    assert len(fake.calls) == 2


def test_console_inbound_takes_synthetic_numbers_only(world):
    client, _, _, fake, _ = world
    ok = client.post("/cm03/console/inbound", json={"from": B2001, "text": "hi", "message_id": "c-1"})
    assert ok.status_code == 200 and ok.json()["results"][0]["state"] == "AWAIT_OTP"
    assert client.post("/cm03/console/inbound", json={"from": NUM, "text": "hi"}).status_code == 403
    assert fake.calls == []


# ------------------------------------------------------------------ outbound request + OTP round trip
def test_allowlisted_inbound_builds_the_cloud_api_request(world):
    client, chat, adapter, fake, _ = world
    r = say(client, "Hi")
    assert r["state"] == "AWAIT_OTP"
    assert len(fake.calls) == 2                       # the OTP message, then the greeting
    for c in fake.calls:
        assert c["url"] == f"https://graph.facebook.com/v23.0/{PNID}/messages"
        assert c["headers"] == {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
        assert c["json"]["messaging_product"] == "whatsapp" and c["json"]["recipient_type"] == "individual"
        assert c["json"]["to"] == NUM[1:] and c["json"]["type"] == "text"
        assert c["json"]["text"]["preview_url"] is False
    assert re.search(r"\d{6}", fake.calls[0]["json"]["text"]["body"])     # the code really goes to the phone
    assert fake.calls[1]["json"]["text"]["body"] == r["replies"][0]
    # the evidence log is masked: no token, no number, no code
    for m in adapter.requests:
        assert m["request"]["headers"]["Authorization"] == "Bearer <redacted>"
        assert m["request"]["json"]["to"] == "+*******1234"
        assert "<WA_PHONE_NUMBER_ID>" in m["request"]["url"]
        assert not re.search(r"(?<!\d)\d{6}(?!\d)", m["request"]["json"]["text"]["body"])
    tl = chat.engine.timeline(r["conversation_id"])
    sends = [e for e in tl if e["kind"] == "TOOL_CALL" and e["name"] in ("send_otp", "send_whatsapp_reply")]
    assert [e["payload"]["result"]["mode"] for e in sends] == ["live", "live"]
    assert all(e["payload"]["result"]["provider_ref"].startswith("wamid.TEST") for e in sends)
    look = [e for e in tl if e["name"] == "lookup_account"][0]
    assert look["payload"]["result"]["identity"] == "live number = SIMULATED demo borrower"


def test_otp_round_trip_on_the_live_number(world):
    client, chat, adapter, fake, _ = world
    say(client, "Hi")
    code = otp_from(fake)
    assert code
    r = say(client, code)
    assert r["verified"] and r["state"] == "VERIFIED" and r["borrower_id"] == "B-2001"
    assert fake.bodies()[-1] == r["replies"][-1]
    r = say(client, "what is my balance")
    assert r["replies"] and fake.bodies()[-1] == r["replies"][-1]
    assert adapter.live_sent == len(fake.calls) >= 4
    assert chat.engine.adapter.inbox(NUM) == []        # live texts (the code) are not kept in the dry-run inbox


def test_wrong_code_is_not_accepted_live(world):
    client, _, _, fake, _ = world
    say(client, "Hi")
    code = otp_from(fake)
    r = say(client, "000000" if code != "000000" else "111111")
    assert not r["verified"] and r["state"] == "AWAIT_OTP"


# ------------------------------------------------------------------ status webhooks
def test_status_webhook_is_recorded_once(world):
    client, chat, adapter, fake, _ = world
    r = say(client, "Hi")
    wamid = "wamid.TEST0002"
    first = post(client, wh_status(wamid, "delivered")).json()
    assert first["statuses"] == {"recorded": 1, "duplicate": 0, "unknown": 0}
    again = post(client, wh_status(wamid, "delivered")).json()
    assert again["statuses"] == {"recorded": 0, "duplicate": 1, "unknown": 0}
    post(client, wh_status(wamid, "read"))
    assert post(client, wh_status("wamid.NOT-OURS", "read")).json()["statuses"]["unknown"] == 1
    tl = chat.engine.timeline(r["conversation_id"])
    dl = [(e["name"], e["payload"]["wamid"]) for e in tl if e["kind"] == "DELIVERY"]
    assert dl == [("DELIVERED", wamid), ("READ", wamid)]
    assert all(e["provenance"] == "LIVE" for e in tl if e["kind"] == "DELIVERY")


# ------------------------------------------------------------------ kill switch, window, rate, kinds
def test_kill_switch_env_flag_stops_sends_at_once(world, env):
    client, chat, adapter, fake, _ = world
    say(client, "Hi")
    n = len(fake.calls)
    env.setenv("KAIROS_CM03_LIVE_WHATSAPP", "0")
    adapter.note_inbound(NUM)
    assert adapter.send(NUM, "Hello", kind="reply")["status"] == "DRY_RUN"
    assert len(fake.calls) == n


def test_kill_switch_outbound_pause_stops_sends(world):
    client, chat, adapter, fake, _ = world
    say(client, "Hi")
    n = len(fake.calls)
    chat.ledger.set_outbound_paused(True, "human:ops", "demo kill switch")
    code = otp_from(fake)
    r = say(client, code)
    assert r["verified"]                                  # the engine still works ...
    assert len(fake.calls) == n                           # ... but nothing leaves the box
    assert adapter.not_sent["OUTBOUND_PAUSED"] >= 1
    tl = chat.engine.timeline(r["conversation_id"])
    last = [e for e in tl if e["name"] == "send_whatsapp_reply"][-1]["payload"]["result"]
    assert last["status"] == "NOT_SENT" and last["reason"] == "OUTBOUND_PAUSED"
    chat.ledger.set_outbound_paused(False, "human:ops", "resume")
    say(client, "balance")
    assert len(fake.calls) > n


def test_window_closed_after_24h_no_business_initiated_free_form(world):
    client, chat, adapter, fake, ft = world
    fresh = L.LiveWhatsAppAdapter(ledger=chat.ledger, transport=fake)
    assert fresh.send(NUM, "Hello", kind="reply")["reason"] == "WINDOW_CLOSED"    # never messaged us: closed
    r = say(client, "Hi")
    say(client, otp_from(fake))
    take = client.post(f"/cm03/conversations/{r['conversation_id']}/takeover", json={"agent": "asha"})
    assert take.status_code == 200
    n = len(fake.calls)
    ft.wall += 24 * 3600 - 60                           # inside 24 h but inside the safety margin: closed
    out = client.post(f"/cm03/conversations/{r['conversation_id']}/agent-message",
                      json={"agent": "asha", "text": "Hi, this is Asha."}).json()
    assert out["sent"] is False and out["reason"] == "WINDOW_CLOSED"
    assert len(fake.calls) == n
    audit = chat.ledger._conn().execute("SELECT COUNT(*) FROM audit WHERE type='CM03_LIVE_NOT_SENT'"
                                        " AND reason='WINDOW_CLOSED'").fetchone()[0]
    assert audit >= 1
    post(client, wh(NUM, "hello again", mid="wamid.REOPEN"))   # the borrower writes: the window reopens
    out = client.post(f"/cm03/conversations/{r['conversation_id']}/agent-message",
                      json={"agent": "asha", "text": "Thanks for writing back."}).json()
    assert out["sent"] is True and out["status"] == "SENT_LIVE"


def test_rate_limit_waits_one_second_and_caps_the_hour(world, env):
    client, chat, adapter, fake, ft = world
    env.setenv("KAIROS_CM03_LIVE_MAX_PER_HOUR", "3")
    adapter.note_inbound(NUM)
    for i in range(3):
        assert adapter.send(NUM, f"message {i}", kind="reply")["status"] == "SENT_LIVE"
    assert ft.slept == [1.0, 1.0]                     # >= 1 s between sends
    res = adapter.send(NUM, "one too many", kind="reply")
    assert res == {"status": "NOT_SENT", "reason": "RATE_LIMITED", "mode": "live"}
    assert len(fake.calls) == 3
    ft.mono += 3601
    assert adapter.send(NUM, "next hour", kind="reply")["status"] == "SENT_LIVE"


def test_only_cm03_message_kinds_and_only_the_live_number(world):
    client, chat, adapter, fake, _ = world
    adapter.note_inbound(NUM)
    assert adapter.send(NUM, "free text from somewhere", kind="text")["reason"] == "KIND_NOT_ALLOWED"
    adapter.note_inbound(OTHER)
    assert adapter.send(OTHER, "hello", kind="reply")["status"] == "DRY_RUN"
    assert fake.calls == []


# ------------------------------------------------------------------ the whole story + masking
def test_story_otp_answer_fake_screenshot_takeover_stop_reset(world, caplog):
    caplog.set_level(logging.DEBUG)
    client, chat, adapter, fake, _ = world
    r = say(client, "Hi")
    cid = r["conversation_id"]
    say(client, otp_from(fake))
    say(client, "what is my balance")
    assert client.post("/cm03/live/simulated-ocr", json={"preset": "edited"}).json()["armed"] == "edited"
    shot = post(client, wh(NUM, "I already paid, here is the screenshot", mid="wamid.IMG", image=True)).json()["results"][0]
    assert shot["fraud_suspect"] and shot["state"] == "HANDOFF"
    assert client.get("/cm03/live/status").json()["simulated_ocr_armed"] is None      # one image only
    assert client.post(f"/cm03/conversations/{cid}/takeover", json={"agent": "asha"}).status_code == 200
    n = len(fake.calls)
    say(client, "hello?")                                                             # the bot stays silent
    assert len(fake.calls) == n
    am = client.post(f"/cm03/conversations/{cid}/agent-message", json={"agent": "asha", "text": "Hi, Asha here."}).json()
    assert am["status"] == "SENT_LIVE" and fake.bodies()[-1] == "Hi, Asha here."
    stop = say(client, "STOP")
    assert "STOP_HONOURED" in stop["outcomes"] and stop["replies"] == []
    refused = client.post(f"/cm03/conversations/{cid}/agent-message", json={"agent": "asha", "text": "one more"})
    assert refused.status_code == 409
    st = client.get("/cm03/live/status").json()
    assert st["live"]["enabled"] and st["live"]["number"] == "+*******1234"
    assert st["conversation"]["conversation_id"] == cid and st["conversation"]["owner"] == "human:asha"
    post(client, wh_status("wamid.TEST0001", "delivered"))
    reset = client.post("/cm03/live/reset", json={"agent": "console.operator"}).json()
    assert [c["conversation_id"] for c in reset["closed"]] == [cid] and reset["demo_borrower_restored"]
    assert chat.engine.get(cid).state == "CLOSED"
    stopped = chat.ledger._conn().execute("SELECT stopped FROM borrower_state WHERE borrower_id='B-2001'").fetchone()[0]
    assert stopped == 0
    assert chat.ledger._conn().execute("SELECT COUNT(*) FROM audit WHERE type='CM03_LIVE_CHAT_RESET'").fetchone()[0] == 1
    again = say(client, "Hi")                                                         # a fresh, verify-first chat
    assert again["conversation_id"] != cid and again["state"] == "AWAIT_OTP"

    # nothing sensitive anywhere we write or log
    digits = NUM[1:]
    audit = json.dumps([dict(x) for x in chat.ledger._conn().execute("SELECT * FROM audit").fetchall()])
    timeline = json.dumps(chat.engine.timeline(cid) + chat.engine.timeline(again["conversation_id"]))
    status = json.dumps(client.get("/cm03/live/status").json())
    requests_log = json.dumps(list(adapter.requests))
    convs = json.dumps(client.get("/cm03/conversations").json())
    for blob in (caplog.text, audit, timeline, status, requests_log, convs):
        assert digits not in blob and digits[-10:] not in blob
        assert TOKEN not in blob and APP_SECRET not in blob
    assert "wamid.TEST" in caplog.text and "+*******1234" in caplog.text            # the masked form is what is logged
