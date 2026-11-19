"""Journey 2 (/demo/journey/inbound) and CM-03 human takeover. Everything here is SIMULATED: synthetic
borrowers, a synthetic bank feed, a dry-run WhatsApp adapter.

Covers: no disclosure before OTP; fake claims flagged and the genuine one passing; injection refused; while a
person owns a conversation the bot never replies (and hand-back restores it); STOP revokes every channel and
cancels queued touches; human actions are audited; no PII in what the page receives."""
from __future__ import annotations

import json
import re
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kairos_backend import journey_inbound as J
from kairos_backend.cm03.api import build_router
from kairos_backend.cm03.copy import PRE_VERIFY_SAFE
from kairos_backend.cm03.takeover import Takeover, TakeoverError, human_owned

from .cm03_helpers import B1042, B2001, PENDING_REF, Chat

FULL_PHONE = re.compile(r"\+?910\d{9}")


@pytest.fixture
def w():
    world = J.World()
    yield world
    world.close()


def _verified(w, key="A"):
    w.send(key, None, "emi")
    tr = w.send(key, None, "otp")
    assert tr["identity"]["after"] == "VERIFIED"
    return tr


def _keys(tr):
    return [r["key"] for r in tr["reply"]["items"]]


def _payments(w):
    return w.ledger._conn().execute("SELECT COUNT(*) FROM payments").fetchone()[0]


# ---------------------------------------------------------------- disclosure before OTP
def test_no_account_disclosure_before_otp_then_answer_after(w):
    tr = w.send("A", None, "emi")
    assert tr["identity"]["before"] == "NEW" and tr["identity"]["after"] == "AWAIT_OTP"
    assert _keys(tr) == ["GREET_VERIFY"] and set(_keys(tr)) <= PRE_VERIFY_SAFE
    assert tr["guard"]["status"] == "blocked"
    assert "account_summary" not in [a["tool"] for a in tr["actions"]]
    shown = " ".join(m["text"] for m in w.phones["A"]["messages"] if m["who"] == "bot")
    assert "₹" not in shown and "ending" not in shown          # no amount, no account digits before OTP
    again = w.send("A", "what is my balance?", None)            # asking again: still parked, still nothing
    assert set(_keys(again)) <= PRE_VERIFY_SAFE and again["guard"]["status"] == "blocked"
    ok = w.send("A", None, "otp")
    assert ok["identity"]["after"] == "VERIFIED" and ok["guard"]["status"] == "unlocked"
    assert "BALANCE" in _keys(ok)                                # the parked question is answered after OTP
    assert ok["timing"]["engine_ms"] < 30_000


def test_pay_link_and_devanagari_question_after_verification(w):
    _verified(w)
    assert _keys(w.send("A", None, "paylink")) == ["PAY_LINK"]
    tr = w.send("A", None, "hindi")                              # Devanagari: rules unsure, the small model answers
    assert tr["understand"]["intent"] == "BALANCE" and tr["understand"]["decided_by"] == "small_model"
    assert "BALANCE" in _keys(tr)
    rungs = {r["tier"]: r["status"] for r in tr["understand"]["ladder"]}
    assert rungs["Rules"] == "unsure" and rungs["Small model"] == "hit" and rungs["Prism"] == "skip"


# ---------------------------------------------------------------- payment claims
def test_genuine_claim_passes_every_check_and_is_not_marked_paid(w):
    _verified(w)
    paid = _payments(w)
    tr = w.send("A", None, "claim_genuine")
    assert not tr["fraud"] and _keys(tr) == ["CLAIM_PENDING"]
    rows = {r["id"]: r["result"] for r in tr["checks"]["claim"]["rows"]}
    assert rows["format"] == rows["reused"] == rows["amount"] == "pass" and rows["bank"] == "info"
    assert _payments(w) == paid                                   # a claim never marks anything paid


def test_fake_screenshot_is_caught(w):
    _verified(w)
    paid = _payments(w)
    tr = w.send("A", None, "claim_fake_screenshot")
    assert tr["fraud"] and _keys(tr) == ["FRAUD_REFUSE"] and tr["identity"]["after"] == "HANDOFF"
    assert "DOCTORED_ATTACHMENT" in tr["checks"]["fired"]
    rows = {r["id"]: r["result"] for r in tr["checks"]["claim"]["rows"]}
    assert rows["edited"] == rows["hidden"] == rows["reused"] == "fail"
    assert _payments(w) == paid
    q = [x for x in w.state()["queue"] if x["phone"] == "A"]
    assert q and q[0]["reason"] == "FRAUD_SUSPECT" and q[0]["claim_related"]


