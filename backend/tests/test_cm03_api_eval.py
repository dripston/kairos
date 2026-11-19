"""CM-03 HTTP route (Cloud API webhook + simple form) and a small seeded eval with concurrency (SIMULATED)."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kairos_backend.cm03.api import build_router, parse_inbound
from kairos_backend.cm03.evaluation import build_world, run_concurrent_engine, run_concurrent_http, run_sequential, score
from tests.cm03_helpers import B2001, Chat


@pytest.fixture
def client(policy, tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_CM03_DEMO_INBOX", "1")
    chat = Chat(policy, tmp_path)
    app = FastAPI()
    app.include_router(build_router(chat.engine))
    return TestClient(app), chat


def _wh(frm, body, mid):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"messages": [
        {"from": frm.lstrip("+"), "id": mid, "type": "text", "text": {"body": body}}]}}]}]}


def test_cloud_api_webhook_flow(client):
    c, chat = client
    r = c.post("/cm03/whatsapp/inbound", json=_wh(B2001, "balance?", "wamid.1")).json()["results"][0]
    assert r["reply_keys"] == ["GREET_VERIFY"] and r["sender"].endswith("2001") and "+910000002001" not in json.dumps(r)
    inbox = c.get("/cm03/demo/inbox", params={"phone": B2001}).json()["messages"]
    code = [m for m in inbox if m["kind"] == "otp"][-1]["text"][:6]
    r = c.post("/cm03/whatsapp/inbound", json={"from": B2001, "text": code}).json()["results"][0]
    assert r["verified"] and "BALANCE_SHARED" in r["outcomes"]
    conv = c.get(f"/cm03/conversations/{r['conversation_id']}").json()
    kinds = {e["kind"] for e in conv["timeline"]}
    assert {"INBOUND", "TOOL_CALL", "REPLY", "STATE", "OUTCOME"} <= kinds
    assert all(e["provenance"] == "SIMULATED" for e in conv["timeline"])
    m = c.get("/cm03/metrics").json()
    assert m["conversations"] == 1 and m["resolved_without_human"] == 1
    assert c.get("/cm03/console").status_code == 200


def test_demo_inbox_off_by_default_and_synthetic_only(client, monkeypatch):
    c, _ = client
    assert c.get("/cm03/demo/inbox", params={"phone": "+911000000000"}).status_code == 403
    monkeypatch.delenv("KAIROS_CM03_DEMO_INBOX")
    assert c.get("/cm03/demo/inbox", params={"phone": B2001}).status_code == 403


def test_webhook_signature_enforced_when_secret_set(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("WA_APP_SECRET", "test-secret")
    body = json.dumps({"from": B2001, "text": "hi"}).encode()
    assert c.post("/cm03/whatsapp/inbound", content=body, headers={"content-type": "application/json"}).status_code == 401
    sig = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    ok = c.post("/cm03/whatsapp/inbound", content=body, headers={"content-type": "application/json", "x-hub-signature-256": sig})
    assert ok.status_code == 200


def test_parse_inbound_button_and_image():
    p = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "910000002001", "id": "a", "type": "interactive", "interactive": {"button_reply": {"id": "2", "title": "Pay"}}},
        {"from": "910000002001", "id": "b", "type": "image", "image": {"id": "m1", "caption": "paid"},
         "kairos_simulated_media": {"ocr": {"reference": "512345678901"}}}]}}]}]}
    a, b = parse_inbound(p)
    assert a["button"] == "2" and a["from"] == "+910000002001"
    assert b["attachment"]["ocr"]["reference"] == "512345678901" and b["text"] == "paid"


def test_small_seeded_eval_meets_bars_and_is_deterministic(tmp_path):
    w = build_world(200, 7, db_path=str(tmp_path / "e1.db"))
    s = score(w, run_sequential(w))
    assert s["fakes"]["caught"] == s["fakes"]["seeded"] > 0
    assert s["fakes"]["false_positives_on_genuine"] == 0
    assert s["disclosure_before_verification"] == 0 and s["cross_talk_violations"] == 0
    assert s["resolution_rate"] >= 0.70
    assert s["first_reply_ms"]["over_30s"] == 0
    w2 = build_world(200, 7, db_path=str(tmp_path / "e2.db"))
    assert score(w2, run_sequential(w2))["outcome_digest"] == s["outcome_digest"]


def test_concurrent_engine_and_http_match_sequential(tmp_path):
    ref_w = build_world(80, 11, db_path=str(tmp_path / "r.db"))
    ref = score(ref_w, run_sequential(ref_w))
    w = build_world(80, 11, db_path=str(tmp_path / "c.db"))
    res, stats = run_concurrent_engine(w, workers=16)
    s = score(w, res)
    assert stats["error_count"] == 0 and s["cross_talk_violations"] == 0 and s["outcome_digest"] == ref["outcome_digest"]
    w = build_world(80, 11, db_path=str(tmp_path / "h.db"))
    res, stats = run_concurrent_http(w)
    s = score(w, res)
    assert stats["error_count"] == 0 and s["cross_talk_violations"] == 0 and s["outcome_digest"] == ref["outcome_digest"]
