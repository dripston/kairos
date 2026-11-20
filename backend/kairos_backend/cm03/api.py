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
    POST /cm03/conversations/{id}/takeover        {"agent"}: a person owns the chat; the bot goes silent
    POST /cm03/conversations/{id}/agent-message   {"agent","text"}: the owner replies (dry-run adapter)
    POST /cm03/conversations/{id}/claim-decision  {"agent","decision":"approve"|"reject"}: never marks paid
    POST /cm03/conversations/{id}/hand-back       {"agent"}: the bot owns it again

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
from .takeover import Takeover, TakeoverError
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

    # ---- human takeover (takeover.py): owner flag + bot-silence guard + agent messages, all audited ----
    takeover = Takeover(engine)

    @r.post("/demo/new-chat")
    def demo_new_chat(body: dict):
        """Console "New chat": an explicit, audited operator action that closes this synthetic number's open
        conversation server-side (a bot hand-off included), so the next message starts fresh. Demo only."""
        if os.environ.get("KAIROS_CM03_DEMO_INBOX") != "1":
            raise HTTPException(403, "demo console actions are off (KAIROS_CM03_DEMO_INBOX=1)")
        phone = "+" + str(body.get("phone") or "").strip().lstrip("+")
        if not SYNTHETIC_NUMBER.match(phone):
            raise HTTPException(403, "only synthetic +910 numbers can be reset from the demo console")
        return _human(takeover.close_for_sender, phone, str(body.get("agent") or "console.operator"),
                      str(body.get("reason") or "new chat from the demo console"))

    def _human(fn, *a, **k):
        try:
            return {"provenance": PROVENANCE, **fn(*a, **k)}
        except TakeoverError as e:
            raise HTTPException(e.http, e.as_dict())

    @r.post("/conversations/{conversation_id}/takeover")
    def take_over(conversation_id: str, body: dict):
        return _human(takeover.take_over, conversation_id, str(body.get("agent") or ""), str(body.get("reason") or "taken over"))

    @r.post("/conversations/{conversation_id}/agent-message")
    def agent_message(conversation_id: str, body: dict):
        return _human(takeover.agent_message, conversation_id, str(body.get("agent") or ""), str(body.get("text") or ""))

    @r.post("/conversations/{conversation_id}/claim-decision")
    def claim_decision(conversation_id: str, body: dict):
        return _human(takeover.decide_claim, conversation_id, str(body.get("agent") or ""), str(body.get("decision") or ""),
                      str(body.get("reason") or ""))

    @r.post("/conversations/{conversation_id}/hand-back")
    def hand_back(conversation_id: str, body: dict):
        return _human(takeover.hand_back, conversation_id, str(body.get("agent") or ""))

    return r
