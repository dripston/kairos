"""Local proof of CM-03 live WhatsApp mode, with NOTHING leaving the machine.

Boots the real backend app (kairos_backend.api:app, temp DB) in live mode with FAKE credentials, swaps the Cloud API
HTTP transport for a recorder, and drives it with signed Meta-shaped webhook POSTs through the full HTTP stack:

    webhook verify -> "Hi" -> OTP on the (fake) phone -> code -> balance -> delivery/read statuses (+ a duplicate)
    -> SIMULATED-OCR edited screenshot -> take over -> agent message -> borrower writes (bot silent) -> STOP
    -> stranger ignored -> unsigned POST refused -> kill switch (outbound pause) -> reset live chat

Writes masked evidence (requests as they would go to Meta, minus the token / number / code) to --out.

    python scripts/cm03_live_local_proof.py --out ../../qa/live_wa
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path

NUM = "+15550001234"            # fictional (555) "user phone"
STRANGER = "+15550009876"       # fictional, not allow-listed
FAKE_TOKEN = "EAA-FAKE-LOCAL-PROOF-TOKEN-0000"
FAKE_SECRET = "fake-local-proof-app-secret"
FAKE_PNID = "100000000000001"
VERIFY = "local-proof-verify-token"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    out = Path(ap.parse_args().out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="kairos-live-proof-"))
    os.environ.update({"KAIROS_DB": str(tmp / "kairos.db"), "KAIROS_DEMO_RECIPIENTS_FILE": str(tmp / "none.json"),
                       "KAIROS_CM03_LIVE_WHATSAPP": "1", "WA_TOKEN": FAKE_TOKEN, "WA_PHONE_NUMBER_ID": FAKE_PNID,
                       "WA_APP_SECRET": FAKE_SECRET, "WA_VERIFY_TOKEN": VERIFY, "KAIROS_DEMO_ALLOWLIST": NUM})
    for k in ("SARVAM_API_KEY", "KAIROS_CM03_DEMO_INBOX"):
        os.environ.pop(k, None)
    log_file = out / "server.log"
    logging.basicConfig(filename=str(log_file), filemode="w", level=logging.DEBUG,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fastapi.testclient import TestClient
    from kairos_backend import api

    phone = []          # what the fake phone received (raw, kept in memory only)

    def transport(url, headers, body, timeout):
        assert url == f"https://graph.facebook.com/v23.0/{FAKE_PNID}/messages", url
        assert headers["Authorization"] == f"Bearer {FAKE_TOKEN}"
        phone.append(json.loads(body))
        return 200, {"messaging_product": "whatsapp", "contacts": [{"input": NUM[1:], "wa_id": NUM[1:]}],
                     "messages": [{"id": f"wamid.FAKE{len(phone):04d}"}]}

    live = api.cm03_engine.live
    live.transport = transport
    client = TestClient(api.app)
    steps, n = [], [0]

    def signed(payload: dict, sign: bool = True):
        raw = json.dumps(payload).encode()
        h = {"Content-Type": "application/json"}
        if sign:
            h["X-Hub-Signature-256"] = "sha256=" + hmac.new(FAKE_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        return client.post("/cm03/whatsapp/inbound", content=raw, headers=h)

    def msg(frm, text, image=False):
        n[0] += 1
        m = {"from": frm[1:], "id": f"wamid.IN{n[0]:03d}", "timestamp": str(int(time.time())), "type": "text",
             "text": {"body": text}}
        if image:
            m = {**m, "type": "image", "image": {"id": "media-1", "mime_type": "image/jpeg", "caption": text}}
            m.pop("text")
        return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"display_phone_number": "15550000000", "phone_number_id": FAKE_PNID},
            "contacts": [{"profile": {"name": "Demo"}, "wa_id": frm[1:]}], "messages": [m]}}]}]}

    def status(wamid, st):
        return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"display_phone_number": "15550000000", "phone_number_id": FAKE_PNID},
            "statuses": [{"id": wamid, "status": st, "timestamp": str(int(time.time())), "recipient_id": NUM[1:]}]}}]}]}

    def step(name, resp, **extra):
        seen =sum(len(s.get("outbound", [])) for s in steps)
        reqs = list(live.requests)[seen:]
        body = resp.json() if resp is not None and resp.headers.get("content-type", "").startswith("application/json") \
            else (resp.text if resp is not None else None)
        steps.append({"step": name, "http": resp.status_code if resp is not None else None, "response": body,
                      "outbound": reqs, **extra})
        print(f"{name:<44} HTTP {resp.status_code if resp is not None else '-'}  live sends: {len(reqs)}")

    from kairos_backend.cm03.store import scrub
    v = client.get("/cm03/whatsapp/inbound", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY,
                                                     "hub.challenge": "1158201444"})
    step("1. Meta webhook verification (GET)", v)
    step("2. borrower: Hi", signed(msg(NUM, "Hi")))
    code = re.search(r"(?<!\d)(\d{6})(?!\d)", phone[0]["text"]["body"]).group(1)
    step("3. borrower types the OTP from WhatsApp", signed(msg(NUM, code)), note="code read from the fake phone; masked here")
    step("4. borrower: what is my balance", signed(msg(NUM, "what is my balance")))
    s1 = signed(status("wamid.FAKE0001", "sent"))
    s2 = signed(status("wamid.FAKE0001", "delivered"))
    s3 = signed(status("wamid.FAKE0001", "delivered"))
    s4 = signed(status("wamid.FAKE0001", "read"))
    step("5. status webhooks sent/delivered/DUP/read", s4,
         statuses=[s1.json().get("statuses"), s2.json().get("statuses"), s3.json().get("statuses"), s4.json().get("statuses")])
    step("6. operator arms SIMULATED OCR (edited)", client.post("/cm03/live/simulated-ocr", json={"preset": "edited"}))
    step("7. borrower sends a payment screenshot", signed(msg(NUM, "I already paid, see screenshot", image=True)))
    cid = client.get("/cm03/live/status").json()["conversation"]["conversation_id"]
    step("8. Asha takes over", client.post(f"/cm03/conversations/{cid}/takeover", json={"agent": "asha"}))
    step("9. Asha replies on WhatsApp", client.post(f"/cm03/conversations/{cid}/agent-message",
                                                     json={"agent": "asha", "text": "Hi, this is Asha from the collections team. I am checking your screenshot."}))
    step("10. borrower: ok (bot stays silent)", signed(msg(NUM, "ok")))
    step("11. borrower: STOP", signed(msg(NUM, "STOP")))
    step("12. Asha tries again after STOP (refused)", client.post(f"/cm03/conversations/{cid}/agent-message",
                                                                   json={"agent": "asha", "text": "One more thing"}))
    step("13. a stranger writes (ignored, audited)", signed(msg(STRANGER, "hello, balance?")))
    step("14. unsigned POST (refused)", signed(msg(NUM, "forged"), sign=False))
    step("15. reset live chat (audited)", client.post("/cm03/live/reset", json={"agent": "console.operator"}))
    step("16. kill switch ON (outbound pause)", client.post("/control/outbound", json={"paused": True, "reason": "proof"}))
    step("17. borrower: Hi (nothing leaves the box)", signed(msg(NUM, "Hi")))
    step("18. kill switch OFF", client.post("/control/outbound", json={"paused": False, "reason": "proof"}))
    step("19. borrower: resend code", signed(msg(NUM, "resend code")))
    final = client.get("/cm03/live/status").json()
    tl_first = client.get(f"/cm03/conversations/{cid}").json()["timeline"]
    audit = [dict(r) for r in api.ledger._conn().execute(
        "SELECT ts, actor, type, borrower_id, reason, detail FROM audit WHERE type LIKE 'CM03_%' OR type='STOP_RECEIVED'"
        " ORDER BY id").fetchall()]

    evidence = {"what": "CM-03 live WhatsApp local proof: real app, fake Cloud API transport, signed Meta-shaped webhooks",
                "provenance": "SIMULATED borrower + LIVE-mode code path; transport FAKE (nothing sent)",
                "steps": steps, "final_status": final, "first_conversation_timeline": tl_first, "audit": audit,
                "meta_requests_total": len(phone)}
    blob = json.dumps(evidence, indent=2, ensure_ascii=False, default=str)
    (out / "evidence.json").write_text(blob)
    lines = ["# CM-03 live WhatsApp - local proof (fake transport, nothing sent)", ""]
    for s in steps:
        lines.append(f"## {s['step']}  (HTTP {s['http']})")
        r = s["response"]
        if isinstance(r, dict) and r.get("results"):
            for x in r["results"]:
                lines.append(f"- state {x.get('state')} verified={x.get('verified')} replies={len(x.get('replies', []))}")
        for q in s["outbound"]:
            lines.append(f"- OUT {q['kind']:<5} -> {q['request']['json']['to']}: {q['request']['json']['text']['body'][:110]}")
        for k in ("statuses", "note"):
            if s.get(k):
                lines.append(f"- {k}: {s[k]}")
        if isinstance(r, dict) and (r.get("ignored") or r.get("reason") or r.get("detail")):
            lines.append(f"- {json.dumps({k: r[k] for k in ('ignored', 'reason', 'detail', 'sent', 'status') if k in r})}")
        lines.append("")
    (out / "transcript.md").write_text("\n".join(lines))
    logging.shutdown()
    leaks = []
    for name in ("evidence.json", "transcript.md", "server.log"):
        text = (out / name).read_text()
        for secret in (FAKE_TOKEN, FAKE_SECRET, NUM[1:], NUM[-10:], STRANGER[1:], code):
            if secret in text:
                leaks.append((name, "code" if secret == code else "secret/number"))
    print(f"\nMeta requests built: {len(phone)}  |  evidence: {out}")
    print("leak check:", "CLEAN (no token, app secret, full number or OTP code in evidence or logs)" if not leaks else leaks)
    assert scrub(code) == "******"
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
