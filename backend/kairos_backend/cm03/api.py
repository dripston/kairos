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

    POST /cm03/console/inbound         the console's simulated chat: simple form, synthetic +910 numbers only
    GET  /cm03/live/status             live WhatsApp mode (live.py): on/off + what is missing, kill switch, 24 h window,
                                       rate, and the live number's conversation (masked) for the console
    POST /cm03/live/reset              {"agent"}: close the live number's open chat (audited) + reset the demo borrower
    POST /cm03/live/simulated-ocr      {"preset": "edited"|"off"}: SIMULATED OCR for the next image from the live phone

If WA_APP_SECRET is set, POSTs must carry a valid X-Hub-Signature-256 (HMAC-SHA256 of the raw body).
Live WhatsApp mode (KAIROS_CM03_LIVE_WHATSAPP=1) REQUIRES WA_APP_SECRET: without it every POST is refused (503).
In live mode, messages from numbers that are not the allow-listed live number (and not synthetic) are ignored,
audited (masked) and never answered; status webhooks are recorded on the conversation timeline.
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
from .engine import ConversationEngine, normalize_sender
from .live import LiveError, live_config, live_conversation, reset_live_chat
from .store import mask_phone
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
        if live_config().flag and not secret:
            raise HTTPException(503, "live WhatsApp mode requires WA_APP_SECRET (signed webhooks); nothing processed")
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
        return await _handle(payload)

    async def _handle(payload: dict) -> dict:
        msgs = parse_inbound(payload)
        live = getattr(engine, "live", None)
        cfg = live.config() if live is not None else live_config()
        statuses = live.ingest_statuses(payload) if live is not None and "entry" in payload else None
        ignored = 0
        if cfg.enabled:
            keep = []
            for m in msgs:
                sender = normalize_sender(m["from"])
                if sender == cfg.number and live is not None:
                    live.note_inbound(sender)               # the 24 h customer-service window opens again
                    m["attachment"] = live.apply_ocr(m["attachment"])
                    keep.append(m)
                elif SYNTHETIC_NUMBER.match(sender):        # console / eval traffic: dry-run as always
                    keep.append(m)
                else:                                       # never answered; one masked audit row, no text
                    ignored += 1
                    with engine.ledger.tx() as c:
                        engine.ledger._audit(c, "agent:cm03-live", "CM03_LIVE_IGNORED_SENDER", None,
                                             at=engine.clock(), provenance="LIVE", sender=mask_phone(sender),
                                             reason="not the allow-listed live number")
            msgs = keep
            if any(normalize_sender(m["from"]) == cfg.number for m in msgs):
                seed_demo(engine.ledger, engine.clock().date())   # the demo borrower must exist (idempotent)

        def run():
            return [engine.handle(m["from"], text=m["text"], attachment=m["attachment"], button=m["button"],
                                  message_id=m["message_id"]) for m in msgs]
        out = {"provenance": PROVENANCE, "results": await run_in_threadpool(run)}
        if statuses is not None and any(statuses.values()):
            out["statuses"] = statuses
        if ignored:
            out["ignored"] = ignored
        return out

    @r.post("/console/inbound")
    async def console_inbound(body: dict):
        """The console's simulated chat. Not a webhook (no signature), so only synthetic +910 numbers."""
        if "entry" in body:
            raise HTTPException(422, "the console takes the simple form {from, text, attachment, message_id}")
        frm = normalize_sender(str(body.get("from") or ""))
        if not SYNTHETIC_NUMBER.match(frm):
            raise HTTPException(403, "the console only chats as synthetic +910 numbers")
        return await _handle({**body, "from": frm})

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

    # ---- live WhatsApp mode (live.py) ----
    def _live(fn, *a, **k):
        try:
            return {"provenance": PROVENANCE, **fn(*a, **k)}
        except LiveError as e:
            raise HTTPException(e.http, e.as_dict())
        except TakeoverError as e:
            raise HTTPException(e.http, e.as_dict())

    @r.get("/live/status")
    def live_status():
        live = getattr(engine, "live", None)
        st = live.status() if live is not None else {"live": live_config().public()}
        return {"provenance": PROVENANCE, **st, "conversation": live_conversation(engine)}

    @r.post("/live/reset")
    def live_reset(body: dict):
        return _live(reset_live_chat, engine, str(body.get("agent") or "console.operator"),
                     str(body.get("reason") or "reset live chat from the console"),
                     bool(body.get("restore_demo_borrower", True)))

    @r.post("/live/simulated-ocr")
    def live_simulated_ocr(body: dict):
        live = getattr(engine, "live", None)
        if live is None:
            raise HTTPException(409, "live WhatsApp adapter is not installed")
        return _live(lambda: {"armed": live.arm_ocr(body.get("preset"))})

    return r