def test_reused_utr_is_caught(w):
    _verified(w)
    tr = w.send("A", None, "claim_fake_utr")
    assert tr["fraud"] and "REFERENCE_REUSED" in tr["checks"]["fired"]
    claim = w.takeover.latest_claim(tr["conversation_id"])
    assert claim["status"] == "FAKE_SUSPECT"


# ---------------------------------------------------------------- injection
@pytest.mark.parametrize("verified", [False, True])
def test_injection_is_refused_without_disclosure(w, verified):
    if verified:
        _verified(w)
    tr = w.send("A", None, "injection")
    assert tr["fraud"] and "PROMPT_INJECTION" in tr["checks"]["fired"]
    assert _keys(tr) == ["FRAUD_REFUSE"] and "account_summary" not in [a["tool"] for a in tr["actions"]]
    assert not tr["understand"]["ran"]                            # the detector fired before any intent model


# ---------------------------------------------------------------- takeover: engine level
@pytest.fixture
def chat(policy, tmp_path):
    c = Chat(policy, tmp_path)
    yield c
    c.ledger.close()


def test_bot_never_replies_while_a_person_owns_the_chat_and_hand_back_restores_it(chat):
    chat.verify(B2001)
    conv = chat.say(B2001, "hi")["conversation_id"]
    tk = Takeover(chat.engine)
    tk.take_over(conv, "asha")
    outbox = len(chat.engine.adapter.inbox(B2001))
    for text in ("what is my balance", "1", "send me the payment link", "hi", "I already paid UTR " + PENDING_REF, "thanks"):
        r = chat.say(B2001, text)
        assert r["replies"] == [] and r["conversation_id"] == conv and human_owned(chat.engine.get(conv).data)
    assert len(chat.engine.adapter.inbox(B2001)) == outbox       # nothing reached the phone from the bot
    chat.clock.advance(minutes=45)                               # idle past the 30-min session: still the person's chat
    r = chat.say(B2001, "hello?")
    assert r["replies"] == [] and r["conversation_id"] == conv
    tl = chat.timeline(conv)
    assert sum(1 for e in tl if e["kind"] == "HUMAN_OWNED") == 7
    sent = tk.agent_message(conv, "asha", "Hi, this is Asha. Your EMI of Rs 5,000 is due; call me on 9876543210.")
    assert sent["sent"] and chat.engine.adapter.inbox(B2001)[-1]["text"].startswith("Hi, this is Asha")
    agent_env = [e for e in chat.timeline(conv) if e["kind"] == "AGENT_REPLY"][-1]
    assert "9876543210" not in json.dumps(agent_env)              # phone numbers scrubbed on the timeline
    back = tk.hand_back(conv, "asha")
    assert back["owner"] == "bot" and back["state"] == "VERIFIED"
    r = chat.say(B2001, "what is my balance")
    assert "BALANCE" in r["reply_keys"]                           # the bot answers again


def test_d7_recheck_cannot_reply_while_owned(chat):
    chat.verify(B2001)
    r = chat.say(B2001, f"I already paid Rs 5,000 yesterday, UTR {PENDING_REF}")
    assert "CLAIM_PENDING" in r["reply_keys"]
    tk = Takeover(chat.engine)
    tk.take_over(r["conversation_id"], "asha")
    before = len(chat.engine.adapter.inbox(B2001))
    from kairos_backend.cm03.world import settle_feed
    settle_feed(chat.ledger._spine(), PENDING_REF)
    changed = chat.engine.recheck_pending_claims()
    assert changed and changed[0]["status"] == "VERIFIED"          # the money is applied (bank-verified) ...
    assert len(chat.engine.adapter.inbox(B2001)) == before         # ... but the bot sends nothing
    assert any(e["kind"] == "GUARD_BLOCK" and e["payload"].get("owner") == "human:asha" for e in chat.timeline(r["conversation_id"]))


def test_takeover_guards(chat):
    chat.verify(B2001)
    conv = chat.say(B2001, "hi")["conversation_id"]
    tk = Takeover(chat.engine)
    with pytest.raises(TakeoverError) as e:
        tk.agent_message(conv, "asha", "hello")
    assert e.value.code == "NOT_TAKEN_OVER"
    tk.take_over(conv, "asha")
    with pytest.raises(TakeoverError) as e:
        tk.take_over(conv, "ravi")
    assert e.value.code == "OWNED_BY_ANOTHER_AGENT"
    with pytest.raises(TakeoverError):
        tk.hand_back(conv, "ravi")
    with pytest.raises(TakeoverError):
        tk.agent_message(conv, "asha", "   ")
    assert tk.take_over(conv, "asha")["idempotent"]


