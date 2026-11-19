"""CM-03 HTTP surface (mounted on kairos_backend.api:app).

    POST /cm03/whatsapp/inbound        WhatsApp Cloud API webhook payload, or {"from","text","attachment","message_id"}
    GET  /cm03/whatsapp/inbound        Meta webhook verification (hub.challenge; WA_VERIFY_TOKEN)
    GET  /cm03/conversations/{id}      state + CRM timeline (envelopes, all SIMULATED)
    GET  /cm03/conversations           latest conversations (masked senders)
    GET  /cm03/metrics                 resolution, hand-off reasons, first-reply latency, tool calls
    GET  /cm03/console                 operator chat console (plain HTML + JS)
    POST /cm03/d7/recheck              re-check PENDING payment claims against the D7 feed (E14)
    POST /cm03/demo/seed               seed the 3 demo borrowers + D7 feed rows (idempotent)
    POST /cm03/demo/settle             mark a synthetic D7 feed row SETTLED, then recheck
    GET  /cm03/demo/inbox?phone=       the SIMULATED phone's inbox (OTP messages). Off unless
                                       KAIROS_CM03_DEMO_INBOX=1, and only for synthetic +910 numbers.

If WA_APP_SECRET is set, POSTs must carry a valid X-Hub-Signature-256 (HMAC-SHA256 of the raw body).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

from . import PROVENANCE
from .engine import ConversationEngine
from .world import DEMO_BORROWERS, DEMO_FEED, seed_demo, settle_feed

SYNTHETIC_NUMBER = re.compile(r"^\+910\d{9}$")
CONSOLE = Path(__file__).with_name("static") / "console.html"


def parse_inbound(payload: dict) -> List[dict]:
    """Cloud API webhook -> [{"from","text","button","attachment","message_id"}]; simple form passes through."""
    if "entry" not in payload:
        if not payload.get("from"):
            raise HTTPException(422, "need 'from' (sender number) or a WhatsApp Cloud API webhook payload")
        return [{"from": payload["from"], "text": payload.get("text"), "button": payload.get("button"),
                 "attachment": payload.get("attachment"), "message_id": payload.get("message_id")}]
    out = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for m in value.get("messages") or []:
                t = m.get("type")
                text = button = attachment = None
                if t == "text":
                    text = (m.get("text") or {}).get("body")
                elif t == "button":
                    button = (m.get("button") or {}).get("payload") or (m.get("button") or {}).get("text")
                elif t == "interactive":
                    inter = m.get("interactive") or {}
                    rep = inter.get("button_reply") or inter.get("list_reply") or {}
                    button = rep.get("id") or rep.get("title")
                elif t in ("image", "document"):
                    media = m.get(t) or {}
                    text = media.get("caption")
                    # SIMULATED: OCR / EXIF fields arrive pre-extracted on the payload (no media download)
                    attachment = {"type": t, "kind": "payment_screenshot", "media_id": media.get("id"),
                                  **(m.get("kairos_simulated_media") or {})}
                elif t == "audio":
                    text = (m.get("kairos_simulated_media") or {}).get("transcript")
                out.append({"from": "+" + str(m.get("from", "")).lstrip("+"), "text": text, "button": button,
                            "attachment": attachment, "message_id": m.get("id")})
    return out


def build_router(engine: ConversationEngine) -> APIRouter:
    r = APIRouter(prefix="/cm03", tags=["CM-03 WhatsApp servicing"])

    @r.get("/whatsapp/inbound", response_class=PlainTextResponse)
    def verify_webhook(request: Request):
        q = request.query_params
        token = os.environ.get("WA_VERIFY_TOKEN")
        if q.get("hub.mode") == "subscribe" and token and hmac.compare_digest(q.get("hub.verify_token", ""), token):
            return q.get("hub.challenge", "")
        raise HTTPException(403, "verification failed")

    @r.post("/whatsapp/inbound")
    async def inbound(request: Request):
        raw = await request.body()
        secret = os.environ.get("WA_APP_SECRET")
        if secret:
            sig = request.headers.get("x-hub-signature-256", "")
            good = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, good):
                raise HTTPException(401, "bad signature")
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(422, "body is not JSON")
        if not isinstance(payload, dict):
            raise HTTPException(422, "body must be a JSON object")
        msgs = parse_inbound(payload)

        def run():
            return [engine.handle(m["from"], text=m["text"], attachment=m["attachment"], button=m["button"],
                                  message_id=m["message_id"]) for m in msgs]
        return {"provenance": PROVENANCE, "results": await run_in_threadpool(run)}

    @r.get("/conversations")
    def conversations(limit: int = 20):
        rows = engine.ledger._conn().execute("SELECT conversation_id FROM cm03_conversations ORDER BY updated_at DESC, rowid DESC"
                                             " LIMIT ?", (min(limit, 200),)).fetchall()
        return [engine.get(x[0]).public() for x in rows]

    @r.get("/conversations/{conversation_id}")
    def conversation(conversation_id: str):
        conv = engine.get(conversation_id)
        if conv is None:
            raise HTTPException(404, "unknown conversation")
        return {**conv.public(), "timeline": engine.timeline(conversation_id)}

    @r.get("/metrics")
    def metrics():
        return engine.metrics()

    @r.get("/console", response_class=HTMLResponse)
    def console():
        return CONSOLE.read_text(encoding="utf-8")

    @r.post("/d7/recheck")
    def recheck():
        return {"provenance": PROVENANCE, "changed": engine.recheck_pending_claims()}

    @r.post("/demo/seed")
    def demo_seed():
        seed_demo(engine.ledger, engine.clock().date())
        return {"provenance": PROVENANCE, "borrowers": [{"borrower_id": b[0], "phone": b[1], "loans": b[2], "dpd": b[3],
                                                         "closed": b[4], "lang": b[5]} for b in DEMO_BORROWERS],
                "d7_feed": DEMO_FEED}

    @r.post("/demo/settle")
    def demo_settle(body: dict):
        ref = str(body.get("reference") or "")
        if not ref:
            raise HTTPException(422, "need reference")
        settle_feed(engine.ledger._spine(), ref)
        return {"provenance": PROVENANCE, "changed": engine.recheck_pending_claims()}

    @r.get("/demo/inbox")
    def demo_inbox(phone: str):
        if os.environ.get("KAIROS_CM03_DEMO_INBOX") != "1":
            raise HTTPException(403, "demo inbox is off (set KAIROS_CM03_DEMO_INBOX=1 for the console demo)")
        phone = "+" + phone.strip().lstrip("+")
        if not SYNTHETIC_NUMBER.match(phone):
            raise HTTPException(403, "only synthetic +910 numbers have a simulated inbox")
        return {"provenance": PROVENANCE, "messages": engine.adapter.inbox(phone)}

    return r