def test_stop_while_owned_is_honoured_silently_and_blocks_agent_messages(chat):
    chat.verify(B1042)
    conv = chat.say(B1042, "hi")["conversation_id"]
    tk = Takeover(chat.engine)
    tk.take_over(conv, "asha")
    r = chat.say(B1042, "STOP")
    assert r["replies"] == [] and "STOP_HONOURED" in r["outcomes"]
    consents = chat.ledger._conn().execute("SELECT opted_out FROM consents WHERE borrower_id='B-1042'").fetchall()
    assert consents and all(c[0] == 1 for c in consents)
    with pytest.raises(TakeoverError) as e:
        tk.agent_message(conv, "asha", "Sorry to see you go")
    assert e.value.code == "BORROWER_OPTED_OUT"
    assert tk.hand_back(conv, "asha")["state"] == "CLOSED"


def test_cm03_takeover_http_endpoints(chat):
    app = FastAPI()
    app.include_router(build_router(chat.engine))
    c = TestClient(app)
    chat.verify(B2001)
    conv = chat.say(B2001, "hi")["conversation_id"]
    assert c.post(f"/cm03/conversations/{conv}/agent-message", json={"agent": "asha", "text": "hi"}).status_code == 409
    assert c.post(f"/cm03/conversations/{conv}/takeover", json={"agent": "asha"}).json()["owner"]["agent"] == "human:asha"
    assert chat.say(B2001, "balance")["replies"] == []
    assert c.post(f"/cm03/conversations/{conv}/agent-message", json={"agent": "asha", "text": "Hello from Asha"}).json()["sent"]
    assert c.post(f"/cm03/conversations/{conv}/hand-back", json={"agent": "asha"}).json()["owner"] == "bot"
    assert c.post("/cm03/conversations/WA-nope/takeover", json={"agent": "asha"}).status_code == 404


# ---------------------------------------------------------------- the demo story through the journey world
def test_story_takeover_reject_hand_back_then_injection(w):
    _verified(w)
    cid = w.send("A", None, "claim_fake_screenshot")["conversation_id"]
    assert w.human("takeover", cid)["status"]["owner"] == J.AGENT_LABEL
    silent = w.send("A", "hello?", None)
    assert silent["reply"]["silent"] and silent["guard"]["status"] == "silent" and not silent["reply"]["items"]
    rej = w.human("claim", cid, decision="reject")
    assert rej["result"]["status"] == "REJECTED" and rej["result"]["marks_paid"] is False
    back = w.human("handback", cid)
    assert back["status"]["bot_replies"] and back["result"]["resolution_code"] == "CONTACTED_RESOLVED"
    tr = w.send("A", None, "injection")                           # the bot is back and the detector runs again
    assert tr["fraud"] and "PROMPT_INJECTION" in tr["checks"]["fired"]


def test_hardship_takeover_hold_and_queue(w):
    assert len(w._queued("B-1042")) == 4
    tr = w.send("B", None, "hardship")
    assert _keys(tr) == ["HARDSHIP"]
    assert (tr["effects"]["outbound"]["before"], tr["effects"]["outbound"]["after"]) == (4, 0)
    assert "HARDSHIP_HOLD" in tr["effects"]["holds"]
    cid = tr["conversation_id"]
    w.human("takeover", cid)
    w.human("message", cid, text="I'm sorry to hear that. I've paused reminders for 30 days.")
    assert w.phones["B"]["messages"][-1]["who"] == "agent"
    hold = w.human("hold", cid)
    assert hold["status"]["held_until"] and hold["result"]["days"] == 30
    back = w.human("handback", cid)
    assert back["result"]["resolution_code"] == "HARDSHIP_CONFIRMED"
    case = w.ledger._conn().execute("SELECT status FROM tickets WHERE ticket_id=?", (back["result"]["ticket_id"],)).fetchone()
    assert case["status"] == "RESOLVED"


# ---------------------------------------------------------------- STOP propagation
def test_stop_revokes_every_channel_and_cancels_queued_touches(w):
    assert len(w._queued("B-2001")) == 4 and all(w._consent("B-2001").values())
    tr = w.send("A", None, "stop")
    assert _keys(tr) == ["STOP_ACK"]
    ob = tr["effects"]["outbound"]
    assert ob["before"] == 4 and ob["after"] == 0
    assert tr["effects"]["consent"]["after"] == {"WhatsApp": False, "SMS": False, "Voice": False, "Email": False}
    assert all(not g["allowed"] and g["reason"] == "STOP_OPT_OUT" for g in tr["effects"]["gate"])
    st = w.ledger._conn().execute("SELECT status, reason FROM touches WHERE borrower_id='B-2001'").fetchall()
    assert st and all((r["status"], r["reason"]) == ("CANCELED", "STOP_OPT_OUT") for r in st)
    assert len(w._queued("B-1042")) == 4                          # the other borrower is untouched


def test_stop_after_a_fraud_handoff_is_still_honoured(w):
    _verified(w)
    w.send("A", None, "injection")
    tr = w.send("A", None, "stop")
    assert tr["effects"]["outbound"]["after"] == 0 and not any(tr["effects"]["consent"]["after"].values())


# ---------------------------------------------------------------- audit + PII
def test_every_human_action_is_audited_without_text(w):
    _verified(w)
    cid = w.send("A", None, "claim_fake_screenshot")["conversation_id"]
    w.human("takeover", cid)
    w.human("message", cid, text="We could not match that payment, sorry.")
    w.human("claim", cid, decision="reject")
    w.human("handback", cid)
    tr = w.send("B", None, "hardship")
    w.human("takeover", tr["conversation_id"])
    w.human("hold", tr["conversation_id"])
    rows = w.ledger._conn().execute("SELECT actor, type, detail FROM audit WHERE actor='human:asha'").fetchall()
    types = [r["type"] for r in rows]
    for t in ("CM03_TAKEOVER", "CASE_CLAIMED", "CM03_AGENT_MESSAGE", "CM03_CLAIM_REJECTED", "CASE_RESOLVED", "CM03_HAND_BACK",
              "HUMAN_ACTION"):
        assert t in types, t
    assert "could not match" not in json.dumps([r["detail"] for r in rows])
    panel = w.state()["audit"]
    assert any(a["human"] and a["type"] == "CM03_TAKEOVER" for a in panel)


def test_no_pii_in_the_page_payloads_or_the_logs(w):
    _verified(w)
    code = w._otp_for(w.phones["A"]["phone"])
    w.send("A", None, "claim_fake_screenshot")
    cid = w._latest_conv(w.phones["A"]["phone"]).conversation_id
    w.human("takeover", cid)
    w.human("message", cid, text="Call me back on +91 98765 43210")
    w.send("B", None, "hardship")
    w.send("A", None, "stop")
    traces_and_queue = json.dumps({**w.state(), "phones": None}, ensure_ascii=False)
    assert not FULL_PHONE.search(traces_and_queue) and "Synthetic Borrower" not in traces_and_queue
    assert "98765 43210" not in traces_and_queue
    phone_screen = json.dumps(w.state()["phones"], ensure_ascii=False)
    assert not FULL_PHONE.search(phone_screen) and "Synthetic Borrower" not in phone_screen
    db = sqlite3.connect(w.ledger.path)
    for table, col in (("cm03_timeline", "payload"), ("audit", "detail"), ("tickets", "summary_line_1")):
        blob = " ".join(str(r[0]) for r in db.execute(f"SELECT {col} FROM {table}"))
        assert code not in blob, table                             # the OTP never reaches a log
        assert not FULL_PHONE.search(blob), table
    db.close()


# ---------------------------------------------------------------- HTTP surface
def test_http_page_send_agent_and_reset():
    app = FastAPI()
    app.include_router(J.router)
    c = TestClient(app)
    try:
        page = c.get("/demo/journey/inbound")
        assert page.status_code == 200 and "SIMULATED" in page.text and "cdn" not in page.text.lower()
        assert not re.search(r"""(src|href)=["']https?://""", page.text)   # no external assets
        st = c.post("/demo/journey/inbound/reset", json={}).json()["state"]
        assert st["provenance"] == "SIMULATED" and st["traces"] == []
        r = c.post("/demo/journey/inbound/send", json={"phone": "A", "scenario": "emi"}).json()
        assert r["trace"]["identity"]["after"] == "AWAIT_OTP"
        assert c.post("/demo/journey/inbound/send", json={"phone": "Z", "text": "hi"}).status_code == 422
        assert c.post("/demo/journey/inbound/send", json={"phone": "B", "scenario": "otp"}).status_code == 409
        cid = r["trace"]["conversation_id"]
        assert c.post("/demo/journey/inbound/agent/message", json={"conversation_id": cid, "text": "x"}).status_code == 409
        assert c.post("/demo/journey/inbound/agent/takeover", json={"conversation_id": cid}).status_code == 200
        assert c.post("/demo/journey/inbound/send", json={"phone": "A", "text": "hello"}).json()["trace"]["reply"]["silent"]
        st = c.post("/demo/journey/inbound/reset", json={}).json()["state"]
        assert st["traces"] == [] and st["active"] is None and st["phones"]["A"]["messages"] == []
    finally:
        J.reset_world().close()
        J._world = None

